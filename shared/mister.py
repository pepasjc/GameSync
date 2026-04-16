"""Shared MiSTer folder mappings.

These mappings are used by both the desktop MiSTer integration and the
standalone MiSTer shell script via generated JSON.
"""

from __future__ import annotations

MISTER_FOLDER_TO_SYSTEM: dict[str, str] = {
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
    "Atari2600": "A2600",
    "Atari7800": "A7800",
    "Lynx": "LYNX",
    "NeoGeo": "NEOGEO",
    "32X": "32X",
    "MegaCD": "SEGACD",
    "PSX": "PS1",
    # Some MiSTer builds use slightly different names.
    "GG": "GG",
    "NEOGEO": "NEOGEO",
    "Lynx48": "LYNX",
    "Saturn": "SAT",
}

MISTER_SYSTEM_TO_FOLDER: dict[str, str] = {
    "GBA": "GBA",
    "SNES": "SNES",
    "NES": "NES",
    "MD": "Genesis",
    "N64": "N64",
    "GB": "Gameboy",
    "GBC": "GBC",
    "GG": "GameGear",
    "SMS": "SMS",
    "PCE": "PCEngine",
    "A2600": "Atari2600",
    "A7800": "Atari7800",
    "LYNX": "Lynx",
    "NEOGEO": "NeoGeo",
    "32X": "32X",
    "SEGACD": "MegaCD",
    "PS1": "PSX",
    "SAT": "Saturn",
}

# Compatibility aliases for current desktop imports.
FOLDER_TO_SYSTEM = MISTER_FOLDER_TO_SYSTEM
SYSTEM_TO_FOLDER = MISTER_SYSTEM_TO_FOLDER
MISTER_FOLDER_MAP = MISTER_FOLDER_TO_SYSTEM

__all__ = [
    "FOLDER_TO_SYSTEM",
    "MISTER_FOLDER_MAP",
    "MISTER_FOLDER_TO_SYSTEM",
    "MISTER_SYSTEM_TO_FOLDER",
    "SYSTEM_TO_FOLDER",
]
