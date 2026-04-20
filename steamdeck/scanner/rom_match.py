"""Server ROM catalog index and post-scan dedup helpers.

Lifted out of ``ui.main_window`` so the matching logic can be unit
tested without dragging in PyQt6.

Two responsibilities:

1. ``_RomIndex`` — wraps the server's ``/api/v1/roms`` payload and
   answers "which catalog rows match this entry?" via title_id, exact
   filename, full slug, or region-stripped slug.  Used both to re-key
   local slug entries (``PS1_<slug>`` → ``SLUS01324``) and to flag
   each entry with the rows the user can actually download.

2. ``dedup_disc_slug_entries`` — collapses ``PS1_<slug>`` /
   ``PS2_<slug>`` / ``SAT_<slug>`` rows into their serial-keyed
   siblings when both end up in the final list (catalog enrichment
   couldn't reach them, so the server-only placeholder for the
   serial got added alongside the local slug row).
"""

from __future__ import annotations

import re

from .base import normalize_rom_name
from .models import GameEntry, SyncStatus


# Disc-based systems where the local scanner falls back to a slug
# title_id when the ISO serial can't be extracted (CHD, scrambled bin,
# etc.).  Treated as soft so a serial-keyed sibling always wins during
# the post-scan dedup pass.
DISC_SLUG_SYSTEMS = frozenset({"PS1", "PS2", "SAT"})

_BRACKET_TAG_RE = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def is_disc_slug_title_id(title_id: str, system: str) -> bool:
    """Return True for ``PS1_<slug>`` / ``PS2_<slug>`` / ``SAT_<slug>``
    fallback IDs, False for serial-format IDs (``SLUS01324``,
    ``SAT_T-12345G``)."""
    if system not in DISC_SLUG_SYSTEMS:
        return False
    prefix = f"{system}_"
    if not title_id.startswith(prefix):
        return False
    rest = title_id[len(prefix) :]
    # Saturn product codes are upper-case alphanumeric with hyphens
    # ("T-12345G", "GS-9188", "MK-81088") — the slug fallback is
    # lower-case underscore-separated.  Treat anything containing a
    # lowercase letter as the slug variant.
    return any(ch.islower() for ch in rest)


def core_name_slug(label: str | None) -> str:
    """Return a region/language-stripped slug for fuzzy ROM matching.

    ``"Chrono Trigger (USA) (En,Fr).nds"`` → ``"chrono_trigger"``.  Used
    so a local save named ``"Chrono Trigger.sav"`` still matches the
    server's regional ROM entry, which is the only difference that
    breaks strict ``normalize_rom_name`` matching for NDS-style slugs.
    """
    if not label:
        return ""
    name = label
    # Strip a single trailing extension (.sav, .nds, .chd, ...) so the
    # caller can hand us either a filename or a display name.
    dot = name.rfind(".")
    if 0 < dot and 1 <= len(name) - dot - 1 <= 5 and name[dot + 1 :].isalnum():
        name = name[:dot]
    name = _BRACKET_TAG_RE.sub("", name).strip().lower()
    name = _NON_ALNUM_RE.sub("_", name).strip("_")
    return name


class RomIndex:
    """Lookup helper over the server's ROM catalog.

    Exposes title_id, filename, full-slug (region-aware), and core-slug
    (region-stripped) lookups so we can map local rows to the catalog
    regardless of whether the local entry carries a serial, a slug
    fallback, or a save name that drops the region tag.
    """

    def __init__(self) -> None:
        self._by_title: dict[str, list[dict]] = {}
        self._by_filename: dict[tuple[str, str], str] = {}  # (system, fname) -> title_id
        self._by_norm: dict[tuple[str, str], str] = {}     # (system, full slug) -> title_id
        self._by_core: dict[tuple[str, str], str] = {}     # (system, core slug) -> title_id

    @classmethod
    def build(cls, catalog: list[dict]) -> "RomIndex":
        idx = cls()
        for rom in catalog:
            title_id = rom.get("title_id")
            system = (rom.get("system") or "").upper()
            if not title_id or not system:
                continue
            idx._by_title.setdefault(title_id, []).append(rom)
            filename = rom.get("filename") or ""
            if filename:
                idx._by_filename.setdefault((system, filename), title_id)
            for label in (rom.get("name"), filename):
                if not label:
                    continue
                full_slug = normalize_rom_name(label)
                if full_slug and full_slug != "unknown":
                    idx._by_norm.setdefault((system, full_slug), title_id)
                core_slug = core_name_slug(label)
                if core_slug and core_slug != "unknown":
                    idx._by_core.setdefault((system, core_slug), title_id)
        return idx

    def title_id_for_filename(self, system: str, filename: str) -> str | None:
        return self._by_filename.get((system.upper(), filename))

    def title_id_for_name(self, system: str, name: str) -> str | None:
        sys_up = system.upper()
        slug = normalize_rom_name(name) if name else ""
        if slug and slug != "unknown":
            tid = self._by_norm.get((sys_up, slug))
            if tid:
                return tid
        core = core_name_slug(name)
        if core and core != "unknown":
            return self._by_core.get((sys_up, core))
        return None

    def matches_for(self, entry: GameEntry) -> list[dict]:
        # Direct title_id hit covers the common case where local and
        # server agree on the identifier.
        roms = self._by_title.get(entry.title_id)
        if roms:
            return list(roms)
        # Fallbacks for entries the catalog doesn't cover under the same
        # title_id (e.g. server has SLUS01324, local has PS1_<slug>; or
        # NDS slugs that differ on region tags).
        system = entry.system
        filename = entry.rom_filename or (
            entry.rom_path.name if entry.rom_path else None
        )
        if filename:
            tid = self.title_id_for_filename(system, filename)
            if tid:
                return list(self._by_title.get(tid, []))
            tid = self.title_id_for_name(system, filename)
            if tid:
                return list(self._by_title.get(tid, []))
        tid = self.title_id_for_name(system, entry.display_name)
        if tid:
            return list(self._by_title.get(tid, []))
        return []


def dedup_disc_slug_entries(entries: list[GameEntry]) -> list[GameEntry]:
    """Merge ``PS1_<slug>`` rows into their serial-keyed siblings.

    When the local scanner falls back to a slug ID for a CHD it can't
    parse and the server hands back a save under the proper serial,
    we end up with two rows for the same game.  Match them by
    (system, region-stripped display_name) and keep the serial row,
    copying over local-only fields (ROM/save paths, hashes) from the
    slug row.
    """
    by_key: dict[tuple[str, str], list[GameEntry]] = {}
    order: list[GameEntry] = []
    for entry in entries:
        if entry.system not in DISC_SLUG_SYSTEMS:
            order.append(entry)
            continue
        slug = core_name_slug(entry.display_name)
        if not slug or slug == "unknown":
            order.append(entry)
            continue
        by_key.setdefault((entry.system, slug), []).append(entry)

    survivors: list[GameEntry] = []
    dropped: set[int] = set()
    for group in by_key.values():
        if len(group) == 1:
            survivors.append(group[0])
            continue
        serial_entries = [
            e for e in group if not is_disc_slug_title_id(e.title_id, e.system)
        ]
        slug_entries = [
            e for e in group if is_disc_slug_title_id(e.title_id, e.system)
        ]
        # Only merge when there's a slug entry to absorb.  Two serials
        # for the same name slug (Final Fantasy VII USA vs Europe) are
        # genuinely distinct titles and must both survive.
        if not slug_entries or not serial_entries:
            survivors.extend(group)
            continue
        winner = serial_entries[0]
        survivors.append(winner)
        # Other serial entries in the group survive untouched (separate
        # regional releases).
        for other_serial in serial_entries[1:]:
            survivors.append(other_serial)
        for loser in slug_entries:
            _merge_entry_into(winner, loser)
            dropped.add(id(loser))

    result: list[GameEntry] = []
    seen: set[int] = set()
    for entry in order + survivors:
        if id(entry) in dropped or id(entry) in seen:
            continue
        seen.add(id(entry))
        result.append(entry)
    return result


def _merge_entry_into(winner: GameEntry, loser: GameEntry) -> None:
    """Copy useful local-only fields from a slug entry into the
    serial-keyed entry that's about to take its place."""
    if winner.rom_path is None and loser.rom_path is not None:
        winner.rom_path = loser.rom_path
    if not winner.rom_filename and loser.rom_filename:
        winner.rom_filename = loser.rom_filename
    if winner.save_path is None and loser.save_path is not None:
        winner.save_path = loser.save_path
        winner.save_hash = loser.save_hash
        winner.save_mtime = loser.save_mtime
        winner.save_size = loser.save_size
    if winner.status in (SyncStatus.UNKNOWN, SyncStatus.SERVER_ONLY) and \
            loser.status not in (SyncStatus.UNKNOWN, SyncStatus.SERVER_ONLY):
        winner.status = loser.status
