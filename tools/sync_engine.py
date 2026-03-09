"""Sync engine for ROM-based save syncing (RetroArch, MiSTer, Analogue Pocket, etc.).

Standalone module — does not import from the server codebase.
Uses the server's raw save API for upload/download.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests

# ---------------------------------------------------------------------------
# ROM name normalization (mirrors server/app/services/rom_id.py)
# ---------------------------------------------------------------------------

SYSTEM_CODES = frozenset({
    "GBA", "SNES", "NES", "MD", "N64", "GB", "GBC", "GG", "NGP",
    "PCE", "PS1", "PS2", "SMS", "ATARI2600", "ATARI7800", "LYNX", "NEOGEO",
    "32X", "SEGACD", "SAT", "TG16", "WSWAN", "WSWANC", "DC", "NDS", "GC",
    "ARCADE", "MAME", "CPS1", "CPS2", "CPS3",
})

_REGION_RE = re.compile(
    r"\s*\((?:USA|Europe|Japan|World|Germany|France|Italy|Spain|Australia|"
    r"Brazil|Korea|China|Netherlands|Sweden|Denmark|Norway|Finland|Asia|"
    r"En|Ja|Fr|De|Es|It|Nl|Pt|Sv|No|Da|Fi|Ko|Zh|[A-Z][a-z,\s]+)\)",
    re.IGNORECASE,
)
_REV_RE = re.compile(
    r"\s*\((?:Rev\s*\w+|v\d[\d.]*|Version\s*\w+|Beta\s*\d*|Proto\s*\d*|Demo|Sample|Unl)\)",
    re.IGNORECASE,
)
_DISC_RE = re.compile(r"\s*\((?:Disc|Disk|CD)\s*\d+\)", re.IGNORECASE)
_EXTRA_RE = re.compile(r"\s*\([^)]+\)")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_MULTI_UNDERSCORE_RE = re.compile(r"_+")
_EMULATOR_TITLE_ID_RE = re.compile(r"^([A-Z0-9]{2,8})_([a-z0-9][a-z0-9_]{0,99})$")

# Region tag extraction for collision disambiguation
_REGION_EXTRACT_RE = re.compile(
    r"\((?P<region>USA|Europe|Japan|World|Germany|France|Italy|Spain|Australia|"
    r"Brazil|Korea|China|Netherlands|Sweden|Denmark|Norway|Finland|Asia)\)",
    re.IGNORECASE,
)


def _extract_region(stem: str) -> str:
    """Return first region tag found in a filename stem, lowercased (e.g. 'usa', 'europe')."""
    m = _REGION_EXTRACT_RE.search(stem)
    return m.group("region").lower() if m else ""


def normalize_rom_name(filename: str) -> str:
    """Strip extension, region/revision tags, normalize to lowercase slug."""
    name = filename
    for _ in range(3):
        dot_idx = name.rfind(".")
        if dot_idx <= 0:
            break
        suffix = name[dot_idx + 1:]
        if 1 <= len(suffix) <= 5 and suffix.isalnum():
            name = name[:dot_idx]
        else:
            break
    name = _REGION_RE.sub("", name)
    name = _REV_RE.sub("", name)
    name = _DISC_RE.sub("", name)
    name = _EXTRA_RE.sub("", name)
    name = name.lower()
    name = _NON_ALNUM_RE.sub("_", name)
    name = _MULTI_UNDERSCORE_RE.sub("_", name).strip("_")
    return name or "unknown"


def make_title_id(system: str, rom_filename: str) -> str:
    """Return canonical title_id e.g. GBA_zelda_the_minish_cap."""
    system = system.upper().strip()
    if system not in SYSTEM_CODES:
        raise ValueError(f"Unknown system code: {system!r}")
    return f"{system}_{normalize_rom_name(rom_filename)}"


def slug_to_display_name(slug: str) -> str:
    """Convert a slug like 'zelda_the_minish_cap' to 'Zelda The Minish Cap'."""
    return slug.replace("_", " ").title()


# ---------------------------------------------------------------------------
# Device profile mappings
# ---------------------------------------------------------------------------

# RetroArch core subdirectory name -> system code
RETROARCH_CORE_MAP: dict[str, str] = {
    "Snes9x": "SNES",
    "bsnes": "SNES",
    "bsnes-mercury": "SNES",
    "mGBA": "GBA",
    "VBA-M": "GBA",
    "Nestopia": "NES",
    "FCEUmm": "NES",
    "Genesis Plus GX": "MD",
    "PicoDrive": "MD",
    "Mupen64Plus-Next": "N64",
    "ParaLLEl N64": "N64",
    "Gambatte": "GB",
    "SameBoy": "GB",
    "Gearboy": "GBC",
    "TGB Dual": "GB",
    "Mednafen NGP": "NGP",
    "Beetle PCE": "PCE",
    "Beetle GG": "GG",
    "Beetle PSX": "PS1",
    "PCSX-ReARMed": "PS1",
    "SMS Plus GX": "SMS",
    "Stella": "ATARI2600",
    "ProSystem": "ATARI7800",
    "Beetle Lynx": "LYNX",
    "FinalBurn Neo": "ARCADE",
    "MAME": "MAME",
}

# MiSTer saves folder name -> system code
MISTER_FOLDER_MAP: dict[str, str] = {
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
    "Atari2600": "ATARI2600",
    "Atari7800": "ATARI7800",
    "Lynx": "LYNX",
    "NeoGeo": "NEOGEO",
    "32X": "32X",
    "MegaCD": "SEGACD",
    "PSX": "PS1",
}

# Analogue Pocket platform folder -> system code (standard Memories/<Platform>/ layout)
POCKET_FOLDER_MAP: dict[str, str] = {
    "GB": "GB",
    "GBA": "GBA",
    "GBC": "GBC",
    "GameGear": "GG",
    "SMS": "SMS",
    "NES": "NES",
    "SNES": "SNES",
    "Genesis": "MD",
    "NGP": "NGP",
    "NGPC": "NGP",
    "TurboGrafx-16": "PCE",
    "Lynx": "LYNX",
    "WonderSwan": "WSWAN",
    "WonderSwan Color": "WSWANC",
}

# Analogue Pocket openFPGA layout: Saves/<system>/... (lowercase folder names, deep structure).
# Used when saves are in a dedicated Saves/ tree that mirrors the Assets/ ROM tree.
POCKET_OPENFPGA_FOLDER_MAP: dict[str, str] = {
    # lowercase variants (openFPGA core convention)
    "gb": "GB",
    "gba": "GBA",
    "gbc": "GBC",
    "gg": "GG",
    "gamegear": "GG",
    "sms": "SMS",
    "nes": "NES",
    "snes": "SNES",
    "genesis": "MD",
    "megadrive": "MD",
    "md": "MD",
    "ngp": "NGP",
    "ngpc": "NGP",
    "pce": "PCE",
    "tg16": "PCE",
    "turbografx": "PCE",
    "lynx": "LYNX",
    "wswan": "WSWAN",
    "wonderswan": "WSWAN",
    "wswanc": "WSWANC",
    "n64": "N64",
    "ps1": "PS1",
    "psx": "PS1",
    "32x": "32X",
    "segacd": "SEGACD",
    "sat": "SAT",
    "saturn": "SAT",
    "segasaturn": "SAT",
}

# Save file extensions to consider
SAVE_EXTENSIONS = {".sav", ".srm", ".mcr", ".frz", ".fs", ".mcd", ".dsv", ".ps2"}

# ROM file extensions used when scanning ROM folders (Pocket openFPGA etc.)
ROM_EXTENSIONS = {
    ".sfc", ".smc",           # SNES
    ".gba",                   # GBA
    ".gb", ".gbc",            # GB/GBC
    ".nes",                   # NES
    ".md", ".smd", ".gen",    # Genesis/MD
    ".n64", ".z64", ".v64",   # N64
    ".gg",                    # Game Gear
    ".sms",                   # SMS
    ".pce",                   # PC Engine
    ".lnx",                   # Lynx
    ".ws", ".wsc",            # WonderSwan
    ".ngp", ".ngc",           # NGP
    ".nds",                   # NDS
}

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SaveFile:
    title_id: str              # e.g. GBA_zelda_the_minish_cap
    path: Optional[Path]       # local save path (or expected path); None for server-only saves
    hash: str                  # sha256 hex (empty string when no local save exists)
    mtime: float               # modification time (unix timestamp; 0 when no local save)
    system: str                # e.g. "GBA"
    game_name: str             # display name
    save_exists: bool = True   # False when ROM is present but no local save file exists yet


@dataclass
class SyncStatus:
    save: SaveFile
    server_hash: Optional[str] = None
    server_timestamp: Optional[str] = None
    server_name: Optional[str] = None
    last_synced_hash: Optional[str] = None
    # "up_to_date" | "local_newer" | "server_newer" | "not_on_server" | "conflict"
    status: str = "unknown"


# ---------------------------------------------------------------------------
# State file (tracks last-synced hash per title, like 3DS client's state/)
# ---------------------------------------------------------------------------

STATE_FILE = Path(__file__).parent / ".sync_state.json"


def _load_state() -> dict[str, str]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_state(state: dict[str, str]) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _update_state(title_id: str, hash_val: str) -> None:
    state = _load_state()
    state[title_id] = hash_val
    _save_state(state)


# ---------------------------------------------------------------------------
# Hash helpers
# ---------------------------------------------------------------------------

def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Profile scanning
# ---------------------------------------------------------------------------

def scan_profile(profile: dict) -> list[SaveFile]:
    """Walk a profile folder and return SaveFile entries for each save found.

    For all device types: when a separate save_folder is configured, the ROM
    folder (path) is scanned to build the list of games.  Only games whose ROM
    is physically present on the device are returned.  This prevents syncing
    saves for games you don't own / no longer have on the card.

    Profile dict keys:
        name        : str — display name
        device_type : str — "RetroArch" | "MiSTer" | "Pocket" | "Everdrive" | "Generic"
        path        : str — root folder path (ROMs / assets)
        save_folder : str — separate save folder when saves are not co-located with ROMs
        system      : str — system code override (used for Generic/Everdrive/flat folders)
    """
    device_type = profile.get("device_type", "Generic")
    rom_folder_str = profile.get("path", "")
    save_folder_str = profile.get("save_folder", "")
    system_override = profile.get("system", "").upper()

    # For most device types: if save_folder is set, saves live separately from ROMs.
    # folder = save location (for scan-saves fallbacks); rom_folder = ROM location.
    save_folder = Path(save_folder_str) if save_folder_str else None
    rom_folder = Path(rom_folder_str) if rom_folder_str else None
    # Convenience: the "active" folder for legacy save-based scanners
    folder = save_folder if (save_folder and save_folder.exists()) else (rom_folder or Path("."))

    if not folder.exists() and not (rom_folder and rom_folder.exists()):
        return []

    systems_filter: set[str] = set(profile.get("systems_filter") or [])
    results: list[SaveFile] = []

    if device_type == "RetroArch":
        # RetroArch: saves are already organised per-core; ROMs scattered elsewhere.
        # Scan saves folder; each save file name matches a ROM that was played.
        results = _scan_retroarch(folder)

    elif device_type == "MiSTer":
        # MiSTer: if a ROM folder is configured scan ROMs to get the full game list.
        # Otherwise fall back to scanning the saves folder.
        if rom_folder and rom_folder.exists() and save_folder and save_folder.exists():
            for sys_dir in sorted(rom_folder.iterdir()):
                if not sys_dir.is_dir():
                    continue
                sys_code = MISTER_FOLDER_MAP.get(sys_dir.name)
                if not sys_code:
                    continue
                sv_dir = save_folder / sys_dir.name
                results.extend(_scan_roms_match_saves(sys_dir, sv_dir, sys_code))
        else:
            results = _scan_mister(folder)

    elif device_type == "Pocket":
        # Pocket standard Memories layout — saves named after ROMs already.
        results = _scan_pocket(folder)

    elif device_type == "Pocket (openFPGA)":
        # ROM-based scan: Assets/<system>/... → Saves/<system>/...
        # This is the primary approach: only show games that exist on the SD card.
        if rom_folder and rom_folder.exists() and save_folder is not None:
            results = _scan_pocket_openfpga_from_roms(rom_folder, save_folder)
        elif save_folder and save_folder.exists():
            results = _scan_pocket_openfpga(save_folder)
        elif rom_folder and rom_folder.exists():
            results = _scan_pocket_openfpga(rom_folder)

    elif device_type == "EmuDeck":
        results = _scan_emudeck(folder)

    else:
        # Generic / Everdrive
        if system_override and system_override in SYSTEM_CODES:
            if rom_folder and rom_folder.exists() and save_folder is not None:
                # ROM-based: scan ROMs from path, expect saves in save_folder
                results = _scan_roms_match_saves(rom_folder, save_folder, system_override)
            else:
                # Co-located: saves are in the same folder as ROMs
                results = _scan_flat(folder, system_override, recursive=True)

    # Apply systems filter (empty = all systems pass through)
    if systems_filter:
        results = [r for r in results if r.system.upper() in systems_filter]

    return results


def _make_title_id_with_region(system: str, filename: str) -> str:
    """Like make_title_id but always appends the region tag when present.

    Keeps regional saves in separate server slots so a USA save never
    overwrites a Japan save on sync.
      "Super Mario World (USA).srm"    -> SNES_super_mario_world_usa
      "Super Mario World (Japan).srm"  -> SNES_super_mario_world_japan
      "Super Mario World.srm"          -> SNES_super_mario_world
    """
    region = _extract_region(Path(filename).stem)
    base = make_title_id(system, filename)  # region already stripped inside
    return f"{base}_{region}" if region else base


def _scan_flat(folder: Path, system: str, recursive: bool = False) -> list[SaveFile]:
    """Scan a folder of saves for a single system."""
    results = []
    candidates = sorted(folder.rglob("*") if recursive else folder.iterdir())
    for f in candidates:
        if f.is_file() and f.suffix.lower() in SAVE_EXTENSIONS:
            title_id = _make_title_id_with_region(system, f.name)
            file_hash = _hash_file(f)
            slug = title_id.split("_", 1)[1] if "_" in title_id else f.stem
            results.append(SaveFile(
                title_id=title_id,
                path=f,
                hash=file_hash,
                mtime=f.stat().st_mtime,
                system=system,
                game_name=slug_to_display_name(slug),
            ))
    return results


def _scan_roms_match_saves(
    rom_folder: Path,
    save_folder: Path,
    system: str,
    save_ext: str = ".sav",
    recursive: bool = True,
) -> list[SaveFile]:
    """Scan ROMs in rom_folder and find/expect saves in save_folder.

    For each ROM found, the expected save path is:
        save_folder / <rom_stem><save_ext>

    This is the correct approach for devices like Everdrive and Generic profiles
    where ROMs and saves live in separate folder trees.  Only games whose ROM is
    physically present are returned (save_exists=False means no save yet).
    """
    # Build a flat stem→[save_path] index from the save folder for fast lookup
    save_index: dict[str, Path] = {}
    if save_folder.exists():
        for f in save_folder.rglob("*"):
            if f.is_file() and f.suffix.lower() in SAVE_EXTENSIONS:
                save_index[f.stem.lower()] = f

    results: list[SaveFile] = []
    candidates = sorted(rom_folder.rglob("*") if recursive else rom_folder.iterdir())
    for rom_file in candidates:
        if not rom_file.is_file():
            continue
        if rom_file.suffix.lower() not in ROM_EXTENSIONS:
            continue
        if rom_file.name.startswith("."):
            continue

        title_id = _make_title_id_with_region(system, rom_file.name)
        slug = title_id.split("_", 1)[1] if "_" in title_id else rom_file.stem

        # Look up save by exact stem match first, then fall back to expected path
        save_path = save_index.get(rom_file.stem.lower())
        if save_path is None:
            # Save doesn't exist yet; compute expected destination path
            save_path = save_folder / (rom_file.stem + save_ext)
            file_hash = ""
            mtime = 0.0
            save_exists = False
        else:
            file_hash = _hash_file(save_path)
            mtime = save_path.stat().st_mtime
            save_exists = True

        results.append(SaveFile(
            title_id=title_id,
            path=save_path,
            hash=file_hash,
            mtime=mtime,
            system=system,
            game_name=slug_to_display_name(slug),
            save_exists=save_exists,
        ))
    return results


def _scan_retroarch(root: Path) -> list[SaveFile]:
    """Scan RetroArch saves/CoreName/game.srm structure."""
    results = []
    for core_dir in sorted(root.iterdir()):
        if not core_dir.is_dir():
            continue
        system = RETROARCH_CORE_MAP.get(core_dir.name)
        if not system:
            continue
        results.extend(_scan_flat(core_dir, system))
    return results


def _scan_mister(root: Path) -> list[SaveFile]:
    """Scan MiSTer saves/<System>/ structure."""
    results = []
    for sys_dir in sorted(root.iterdir()):
        if not sys_dir.is_dir():
            continue
        system = MISTER_FOLDER_MAP.get(sys_dir.name)
        if not system:
            continue
        results.extend(_scan_flat(sys_dir, system))
    return results


def _scan_pocket(root: Path) -> list[SaveFile]:
    """Scan Analogue Pocket Memories/<Platform>/ structure."""
    results = []
    for plat_dir in sorted(root.iterdir()):
        if not plat_dir.is_dir():
            continue
        system = POCKET_FOLDER_MAP.get(plat_dir.name)
        if not system:
            continue
        results.extend(_scan_flat(plat_dir, system))
    return results


def _scan_pocket_openfpga(saves_root: Path) -> list[SaveFile]:
    """Scan Analogue Pocket openFPGA layout: Saves/<system>/.../**/*.sav

    Used when saves live in a dedicated Saves/ tree that mirrors the Assets/
    ROM tree (e.g. Saves/snes/common/all/A-F/game.sav).  Folder names are
    matched case-insensitively against POCKET_OPENFPGA_FOLDER_MAP.
    """
    results = []
    for sys_dir in sorted(saves_root.iterdir()):
        if not sys_dir.is_dir():
            continue
        system = POCKET_OPENFPGA_FOLDER_MAP.get(sys_dir.name.lower())
        if not system:
            continue
        results.extend(_scan_flat(sys_dir, system, recursive=True))
    return results


def _scan_pocket_openfpga_from_roms(assets_root: Path, saves_root: Path) -> list[SaveFile]:
    """Scan Pocket openFPGA by walking the Assets (ROM) folder.

    For every ROM found under assets_root/<system>/..., computes the expected
    save path at saves_root/<system>/.../<rom_stem>.sav (same relative path,
    same stem, .sav extension).

    Returns a SaveFile for every ROM on the SD card:
    - save_exists=True  if the corresponding .sav already exists locally
    - save_exists=False if the ROM exists but no save file yet (so the server
      can still show a "Server newer" entry and offer to download)

    This guarantees:
    1. Only games physically present on the SD card appear in the sync table.
    2. Downloaded saves land at exactly the right path with the right filename
       so the core can find them.
    """
    results: list[SaveFile] = []
    for sys_dir in sorted(assets_root.iterdir()):
        if not sys_dir.is_dir():
            continue
        system = POCKET_OPENFPGA_FOLDER_MAP.get(sys_dir.name.lower())
        if not system:
            continue
        sys_folder_name = sys_dir.name  # preserve original case for saves path
        for rom_file in sorted(sys_dir.rglob("*")):
            if not rom_file.is_file():
                continue
            if rom_file.suffix.lower() not in ROM_EXTENSIONS:
                continue
            if rom_file.name.startswith("."):
                continue
            try:
                rel = rom_file.relative_to(sys_dir)
            except ValueError:
                continue
            # Mirror: saves_root/<sys>/<same subpath>/<rom_stem>.sav
            save_path = saves_root / sys_folder_name / rel.parent / (rom_file.stem + ".sav")
            title_id = _make_title_id_with_region(system, rom_file.name)
            slug = title_id.split("_", 1)[1] if "_" in title_id else rom_file.stem
            if save_path.exists():
                file_hash = _hash_file(save_path)
                mtime = save_path.stat().st_mtime
                save_exists = True
            else:
                file_hash = ""
                mtime = 0.0
                save_exists = False
            results.append(SaveFile(
                title_id=title_id,
                path=save_path,
                hash=file_hash,
                mtime=mtime,
                system=system,
                game_name=slug_to_display_name(slug),
                save_exists=save_exists,
            ))
    return results


# EmuDeck: emulator subfolder -> (saves subfolder, system code)
# Emulators with file-per-game saves that map cleanly to our slug format.
EMUDECK_EMULATOR_MAP: dict[str, tuple[str, str]] = {
    "duckstation": ("saves", "PS1"),   # .mcd memory card files, named by game
    "pcsx2":       ("saves", "PS2"),   # .ps2 shared memory cards (Mcd001.ps2 etc.)
    "melonds":     ("saves", "NDS"),   # .sav/.dsv per-game saves
    "flycast":     ("saves", "DC"),    # .sav Dreamcast VMU saves
}

# PSP product code prefix: 4 uppercase letters + 5 digits
_PSP_CODE_RE = re.compile(r"^([A-Z]{4}\d{5})")
# PS3 product code prefix: same pattern (BLUS, BLJM, NPUB, etc.)
_PS3_CODE_RE = re.compile(r"^([A-Z]{4}\d{5})")
# Files to skip when scanning PSP/PS3 save folders (metadata, icons)
_PSP_PS3_SKIP_EXTS = {".png", ".pmf", ".sfo", ".at3"}
# Duckstation memory card slot suffix: "_1", "_2" before extension
_MCD_SLOT_RE = re.compile(r"_\d+$")


def _scan_emudeck(root: Path) -> list[SaveFile]:
    """Scan an EmuDeck saves root folder.

    Handles:
      - duckstation  (PS1 .mcd files)
      - pcsx2        (PS2 .ps2 shared memory cards)
      - melonds      (NDS .sav/.dsv files)
      - flycast      (DC  .sav files)
      - ppsspp       (PSP saves — DATA.BIN per product code folder)
      - rpcs3        (PS3 saves — SYS-DATA/DATA.DAT per product code folder)

    Skipped: retroarch (no core-dir structure in EmuDeck — add a RetroArch
    profile instead), dolphin/citra/Cemu (complex internal formats).
    """
    results: list[SaveFile] = []

    # --- File-per-game emulators ---
    for emu_name, (subfolder, system) in EMUDECK_EMULATOR_MAP.items():
        saves_dir = root / emu_name / subfolder
        if not saves_dir.exists():
            continue
        for f in sorted(saves_dir.rglob("*")):
            if not f.is_file() or f.suffix.lower() not in SAVE_EXTENSIONS:
                continue
            # Strip memory card slot suffix (duckstation names files "Game_1.mcd")
            stem = _MCD_SLOT_RE.sub("", f.stem)
            display_name = stem + f.suffix
            title_id = make_title_id(system, display_name)
            file_hash = _hash_file(f)
            slug = title_id.split("_", 1)[1] if "_" in title_id else f.stem
            results.append(SaveFile(
                title_id=title_id,
                path=f,
                hash=file_hash,
                mtime=f.stat().st_mtime,
                system=system,
                game_name=slug_to_display_name(slug),
            ))

    # --- ppsspp: product-code folders, DATA.BIN/GAMESAV.BIN per slot ---
    ppsspp_saves = root / "ppsspp" / "saves"
    if ppsspp_saves.exists():
        # Group slot folders by 9-char product code; keep highest-mtime save file
        psp_best: dict[str, SaveFile] = {}
        for slot_dir in sorted(ppsspp_saves.iterdir()):
            if not slot_dir.is_dir():
                continue
            m = _PSP_CODE_RE.match(slot_dir.name)
            if not m:
                continue
            product_code = m.group(1)  # e.g. "UCES00422"
            # Find the actual save data file (skip icons/metadata)
            for f in sorted(slot_dir.iterdir()):
                if not f.is_file() or f.suffix.lower() in _PSP_PS3_SKIP_EXTS:
                    continue
                existing = psp_best.get(product_code)
                if existing is None or f.stat().st_mtime > existing.mtime:
                    file_hash = _hash_file(f)
                    psp_best[product_code] = SaveFile(
                        title_id=product_code,
                        path=f,
                        hash=file_hash,
                        mtime=f.stat().st_mtime,
                        system="PSP",
                        game_name=product_code,
                    )
        results.extend(psp_best.values())

    # --- rpcs3: product-code folders, SYS-DATA/DATA.DAT/GAME per save ---
    rpcs3_saves = root / "rpcs3" / "saves"
    if rpcs3_saves.exists():
        ps3_best: dict[str, SaveFile] = {}
        for save_dir in sorted(rpcs3_saves.iterdir()):
            if not save_dir.is_dir():
                continue
            m = _PS3_CODE_RE.match(save_dir.name)
            if not m:
                continue
            product_code = m.group(1)  # e.g. "BLJM60055"
            for f in sorted(save_dir.rglob("*")):
                if not f.is_file() or f.suffix.lower() in _PSP_PS3_SKIP_EXTS:
                    continue
                existing = ps3_best.get(product_code)
                if existing is None or f.stat().st_mtime > existing.mtime:
                    file_hash = _hash_file(f)
                    ps3_best[product_code] = SaveFile(
                        title_id=product_code,
                        path=f,
                        hash=file_hash,
                        mtime=f.stat().st_mtime,
                        system="PS3",
                        game_name=product_code,
                    )
        results.extend(ps3_best.values())

    return results


# ---------------------------------------------------------------------------
# Server comparison
# ---------------------------------------------------------------------------

def compare_with_server(
    saves: list[SaveFile],
    base_url: str,
    headers: dict,
    timeout: int = 30,
    systems_filter: Optional[set[str]] = None,
) -> list[SyncStatus]:
    """Compare local saves with server and also fetch server-only titles.

    systems_filter: if non-empty, server-only titles whose system is NOT in
    the set are excluded from results.  Local saves are never filtered here
    (they were already filtered by scan_profile).

    Returns a combined list: local saves (with their sync status) followed by
    any server-only titles that have no matching local save.
    """
    state = _load_state()
    results = []
    seen_title_ids: set[str] = set()

    for save in saves:
        seen_title_ids.add(save.title_id)
        last_synced = state.get(save.title_id)
        try:
            resp = requests.get(
                f"{base_url}/api/v1/saves/{save.title_id}/meta",
                headers=headers,
                timeout=timeout,
            )
        except requests.RequestException:
            if save.save_exists:
                results.append(SyncStatus(save=save, status="error"))
            continue

        if resp.status_code == 404:
            if not save.save_exists:
                # ROM present but no local save and nothing on server — nothing to do
                continue
            results.append(SyncStatus(
                save=save,
                last_synced_hash=last_synced,
                status="not_on_server",
            ))
            continue

        if resp.status_code != 200:
            if save.save_exists:
                results.append(SyncStatus(save=save, status="error"))
            continue

        meta = resp.json()
        server_hash = meta.get("save_hash", "")
        server_ts = meta.get("server_timestamp", "")
        server_name = meta.get("name", "")

        if not save.save_exists:
            # ROM present, no local save, server has a save — always offer download
            status = "server_newer"
        else:
            status = _determine_status(save.hash, server_hash, last_synced)
        results.append(SyncStatus(
            save=save,
            server_hash=server_hash,
            server_timestamp=server_ts,
            server_name=server_name,
            last_synced_hash=last_synced,
            status=status,
        ))

    # Fetch server-only titles (exist on server but not found in any local profile)
    try:
        resp = requests.get(
            f"{base_url}/api/v1/titles",
            headers=headers,
            timeout=timeout,
        )
        if resp.status_code == 200:
            body = resp.json()
            # Response is either a list or {"titles": [...]}
            titles_list = body if isinstance(body, list) else body.get("titles", [])
            for title in titles_list:
                tid = title.get("title_id", "")
                if not tid or tid in seen_title_ids:
                    continue
                system = title.get("system") or title.get("platform", "")
                if systems_filter and system.upper() not in systems_filter:
                    continue
                name = title.get("name", tid)
                server_hash = title.get("save_hash", "")
                server_ts = title.get("server_timestamp", "")
                phantom = SaveFile(
                    title_id=tid,
                    path=None,
                    hash="",
                    mtime=0.0,
                    system=system,
                    game_name=name,
                )
                results.append(SyncStatus(
                    save=phantom,
                    server_hash=server_hash,
                    server_timestamp=server_ts,
                    server_name=name,
                    status="server_only",
                ))
    except requests.RequestException:
        pass  # Server unreachable — local-only results still returned

    return results


def _determine_status(
    local_hash: str,
    server_hash: str,
    last_synced_hash: Optional[str],
) -> str:
    """Three-way hash comparison (mirrors the 3DS client's sync logic)."""
    if local_hash == server_hash:
        return "up_to_date"
    if last_synced_hash is None:
        # No sync history — treat server as authoritative if both exist
        return "conflict"
    if last_synced_hash == server_hash:
        return "local_newer"   # Only local changed
    if last_synced_hash == local_hash:
        return "server_newer"  # Only server changed
    return "conflict"          # Both changed


# ---------------------------------------------------------------------------
# Upload / download
# ---------------------------------------------------------------------------

def upload_save(
    title_id: str,
    path: Path,
    base_url: str,
    headers: dict,
    force: bool = False,
    timeout: int = 30,
) -> None:
    """Upload a raw save file to the server via POST /api/v1/saves/{title_id}/raw."""
    params = {"force": "true"} if force else {}
    data = path.read_bytes()
    resp = requests.post(
        f"{base_url}/api/v1/saves/{title_id}/raw",
        headers={**headers, "Content-Type": "application/octet-stream"},
        params=params,
        data=data,
        timeout=timeout,
    )
    resp.raise_for_status()
    # Update local state with hash of uploaded file
    local_hash = hashlib.sha256(data).hexdigest()
    _update_state(title_id, local_hash)


def download_save(
    title_id: str,
    dest_path: Path,
    base_url: str,
    headers: dict,
    timeout: int = 30,
) -> str:
    """Download save from server to dest_path. Returns the server hash."""
    resp = requests.get(
        f"{base_url}/api/v1/saves/{title_id}/raw",
        headers=headers,
        timeout=timeout,
    )
    resp.raise_for_status()
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_bytes(resp.content)
    server_hash = resp.headers.get("X-Save-Hash", hashlib.sha256(resp.content).hexdigest())
    _update_state(title_id, server_hash)
    return server_hash
