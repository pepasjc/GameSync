"""Shared MiSTer folder mappings.

These mappings are used by both the desktop MiSTer integration and the
standalone MiSTer shell script via generated JSON.
"""

from __future__ import annotations

# Storage roots a MiSTer scans for game folders.  Cores search /media/usb0..5
# before /media/fat, so a game folder existing on USB takes precedence.
MISTER_GAMES_ROOTS: dict[str, str] = {
    "sd": "/media/fat/games",
    "usb": "/media/usb0/games",
}

MISTER_FOLDER_TO_SYSTEM: dict[str, str] = {
    "GBA": "GBA",
    "SNES": "SNES",
    "NES": "NES",
    "Genesis": "MD",
    "MegaDrive": "MD",
    "N64": "N64",
    "Gameboy": "GB",
    "GAMEBOY": "GB",
    "GBC": "GBC",
    "GameGear": "GG",
    "SMS": "SMS",
    "PCEngine": "PCE",
    "TurboGrafx16": "PCE",
    "TGFX16": "PCE",
    "TGFX16-CD": "PCECD",
    "Atari2600": "A2600",
    "Atari7800": "A7800",
    "ATARI7800": "A7800",
    "Lynx": "LYNX",
    "AtariLynx": "LYNX",
    "NeoGeo": "NEOGEO",
    "NeoGeo-CD": "NEOCD",
    "NeoGeoPocket": "NGP",
    "NeoGeoPocket-Color": "NGPC",
    "32X": "32X",
    "S32X": "32X",
    "MegaCD": "SEGACD",
    "PSX": "PS1",
    "WonderSwan": "WSWAN",
    "WonderSwanColor": "WSWANC",
    "3DO": "3DO",
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

# Ordered game-folder candidates per system for ROM installs.  Core folder
# names drifted over MiSTer releases (Genesis -> MegaDrive, PCEngine ->
# TGFX16, 32X -> S32X, ...): prefer the name current cores create, but honor
# a legacy folder if it already exists on the target.
MISTER_SYSTEM_FOLDER_CANDIDATES: dict[str, list[str]] = {
    "GBA": ["GBA"],
    "SNES": ["SNES"],
    "NES": ["NES"],
    "MD": ["MegaDrive", "Genesis"],
    "N64": ["N64"],
    "GB": ["GAMEBOY", "Gameboy"],
    "GBC": ["GBC"],
    "GG": ["GameGear"],
    "SMS": ["SMS"],
    "PCE": ["TGFX16", "PCEngine", "TurboGrafx16"],
    "PCECD": ["TGFX16-CD"],
    "A2600": ["Atari2600"],
    "A7800": ["ATARI7800", "Atari7800"],
    "LYNX": ["AtariLynx", "Lynx"],
    "NEOGEO": ["NEOGEO", "NeoGeo"],
    "NEOCD": ["NeoGeo-CD"],
    "NGP": ["NeoGeoPocket"],
    "NGPC": ["NeoGeoPocket-Color"],
    "32X": ["S32X", "32X"],
    "SEGACD": ["MegaCD"],
    "PS1": ["PSX"],
    "SAT": ["Saturn"],
    "WSWAN": ["WonderSwan"],
    "WSWANC": ["WonderSwanColor"],
    "3DO": ["3DO"],
    # Deliberately absent: PCFX. There is no MiSTer PC-FX core (checked
    # 2026-09 on a fully updated device), and this table is what the
    # on-device client treats as "runnable", so leaving it out hides PC-FX
    # from the catalog and refuses installs instead of stranding CHDs in a
    # folder no core reads.
}


# What a core will actually load out of its games folder.
#
# MiSTer ships every core's folder whether or not you own games for it, and
# each one holds the core's BIOS, blank disk images and support files. Those
# files use generic extensions - ``boot.rom``, ``sid_data.bin``, ``kanji.rom``
# - so the global ROM extension set says "game" for all of them and the
# Installed tab fills up with systems that hold nothing playable.
#
# A core only loads a handful of extensions, so the folder's own list is what
# decides. Folders absent from this table fall back to the global set, which
# is the old behaviour; an empty tuple means the folder never holds games
# (utility cores).
MISTER_FOLDER_ROM_EXTENSIONS: dict[str, tuple] = {
    # --- CD-based cores: the disc image formats, wherever the core lands.
    "3DO": (".chd", ".cue", ".iso", ".bin"),
    "CD-i": (".chd", ".cue", ".iso", ".bin"),
    "Dreamcast": (".gdi", ".cdi", ".chd", ".cue", ".iso"),
    "MegaCD": (".chd", ".cue", ".iso", ".bin"),
    "NeoGeo-CD": (".chd", ".cue", ".iso", ".bin"),
    "PSX": (".chd", ".cue", ".iso", ".bin", ".exe"),
    "Saturn": (".chd", ".cue", ".iso", ".bin"),
    "TGFX16-CD": (".chd", ".cue", ".iso", ".bin"),
    # --- Cartridge consoles.
    "32X": (".32x", ".bin"),
    "S32X": (".32x", ".bin"),
    "ATARI5200": (".a52", ".car", ".bin"),
    "ATARI7800": (".a78", ".bin"),
    "Atari7800": (".a78", ".bin"),
    "Atari2600": (".a26", ".bin"),
    "AtariLynx": (".lnx",),
    "Lynx": (".lnx",),
    "Lynx48": (".lnx",),
    "Astrocade": (".bin",),
    "ChannelF": (".bin", ".rom"),
    "Coleco": (".col", ".sg", ".bin", ".rom"),
    "GAMEBOY": (".gb", ".gbc"),
    "GAMEBOY2P": (".gb", ".gbc"),
    "Gameboy": (".gb", ".gbc"),
    "GBC": (".gbc", ".gb"),
    "GBA": (".gba",),
    "GBA2P": (".gba",),
    "SGB": (".gb", ".gbc"),
    "GameGear": (".gg", ".sms"),
    "GameGear2P": (".gg", ".sms"),
    "Gamate": (".bin",),
    "Intellivision": (".int", ".bin", ".rom"),
    "Jaguar": (".jag", ".j64", ".abs", ".cof", ".rom"),
    "MegaDrive": (".md", ".gen", ".smd", ".bin"),
    "Genesis": (".md", ".gen", ".smd", ".bin"),
    "N64": (".z64", ".n64", ".v64", ".ndd"),
    "NES": (".nes", ".fds", ".nsf"),
    "NEOGEO": (".neo", ".zip"),
    "NeoGeo": (".neo", ".zip"),
    "NeoGeoPocket": (".ngp", ".ngc", ".npc"),
    "NeoGeoPocket-Color": (".ngc", ".ngp", ".npc"),
    "NGPC": (".ngc", ".ngp", ".npc"),
    "Odyssey2": (".bin",),
    "PokemonMini": (".min",),
    "SMS": (".sms", ".sg", ".gg"),
    "SNES": (".sfc", ".smc", ".bs"),
    "TGFX16": (".pce", ".sgx", ".bin", ".zip"),
    "PCEngine": (".pce", ".sgx", ".bin", ".zip"),
    "TurboGrafx16": (".pce", ".sgx", ".bin", ".zip"),
    "VECTREX": (".vec", ".bin"),
    "WonderSwan": (".ws", ".wsc"),
    "WonderSwanColor": (".wsc", ".ws"),
    # --- Home computers: disk and tape images, never a bare .rom/.bin.
    "AO486": (".vhd", ".img", ".ima", ".iso", ".vfd"),
    "Amiga": (".adf", ".hdf", ".iso"),
    "Amstrad": (".dsk", ".cdt"),
    "Apple-II": (".dsk", ".do", ".po", ".nib", ".hdv", ".2mg"),
    "ARCHIE": (".vhd", ".adf"),
    "Archie": (".vhd", ".adf"),
    "AtariST": (".st", ".msa", ".img", ".vhd"),
    "ATARI800": (".atr", ".xex", ".car", ".cas", ".atx"),
    "BBCMicro": (".ssd", ".dsd", ".vhd"),
    "C16": (".d64", ".prg", ".tap"),
    "C64": (".d64", ".d71", ".d81", ".g64", ".t64", ".prg", ".crt", ".tap",
            ".nib"),
    "C128": (".d64", ".d71", ".d81", ".g64", ".g71", ".prg", ".crt"),
    "MACPLUS": (".dsk", ".vhd", ".img", ".hda"),
    "MacLC": (".dsk", ".vhd", ".img", ".hda"),
    "MSX1": (".rom", ".mx1", ".dsk"),
    "PET2001": (".prg", ".tap"),
    "QL": (".mdv", ".win"),
    "Spectrum": (".tap", ".tzx", ".z80", ".trd", ".scl", ".dsk", ".sna"),
    "TI-99_4A": (".rpk", ".dsk", ".bin"),
    "TSConf": (".trd", ".scl", ".tap", ".spg"),
    "VIC20": (".d64", ".prg", ".tap", ".crt"),
    "X68000": (".d88", ".hdf", ".vhd", ".dim"),
    "ZXNext": (".vhd",),
    # --- Not game folders at all.
    "MEMTEST": (),
    "DVD-Player": (),
}


# Some cores keep their saves somewhere other than their own games folder.
#
# The TurboGrafx-16 core writes **both** HuCard and CD saves into
# ``saves/TGFX16``; there is no ``saves/TGFX16-CD``, even though CD *games*
# live in ``games/TGFX16-CD``. Writing a downloaded CD save to a folder named
# after the games folder puts it somewhere the core never looks.
MISTER_SYSTEM_SAVE_FOLDERS: dict[str, list[str]] = {
    "PCECD": ["TGFX16"],
}

# The other half of that: one save folder now serves two systems, so the
# folder alone no longer says which system a save belongs to. The games folders
# are still separate, so an installed game is what disambiguates.
MISTER_SHARED_SAVE_FOLDERS: dict[str, tuple] = {
    "TGFX16": ("PCECD", "PCE"),
}


def mister_system_save_folder_candidates(system: str) -> list[str]:
    """Ordered save-folder names for a system (best first).

    Defaults to the games-folder names, since most cores use the same name for
    both, and is overridden where a core does not.
    """
    system_up = (system or "").upper()
    override = MISTER_SYSTEM_SAVE_FOLDERS.get(system_up)
    if override:
        return list(override)
    return mister_system_folder_candidates(system_up)


# CD-based cores.  Their games install into a per-game subfolder
# (games/PSX/<Game>/<Game>.chd): the cores name the autosave memory card /
# backup RAM after the game's folder, so each game gets a dedicated save and
# multi-disc games (disc tag stripped from the folder name) share one card.
MISTER_CD_SYSTEMS: frozenset[str] = frozenset(
    {"PS1", "SAT", "SEGACD", "PCECD", "NEOCD", "3DO"}
)


def mister_system_folder_candidates(system: str) -> list[str]:
    """Ordered MiSTer game-folder names for a system code (best first)."""
    system_up = (system or "").upper()
    candidates = MISTER_SYSTEM_FOLDER_CANDIDATES.get(system_up)
    if candidates:
        return list(candidates)
    fallback = MISTER_SYSTEM_TO_FOLDER.get(system_up)
    return [fallback] if fallback else []


# ── Where GameSync keeps its files on the device ────────────────────────────
#
# MiSTer scripts keep their data in ``/media/fat/Scripts/.config/<script>/``;
# the stock ``downloader`` puts its config, state and log there together, so
# GameSync follows suit rather than littering the SD card root.
#
# These constants are shared because three different programs read and write
# the same state file - the on-device client, the desktop client over SFTP and
# the legacy ``sync_saves.sh``. If they ever disagreed on the path, each would
# keep its own idea of the last synced hash and every save would look like a
# conflict.

#: MiSTer's Saturn core reads and writes the internal backup RAM byte-expanded
#: to 64 KB (0xFF padding at even offsets) - the same layout Yabause uses - and
#: always names it ``.sav``. The server keeps the canonical 32 KB image, so a
#: download has to be converted into this shape or the core ignores it.
MISTER_SATURN_FORMAT = "yabause"

MISTER_CONFIG_DIR = "/media/fat/Scripts/.config/gamesync"
MISTER_CONFIG_FILE = MISTER_CONFIG_DIR + "/gamesync.cfg"
MISTER_STATE_FILE = MISTER_CONFIG_DIR + "/state.json"
MISTER_LOG_FILE = MISTER_CONFIG_DIR + "/gamesync.log"

#: Pre-0.5.4 locations. Still read when the current path holds nothing, so an
#: existing install keeps its sync state instead of re-conflicting everything.
LEGACY_MISTER_CONFIG_FILE = "/media/fat/3dssync.cfg"
LEGACY_MISTER_STATE_FILE = "/media/fat/3dssync_state.json"


# Compatibility aliases for current desktop imports.
FOLDER_TO_SYSTEM = MISTER_FOLDER_TO_SYSTEM
SYSTEM_TO_FOLDER = MISTER_SYSTEM_TO_FOLDER
MISTER_FOLDER_MAP = MISTER_FOLDER_TO_SYSTEM

__all__ = [
    "FOLDER_TO_SYSTEM",
    "LEGACY_MISTER_CONFIG_FILE",
    "LEGACY_MISTER_STATE_FILE",
    "MISTER_CD_SYSTEMS",
    "MISTER_CONFIG_DIR",
    "MISTER_CONFIG_FILE",
    "MISTER_FOLDER_MAP",
    "MISTER_FOLDER_ROM_EXTENSIONS",
    "MISTER_FOLDER_TO_SYSTEM",
    "MISTER_GAMES_ROOTS",
    "MISTER_LOG_FILE",
    "MISTER_SATURN_FORMAT",
    "MISTER_STATE_FILE",
    "MISTER_SHARED_SAVE_FOLDERS",
    "MISTER_SYSTEM_FOLDER_CANDIDATES",
    "MISTER_SYSTEM_SAVE_FOLDERS",
    "MISTER_SYSTEM_TO_FOLDER",
    "SYSTEM_TO_FOLDER",
    "mister_system_folder_candidates",
    "mister_system_save_folder_candidates",
]
