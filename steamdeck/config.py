"""Configuration management for the Steam Deck SaveSync client."""

import json
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "savesync" / "steamdeck.json"
STATE_PATH = Path.home() / ".config" / "savesync" / "steamdeck_state.json"
SATURN_ARCHIVE_STATE_PATH = (
    Path.home() / ".config" / "savesync" / "steamdeck_saturn_archives.json"
)

SATURN_SYNC_FORMATS = ("mednafen", "yabause", "yabasanshiro")

DEFAULT_CONFIG = {
    "host": "192.168.1.100",
    "port": 8000,
    "api_key": "",
    "emulation_path": "",  # auto-detected if empty
    "rom_scan_dir": "",  # additional ROM scan directory (e.g. external drive)
    # Saturn emulator format for local saves.  Server storage stays on
    # Beetle/Mednafen canonical; this just controls how the Steam Deck
    # writes Saturn saves out locally, matching Android's SettingsStore.
    "saturn_sync_format": "mednafen",
}


def normalize_saturn_sync_format(value: str | None) -> str:
    fmt = (str(value or "").strip().lower())
    return fmt if fmt in SATURN_SYNC_FORMATS else "mednafen"


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

    return config


def save_config(config: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)


def load_sync_state() -> dict:
    """Load last-synced hashes per title_id."""
    if STATE_PATH.exists():
        try:
            with open(STATE_PATH) as f:
                return json.load(f)
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
