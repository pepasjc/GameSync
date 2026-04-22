"""Pick the on-disk folder to drop a downloaded ROM into.

Mirrors the Android ``SyncEngine.downloadRom`` folder-candidate logic so that
ROMs downloaded from the server land under the same EmuDeck-style
``~/Emulation/roms/<system>/`` layout the scanner already reads from.  If an
existing folder matches any candidate it wins; otherwise we create the first
candidate (lowercase, EmuDeck convention).

The caller may pass ``overrides``, a mapping of upper-case system code to an
absolute folder path, to short-circuit the candidate search for individual
systems (configured from Settings → "Per-system ROM folders").
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Optional


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


def resolve_rom_target_dir(
    roms_base: Path,
    system: str,
    overrides: Optional[Mapping[str, str]] = None,
) -> Path:
    """
    Return the folder where a ROM for ``system`` should land.

    Resolution order:
    1. If ``overrides[system.upper()]`` is set to a non-empty path, use it
       verbatim (absolute paths are returned as-is; relative paths resolve
       under ``roms_base``).  This mirrors the Android client's
       ``romDirOverrides`` so users can pin individual systems to a custom
       folder (e.g. a separate SD card or an external drive) without
       moving the rest of the library.
    2. Otherwise, prefer an existing folder matching any known EmuDeck /
       RetroDeck / Batocera alias.
    3. Otherwise, fall back to the first candidate (lowercase, EmuDeck
       convention) under ``roms_base``.  The caller is responsible for
       ``mkdir`` on the returned path.
    """
    sys_up = system.upper()
    if overrides:
        override_raw = overrides.get(sys_up)
        if override_raw:
            override = str(override_raw).strip()
            if override:
                override_path = Path(override).expanduser()
                if not override_path.is_absolute():
                    override_path = roms_base / override_path
                return override_path

    candidates = SYSTEM_ROM_DIRS.get(sys_up, [system, system.lower()])
    for name in candidates:
        candidate = roms_base / name
        if candidate.is_dir():
            return candidate
    return roms_base / candidates[0]
