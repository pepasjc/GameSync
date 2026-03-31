"""RPCS3 PS3 scanner for EmuDeck on Steam Deck."""

from pathlib import Path
from typing import Generator

from .base import sha256_dir, find_paths
from .models import GameEntry

FLATPAK_RPCS3_DATA = (
    Path.home() / ".var/app/net.rpcs3.RPCS3/data/rpcs3/dev_hdd0/home/00000001/savedata"
)
EMUDECK_RPCS3_SAVES = Path.home() / "Emulation/saves/rpcs3"

import re
_PS3_ID_RE = re.compile(r"^[A-Z]{4}\d{5}")


def scan(emulation_path: Path) -> Generator[GameEntry, None, None]:
    """
    Scan RPCS3 save data.
    Structure: savedata/<TITLE_ID>/ each containing SYS-DATA, DATA.DAT etc.
    """
    saves_root = find_paths(
        EMUDECK_RPCS3_SAVES,
        FLATPAK_RPCS3_DATA,
    )
    if saves_root is None or not saves_root.exists():
        return

    seen: dict[str, GameEntry] = {}

    for save_dir in saves_root.iterdir():
        if not save_dir.is_dir():
            continue
        folder_name = save_dir.name

        # Extract PS3 title ID (e.g. BLUS30464)
        m = _PS3_ID_RE.match(folder_name)
        if not m:
            continue

        # The game ID is always the first 9 chars (XXXX + 5 digits)
        game_id = folder_name[:9].upper()
        title_id = f"PS3_{game_id}"

        # Use the full folder name as display (includes save slot identifier)
        display_name = folder_name

        try:
            mtime = max(
                (f.stat().st_mtime for f in save_dir.rglob("*") if f.is_file()),
                default=0.0,
            )
        except Exception:
            mtime = 0.0

        # Group slots by game_id, keep latest mtime
        if title_id not in seen or mtime > seen[title_id].save_mtime:
            entry = GameEntry(
                title_id=title_id,
                display_name=game_id,
                system="PS3",
                emulator="RPCS3",
                save_path=save_dir,
                is_multi_file=True,
                save_mtime=mtime,
            )
            try:
                entry.save_hash = sha256_dir(save_dir)
                entry.save_size = sum(
                    f.stat().st_size for f in save_dir.rglob("*") if f.is_file()
                )
            except Exception:
                pass
            seen[title_id] = entry

    yield from seen.values()
