"""Data models for the Steam Deck scanner."""

import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

# Make the repo root importable so 'shared' can be found.
_REPO_ROOT = str(Path(__file__).parent.parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from shared.systems import SYSTEM_COLOR, DEFAULT_SYSTEM_COLOR  # noqa: E402


class SyncStatus(Enum):
    UNKNOWN = "unknown"
    SYNCED = "synced"
    LOCAL_NEWER = "local_newer"  # local changed since last sync -> needs upload
    SERVER_NEWER = "server_newer"  # server changed since last sync -> needs download
    LOCAL_ONLY = "local_only"  # server has no save yet -> upload
    SERVER_ONLY = "server_only"  # no local save -> can download
    CONFLICT = "conflict"  # both changed independently
    NO_SAVE = "no_save"  # game found but no save file exists locally


STATUS_LABEL = {
    SyncStatus.UNKNOWN: "Unknown",
    SyncStatus.SYNCED: "Synced",
    SyncStatus.LOCAL_NEWER: "Upload",
    SyncStatus.SERVER_NEWER: "Download",
    SyncStatus.LOCAL_ONLY: "Local Only",
    SyncStatus.SERVER_ONLY: "Server Only",
    SyncStatus.CONFLICT: "Conflict",
    SyncStatus.NO_SAVE: "No Save",
}

# Hex colors for status badges
STATUS_COLOR = {
    SyncStatus.UNKNOWN: "#7e7e7e",
    SyncStatus.SYNCED: "#4caf50",
    SyncStatus.LOCAL_NEWER: "#ff9800",
    SyncStatus.SERVER_NEWER: "#1a9fff",
    SyncStatus.LOCAL_ONLY: "#ff9800",
    SyncStatus.SERVER_ONLY: "#1a9fff",
    SyncStatus.CONFLICT: "#e84118",
    SyncStatus.NO_SAVE: "#555555",
}

# SYSTEM_COLOR and DEFAULT_SYSTEM_COLOR imported from shared.systems above.


@dataclass
class GameEntry:
    title_id: str  # Server slot key, e.g. "GBA_pokemon_emerald"
    display_name: str  # Human-readable name
    system: str  # System code, e.g. "GBA"
    emulator: str  # Source emulator name
    save_path: Optional[Path] = None  # Local save file (or dir for multi-file)
    rom_path: Optional[Path] = None  # ROM file (for RetroArch games)
    rom_filename: Optional[str] = (
        None  # Original ROM filename (for server serial lookup)
    )
    save_hash: Optional[str] = None  # SHA-256 of local save
    save_mtime: float = 0.0
    save_size: int = 0
    is_multi_file: bool = False  # True for PPSSPP/RPCS3 (dir-based saves)
    is_psp_slot: bool = False  # True for PSP SAVEDATA slot dirs (bundle protocol)
    extra_files: list[Path] = field(
        default_factory=list
    )  # Additional save files (GC multi-gci)
    # Sync state
    status: SyncStatus = SyncStatus.UNKNOWN
    server_hash: Optional[str] = None
    server_title_id: Optional[str] = None
    server_timestamp: Optional[float] = None
    server_size: Optional[int] = None
    last_synced_hash: Optional[str] = None
