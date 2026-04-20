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


def scan(
    emulation_path: Path,
    rom_scan_dir: Optional[str] = None,
) -> Generator[GameEntry, None, None]:
    """
    Scan DuckStation PS1 saves and ROMs.

    Yields GameEntry objects for every memory card found AND every ROM found,
    so the user sees a complete picture even when cards and ROMs don't
    line up one-to-one.
    """
    memcards_dir = _memcards_dir(emulation_path)

    # Build ROM search paths
    rom_bases: list[Path] = [emulation_path / "roms"]
    if rom_scan_dir:
        rom_bases.append(Path(rom_scan_dir))
    rom_dirs = find_rom_dirs(rom_bases, PS1_ROM_DIRS)

    # ------------------------------------------------------------------
    # Pass A — walk memcards, yield one entry per card file
    # ------------------------------------------------------------------
    yielded: dict[str, GameEntry] = {}
    card_labels_by_id: dict[str, str] = {}  # title_id -> original card stem

    if memcards_dir and memcards_dir.exists():
        card_exts = (".mcd", ".mcr")
        all_cards: list[Path] = []
        for ext in card_exts:
            all_cards.extend(memcards_dir.rglob(f"*{ext}"))

        # Group by (stem_no_slot) so we pick just one slot per game
        best_by_stem: dict[str, Path] = {}
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
            # Prefer slot _1 over _2; otherwise the most-recently-modified file.
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

        for stem_no_slot, card in best_by_stem.items():
            title_id = normalize_serial(stem_no_slot) or to_ps1_title_id(stem_no_slot)
            if title_id in yielded:
                continue
            entry = GameEntry(
                title_id=title_id,
                display_name=stem_no_slot,
                system="PS1",
                emulator="DuckStation",
                save_path=card,
            )
            _fill_save_metadata(entry, card)
            yielded[title_id] = entry
            card_labels_by_id[title_id] = stem_no_slot

    # ------------------------------------------------------------------
    # Pass B — walk ROMs, yield one entry per ROM
    # ------------------------------------------------------------------
    if rom_dirs:
        for rom_dir in rom_dirs:
            for rom_file in scan_rom_files([rom_dir], PS1_ROM_EXTENSIONS):
                label = _rom_label_for(rom_dir, rom_file)
                # Prefer real serial from ISO; fall back to filename-looking-like-serial;
                # finally fall back to a deterministic slug so CHD/PBP still appear.
                serial = (
                    read_ps1_serial(rom_file)
                    or normalize_serial(rom_file.stem)
                    or normalize_serial(label)
                )
                title_id = serial or to_ps1_title_id(label)

                # If we already have an entry under a different (weaker) ID but
                # the same display-name/label, drop the weaker one in favour of
                # this serial-backed entry.
                if serial and _is_serial_title_id(title_id):
                    stale_ids = [
                        tid
                        for tid, existing in yielded.items()
                        if not _is_serial_title_id(tid)
                        and existing.display_name.lower()
                        == _clean_card_label(label).lower()
                    ]
                    for tid in stale_ids:
                        yielded.pop(tid, None)

                if title_id in yielded:
                    # Already have an entry — enrich missing ROM info and maybe
                    # a save_path if we can match a card name.
                    existing = yielded[title_id]
                    if existing.rom_path is None:
                        existing.rom_path = rom_file
                        existing.rom_filename = rom_file.name
                    # Try to attach a card we might have missed by predicted name
                    if existing.save_path is None and memcards_dir:
                        predicted = _predict_card(memcards_dir, label)
                        if predicted is not None:
                            existing.save_path = predicted
                            _fill_save_metadata(existing, predicted)
                    continue

                clean_label = _clean_card_label(label)
                save_path: Optional[Path] = None
                if memcards_dir:
                    save_path = _predict_card(memcards_dir, label)
                    if save_path is None:
                        # Predict where DuckStation would put the card.  Only
                        # set as save_path if the file actually exists; a
                        # non-existent path confuses sync_client.download_save
                        # which falls back to the write location.
                        predicted = memcards_dir / f"{clean_label}_1.mcd"
                        save_path = predicted if predicted.exists() else None

                entry = GameEntry(
                    title_id=title_id,
                    display_name=clean_label or label,
                    system="PS1",
                    emulator="DuckStation",
                    save_path=save_path,
                    rom_path=rom_file,
                    rom_filename=rom_file.name,
                )
                if save_path is not None:
                    _fill_save_metadata(entry, save_path)
                yielded[title_id] = entry

    yield from yielded.values()


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
