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

from .base import normalize_rom_name, sha256_file, find_paths
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
}

# Map: ROM folder name → (system_code, [core_save_subdirs], [save_exts])
ROM_FOLDER_MAP = {
    "gba":          ("GBA",    ["mGBA", "gpsp", "VBA-M", "VBA Next"],                   [".srm", ".sav"]),
    "gb":           ("GB",     ["Gambatte", "SameBoy", "mGBA", "TGB Dual"],               [".srm", ".sav"]),
    "gbc":          ("GBC",    ["Gambatte", "SameBoy", "mGBA"],                           [".srm", ".sav"]),
    "snes":         ("SNES",   ["Snes9x", "Snes9x 2010", "bsnes", "bsnes-hd beta", "Mesen-S"], [".srm"]),
    "nes":          ("NES",    ["Nestopia UE", "FCEUmm", "Mesen", "QuickNES"],            [".srm", ".sav"]),
    "n64":          ("N64",    ["Mupen64Plus-Next", "ParaLLEl N64"],                      [".srm"]),
    "genesis":      ("MD",     ["Genesis Plus GX", "Genesis Plus GX Wide", "PicoDrive", "BlastEm"], [".srm"]),
    "megadrive":    ("MD",     ["Genesis Plus GX", "Genesis Plus GX Wide", "PicoDrive"], [".srm"]),
    "mastersystem": ("SMS",    ["Genesis Plus GX", "PicoDrive"],                          [".srm"]),
    "gamegear":     ("GG",     ["Genesis Plus GX"],                                       [".srm"]),
    "32x":          ("32X",    ["PicoDrive"],                                             [".srm"]),
    "segacd":       ("SEGACD", ["Genesis Plus GX", "PicoDrive"],                          [".srm", ".bak"]),
    "pce":          ("PCE",    ["Beetle PCE", "Beetle PCE Fast"],                         [".srm"]),
    "pcecd":        ("PCECD",  ["Beetle PCE", "Beetle PCE Fast"],                         [".srm"]),
    "tg16":         ("TG16",   ["Beetle PCE", "Beetle PCE Fast"],                         [".srm"]),
    "tgcd":         ("TGCD",   ["Beetle PCE", "Beetle PCE Fast"],                         [".srm"]),
    "atari2600":    ("A2600",  ["Stella", "Stella 2014"],                                  [".srm"]),
    "atari7800":    ("A7800",  ["ProSystem"],                                              [".srm"]),
    "lynx":         ("LYNX",   ["Beetle Lynx", "Handy"],                                  [".srm"]),
    "ngp":          ("NGP",    ["Beetle NeoPop", "RACE"],                                  [".srm"]),
    "ngpc":         ("NGPC",   ["Beetle NeoPop", "RACE"],                                  [".srm"]),
    "wonderswan":   ("WSWAN",  ["Beetle Cygne"],                                           [".srm"]),
    "wonderswancolor": ("WSWANC", ["Beetle Cygne"],                                        [".srm"]),
    "neogeo":       ("NEOGEO", ["FinalBurn Neo", "MAME 2003-Plus", "MAME"],                [".srm"]),
    "arcade":       ("ARCADE", ["FinalBurn Neo", "MAME 2003-Plus", "MAME"],                [".srm"]),
    "fba":          ("ARCADE", ["FinalBurn Neo"],                                          [".srm"]),
}

# ROM file extensions to recognize
ROM_EXTENSIONS = {
    ".gba", ".gb", ".gbc",
    ".smc", ".sfc", ".fig",
    ".nes", ".fds",
    ".z64", ".n64", ".v64",
    ".md", ".gen", ".smd",
    ".sms", ".gg",
    ".32x",
    ".cue", ".chd", ".iso", ".bin",
    ".pce",
    ".a26", ".a78",
    ".lnx",
    ".ngp", ".ngc",
    ".ws", ".wsc",
    ".zip", ".7z",
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
    folder_info = None
    for folder_key, info in ROM_FOLDER_MAP.items():
        if info[0] == system:
            folder_info = info
            break

    if folder_info:
        core_dirs, save_exts = folder_info[1], folder_info[2]
    else:
        core_dirs = []
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
    # Locate RetroArch config and saves
    ra_config_dir = find_paths(
        FLATPAK_RA_DATA,
        emulation_path / "saves" / "retroarch",
        Path.home() / ".config" / "retroarch",
    )
    if ra_config_dir is None:
        return

    saves_dir = find_paths(
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
        slug = normalize_rom_name(rom_stem)
        title_id = f"{system}_{slug}"
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
        )
        if save_path and save_path.exists():
            try:
                entry.save_hash = sha256_file(save_path)
                stat = save_path.stat()
                entry.save_mtime = stat.st_mtime
                entry.save_size = stat.st_size
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
            slug = normalize_rom_name(rom_stem)
            title_id = f"{system}_{slug}"
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
            )
            if save_path and save_path.exists():
                try:
                    entry.save_hash = sha256_file(save_path)
                    stat = save_path.stat()
                    entry.save_mtime = stat.st_mtime
                    entry.save_size = stat.st_size
                except Exception:
                    pass
            yield entry
