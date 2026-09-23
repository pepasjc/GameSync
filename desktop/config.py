import os
import re
import json
import requests
from pathlib import Path

from PyQt6.QtGui import QColor

from systems import ALL_CONSOLE_TYPES, SYSTEM_CHOICES  # noqa: F401 (re-exported)

CONFIG_FILE = Path(__file__).parent / "config.json"

DEVICE_TYPES = [
    "Generic",
    "RetroArch",
    "MiSTer",
    "Analogue Pocket",
    "Pocket (openFPGA)",
    "Everdrive",
    "MEGA EverDrive",
    "SAROO",
    "Super SD System 3",
    "EmuDeck",
    "MemCard Pro",
    "MemCard Pro FTP",
    "MemCard Pro DC",
    "PSIO",
    "CD Folder",
    "OPL",
    "GDEMU",
    "openMenu",
]

STATUS_COLORS = {
    "up_to_date": QColor(0, 200, 0),
    "local_newer": QColor(0, 160, 255),
    "server_newer": QColor(255, 200, 0),
    "not_on_server": QColor(180, 180, 180),
    "server_only": QColor(180, 100, 255),
    "conflict": QColor(220, 60, 60),
    "mapping_conflict": QColor(255, 120, 40),
    "local_duplicate_conflict": QColor(255, 140, 80),
    "error": QColor(200, 0, 200),
    "unknown": QColor(180, 180, 180),
}

STATUS_LABELS = {
    "up_to_date": "Up to date",
    "local_newer": "Local newer",
    "server_newer": "Server newer",
    "not_on_server": "Not on server",
    "server_only": "Server only",
    "conflict": "Conflict",
    "mapping_conflict": "Mapping conflict",
    "local_duplicate_conflict": "Local duplicates differ",
    "error": "Error",
    "unknown": "Unknown",
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


def get_retroachievements_credentials() -> tuple[str, str]:
    """``(username, web_api_key)`` for RA's web API; both empty when unset."""
    ra_cfg = load_config().get("retroachievements", {}) or {}
    return (
        str(ra_cfg.get("username", "") or "").strip(),
        str(ra_cfg.get("web_api_key", "") or "").strip(),
    )


def get_sd_card_location() -> str:
    """Current drive/root where the removable SD card is mounted right now.

    SD card readers get a different drive letter on each reconnect, so profiles
    flagged ``is_sd_card`` store their paths against a fixed letter and have it
    rewritten to this value at operation time. Empty = no remap.
    """
    return str(load_config().get("sd_card_location", "") or "").strip()


_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def remap_sd_path(path: str, sd_location: str | None = None) -> str:
    """Swap the leading drive letter of *path* for the SD card's current drive.

    ``J:/MEGA/gamedata`` with sd_location ``K:`` -> ``K:/MEGA/gamedata``.
    Paths without a drive letter, or an empty/invalid sd_location, are returned
    unchanged. Only the drive letter of *sd_location* is used (any subpath in it
    is ignored), so ``K:``, ``K:\\`` and ``K:\\foo`` all map drives to ``K:``.
    """
    if not path:
        return path
    if sd_location is None:
        sd_location = get_sd_card_location()
    m = _DRIVE_RE.match((sd_location or "").strip())
    if not m:
        return path
    new_drive = m.group(0)
    if _DRIVE_RE.match(path):
        return new_drive + path[2:]
    return path


def resolve_profile_for_sd(profile: dict) -> dict:
    """Return a profile copy with SD-card paths remapped to the current drive.

    No-op (returns the original object) unless the profile is flagged
    ``is_sd_card`` and a global ``sd_card_location`` is configured.
    """
    if not isinstance(profile, dict) or not profile.get("is_sd_card"):
        return profile
    sd = get_sd_card_location()
    if not sd:
        return profile
    p = dict(profile)
    for key in ("path", "save_folder", "dat_path"):
        if p.get(key):
            p[key] = remap_sd_path(p[key], sd)
    if isinstance(p.get("systems"), list):
        p["systems"] = [
            {
                **s,
                **(
                    {"save_folder": remap_sd_path(s["save_folder"], sd)}
                    if s.get("save_folder")
                    else {}
                ),
                **(
                    {"rom_folder": remap_sd_path(s["rom_folder"], sd)}
                    if s.get("rom_folder")
                    else {}
                ),
            }
            for s in p["systems"]
            if isinstance(s, dict)
        ]
    return p


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
# Slug ids are lowercase (``GBA_zelda``), but serial/gamecode ids are not
# (``DC_T1249M``, ``GC_GALE01``, ``SAT_T-4507G``) — both are emulator-format
# title ids and both must resolve to their system here.
_EMULATOR_RE = re.compile(r"^([A-Z0-9]{2,8})_[A-Za-z0-9]")

_PS3_PREFIXES = {
    "BCAS",
    "BCES",
    "BCJS",
    "BCKS",
    "BCUS",
    "BLAS",
    "BLES",
    "BLJM",
    "BLJS",
    "BLKS",
    "BLUS",
    "NPHA",
    "NPEA",
    "NPJA",
    "NPUA",
    "NPEB",
    "NPJB",
    "NPUB",
}

# PS1 retail disc prefixes — uniquely identify PS1 physical/PSN discs vs PSP games.
# Used to classify product codes from PPSSPP PSone Classics, MemCard Pro, etc.
_PSX_RETAIL_PREFIXES = {
    # North America
    "SLUS",
    "SCUS",
    "PAPX",
    # Europe
    "SLES",
    "SCES",
    "SCED",
    # Japan
    "SLPS",
    "SLPM",
    "SCPS",
    "SCPM",
    # Other regions
    "SLAJ",
    "SLEJ",
    "SCAJ",
}


def detect_console_type(title_id: str) -> str:
    title_id = title_id.strip()
    m = _EMULATOR_RE.match(title_id)
    if m:
        return m.group(1)
    uid = title_id.upper()
    if _HEX_TITLE_RE.match(uid):
        # Wii U titles are 00050000xxxxxxxx; 3DS is 00040..., DSiWare 00048...
        if uid.startswith("00050"):
            return "WIIU"
        return "3DS"
    if _PS_PREFIX_RE.match(uid):
        base = uid[:9]
        if base.startswith("PCS"):
            return "VITA"
        if uid[:4] in _PS3_PREFIXES:
            return "PS3"
        if uid[:4] in _PSX_RETAIL_PREFIXES:
            return "PS1"
        return "PSP"
    return "NDS"


def format_display_game_name(name: str, console_type: str = "") -> str:
    """Return a UI-friendly game name without changing any underlying identifiers.

    For PlayStation disc-based titles, the server or local matcher can legitimately
    return names like ``Parasite Eve (USA) (Disc 1)``. DuckStation and similar tools
    usually present the shared game title without the disc marker, so the desktop UI
    mirrors that behavior while keeping the raw title ID / file path untouched.
    """
    text = (name or "").strip()
    if not text:
        return text

    if (console_type or "").upper() == "PS1":
        text = re.sub(
            r"\s*[\(\[]\s*(disc|cd)\s*\d+(?:\s*of\s*\d+)?\s*[\)\]]",
            "",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(r"\s+", " ", text).strip()
    return text


# ---------------------------------------------------------------------------
# Wii U game names
#
# A Wii U save is keyed by a 16-hex title id whose low word is not the product
# code, so no DAT — server-side or here — can name it.  The name lives in the
# title's meta.xml, which on a PC means the local Cemu install: either its
# mlc01 (installed titles) or the loose game dumps it launches.
# ---------------------------------------------------------------------------


def get_cemu_dirs() -> list[Path]:
    """Cemu install / game folders to search for meta.xml, config first."""
    config = load_config()
    dirs: list[Path] = []

    configured = str(config.get("cemu_dir", "") or "").strip()
    if configured:
        dirs.append(Path(configured))

    home = Path.home()
    dirs.extend(
        [
            home / "Documents" / "Cemu",
            home / "Cemu",
            home / "AppData" / "Roaming" / "Cemu",
            home / ".local" / "share" / "Cemu",
            home / ".var" / "app" / "info.cemu.Cemu" / "data" / "Cemu",
        ]
    )

    seen: set[Path] = set()
    result: list[Path] = []
    for d in dirs:
        if d in seen:
            continue
        seen.add(d)
        if d.is_dir():
            result.append(d)
    return result


_wiiu_name_index: dict[str, tuple[str | None, str | None]] | None = None


def wiiu_name_index(refresh: bool = False) -> dict[str, tuple[str | None, str | None]]:
    """``title_id -> (name, game_code)`` read from this PC's Cemu install.

    Cached: the walk touches every game folder, and the answer only changes
    when the user installs a game.  Pass ``refresh=True`` after changing the
    Cemu folder setting.
    """
    global _wiiu_name_index
    if _wiiu_name_index is not None and not refresh:
        return _wiiu_name_index

    try:
        from shared.wiiu_meta import (
            build_meta_index,
            game_paths_from_settings,
            mlc_path_from_settings,
        )
    except Exception:
        _wiiu_name_index = {}
        return _wiiu_name_index

    index: dict[str, tuple[str | None, str | None]] = {}
    for cemu_dir in get_cemu_dirs():
        settings_xml = cemu_dir / "settings.xml"
        mlc = mlc_path_from_settings(settings_xml)
        if mlc is None:
            candidate = cemu_dir / "mlc01"
            mlc = candidate if (candidate / "usr").is_dir() else None

        game_roots = game_paths_from_settings(settings_xml)
        game_roots.extend([cemu_dir / "games", cemu_dir])

        for title_id, entry in build_meta_index(mlc, game_roots).items():
            prev_name, prev_code = index.get(title_id, (None, None))
            index[title_id] = (entry[0] or prev_name, entry[1] or prev_code)

    _wiiu_name_index = index
    return index


def push_name_hints(codes: dict[str, str], names: dict[str, str]) -> bool:
    """Persist locally-resolved names for server saves stored under a raw id.

    The server applies a hint only when the stored name is still the title id,
    so this can never clobber a real name.  Best-effort: a failure just means
    the list keeps showing hex until the next refresh.
    """
    if not codes and not names:
        return False
    try:
        resp = requests.post(
            f"{get_base_url()}/api/v1/titles/update_names",
            json={"codes": codes, "names": names},
            headers=get_api_headers(),
            timeout=15,
        )
        return resp.status_code == 200
    except Exception:
        return False


def resolve_wiiu_names(saves: list[dict]) -> list[dict]:
    """Name Wii U saves the server could not, and teach the server what we found.

    Mutates nothing: returns the same list with ``game_name`` filled in where a
    local meta.xml had an answer.
    """
    unnamed = [
        s
        for s in saves
        if (s.get("console_type") or detect_console_type(s.get("title_id", ""))) == "WIIU"
        and (not s.get("game_name") or s.get("game_name") == s.get("title_id"))
    ]
    if not unnamed:
        return saves

    index = wiiu_name_index()
    if not index:
        return saves

    codes: dict[str, str] = {}
    names: dict[str, str] = {}
    for save in unnamed:
        title_id = save.get("title_id", "")
        name, code = index.get(title_id.upper(), (None, None))
        if name:
            save["game_name"] = name
            names[title_id] = name
        if code:
            codes[title_id] = code

    push_name_hints(codes, names)
    return saves


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------


def fetch_all_saves() -> list[dict]:
    resp = requests.get(
        f"{get_base_url()}/api/v1/titles", headers=get_api_headers(), timeout=30
    )
    resp.raise_for_status()
    return resolve_wiiu_names(resp.json().get("titles", []))


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


def download_raw_save_bytes(title_id: str) -> bytes:
    """Download raw save bytes from the generic save endpoint."""
    resp = requests.get(
        f"{get_base_url()}/api/v1/saves/{title_id}/raw",
        headers=get_api_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.content


def download_raw_save(title_id: str, dest_path: Path) -> None:
    """Download the raw save bytes to dest_path."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_bytes(download_raw_save_bytes(title_id))


def download_ps3_save(title_id: str, dest_path: Path) -> None:
    """Download a PS3 save bundle and extract it into dest_path."""
    from sync_engine import _extract_bundle_to_dir

    resp = requests.get(
        f"{get_base_url()}/api/v1/saves/{title_id}",
        headers=get_api_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    _extract_bundle_to_dir(resp.content, dest_path)


def download_ps1_cards(title_id: str, dest_path: Path) -> list[Path]:
    """Download PS1 memory card slot 0 and, if present, slot 1.

    The chosen dest_path receives slot 0. If slot 1 exists on the server, it is
    written beside dest_path using a ``_2`` suffix before the extension.
    """
    written: list[Path] = []
    headers = get_api_headers()
    base_url = get_base_url()

    resp0 = requests.get(
        f"{base_url}/api/v1/saves/{title_id}/ps1-card",
        headers=headers,
        params={"slot": 0},
        timeout=30,
    )
    resp0.raise_for_status()
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_bytes(resp0.content)
    written.append(dest_path)

    resp1 = requests.get(
        f"{base_url}/api/v1/saves/{title_id}/ps1-card",
        headers=headers,
        params={"slot": 1},
        timeout=30,
    )
    if resp1.status_code == 404:
        return written
    resp1.raise_for_status()
    slot1_path = dest_path.with_name(f"{dest_path.stem}_2{dest_path.suffix}")
    slot1_path.write_bytes(resp1.content)
    written.append(slot1_path)
    return written


def download_ps2_card(title_id: str, dest_path: Path, card_format: str = "mc2") -> None:
    """Download a PS2 memory card in either MemCard Pro (`mc2`) or PCSX2 (`ps2`) format."""
    if card_format not in {"mc2", "ps2"}:
        raise ValueError(f"Unsupported PS2 card format: {card_format}")

    resp = requests.get(
        f"{get_base_url()}/api/v1/saves/{title_id}/ps2-card",
        headers=get_api_headers(),
        params={"format": card_format},
        timeout=30,
    )
    resp.raise_for_status()
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_bytes(resp.content)
