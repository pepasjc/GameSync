"""Dolphin GameCube/Wii scanner for EmuDeck on Steam Deck."""

import struct
from pathlib import Path
from typing import Generator

from .base import sha256_file, normalize_rom_name, find_paths
from .models import GameEntry

FLATPAK_DOLPHIN_DATA = Path.home() / ".var/app/org.DolphinEmu.dolphin-emu/data/dolphin-emu"
EMUDECK_DOLPHIN_SAVES = Path.home() / "Emulation/saves/dolphin"

# GC memory card regions
GC_REGIONS = ["USA", "EUR", "JAP"]
GC_CARD_NAMES = ["Card A", "Card B"]


def _gci_game_code(gci_path: Path) -> str | None:
    """Read 4-char game code from a .gci file header."""
    try:
        with open(gci_path, "rb") as f:
            data = f.read(6)
        if len(data) == 6:
            game_code = data[:4].decode("ascii", errors="replace")
            # Validate: printable ASCII
            if all(32 <= ord(c) < 127 for c in game_code):
                return game_code.strip()
    except Exception:
        pass
    return None


def scan(emulation_path: Path) -> Generator[GameEntry, None, None]:
    """
    Scan Dolphin GameCube saves (.gci files).
    Structure: GC/<region>/Card A/<GAMECODE>.gci
    """
    gc_base = find_paths(
        EMUDECK_DOLPHIN_SAVES / "GC",
        FLATPAK_DOLPHIN_DATA / "GC",
    )
    if gc_base is None or not gc_base.exists():
        return

    seen: set[str] = set()

    for region in GC_REGIONS:
        region_dir = gc_base / region
        if not region_dir.exists():
            continue
        for card_name in GC_CARD_NAMES:
            card_dir = region_dir / card_name
            if not card_dir.exists():
                continue
            for gci_file in card_dir.glob("*.gci"):
                if not gci_file.is_file():
                    continue
                game_code = _gci_game_code(gci_file)
                if not game_code:
                    # Fall back to filename-based slug
                    slug = normalize_rom_name(gci_file.stem)
                    title_id = f"GC_{slug}"
                    display_name = gci_file.stem
                else:
                    title_id = f"GC_{game_code.lower()}"
                    display_name = game_code

                if title_id in seen:
                    continue
                seen.add(title_id)

                entry = GameEntry(
                    title_id=title_id,
                    display_name=display_name,
                    system="GC",
                    emulator="Dolphin",
                    save_path=gci_file,
                )
                try:
                    entry.save_hash = sha256_file(gci_file)
                    stat = gci_file.stat()
                    entry.save_mtime = stat.st_mtime
                    entry.save_size = stat.st_size
                except Exception:
                    pass
                yield entry
