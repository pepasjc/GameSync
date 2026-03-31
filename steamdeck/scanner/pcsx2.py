"""PCSX2 PS2 scanner for EmuDeck on Steam Deck."""

import re
from pathlib import Path
from typing import Generator

from .base import normalize_rom_name, sha256_file, find_paths
from .models import GameEntry

FLATPAK_PCSX2_DATA = Path.home() / ".var/app/net.pcsx2.PCSX2/data/PCSX2"
EMUDECK_PCSX2_SAVES = Path.home() / "Emulation/saves/pcsx2"

_PS2_SERIAL_RE = re.compile(r"(S[LC][A-Z]{2}[-_]?\d{5})", re.IGNORECASE)


def _extract_serial(filename: str) -> str | None:
    m = _PS2_SERIAL_RE.search(filename)
    if m:
        return re.sub(r"[-_]", "", m.group(1).upper())
    return None


def scan(emulation_path: Path) -> Generator[GameEntry, None, None]:
    """Scan PCSX2 memory cards."""
    saves_dirs = [
        EMUDECK_PCSX2_SAVES / "memcards",
        EMUDECK_PCSX2_SAVES,
        FLATPAK_PCSX2_DATA / "memcards",
    ]

    seen: set[str] = set()

    for saves_dir in saves_dirs:
        if not saves_dir.exists():
            continue
        for card_file in saves_dir.iterdir():
            if not card_file.is_file():
                continue
            if card_file.suffix.lower() not in {".ps2", ".bin", ".mc"}:
                continue

            serial = _extract_serial(card_file.stem)
            if serial:
                title_id = f"PS2_{serial}"
                display_name = card_file.stem
            else:
                slug = normalize_rom_name(card_file.stem)
                title_id = f"PS2_{slug}"
                display_name = card_file.stem

            if title_id in seen:
                continue
            seen.add(title_id)

            entry = GameEntry(
                title_id=title_id,
                display_name=display_name,
                system="PS2",
                emulator="PCSX2",
                save_path=card_file,
            )
            try:
                entry.save_hash = sha256_file(card_file)
                stat = card_file.stat()
                entry.save_mtime = stat.st_mtime
                entry.save_size = stat.st_size
            except Exception:
                pass
            yield entry
