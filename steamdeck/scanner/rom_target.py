"""Pick the on-disk folder to drop a downloaded ROM into.

Mirrors the Android ``SyncEngine.downloadRom`` folder-candidate logic so that
ROMs downloaded from the server land under the same EmuDeck-style
``~/Emulation/roms/<system>/`` layout the scanner already reads from.  If an
existing folder matches any candidate it wins; otherwise we create the first
candidate (lowercase, EmuDeck convention).
"""

from __future__ import annotations

from pathlib import Path


# Preferred ROM sub-folder names per system.  First existing entry wins; if
# none exist the first name is created.  Lowercase forms come first because
# EmuDeck ships that layout by default; the Title-Case aliases make us
# friendly to RetroDeck / Batocera / manual setups that use capitalised names.
SYSTEM_ROM_DIRS: dict[str, list[str]] = {
    # Sony
    "PS1":    ["psx", "PSX", "PS1", "ps1", "PlayStation", "playstation", "PlayStation 1"],
    "PS2":    ["ps2", "PS2", "PlayStation 2", "PlayStation2"],
    "PS3":    ["ps3", "PS3", "PlayStation 3", "PlayStation3"],
    "PSP":    ["psp", "PSP", "PlayStation Portable"],
    "PSVITA": ["psvita", "PSVITA", "Vita", "PS Vita"],
    # Nintendo
    "GBA":    ["gba", "GBA", "Game Boy Advance", "GameBoyAdvance"],
    "GB":     ["gb", "GB", "Game Boy", "GameBoy"],
    "GBC":    ["gbc", "GBC", "Game Boy Color", "GameBoyColor"],
    "NES":    ["nes", "NES", "Nintendo", "Famicom"],
    "SNES":   ["snes", "SNES", "Super Nintendo", "SuperNintendo"],
    "N64":    ["n64", "N64", "Nintendo 64", "Nintendo64"],
    "GC":     ["gc", "GC", "GameCube", "Nintendo GameCube"],
    "WII":    ["wii", "Wii"],
    "NDS":    ["nds", "NDS", "DS", "Nintendo DS"],
    "3DS":    ["3ds", "n3ds", "Nintendo 3DS"],
    "VB":     ["virtualboy", "VB", "Virtual Boy"],
    # Sega
    "MD":     ["megadrive", "genesis", "MD", "Mega Drive", "Genesis", "MegaDrive"],
    "SEGACD": ["segacd", "megacd", "Sega CD", "Mega CD", "SegaCD", "MegaCD"],
    "SMS":    ["mastersystem", "SMS", "Master System", "Sega Master System"],
    "GG":     ["gamegear", "GG", "Game Gear", "GameGear"],
    "SAT":    ["saturn", "Saturn", "Sega Saturn", "Sega - Saturn", "SAT"],
    "DC":     ["dreamcast", "dc", "Dreamcast", "Sega Dreamcast", "DC"],
    "32X":    ["sega32x", "32x", "32X", "Sega 32X"],
    # NEC / SNK / misc
    "PCE":    ["pcengine", "tg16", "PCE", "PC Engine", "TurboGrafx", "PCEngine"],
    "PCECD":  ["pcenginecd", "tgcd", "PCECD", "PC Engine CD"],
    "NEOGEO": ["neogeo", "NeoGeo", "NEOGEO"],
    "NEOCD":  ["neogeocd", "NEOCD", "Neo Geo CD", "NeoGeoCD"],
    "NGP":    ["ngp", "NGP", "Neo Geo Pocket", "NeoGeoPocket"],
    "NGPC":   ["ngpc", "NGPC", "Neo Geo Pocket Color"],
    "WSWAN":  ["wonderswan", "WSWAN", "WonderSwan"],
    "WSWANC": ["wonderswancolor", "WSWANC", "WonderSwan Color", "WonderSwanColor"],
    # Atari
    "A2600":  ["atari2600", "A2600", "Atari 2600"],
    "A5200":  ["atari5200", "A5200", "Atari 5200"],
    "A7800":  ["atari7800", "A7800", "Atari 7800"],
    "A800":   ["atari800", "A800", "Atari 800"],
    "LYNX":   ["lynx", "Lynx", "Atari Lynx"],
    "JAGUAR": ["jaguar", "Jaguar", "Atari Jaguar"],
    # Misc
    "3DO":    ["3do", "3DO"],
    "POKEMINI": ["pokemini", "Pokemon Mini", "PokemonMini"],
}


def resolve_rom_target_dir(roms_base: Path, system: str) -> Path:
    """
    Return the folder under ``roms_base`` where a ROM for ``system`` should
    land.  Prefers an existing folder matching any known alias; otherwise
    falls back to the first candidate (creating nothing — the caller is
    responsible for ``mkdir``).
    """
    sys_up = system.upper()
    candidates = SYSTEM_ROM_DIRS.get(sys_up, [system, system.lower()])
    for name in candidates:
        candidate = roms_base / name
        if candidate.is_dir():
            return candidate
    return roms_base / candidates[0]
