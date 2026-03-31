"""
PCSX2 PS2 scanner for EmuDeck on Steam Deck.

Ported from Android's AetherSX2Emulator.kt:
  - Scans PS2 ROMs and extracts serials via ISO 9660 SYSTEM.CNF parsing
  - Matches memory card files (.ps2/.mc2/.bin) to ROMs
  - Only shows saves that have matching ROMs on disk
  - Filters out shared/default memory cards (Mcd001, Mcd002, etc.)
"""

import re
from pathlib import Path
from typing import Generator, Optional

from .base import (
    normalize_rom_name,
    sha256_file,
    find_paths,
    to_title_id,
    normalize_serial,
    read_ps2_serial,
    find_rom_dirs,
    scan_rom_files,
    PS2_ROM_EXTENSIONS,
    PS2_ROM_DIRS,
    PS2_SHARED_CARD_RE,
)
from .models import GameEntry

FLATPAK_PCSX2_DATA = Path.home() / ".var/app/net.pcsx2.PCSX2/data/PCSX2"
EMUDECK_PCSX2_SAVES = Path.home() / "Emulation/saves/pcsx2"

# Embedded serial pattern in filename: "SLUS20002_Final Fantasy X.ps2"
_EMBEDDED_SERIAL_RE = re.compile(
    r"^([A-Z]{4}\d{5})(?:[_\-\s].+)?$",
    re.IGNORECASE,
)

_MCD_EXTENSIONS = {".ps2", ".bin", ".mc", ".mc2"}


def _extract_embedded_serial(stem: str) -> Optional[str]:
    """Extract a PS2 serial embedded at the start of a filename."""
    m = _EMBEDDED_SERIAL_RE.match(stem)
    if m:
        serial = m.group(1).upper()
        if re.match(r"^[A-Z]{4}\d{5}$", serial):
            return serial
    return None


def scan(
    emulation_path: Path,
    rom_scan_dir: Optional[str] = None,
) -> Generator[GameEntry, None, None]:
    """
    Scan PCSX2 PS2 saves, yielding only saves that match ROMs.

    Strategy (mirrors Android's AetherSX2Emulator):
    1. Build a ROM serial map by scanning PS2 ISOs
    2. For each memory card, match to a ROM by serial or name
    3. Skip shared/default memory cards
    """
    # Find memcards directories — prefer user-configured emulation_path
    emu_saves = emulation_path / "saves" / "pcsx2"
    memcards_dirs = [
        emu_saves / "memcards",
        emu_saves / "saves",
        emu_saves,
        EMUDECK_PCSX2_SAVES / "memcards",
        EMUDECK_PCSX2_SAVES / "saves",
        EMUDECK_PCSX2_SAVES,
        FLATPAK_PCSX2_DATA / "memcards",
    ]

    # Build ROM search paths
    rom_bases: list[Path] = [emulation_path / "roms", emulation_path]
    if rom_scan_dir:
        rom_bases.append(Path(rom_scan_dir))

    rom_dirs = find_rom_dirs(rom_bases, PS2_ROM_DIRS)

    # Build ROM serial map: stem -> serial, and serial -> (rom, stem)
    rom_serial_by_stem: dict[str, str] = {}
    rom_by_serial: dict[str, tuple[Path, str]] = {}
    rom_by_name: dict[str, tuple[Path, str]] = {}

    for rom_file in scan_rom_files(rom_dirs, PS2_ROM_EXTENSIONS):
        serial = read_ps2_serial(rom_file)
        stem = rom_file.stem
        norm = normalize_rom_name(stem)

        if serial:
            rom_serial_by_stem[stem] = serial
            rom_serial_by_stem[norm] = serial
            if serial not in rom_by_serial:
                rom_by_serial[serial] = (rom_file, stem)

        if norm not in rom_by_name:
            rom_by_name[norm] = (rom_file, stem)

    seen: set[str] = set()

    # Scan memory cards and match to ROMs
    for saves_dir in memcards_dirs:
        if not saves_dir.exists():
            continue
        for card_file in sorted(saves_dir.iterdir()):
            if not card_file.is_file():
                continue
            if card_file.suffix.lower() not in _MCD_EXTENSIONS:
                continue

            stem = card_file.stem

            # Skip shared default cards
            if PS2_SHARED_CARD_RE.match(stem):
                continue

            # Try to find the serial for this card
            # 1. From ROM serial map by filename match
            serial = rom_serial_by_stem.get(stem)
            matched_rom: Optional[Path] = None

            # 2. From embedded serial in filename
            if not serial:
                serial = _extract_embedded_serial(stem)

            # 3. From normalized name match to ROM
            if not serial:
                norm = normalize_rom_name(stem)
                if norm in rom_by_name:
                    rom_file, _ = rom_by_name[norm]
                    matched_rom = rom_file
                    serial = read_ps2_serial(rom_file)

            # Determine title_id
            if serial:
                title_id = serial
                # Verify this serial maps to a known ROM
                if serial in rom_by_serial:
                    matched_rom = rom_by_serial[serial][0]
            else:
                # No serial found - check if we have a ROM with matching name
                norm = normalize_rom_name(stem)
                if norm not in rom_by_name:
                    # No matching ROM found — skip this card
                    continue
                matched_rom = rom_by_name[norm][0]
                title_id = to_title_id(matched_rom.stem, "PS2")

            if title_id in seen:
                continue
            seen.add(title_id)

            entry = GameEntry(
                title_id=title_id,
                display_name=stem,
                system="PS2",
                emulator="PCSX2",
                save_path=card_file,
                rom_path=matched_rom,
                rom_filename=matched_rom.name if matched_rom else None,
            )
            try:
                entry.save_hash = sha256_file(card_file)
                stat = card_file.stat()
                entry.save_mtime = stat.st_mtime
                entry.save_size = stat.st_size
            except Exception:
                pass
            yield entry

    # Also create entries from ROMs that have serials but no card yet
    # (so server-only downloads can find a save path)
    for serial, (rom_file, rom_stem) in rom_by_serial.items():
        if serial in seen:
            continue
        seen.add(serial)

        # Predict where PCSX2 would write the card
        best_memcards_dir = None
        for d in memcards_dirs:
            if d.exists():
                best_memcards_dir = d
                break

        save_path = None
        if best_memcards_dir:
            save_path = best_memcards_dir / f"{rom_stem}.ps2"

        entry = GameEntry(
            title_id=serial,
            display_name=rom_stem,
            system="PS2",
            emulator="PCSX2",
            save_path=save_path,
            rom_path=rom_file,
            rom_filename=rom_file.name,
        )
        # No local save hash since the card doesn't exist yet
        yield entry
