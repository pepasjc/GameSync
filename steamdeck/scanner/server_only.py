"""Generic server-only placeholder entries.

The scanner-level modules (``duckstation``, ``pcsx2``, ``retroarch``, ...)
emit one entry per ROM/save found on the local disk.  Saves that only exist
on the server — because the user hasn't yet installed the ROM for that game
on this device — would therefore be invisible in the UI.

``rpcs3`` and ``dolphin`` already solve this for PS3 and GameCube by
producing per-system placeholders.  This module fills in the gap for every
other system: it walks the server's save list, skips anything already
covered by a local scan, and emits a minimal ``GameEntry`` with
``status=SERVER_ONLY`` so the Save Info dialog can surface the "Download
ROM" action for that title.

The placeholders intentionally leave ``save_path`` unset.  Once the user
downloads the ROM we trigger a rescan; the system-specific scanner then
produces a real entry (with a real save_path) that merges with the server
data, and the Save / Download buttons come alive.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable

_REPO_ROOT = str(Path(__file__).parent.parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from shared.systems import normalize_system_code  # noqa: E402

from .models import GameEntry, SyncStatus


# Systems that have their own dedicated builder.  We skip them here so we
# don't clobber the save_path placeholders those builders already compute.
_HANDLED_BY_DEDICATED_BUILDER = {"PS3", "GC", "3DS"}


# Best-effort emulator label per system, used only for display in the Save
# Info dialog.  Unknown systems simply get "Server" so the column stays
# populated.
_SYSTEM_EMULATOR: dict[str, str] = {
    "PS1": "DuckStation",
    "PS2": "PCSX2",
    "PSP": "PPSSPP",
    "VITA": "Vita3K",
    "NDS": "melonDS",
    "3DS": "Citra",
    "SAT": "RetroArch",
    "DC": "RetroArch",
    "MD": "RetroArch",
    "SEGACD": "RetroArch",
    "SMS": "RetroArch",
    "GG": "RetroArch",
    "32X": "RetroArch",
    "GBA": "RetroArch",
    "GB": "RetroArch",
    "GBC": "RetroArch",
    "NES": "RetroArch",
    "SNES": "RetroArch",
    "N64": "RetroArch",
    "WII": "Dolphin",
}

# Systems whose title IDs are bare product codes (no "SYS_" prefix) but are
# still recognisable by their well-known starting letters.  Used only as a
# last-ditch fallback when the server response carries no platform metadata
# — ``_resolve_system`` always prefers ``console_type`` / ``platform`` first,
# which disambiguates prefixes shared between PS1 and PS2.
_CODE_PREFIX_TO_SYSTEM = {
    "SLUS": "PS1",
    "SLES": "PS1",
    "SCUS": "PS1",
    "SCES": "PS1",
    "SLPS": "PS1",
    "SLPM": "PS1",
    "SCPS": "PS1",
    "SCPM": "PS1",
    "NPJH": "PSP",
    "UCUS": "PSP",
    "ULUS": "PSP",
    "UCES": "PSP",
    "ULJS": "PSP",
    "NPUG": "PSP",
    "NPJG": "PSP",
    "BLUS": "PS3",
    "BLES": "PS3",
    "BCUS": "PS3",
    "BCES": "PS3",
}


def _system_from_title_id(title_id: str) -> str:
    """Best-effort mapping from a canonical title_id to a system code."""
    upper = title_id.upper()
    # SYS_slug style (e.g. "GBA_pokemon_emerald_usa")
    if "_" in upper:
        sys_prefix = upper.split("_", 1)[0]
        if 2 <= len(sys_prefix) <= 8 and sys_prefix.isalnum():
            return sys_prefix
    # Sony bare-code style (SLUS01234, NPJH50001, BLUS30464-SAVE)
    for prefix, sys in _CODE_PREFIX_TO_SYSTEM.items():
        if upper.startswith(prefix):
            return sys
    return ""


def _resolve_system(title_id: str, info: dict) -> str:
    """Pick a system for an arbitrary server save record.

    Server metadata may carry non-canonical labels for the same console
    (``"Genesis"`` vs ``"MD"``, ``"PSX"`` vs ``"PS1"``).  Normalising here
    keeps the system filter from showing duplicate entries for one console.
    """
    for key in ("console_type", "system", "platform"):
        value = info.get(key)
        if value:
            normalized = normalize_system_code(value)
            if normalized:
                return normalized
    return normalize_system_code(_system_from_title_id(title_id))


def build_server_only_entries(
    server_saves: dict[str, dict],
    seen_ids: Iterable[str],
    emulation_path: Path,
) -> list[GameEntry]:
    """
    Emit a placeholder ``GameEntry`` for each server save that is not
    already represented by a local scan and is not handled by a
    system-specific builder (PS3 / GameCube).  The entries carry enough
    info (title_id, display name, system, server metadata) for the Save
    Info dialog to render them and offer ROM downloads.
    """
    seen_set = set(seen_ids)
    results: list[GameEntry] = []

    for title_id, info in server_saves.items():
        if title_id in seen_set:
            continue
        system = _resolve_system(title_id, info)
        if not system:
            continue
        if system in _HANDLED_BY_DEDICATED_BUILDER:
            # Those builders have already run and decided whether to emit a
            # placeholder with a real save_path.  Don't double up.
            continue

        emulator = _SYSTEM_EMULATOR.get(system, "Server")
        results.append(
            GameEntry(
                title_id=title_id,
                display_name=info.get("name")
                or info.get("game_name")
                or title_id,
                system=system,
                emulator=emulator,
                status=SyncStatus.SERVER_ONLY,
                server_hash=info.get("save_hash"),
                server_title_id=info.get("title_id") or title_id,
                server_timestamp=info.get("client_timestamp"),
                server_size=info.get("save_size"),
            )
        )
    return results
