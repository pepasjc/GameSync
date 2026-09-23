"""Configuration management for the Steam Deck SaveSync client."""

import json
import sys
from pathlib import Path

# Make the repo root importable so 'shared' can be found when config.py is
# imported before main.py's own sys.path bootstrap (e.g. from tests).
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from shared.sync_id import canonicalize_code_form_title_id  # noqa: E402

CONFIG_PATH = Path.home() / ".config" / "savesync" / "steamdeck.json"
STATE_PATH = Path.home() / ".config" / "savesync" / "steamdeck_state.json"
SATURN_ARCHIVE_STATE_PATH = (
    Path.home() / ".config" / "savesync" / "steamdeck_saturn_archives.json"
)
# SQLite-backed download queue.  Survives app restarts so the user can
# pause a multi-GB download, close the app, and resume later.  Mirrors
# Android's Room "downloads" table.
DOWNLOADS_DB_PATH = Path.home() / ".config" / "savesync" / "steamdeck_downloads.db"

SATURN_SYNC_FORMATS = ("mednafen", "yabause", "yabasanshiro")

# ``save_dir_overrides`` key for the Cemu folder.  Same spelling Android uses
# for ``CemuEmulator.EMULATOR_KEY`` so a config is readable across both.
CEMU_SAVE_DIR_KEY = "Cemu"

DEFAULT_CONFIG = {
    "host": "192.168.1.100",
    "port": 8000,
    "api_key": "",
    "emulation_path": "",  # auto-detected if empty
    "rom_scan_dir": "",  # additional ROM scan directory (e.g. external drive)
    # Per-system ROM folder overrides, keyed by upper-case system code
    # (e.g. "PS1", "SAT").  Values are absolute folder paths; any system
    # without an entry falls back to the EmuDeck-style candidate search
    # inside ``scanner/rom_target.py``.  Mirrors Android's
    # ``romDirOverrides`` in SettingsStore.
    "rom_dir_overrides": {},
    # Per-emulator save folder overrides, keyed by emulator name ("Cemu").
    # Mirrors Android's ``saveDirOverrides`` in SettingsStore.  Only Cemu
    # reads one today: EmuDeck installs Cemu outside the Emulation folder
    # (Proton prefix, flatpak data dir, a second install on an SD card), so
    # the candidate search can miss it entirely and there was no way to say
    # where it actually lives.
    "save_dir_overrides": {},
    # Saturn emulator format for local saves.  Server storage stays on
    # Beetle/Mednafen canonical; this just controls how the Steam Deck
    # writes Saturn saves out locally, matching Android's SettingsStore.
    "saturn_sync_format": "mednafen",
}


def normalize_saturn_sync_format(value: str | None) -> str:
    fmt = (str(value or "").strip().lower())
    return fmt if fmt in SATURN_SYNC_FORMATS else "mednafen"


def normalize_rom_dir_overrides(value) -> dict:
    """Coerce ``rom_dir_overrides`` to ``{upper-case system: stripped path}``.

    Drops entries with empty values so ``resolve_rom_target_dir`` can short-
    circuit on truthiness alone.  Non-dict or malformed inputs become ``{}``
    so a corrupt config file never breaks downloads.
    """
    if not isinstance(value, dict):
        return {}
    result: dict[str, str] = {}
    for k, v in value.items():
        if not isinstance(k, str):
            continue
        path = str(v or "").strip()
        if not path:
            continue
        result[k.strip().upper()] = path
    return result


def normalize_save_dir_overrides(value) -> dict:
    """Coerce ``save_dir_overrides`` to ``{emulator name: stripped path}``.

    Keys keep their case — they name an emulator ("Cemu"), matching Android's
    ``saveDirOverrides`` keys — so lookups go through
    :func:`save_dir_override`, which compares case-insensitively.
    """
    if not isinstance(value, dict):
        return {}
    result: dict[str, str] = {}
    for k, v in value.items():
        if not isinstance(k, str):
            continue
        name = k.strip()
        path = str(v or "").strip()
        if not name or not path:
            continue
        result[name] = path
    return result


def save_dir_override(overrides, emulator: str) -> str:
    """The configured save folder for [emulator], or ``""``.

    Case-insensitive: a hand-edited config with ``"cemu"`` should still work.
    """
    if not isinstance(overrides, dict):
        return ""
    wanted = emulator.strip().lower()
    for key, path in overrides.items():
        if isinstance(key, str) and key.strip().lower() == wanted:
            return str(path or "").strip()
    return ""


def find_emulation_path() -> str:
    """Auto-detect EmuDeck installation path."""
    import sys

    candidates = [
        Path.home() / "Emulation",
    ]

    # Linux: SD card mounts
    if sys.platform != "win32":
        candidates.extend(
            [
                Path("/run/media/mmcblk0p1/Emulation"),
                Path("/run/media/mmcblk1p1/Emulation"),
            ]
        )
        for base in [Path("/run/media/deck"), Path("/run/media") / Path.home().name]:
            if base.exists():
                try:
                    for mount in base.iterdir():
                        candidates.append(mount / "Emulation")
                except PermissionError:
                    pass

    # Windows: check common locations and drive roots
    if sys.platform == "win32":
        import string

        # Check common folder names on all drives
        for letter in string.ascii_uppercase:
            drive = Path(f"{letter}:\\")
            if drive.exists():
                for name in ("Emulation", "EmuDeck", "Emudeck"):
                    candidates.append(drive / name)

    for path in candidates:
        if path.exists() and (path / "roms").exists():
            return str(path)

    return str(Path.home() / "Emulation")


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                saved = json.load(f)
            config = {**DEFAULT_CONFIG, **saved}
        except Exception:
            config = DEFAULT_CONFIG.copy()
    else:
        config = DEFAULT_CONFIG.copy()

    if not config.get("emulation_path"):
        config["emulation_path"] = find_emulation_path()

    config["saturn_sync_format"] = normalize_saturn_sync_format(
        config.get("saturn_sync_format")
    )
    config["rom_dir_overrides"] = normalize_rom_dir_overrides(
        config.get("rom_dir_overrides")
    )
    config["save_dir_overrides"] = normalize_save_dir_overrides(
        config.get("save_dir_overrides")
    )

    return config


def save_config(config: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)


def _canonicalize_state_keys(state: dict) -> dict:
    """Fold gamecode-form keys (GC_grse) onto their canonical form (GC_GRSE).

    Builds before the GC title-id canonicalisation wrote lowercase keys.  Left
    alone, every GameCube game would lose its last_synced_hash on upgrade and
    come back as a spurious conflict.  An existing canonical entry wins.
    """
    out: dict = {}
    for key, value in state.items():
        canonical = canonicalize_code_form_title_id(key)
        if canonical == key or canonical not in state:
            out[canonical] = value
    return out


def load_sync_state() -> dict:
    """Load last-synced hashes per title_id."""
    if STATE_PATH.exists():
        try:
            with open(STATE_PATH) as f:
                return _canonicalize_state_keys(json.load(f))
        except Exception:
            pass
    return {}


def save_sync_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def load_saturn_archive_state() -> dict:
    if SATURN_ARCHIVE_STATE_PATH.exists():
        try:
            with open(SATURN_ARCHIVE_STATE_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_saturn_archive_state(state: dict) -> None:
    SATURN_ARCHIVE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SATURN_ARCHIVE_STATE_PATH, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
