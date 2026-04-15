"""
shared/systems.py — canonical system registry for Save Sync.

Single source of truth for all Python components:
  - desktop/systems.py  (shim that re-exports from here)
  - server/app/services/rom_id.py
  - steamdeck/scanner/models.py and base.py

Non-Python clients (MiSTer bash script) can consume shared/systems.json
which is auto-generated via: python shared/generate_json.py
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# System codes — UI subset
# ---------------------------------------------------------------------------

# Ordered list shown in UI dropdowns (normalizer, collection, profile tabs).
# Subset of SYSTEM_CODES; excludes aliases and server-only/exotic systems.
SYSTEM_CHOICES: list[str] = sorted(
    [
        "32X",
        "A2600",
        "A7800",
        "ARCADE",
        "DC",
        "FDS",
        "GB",
        "GBA",
        "GBC",
        "GC",
        "GG",
        "LYNX",
        "MAME",
        "MD",
        "N64",
        "N64DD",
        "NDS",
        "NES",
        "NEOGEO",
        "NGP",
        "NGPC",
        "PCE",
        "PCECD",
        "PCSG",
        "PS1",
        "PS2",
        "PS3",
        "PSP",
        "SAT",
        "SEGACD",
        "SMS",
        "SNES",
        "TG16",
        "VB",
        "WSWAN",
        "WSWANC",
    ],
    key=str.lower,
)

# All console types known to the server (includes handheld-native 3DS/VITA/NDS).
# "All" is prepended so it can be used directly in filter dropdowns.
ALL_CONSOLE_TYPES: list[str] = ["All"] + sorted(
    [
        "3DS",
        "32X",
        "A2600",
        "A7800",
        "ARCADE",
        "DC",
        "FDS",
        "GB",
        "GBA",
        "GBC",
        "GC",
        "GG",
        "LYNX",
        "MAME",
        "MD",
        "N64",
        "N64DD",
        "NDS",
        "NES",
        "NEOGEO",
        "NGP",
        "NGPC",
        "PCE",
        "PCECD",
        "PCSG",
        "PS1",
        "PS2",
        "PS3",
        "PSP",
        "SAT",
        "SEGACD",
        "SMS",
        "SNES",
        "VB",
        "VITA",
        "WSWAN",
        "WSWANC",
    ],
    key=str.lower,
)

# ---------------------------------------------------------------------------
# System aliases: non-canonical → canonical code
# ---------------------------------------------------------------------------

SYSTEM_ALIASES: dict[str, str] = {
    "GEN":       "MD",       # Mega Drive / Genesis
    "SCD":       "SEGACD",   # Sega CD
    "WS":        "WSWAN",    # WonderSwan
    "ATARI2600": "A2600",
    "ATARI5200": "A5200",
    "ATARI7800": "A7800",
}

# ---------------------------------------------------------------------------
# System codes — full set (canonical + aliases + extended server/exotic codes)
# ---------------------------------------------------------------------------

# All valid system codes accepted by the server.
# Superset of SYSTEM_CHOICES; includes aliases, server-only, and exotic systems.
SYSTEM_CODES: frozenset[str] = frozenset(SYSTEM_CHOICES) | frozenset(
    {
        # Consoles / handhelds not in the desktop UI
        "3DS",
        "VITA",
        "WII",
        "NSW",       # Nintendo Switch
        "NEOCD",     # Neo Geo CD
        "PS3",       # already in SYSTEM_CHOICES; listed here for clarity

        # Atari extended
        "A5200",     # Atari 5200 (canonical)
        "A800",      # Atari 800 / 400 / XL / XE 8-bit computers
        "ATARIXED",  # Atari XE Game System
        "JAGUAR",    # Atari Jaguar
        "JAGCD",     # Atari Jaguar CD
        "ATARIST",   # Atari ST / STE / TT / Falcon

        # Sega misc
        "NAOMI",     # Sega NAOMI arcade
        "NAOMI2",    # Sega NAOMI 2 arcade

        # NEC misc
        "PC98",      # NEC PC-98
        "PCFX",      # NEC PC-FX

        # Sharp
        "X1",        # Sharp X1
        "X68K",      # Sharp X68000

        # Other
        "3DO",
        "BS",        # Satellaview (BS-X)
        "POKEMINI",  # Pokémon Mini

        # Arcade sub-systems
        "FBA",
        "FBNEO",
        "CPS1",
        "CPS2",
        "CPS3",

        # Aliases (accepted by server for backwards compatibility)
        "GEN",       # → MD
        "SCD",       # → SEGACD
        "WS",        # → WSWAN
        "ATARI2600", # → A2600
        "ATARI5200", # → A5200
        "ATARI7800", # → A7800
    }
)

# ---------------------------------------------------------------------------
# ROM file extensions
# ---------------------------------------------------------------------------

# Canonical union of all ROM formats across every supported system.
# Used when scanning folders for ROM files.
ROM_EXTENSIONS: frozenset[str] = frozenset(
    {
        # Game Boy family
        ".gb",
        ".gbc",
        ".gba",
        # NES / Famicom
        ".nes",
        ".fds",    # Famicom Disk System
        ".qd",     # Quick Disk (FDS variant)
        # Super Nintendo
        ".sfc",
        ".smc",
        ".sgb",    # Super Game Boy cartridge
        # Nintendo 64
        ".n64",
        ".z64",
        ".v64",
        ".ndd",    # N64DD
        # Nintendo DS / 3DS
        ".nds",
        ".3ds",
        ".cia",
        # GameCube / Wii
        ".gcm",    # GameCube disc image
        ".gci",    # GameCube save container (used as ROM in some loaders)
        # Sega 8-bit
        ".sms",    # Master System
        ".gg",     # Game Gear
        # Sega 16/32-bit
        ".md",
        ".gen",
        ".smd",    # Mega Drive alternate header
        ".32x",
        # NEC PC Engine
        ".pce",
        ".sgx",    # SuperGrafx
        ".tg16",   # TurboGrafx-16
        # SNK Neo Geo Pocket
        ".ngp",
        ".ngc",    # Neo Geo Pocket Color
        # Atari cartridge / disk
        ".a26",    # Atari 2600
        ".a52",    # Atari 5200
        ".a78",    # Atari 7800
        ".lnx",    # Atari Lynx
        ".jag",    # Atari Jaguar
        ".j64",    # Atari Jaguar (byteswapped)
        ".rom",    # Atari Jaguar / generic
        ".st",     # Atari ST floppy image
        ".stx",    # Atari ST extended floppy
        ".msa",    # Atari ST Magic Shadow Archiver
        ".dim",    # Atari ST disk image
        # Other cartridge / misc systems
        ".vb",     # Virtual Boy
        ".ws",     # WonderSwan
        ".wsc",    # WonderSwan Color
        ".pc2",    # PC Engine (alternate)
        ".vec",    # GCE Vectrex
        ".col",    # ColecoVision
        ".neo",    # SNK Neo Geo (MAME)
        ".d64",    # Commodore 64 disk image
        # Optical disc — single-file formats
        ".iso",
        ".chd",    # Compressed Hunks of Data (universal CD image)
        ".cso",    # PSP compressed ISO
        ".pbp",    # PSP / PS3 eboot
        ".pkg",    # PSP / PS3 package
        ".ecm",    # Error Code Modeler (compressed CD image)
        ".dax",    # PSP compressed format
        ".mds",    # Media Descriptor (Alcohol 120%)
        ".sat",    # Yabause Saturn disc image
        # Optical disc — data-track files (multi-file disc images)
        ".bin",
        ".img",
        ".mdf",    # Alcohol 120% / Saturn
        ".ccd",    # CloneCD control file
        ".cue",    # Cue sheet
        # Archives (many emulators accept zipped ROMs)
        ".zip",
        ".7z",
        ".rar",
        # Generic executable (homebrew / PS3)
        ".elf",
    }
)

# ---------------------------------------------------------------------------
# CD / disc image extensions
# ---------------------------------------------------------------------------

# Data-track files used for CRC fingerprinting and as the "representative
# file" when scanning game subfolders in CD Folder mode.
CD_DATA_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".iso",
        ".bin",
        ".img",
        ".mdf",
        ".chd",
        ".cso",    # PSP compressed image
        ".pbp",    # PSP / PS3 eboot
    }
)

# All CD-related file extensions — superset of CD_DATA_EXTENSIONS; includes
# cue sheets and track-index files that accompany the data track.
CD_ALL_EXTENSIONS: frozenset[str] = CD_DATA_EXTENSIONS | {".cue", ".ccd"}

# Systems where each game lives in its own subfolder rather than as a flat
# file (e.g. PS1 multi-track .cue+.bin, Saturn, Dreamcast).
# CD Folder normalizer mode scans these folder-by-folder.
CD_FOLDER_SYSTEMS: frozenset[str] = frozenset({"SEGACD", "SAT", "DC", "PS1"})

# Systems that use "cd-bram.brm" (vs "bram.srm") on the MEGA EverDrive.
# Only Sega CD produces a CD-style BRAM file on that device.
MEGA_EVERDRIVE_CD_SYSTEMS: frozenset[str] = frozenset({"SEGACD"})

# ---------------------------------------------------------------------------
# Save file extensions
# ---------------------------------------------------------------------------

# Canonical union of all save formats across every supported emulator / device.
SAVE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".sav",    # Generic / most cartridge systems
        ".srm",    # RetroArch default (SRAM)
        ".bkr",    # Beetle Saturn / Mednafen Saturn
        ".mcr",    # PS1 memory card (ePSXe)
        ".mcd",    # PS1 memory card (Bizhawk / Mednafen)
        ".mc2",    # PS2 memory card
        ".ps2",    # PS2 memory card (alternate)
        ".dsv",    # Nintendo DS (DeSmuME)
        ".frz",    # Freeze / state (some emulators)
        ".fs",     # Freeze state (some emulators)
        ".rtc",    # Real-time clock data
        ".raw",    # Raw save (some emulators)
    }
)

# Companion files renamed alongside the ROM (not treated as ROMs themselves).
COMPANION_EXTENSIONS: frozenset[str] = frozenset({".msu", ".pcm", ".cue"})

# Ordered list for UI save-extension dropdowns (most common first).
SAVE_EXT_CHOICES: list[str] = [
    ".sav",    # most common generic
    ".srm",    # RetroArch
    ".bkr",    # Beetle Saturn
    ".mcr",    # PS1 ePSXe
    ".mcd",    # PS1 Bizhawk/Mednafen
    ".mc2",    # PS2
    ".ps2",    # PS2 alternate
    ".dsv",    # NDS DeSmuME
    ".frz",
    ".fs",
    ".raw",
    ".rtc",
]

# Per-system override for the default save extension when the profile hasn't
# specified one explicitly.
SYSTEM_DEFAULT_SAVE_EXT: dict[str, str] = {
    "SAT": ".bkr",
}

# ---------------------------------------------------------------------------
# No-Intro DAT keyword lookup
# ---------------------------------------------------------------------------

# Maps system code → list of substrings to match against DAT file names
# (case-insensitive). Earlier entries in each list are preferred when multiple
# DAT files match.
SYSTEM_DAT_KEYWORDS: dict[str, list[str]] = {
    "SNES":   ["Super Nintendo Entertainment System", "Super Nintendo"],
    "NES":    ["Nintendo Entertainment System"],
    "FDS":    ["Family Computer Disk System"],
    "GBA":    ["Game Boy Advance"],
    "GBC":    ["Game Boy Color"],
    "GB":     ["Game Boy"],
    "N64":    ["Nintendo 64"],
    "N64DD":  ["Nintendo 64DD"],
    "NDS":    ["Nintendo DS"],
    "GC":     ["GameCube", "Gamecube"],
    "VB":     ["Nintendo - Virtual Boy", "Virtual Boy"],
    "MD":     ["Mega Drive", "Genesis"],
    "32X":    ["32X"],
    "SEGACD": ["Sega - Mega-CD", "Mega-CD", "Sega CD"],
    "GG":     ["Game Gear"],
    "SMS":    ["Master System"],
    "SAT":    ["Sega - Saturn", "Saturn"],
    "DC":     ["Sega - Dreamcast", "Dreamcast"],
    "PCE":    ["NEC - PC Engine - TurboGrafx-16", "NEC - PC Engine - TurboGrafx 16",
               "PC Engine - TurboGrafx-16", "PC Engine - TurboGrafx 16"],
    "PCSG":   ["NEC - PC Engine SuperGrafx", "PC Engine SuperGrafx", "SuperGrafx"],
    "PCECD":  ["PC Engine CD", "TurboGrafx CD", "PC Engine CD-ROM"],
    "NGP":    ["SNK - Neo Geo Pocket", "Neo Geo Pocket"],
    "NGPC":   ["SNK - Neo Geo Pocket Color", "Neo Geo Pocket Color"],
    "NEOCD":  ["SNK - Neo Geo CD", "Neo Geo CD"],
    "NEOGEO": ["Neo Geo"],
    "LYNX":   ["Atari - Lynx", "Lynx"],
    "WSWAN":  ["Bandai - WonderSwan", "WonderSwan"],
    "WSWANC": ["Bandai - WonderSwan Color", "WonderSwan Color"],
    "PS1":    ["Sony - PlayStation"],
    "PS2":    ["Sony - PlayStation 2"],
    "PSP":    ["Sony - PlayStation Portable"],
    "PS3":    ["Sony - PlayStation 3"],
    "A2600":  ["Atari 2600"],
    "A7800":  ["Atari 7800"],
}

# ---------------------------------------------------------------------------
# Folder-name → system code (used by server ROM scanner and Steam Deck)
# ---------------------------------------------------------------------------

# Maps common emulator/RetroArch folder names (lowercase) to canonical system codes.
FOLDER_TO_SYSTEM: dict[str, str] = {
    "3do":             "3DO",
    "ags":             "ARCADE",
    "amiga":           "ARCADE",
    "amiga1200":       "ARCADE",
    "amiga600":        "ARCADE",
    "amigacd32":       "ARCADE",
    "amstradcpc":      "ARCADE",
    "atari2600":       "A2600",
    "atari5200":       "A5200",
    "atari7800":       "A7800",
    "atari800":        "A800",
    "atarijaguar":     "JAGUAR",
    "atarijaguarcd":   "JAGCD",
    "atarilynx":       "LYNX",
    "atarist":         "ATARIST",
    "atarixe":         "ATARIXED",
    "atomiswave":      "ARCADE",
    "arcade":          "ARCADE",
    "fba":             "FBA",
    "fbneo":           "FBNEO",
    "c64":             "ARCADE",
    "cavestory":       "ARCADE",
    "colecovision":    "ARCADE",
    "cps":             "CPS1",
    "cps1":            "CPS1",
    "cps2":            "CPS2",
    "cps3":            "CPS3",
    "daphne":          "ARCADE",
    "dreamcast":       "DC",
    "famicom":         "NES",
    "fds":             "FDS",
    "gameandwatch":    "ARCADE",
    "gamegear":        "GG",
    "gb":              "GB",
    "gba":             "GBA",
    "gbc":             "GBC",
    "gc":              "GC",
    "genesis":         "MD",
    "megadrive":       "MD",
    "megadrivejp":     "MD",
    "mastersystem":    "SMS",
    "megacd":          "SCD",
    "megacdjp":        "SCD",
    "sega32x":         "32X",
    "sega32xjp":       "32X",
    "sega32xna":       "32X",
    "segacd":          "SCD",
    "model2":          "ARCADE",
    "model3":          "ARCADE",
    "naomi":           "ARCADE",
    "naomigd":         "ARCADE",
    "n3ds":            "3DS",
    "n64":             "N64",
    "n64dd":           "N64DD",
    "nds":             "NDS",
    "nes":             "NES",
    "neogeo":          "NEOGEO",
    "neogeocd":        "NEOCD",
    "neogeocdjp":      "NEOCD",
    "ngp":             "NGP",
    "ngpc":            "NGPC",
    "pcengine":        "PCE",
    "pcenginecd":      "PCECD",
    "pcfx":            "PCE",
    "psx":             "PS1",
    "ps1":             "PS1",
    "ps2":             "PS2",
    "ps3":             "PS3",
    "psp":             "PSP",
    "psvita":          "VITA",
    "saturn":          "SAT",
    "saturnjp":        "SAT",
    "sfc":             "SNES",
    "sgb":             "SNES",
    "snes":            "SNES",
    "snesna":          "SNES",
    "sneshd":          "SNES",
    "satellaview":     "SNES",
    "sufami":          "SNES",
    "tg16":            "TG16",
    "tg-cd":           "PCECD",
    "virtualboy":      "VB",
    "wii":             "WII",
    "wonderswan":      "WSWAN",
    "wonderswancolor": "WSWANC",
    "mame":            "MAME",
    "mame-advmame":    "MAME",
    "mame-mame4all":   "MAME",
}

# ---------------------------------------------------------------------------
# UI display: system color mapping (used by Steam Deck scanner)
# ---------------------------------------------------------------------------

SYSTEM_COLOR: dict[str, str] = {
    "GBA":    "#7b1fa2",
    "GB":     "#388e3c",
    "GBC":    "#2e7d32",
    "SNES":   "#5d4037",
    "NES":    "#c62828",
    "N64":    "#1565c0",
    "MD":     "#0d47a1",
    "SMS":    "#1976d2",
    "GG":     "#00838f",
    "32X":    "#0277bd",
    "SEGACD": "#006064",
    "PCE":    "#558b2f",
    "PCECD":  "#558b2f",
    "TG16":   "#33691e",
    "TGCD":   "#33691e",
    "A2600":  "#e65100",
    "A7800":  "#bf360c",
    "LYNX":   "#4e342e",
    "NGP":    "#37474f",
    "NGPC":   "#263238",
    "WSWAN":  "#4527a0",
    "WSWANC": "#311b92",
    "NEOGEO": "#b71c1c",
    "ARCADE": "#880e4f",
    "PS1":    "#37474f",
    "PS2":    "#1a237e",
    "PS3":    "#212121",
    "PSP":    "#004d40",
    "NDS":    "#1b5e20",
    "3DS":    "#b71c1c",
    "GC":     "#4a148c",
    "WII":    "#880e4f",
    "NSW":    "#e53935",
}

DEFAULT_SYSTEM_COLOR: str = "#424242"

# ---------------------------------------------------------------------------
# PS1 / PS2 retail disc ID prefixes (used by Steam Deck scanner)
# ---------------------------------------------------------------------------

PSX_RETAIL_PREFIXES: frozenset[str] = frozenset(
    {
        "SLUS", "SCUS", "PAPX",          # NA
        "SLES", "SCES", "SCED",          # EU
        "SLPS", "SLPM", "SCPS", "SCPM",  # JP
        "SLAJ", "SLEJ", "SCAJ",          # Other
    }
)
