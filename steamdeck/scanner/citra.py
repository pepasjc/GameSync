"""Azahar / Citra 3DS save scanner for Steam Deck."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Generator

_REPO_ROOT = str(Path(__file__).parent.parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from shared.systems import normalize_system_code  # noqa: E402

from .base import find_paths, sha256_dir_tree_files
from .models import GameEntry, SyncStatus

_ZERO_ID = "00000000000000000000000000000000"
_HEX8_RE = re.compile(r"^[0-9A-Fa-f]{8}$")
_TITLE_ID_RE = re.compile(r"^[0-9A-Fa-f]{16}$")

_FLATPAK_AZAHAR_TITLE_ROOT = (
    Path.home()
    / ".var/app/org.azahar_emu.Azahar/data/azahar-emu/sdmc/Nintendo 3DS"
    / _ZERO_ID
    / _ZERO_ID
    / "title"
)
_LOCAL_AZAHAR_TITLE_ROOT = (
    Path.home()
    / ".local/share/azahar-emu/sdmc/Nintendo 3DS"
    / _ZERO_ID
    / _ZERO_ID
    / "title"
)
_LOCAL_CITRA_TITLE_ROOT = (
    Path.home()
    / ".local/share/citra-emu/sdmc/Nintendo 3DS"
    / _ZERO_ID
    / _ZERO_ID
    / "title"
)
_LOCAL_LIME3DS_TITLE_ROOT = (
    Path.home()
    / ".local/share/lime3ds-emu/sdmc/Nintendo 3DS"
    / _ZERO_ID
    / _ZERO_ID
    / "title"
)


def _title_root_suffix() -> Path:
    return Path("sdmc") / "Nintendo 3DS" / _ZERO_ID / _ZERO_ID / "title"


def resolve_title_root(emulation_path: Path) -> Path | None:
    suffix = _title_root_suffix()
    return find_paths(
        emulation_path / "storage" / "azahar-emu" / suffix,
        emulation_path / "storage" / "citra-emu" / suffix,
        emulation_path / "storage" / "lime3ds-emu" / suffix,
        emulation_path / "saves" / "azahar-emu" / suffix,
        emulation_path / "saves" / "azahar" / suffix,
        emulation_path / "saves" / "citra-emu" / suffix,
        emulation_path / "saves" / "citra" / suffix,
        emulation_path / "saves" / "lime3ds-emu" / suffix,
        _FLATPAK_AZAHAR_TITLE_ROOT,
        _LOCAL_AZAHAR_TITLE_ROOT,
        _LOCAL_CITRA_TITLE_ROOT,
        _LOCAL_LIME3DS_TITLE_ROOT,
    )


def default_save_path(emulation_path: Path, title_id: str) -> Path:
    if not _TITLE_ID_RE.match(title_id):
        raise ValueError(f"Invalid 3DS title_id: {title_id}")

    title_root = resolve_title_root(emulation_path)
    if title_root is None:
        title_root = emulation_path / "storage" / "azahar-emu" / _title_root_suffix()

    upper = title_id.upper()
    return title_root / upper[:8] / upper[8:] / "data" / "00000001"


def _dir_mtime(save_dir: Path) -> float:
    try:
        return max((fp.stat().st_mtime for fp in save_dir.rglob("*") if fp.is_file()), default=0.0)
    except Exception:
        return 0.0


def _dir_size(save_dir: Path) -> int:
    total = 0
    for fp in save_dir.rglob("*"):
        if fp.is_file():
            total += fp.stat().st_size
    return total


def scan(emulation_path: Path) -> Generator[GameEntry, None, None]:
    title_root = resolve_title_root(emulation_path)
    if title_root is None or not title_root.exists():
        return

    for high_dir in sorted(title_root.iterdir()):
        if not high_dir.is_dir() or not _HEX8_RE.match(high_dir.name):
            continue

        for low_dir in sorted(high_dir.iterdir()):
            if not low_dir.is_dir() or not _HEX8_RE.match(low_dir.name):
                continue

            title_id = f"{high_dir.name}{low_dir.name}".upper()
            save_dir = low_dir / "data" / "00000001"
            if not save_dir.is_dir():
                continue
            if not any(fp.is_file() for fp in save_dir.rglob("*")):
                continue

            entry = GameEntry(
                title_id=title_id,
                display_name=title_id,
                system="3DS",
                emulator="Azahar",
                save_path=save_dir,
                is_multi_file=True,
                save_mtime=_dir_mtime(save_dir),
            )
            try:
                entry.save_hash = sha256_dir_tree_files(save_dir)
                entry.save_size = _dir_size(save_dir)
            except Exception:
                pass
            yield entry


def build_server_only_entries(
    server_saves: dict[str, dict],
    seen_ids: set[str],
    emulation_path: Path,
) -> list[GameEntry]:
    results: list[GameEntry] = []

    for title_id, info in server_saves.items():
        if title_id in seen_ids:
            continue
        if not _TITLE_ID_RE.match(title_id):
            continue

        system = normalize_system_code(
            str(
                info.get("system")
                or info.get("console_type")
                or info.get("platform")
                or ""
            )
        )
        if system != "3DS":
            continue

        results.append(
            GameEntry(
                title_id=title_id,
                display_name=info.get("name") or info.get("game_name") or title_id,
                system="3DS",
                emulator="Azahar",
                save_path=default_save_path(emulation_path, title_id),
                is_multi_file=True,
                status=SyncStatus.SERVER_ONLY,
                server_hash=info.get("save_hash"),
                server_title_id=info.get("title_id") or title_id,
                server_timestamp=info.get("client_timestamp"),
                server_size=info.get("save_size"),
            )
        )

    return results
