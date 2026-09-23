"""Configuration and sync state for the on-device MiSTer client.

Files live in the MiSTer script convention directory,
``/media/fat/Scripts/.config/gamesync/``, alongside every other script's data
rather than loose in the SD card root. The paths themselves come from
``shared/mister.py`` because the desktop client writes the same state file over
SFTP - if the two disagreed on the path, each would keep its own idea of the
last synced hash and every save would look like a conflict.

The config is flat ``KEY=VALUE``. MiSTer is standardised enough that there are
only three keys worth having; anything else (save folders, games roots, system
names) is derived from ``shared/mister.py``.
"""

from __future__ import annotations

import json
import os

from shared.mister import (
    LEGACY_MISTER_CONFIG_FILE,
    LEGACY_MISTER_STATE_FILE,
    MISTER_CONFIG_DIR,
    MISTER_CONFIG_FILE,
    MISTER_STATE_FILE,
)

DEFAULTS = {
    "SERVER_URL": "",
    "API_KEY": "",
    "ROM_TARGET": "sd",
    "OVERSCAN_X": "",
    "OVERSCAN_Y": "",
    "BUTTONS": "",
}

ROM_TARGETS = ("sd", "usb")

#: Percent of each edge to keep clear. A CRT, and an arcade tube especially,
#: does not show the whole picture; the cabinet this was calibrated against
#: loses all four corners at 0.
MAX_OVERSCAN = 25.0

#: What a 240p display gets when nothing has been calibrated. A consumer CRT
#: hides roughly 5% of each side and rather more of the top and bottom - the
#: header and the footer were the first things to go on a YPbPr set. A monitor
#: shows everything, so its default stays at zero.
CRT_DEFAULT_OVERSCAN = (5.0, 8.0)


def _percent(value, fallback=None):
    """A clamped percentage, or ``fallback`` for blank/unparseable.

    Blank means "never calibrated", which is different from "calibrated to
    zero": the former gets the CRT default on a 240p display, the latter is a
    deliberate choice and is kept.
    """
    if value is None or str(value).strip() == "":
        return fallback
    try:
        return max(0.0, min(float(value), MAX_OVERSCAN))
    except (TypeError, ValueError):
        return fallback


def parse_buttons(value: str) -> dict:
    """``primary=0x130,back=0x131`` into ``{action: evdev_code}``.

    Stored as codes rather than names because the mapping from a physical
    arcade button to an evdev code is whatever hid-generic decided for that
    encoder, and no two sticks agree.
    """
    mapping = {}
    for part in (value or "").split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        action, _, code = part.partition("=")
        try:
            mapping[action.strip().lower()] = int(code.strip(), 0)
        except ValueError:
            continue
    return mapping


def format_buttons(mapping: dict) -> str:
    return ",".join("%s=0x%x" % (action, code)
                    for action, code in sorted(mapping.items()))


class Config:
    """The three settings the client needs, plus where they came from."""

    __slots__ = ("server_url", "api_key", "rom_target", "path",
                 "overscan_x", "overscan_y", "buttons")

    def __init__(self, server_url="", api_key="", rom_target="sd", path="",
                 overscan_x=None, overscan_y=None, buttons=None):
        self.server_url = server_url
        self.api_key = api_key
        self.rom_target = rom_target if rom_target in ROM_TARGETS else "sd"
        self.path = path
        self.overscan_x = _percent(overscan_x)
        self.overscan_y = _percent(overscan_y)
        self.buttons = dict(buttons or {})

    @property
    def is_configured(self) -> bool:
        return bool(self.server_url and self.api_key)

    @property
    def overscan_is_set(self) -> bool:
        return self.overscan_x is not None or self.overscan_y is not None

    def overscan_for(self, lowres: bool):
        """``(x, y)`` percent to actually inset by on this display."""
        default = CRT_DEFAULT_OVERSCAN if lowres else (0.0, 0.0)
        return (default[0] if self.overscan_x is None else self.overscan_x,
                default[1] if self.overscan_y is None else self.overscan_y)

    @property
    def base_url(self) -> str:
        return self.server_url.rstrip("/") + "/api/v1"

    def headers(self) -> dict:
        return {"X-API-Key": self.api_key} if self.api_key else {}

    def to_text(self) -> str:
        return (
            "# GameSync configuration for MiSTer\n"
            "# Everything not listed here is derived from shared/mister.py.\n"
            "SERVER_URL=%s\n"
            "API_KEY=%s\n"
            "ROM_TARGET=%s\n"
            "\n"
            "# Percent of each edge kept clear, for CRTs that overscan.\n"
            "# Set from the client: Settings tab -> Adjust screen. Blank\n"
            "# means automatic: 5%%/8%% on a 240p display, 0 on a monitor.\n"
            "OVERSCAN_X=%s\n"
            "OVERSCAN_Y=%s\n"
            "\n"
            "# action=evdev_code pairs, written by Settings -> Remap buttons.\n"
            "# Empty means use the built-in gamepad defaults.\n"
            "BUTTONS=%s\n"
        ) % (self.server_url, self.api_key, self.rom_target,
             _format_percent(self.overscan_x), _format_percent(self.overscan_y),
             format_buttons(self.buttons))


def _format_percent(value) -> str:
    return "" if value is None else "%g" % value


def _parse(text: str) -> dict:
    """Flat KEY=VALUE, ignoring comments, blanks and surrounding quotes."""
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().upper()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def config_path() -> str:
    """The config to read: the current one, else a pre-0.5.4 one."""
    if os.path.exists(MISTER_CONFIG_FILE):
        return MISTER_CONFIG_FILE
    if os.path.exists(LEGACY_MISTER_CONFIG_FILE):
        return LEGACY_MISTER_CONFIG_FILE
    return MISTER_CONFIG_FILE


def load_config() -> Config:
    path = config_path()
    values = dict(DEFAULTS)
    try:
        with open(path, "r") as handle:
            values.update(_parse(handle.read()))
    except OSError:
        pass
    return Config(
        server_url=values.get("SERVER_URL", ""),
        api_key=values.get("API_KEY", ""),
        rom_target=values.get("ROM_TARGET", "sd"),
        path=path,
        overscan_x=values.get("OVERSCAN_X", ""),
        overscan_y=values.get("OVERSCAN_Y", ""),
        buttons=parse_buttons(values.get("BUTTONS", "")),
    )


def save_config(config: Config) -> None:
    """Write the config atomically to the current location."""
    _write_atomic(MISTER_CONFIG_FILE, config.to_text().encode("utf-8"))
    config.path = MISTER_CONFIG_FILE


def load_state() -> dict:
    """``{title_id: last_synced_hash}``, falling back to the pre-0.5.4 path."""
    for path in (MISTER_STATE_FILE, LEGACY_MISTER_STATE_FILE):
        try:
            with open(path, "r") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except (OSError, ValueError):
            continue
    return {}


UI_STATE_FILE = MISTER_CONFIG_DIR + "/ui_state.json"


def load_ui_state() -> dict:
    """Where the user left off - the catalogue's system and row, for now.

    Separate from state.json, which holds sync hashes and must survive
    anything; this is a convenience and losing it costs nothing.
    """
    try:
        with open(UI_STATE_FILE, "r") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_ui_state(state: dict) -> None:
    try:
        _write_atomic(UI_STATE_FILE,
                      json.dumps(state, indent=2, sort_keys=True)
                      .encode("utf-8"))
    except OSError:
        pass


def save_state(state: dict) -> None:
    _write_atomic(MISTER_STATE_FILE,
                  json.dumps(state, indent=2, sort_keys=True).encode("utf-8"))


def _write_atomic(path: str, payload: bytes) -> None:
    """Write via a .part file and rename.

    The SD card is exfat mounted ``sync``; a half-written state file after a
    power cut would lose every last-synced hash.
    """
    try:
        os.makedirs(MISTER_CONFIG_DIR, exist_ok=True)
    except OSError:
        pass
    temp = path + ".part"
    with open(temp, "wb") as handle:
        handle.write(payload)
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except OSError:
            pass
    os.replace(temp, path)
