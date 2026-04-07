"""Data models for the Steam Deck scanner."""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


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

# Colors for system badges
SYSTEM_COLOR = {
    "GBA": "#7b1fa2",
    "GB": "#388e3c",
    "GBC": "#2e7d32",
    "SNES": "#5d4037",
    "NES": "#c62828",
    "N64": "#1565c0",
    "MD": "#0d47a1",
    "SMS": "#1976d2",
    "GG": "#00838f",
    "32X": "#0277bd",
    "SEGACD": "#006064",
    "PCE": "#558b2f",
    "PCECD": "#558b2f",
    "TG16": "#33691e",
    "TGCD": "#33691e",
    "A2600": "#e65100",
    "A7800": "#bf360c",
    "LYNX": "#4e342e",
    "NGP": "#37474f",
    "NGPC": "#263238",
    "WSWAN": "#4527a0",
    "WSWANC": "#311b92",
    "NEOGEO": "#b71c1c",
    "ARCADE": "#880e4f",
    "PS1": "#37474f",
    "PS2": "#1a237e",
    "PS3": "#212121",
    "PSP": "#004d40",
    "NDS": "#1b5e20",
    "3DS": "#b71c1c",
    "GC": "#4a148c",
    "WII": "#880e4f",
    "NSW": "#e53935",
}

DEFAULT_SYSTEM_COLOR = "#424242"


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
