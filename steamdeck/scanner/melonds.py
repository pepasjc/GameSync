"""melonDS NDS scanner for EmuDeck on Steam Deck."""

from pathlib import Path
from typing import Generator

from .base import sha256_file, find_paths, to_title_id
from .models import GameEntry

FLATPAK_MELON_DATA = Path.home() / ".var/app/net.kuribo64.melonDS/data/melonDS"
EMUDECK_MELON_SAVES = Path.home() / "Emulation/saves/melonds"

SAVE_EXTENSIONS = {".sav", ".dsv", ".bin"}


def scan(emulation_path: Path) -> Generator[GameEntry, None, None]:
    """Scan melonDS NDS save files."""
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

    seen: set[str] = set()

    for saves_dir in saves_dirs:
        if not saves_dir.exists():
            continue
        for save_file in saves_dir.iterdir():
            if not save_file.is_file():
                continue
            if save_file.suffix.lower() not in SAVE_EXTENSIONS:
                continue

            title_id = to_title_id(save_file.stem, "NDS")
            if title_id in seen:
                continue
            seen.add(title_id)

            entry = GameEntry(
                title_id=title_id,
                display_name=save_file.stem,
                system="NDS",
                emulator="melonDS",
                save_path=save_file,
            )
            try:
                entry.save_hash = sha256_file(save_file)
                stat = save_file.stat()
                entry.save_mtime = stat.st_mtime
                entry.save_size = stat.st_size
            except Exception:
                pass
            yield entry
