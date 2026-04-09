"""
PPSSPP PSP scanner for EmuDeck on Steam Deck.

Mirrors the desktop sync_engine approach:
  - Scans PSP/SAVEDATA slot directories
  - Detects PS1 PSone Classics vs native PSP via product code prefix
  - Groups all slot dirs by 9-char product code, keeps newest slot only
  - PS1 saves get bare serial as title_id (e.g. "SLUS00975")
  - PSP saves use full slot dir name if valid, else 9-char product code
  - Uses sha256_dir_files (content-only hash) matching server bundle hash
"""

import re
from pathlib import Path
from typing import Generator, Optional

from .base import (
    sha256_dir_files,
    find_paths,
    PSX_RETAIL_PREFIXES,
)
from .models import GameEntry

FLATPAK_PPSSPP_DATA = Path.home() / ".var/app/org.ppsspp.PPSSPP/data/PSP"
EMUDECK_PPSSPP_SAVES = Path.home() / "Emulation/saves/ppsspp"

# PSP product code: 4 uppercase letters + 5 digits
_PRODUCT_CODE_RE = re.compile(r"^[A-Z]{4}\d{5}")

# Valid slot directory name for server (alphanumeric only, 4-31 chars)
_VALID_SLOT_DIR_RE = re.compile(r"^[A-Za-z0-9]{4,31}$")


def _extract_product_code(dir_name: str) -> Optional[str]:
    """Extract 9-char product code from a PSP slot directory name."""
    m = _PRODUCT_CODE_RE.match(dir_name)
    return m.group(0) if m else None


def _detect_system(product_code: str) -> str:
    """
    Detect whether a PSP save is actually a PS1 PSone Classic.
    PS1 retail prefixes and PSN codes (NP*) -> "PS1"
    Everything else -> "PSP"
    """
    upper = product_code.upper()
    prefix = upper[:4] if len(upper) >= 4 else ""
    if prefix in PSX_RETAIL_PREFIXES:
        return "PS1"
    if upper[:2] == "NP":
        return "PS1"
    return "PSP"


def scan(
    emulation_path: Path,
    rom_scan_dir: Optional[str] = None,
) -> Generator[GameEntry, None, None]:
    """
    Scan PPSSPP save data.

    Structure: SAVEDATA/<SLOT_NAME>/ — each is a separate save slot folder.
    e.g. SAVEDATA/UCUS99082DATA/, SAVEDATA/SLUS00975/

    Strategy (mirrors Android's PpssppEmulator):
    1. List subdirectories in SAVEDATA
    2. Extract 9-char product code from each
    3. Detect system (PS1 vs PSP) via prefix
    4. Title ID: bare serial for PS1, full slot name for PSP (if valid)
    5. Hash using sha256_dir_files (content-only, no paths)
    """
    emu_saves = emulation_path / "saves" / "ppsspp"
    saves_root = find_paths(
        emu_saves / "saves" / "SAVEDATA",
        emu_saves / "SAVEDATA",
        emu_saves / "saves",
        emu_saves,
        EMUDECK_PPSSPP_SAVES / "SAVEDATA",
        EMUDECK_PPSSPP_SAVES,
        FLATPAK_PPSSPP_DATA / "SAVEDATA",
    )
    if saves_root is None or not saves_root.exists():
        return

    # Group by product code, keeping the most recently modified slot.
    # Multiple slot directories may exist for the same game (e.g.
    # ULUS10567DATA00, ULUS10567SYSDATA).  We show only one entry per game,
    # using the newest slot's directory as save_path and title_id.
    best_entries: dict[str, GameEntry] = {}  # keyed by 9-char product_code

    for slot_dir in sorted(saves_root.iterdir()):
        if not slot_dir.is_dir():
            continue

        folder_name = slot_dir.name
        product_code = _extract_product_code(folder_name)
        if product_code is None:
            continue

        system = _detect_system(product_code)

        # Build title ID from the slot directory name
        if system == "PS1":
            # PS1 PSone Classics: bare serial (e.g. "SLUS00975")
            title_id = product_code
        elif _VALID_SLOT_DIR_RE.match(folder_name):
            # PSP with valid alphanumeric slot name: use full name
            title_id = folder_name
        else:
            # Fallback to just the product code
            title_id = product_code

        # Compute most-recently-modified time across all files
        try:
            mtime = max(
                (f.stat().st_mtime for f in slot_dir.rglob("*") if f.is_file()),
                default=0.0,
            )
        except Exception:
            mtime = 0.0

        # Keep only the most recent slot per product code
        if (
            product_code in best_entries
            and mtime <= best_entries[product_code].save_mtime
        ):
            continue

        entry = GameEntry(
            title_id=title_id,
            display_name=product_code,
            system=system,
            emulator="PPSSPP",
            save_path=slot_dir,
            is_psp_slot=True,
            save_mtime=mtime,
        )
        try:
            entry.save_hash = sha256_dir_files(slot_dir)
            entry.save_size = sum(
                f.stat().st_size for f in slot_dir.rglob("*") if f.is_file()
            )
        except Exception:
            pass
        best_entries[product_code] = entry

    yield from best_entries.values()
