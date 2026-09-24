"""Save sync for the on-device MiSTer client.

Everything that decides *what a save is* comes from ``shared/mister_saves.py``
and ``shared/mister_scan.py``, the same modules the desktop client uses, so a
save synced from the MiSTer itself lands in exactly the slot it would have
landed in when synced over SFTP from a PC.

The flow is the project's standard three-way hash sync: hash every local save,
send the whole lot to ``POST /api/v1/sync`` in one request, then act on the
plan the server returns.
"""

from __future__ import annotations

import hashlib
import posixpath
import time

from shared import mister_saves
from shared.mister_install import safe_file_name
from shared.mister_scan import (
    LocalProvider,
    build_save_path,
    scan_saves,
)

from . import config as gsconfig
from .hashcache import HashCache
from .netcache import NetCache

# Status values, ordered by how much they want the user's attention.
CONFLICT = "conflict"
UPLOAD = "upload"
DOWNLOAD = "download"
SERVER_ONLY = "server only"
SYNCED = "synced"
BLANK = "empty"
ERROR = "error"
#: Scanned but never compared - no server configured, or the plan has not been
#: fetched yet. Calling that "synced" would claim a guarantee we do not have.
LOCAL = "local"

STATUS_ORDER = {CONFLICT: 0, DOWNLOAD: 1, UPLOAD: 2, SERVER_ONLY: 3,
                BLANK: 4, LOCAL: 5, SYNCED: 6, ERROR: 7}


def _row_is_system(title_id, info, system):
    """Does this server title belong to *system*?

    The ``system`` column is authoritative when set, but older rows predate it
    and hold an empty string - the live server's Final Fantasy IX save is one -
    so fall back to the shape of the identifier. Skipping those rows meant the
    save slot that actually held data was invisible to the matcher.
    """
    declared = str(info.get("system") or info.get("platform") or "").upper()
    if declared:
        return declared == system

    title_id = str(title_id or "")
    if title_id.upper().startswith(system + "_"):
        return True
    if system == "PS1":
        from shared.mister_saves import PSX_RETAIL_PREFIXES

        return title_id[:4].upper() in PSX_RETAIL_PREFIXES
    return False


def three_way_status(local_hash, server_hash, last_synced_hash):
    """The project's standard three-way comparison.

    Mirrors ``desktop/mister_ssh.determine_status`` and the server's own rules
    in ``server/app/routes/sync.py``; the safe default with no history is a
    conflict, so neither side is overwritten blindly.
    """
    if not server_hash:
        return UPLOAD
    if local_hash == server_hash:
        return SYNCED
    if not last_synced_hash:
        return CONFLICT
    if last_synced_hash == server_hash:
        return UPLOAD
    if last_synced_hash == local_hash:
        return DOWNLOAD
    return CONFLICT


class SaveEntry:
    """One save, with everything the UI and the transfer need."""

    __slots__ = ("title_id", "system", "name", "path", "size", "mtime",
                 "hash", "status", "exists", "is_blank", "message", "server",
                 "is_cd", "display")

    def __init__(self, title_id, system, name, path="", size=0, mtime=0.0,
                 save_hash="", status=SYNCED, exists=True, is_blank=False,
                 is_cd=False):
        self.title_id = title_id
        self.system = system
        self.name = name
        #: The core names a card after the ROM file, except when booting a
        #: real disc, when all it has is the disc serial: ``SLUS_012.79``.
        #: Such a card shares its slot with the ISO card for the same game.
        self.is_cd = is_cd
        #: What the UI shows: the game's name, with ``[CD]`` for a disc card.
        self.display = name
        self.path = path
        self.size = size
        self.mtime = mtime
        self.hash = save_hash
        self.status = status
        self.exists = exists
        self.is_blank = is_blank
        self.message = ""
        #: The server's copy, when known: hash, size, timestamp and which
        #: client last wrote it. Populated for conflicts so they can be
        #: resolved on the device instead of "go and use the desktop".
        self.server = {}

    @property
    def sort_key(self):
        # By display name, so a disc card sits next to its ISO card rather
        # than down among the S's with the other serials.
        return (STATUS_ORDER.get(self.status, 9), self.system,
                self.display.lower(), self.is_cd)


#: How long the server's title list is trusted for name matching.
TITLES_TTL = 24 * 3600.0


class SyncEngine:
    def __init__(self, config=None, provider=None, client=None):
        self.config = config or gsconfig.load_config()
        self.provider = provider or LocalProvider()
        self.client = client
        self.state = gsconfig.load_state()
        self.cache = HashCache()
        self.net = NetCache()
        self.entries = []
        self.last_error = ""
        #: Set when the server did not answer: the scan then resolves names
        #: from whatever is cached, however old, and never waits on the
        #: network - a hung server used to cost a 60 s timeout per lookup.
        self.offline = False

    # ------------------------------------------------------------------ scan

    def scan(self, progress=None):
        """Hash every local save, applying the per-system identity rules.

        Files whose size and mtime are unchanged since the last scan reuse
        their cached hash instead of being read again.
        """
        entries = []
        files = scan_saves(self.provider)
        total = len(files)

        for index, found in enumerate(files, start=1):
            if progress:
                progress("Scanning %d/%d  %s" % (index, total, found.filename))
            entry = self._entry_for(found)
            if entry is not None:
                entries.append(entry)

        self.cache.prune(item.path for item in files)
        self.cache.save()
        self.entries = entries
        self._label_entries()
        # Once, at the end: it holds every system's matcher, and writing it
        # after each lookup cost seconds of JSON per scan.
        self.net.save()
        return entries

    # ------------------------------------------------------- CD / ISO pairs

    def siblings(self, entry):
        """Other local cards keyed to the same slot - the CD card for an ISO
        card and vice versa. Same game, same server save, two files."""
        return [other for other in self.entries
                if other is not entry and other.path
                and other.title_id == entry.title_id]

    def _label_entries(self):
        """Give every entry its on-screen name.

        A disc card is named ``SLUS_012.79``; nobody knows their library by
        serial, so borrow the name from the ISO card sharing the slot, else
        from the server, and mark it ``[CD]`` so the two rows can be told
        apart - they are different files that can hold different progress.
        """
        titles = None
        for entry in self.entries:
            if not entry.is_cd:
                entry.display = entry.name
                continue
            name = ""
            for other in self.siblings(entry):
                if not other.is_cd:
                    name = other.name
                    break
            if not name and self.client is not None:
                if titles is None:
                    try:
                        titles = self._server_titles()
                    except Exception:
                        titles = {}
                info = titles.get(entry.title_id) or {}
                name = str(info.get("game_name") or info.get("name") or "")
            entry.display = "%s [CD]" % (name or entry.name)

    def _entry_for(self, found):
        title_id = found.title_id
        serial = None
        is_blank = False
        needs_payload = mister_saves.needs_payload_read(found.system,
                                                        found.size)

        cached = self.cache.get(found.path, found.size, found.mtime)
        serials = ()
        if cached:
            save_hash = str(cached.get("hash") or "")
            serial = cached.get("serial")
            serials = tuple(cached.get("serials") or ([serial] if serial else []))
            is_blank = bool(cached.get("blank"))
        else:
            try:
                data = self.provider.read(found.path)
                if needs_payload:
                    # PS1 / Saturn / Sega CD / a Mega Drive core image: the
                    # bytes decide both the identity and which bytes get hashed.
                    identity = mister_saves.resolve_save_identity(found.system,
                                                                  data)
                    save_hash = hashlib.sha256(
                        identity.hash_payload).hexdigest()
                    serial = identity.serial
                    serials = identity.serials
                    is_blank = identity.is_blank
                else:
                    save_hash = hashlib.sha256(data).hexdigest()
            except OSError as exc:
                entry = SaveEntry(title_id, found.system, found.stem,
                                  found.path, found.size, found.mtime, "",
                                  ERROR)
                entry.message = str(exc)
                return entry
            self.cache.put(found.path, found.size, found.mtime, save_hash,
                           serial=serial, blank=is_blank, serials=serials)

        # Resolved every scan, not cached: it can depend on the ROM
        # catalogue, which changes when the server's library does. Every
        # system goes through it - a slug system's save named exactly like
        # a catalogue file takes that file's server key, which for a
        # translation patch is not the slug of the name.
        identity = mister_saves.SaveIdentity(b"", serial=serial,
                                             is_blank=is_blank,
                                             serials=serials)
        title_id = mister_saves.resolve_title_id(
            found.system, found.stem, identity, title_id,
            catalog_lookup=self._catalog_lookup)
        entry = SaveEntry(
            title_id=title_id,
            system=found.system,
            name=found.stem,
            path=found.path,
            size=found.size,
            mtime=found.mtime,
            save_hash="" if is_blank else save_hash,
            status=BLANK if is_blank else LOCAL,
            # A formatted-but-empty card can receive a download but must never
            # upload over a real save.
            exists=not is_blank,
            is_blank=is_blank,
            is_cd=(found.system == "PS1"
                   and mister_saves.ps1_serial_from_filename(found.stem)
                   is not None),
        )
        return entry

    def _catalog_lookup(self, system, stem):
        """Resolve a save's name to the server's key for serial-keyed systems.

        PS1 and Saturn are keyed by disc serial. When the save carries no
        serial of its own - a memory card no game has written to, or Saturn
        backup RAM, which has no disc id at all - the name is the only bridge,
        and the two sides routinely spell it differently
        ("Final Fantasy IX (USA)" against "Final Fantasy IX (USA, Canada)").
        """
        from shared.sync_id import uses_serial_identity

        if self.client is None:
            return None
        # Only serial-keyed systems may be matched loosely. For a slug-keyed
        # system the name *is* the identity, so a near-miss match would file
        # two different games into one save slot - but a save named exactly
        # like a catalogue file belongs under whatever the server keyed that
        # file by, which for a translation patch is not the file's own slug.
        if not uses_serial_identity(system):
            return self._exact_titles_for(system).get(
                str(stem or "").lower())
        return self._matcher_for(system).lookup(stem)

    _matcher_cache = None
    _exact_cache = None
    #: Set by the app: ``system -> catalogue rows`` it already holds (the
    #: fingerprint cache), so an exact file-name lookup costs no fetch and no
    #: slug pass. Falls back to the server when the app has nothing.
    catalog_rows = None

    def _exact_titles_for(self, system):
        """``lowercased file stem -> title id`` for one system's catalogue.

        Deliberately not a TitleMatcher: that runs the slug rules over every
        name, which costs seconds on a MiSTer, and an exact match does not
        need them. The stem is what the install would name the file, since
        that is what the core names the save after.
        """
        if self._exact_cache is None:
            self._exact_cache = {}
        cached = self._exact_cache.get(system)
        if cached is not None:
            return cached
        rows = None
        if self.catalog_rows is not None:
            try:
                rows = self.catalog_rows(system)
            except Exception:
                rows = None
        if not rows:
            try:
                rows = self._roms_for(system)
            except Exception:
                rows = []
        index = {}
        for rom in rows:
            title_id = str(rom.get("title_id") or "")
            filename = rom.get("filename") or ""
            if not title_id or not filename:
                continue
            stem = posixpath.splitext(safe_file_name(filename))[0].lower()
            index.setdefault(stem, title_id)
        self._exact_cache[system] = index
        return index

    def forget_catalog(self):
        """The catalogue changed: rebuild the exact-name indexes lazily."""
        self._exact_cache = None

    def _matcher_for(self, system):
        """A name matcher built from the server's ROMs *and* its saves.

        Saves are indexed too: the server may hold a save under a serial for a
        game whose ROM is not in the catalogue at all, and without that the
        save could never be matched.
        """
        from shared.title_match import TitleMatcher

        if self._matcher_cache is None:
            self._matcher_cache = {}
        cached = self._matcher_cache.get(system)
        if cached is not None:
            return cached

        try:
            roms = self._roms_for(system)
        except Exception:
            roms = []  # offline: fall back to slug ids
        try:
            titles = self._server_titles()
        except Exception:
            titles = {}

        # Building a matcher runs the slug rules over every name - seconds
        # on a MiSTer - so a built one is kept for as long as what it was
        # built from is unchanged, however long that is. Keyed on its
        # inputs rather than on a timer, which rebuilt it every ten minutes.
        signature = "%d:%s|%s" % (len(roms), self._catalog_signature(system),
                                  self.net.stored("titles"))
        key = "matcher:%s" % system
        snapshot = self.net.get(key, max_age=float("inf"))
        if isinstance(snapshot, dict) and snapshot.get("signature") == signature:
            matcher = TitleMatcher.from_dict(snapshot.get("matcher") or {})
            self._matcher_cache[system] = matcher
            return matcher

        matcher = TitleMatcher()
        for rom in roms:
            matcher.add(rom.get("title_id"),
                        rom.get("filename"), rom.get("name"))
        for title_id, info in titles.items():
            if not _row_is_system(title_id, info, system):
                continue
            # Slots that already hold a save outrank the catalogue.
            matcher.add(title_id, info.get("name"),
                        info.get("game_name"), authoritative=True)

        self.net.put(key, {"signature": signature,
                           "matcher": matcher.to_dict()})
        self._matcher_cache[system] = matcher
        return matcher

    #: Set by the app: ``system -> fingerprint`` of the catalogue cache, so a
    #: matcher knows when the ROMs it was built from have changed.
    catalog_fingerprint = None

    def _catalog_signature(self, system) -> str:
        if self.catalog_fingerprint is None:
            return ""
        try:
            return str(self.catalog_fingerprint(system) or "")
        except Exception:
            return ""

    _titles_cache = None

    def _roms_for(self, system):
        """The catalogue for one system.

        From the app's catalogue cache when it has one: that copy is kept
        current per system by the server's fingerprints, so fetching the same
        list again here only cost a round trip and a large JSON write. The
        server is asked only before the app has ever loaded a catalogue.
        """
        if self.catalog_rows is not None:
            try:
                rows = self.catalog_rows(system)
            except Exception:
                rows = None
            if rows or self._catalog_known():
                return rows or []
        key = "roms:%s" % system
        rows = self.net.get(key, max_age=float("inf") if self.offline
                            else None)
        if rows is None:
            if self.offline:
                return []
            rows = self.client.list_roms(
                system, fields=("title_id", "filename", "name"))
            self.net.put(key, rows)
        return rows

    #: Set by the app: whether its catalogue cache holds anything at all. A
    #: system missing from a populated cache has no ROMs on the server.
    catalog_known = None

    def _catalog_known(self) -> bool:
        try:
            return bool(self.catalog_known and self.catalog_known())
        except Exception:
            return False

    def _server_titles(self):
        """Every title the server holds, cached on disk for a day.

        Only used for name matching. What to upload or download always comes
        from a live sync plan, never from here, so a day-old copy can at
        worst leave a save that another console uploaded today unmatched by
        name until the next refresh - and Y refreshes it on demand. Fetching
        it cost 2-4 s of server time on every start ten minutes apart.
        """
        if self._titles_cache is None:
            cached = self.net.get("titles", max_age=float("inf")
                                  if self.offline else TITLES_TTL)
            if cached is None:
                if self.offline:
                    raise RuntimeError("offline and no cached titles")
                cached = {
                    title_id: {key: info.get(key)
                               for key in ("system", "platform", "name",
                                           "game_name", "save_size")}
                    for title_id, info in self.client.list_titles().items()
                }
                self.net.put("titles", cached)
            self._titles_cache = cached
        return self._titles_cache

    def refresh_server_data(self):
        """Drop the cached server lists so the next scan refetches them."""
        self.net.invalidate()
        self.net.save()
        self._titles_cache = None
        self._matcher_cache = None
        self._exact_cache = None

    # ------------------------------------------------------------------ plan

    def fetch_plan(self, progress=None):
        """Ask the server what to do with every scanned save."""
        if self.client is None:
            raise RuntimeError("not configured")
        if progress:
            progress("Asking the server...")

        titles = []
        sent = set()
        # Newest first, so of a CD/ISO pair the card played most recently is
        # the one the server is asked about; the other is decided per entry
        # afterwards (card systems re-check every entry individually).
        for entry in sorted(self.entries, key=lambda e: -(e.mtime or 0)):
            if not entry.exists or entry.title_id in sent:
                continue
            sent.add(entry.title_id)
            titles.append({
                "title_id": entry.title_id,
                "save_hash": entry.hash,
                "timestamp": int(entry.mtime or time.time()),
                "size": entry.size,
                "last_synced_hash": self.state.get(entry.title_id, ""),
            })

        platforms = sorted({entry.system for entry in self.entries})
        plan = self.client.sync_plan(titles, platforms=platforms)
        self._apply_plan(plan)
        return plan

    def _apply_plan(self, plan):
        by_id = {}
        for entry in self.entries:
            by_id.setdefault(entry.title_id, entry)

        def mark(ids, status):
            for title_id in ids or []:
                # Every card keyed to the slot, not just the first: a CD card
                # and an ISO card are separate files with separate progress.
                for entry in self.entries:
                    if entry.title_id == title_id and not entry.is_blank \
                            and entry.path:
                        entry.status = status

        mark(plan.get("up_to_date"), SYNCED)
        mark(plan.get("upload"), UPLOAD)
        mark(plan.get("download"), DOWNLOAD)
        mark(plan.get("conflict"), CONFLICT)

        # Saves that exist only on the server, including ones destined for a
        # blank card already sitting on the device.
        server_only = plan.get("server_only") or []
        unknown = [tid for tid in server_only if tid not in by_id]
        metadata = {}
        if unknown:
            # Named from the cached title list; the server spends seconds on
            # a fresh one. Only a save the cache has never seen - uploaded
            # from another console since - is worth fetching it again for.
            try:
                metadata = self._server_titles()
            except Exception:
                metadata = {}
            if any(tid not in metadata for tid in unknown) \
                    and not self.offline:
                try:
                    fresh = self.client.list_titles()
                    metadata = {
                        tid: {key: info.get(key)
                              for key in ("system", "platform", "name",
                                          "game_name", "save_size")}
                        for tid, info in fresh.items()}
                    self.net.put("titles", metadata)
                    self._titles_cache = metadata
                    self.net.save()
                except Exception:
                    pass

        for title_id in server_only:
            entry = by_id.get(title_id)
            if entry is not None:
                entry.status = DOWNLOAD if entry.is_blank else SERVER_ONLY
                continue
            # No local file at all: still offer it, so a save can be pulled
            # down for a game that has never been played on this device.
            info = metadata.get(title_id, {})
            system = str(info.get("system") or "").upper()
            if not system:
                continue  # cannot place a save without knowing its system
            self.entries.append(SaveEntry(
                title_id=title_id,
                system=system,
                name=str(info.get("name") or info.get("game_name")
                         or title_id),
                size=int(info.get("save_size") or 0),
                status=SERVER_ONLY,
                exists=False,
            ))

        self._recheck_card_systems()
        self._settle_housekeeping_differences()
        self._attach_conflict_details(plan)
        self._label_entries()
        self.entries.sort(key=lambda item: item.sort_key)

    def _settle_housekeeping_differences(self):
        """Do not report a conflict over bytes the game never reads.

        A MiSTer core rewrites its own bookkeeping - a PlayStation card's
        write-test frame, a Saturn archive's comment field - so a save that
        holds identical progress can still hash differently, forever. Where the
        two sides differ only there, they are already in sync.
        """
        pending = [e for e in self.entries
                   if e.system in mister_saves.HOUSEKEEPING_SYSTEMS
                   and e.exists and e.path
                   and e.status in (CONFLICT, UPLOAD, DOWNLOAD)]
        if not pending:
            return

        for entry in pending:
            try:
                remote = self.client.download_save(entry.title_id,
                                                   system=entry.system)
                if remote is None:
                    continue
                local = self.provider.read(entry.path)
                if entry.system == "SAT":
                    # The device keeps the byte-expanded form; compare the
                    # canonical one the server stores.
                    local = mister_saves.resolve_save_identity(
                        entry.system, local).hash_payload
                elif entry.system == "MD":
                    # Expand to the size the server actually holds, rather than
                    # guessing from the core's 0xFF padding.
                    local = mister_saves.md_from_mister(
                        local, target_size=len(remote))
                if not mister_saves.same_content(entry.system, local, remote):
                    # A real difference. Say where, so "upload" on a card the
                    # user never saved to can be told apart from a genuine
                    # save: "block 0 write-test frame" is the core, "block 3
                    # (BASLUS-01251FF7-S01)" is the game.
                    where = mister_saves.describe_difference(
                        entry.system, local, remote)
                    if where:
                        entry.message = "differs from server: %s" % where
                    continue
            except Exception as exc:
                # Left as it was, but say why: a status that cannot be
                # verified is not the same as a verified one.
                entry.message = "could not compare with server: %s" % \
                    str(exc)[:60]
                continue

            entry.status = SYNCED
            entry.message = "identical apart from housekeeping bytes"
            # Remember the server's hash so the three-way rule agrees next time
            # without another download.
            self.state[entry.title_id] = entry.hash
            gsconfig.save_state(self.state)

    def _attach_conflict_details(self, plan):
        """Record the server's side of every conflict, for the UI to show."""
        info = {}
        for item in plan.get("conflict_info") or []:
            title_id = str(item.get("title_id") or "")
            if title_id:
                info[title_id] = dict(item)

        conflicts = [e for e in self.entries if e.status == CONFLICT]
        if not conflicts:
            return
        try:
            titles = self._server_titles()
        except Exception:
            titles = {}

        for entry in conflicts:
            details = dict(titles.get(entry.title_id) or {})
            details.update(info.get(entry.title_id) or {})
            entry.server = details

    def _recheck_card_systems(self):
        """Re-decide memory-card titles against the raw-card hash.

        The sync plan compares against the stored bundle's hash, which a client
        holding a raw 128 KB card can never reproduce, so every card would come
        back as "download" on every run. The dedicated card endpoint reports the
        hash of the card itself, which is like-for-like.
        """
        from concurrent.futures import ThreadPoolExecutor

        from .api import CARD_SYSTEMS

        # Nothing local to compare means a download, not a clash: running the
        # three-way rule on it turned every server-only card into a conflict,
        # because "" never equals the server's hash and there is no sync
        # history for a save we have never had.
        cards = [entry for entry in self.entries
                 if entry.system in CARD_SYSTEMS and not entry.is_blank
                 and entry.exists and entry.hash]
        if not cards:
            return

        def fetch(entry):
            try:
                return self.client.card_meta(entry.title_id)
            except Exception:
                return False

        # One request per card, and each is mostly round trip: asked one at
        # a time they were most of the plan's wait on a MiSTer over Wi-Fi.
        with ThreadPoolExecutor(max_workers=6) as pool:
            metas = list(pool.map(fetch, cards))

        for entry, meta in zip(cards, metas):
            if meta is False:
                continue
            server_hash = str((meta or {}).get("save_hash") or "")
            entry.status = three_way_status(
                entry.hash, server_hash, self.state.get(entry.title_id, ""))

    # -------------------------------------------------------------- transfers

    def ps1_saves_at_risk(self, entry):
        """``(own_save_lost, other_saves_lost)`` for downloading this card.

        A PlayStation card is shared between games. Losing *this* game's save
        is the case worth refusing; other games' saves on the same card are
        reported so the choice is informed, but they do not block the sync.
        """
        if entry.system != "PS1" or not entry.path:
            return False, []
        try:
            local = self.provider.read(entry.path)
            remote = self.client.download_save(entry.title_id,
                                               system=entry.system)
        except Exception:
            return False, []
        if not remote:
            return False, []
        return mister_saves.ps1_download_risk(local, remote, entry.title_id)

    def sync_entry(self, entry, progress=None, allow_data_loss=False):
        """Push or pull one save. Returns True when something changed."""
        if self.client is None:
            raise RuntimeError("not configured")

        # What the slot was last synced at, before this transfer moves it:
        # the test for "the other card has not changed since" needs it.
        last_synced = self.state.get(entry.title_id, "")
        if entry.status == UPLOAD:
            changed = self._upload(entry, progress)
        elif entry.status in (DOWNLOAD, SERVER_ONLY):
            changed = self._download(entry, progress,
                                     allow_data_loss=allow_data_loss)
        else:
            return False
        if changed:
            self._mirror_to_siblings(entry, last_synced, progress)
        return changed

    def _mirror_to_siblings(self, entry, last_synced, progress=None):
        """Keep a CD card and its ISO card carrying the same progress.

        Played on the real disc, then from the ISO next week: the save has to
        follow. After one card is synced, the other is overwritten with the
        same bytes - but only when that is provably safe: it is blank, or it
        has not changed since it was last synced, and it holds no save the
        new card lacks. A card that *has* changed too is a real fork between
        the two copies, and is flagged as a conflict for the user to decide;
        the conflict dialog then uploads that side and mirrors back.
        """
        siblings = self.siblings(entry)
        if not siblings or not entry.path:
            return
        try:
            payload = self.provider.read(entry.path)
        except OSError:
            return
        for other in siblings:
            if other.hash == entry.hash:
                other.status = SYNCED
                continue
            try:
                current = self.provider.read(other.path)
            except OSError:
                current = b""
            if current and mister_saves.same_content(other.system, current,
                                                     payload):
                other.status = SYNCED
                other.message = "identical apart from housekeeping bytes"
                continue
            reason = ""
            unchanged = other.is_blank or not other.hash \
                or other.hash == last_synced
            if not unchanged:
                reason = "also changed on the %s card" % (
                    "CD" if other.is_cd else "ISO")
            elif other.system == "PS1" and current:
                own_lost, others = mister_saves.ps1_download_risk(
                    current, payload, other.title_id)
                if own_lost or others:
                    reason = "card holds %d save(s) the other does not" % (
                        len(others) + (1 if own_lost else 0))
            if reason:
                other.status = CONFLICT
                other.message = reason
                continue
            if progress:
                progress("Mirroring to %s" % other.display)
            try:
                _write_atomic(other.path, payload)
            except OSError as exc:
                other.status = ERROR
                other.message = "mirror failed: %s" % exc
                continue
            other.size = len(payload)
            other.mtime = _mtime_of(other.path) or time.time()
            other.hash = entry.hash
            other.status = SYNCED
            other.exists = True
            other.is_blank = False
            other.message = "mirrored from the %s card" % (
                "CD" if entry.is_cd else "ISO")
            identity = mister_saves.resolve_save_identity(other.system,
                                                          payload)
            self.cache.put(other.path, other.size, other.mtime, other.hash,
                           serial=identity.serial, blank=identity.is_blank,
                           serials=identity.serials)
        self.cache.save()

    def _upload(self, entry, progress=None):
        if not entry.exists:
            entry.message = "blank card - nothing to upload"
            return False
        if progress:
            progress("Uploading %s" % entry.display)

        data = self.provider.read(entry.path)
        identity = mister_saves.resolve_save_identity(entry.system, data)
        payload = identity.hash_payload  # canonical form, not the raw file

        if entry.system == "MD":
            # The core pads to a fixed 64 KB, so the real SRAM size can only be
            # guessed from the padding. When the server already holds a copy,
            # its size is the truth - uploading a differently sized image would
            # hand every other client a reshaped save.
            server_size = self._server_save_size(entry.title_id)
            if server_size:
                payload = mister_saves.md_from_mister(
                    data, target_size=server_size)

        # A disc card is named after the serial, which is no name at all;
        # the server resolves the serial itself, so send nothing rather than
        # "SLUS_012.79".
        self.client.upload_save(entry.title_id, payload, system=entry.system,
                                game_name="" if entry.is_cd else entry.name)
        entry.hash = hashlib.sha256(payload).hexdigest()
        self.cache.put(entry.path, entry.size, entry.mtime, entry.hash,
                       serial=identity.serial, blank=identity.is_blank,
                       serials=identity.serials)
        self.cache.save()
        entry.status = SYNCED
        self._remember(entry.title_id, entry.hash)
        return True

    def download_target(self, entry):
        """Where a download of ``entry`` would be written, or "" if nowhere.

        A "server only" row has no path of its own; the predicted one may
        already hold a file the scan filed under a different title id, so
        callers must not assume it is free.
        """
        return entry.path or build_save_path(
            self.provider, entry.system, entry.title_id, entry.name,
            catalog_lookup=self._catalog_lookup) or ""

    def _download(self, entry, progress=None, allow_data_loss=False):
        if progress:
            progress("Downloading %s" % entry.display)

        payload = self.client.download_save(entry.title_id, system=entry.system)
        if payload is None:
            entry.message = "server has no save"
            return False

        # Resolve the destination before any safety check (see download_target).
        target = self.download_target(entry)
        if not target:
            entry.message = "no place to write it - is the game installed?"
            entry.status = ERROR
            return False

        if entry.system == "PS1" and not allow_data_loss:
            # A memory card is shared between games. Writing the server's copy
            # over it would delete every save the server does not have.
            try:
                local = self.provider.read(target)
            except OSError:
                local = b""
            if local:
                own_lost, others = mister_saves.ps1_download_risk(
                    local, payload, entry.title_id)
                if own_lost:
                    entry.message = (
                        "the server's card has no save for this game - "
                        "downloading would delete yours")
                    raise RuntimeError(entry.message)
                if others:
                    # Not fatal: these belong to other games that happen to
                    # share the card. Recorded so it is visible afterwards.
                    entry.message = ("replaced the card; %d save(s) for other "
                                     "games were not on the server" % len(others))

        # Convert to whatever shape the core expects before writing.
        data = _to_device_format(entry.system, payload)
        _write_atomic(target, data)

        entry.path = target
        entry.size = len(data)
        entry.mtime = _mtime_of(target) or time.time()
        identity = mister_saves.resolve_save_identity(entry.system, data)
        entry.hash = hashlib.sha256(identity.hash_payload).hexdigest()
        # Seed the cache with what we just wrote, so the next scan does not
        # read the file back only to learn what it already knows.
        self.cache.put(target, entry.size, entry.mtime, entry.hash,
                       serial=identity.serial, blank=identity.is_blank,
                       serials=identity.serials)
        self.cache.save()
        entry.status = SYNCED
        entry.exists = True
        entry.is_blank = False
        self._remember(entry.title_id, entry.hash)
        return True

    def _server_save_size(self, title_id):
        """Size of the copy already on the server, or 0."""
        try:
            info = self._server_titles().get(title_id) or {}
            return int(info.get("save_size") or 0)
        except Exception:
            return 0

    def _remember(self, title_id, save_hash):
        self.state[title_id] = save_hash
        gsconfig.save_state(self.state)

    def sync_all(self, progress=None):
        """Transfer everything the plan asked for. Conflicts are left alone."""
        changed = failed = 0
        pending = [e for e in self.entries
                   if e.status in (UPLOAD, DOWNLOAD, SERVER_ONLY)]
        for index, entry in enumerate(pending, start=1):
            if progress:
                progress("%d/%d  %s" % (index, len(pending), entry.display))
            try:
                if self.sync_entry(entry):
                    changed += 1
            except Exception as exc:
                # Never destroy other games' saves as part of a bulk sync; it
                # needs an explicit decision, which the conflict dialog offers.
                failed += 1
                entry.status = ERROR
                entry.message = str(exc)
        return changed, failed


def _mtime_of(path):
    import os

    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _to_device_format(system, payload):
    """Server payload -> the bytes this core actually reads."""
    system = (system or "").upper()
    if system == "MD":
        return mister_saves.md_to_mister(payload)
    if system == "SAT":
        # The core wants the 64 KB byte-expanded image, not the canonical 32 KB.
        from shared.mister import MISTER_SATURN_FORMAT
        from shared.saturn_format import convert_saturn_save_format

        return convert_saturn_save_format(payload, MISTER_SATURN_FORMAT)
    return payload


def _write_atomic(path, data):
    import os

    directory = path.rsplit("/", 1)[0]
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError:
        pass
    temp = path + ".part"
    with open(temp, "wb") as handle:
        handle.write(data)
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except OSError:
            pass
    os.replace(temp, path)
