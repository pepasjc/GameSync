"""
DuckStation PS1 scanner for EmuDeck on Steam Deck.

Ported from Android's DuckStationEmulator.kt:
  - Scans ROMs first to build a serial map (via ISO 9660 SYSTEM.CNF parsing)
  - Matches memory card files (.mcd/.mcr) to ROMs by serial or name
  - Only shows saves that have matching ROMs on disk
  - Falls back to slug-based title IDs when no serial is found
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
    If the ROM is inside a game-specific subfolder (containing multiple disc
    images, suggesting multi-disc game), use the folder name.
    Otherwise use the filename stem.
    Mirrors Android's romLabelFor().
    """
    parent = rom_file.parent
    if parent == system_dir:
        return rom_file.stem

    # Check if parent looks like a multi-disc game folder
    image_count = sum(
        1
        for f in parent.iterdir()
        if f.is_file() and f.suffix.lower() in PS1_ROM_EXTENSIONS
    )
    # Use folder name only for multi-disc folders (2+ images);
    # single-image subfolders use the filename (handles "games/" etc.)
    return parent.name if image_count >= 2 else rom_file.stem


def scan(
    emulation_path: Path,
    rom_scan_dir: Optional[str] = None,
) -> Generator[GameEntry, None, None]:
    """
    Scan DuckStation PS1 saves, yielding only saves that match ROMs.

    Strategy (mirrors Android):
    1. Find memcards directory
    2. Scan ROMs to build serial/name maps
    3. For each ROM, create an entry with the predicted card path
    4. Also check existing .mcd files for serials matching known ROMs
    """
    # Find memcards directory — prefer user-configured emulation_path
    emu_saves = emulation_path / "saves" / "duckstation"
    memcards_dir = find_paths(
        emu_saves / "memcards",
        emu_saves / "saves",
        emu_saves,
        EMUDECK_DS_SAVES / "memcards",
        EMUDECK_DS_SAVES / "saves",
        EMUDECK_DS_SAVES,
        FLATPAK_DS_DATA / "memcards",
        FLATPAK_DS_DATA,
    )

    # Build ROM search paths
    rom_bases: list[Path] = [emulation_path / "roms"]
    if rom_scan_dir:
        rom_bases.append(Path(rom_scan_dir))

    rom_dirs = find_rom_dirs(rom_bases, PS1_ROM_DIRS)

    # Scan ROMs and build serial -> (rom_file, label) map
    rom_serials: dict[str, tuple[Path, str]] = {}  # serial -> (rom, label)
    rom_labels: dict[str, tuple[Path, str]] = {}  # normalized_label -> (rom, label)

    for rom_dir in rom_dirs:
        for rom_file in scan_rom_files([rom_dir], PS1_ROM_EXTENSIONS):
            label = _rom_label_for(rom_dir, rom_file)
            serial = read_ps1_serial(rom_file)

            if serial and serial not in rom_serials:
                rom_serials[serial] = (rom_file, label)

            # Also track by normalized label for non-serial matching
            norm_label = normalize_rom_name(label)
            if norm_label not in rom_labels:
                rom_labels[norm_label] = (rom_file, label)

    seen: set[str] = set()

    # Strategy 1: Create entries from ROMs (preferred, serial-backed)
    if memcards_dir and memcards_dir.exists():
        for serial, (rom_file, label) in rom_serials.items():
            title_id = serial
            if title_id in seen:
                continue

            # Predict the card file DuckStation would create
            clean_label = _clean_card_label(label)
            card_file = memcards_dir / f"{clean_label}_1.mcd"

            # Also check if a serial-named card exists
            serial_card = memcards_dir / f"{serial}_1.mcd"
            if serial_card.exists():
                card_file = serial_card
            elif not card_file.exists():
                # Try finding any .mcd that matches the label
                for mcd in memcards_dir.glob("*.mcd"):
                    stem_no_slot = _SLOT_SUFFIX_RE.sub("", mcd.stem)
                    if normalize_rom_name(stem_no_slot) == normalize_rom_name(label):
                        card_file = mcd
                        break

            seen.add(title_id)
            entry = GameEntry(
                title_id=title_id,
                display_name=clean_label or label,
                system="PS1",
                emulator="DuckStation",
                save_path=card_file if card_file.exists() else None,
                rom_path=rom_file,
                rom_filename=rom_file.name,
            )
            if entry.save_path and entry.save_path.exists():
                try:
                    entry.save_hash = sha256_file(entry.save_path)
                    stat = entry.save_path.stat()
                    entry.save_mtime = stat.st_mtime
                    entry.save_size = stat.st_size
                except Exception:
                    pass
            yield entry

    # Strategy 2: Check existing .mcd files with serials from filenames
    if memcards_dir and memcards_dir.exists():
        for mcd_file in sorted(memcards_dir.rglob("*.mcd")):
            if not mcd_file.is_file():
                continue

            stem_no_slot = _SLOT_SUFFIX_RE.sub("", mcd_file.stem)
            if stem_no_slot.lower() in SHARED_CARD_NAMES:
                continue

            serial = normalize_serial(stem_no_slot)
            if serial and serial in seen:
                continue

            # Track matched ROM for rom_path / rom_filename
            matched_rom: Optional[Path] = None

            # Only include if we can match to a ROM
            if serial and serial in rom_serials:
                title_id = serial
                matched_rom = rom_serials[serial][0]
            else:
                # Check by normalized name
                norm = normalize_rom_name(stem_no_slot)
                if norm in rom_labels:
                    rom_file_match, orig_label = rom_labels[norm]
                    matched_rom = rom_file_match
                    # Try to get serial from the matched ROM
                    rom_serial = read_ps1_serial(rom_file_match)
                    if rom_serial:
                        title_id = rom_serial
                    else:
                        # Use ROM filename (with region tags) for slug, not card stem
                        title_id = to_ps1_title_id(rom_file_match.stem)
                else:
                    # No matching ROM found — skip this card
                    continue

            if title_id in seen:
                continue
            seen.add(title_id)

            # Prefer slot _1 over _2
            if mcd_file.stem.endswith("_2"):
                slot1 = mcd_file.parent / f"{stem_no_slot}_1.mcd"
                if slot1.exists():
                    mcd_file = slot1

            entry = GameEntry(
                title_id=title_id,
                display_name=stem_no_slot,
                system="PS1",
                emulator="DuckStation",
                save_path=mcd_file,
                rom_path=matched_rom,
                rom_filename=matched_rom.name if matched_rom else None,
            )
            try:
                entry.save_hash = sha256_file(mcd_file)
                stat = mcd_file.stat()
                entry.save_mtime = stat.st_mtime
                entry.save_size = stat.st_size
            except Exception:
                pass
            yield entry

    # Also check .mcr files
    if memcards_dir and memcards_dir.exists():
        for mcr_file in sorted(memcards_dir.rglob("*.mcr")):
            if not mcr_file.is_file():
                continue

            stem_no_slot = _SLOT_SUFFIX_RE.sub("", mcr_file.stem)
            if stem_no_slot.lower() in SHARED_CARD_NAMES:
                continue

            serial = normalize_serial(stem_no_slot)
            if serial and serial in seen:
                continue

            matched_rom: Optional[Path] = None

            if serial and serial in rom_serials:
                title_id = serial
                matched_rom = rom_serials[serial][0]
            else:
                norm = normalize_rom_name(stem_no_slot)
                if norm in rom_labels:
                    rom_file_match, _ = rom_labels[norm]
                    matched_rom = rom_file_match
                    rom_serial = read_ps1_serial(rom_file_match)
                    if rom_serial:
                        title_id = rom_serial
                    else:
                        title_id = to_ps1_title_id(rom_file_match.stem)
                else:
                    continue

            if title_id in seen:
                continue
            seen.add(title_id)

            entry = GameEntry(
                title_id=title_id,
                display_name=stem_no_slot,
                system="PS1",
                emulator="DuckStation",
                save_path=mcr_file,
                rom_path=matched_rom,
                rom_filename=matched_rom.name if matched_rom else None,
            )
            try:
                entry.save_hash = sha256_file(mcr_file)
                stat = mcr_file.stat()
                entry.save_mtime = stat.st_mtime
                entry.save_size = stat.st_size
            except Exception:
                pass
            yield entry
