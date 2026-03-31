"""
Dolphin GameCube scanner for EmuDeck on Steam Deck.

Ported from Android's DolphinEmulator.kt:
  - Extracts 4-char game code from GCI filename regex pattern
    (e.g. "01-GM4E-MarioKart Double Dash!!.gci")
  - Groups multiple .gci files per game code
  - Most recently modified file is the primary, rest are extra_files
  - Display name extracted from filename description part
  - Title ID: GC_<code_lowercase>
"""

import re
from pathlib import Path
from typing import Generator, Optional

from .base import sha256_file, sha256_files, find_paths
from .models import GameEntry

FLATPAK_DOLPHIN_DATA = (
    Path.home() / ".var/app/org.DolphinEmu.dolphin-emu/data/dolphin-emu"
)
EMUDECK_DOLPHIN_SAVES = Path.home() / "Emulation/saves/dolphin"

# GC memory card regions and card names
GC_REGIONS = ["USA", "EUR", "JAP", "PAL", "NTSC"]
GC_CARD_NAMES = ["Card A", "Card B"]

# GCI filename regex: "<hex>-<GAMECODE>-<description>.gci"
# e.g. "01-GM4E-MarioKart Double Dash!!.gci"
_GCI_NAME_RE = re.compile(r"^[0-9A-Fa-f]+-([A-Z0-9]{4})-")


def _gci_game_code(filename: str) -> Optional[str]:
    """Extract 4-char game code from a .gci filename."""
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    m = _GCI_NAME_RE.match(stem)
    if m:
        return m.group(1).upper()
    return None


def _gci_description(name_without_ext: str) -> str:
    """
    Extract human-readable description from a .gci filename.
    "01-GM4E-MarioKart Double Dash!!" -> "MarioKart Double Dash!!"
    """
    parts = name_without_ext.split("-", maxsplit=2)
    if len(parts) >= 3:
        desc = parts[2].strip()
        return desc if desc else name_without_ext
    return name_without_ext


def scan(
    emulation_path: Path,
    rom_scan_dir: Optional[str] = None,
) -> Generator[GameEntry, None, None]:
    """
    Scan Dolphin GameCube saves (.gci files).

    Structure: GC/<REGION>/Card <X>/<HH>-<GAMECODE>-<description>.gci

    Strategy (mirrors Android's DolphinEmulator):
    1. Walk region/card directories
    2. Group .gci files by 4-char game code (from filename regex)
    3. Most recently modified file is primary, rest are extra_files
    4. Hash uses sha256_files over all files sorted by name
    """
    emu_saves = emulation_path / "saves" / "dolphin"
    gc_base = find_paths(
        emu_saves / "GC",
        EMUDECK_DOLPHIN_SAVES / "GC",
        FLATPAK_DOLPHIN_DATA / "GC",
    )
    if gc_base is None or not gc_base.exists():
        return

    # Collect all GCI files grouped by game code
    by_code: dict[str, list[Path]] = {}

    for region in GC_REGIONS:
        region_dir = gc_base / region
        if not region_dir.exists():
            continue

        # List all card directories (Card A, Card B, etc.)
        try:
            card_dirs = [d for d in region_dir.iterdir() if d.is_dir()]
        except Exception:
            continue

        for card_dir in card_dirs:
            try:
                gci_files = [
                    f
                    for f in card_dir.iterdir()
                    if f.is_file() and f.suffix.lower() == ".gci"
                ]
            except Exception:
                continue

            for gci_file in gci_files:
                code = _gci_game_code(gci_file.name)
                if code is None:
                    continue
                by_code.setdefault(code, []).append(gci_file)

    # Create one entry per game code
    for code, files in by_code.items():
        # Sort by last modified descending
        sorted_files = sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)
        primary = sorted_files[0]
        extras = sorted_files[1:]

        title_id = f"GC_{code.lower()}"
        display_name = _gci_description(primary.stem)

        entry = GameEntry(
            title_id=title_id,
            display_name=display_name,
            system="GC",
            emulator="Dolphin",
            save_path=primary,
            extra_files=extras,
        )
        try:
            # Hash all GCI files for this game, sorted by name
            all_files = sorted(files, key=lambda f: f.name)
            entry.save_hash = sha256_files(all_files)
            entry.save_mtime = primary.stat().st_mtime
            entry.save_size = sum(f.stat().st_size for f in files)
        except Exception:
            pass
        yield entry
