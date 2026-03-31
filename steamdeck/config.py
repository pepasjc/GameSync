"""Configuration management for the Steam Deck SaveSync client."""

import json
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "savesync" / "steamdeck.json"
STATE_PATH = Path.home() / ".config" / "savesync" / "steamdeck_state.json"

DEFAULT_CONFIG = {
    "host": "192.168.1.100",
    "port": 8000,
    "api_key": "",
    "emulation_path": "",  # auto-detected if empty
}


def find_emulation_path() -> str:
    """Auto-detect EmuDeck installation path."""
    candidates = [
        Path.home() / "Emulation",
        Path("/run/media/mmcblk0p1/Emulation"),
        Path("/run/media/mmcblk1p1/Emulation"),
    ]
    # Check user-labelled SD card mounts
    for base in [Path("/run/media/deck"), Path("/run/media") / Path.home().name]:
        if base.exists():
            try:
                for mount in base.iterdir():
                    candidates.append(mount / "Emulation")
            except PermissionError:
                pass

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
