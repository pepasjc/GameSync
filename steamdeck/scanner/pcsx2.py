"""
PCSX2 PS2 scanner for EmuDeck on Steam Deck.

Ported from Android's AetherSX2Emulator.kt with the same "discover-then-enrich"
strategy as DuckStation:
  - Every memory card file (.ps2/.mc2/.bin/.mc) is yielded, regardless of
    whether a matching ROM exists on disk
  - Every PS2 ROM is yielded with a predicted card path, even if its ISO
    serial cannot be parsed (CHDs etc.)
  - Shared / default memory cards (Mcd001, Mcd002, …) are filtered out
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
    PS1_SERIAL_RE,
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


def _find_memcards_dirs(emulation_path: Path) -> list[Path]:
    emu_saves = emulation_path / "saves" / "pcsx2"
    return [
        emu_saves / "memcards",
        emu_saves / "saves",
        emu_saves,
        EMUDECK_PCSX2_SAVES / "memcards",
        EMUDECK_PCSX2_SAVES / "saves",
        EMUDECK_PCSX2_SAVES,
        FLATPAK_PCSX2_DATA / "memcards",
    ]


def scan(
    emulation_path: Path,
    rom_scan_dir: Optional[str] = None,
) -> Generator[GameEntry, None, None]:
    """
    Scan PCSX2 PS2 saves and ROMs.

    Yields an entry for every memory card file AND every ROM, so users see
    a complete picture even when cards and ROMs don't line up.
    """
    memcards_dirs = _find_memcards_dirs(emulation_path)
    existing_memcards_dirs = [d for d in memcards_dirs if d.exists()]
    primary_memcards_dir = existing_memcards_dirs[0] if existing_memcards_dirs else None

    # Build ROM search paths
    rom_bases: list[Path] = [emulation_path / "roms", emulation_path]
    if rom_scan_dir:
        rom_bases.append(Path(rom_scan_dir))
    rom_dirs = find_rom_dirs(rom_bases, PS2_ROM_DIRS)

    # Build ROM serial map: stem -> serial, and serial/name -> (rom, stem)
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

    yielded: dict[str, GameEntry] = {}

    # ------------------------------------------------------------------
    # Pass A — memory cards
    # ------------------------------------------------------------------
    for saves_dir in existing_memcards_dirs:
        try:
            card_files = [
                f
                for f in saves_dir.iterdir()
                if f.is_file() and f.suffix.lower() in _MCD_EXTENSIONS
            ]
        except Exception:
            continue
        for card_file in sorted(card_files):
            stem = card_file.stem

            # Skip shared default cards
            if PS2_SHARED_CARD_RE.match(stem):
                continue

            # Figure out the best title_id for this card.
            serial = rom_serial_by_stem.get(stem) or rom_serial_by_stem.get(
                normalize_rom_name(stem)
            )
            matched_rom: Optional[Path] = None
            if not serial:
                serial = _extract_embedded_serial(stem)
            if not serial:
                norm = normalize_rom_name(stem)
                if norm in rom_by_name:
                    rom_file, _ = rom_by_name[norm]
                    matched_rom = rom_file
                    serial = read_ps2_serial(rom_file)
            if serial and serial in rom_by_serial:
                matched_rom = rom_by_serial[serial][0]

            if serial:
                title_id = serial
            else:
                # Fall back to a slug derived from the card filename so the
                # entry is still visible / syncable.
                title_id = normalize_serial(stem) or to_title_id(stem, "PS2")

            if title_id in yielded:
                continue

            entry = GameEntry(
                title_id=title_id,
                display_name=stem,
                system="PS2",
                emulator="PCSX2",
                save_path=card_file,
                rom_path=matched_rom,
                rom_filename=matched_rom.name if matched_rom else None,
            )
            _fill_save_metadata(entry, card_file)
            yielded[title_id] = entry

    # ------------------------------------------------------------------
    # Pass B — ROMs (create entries even without parseable serial)
    # ------------------------------------------------------------------
    for rom_file in scan_rom_files(rom_dirs, PS2_ROM_EXTENSIONS):
        stem = rom_file.stem
        serial = read_ps2_serial(rom_file) or normalize_serial(stem)
        title_id = serial or to_title_id(stem, "PS2")

        # If we're about to add a serial-backed entry and we already have a
        # weaker slug entry with the same display name, drop the stale one.
        if serial and _is_serial_title_id(title_id):
            for tid in [
                tid
                for tid, existing in yielded.items()
                if not _is_serial_title_id(tid)
                and existing.display_name.lower() == stem.lower()
            ]:
                yielded.pop(tid, None)

        if title_id in yielded:
            existing = yielded[title_id]
            if existing.rom_path is None:
                existing.rom_path = rom_file
                existing.rom_filename = rom_file.name
            continue

        # Predict the card path so a later server download has somewhere to
        # land; only set save_path if the file actually exists on disk so
        # we don't falsely show "synced" or tamper with hashes.
        save_path: Optional[Path] = None
        if primary_memcards_dir:
            predicted = primary_memcards_dir / f"{stem}.ps2"
            if predicted.exists():
                save_path = predicted

        entry = GameEntry(
            title_id=title_id,
            display_name=stem,
            system="PS2",
            emulator="PCSX2",
            save_path=save_path,
            rom_path=rom_file,
            rom_filename=rom_file.name,
        )
        if save_path is not None:
            _fill_save_metadata(entry, save_path)
        yielded[title_id] = entry

    yield from yielded.values()
