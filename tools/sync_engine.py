"""Sync engine for ROM-based save syncing (RetroArch, MiSTer, Analogue Pocket, etc.).

Standalone module — does not import from the server codebase.
Uses the server's raw save API for upload/download.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests

# ---------------------------------------------------------------------------
# ROM name normalization (mirrors server/app/services/rom_id.py)
# ---------------------------------------------------------------------------

SYSTEM_CODES = frozenset({
    "GBA", "SNES", "NES", "MD", "N64", "GB", "GBC", "GG", "NGP",
    "PCE", "PS1", "SMS", "ATARI2600", "ATARI7800", "LYNX", "NEOGEO",
    "32X", "SEGACD", "TG16", "WSWAN", "WSWANC",
    "ARCADE", "MAME", "CPS1", "CPS2", "CPS3",
})

_REGION_RE = re.compile(
    r"\s*\((?:USA|Europe|Japan|World|Germany|France|Italy|Spain|Australia|"
    r"Brazil|Korea|China|Netherlands|Sweden|Denmark|Norway|Finland|Asia|"
    r"En|Ja|Fr|De|Es|It|Nl|Pt|Sv|No|Da|Fi|Ko|Zh|[A-Z][a-z,\s]+)\)",
    re.IGNORECASE,
)
_REV_RE = re.compile(
    r"\s*\((?:Rev\s*\w+|v\d[\d.]*|Version\s*\w+|Beta\s*\d*|Proto\s*\d*|Demo|Sample|Unl)\)",
    re.IGNORECASE,
)
_DISC_RE = re.compile(r"\s*\((?:Disc|Disk|CD)\s*\d+\)", re.IGNORECASE)
_EXTRA_RE = re.compile(r"\s*\([^)]+\)")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_MULTI_UNDERSCORE_RE = re.compile(r"_+")
_EMULATOR_TITLE_ID_RE = re.compile(r"^([A-Z0-9]{2,8})_([a-z0-9][a-z0-9_]{0,99})$")


def normalize_rom_name(filename: str) -> str:
    """Strip extension, region/revision tags, normalize to lowercase slug."""
    name = filename
    for _ in range(3):
        dot_idx = name.rfind(".")
        if dot_idx <= 0:
            break
        suffix = name[dot_idx + 1:]
        if 1 <= len(suffix) <= 5 and suffix.isalnum():
            name = name[:dot_idx]
        else:
            break
    name = _REGION_RE.sub("", name)
    name = _REV_RE.sub("", name)
    name = _DISC_RE.sub("", name)
    name = _EXTRA_RE.sub("", name)
    name = name.lower()
    name = _NON_ALNUM_RE.sub("_", name)
    name = _MULTI_UNDERSCORE_RE.sub("_", name).strip("_")
    return name or "unknown"


def make_title_id(system: str, rom_filename: str) -> str:
    """Return canonical title_id e.g. GBA_zelda_the_minish_cap."""
    system = system.upper().strip()
    if system not in SYSTEM_CODES:
        raise ValueError(f"Unknown system code: {system!r}")
    return f"{system}_{normalize_rom_name(rom_filename)}"


def slug_to_display_name(slug: str) -> str:
    """Convert a slug like 'zelda_the_minish_cap' to 'Zelda The Minish Cap'."""
    return slug.replace("_", " ").title()


# ---------------------------------------------------------------------------
# Device profile mappings
# ---------------------------------------------------------------------------

# RetroArch core subdirectory name -> system code
RETROARCH_CORE_MAP: dict[str, str] = {
    "Snes9x": "SNES",
    "bsnes": "SNES",
    "bsnes-mercury": "SNES",
    "mGBA": "GBA",
    "VBA-M": "GBA",
    "Nestopia": "NES",
    "FCEUmm": "NES",
    "Genesis Plus GX": "MD",
    "PicoDrive": "MD",
    "Mupen64Plus-Next": "N64",
    "ParaLLEl N64": "N64",
    "Gambatte": "GB",
    "SameBoy": "GB",
    "Gearboy": "GBC",
    "TGB Dual": "GB",
    "Mednafen NGP": "NGP",
    "Beetle PCE": "PCE",
    "Beetle GG": "GG",
    "Beetle PSX": "PS1",
    "PCSX-ReARMed": "PS1",
    "SMS Plus GX": "SMS",
    "Stella": "ATARI2600",
    "ProSystem": "ATARI7800",
    "Beetle Lynx": "LYNX",
    "FinalBurn Neo": "ARCADE",
    "MAME": "MAME",
}

# MiSTer saves folder name -> system code
MISTER_FOLDER_MAP: dict[str, str] = {
    "GBA": "GBA",
    "SNES": "SNES",
    "NES": "NES",
    "Genesis": "MD",
    "MegaDrive": "MD",
    "N64": "N64",
    "Gameboy": "GB",
    "GBC": "GBC",
    "GameGear": "GG",
    "SMS": "SMS",
    "PCEngine": "PCE",
    "TurboGrafx16": "PCE",
    "Atari2600": "ATARI2600",
    "Atari7800": "ATARI7800",
    "Lynx": "LYNX",
    "NeoGeo": "NEOGEO",
    "32X": "32X",
    "MegaCD": "SEGACD",
    "PSX": "PS1",
}

# Analogue Pocket platform folder -> system code
POCKET_FOLDER_MAP: dict[str, str] = {
    "GB": "GB",
    "GBA": "GBA",
    "GBC": "GBC",
    "GameGear": "GG",
    "SMS": "SMS",
    "NES": "NES",
    "SNES": "SNES",
    "Genesis": "MD",
    "NGP": "NGP",
    "NGPC": "NGP",
    "TurboGrafx-16": "PCE",
    "Lynx": "LYNX",
    "WonderSwan": "WSWAN",
    "WonderSwan Color": "WSWANC",
}

# Save file extensions to consider
SAVE_EXTENSIONS = {".sav", ".srm", ".mcr", ".frz", ".fs"}

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SaveFile:
    title_id: str    # e.g. GBA_zelda_the_minish_cap
    path: Path       # local file path
    hash: str        # sha256 hex
    mtime: float     # modification time (unix timestamp)
    system: str      # e.g. "GBA"
    game_name: str   # display name


@dataclass
class SyncStatus:
    save: SaveFile
    server_hash: Optional[str] = None
    server_timestamp: Optional[str] = None
    server_name: Optional[str] = None
    last_synced_hash: Optional[str] = None
    # "up_to_date" | "local_newer" | "server_newer" | "not_on_server" | "conflict"
    status: str = "unknown"


# ---------------------------------------------------------------------------
# State file (tracks last-synced hash per title, like 3DS client's state/)
# ---------------------------------------------------------------------------

STATE_FILE = Path(__file__).parent / ".sync_state.json"


def _load_state() -> dict[str, str]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_state(state: dict[str, str]) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _update_state(title_id: str, hash_val: str) -> None:
    state = _load_state()
    state[title_id] = hash_val
    _save_state(state)


# ---------------------------------------------------------------------------
# Hash helpers
# ---------------------------------------------------------------------------

def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Profile scanning
# ---------------------------------------------------------------------------

def scan_profile(profile: dict) -> list[SaveFile]:
    """Walk a profile folder and return SaveFile entries for each save found.

    Profile dict keys:
        name        : str — display name
        device_type : str — "RetroArch" | "MiSTer" | "Pocket" | "Everdrive" | "Generic"
        path        : str — root folder path
        system      : str — system code override (used for Generic/Everdrive/flat folders)
    """
    device_type = profile.get("device_type", "Generic")
    folder = Path(profile.get("path", ""))
    system_override = profile.get("system", "").upper()

    if not folder.exists():
        return []

    results: list[SaveFile] = []

    if device_type == "RetroArch":
        results = _scan_retroarch(folder)
    elif device_type == "MiSTer":
        results = _scan_mister(folder)
    elif device_type == "Pocket":
        results = _scan_pocket(folder)
    else:
        # Generic / Everdrive / flat folder — requires system_override
        if system_override and system_override in SYSTEM_CODES:
            results = _scan_flat(folder, system_override)

    return results


def _scan_flat(folder: Path, system: str) -> list[SaveFile]:
    """Scan a flat folder of saves for a single system."""
    results = []
    for f in sorted(folder.iterdir()):
        if f.is_file() and f.suffix.lower() in SAVE_EXTENSIONS:
            title_id = make_title_id(system, f.name)
            file_hash = _hash_file(f)
            slug = title_id.split("_", 1)[1] if "_" in title_id else f.stem
            results.append(SaveFile(
                title_id=title_id,
                path=f,
                hash=file_hash,
                mtime=f.stat().st_mtime,
                system=system,
                game_name=slug_to_display_name(slug),
            ))
    return results


def _scan_retroarch(root: Path) -> list[SaveFile]:
    """Scan RetroArch saves/CoreName/game.srm structure."""
    results = []
    for core_dir in sorted(root.iterdir()):
        if not core_dir.is_dir():
            continue
        system = RETROARCH_CORE_MAP.get(core_dir.name)
        if not system:
            continue
        results.extend(_scan_flat(core_dir, system))
    return results


def _scan_mister(root: Path) -> list[SaveFile]:
    """Scan MiSTer saves/<System>/ structure."""
    results = []
    for sys_dir in sorted(root.iterdir()):
        if not sys_dir.is_dir():
            continue
        system = MISTER_FOLDER_MAP.get(sys_dir.name)
        if not system:
            continue
        results.extend(_scan_flat(sys_dir, system))
    return results


def _scan_pocket(root: Path) -> list[SaveFile]:
    """Scan Analogue Pocket Memories/<Platform>/ structure."""
    results = []
    for plat_dir in sorted(root.iterdir()):
        if not plat_dir.is_dir():
            continue
        system = POCKET_FOLDER_MAP.get(plat_dir.name)
        if not system:
            continue
        results.extend(_scan_flat(plat_dir, system))
    return results


# ---------------------------------------------------------------------------
# Server comparison
# ---------------------------------------------------------------------------

def compare_with_server(
    saves: list[SaveFile],
    base_url: str,
    headers: dict,
    timeout: int = 30,
) -> list[SyncStatus]:
    """For each local save, fetch server metadata and determine sync status."""
    state = _load_state()
    results = []

    for save in saves:
        last_synced = state.get(save.title_id)
        try:
            resp = requests.get(
                f"{base_url}/api/v1/saves/{save.title_id}/meta",
                headers=headers,
                timeout=timeout,
            )
        except requests.RequestException as e:
            results.append(SyncStatus(save=save, status="error"))
            continue

        if resp.status_code == 404:
            results.append(SyncStatus(
                save=save,
                last_synced_hash=last_synced,
                status="not_on_server",
            ))
            continue

        if resp.status_code != 200:
            results.append(SyncStatus(save=save, status="error"))
            continue

        meta = resp.json()
        server_hash = meta.get("save_hash", "")
        server_ts = meta.get("server_timestamp", "")
        server_name = meta.get("name", "")

        status = _determine_status(save.hash, server_hash, last_synced)
        results.append(SyncStatus(
            save=save,
            server_hash=server_hash,
            server_timestamp=server_ts,
            server_name=server_name,
            last_synced_hash=last_synced,
            status=status,
        ))

    return results


def _determine_status(
    local_hash: str,
    server_hash: str,
    last_synced_hash: Optional[str],
) -> str:
    """Three-way hash comparison (mirrors the 3DS client's sync logic)."""
    if local_hash == server_hash:
        return "up_to_date"
    if last_synced_hash is None:
        # No sync history — treat server as authoritative if both exist
        return "conflict"
    if last_synced_hash == server_hash:
        return "local_newer"   # Only local changed
    if last_synced_hash == local_hash:
        return "server_newer"  # Only server changed
    return "conflict"          # Both changed


# ---------------------------------------------------------------------------
# Upload / download
# ---------------------------------------------------------------------------

def upload_save(
    title_id: str,
    path: Path,
    base_url: str,
    headers: dict,
    force: bool = False,
    timeout: int = 30,
) -> None:
    """Upload a raw save file to the server via POST /api/v1/saves/{title_id}/raw."""
    params = {"force": "true"} if force else {}
    data = path.read_bytes()
    resp = requests.post(
        f"{base_url}/api/v1/saves/{title_id}/raw",
        headers={**headers, "Content-Type": "application/octet-stream"},
        params=params,
        data=data,
        timeout=timeout,
    )
    resp.raise_for_status()
    # Update local state with hash of uploaded file
    local_hash = hashlib.sha256(data).hexdigest()
    _update_state(title_id, local_hash)


def download_save(
    title_id: str,
    dest_path: Path,
    base_url: str,
    headers: dict,
    timeout: int = 30,
) -> str:
    """Download save from server to dest_path. Returns the server hash."""
    resp = requests.get(
        f"{base_url}/api/v1/saves/{title_id}/raw",
        headers=headers,
        timeout=timeout,
    )
    resp.raise_for_status()
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_bytes(resp.content)
    server_hash = resp.headers.get("X-Save-Hash", hashlib.sha256(resp.content).hexdigest())
    _update_state(title_id, server_hash)
    return server_hash
