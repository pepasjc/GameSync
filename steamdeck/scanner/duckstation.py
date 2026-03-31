"""DuckStation PS1 scanner for EmuDeck on Steam Deck."""

import re
from pathlib import Path
from typing import Generator

from .base import normalize_rom_name, sha256_file, find_paths
from .models import GameEntry

# Flatpak data path
FLATPAK_DS_DATA = Path.home() / ".var/app/org.duckstation.DuckStation/data/duckstation"

# EmuDeck symlink
EMUDECK_DS_SAVES = Path.home() / "Emulation/saves/duckstation"

# PS1 serial pattern: SLUS-01234, SCUS-94163, SLES-01234, SCES-01234, SLPM-86xxx, etc.
_SERIAL_RE = re.compile(r"([A-Z]{4}[-_]?\d{5})", re.IGNORECASE)


def _extract_serial(filename: str) -> str | None:
    """Extract PS1 serial from a memory card filename."""
    m = _SERIAL_RE.search(filename)
    if m:
        # Normalize: uppercase, remove dash/underscore
        return re.sub(r"[-_]", "", m.group(1).upper())
    return None


def scan(emulation_path: Path) -> Generator[GameEntry, None, None]:
    """Scan DuckStation memory cards."""
    saves_dir = find_paths(
        EMUDECK_DS_SAVES / "memcards",
        EMUDECK_DS_SAVES,
        FLATPAK_DS_DATA / "memcards",
        FLATPAK_DS_DATA,
    )
    if saves_dir is None or not saves_dir.exists():
        return

    seen: set[str] = set()

    for mcd_file in saves_dir.rglob("*.mcd"):
        if not mcd_file.is_file():
            continue

        serial = _extract_serial(mcd_file.stem)
        if serial:
            title_id = serial  # Stored as-is, e.g. "SLUS01279"
            display_name = mcd_file.stem
        else:
            # Shared card or non-standard name: use normalized slug
            slug = normalize_rom_name(mcd_file.stem)
            title_id = f"PS1_{slug}"
            display_name = mcd_file.stem

        if title_id in seen:
            continue
        seen.add(title_id)

        entry = GameEntry(
            title_id=title_id,
            display_name=display_name,
            system="PS1",
            emulator="DuckStation",
            save_path=mcd_file,
        )
        try:
            entry.save_hash = sha256_file(mcd_file)
            stat = mcd_file.stat()
            entry.save_mtime = stat.st_mtime
            entry.save_size = stat.st_size
        except Exception:
            pass
        yield entry
