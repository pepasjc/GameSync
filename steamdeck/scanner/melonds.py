"""melonDS NDS scanner for EmuDeck on Steam Deck."""

from pathlib import Path
from typing import Generator, Optional

from .base import (
    NDS_ROM_DIRS,
    find_matching_nds_rom,
    find_paths,
    find_rom_dirs,
    make_title_id,
    nds_gamecode_to_title_id,
    read_nds_gamecode,
    sha256_file,
)
from .models import GameEntry

FLATPAK_MELON_DATA = Path.home() / ".var/app/net.kuribo64.melonDS/data/melonDS"
EMUDECK_MELON_SAVES = Path.home() / "Emulation/saves/melonds"

SAVE_EXTENSIONS = {".sav", ".dsv", ".bin"}


def _resolve_nds_title_id(
    save_stem: str,
    rom_search_dirs: list[Path],
) -> tuple[str, Optional[Path]]:
    """Return the best (title_id, matching_rom) pair for an NDS save.

    Prefers the canonical hex ``00048000XXXXXXXX`` form so saves roundtrip with
    the 3DS/NDS homebrew and Android clients; falls back to the slug form when
    the matching ROM can't be found on the device.
    """
    rom_file = find_matching_nds_rom(save_stem, rom_search_dirs)
    if rom_file is not None:
        gamecode = read_nds_gamecode(rom_file)
        if gamecode:
            canonical = nds_gamecode_to_title_id(gamecode)
            if canonical:
                return canonical, rom_file

    # Fallback: slug — preserves existing behaviour for users with saves but no
    # ROM available (e.g. homebrew saves, non-standard filenames).
    return make_title_id("NDS", save_stem), rom_file


def scan(
    emulation_path: Path,
    rom_scan_dir: Optional[str] = None,
) -> Generator[GameEntry, None, None]:
    """Scan melonDS NDS save files.

    ``rom_scan_dir`` is an optional external ROM root (e.g. user's SD card).
    When a ROM is found for a save the canonical hex NDS title_id is emitted;
    otherwise the scanner falls back to a ``NDS_slug`` identifier.
    """
    # melonDS can store saves next to ROMs or in a dedicated saves folder
    emu_saves = emulation_path / "saves" / "melonds"
    saves_dirs = [
        emu_saves / "saves",
        emu_saves,
        EMUDECK_MELON_SAVES / "saves",
        EMUDECK_MELON_SAVES,
        FLATPAK_MELON_DATA / "saves",
        FLATPAK_MELON_DATA,
        emulation_path / "roms" / "nds",  # saves alongside ROMs
    ]

    # Build NDS ROM search locations.  Both the EmuDeck roms/ tree and any
    # user-configured extra scan dir are considered.
    rom_bases: list[Path] = [emulation_path / "roms"]
    if rom_scan_dir:
        rom_bases.append(Path(rom_scan_dir))
    rom_search_dirs = find_rom_dirs(rom_bases, NDS_ROM_DIRS)

    seen: set[str] = set()

    for saves_dir in saves_dirs:
        if not saves_dir.exists():
            continue
        for save_file in saves_dir.iterdir():
            if not save_file.is_file():
                continue
            if save_file.suffix.lower() not in SAVE_EXTENSIONS:
                continue

            title_id, rom_file = _resolve_nds_title_id(
                save_file.stem, rom_search_dirs
            )
            if title_id in seen:
                continue
            seen.add(title_id)

            entry = GameEntry(
                title_id=title_id,
                display_name=save_file.stem,
                system="NDS",
                emulator="melonDS",
                save_path=save_file,
                rom_path=rom_file,
                rom_filename=save_file.name,
            )
            try:
                entry.save_hash = sha256_file(save_file)
                stat = save_file.stat()
                entry.save_mtime = stat.st_mtime
                entry.save_size = stat.st_size
            except Exception:
                pass
            yield entry
