"""
DuckStation PS1 scanner for EmuDeck on Steam Deck.

Ported from Android's DuckStationEmulator.kt.

Strategy (mirrors Android):
  1. Walk the memcards directory and yield an entry for EVERY .mcd/.mcr file,
     using `normalize_serial()` or falling back to a slug title_id.  No ROM
     matching is required — saves for games that are no longer on disk still
     appear and can be synced.
  2. Walk ROMs and yield an entry for EVERY ROM, regardless of whether the ISO
     serial can be parsed.  CHD / PBP files (which the lightweight ISO parser
     cannot read) still participate so server-only downloads have a predicted
     card path, and so users see games that haven't created a card yet.
  3. Entries with a real PS1 product-code serial supersede the earlier
     slug-based placeholder for the same game.
"""

import re
from pathlib import Path
from typing import Generator, Optional

from .base import (
    normalize_rom_name,
    sha256_file,
    find_paths,
    to_ps1_title_id,
    normalize_serial,
    read_ps1_serial,
    find_rom_dirs,
    scan_rom_files,
    PS1_ROM_EXTENSIONS,
    PS1_ROM_DIRS,
    SHARED_CARD_NAMES,
    PS1_SERIAL_RE,
)
from .models import GameEntry

# Flatpak data path
FLATPAK_DS_DATA = Path.home() / ".var/app/org.duckstation.DuckStation/data/duckstation"

# EmuDeck symlink
EMUDECK_DS_SAVES = Path.home() / "Emulation/saves/duckstation"

# Slot suffix pattern: "_1" or "_2" immediately before the extension
_SLOT_SUFFIX_RE = re.compile(r"_\d+$")

# DuckStation card label cleaning (strip dump junk, disc markers, region codes)
_DISC_TAG_RE = re.compile(
    r"""\s*[\(\[]\s*(?:disc|cd)\s*\d+(?:\s*of\s*\d+)?\s*[\)\]]""",
    re.IGNORECASE,
)
_SERIAL_TAG_RE = re.compile(
    r"""\s*[\(\[][A-Z]{4}[-_ ]?\d{5}.*?[\)\]]""",
    re.IGNORECASE,
)
_REGION_TAG_RE = re.compile(
    r"""\s*[\(\[]\s*(?:U|E|J|USA|EUROPE|JAPAN)\s*[\)\]]""",
    re.IGNORECASE,
)


def _clean_card_label(label: str) -> str:
    """Clean a ROM/folder label for use as a DuckStation card name."""
    result = label
    result = _DISC_TAG_RE.sub("", result)
    result = _SERIAL_TAG_RE.sub("", result)
    result = _REGION_TAG_RE.sub("", result)
    result = re.sub(r"\s+", " ", result).strip()
    return result or label


def _rom_label_for(system_dir: Path, rom_file: Path) -> str:
    """
    Determine the card label from a ROM file.
    If the ROM is inside a game-specific subfolder (containing one or more
    disc images), use the folder name.  Otherwise use the filename stem.

    Mirrors Android's romLabelFor() — uses folder name whenever the ROM sits
    in a dedicated game folder (not just multi-disc), because DuckStation on
    desktop also tends to name per-game cards after the containing folder.
    """
    parent = rom_file.parent
    if parent == system_dir:
        return rom_file.stem

    try:
        image_count = sum(
            1
            for f in parent.iterdir()
            if f.is_file() and f.suffix.lower() in PS1_ROM_EXTENSIONS
        )
    except Exception:
        image_count = 1

    # Use folder name when the ROM sits in its own game folder (1 or more
    # images).  Only fall back to the filename if the parent is literally the
    # system ROMs dir (handled above).  This mirrors Android's behavior where
    # `Racing/` category folders are rare and dedicated `Game Name/` folders
    # are the norm for PS1 CHD/BIN+CUE dumps.
    return parent.name if image_count >= 1 else rom_file.stem


def _memcards_dir(emulation_path: Path) -> Optional[Path]:
    emu_saves = emulation_path / "saves" / "duckstation"
    return find_paths(
        emu_saves / "memcards",
        emu_saves / "saves",
        emu_saves,
        EMUDECK_DS_SAVES / "memcards",
        EMUDECK_DS_SAVES / "saves",
        EMUDECK_DS_SAVES,
        FLATPAK_DS_DATA / "memcards",
        FLATPAK_DS_DATA,
    )


def _is_serial_title_id(title_id: str) -> bool:
    return bool(PS1_SERIAL_RE.match(title_id))


def _fill_save_metadata(entry: GameEntry, path: Path) -> None:
    try:
        entry.save_hash = sha256_file(path)
        stat = path.stat()
        entry.save_mtime = stat.st_mtime
        entry.save_size = stat.st_size
    except Exception:
        pass


def _collect_best_cards(memcards_dir: Optional[Path]) -> dict[str, Path]:
    """Walk ``memcards_dir`` and return ``{stem_no_slot: card_path}`` with one
    best card per logical game (``_1`` wins over ``_2``; mtime breaks ties)."""
    best_by_stem: dict[str, Path] = {}
    if memcards_dir is None or not memcards_dir.exists():
        return best_by_stem

    all_cards: list[Path] = []
    for ext in (".mcd", ".mcr"):
        all_cards.extend(memcards_dir.rglob(f"*{ext}"))
    for card in sorted(all_cards):
        if not card.is_file():
            continue
        stem_no_slot = _SLOT_SUFFIX_RE.sub("", card.stem)
        if stem_no_slot.lower() in SHARED_CARD_NAMES:
            continue
        current = best_by_stem.get(stem_no_slot)
        if current is None:
            best_by_stem[stem_no_slot] = card
            continue
        cur_is_2 = current.stem.endswith("_2")
        new_is_2 = card.stem.endswith("_2")
        if cur_is_2 and not new_is_2:
            best_by_stem[stem_no_slot] = card
        elif cur_is_2 == new_is_2:
            try:
                if card.stat().st_mtime > current.stat().st_mtime:
                    best_by_stem[stem_no_slot] = card
            except Exception:
                pass
    return best_by_stem


def scan(
    emulation_path: Path,
    rom_scan_dir: Optional[str] = None,
) -> Generator[GameEntry, None, None]:
    """
    Scan DuckStation PS1 saves and ROMs.

    The scan is structured so ROMs establish game identity first and cards
    simply *attach* to whichever ROM entry they describe — this avoids the
    duplicate-row class of bug we hit when both a card-named-by-title and a
    ROM-with-real-serial referenced the same game but produced different
    title IDs under different passes.

    Flow:
      1. Walk ROMs → one entry per game, keyed by ISO serial when available
         and by a slug fallback otherwise.  Record a normalised label so
         cards can find the entry by name later.
      2. Walk cards → for each best-per-game card:
           a) If the filename already *is* a serial (DuckStation's default
              naming), attach to the matching serial entry if present.
           b) Otherwise, normalise the stem and try to match any existing
              entry's ROM-derived normalised label.  If found, attach.
           c) If still unmatched, emit a card-only entry with a slug ID so
              the save is still visible / syncable.
    """
    memcards_dir = _memcards_dir(emulation_path)

    rom_bases: list[Path] = [emulation_path / "roms"]
    if rom_scan_dir:
        rom_bases.append(Path(rom_scan_dir))
    rom_dirs = find_rom_dirs(rom_bases, PS1_ROM_DIRS)

    yielded: dict[str, GameEntry] = {}
    # {normalised_display_name: title_id} — used by the card pass to attach
    # a card to the ROM entry that already exists for the same game.
    norm_to_title: dict[str, str] = {}

    def _record(title_id: str, entry: GameEntry) -> None:
        yielded[title_id] = entry
        key = normalize_rom_name(entry.display_name)
        if key and key != "unknown":
            # Serial-backed entries win over slug entries with the same
            # normalised name — they're the authoritative identity.
            if _is_serial_title_id(title_id) or key not in norm_to_title:
                norm_to_title[key] = title_id

    # ------------------------------------------------------------------
    # Pass A — walk ROMs, one entry per game
    # ------------------------------------------------------------------
    if rom_dirs:
        for rom_dir in rom_dirs:
            for rom_file in scan_rom_files([rom_dir], PS1_ROM_EXTENSIONS):
                label = _rom_label_for(rom_dir, rom_file)
                serial = (
                    read_ps1_serial(rom_file)
                    or normalize_serial(rom_file.stem)
                    or normalize_serial(label)
                )
                title_id = serial or to_ps1_title_id(label)
                clean_label = _clean_card_label(label) or label

                if title_id in yielded:
                    existing = yielded[title_id]
                    if existing.rom_path is None:
                        existing.rom_path = rom_file
                        existing.rom_filename = rom_file.name
                    continue

                entry = GameEntry(
                    title_id=title_id,
                    display_name=clean_label,
                    system="PS1",
                    emulator="DuckStation",
                    rom_path=rom_file,
                    rom_filename=rom_file.name,
                )
                _record(title_id, entry)

    # ------------------------------------------------------------------
    # Pass B — attach cards to existing ROM entries, else create card-only
    # ------------------------------------------------------------------
    best_cards = _collect_best_cards(memcards_dir)
    for stem_no_slot, card in best_cards.items():
        serial = normalize_serial(stem_no_slot)
        if serial and serial in yielded:
            _attach_card(yielded[serial], card)
            continue

        # Try to match against a ROM entry by normalised display name so
        # cards like "Final Fantasy VII (USA)_1.mcd" find the serial-keyed
        # entry built from "Final Fantasy VII (USA) (Disc 1).bin".  The
        # normalisation strips ALL tags so multi-disc names and extra
        # parentheticals don't block the match.
        card_norm = normalize_rom_name(stem_no_slot)
        attached_tid = norm_to_title.get(card_norm)
        if attached_tid:
            _attach_card(yielded[attached_tid], card)
            continue

        # No ROM match — emit a card-only entry so the save is still shown.
        fallback_tid = serial or to_ps1_title_id(stem_no_slot)
        if fallback_tid in yielded:
            _attach_card(yielded[fallback_tid], card)
            continue
        entry = GameEntry(
            title_id=fallback_tid,
            display_name=stem_no_slot,
            system="PS1",
            emulator="DuckStation",
            save_path=card,
        )
        _fill_save_metadata(entry, card)
        _record(fallback_tid, entry)

    # ------------------------------------------------------------------
    # Pass C — for ROM entries that still lack a card, attach an existing
    # one by predicted name, OR set the DuckStation-expected write path so
    # a server Download Save has somewhere to land.  Matches Android's
    # DuckStationEmulator.buildRomEntry(), which always points saveFile at
    # "<clean label>_1.mcd" whether the file exists yet or not.
    # ------------------------------------------------------------------
    if memcards_dir:
        for entry in yielded.values():
            if entry.save_path is not None:
                continue
            label = entry.display_name
            predicted = _predict_card(memcards_dir, label)
            if predicted is not None:
                entry.save_path = predicted
                _fill_save_metadata(entry, predicted)
                continue
            # No card exists yet — point save_path at the path DuckStation
            # will create on first launch (or where a server Download Save
            # should write).  We do NOT call _fill_save_metadata because the
            # file has no content; save_hash stays None and compute_status
            # correctly reports SERVER_ONLY / NO_SAVE.
            clean_label = _clean_card_label(label) or label
            entry.save_path = memcards_dir / f"{clean_label}_1.mcd"

    yield from yielded.values()


def _attach_card(entry: GameEntry, card: Path) -> None:
    """Attach ``card`` to ``entry`` if the entry doesn't already have a save
    file — picks the most-recently-modified when both are present."""
    if entry.save_path is None:
        entry.save_path = card
        _fill_save_metadata(entry, card)
        return
    try:
        if card.stat().st_mtime > entry.save_path.stat().st_mtime:
            entry.save_path = card
            _fill_save_metadata(entry, card)
    except Exception:
        pass


def _predict_card(memcards_dir: Path, label: str) -> Optional[Path]:
    """
    Find a memory card file that matches a ROM label, normalising the
    DuckStation naming conventions.  Returns an existing file or None.
    """
    clean_label = _clean_card_label(label)
    candidates = [
        memcards_dir / f"{clean_label}_1.mcd",
        memcards_dir / f"{clean_label}_1.mcr",
        memcards_dir / f"{label}_1.mcd",
        memcards_dir / f"{label}_1.mcr",
        memcards_dir / f"{clean_label}.mcd",
        memcards_dir / f"{label}.mcd",
    ]
    for c in candidates:
        if c.exists() and c.is_file():
            return c

    # Last resort: scan all cards and match by normalised name
    target = normalize_rom_name(label)
    for ext in (".mcd", ".mcr"):
        for mcd in memcards_dir.glob(f"*{ext}"):
            stem_no_slot = _SLOT_SUFFIX_RE.sub("", mcd.stem)
            if normalize_rom_name(stem_no_slot) == target:
                return mcd
    return None
