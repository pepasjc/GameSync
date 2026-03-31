"""PPSSPP PSP scanner for EmuDeck on Steam Deck."""

from pathlib import Path
from typing import Generator

from .base import sha256_dir, find_paths
from .models import GameEntry

FLATPAK_PPSSPP_DATA = Path.home() / ".var/app/org.ppsspp.PPSSPP/data/PSP"
EMUDECK_PPSSPP_SAVES = Path.home() / "Emulation/saves/ppsspp"

# PSP game ID pattern: UCUS99xxx, ULUS10xxx, UCES01xxx, NPJH50xxx, etc.
import re
_PSP_ID_RE = re.compile(r"^[A-Z]{4}\d{5}$")


def _is_psp_game_id(name: str) -> bool:
    return bool(_PSP_ID_RE.match(name))


def scan(emulation_path: Path) -> Generator[GameEntry, None, None]:
    """
    Scan PPSSPP save data.
    Structure: SAVEDATA/<GAME_ID>/ — each is a separate save slot folder.
    """
    saves_root = find_paths(
        EMUDECK_PPSSPP_SAVES / "SAVEDATA",
        EMUDECK_PPSSPP_SAVES,
        FLATPAK_PPSSPP_DATA / "SAVEDATA",
    )
    if saves_root is None or not saves_root.exists():
        return

    seen_game_ids: dict[str, GameEntry] = {}

    for slot_dir in saves_root.iterdir():
        if not slot_dir.is_dir():
            continue
        # PPSSPP saves: GAME_ID (first 9 chars) + optional suffix
        # e.g. UCUS99082DATA → game ID is UCUS99082
        folder_name = slot_dir.name

        # Extract game ID (first 9 chars if follows pattern)
        game_id = None
        for length in (9, 4):
            candidate = folder_name[:length].upper()
            if _is_psp_game_id(candidate):
                game_id = candidate
                break
        if game_id is None:
            if _is_psp_game_id(folder_name):
                game_id = folder_name
            else:
                continue

        title_id = f"PSP_{game_id}"

        # Track the most-recently-modified slot per game
        try:
            mtime = max(
                (f.stat().st_mtime for f in slot_dir.rglob("*") if f.is_file()),
                default=0.0,
            )
        except Exception:
            mtime = 0.0

        if title_id not in seen_game_ids or mtime > seen_game_ids[title_id].save_mtime:
            entry = GameEntry(
                title_id=title_id,
                display_name=game_id,
                system="PSP",
                emulator="PPSSPP",
                save_path=slot_dir,
                is_multi_file=True,
                save_mtime=mtime,
            )
            try:
                entry.save_hash = sha256_dir(slot_dir)
                entry.save_size = sum(
                    f.stat().st_size for f in slot_dir.rglob("*") if f.is_file()
                )
            except Exception:
                pass
            seen_game_ids[title_id] = entry

    yield from seen_game_ids.values()
