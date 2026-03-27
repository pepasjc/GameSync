import os
import re
import json
import requests
from pathlib import Path

from PyQt6.QtGui import QColor


CONFIG_FILE = Path(__file__).parent / "config.json"

ALL_CONSOLE_TYPES = [
    "All", "3DS", "NDS", "PSP", "PS3", "VITA", "PSX",
    "GBA", "SNES", "NES", "MD", "N64", "GB", "GBC",
    "GG", "SMS", "PCE", "PS1", "PS2", "NGP", "DC", "GC",
    "ATARI2600", "ATARI7800", "LYNX", "NEOGEO", "32X", "SEGACD",
    "WSWAN", "WSWANC", "ARCADE", "MAME",
]

DEVICE_TYPES = ["Generic", "RetroArch", "MiSTer", "Analogue Pocket", "Pocket (openFPGA)", "Everdrive", "MEGA EverDrive", "EmuDeck", "MemCard Pro"]

SYSTEM_CHOICES = [
    "GBA", "SNES", "NES", "MD", "N64", "GB", "GBC", "GG", "NGP",
    "PCE", "PS1", "PS2", "PSP", "PS3", "SMS", "ATARI2600", "ATARI7800", "LYNX", "NEOGEO",
    "32X", "SAT", "SEGACD", "TG16", "WSWAN", "WSWANC", "DC", "NDS", "GC",
    "ARCADE", "MAME",
]

STATUS_COLORS = {
    "up_to_date":    QColor(0, 200, 0),
    "local_newer":   QColor(0, 160, 255),
    "server_newer":  QColor(255, 200, 0),
    "not_on_server": QColor(180, 180, 180),
    "server_only":   QColor(180, 100, 255),
    "conflict":      QColor(220, 60, 60),
    "mapping_conflict": QColor(255, 120, 40),
    "local_duplicate_conflict": QColor(255, 140, 80),
    "error":         QColor(200, 0, 200),
    "unknown":       QColor(180, 180, 180),
}

STATUS_LABELS = {
    "up_to_date":    "Up to date",
    "local_newer":   "Local newer",
    "server_newer":  "Server newer",
    "not_on_server": "Not on server",
    "server_only":   "Server only",
    "conflict":      "Conflict",
    "mapping_conflict": "Mapping conflict",
    "local_duplicate_conflict": "Local duplicates differ",
    "error":         "Error",
    "unknown":       "Unknown",
}


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "host": os.environ.get("SYNC_HOST", "localhost"),
        "port": int(os.environ.get("SYNC_PORT", "8000")),
        "api_key": os.environ.get("SYNC_API_KEY", "anything"),
        "profiles": [],
    }


def save_config(config: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")


def get_api_headers() -> dict:
    config = load_config()
    return {"X-API-Key": config.get("api_key", "anything")}


def get_base_url() -> str:
    config = load_config()
    host = config.get("host", "localhost")
    port = config.get("port", "8000")
    return f"http://{host}:{port}"


# ---------------------------------------------------------------------------
# Console type detection
# ---------------------------------------------------------------------------

_HEX_TITLE_RE = re.compile(r"^[0-9A-Fa-f]{16}$")
_PS_PREFIX_RE = re.compile(r"^[A-Z]{4}\d{5}")
_EMULATOR_RE = re.compile(r"^([A-Z0-9]{2,8})_[a-z0-9]")

_PS3_PREFIXES = {"BCAS", "BCES", "BCJS", "BCKS", "BCUS",
                 "BLAS", "BLES", "BLJM", "BLJS", "BLKS", "BLUS",
                 "NPHA", "NPEA", "NPJA", "NPUA", "NPEB", "NPJB", "NPUB"}

# PS1 retail disc prefixes — uniquely identify PS1 physical/PSN discs vs PSP games.
# Used to classify product codes from PPSSPP PSone Classics, MemCard Pro, etc.
_PSX_RETAIL_PREFIXES = {
    # North America
    "SLUS", "SCUS", "PAPX",
    # Europe
    "SLES", "SCES", "SCED",
    # Japan
    "SLPS", "SLPM", "SCPS", "SCPM",
    # Other regions
    "SLAJ", "SLEJ", "SCAJ",
}


def detect_console_type(title_id: str) -> str:
    title_id = title_id.strip()
    m = _EMULATOR_RE.match(title_id)
    if m:
        return m.group(1)
    uid = title_id.upper()
    if _HEX_TITLE_RE.match(uid):
        return "3DS"
    if _PS_PREFIX_RE.match(uid):
        base = uid[:9]
        if base.startswith("PCS"):
            return "VITA"
        if uid[:4] in _PS3_PREFIXES:
            return "PS3"
        if uid[:4] in _PSX_RETAIL_PREFIXES:
            return "PSX"
        return "PSP"
    return "NDS"


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def fetch_all_saves() -> list[dict]:
    resp = requests.get(
        f"{get_base_url()}/api/v1/titles", headers=get_api_headers(), timeout=30
    )
    resp.raise_for_status()
    return resp.json().get("titles", [])


def fetch_history(title_id: str, console_id: str = "") -> list[dict]:
    params = {"console_id": console_id} if console_id else {}
    resp = requests.get(
        f"{get_base_url()}/api/v1/saves/{title_id}/history",
        headers=get_api_headers(),
        params=params,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("versions", [])


def delete_save(title_id: str, console_id: str = "") -> None:
    params = {"console_id": console_id} if console_id else {}
    resp = requests.delete(
        f"{get_base_url()}/api/v1/saves/{title_id}",
        headers=get_api_headers(),
        params=params,
        timeout=30,
    )
    resp.raise_for_status()


def restore_history(title_id: str, timestamp: int, console_id: str = "") -> None:
    params = {"console_id": console_id} if console_id else {}
    resp = requests.get(
        f"{get_base_url()}/api/v1/saves/{title_id}/history/{timestamp}",
        headers=get_api_headers(),
        params=params,
        timeout=30,
    )
    resp.raise_for_status()

    upload_params = {"force": "true"}
    if console_id:
        upload_params["console_id"] = console_id

    upload_resp = requests.post(
        f"{get_base_url()}/api/v1/saves/{title_id}",
        headers=get_api_headers(),
        params=upload_params,
        data=resp.content,
        timeout=30,
    )
    upload_resp.raise_for_status()


def download_raw_save(title_id: str, dest_path: Path) -> None:
    """Download the raw save bytes to dest_path."""
    resp = requests.get(
        f"{get_base_url()}/api/v1/saves/{title_id}/raw",
        headers=get_api_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_bytes(resp.content)
