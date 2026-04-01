"""RPCS3 PS3 scanner for EmuDeck on Steam Deck."""

from pathlib import Path
from typing import Generator

from .base import find_paths, sha256_dir_tree_files
from .models import GameEntry, SyncStatus

FLATPAK_RPCS3_DATA = (
    Path.home() / ".var/app/net.rpcs3.RPCS3/data/rpcs3/dev_hdd0/home/00000001/savedata"
)
EMUDECK_RPCS3_SAVES = Path.home() / "Emulation/saves/rpcs3"
EMUDECK_RPCS3_SAVE_DIRS = Path.home() / "Emulation/saves/rpcs3/saves"

import re

_PS3_ID_RE = re.compile(r"^[A-Z]{4}\d{5}")


def resolve_saves_root(emulation_path: Path) -> Path | None:
    emu_saves = emulation_path / "saves" / "rpcs3"
    return find_paths(
        emu_saves / "saves",
        emu_saves,
        emulation_path / "rpcs3" / "saves",
        emulation_path / "rpcs3",
        EMUDECK_RPCS3_SAVE_DIRS,
        EMUDECK_RPCS3_SAVES,
        FLATPAK_RPCS3_DATA,
    )


def default_save_path(emulation_path: Path, title_id: str) -> Path:
    saves_root = resolve_saves_root(emulation_path)
    if saves_root is None:
        saves_root = emulation_path / "saves" / "rpcs3" / "saves"
    return saves_root / title_id


def build_server_only_entries(
    server_saves: dict[str, dict],
    seen_ids: set[str],
    emulation_path: Path,
) -> list[GameEntry]:
    """Create downloadable RPCS3 placeholders for PS3 saves only present on the server."""
    results: list[GameEntry] = []

    for title_id, info in server_saves.items():
        if title_id in seen_ids:
            continue

        system = (info.get("system") or "").upper()
        if system != "PS3":
            continue

        results.append(
            GameEntry(
                title_id=title_id,
                display_name=info.get("name") or info.get("game_name") or title_id,
                system="PS3",
                emulator="RPCS3",
                save_path=default_save_path(emulation_path, title_id),
                is_multi_file=True,
                status=SyncStatus.SERVER_ONLY,
                server_hash=info.get("save_hash"),
                server_timestamp=info.get("client_timestamp"),
                server_size=info.get("save_size"),
            )
        )

    return results


def scan(emulation_path: Path) -> Generator[GameEntry, None, None]:
    """
    Scan RPCS3 save data.
    Structure: savedata/<TITLE_ID>/ each containing SYS-DATA, DATA.DAT etc.
    """
    saves_root = resolve_saves_root(emulation_path)
    if saves_root is None or not saves_root.exists():
        return

    seen: dict[str, GameEntry] = {}

    for save_dir in sorted(saves_root.iterdir()):
        if not save_dir.is_dir():
            continue
        folder_name = save_dir.name

        # Extract PS3 title ID (e.g. BLUS30464)
        m = _PS3_ID_RE.match(folder_name)
        if not m:
            continue

        # The game ID is always the first 9 chars (XXXX + 5 digits). Keep the
        # full save-directory name as the server slot key so multiple PS3 save
        # slots for the same title do not collapse into one.
        game_id = folder_name[:9].upper()
        title_id = folder_name.upper()

        # Use the full folder name as display (includes save slot identifier)
        display_name = folder_name

        try:
            mtime = max(
                (f.stat().st_mtime for f in save_dir.rglob("*") if f.is_file()),
                default=0.0,
            )
        except Exception:
            mtime = 0.0

        # Keep the full folder name visible so different save slots are
        # distinguishable before server-side name enrichment kicks in.
        if title_id not in seen or mtime > seen[title_id].save_mtime:
            entry = GameEntry(
                title_id=title_id,
                display_name=display_name,
                system="PS3",
                emulator="RPCS3",
                save_path=save_dir,
                is_multi_file=True,
                save_mtime=mtime,
            )
            try:
                entry.save_hash = sha256_dir_tree_files(save_dir)
                entry.save_size = sum(
                    f.stat().st_size for f in save_dir.rglob("*") if f.is_file()
                )
            except Exception:
                pass
            seen[title_id] = entry

    yield from seen.values()
