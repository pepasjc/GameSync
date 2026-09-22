"""RetroAchievements index for the ROM catalog.

Answers "does this ROM earn achievements?" for every catalog entry, so the
clients can show an RA badge without each of them hashing the library
itself.

RA identifies a game by the ROM's exact MD5 under its own per-system rules
(:mod:`shared.ra_hash`), which means every file has to be read once.  That
happens here, in a background pass after the catalog scan, never during it:
a scan stays as fast as it is today and the badges fill in behind it.  The
result is cached in ``roms.db`` keyed by *path + size + mtime*, so a rescan
— or a server restart — costs nothing, and a ROM that is replaced on disk is
re-hashed because its size or mtime moved.

Cartridge-shaped systems are matched by hash, which is exact.  Some disc
systems can be too: :mod:`shared.ra_disc` reads the boot executable out
of a CHD without decompressing the whole image, which is what RA hashes.

When that hash is not one RA registered - most PlayStation games have
exactly one registered dump, so any other revision misses - the game
falls back to a *title* match (:mod:`shared.ra_titles`): "RA has a set
for this game", not "RA will recognise this dump".  The two are stored and
reported separately as ``ra_match`` = ``hash`` or ``title`` so a client
never presents the weaker one as a guarantee.

The hash library comes from :mod:`shared.ra_api`.  With ``SYNC_RA_API_KEY``
set it carries achievement counts, which is what ``achievements > 0`` — a
game you can actually earn something in, as opposed to a hash RA merely
recognises — is read from.  Without a key the counts are unknown and stored
as ``-1``; clients treat that as "registered, count unknown".
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

from app.services import rom_db

# rom_id puts the repo root on sys.path for us, so `shared` imports plainly.
from app.services import rom_id  # noqa: F401
from shared.ra_api import RaAuthError, RaLibrary, fetch_library
from shared.ra_hash import (
    ra_console_ids,
    ra_hash_file,
    ra_hash_supported,
    ra_title_match_only,
)
from shared.ra_disc import disc_hash_supported, hash_disc_file
from shared.ra_titles import build_index
from shared import msu

logger = logging.getLogger(__name__)

# Stored in `achievements` when the count could not be determined (no API
# key).  Distinct from 0, which positively means "registered, no set yet".
ACHIEVEMENTS_UNKNOWN = -1

#: How a row was identified.  Exact, versus "RA has a set for a game of
#: this name" - which is all a disc system can offer until a CHD reader
#: exists.
MATCH_HASH = "hash"
MATCH_TITLE = "title"

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ra_roms (
    path         TEXT PRIMARY KEY,
    size         INTEGER NOT NULL DEFAULT 0,
    mtime        REAL    NOT NULL DEFAULT 0,
    md5          TEXT    NOT NULL DEFAULT '',
    game_id      INTEGER NOT NULL DEFAULT 0,
    achievements INTEGER NOT NULL DEFAULT 0,
    title        TEXT    NOT NULL DEFAULT '',
    checked_at   REAL    NOT NULL DEFAULT 0,
    match_kind   TEXT    NOT NULL DEFAULT 'hash'
)
"""

_lock = threading.Lock()
_running = threading.Event()

#: Bumped whenever rows are written.  Lets a cache keyed on the catalog
#: alone (the fingerprints) notice that the RA data underneath it moved,
#: without the *values* in a fingerprint ever depending on it - a digest
#: that changed every restart would make every client refetch everything.
_generation = 0


def generation() -> int:
    return _generation


def _conn():
    conn = rom_db.connection()
    conn.execute(_CREATE_TABLE_SQL)
    # An index built before title matching existed has no match_kind; every
    # row in it was a hash match, which is what the default says.
    columns = {row[1] for row in conn.execute("PRAGMA table_info(ra_roms)")}
    if "match_kind" not in columns:
        conn.execute("ALTER TABLE ra_roms ADD COLUMN match_kind TEXT NOT NULL DEFAULT 'hash'")
        conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def lookup(paths: Iterable[str]) -> dict[str, dict]:
    """``{path: {...}}`` for the paths RA recognises.  Unknown paths are absent.

    Only rows with a game id are returned: a ROM that was hashed and found
    to be unknown to RA is cached too (so it is not hashed again), but has
    nothing to say to a client.
    """
    wanted = [p for p in paths if p]
    if not wanted:
        return {}
    conn = _conn()
    out: dict[str, dict] = {}
    # SQLite caps variables per statement; chunk so a 5000-ROM catalog works.
    for i in range(0, len(wanted), 500):
        chunk = wanted[i : i + 500]
        placeholders = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"SELECT path, md5, game_id, achievements, title, match_kind "
            f"FROM ra_roms WHERE game_id != 0 AND path IN ({placeholders})",
            chunk,
        ).fetchall()
        for row in rows:
            data = {
                "ra_game_id": row["game_id"],
                "ra_achievements": row["achievements"],
                "ra_title": row["title"],
                "ra_match": row["match_kind"] or MATCH_HASH,
            }
            if row["md5"]:
                data["ra_hash"] = row["md5"]
            out[row["path"]] = data
    return out


def annotate(entries: list, payloads: list[dict]) -> None:
    """Add the ``ra_*`` fields to already-serialised catalog payloads.

    ``entries`` and ``payloads`` are parallel: ``payloads[i]`` is
    ``entries[i].to_dict()``.  Silent no-op when nothing is indexed yet, so
    a catalog served before the first pass finishes is still valid — just
    unbadged.
    """
    if not entries:
        return
    try:
        found = lookup([getattr(e, "path", "") for e in entries])
    except Exception:  # noqa: BLE001 - a broken index must not break /roms
        logger.exception("[ra_index] lookup failed")
        return
    if not found:
        return
    for entry, payload in zip(entries, payloads):
        data = found.get(getattr(entry, "path", ""))
        if data:
            payload.update(data)


def stats() -> dict[str, int]:
    conn = _conn()
    row = conn.execute(
        "SELECT COUNT(*) AS hashed,"
        "       SUM(game_id != 0) AS known,"
        "       SUM(achievements > 0) AS playable "
        "FROM ra_roms"
    ).fetchone()
    return {
        "hashed": row["hashed"] or 0,
        "known": row["known"] or 0,
        "playable": row["playable"] or 0,
        "indexing": _running.is_set(),
    }


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------


def _cached_paths(conn) -> dict[str, tuple[int, float, str]]:
    """``path -> (size, mtime, match_kind)`` for everything already indexed."""
    rows = conn.execute("SELECT path, size, mtime, match_kind FROM ra_roms").fetchall()
    return {r["path"]: (r["size"], r["mtime"], r["match_kind"] or MATCH_HASH)
            for r in rows}


def resolve_path(path: str, rom_dir: Optional[Path]) -> Path:
    """Absolute path on disk for a catalog entry's ``path``.

    Catalog paths are stored *relative to the ROM directory*, so they only
    resolve against ``settings.rom_dir``.  Absolute ones (older rows, tests)
    are passed through untouched.
    """
    candidate = Path(path)
    if rom_dir is not None and not candidate.is_absolute():
        return Path(rom_dir) / candidate
    return candidate


_MSU_KINDS = {msu.MSU1, msu.MSU_MD, msu.MD_PLUS}
#: A cart image is a few MB; anything bigger in a pack is audio.
_PACK_ROM_MAX = 16 * 1024 * 1024


def _is_msu_pack(entry) -> bool:
    return (getattr(entry, "is_bundle", False)
            and str(getattr(entry, "bundle_kind", "") or "").lower() in _MSU_KINDS)


def _hash_pack(path: Path, entry):
    """RA hash of the cartridge inside an MSU-1 / MSU-MD / MD+ pack.

    The pack is audio plus one patched cart; RA identifies the game by that
    cart alone, so it is found the same way a launcher finds it
    (:func:`shared.msu.rom_member`) and hashed like any loose ROM.  Only
    that member is read - the audio, often a gigabyte, is never touched.
    """
    import zipfile

    from shared.ra_hash import RaHash, ra_hash_bytes

    kind = str(entry.bundle_kind).lower()
    try:
        if path.is_dir():
            names = [p.name for p in path.iterdir() if p.is_file()]
            member = msu.rom_member(kind, names, lambda n: (path / n).read_text("latin-1"))
            if not member:
                return RaHash(None, "no cartridge in pack")
            return ra_hash_file(path / member, entry.system)
        with zipfile.ZipFile(path) as zf:
            infos = {i.filename: i for i in zf.infolist() if not i.is_dir()}
            root, flat = msu.strip_common_root(infos)
            full_name = {n: (f"{root}/{n}" if root else n) for n in flat}
            member = msu.rom_member(kind, flat,
                                    lambda n: zf.read(full_name[n]).decode("latin-1"))
            if not member:
                return RaHash(None, "no cartridge in pack")
            info = infos[full_name[member]]
            if info.file_size > _PACK_ROM_MAX:
                return RaHash(None, "cartridge member implausibly large")
            return ra_hash_bytes(zf.read(info), entry.system, member)
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        return RaHash(None, str(exc))


def _needs_hash(
    entry, cached: dict[str, tuple[int, float]], rom_dir: Optional[Path] = None
) -> Optional[tuple[int, float]]:
    """``(size, mtime)`` when this entry has to be hashed, else None."""
    path = getattr(entry, "path", "")
    if not path:
        return None
    if getattr(entry, "is_bundle", False) and not _is_msu_pack(entry):
        # An ordinary bundle is a folder or a zip of many files; RA hashes a
        # single ROM, so there is nothing well-defined to hash.  An MSU pack
        # is the exception - it wraps exactly one cartridge.
        return None
    if not ra_hash_supported(getattr(entry, "system", "")):
        return None
    try:
        stat = resolve_path(path, rom_dir).stat()
    except OSError:
        return None
    previous = cached.get(path)
    if previous is not None and previous[0] == stat.st_size and abs(previous[1] - stat.st_mtime) < 1:
        return None
    return stat.st_size, stat.st_mtime


def _disc_stat(entry, cached, rom_dir) -> Optional[tuple[int, float]]:
    """``(size, mtime)`` when a disc image needs reading, else None.

    Same freshness rule as a cartridge, plus one more: a row cached as a
    *title* match predates this system gaining a reader, so it is read
    again to see whether it can be identified exactly now.  Without that,
    a library indexed before the reader existed would keep the weaker
    badge forever.
    """
    path = getattr(entry, "path", "")
    if not path:
        return None
    try:
        stat = resolve_path(path, rom_dir).stat()
    except OSError:
        return None
    previous = cached.get(path)
    if previous is None or previous[2] == MATCH_TITLE:
        return stat.st_size, stat.st_mtime
    if previous[0] == stat.st_size and abs(previous[1] - stat.st_mtime) < 1:
        return None
    return stat.st_size, stat.st_mtime


class _Libraries:
    """Lazy per-console hash libraries, fetched once per indexing pass."""

    def __init__(self, cache_dir: Path, api_key: str, username: str):
        self.cache_dir = cache_dir
        self.api_key = api_key
        self.username = username
        self._cache: dict[int, Optional[RaLibrary]] = {}
        self._titles: dict[int, object] = {}

    def get(self, console_id: int) -> Optional[RaLibrary]:
        if console_id not in self._cache:
            try:
                self._cache[console_id] = fetch_library(
                    console_id,
                    cache_dir=self.cache_dir,
                    api_key=self.api_key,
                    username=self.username,
                )
            except RaAuthError:
                logger.error(
                    "[ra_index] RetroAchievements rejected SYNC_RA_API_KEY; "
                    "falling back to the public endpoints (no achievement counts)"
                )
                self.api_key = ""
                self._cache.pop(console_id, None)
                return self.get(console_id)
            except Exception as exc:  # noqa: BLE001 - offline is not fatal
                logger.warning(
                    "[ra_index] could not load RA library for console %d: %s",
                    console_id, exc,
                )
                self._cache[console_id] = None
        return self._cache[console_id]

    def titles(self, console_id: int):
        """Lazy title index for a console, or None when the library is missing."""
        if console_id not in self._titles:
            library = self.get(console_id)
            self._titles[console_id] = build_index(library) if library else None
        return self._titles[console_id]

    def find_by_title(self, name: str, system: str) -> tuple[int, int, str]:
        """``(game_id, achievements, title)`` from the game's name alone."""
        for console_id in ra_console_ids(system):
            index = self.titles(console_id)
            if index is None:
                continue
            found = index.lookup(name)
            if found:
                game_id, achievements = found
                library = self.get(console_id)
                title = library.titles.get(game_id, "") if library else ""
                return game_id, achievements, title
        return 0, 0, ""

    def find(self, md5: str, system: str) -> tuple[int, int, str]:
        """``(game_id, achievements, title)``; game_id 0 when RA does not know it."""
        for console_id in ra_console_ids(system):
            library = self.get(console_id)
            if library is None:
                continue
            game_id, title = library.lookup(md5)
            if game_id:
                count = library.achievement_count(game_id)
                return game_id, ACHIEVEMENTS_UNKNOWN if count is None else count, title or ""
        return 0, 0, ""


def refresh(
    entries: list,
    cache_dir: Path,
    api_key: str = "",
    username: str = "",
    should_stop: Optional[Callable[[], bool]] = None,
    progress_every: int = 200,
    rom_dir: Optional[Path] = None,
) -> dict[str, int]:
    """Hash and look up every catalog entry that isn't cached yet.

    Safe to call on every scan: entries whose path, size and mtime are
    unchanged are skipped without touching the disk.
    """
    if _running.is_set():
        logger.debug("[ra_index] refresh already running, skipping")
        return {"hashed": 0, "known": 0, "skipped": 0}
    _running.set()
    try:
        return _refresh(entries, cache_dir, api_key, username, should_stop,
                        progress_every, rom_dir)
    finally:
        _running.clear()


def _refresh(entries, cache_dir, api_key, username, should_stop, progress_every,
             rom_dir=None) -> dict[str, int]:
    conn = _conn()
    cached = _cached_paths(conn)

    todo = []
    by_title = []
    by_disc = []
    for entry in entries:
        system = getattr(entry, "system", "")
        if disc_hash_supported(system) and not getattr(entry, "is_bundle", False):
            stat = _disc_stat(entry, cached, rom_dir)
            if stat is not None:
                by_disc.append((entry, stat))
            continue
        if ra_title_match_only(system):
            # No file is read for these: the name is the whole input, so a
            # path already in the cache has nothing new to say.
            path = getattr(entry, "path", "")
            if path and path not in cached and not getattr(entry, "is_bundle", False):
                by_title.append(entry)
            continue
        stat = _needs_hash(entry, cached, rom_dir)
        if stat is not None:
            todo.append((entry, stat))

    # Drop rows for ROMs that left the catalog, so the table can't grow
    # without bound across renames and deletions.
    live = {getattr(e, "path", "") for e in entries}
    gone = [p for p in cached if p not in live]
    if gone:
        with _lock:
            for i in range(0, len(gone), 500):
                chunk = gone[i : i + 500]
                conn.execute(
                    f"DELETE FROM ra_roms WHERE path IN ({','.join('?' * len(chunk))})",
                    chunk,
                )
            conn.commit()

    if not todo and not by_title and not by_disc:
        return {"hashed": 0, "known": 0, "titled": 0,
                "skipped": len(cached), "removed": len(gone)}

    logger.info(
        "[ra_index] %d ROM(s) to hash, %d disc(s) to read, %d title(s) to match",
        len(todo), len(by_disc), len(by_title),
    )
    libraries = _Libraries(cache_dir, api_key, username)
    hashed = known = 0
    batch: list[tuple] = []
    now = time.time()

    for index, (entry, (size, mtime)) in enumerate(todo, 1):
        if should_stop and should_stop():
            logger.info("[ra_index] stopping early at %d/%d", index, len(todo))
            break
        full = resolve_path(entry.path, rom_dir)
        result = _hash_pack(full, entry) if _is_msu_pack(entry) else ra_hash_file(full, entry.system)
        if not result.ok:
            # Cache the failure too: an unreadable or malformed ROM should
            # not be retried on every single scan.
            batch.append((entry.path, size, mtime, "", 0, 0, "", now, MATCH_HASH))
            continue
        game_id, achievements, title = libraries.find(result.md5, entry.system)
        batch.append((entry.path, size, mtime, result.md5, game_id, achievements,
                      title, now, MATCH_HASH))
        hashed += 1
        known += bool(game_id)
        if len(batch) >= progress_every:
            _flush(conn, batch)
            batch = []
            logger.info("[ra_index] %d/%d hashed, %d known to RA", index, len(todo), known)

    _flush(conn, batch)

    # Discs we can read: hash the boot executable, and fall back to the
    # title when that exact dump is not one RA registered.
    disc_hashed = 0
    batch = []
    for entry, (size, mtime) in by_disc:
        if should_stop and should_stop():
            break
        name = getattr(entry, "name", "") or Path(entry.path).name
        md5 = hash_disc_file(resolve_path(entry.path, rom_dir), entry.system)
        game_id = achievements = 0
        title = ""
        kind = MATCH_HASH
        if md5:
            game_id, achievements, title = libraries.find(md5, entry.system)
        if not game_id:
            game_id, achievements, title = libraries.find_by_title(name, entry.system)
            kind = MATCH_TITLE
            md5 = ""
        if game_id and kind == MATCH_HASH:
            disc_hashed += 1
        batch.append((entry.path, size, mtime, md5 or "", game_id, achievements,
                      title, now, kind))
        if len(batch) >= progress_every:
            _flush(conn, batch)
            batch = []
            logger.info("[ra_index] %d disc(s) read", len(batch))
    _flush(conn, batch)

    # Disc systems with no reader: matched on the name, no file opened.
    titled = 0
    batch = []
    for entry in by_title:
        if should_stop and should_stop():
            break
        name = getattr(entry, "name", "") or Path(entry.path).name
        game_id, achievements, title = libraries.find_by_title(name, entry.system)
        batch.append((entry.path, 0, 0.0, "", game_id, achievements, title, now,
                      MATCH_TITLE))
        titled += bool(game_id)
        if len(batch) >= progress_every:
            _flush(conn, batch)
            batch = []
    _flush(conn, batch)

    logger.info(
        "[ra_index] done: %d hashed (%d known), %d disc(s) identified exactly, "
        "%d matched by title",
        hashed, known, disc_hashed, titled,
    )
    return {"hashed": hashed, "known": known, "titled": titled,
            "disc_hashed": disc_hashed,
            "skipped": len(cached), "removed": len(gone)}


def _flush(conn, batch: list[tuple]) -> None:
    global _generation
    if not batch:
        return
    _generation += 1
    with _lock:
        conn.executemany(
            "INSERT OR REPLACE INTO ra_roms "
            "(path, size, mtime, md5, game_id, achievements, title, checked_at, "
            " match_kind) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            batch,
        )
        conn.commit()


def clear() -> int:
    """Drop every cached hash so the next pass re-reads the library."""
    global _generation
    _generation += 1
    conn = _conn()
    with _lock:
        count = conn.execute("SELECT COUNT(*) AS c FROM ra_roms").fetchone()["c"]
        conn.execute("DELETE FROM ra_roms")
        conn.commit()
    return count
