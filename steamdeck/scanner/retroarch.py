"""
RetroArch scanner for EmuDeck on Steam Deck.

Strategy (mirrors the Android app):
  1. Parse RetroArch playlist .lpl files → (ROM path, core name, system)
  2. Scan ROM directories under ~/Emulation/roms/ for known systems
  3. For each ROM, look for a matching save file in the saves/ folder
"""

import json
import re
from pathlib import Path
from typing import Generator, Optional

from .base import sha256_file, find_paths, to_title_id
from .models import GameEntry

# RetroArch Flatpak paths
FLATPAK_RA_DATA = Path.home() / ".var/app/org.libretro.RetroArch/config/retroarch"

# EmuDeck symlinked paths
EMUDECK_RA_SAVES = Path.home() / "Emulation/saves/retroarch/saves"

# Map: core name keyword → system code
CORE_SYSTEM_MAP = {
    "game boy advance": "GBA",
    "mgba": "GBA",
    "gpsp": "GBA",
    "vba": "GBA",
    "game boy color": "GBC",
    "gambatte": "GBC",  # handles both GB and GBC; refined by ROM folder
    "sameboy": "GBC",
    "super nintendo": "SNES",
    "snes9x": "SNES",
    "bsnes": "SNES",
    "mesen-s": "SNES",
    "nintendo - nes": "NES",
    "nestopia": "NES",
    "fceumm": "NES",
    "mesen": "NES",
    "nintendo 64": "N64",
    "mupen64plus": "N64",
    "parallel n64": "N64",
    "sega - mega drive": "MD",
    "genesis plus gx": "MD",
    "picodrive": "MD",
    "blastem": "MD",
    "beetle saturn": "SAT",
    "kronos": "SAT",
    "yabause": "SAT",
    "yabasanshiro": "SAT",
    "sega - master system": "SMS",
    "sega - game gear": "GG",
    "sega - 32x": "32X",
    "sega cd": "SEGACD",
    "pc engine": "PCE",
    "beetle pce": "PCE",
    "atari - 2600": "A2600",
    "stella": "A2600",
    "atari - 7800": "A7800",
    "prosystem": "A7800",
    "atari lynx": "LYNX",
    "handy": "LYNX",
    "neo geo pocket": "NGPC",
    "beetle neopop": "NGPC",
    "race": "NGPC",
    "wonderswan": "WSWAN",
    "beetle cygne": "WSWAN",
    "neo geo": "NEOGEO",
    "fbneo": "NEOGEO",
    "finalburn neo": "NEOGEO",
    "mame": "ARCADE",
    "game boy": "GB",
    # Dreamcast
    "dreamcast": "DC",
    "flycast": "DC",
    "reicast": "DC",
    "redream": "DC",
    # 3DO
    "3do": "3DO",
    "opera": "3DO",
    # Virtual Boy
    "virtual boy": "VB",
    "beetle vb": "VB",
    "vecx": "VB",
    # Atari 800 / 5200
    "atari800": "A800",
    "atari 800": "A800",
    "atari - 5200": "A5200",
    "atari 5200": "A5200",
    # Atari Jaguar
    "atari - jaguar": "JAGUAR",
    "virtual jaguar": "JAGUAR",
    # Pokemon Mini
    "pokemini": "POKEMINI",
    "pokémon mini": "POKEMINI",
    # Home computer / arcade-ish systems (grouped under ARCADE for now)
    "commodore - 64": "ARCADE",
    "vice x64": "ARCADE",
    "amiga": "ARCADE",
    "puae": "ARCADE",
    "fs-uae": "ARCADE",
    "msx": "ARCADE",
    "bluemsx": "ARCADE",
    "fmsx": "ARCADE",
    "colecovision": "ARCADE",
    "bluemsx coleco": "ARCADE",
    "intellivision": "ARCADE",
    "freeintv": "ARCADE",
    "amstrad": "ARCADE",
    "cap32": "ARCADE",
    "zx spectrum": "ARCADE",
    "fuse": "ARCADE",
    # Neo Geo CD
    "neogeo cd": "NEOCD",
    "neocd": "NEOCD",
}

# Map: ROM folder name → (system_code, [core_save_subdirs], [save_exts])
ROM_FOLDER_MAP = {
    "gba": ("GBA", ["mGBA", "gpsp", "VBA-M", "VBA Next"], [".srm", ".sav"]),
    "gb": ("GB", ["Gambatte", "SameBoy", "mGBA", "TGB Dual"], [".srm", ".sav"]),
    "gbc": ("GBC", ["Gambatte", "SameBoy", "mGBA"], [".srm", ".sav"]),
    "snes": (
        "SNES",
        ["Snes9x", "Snes9x 2010", "bsnes", "bsnes-hd beta", "Mesen-S"],
        [".srm"],
    ),
    "nes": ("NES", ["Nestopia UE", "FCEUmm", "Mesen", "QuickNES"], [".srm", ".sav"]),
    "n64": ("N64", ["Mupen64Plus-Next", "ParaLLEl N64"], [".srm"]),
    "genesis": (
        "MD",
        ["Genesis Plus GX", "Genesis Plus GX Wide", "PicoDrive", "BlastEm"],
        [".srm"],
    ),
    "megadrive": (
        "MD",
        ["Genesis Plus GX", "Genesis Plus GX Wide", "PicoDrive"],
        [".srm"],
    ),
    "mastersystem": ("SMS", ["Genesis Plus GX", "PicoDrive"], [".srm"]),
    "gamegear": ("GG", ["Genesis Plus GX"], [".srm"]),
    "32x": ("32X", ["PicoDrive"], [".srm"]),
    "segacd": ("SEGACD", ["Genesis Plus GX", "PicoDrive"], [".srm", ".bak"]),
    "saturn": ("SAT", ["Beetle Saturn", "Kronos"], [".bkr"]),
    "pce": ("PCE", ["Beetle PCE", "Beetle PCE Fast"], [".srm"]),
    "pcecd": ("PCECD", ["Beetle PCE", "Beetle PCE Fast"], [".srm"]),
    "tg16": ("TG16", ["Beetle PCE", "Beetle PCE Fast"], [".srm"]),
    "tgcd": ("TGCD", ["Beetle PCE", "Beetle PCE Fast"], [".srm"]),
    "atari2600": ("A2600", ["Stella", "Stella 2014"], [".srm"]),
    "atari7800": ("A7800", ["ProSystem"], [".srm"]),
    "lynx": ("LYNX", ["Beetle Lynx", "Handy"], [".srm"]),
    "ngp": ("NGP", ["Beetle NeoPop", "RACE"], [".srm"]),
    "ngpc": ("NGPC", ["Beetle NeoPop", "RACE"], [".srm"]),
    "wonderswan": ("WSWAN", ["Beetle Cygne"], [".srm"]),
    "wonderswancolor": ("WSWANC", ["Beetle Cygne"], [".srm"]),
    "neogeo": ("NEOGEO", ["FinalBurn Neo", "MAME 2003-Plus", "MAME"], [".srm"]),
    "arcade": ("ARCADE", ["FinalBurn Neo", "MAME 2003-Plus", "MAME"], [".srm"]),
    "fba": ("ARCADE", ["FinalBurn Neo"], [".srm"]),
    # --- Dreamcast (Flycast stores VMU saves per-game as .bin/.vmu) ---
    "dreamcast": ("DC", ["Flycast", "Redream"], [".srm", ".bin", ".vmu"]),
    "dc": ("DC", ["Flycast", "Redream"], [".srm", ".bin", ".vmu"]),
    # --- 3DO ---
    "3do": ("3DO", ["Opera"], [".srm", ".sav"]),
    # --- Virtual Boy ---
    "virtualboy": ("VB", ["Beetle VB"], [".srm"]),
    "vb": ("VB", ["Beetle VB"], [".srm"]),
    # --- Pokémon Mini ---
    "pokemini": ("POKEMINI", ["PokeMini"], [".eep", ".srm"]),
    # --- Atari computers / consoles beyond 2600/7800 ---
    "atari800": ("A800", ["Atari800"], [".srm", ".sav"]),
    "atari5200": ("A5200", ["Atari800"], [".srm", ".sav"]),
    "atarijaguar": ("JAGUAR", ["Virtual Jaguar"], [".srm"]),
    "jaguar": ("JAGUAR", ["Virtual Jaguar"], [".srm"]),
    # --- Home computers (grouped under ARCADE as the server bucket for now) ---
    "c64": ("ARCADE", ["VICE x64", "VICE x64sc", "Frodo"], [".srm", ".sav"]),
    "commodore64": ("ARCADE", ["VICE x64", "VICE x64sc"], [".srm", ".sav"]),
    "amiga": ("ARCADE", ["PUAE", "PUAE 2021", "FS-UAE"], [".srm", ".sav"]),
    "amiga500": ("ARCADE", ["PUAE", "PUAE 2021"], [".srm", ".sav"]),
    "amigacd32": ("ARCADE", ["PUAE", "PUAE 2021"], [".srm", ".sav"]),
    "msx": ("ARCADE", ["blueMSX", "fMSX"], [".srm", ".sav"]),
    "msx2": ("ARCADE", ["blueMSX", "fMSX"], [".srm", ".sav"]),
    "colecovision": ("ARCADE", ["blueMSX", "Gearcoleco"], [".srm", ".sav"]),
    "coleco": ("ARCADE", ["blueMSX", "Gearcoleco"], [".srm", ".sav"]),
    "intellivision": ("ARCADE", ["FreeIntv"], [".srm", ".sav"]),
    "amstradcpc": ("ARCADE", ["Caprice32", "CrocoDS"], [".srm", ".sav"]),
    "amstrad": ("ARCADE", ["Caprice32", "CrocoDS"], [".srm", ".sav"]),
    "zxspectrum": ("ARCADE", ["Fuse", "FBNeo"], [".srm", ".sav"]),
    "spectrum": ("ARCADE", ["Fuse"], [".srm", ".sav"]),
    # --- Neo Geo CD ---
    "neogeocd": ("NEOCD", ["NeoCD"], [".srm"]),
    "ngcd": ("NEOCD", ["NeoCD"], [".srm"]),
}

# ROM file extensions to recognize
ROM_EXTENSIONS = {
    ".gba",
    ".gb",
    ".gbc",
    ".smc",
    ".sfc",
    ".fig",
    ".nes",
    ".fds",
    ".z64",
    ".n64",
    ".v64",
    ".md",
    ".gen",
    ".smd",
    ".sms",
    ".gg",
    ".32x",
    ".cue",
    ".chd",
    ".iso",
    ".bin",
    ".pce",
    ".a26",
    ".a78",
    ".lnx",
    ".ngp",
    ".ngc",
    ".ws",
    ".wsc",
    ".zip",
    ".7z",
    # Dreamcast
    ".cdi",
    ".gdi",
    # Virtual Boy
    ".vb",
    # Pokémon Mini
    ".min",
    # Atari 800 / 5200
    ".atr",
    ".xex",
    ".a52",
    # Atari Jaguar
    ".j64",
    ".jag",
    # C64
    ".d64",
    ".t64",
    ".prg",
    ".crt",
    # Amiga
    ".adf",
    ".ipf",
    ".hdf",
    ".adz",
    # MSX / Amstrad / CPC
    ".dsk",
    ".cas",
    ".rom",
    # ColecoVision
    ".col",
    # Intellivision
    ".int",
    # Amstrad CPC
    ".cpc",
    # ZX Spectrum
    ".tzx",
    ".tap",
    ".sna",
    ".z80",
}


def _resolve_saves_dir(ra_config_dir: Path) -> Path:
    """Read retroarch.cfg for savefile_directory, fall back to saves/."""
    cfg_path = ra_config_dir / "retroarch.cfg"
    if cfg_path.exists():
        try:
            text = cfg_path.read_text(errors="ignore")
            m = re.search(r'^savefile_directory\s*=\s*"(.+)"', text, re.MULTILINE)
            if m:
                p = Path(m.group(1).strip())
                if p.exists():
                    return p
        except Exception:
            pass
    return ra_config_dir / "saves"


def _core_to_system(core_name: str) -> Optional[str]:
    """Map a RetroArch core display name to a system code."""
    cl = core_name.lower()
    for keyword, system in CORE_SYSTEM_MAP.items():
        if keyword in cl:
            return system
    return None


def _folder_to_system(folder_name: str) -> Optional[str]:
    """Map a ROM folder name to a system code."""
    return ROM_FOLDER_MAP.get(folder_name.lower(), (None,))[0]


def _parse_playlists(playlists_dir: Path) -> list[tuple[Path, str]]:
    """
    Parse all .lpl playlist files.
    Returns list of (rom_path, system_code) pairs.
    """
    results: list[tuple[Path, str]] = []
    if not playlists_dir.exists():
        return results

    for lpl_file in playlists_dir.glob("*.lpl"):
        try:
            data = json.loads(lpl_file.read_text(errors="ignore"))
            items = data.get("items", [])
            for item in items:
                rom_path_str = item.get("path", "")
                core_name = item.get("core_name", "")
                if not rom_path_str:
                    continue
                rom_path = Path(rom_path_str)
                # Determine system: core first, then ROM parent folder name
                system = _core_to_system(core_name)
                if not system:
                    system = _folder_to_system(rom_path.parent.name)
                if not system:
                    # Try grandparent (roms/<system>/<game>/game.cue)
                    system = _folder_to_system(rom_path.parent.parent.name)
                if system:
                    results.append((rom_path, system))
        except Exception:
            continue
    return results


def _find_save_for_rom(
    rom_stem: str,
    system: str,
    saves_dir: Path,
) -> Optional[Path]:
    """Find a RetroArch save file matching the ROM stem."""
    if system == "SAT":
        candidates = [
            saves_dir / f"{rom_stem}.srm",
            saves_dir / "Beetle Saturn" / f"{rom_stem}.bkr",
            saves_dir / "Kronos" / f"{rom_stem}.bkr",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate

        shared_candidates = [
            saves_dir / "yabasanshiro" / "backup.bin",
            saves_dir / "backup.bin",
        ]
        for candidate in shared_candidates:
            if candidate.exists():
                return candidate

        if (saves_dir / "yabasanshiro").exists():
            return saves_dir / "yabasanshiro" / "backup.bin"
        if (saves_dir / "Beetle Saturn").exists() or (saves_dir / "Kronos").exists():
            return saves_dir / "Beetle Saturn" / f"{rom_stem}.bkr"
        return saves_dir / f"{rom_stem}.srm"

    # Collect every ROM_FOLDER_MAP entry that targets this system, so we
    # search all relevant core-specific save subdirs.  Many systems share the
    # same system code (e.g. ARCADE covers MAME, FBNeo, VICE, PUAE, blueMSX…),
    # and each core writes to its own subdirectory.
    core_dirs: list[str] = []
    save_exts: list[str] = []
    seen_dirs: set[str] = set()
    seen_exts: set[str] = set()
    for _folder_key, info in ROM_FOLDER_MAP.items():
        if info[0] != system:
            continue
        for c in info[1]:
            if c not in seen_dirs:
                seen_dirs.add(c)
                core_dirs.append(c)
        for ext in info[2]:
            if ext not in seen_exts:
                seen_exts.add(ext)
                save_exts.append(ext)

    if not save_exts:
        save_exts = [".srm", ".sav"]

    # Search in core-specific subdirs and in root saves dir
    search_dirs = [saves_dir / c for c in core_dirs] + [saves_dir]
    for d in search_dirs:
        if not d.exists():
            continue
        for ext in save_exts:
            candidate = d / f"{rom_stem}{ext}"
            if candidate.exists():
                return candidate
    return None


def scan(emulation_path: Path) -> Generator[GameEntry, None, None]:
    """
    Scan RetroArch saves, yielding GameEntry objects.
    Uses playlist-first strategy, falls back to ROM directory scan.
    """
    # Locate RetroArch config and saves — prefer user-configured emulation_path
    emu_ra = emulation_path / "saves" / "retroarch"
    ra_config_dir = find_paths(
        emu_ra,
        FLATPAK_RA_DATA,
        Path.home() / ".config" / "retroarch",
    )
    if ra_config_dir is None:
        return

    saves_dir = find_paths(
        emu_ra / "saves",
        emu_ra,
        EMUDECK_RA_SAVES,
        ra_config_dir / "saves",
        _resolve_saves_dir(ra_config_dir),
    )
    if saves_dir is None or not saves_dir.exists():
        return

    playlists_dir = ra_config_dir / "playlists"

    seen_title_ids: set[str] = set()

    # --- Tier 1: Playlists ---
    playlist_entries = _parse_playlists(playlists_dir)
    for rom_path, system in playlist_entries:
        if not rom_path.exists():
            continue
        rom_stem = rom_path.stem
        title_id = to_title_id(rom_stem, system)
        if title_id in seen_title_ids:
            continue
        seen_title_ids.add(title_id)

        save_path = _find_save_for_rom(rom_stem, system, saves_dir)
        entry = GameEntry(
            title_id=title_id,
            display_name=rom_stem,
            system=system,
            emulator="RetroArch",
            save_path=save_path,
            rom_path=rom_path,
            rom_filename=rom_path.name,
        )
        if save_path and save_path.exists():
            try:
                stat = save_path.stat()
                entry.save_mtime = stat.st_mtime
                entry.save_size = stat.st_size
                if not (system == "SAT" and save_path.name.lower() == "backup.bin"):
                    entry.save_hash = sha256_file(save_path)
            except Exception:
                pass
        yield entry

    # --- Tier 2: ROM directory scan (catches games not in playlists) ---
    roms_base = emulation_path / "roms"
    if not roms_base.exists():
        return

    for folder_name, (system, _, _) in ROM_FOLDER_MAP.items():
        rom_dir = roms_base / folder_name
        if not rom_dir.exists():
            continue
        for rom_file in rom_dir.rglob("*"):
            if not rom_file.is_file():
                continue
            if rom_file.suffix.lower() not in ROM_EXTENSIONS:
                continue
            rom_stem = rom_file.stem
            title_id = to_title_id(rom_stem, system)
            if title_id in seen_title_ids:
                continue
            seen_title_ids.add(title_id)

            save_path = _find_save_for_rom(rom_stem, system, saves_dir)
            entry = GameEntry(
                title_id=title_id,
                display_name=rom_stem,
                system=system,
                emulator="RetroArch",
                save_path=save_path,
                rom_path=rom_file,
                rom_filename=rom_file.name,
            )
            if save_path and save_path.exists():
                try:
                    stat = save_path.stat()
                    entry.save_mtime = stat.st_mtime
                    entry.save_size = stat.st_size
                    if not (system == "SAT" and save_path.name.lower() == "backup.bin"):
                        entry.save_hash = sha256_file(save_path)
                except Exception:
                    pass
            yield entry
