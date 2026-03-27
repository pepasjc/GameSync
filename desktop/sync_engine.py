"""Sync engine for ROM-based save syncing (RetroArch, MiSTer, Analogue Pocket, etc.).

Standalone module — does not import from the server codebase.
Uses the server's raw save API for upload/download.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import zipfile
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

_REGION_NAMES = {
    "usa", "europe", "japan", "world", "germany", "france", "italy", "spain",
    "australia", "brazil", "korea", "china", "netherlands", "sweden",
    "denmark", "norway", "finland", "asia",
}
_PAREN_GROUP_RE = re.compile(r"\(([^)]+)\)")


def _extract_regions(stem: str) -> list[str]:
    """Return ordered region tags from a name, e.g. ['usa', 'europe'].

    Only geographic region tags are kept. Language tags like (En,Fr,De) are ignored.
    """
    regions: list[str] = []
    seen: set[str] = set()
    for match in _PAREN_GROUP_RE.finditer(stem):
        for part in match.group(1).split(","):
            token = part.strip().lower()
            if token in _REGION_NAMES and token not in seen:
                seen.add(token)
                regions.append(token)
    return regions


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
ZIP_ROM_EXTENSIONS = {".zip"}

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
    legacy_title_id: str = ""
    canonical_title_id: str = ""
    title_id_source: str = "legacy"
    title_id_confidence: str = "legacy"
    alternate_paths: list[Path] = field(default_factory=list)
    profile_scope: str = ""


@dataclass
class SyncStatus:
    save: SaveFile
    server_hash: Optional[str] = None
    server_timestamp: Optional[str] = None
    server_name: Optional[str] = None
    last_synced_hash: Optional[str] = None
    # "up_to_date" | "local_newer" | "server_newer" | "not_on_server" | "conflict"
    status: str = "unknown"
    mapping_note: str = ""


# ---------------------------------------------------------------------------
# State file (tracks last-synced hash per title, like 3DS client's state/)
# ---------------------------------------------------------------------------

STATE_FILE = Path(__file__).parent / ".sync_state.json"
SCAN_CACHE_FILE = Path(__file__).parent / ".scan_cache.json"
SLOT_MAPPING_FILE = Path(__file__).parent / ".slot_mappings.json"
_SCAN_CACHE: dict[str, dict[str, object]] | None = None
_SCAN_CACHE_DIRTY = False
_SLOT_MAPPINGS: dict[str, dict[str, str]] | None = None
_SLOT_MAPPINGS_DIRTY = False


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


def _load_scan_cache() -> dict[str, dict[str, object]]:
    global _SCAN_CACHE
    if _SCAN_CACHE is not None:
        return _SCAN_CACHE
    if SCAN_CACHE_FILE.exists():
        try:
            data = json.loads(SCAN_CACHE_FILE.read_text(encoding="utf-8"))
            entries = data.get("entries", {})
            if isinstance(entries, dict):
                _SCAN_CACHE = entries
                return _SCAN_CACHE
        except Exception:
            pass
    _SCAN_CACHE = {}
    return _SCAN_CACHE


def _mark_scan_cache_dirty() -> None:
    global _SCAN_CACHE_DIRTY
    _SCAN_CACHE_DIRTY = True


def _flush_scan_cache() -> None:
    global _SCAN_CACHE_DIRTY
    if not _SCAN_CACHE_DIRTY:
        return
    cache = _load_scan_cache()
    SCAN_CACHE_FILE.write_text(
        json.dumps({"entries": cache}, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    _SCAN_CACHE_DIRTY = False


def _load_slot_mappings() -> dict[str, dict[str, str]]:
    global _SLOT_MAPPINGS
    if _SLOT_MAPPINGS is not None:
        return _SLOT_MAPPINGS
    if SLOT_MAPPING_FILE.exists():
        try:
            data = json.loads(SLOT_MAPPING_FILE.read_text(encoding="utf-8"))
            entries = data.get("entries", {})
            if isinstance(entries, dict):
                _SLOT_MAPPINGS = entries
                return _SLOT_MAPPINGS
        except Exception:
            pass
    _SLOT_MAPPINGS = {}
    return _SLOT_MAPPINGS


def _mark_slot_mappings_dirty() -> None:
    global _SLOT_MAPPINGS_DIRTY
    _SLOT_MAPPINGS_DIRTY = True


def _flush_slot_mappings() -> None:
    global _SLOT_MAPPINGS_DIRTY
    if not _SLOT_MAPPINGS_DIRTY:
        return
    mappings = _load_slot_mappings()
    SLOT_MAPPING_FILE.write_text(
        json.dumps({"entries": mappings}, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    _SLOT_MAPPINGS_DIRTY = False


def clear_slot_mappings() -> None:
    """Remove persisted effective-slot decisions so scan can recompute them."""
    global _SLOT_MAPPINGS, _SLOT_MAPPINGS_DIRTY
    _SLOT_MAPPINGS = {}
    _SLOT_MAPPINGS_DIRTY = False
    try:
        SLOT_MAPPING_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def clear_scan_cache() -> None:
    """Remove cached canonical scan matches so they can be recomputed."""
    global _SCAN_CACHE, _SCAN_CACHE_DIRTY
    _SCAN_CACHE = {}
    _SCAN_CACHE_DIRTY = False
    try:
        SCAN_CACHE_FILE.unlink(missing_ok=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Hash helpers
# ---------------------------------------------------------------------------

def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _unique_existing_save_paths(save: SaveFile) -> list[Path]:
    """Return unique existing local save paths for this entry."""
    paths: list[Path] = []
    for candidate in [save.path, *save.alternate_paths]:
        if candidate is None or candidate in paths:
            continue
        if candidate.exists():
            paths.append(candidate)
    return paths


def _detect_duplicate_local_conflict(save: SaveFile) -> tuple[bool, str]:
    """Return whether duplicate local save copies disagree byte-for-byte."""
    existing_paths = _unique_existing_save_paths(save)
    if len(existing_paths) <= 1:
        return False, ""

    hashes_by_path: list[tuple[Path, str]] = []
    seen_hashes: set[str] = set()
    for path in existing_paths:
        if save.path is not None and path == save.path and save.hash:
            hash_val = save.hash
        else:
            hash_val = _hash_file(path)
        hashes_by_path.append((path, hash_val))
        seen_hashes.add(hash_val)

    if len(seen_hashes) <= 1:
        return False, ""

    lines = ["Multiple local save copies differ for this game:"]
    lines.extend(str(path) for path, _ in hashes_by_path)
    lines.append("Download from server to overwrite all copies, or align them manually before upload.")
    return True, "\n".join(lines)


# ---------------------------------------------------------------------------
# Profile scanning
# ---------------------------------------------------------------------------

def _parse_systems_config(profile: dict) -> dict[str, dict]:
    """Return {system_code: info_dict} for enabled systems in new-format profiles.

    New format:  profile["systems"] = [{system, enabled, save_ext, save_folder}, …]
    Old format:  profile["systems_filter"] = ["GBA", "SNES", …]  (empty = all)

    Returns an empty dict when there is no filter at all (old format with no list,
    or new format where every system is enabled and has no per-system overrides that
    differ from the global defaults).
    """
    if "systems" in profile:
        return {
            s["system"]: s
            for s in profile["systems"]
            if s.get("enabled", True)
        }
    # Old format fallback
    sf = profile.get("systems_filter") or []
    if sf:
        return {s: {} for s in sf}
    return {}


def _profile_scope_key(profile: dict) -> str:
    """Return a stable cache namespace for a sync profile."""
    identity = {
        "name": profile.get("name", ""),
        "device_type": profile.get("device_type", ""),
        "path": profile.get("path", ""),
        "save_folder": profile.get("save_folder", ""),
        "system": profile.get("system", ""),
        "save_ext": profile.get("save_ext", ""),
        "systems": profile.get("systems", []),
        "systems_filter": profile.get("systems_filter", []),
    }
    return json.dumps(identity, sort_keys=True, separators=(",", ":"))


def _volume_identity(path_str: str) -> str:
    """Return a best-effort identity for the storage backing a profile path."""
    if not path_str:
        return ""
    path = Path(path_str)
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path

    anchor = resolved.anchor or str(resolved)
    parts = [anchor]

    if os.name == "nt":
        try:
            import ctypes

            volume_name = ctypes.create_unicode_buffer(261)
            fs_name = ctypes.create_unicode_buffer(261)
            serial = ctypes.c_uint()
            max_component = ctypes.c_uint()
            flags = ctypes.c_uint()
            ok = ctypes.windll.kernel32.GetVolumeInformationW(
                ctypes.c_wchar_p(anchor),
                volume_name,
                len(volume_name),
                ctypes.byref(serial),
                ctypes.byref(max_component),
                ctypes.byref(flags),
                fs_name,
                len(fs_name),
            )
            if ok:
                parts.append(f"serial={serial.value}")
                if volume_name.value:
                    parts.append(f"label={volume_name.value}")
        except Exception:
            pass

    try:
        stat = resolved.stat()
        parts.append(f"dev={getattr(stat, 'st_dev', '')}")
    except OSError:
        pass
    return "|".join(str(part) for part in parts if part != "")


def _profile_runtime_scope(profile: dict) -> str:
    """Return the profile namespace including mounted media identity."""
    identity = {
        "profile": _profile_scope_key(profile),
        "rom_volume": _volume_identity(profile.get("path", "")),
        "save_volume": _volume_identity(profile.get("save_folder", "")),
    }
    return json.dumps(identity, sort_keys=True, separators=(",", ":"))


def scan_profile(profile: dict, progress_callback=None, enable_auto_normalize: bool = True) -> list[SaveFile]:
    """Walk a profile folder and return SaveFile entries for each save found.

    For all device types: when a separate save_folder is configured, the ROM
    folder (path) is scanned to build the list of games.  Only games whose ROM
    is physically present on the device are returned.  This prevents syncing
    saves for games you don't own / no longer have on the card.

    Profile dict keys (new format):
        name        : str  — display name
        device_type : str  — "RetroArch" | "MiSTer" | "Pocket" | "Everdrive" | "Generic" | …
        path        : str  — root game / ROM folder
        save_folder : str  — global save root (empty = same as game folder)
        system      : str  — system code (Generic / Everdrive only)
        save_ext    : str  — save extension (Generic / Everdrive only)
        systems     : list — per-system config for multi-system devices:
                             [{system, enabled, save_ext, save_folder}, …]
    """
    device_type = profile.get("device_type", "Generic")
    rom_folder_str = profile.get("path", "")
    save_folder_str = profile.get("save_folder", "")
    system_override = profile.get("system", "").upper()
    save_ext = profile.get("save_ext", ".sav").strip()
    if not save_ext.startswith("."):
        save_ext = "." + save_ext

    # Per-system config map: {system_code: {save_ext, save_folder, …}}
    # Empty dict means "no filter / no overrides".
    systems_config = _parse_systems_config(profile)
    enabled_systems = list(systems_config.keys())

    save_folder = Path(save_folder_str) if save_folder_str else None
    rom_folder = Path(rom_folder_str) if rom_folder_str else None
    profile_scope = _profile_runtime_scope(profile)
    # Convenience: the "active" folder for legacy save-based scanners
    folder = save_folder if (save_folder and save_folder.exists()) else (rom_folder or Path("."))

    if not folder.exists() and not (rom_folder and rom_folder.exists()):
        return []

    results: list[SaveFile] = []
    profile_name = profile.get("name", "Profile")
    _emit_progress(progress_callback, f"Scanning local files for {profile_name}…", 0, None)

    if device_type == "RetroArch":
        # RetroArch: saves are already organised per-core; ROMs scattered elsewhere.
        results = _scan_retroarch(
            folder,
            progress_callback=progress_callback,
            enable_auto_normalize=enable_auto_normalize,
            profile_scope=profile_scope,
        )

    elif (
        device_type in ("Analogue Pocket", "Pocket", "Pocket (openFPGA)")
        and rom_folder and rom_folder.exists()
        and save_folder and save_folder.exists()
        and len(enabled_systems) == 1
    ):
        # Single-system Pocket profiles sometimes point directly at a mirrored
        # sub-root like Assets/gba/common and Saves/gba/common rather than the
        # global Assets/ and Saves/ roots. Scan those as direct ROM/save trees.
        sys_code = enabled_systems[0]
        sys_info = systems_config.get(sys_code, {})
        sys_ext = sys_info.get("save_ext", save_ext) or save_ext
        results = _scan_roms_match_saves(
            rom_folder,
            save_folder,
            sys_code,
            save_ext=sys_ext,
            recursive=True,
            progress_callback=progress_callback,
            enable_auto_normalize=enable_auto_normalize,
            mirror_relative_path=True,
            profile_scope=profile_scope,
        )

    elif device_type == "MiSTer":
        # MiSTer: ROM-based scan when both game and save folders are configured;
        # respects per-system save_ext and save_folder overrides.
        if rom_folder and rom_folder.exists() and save_folder and save_folder.exists():
            for sys_dir in sorted(rom_folder.iterdir()):
                if not sys_dir.is_dir():
                    continue
                sys_code = MISTER_FOLDER_MAP.get(sys_dir.name)
                if not sys_code:
                    continue
                if systems_config and sys_code not in systems_config:
                    continue
                sys_info = systems_config.get(sys_code, {})
                sys_ext = sys_info.get("save_ext", save_ext) or save_ext
                sys_sv_str = sys_info.get("save_folder", "")
                sv_dir = Path(sys_sv_str) if sys_sv_str else save_folder / sys_dir.name
                results.extend(_scan_roms_match_saves(
                    sys_dir, sv_dir, sys_code, save_ext=sys_ext,
                    progress_callback=progress_callback,
                    enable_auto_normalize=enable_auto_normalize,
                    profile_scope=profile_scope,
                ))
        else:
            results = _scan_mister(
                folder,
                progress_callback=progress_callback,
                enable_auto_normalize=enable_auto_normalize,
                profile_scope=profile_scope,
            )

    elif device_type == "Pocket":
        results = _scan_pocket(
            folder,
            progress_callback=progress_callback,
            enable_auto_normalize=enable_auto_normalize,
            profile_scope=profile_scope,
        )

    elif device_type == "Pocket (openFPGA)":
        if rom_folder and rom_folder.exists() and save_folder is not None:
            results = _scan_pocket_openfpga_from_roms(
                rom_folder, save_folder, save_ext=save_ext,
                progress_callback=progress_callback,
                enable_auto_normalize=enable_auto_normalize,
                profile_scope=profile_scope,
            )
        elif save_folder and save_folder.exists():
            results = _scan_pocket_openfpga(
                save_folder,
                progress_callback=progress_callback,
                enable_auto_normalize=enable_auto_normalize,
                profile_scope=profile_scope,
            )
        elif rom_folder and rom_folder.exists():
            results = _scan_pocket_openfpga(
                rom_folder,
                progress_callback=progress_callback,
                enable_auto_normalize=enable_auto_normalize,
                profile_scope=profile_scope,
            )

    elif device_type == "EmuDeck":
        results = _scan_emudeck(folder, progress_callback=progress_callback, profile_scope=profile_scope)

    elif device_type == "MemCard Pro":
        # MemCard Pro SD card: per-game PS1 memory cards in VIRTUAL MEMORY CARDS/<SERIAL>/
        # or flat *.mcd files in the root.  path (or save_folder) points to the SD card root.
        results = _scan_memcard_pro(folder, progress_callback=progress_callback, profile_scope=profile_scope)

    elif device_type == "MEGA EverDrive":
        # MEGA EverDrive Pro: gamedata/<Game Name>/bram.srm layout.
        # path (or save_folder) points to the gamedata/ folder.
        gamedata = save_folder if (save_folder and save_folder.exists()) else rom_folder
        if gamedata and gamedata.exists() and system_override and system_override in SYSTEM_CODES:
            results = _scan_mega_everdrive(
                gamedata,
                system_override,
                progress_callback=progress_callback,
                enable_auto_normalize=enable_auto_normalize,
                profile_scope=profile_scope,
            )

    else:
        # Generic / Everdrive — single system
        if system_override and system_override in SYSTEM_CODES:
            if rom_folder and rom_folder.exists() and save_folder is not None:
                results = _scan_roms_match_saves(
                    rom_folder, save_folder, system_override, save_ext=save_ext,
                    progress_callback=progress_callback,
                    enable_auto_normalize=enable_auto_normalize,
                    profile_scope=profile_scope,
                )
            else:
                results = _scan_flat(
                    folder,
                    system_override,
                    recursive=True,
                    progress_callback=progress_callback,
                    enable_auto_normalize=enable_auto_normalize,
                    profile_scope=profile_scope,
                )

    # Apply systems filter for device types whose scan functions don't filter internally
    if systems_config:
        results = [r for r in results if r.system.upper() in systems_config]

    _flush_scan_cache()
    _emit_progress(progress_callback, f"Found {len(results)} local save entries.", len(results), len(results))
    return results


def _make_title_id_with_region(system: str, filename: str) -> str:
    """Like make_title_id but always appends the region tag when present.

    Keeps regional saves in separate server slots so a USA save never
    overwrites a Japan save on sync.
      "Super Mario World (USA).srm"    -> SNES_super_mario_world_usa
      "Super Mario World (Japan).srm"  -> SNES_super_mario_world_japan
      "Yu Yu Hakusho (USA, Europe).sav" -> GBA_yu_yu_hakusho_usa_europe
      "Super Mario World.srm"          -> SNES_super_mario_world
    """
    regions = _extract_regions(Path(filename).stem)
    base = make_title_id(system, filename)  # region already stripped inside
    return f"{base}_{'_'.join(regions)}" if regions else base


def _build_save_file(
    system: str,
    game_name: str,
    source_name: str,
    path: Optional[Path],
    file_hash: str,
    mtime: float,
    save_exists: bool,
    enable_auto_normalize: bool,
    match_name: str | None = None,
    profile_scope: str = "",
) -> SaveFile:
    legacy_title_id = _make_title_id_with_region(system, source_name)
    canonical_name = None
    canonical_title_id = ""
    source = "legacy"
    confidence = "legacy"

    if enable_auto_normalize and path is not None:
        canonical_name, source, confidence = _resolve_canonical_sync_name(
            system, path, match_name=match_name, profile_scope=profile_scope
        )
        if canonical_name:
            canonical_title_id = _make_title_id_with_region(system, canonical_name)

    effective_title_id = canonical_title_id or legacy_title_id
    if not canonical_title_id:
        source = "legacy"
        confidence = "legacy"

    return SaveFile(
        title_id=effective_title_id,
        path=path,
        hash=file_hash,
        mtime=mtime,
        system=system,
        game_name=game_name,
        save_exists=save_exists,
        legacy_title_id=legacy_title_id,
        canonical_title_id=canonical_title_id,
        title_id_source=source,
        title_id_confidence=confidence,
        profile_scope=profile_scope,
    )


def _slot_mapping_key(save: SaveFile) -> str | None:
    if save.path is None:
        return None
    try:
        resolved_path = str(save.path.resolve())
    except OSError:
        resolved_path = str(save.path)
    return f"{save.profile_scope}|{resolved_path}"


def _get_slot_mapping(save: SaveFile) -> dict[str, str] | None:
    key = _slot_mapping_key(save)
    if not key:
        return None
    return _load_slot_mappings().get(key)


def _set_slot_mapping(save: SaveFile, effective_title_id: str) -> None:
    key = _slot_mapping_key(save)
    if not key:
        return
    mappings = _load_slot_mappings()
    mappings[key] = {
        "effective_title_id": effective_title_id,
        "legacy_title_id": save.legacy_title_id or save.title_id,
        "canonical_title_id": save.canonical_title_id,
    }
    _mark_slot_mappings_dirty()


def _resolve_effective_title_id(save: SaveFile, server_titles: dict[str, dict]) -> tuple[str, str, str | None]:
    legacy = save.legacy_title_id or save.title_id
    canonical = save.canonical_title_id or ""
    if not canonical or canonical == legacy:
        return legacy, "legacy", None

    legacy_exists = legacy in server_titles
    canonical_exists = canonical in server_titles

    mapped = _get_slot_mapping(save)
    if mapped:
        mapped_id = mapped.get("effective_title_id", "")
        if mapped_id == canonical:
            return mapped_id, "mapped", None
        if mapped_id == legacy and legacy_exists:
            return mapped_id, "mapped", None

    if legacy_exists and not canonical_exists:
        return legacy, "legacy_server", None
    if canonical_exists and not legacy_exists:
        return canonical, "canonical_server", None
    if legacy_exists and canonical_exists:
        return legacy, "ambiguous", f"Both legacy and canonical server slots already exist: {legacy} and {canonical}"

    return canonical, f"canonical_{save.title_id_source}", None


def _scan_flat(
    folder: Path,
    system: str,
    recursive: bool = False,
    progress_callback=None,
    enable_auto_normalize: bool = True,
    profile_scope: str = "",
) -> list[SaveFile]:
    """Scan a folder of saves for a single system."""
    results = []
    candidates = sorted(folder.rglob("*") if recursive else folder.iterdir())
    total = len(candidates)
    for idx, f in enumerate(candidates, start=1):
        if f.is_file() and f.suffix.lower() in SAVE_EXTENSIONS:
            file_hash = _hash_file(f)
            sf = _build_save_file(
                system=system,
                game_name=f.stem,
                source_name=f.name,
                path=f,
                file_hash=file_hash,
                mtime=f.stat().st_mtime,
                save_exists=True,
                enable_auto_normalize=enable_auto_normalize,
                profile_scope=profile_scope,
            )
            results.append(sf)
        if idx == 1 or idx % 25 == 0 or idx == total:
            _emit_progress(progress_callback, f"Scanning {system} files… {idx}/{total}", idx, total)
    return results


def _dedup_saves(saves: list[SaveFile]) -> list[SaveFile]:
    """Deduplicate SaveFile list by title_id.

    Multiple ROM files (different dumps/revisions) can normalize to the same
    title_id.  We keep one entry per title_id, preferring:
      1. An entry whose local save file already exists (save_exists=True)
      2. Among ties, the first one encountered (usually alphabetically first)
    """
    seen: dict[str, SaveFile] = {}
    for sf in saves:
        existing = seen.get(sf.title_id)
        if existing is None:
            seen[sf.title_id] = sf
        elif sf.save_exists and not existing.save_exists:
            # Prefer the ROM that actually has a save — keeps the correct path/hash
            if existing.path and existing.path != sf.path and existing.path not in sf.alternate_paths:
                sf.alternate_paths.append(existing.path)
            for alt in existing.alternate_paths:
                if alt != sf.path and alt not in sf.alternate_paths:
                    sf.alternate_paths.append(alt)
            seen[sf.title_id] = sf
        else:
            candidate_paths: list[Path] = []
            if sf.path is not None:
                candidate_paths.append(sf.path)
            candidate_paths.extend(sf.alternate_paths)
            for candidate in candidate_paths:
                if candidate != existing.path and candidate not in existing.alternate_paths:
                    existing.alternate_paths.append(candidate)
    return list(seen.values())


def _scan_roms_match_saves(
    rom_folder: Path,
    save_folder: Path,
    system: str,
    save_ext: str = ".sav",
    recursive: bool = True,
    progress_callback=None,
    enable_auto_normalize: bool = True,
    mirror_relative_path: bool = False,
    profile_scope: str = "",
) -> list[SaveFile]:
    """Scan ROMs in rom_folder and find/expect saves in save_folder.

    For each ROM found, the expected save path is:
        save_folder / <rom_stem><save_ext>

    This is the correct approach for devices like Everdrive and Generic profiles
    where ROMs and saves live in separate folder trees.  Only games whose ROM is
    physically present are returned (save_exists=False means no save yet).

    game_name is set to the original ROM stem (preserving punctuation, region
    tags, etc.) so the display name and save filename always match the ROM.
    """
    # Build a save lookup index. Generic/Everdrive use a flat stem index, while
    # mirrored layouts like Pocket single-system sub-roots preserve relative paths.
    save_index: dict[object, Path] = {}
    if save_folder.exists():
        save_candidates = sorted(save_folder.rglob("*"))
        save_total = len(save_candidates)
        for idx, f in enumerate(save_candidates, start=1):
            if not f.is_file():
                continue
            ext = f.suffix.lower()
            if mirror_relative_path:
                try:
                    rel_key = f.relative_to(save_folder).with_suffix("").as_posix().lower()
                except ValueError:
                    rel_key = f.stem.lower()
                if ext == save_ext.lower() or (
                    ext in SAVE_EXTENSIONS and rel_key not in save_index
                ):
                    save_index[rel_key] = f
            else:
                if ext == save_ext.lower():
                    save_index[f.stem.lower()] = f          # exact extension wins
                elif ext in SAVE_EXTENSIONS and f.stem.lower() not in save_index:
                    save_index[f.stem.lower()] = f          # fallback if no exact match yet
            if idx == 1 or idx % 100 == 0 or idx == save_total:
                _emit_progress(progress_callback, f"Indexing {system} save files… {idx}/{save_total}", idx, save_total)

    results: list[SaveFile] = []
    candidates = sorted(rom_folder.rglob("*") if recursive else rom_folder.iterdir())
    total = len(candidates)
    for idx, rom_file in enumerate(candidates, start=1):
        if not rom_file.is_file():
            continue
        if rom_file.suffix.lower() not in ROM_EXTENSIONS and rom_file.suffix.lower() not in ZIP_ROM_EXTENSIONS:
            continue
        if rom_file.name.startswith("."):
            continue

        # Look up save by exact relative path for mirrored layouts, else by flat stem.
        rel_parent = Path()
        try:
            rel_parent = rom_file.parent.relative_to(rom_folder)
        except ValueError:
            rel_parent = Path()
        if mirror_relative_path:
            rel_key = (rel_parent / rom_file.stem).as_posix().lower()
            save_path = save_index.get(rel_key)
        else:
            save_path = save_index.get(rom_file.stem.lower())
        if save_path is None:
            save_path = save_folder / rel_parent / (rom_file.stem + save_ext) if mirror_relative_path else save_folder / (rom_file.stem + save_ext)
            file_hash = ""
            mtime = 0.0
            save_exists = False
        else:
            file_hash = _hash_file(save_path)
            mtime = save_path.stat().st_mtime
            save_exists = True

        sf = _build_save_file(
            system=system,
            game_name=rom_file.stem,
            source_name=rom_file.name,
            path=rom_file,
            file_hash=file_hash,
            mtime=mtime,
            save_exists=save_exists,
            enable_auto_normalize=enable_auto_normalize,
            profile_scope=profile_scope,
        )
        sf.path = save_path
        sf.hash = file_hash
        sf.mtime = mtime
        sf.save_exists = save_exists
        results.append(sf)
        if idx == 1 or idx % 25 == 0 or idx == total:
            _emit_progress(progress_callback, f"Scanning {system} ROMs… {idx}/{total}", idx, total)
    return _dedup_saves(results)


def _scan_retroarch(root: Path, progress_callback=None, enable_auto_normalize: bool = True, profile_scope: str = "") -> list[SaveFile]:
    """Scan RetroArch saves/CoreName/game.srm structure."""
    results = []
    for core_dir in sorted(root.iterdir()):
        if not core_dir.is_dir():
            continue
        system = RETROARCH_CORE_MAP.get(core_dir.name)
        if not system:
            continue
        results.extend(_scan_flat(
            core_dir,
            system,
            progress_callback=progress_callback,
            enable_auto_normalize=enable_auto_normalize,
            profile_scope=profile_scope,
        ))
    return results


def _scan_mega_everdrive(
    gamedata_folder: Path,
    system: str,
    progress_callback=None,
    enable_auto_normalize: bool = True,
    profile_scope: str = "",
) -> list[SaveFile]:
    """Scan MEGA EverDrive Pro gamedata/ structure.

    Structure:
        gamedata/<Game Name (Region)>/bram.srm   ← save file (sync this)
        gamedata/<Game Name (Region)>/*.sav       ← save states (ignore)

    Each subfolder is named after the ROM.  Only subfolders that contain
    bram.srm are returned; folders without it have no battery save to sync.
    """
    results = []
    candidates = sorted(gamedata_folder.iterdir())
    total = len(candidates)
    for idx, game_dir in enumerate(candidates, start=1):
        if not game_dir.is_dir():
            continue
        save_file = game_dir / "bram.srm"
        if not save_file.exists():
            continue
        results.append(_build_save_file(
            system=system,
            game_name=game_dir.name,
            source_name=game_dir.name,
            path=save_file,
            file_hash=_hash_file(save_file),
            mtime=save_file.stat().st_mtime,
            save_exists=True,
            enable_auto_normalize=enable_auto_normalize,
            match_name=game_dir.name,
            profile_scope=profile_scope,
        ))
        if idx == 1 or idx % 25 == 0 or idx == total:
            _emit_progress(progress_callback, f"Scanning {system} EverDrive folders… {idx}/{total}", idx, total)
    return _dedup_saves(results)


def _scan_mister(root: Path, progress_callback=None, enable_auto_normalize: bool = True, profile_scope: str = "") -> list[SaveFile]:
    """Scan MiSTer saves/<System>/ structure."""
    results = []
    for sys_dir in sorted(root.iterdir()):
        if not sys_dir.is_dir():
            continue
        system = MISTER_FOLDER_MAP.get(sys_dir.name)
        if not system:
            continue
        results.extend(_scan_flat(
            sys_dir,
            system,
            progress_callback=progress_callback,
            enable_auto_normalize=enable_auto_normalize,
            profile_scope=profile_scope,
        ))
    return results


def _scan_pocket(root: Path, progress_callback=None, enable_auto_normalize: bool = True, profile_scope: str = "") -> list[SaveFile]:
    """Scan Analogue Pocket Memories/<Platform>/ structure."""
    results = []
    for plat_dir in sorted(root.iterdir()):
        if not plat_dir.is_dir():
            continue
        system = POCKET_FOLDER_MAP.get(plat_dir.name)
        if not system:
            continue
        results.extend(_scan_flat(
            plat_dir,
            system,
            progress_callback=progress_callback,
            enable_auto_normalize=enable_auto_normalize,
            profile_scope=profile_scope,
        ))
    return results


def _scan_pocket_openfpga(
    saves_root: Path,
    progress_callback=None,
    enable_auto_normalize: bool = True,
    profile_scope: str = "",
) -> list[SaveFile]:
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
        results.extend(_scan_flat(
            sys_dir,
            system,
            recursive=True,
            progress_callback=progress_callback,
            enable_auto_normalize=enable_auto_normalize,
            profile_scope=profile_scope,
        ))
    return results


def _scan_pocket_openfpga_from_roms(
    assets_root: Path,
    saves_root: Path,
    save_ext: str = ".sav",
    progress_callback=None,
    enable_auto_normalize: bool = True,
    profile_scope: str = "",
) -> list[SaveFile]:
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
        candidates = sorted(sys_dir.rglob("*"))
        total = len(candidates)
        for idx, rom_file in enumerate(candidates, start=1):
            if not rom_file.is_file():
                continue
            if rom_file.suffix.lower() not in ROM_EXTENSIONS and rom_file.suffix.lower() not in ZIP_ROM_EXTENSIONS:
                continue
            if rom_file.name.startswith("."):
                continue
            try:
                rel = rom_file.relative_to(sys_dir)
            except ValueError:
                continue
            # Mirror: saves_root/<sys>/<same subpath>/<rom_stem><save_ext>
            save_path = saves_root / sys_folder_name / rel.parent / (rom_file.stem + save_ext)
            if save_path.exists():
                file_hash = _hash_file(save_path)
                mtime = save_path.stat().st_mtime
                save_exists = True
            else:
                file_hash = ""
                mtime = 0.0
                save_exists = False
            sf = _build_save_file(
                system=system,
                game_name=rom_file.stem,
                source_name=rom_file.name,
                path=rom_file,
                file_hash=file_hash,
                mtime=mtime,
                save_exists=save_exists,
                enable_auto_normalize=enable_auto_normalize,
                profile_scope=profile_scope,
            )
            sf.path = save_path
            sf.hash = file_hash
            sf.mtime = mtime
            sf.save_exists = save_exists
            results.append(sf)
            if idx == 1 or idx % 25 == 0 or idx == total:
                _emit_progress(progress_callback, f"Scanning {system} Assets… {idx}/{total}", idx, total)
    return _dedup_saves(results)


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

# PS1 retail disc product code prefixes (physical/PSN discs, not PSP games).
# Used to classify PSone Classics inside PSP/PPSSPP SAVEDATA correctly as "PSX".
_PSX_RETAIL_PREFIXES: frozenset[str] = frozenset({
    # North America
    "SLUS", "SCUS", "PAPX",
    # Europe
    "SLES", "SCES", "SCED",
    # Japan
    "SLPS", "SLPM", "SCPS", "SCPM",
    # Other
    "SLAJ", "SLEJ", "SCAJ",
})

_PS1_SERIAL_RE = re.compile(r"^([A-Z]{4})(\d{5,})$")


def _normalize_ps1_serial(stem: str) -> str | None:
    """Normalize a PS1 memory-card filename stem to a bare product code.

    Examples: "SLUS-01234" → "SLUS01234", "SCUS_94163" → "SCUS94163".
    Returns None if the result doesn't look like a PS1 product code
    (4 uppercase letters followed by 5+ digits).
    """
    code = re.sub(r"[^A-Z0-9]", "", stem.upper())
    return code if _PS1_SERIAL_RE.match(code) else None


# MemCard Pro: known shared/global card names that hold all games (skip during per-title scan)
_MCD_SHARED_NAMES: frozenset[str] = frozenset({
    "shared_card_1", "shared_card_2", "shared_card_3", "shared_card_4",
    "mcd001", "mcd002", "mcd003", "mcd004",
    "epsxe000", "epsxe001",
    "memorycard", "memory card",
})


def _emit_progress(callback, message: str, current: int | None = None, total: int | None = None) -> None:
    if callback is None:
        return
    try:
        callback(message, current, total)
    except TypeError:
        callback(message)


def _iter_zip_rom_infos(path: Path) -> list[zipfile.ZipInfo]:
    try:
        with zipfile.ZipFile(path) as zf:
            return sorted(
                [
                    info for info in zf.infolist()
                    if not info.is_dir() and Path(info.filename).suffix.lower() in ROM_EXTENSIONS
                ],
                key=lambda info: info.filename.lower(),
            )
    except (OSError, zipfile.BadZipFile):
        return []


def _read_zip_member_header_title(path: Path, info: zipfile.ZipInfo, system: str) -> str | None:
    system = system.upper()
    max_len = 0x80000 if system in ("PSP", "PS3") else 0x10200
    try:
        with zipfile.ZipFile(path) as zf:
            with zf.open(info) as member:
                data = member.read(max_len)
    except (OSError, zipfile.BadZipFile, KeyError):
        return None

    file_size = info.file_size
    title_bytes: bytes | None = None
    if system == "GBA" and len(data) >= 0x00AC:
        title_bytes = data[0x00A0:0x00AC]
    elif system in ("MD", "GEN") and len(data) >= 0x0150:
        title_bytes = data[0x0120:0x0150]
    elif system == "N64" and len(data) >= 0x0034:
        title_bytes = data[0x0020:0x0034]
    elif system in ("GB", "GBC") and len(data) >= 0x0144:
        title_bytes = data[0x0134:0x0144]
    elif system == "SNES":
        offset = 512 if file_size % 1024 == 512 else 0
        data = data[offset:]
        candidates = []
        for addr in (0x7FC0, 0xFFC0):
            if len(data) >= addr + 21:
                chunk = data[addr:addr + 21]
                printable = sum(1 for b in chunk if 0x20 <= b <= 0x7E)
                candidates.append((printable, chunk))
        if candidates:
            title_bytes = max(candidates, key=lambda x: x[0])[1]

    if title_bytes is None:
        return None
    title = title_bytes.decode("ascii", errors="ignore")
    title = re.sub(r"[^\x20-\x7E]", " ", title).strip()
    title = re.sub(r"\s+", " ", title).strip()
    return title if len(title) >= 2 else None


# ---------------------------------------------------------------------------
# No-Intro-aware title resolution for sync scanning
# ---------------------------------------------------------------------------

_NOINTRO_CACHE: dict[str, dict[str, object]] = {}
_CACHE_MISS = object()


def _get_nointro_cache(system: str) -> dict[str, object]:
    """Load and cache the DAT + derived indexes for a system, if available."""
    system = system.upper().strip()
    cached = _NOINTRO_CACHE.get(system)
    if cached is not None:
        return cached

    try:
        import rom_normalizer as rn
    except Exception:
        cached = {"no_intro": {}, "name_index": {}}
        _NOINTRO_CACHE[system] = cached
        return cached

    dat_path = rn.find_dat_for_system(system)
    if dat_path is None:
        cached = {"no_intro": {}, "name_index": {}, "cache_tag": f"{system}:none"}
    else:
        no_intro = rn.load_no_intro_dat(dat_path)
        try:
            dat_stat = dat_path.stat()
            dat_sig = f"{dat_path.name}:{dat_stat.st_mtime_ns}:{dat_stat.st_size}"
        except OSError:
            dat_sig = dat_path.name
        cached = {
            "no_intro": no_intro,
            "name_index": rn.build_name_index(no_intro) if no_intro else {},
            "cache_tag": f"{system}:{dat_sig}",
        }
    _NOINTRO_CACHE[system] = cached
    return cached


def _scan_cache_key(profile_scope: str, system: str, path: Path, match_name: str | None) -> str:
    try:
        canonical_path = str(path.resolve())
    except OSError:
        canonical_path = str(path)
    return f"{profile_scope}|{system.upper()}|{canonical_path}|{match_name or ''}"


def _get_cached_canonical_name(profile_scope: str, system: str, path: Path, match_name: str | None, cache_tag: str) -> tuple[str | None, str, str] | object:
    cache = _load_scan_cache()
    key = _scan_cache_key(profile_scope, system, path, match_name)
    entry = cache.get(key)
    if not isinstance(entry, dict):
        return _CACHE_MISS
    try:
        stat = path.stat()
    except OSError:
        return _CACHE_MISS
    if entry.get("mtime_ns") != stat.st_mtime_ns:
        return _CACHE_MISS
    if entry.get("size") != stat.st_size:
        return _CACHE_MISS
    if entry.get("cache_tag") != cache_tag:
        return _CACHE_MISS
    return (
        entry.get("canonical_name") or None,
        str(entry.get("source") or "legacy"),
        str(entry.get("confidence") or "legacy"),
    )


def _set_cached_canonical_name(
    profile_scope: str,
    system: str,
    path: Path,
    match_name: str | None,
    cache_tag: str,
    canonical_name: str | None,
    source: str,
    confidence: str,
) -> None:
    cache = _load_scan_cache()
    try:
        stat = path.stat()
    except OSError:
        return
    key = _scan_cache_key(profile_scope, system, path, match_name)
    cache[key] = {
        "mtime_ns": stat.st_mtime_ns,
        "size": stat.st_size,
        "cache_tag": cache_tag,
        "canonical_name": canonical_name or "",
        "source": source,
        "confidence": confidence,
    }
    _mark_scan_cache_dirty()


def _resolve_canonical_sync_name(system: str, path: Path, match_name: str | None = None, profile_scope: str = "") -> tuple[str | None, str, str]:
    """Return canonical name plus match source/confidence for sync-time title mapping.

    This mirrors the ROM Normalizer matching pipeline, but does not rename any
    local files. The resolved canonical name is used only to decide which
    server slot the save belongs to.
    """
    try:
        import rom_normalizer as rn
    except Exception:
        return None, "legacy", "legacy"

    cache = _get_nointro_cache(system)
    no_intro = cache.get("no_intro", {})
    name_index = cache.get("name_index", {})
    if not no_intro or not name_index:
        return None, "legacy", "legacy"
    cache_tag = str(cache.get("cache_tag", f"{system}:none"))

    lookup_name = match_name or path.name
    cached = _get_cached_canonical_name(profile_scope, system, path, match_name, cache_tag)
    if cached is not _CACHE_MISS:
        return cached

    canonical: str | None = None
    source = "legacy"
    confidence = "legacy"
    suffix = path.suffix.lower()

    # 1. Exact ROM CRC32 match
    if suffix in ROM_EXTENSIONS:
        try:
            crc = rn._crc32_file(path)
        except Exception:
            crc = ""
        if crc:
            canonical = no_intro.get(crc)
            if canonical:
                source, confidence = "crc", "high"
                _set_cached_canonical_name(profile_scope, system, path, match_name, cache_tag, canonical, source, confidence)
                return canonical, source, confidence
    elif suffix in ZIP_ROM_EXTENSIONS:
        infos = _iter_zip_rom_infos(path)
        for info in infos:
            crc = f"{info.CRC & 0xFFFFFFFF:08X}"
            canonical = no_intro.get(crc)
            if canonical:
                source, confidence = "crc", "high"
                _set_cached_canonical_name(profile_scope, system, path, match_name, cache_tag, canonical, source, confidence)
                return canonical, source, confidence

    # 2. Fuzzy filename lookup
    canonical = rn.fuzzy_filename_search(lookup_name, name_index)
    if canonical:
        region_hint = (
            rn.extract_region_hint(lookup_name)
            or rn.extract_region_hint(path.parent.name)
        )
        if region_hint:
            canonical = rn.find_region_preferred(canonical, no_intro, region_hint)
        source, confidence = "fuzzy", "low"
        _set_cached_canonical_name(profile_scope, system, path, match_name, cache_tag, canonical, source, confidence)
        return canonical, source, confidence
    if suffix in ZIP_ROM_EXTENSIONS:
        infos = _iter_zip_rom_infos(path)
        for info in infos:
            member_path = Path(info.filename)
            canonical = rn.fuzzy_filename_search(member_path.name, name_index)
            if canonical:
                region_hint = (
                    rn.extract_region_hint(member_path.name)
                    or rn.extract_region_hint(member_path.parent.name)
                    or rn.extract_region_hint(path.name)
                    or rn.extract_region_hint(path.parent.name)
                )
                if region_hint:
                    canonical = rn.find_region_preferred(canonical, no_intro, region_hint)
                source, confidence = "fuzzy", "low"
                _set_cached_canonical_name(profile_scope, system, path, match_name, cache_tag, canonical, source, confidence)
                return canonical, source, confidence

    # 3. ROM header title lookup
    if suffix in ROM_EXTENSIONS:
        header_title = rn.read_rom_header_title(path, system)
        if header_title:
            canonical = rn.lookup_header_in_index(header_title, name_index)
            if canonical:
                region_hint = (
                    rn.extract_region_hint(path.name)
                    or rn.extract_region_hint(path.parent.name)
                )
                if region_hint:
                    canonical = rn.find_region_preferred(canonical, no_intro, region_hint)
                source, confidence = "header", "high"
                _set_cached_canonical_name(profile_scope, system, path, match_name, cache_tag, canonical, source, confidence)
                return canonical, source, confidence
    elif suffix in ZIP_ROM_EXTENSIONS:
        infos = _iter_zip_rom_infos(path)
        for info in infos:
            header_title = _read_zip_member_header_title(path, info, system)
            if not header_title:
                continue
            canonical = rn.lookup_header_in_index(header_title, name_index)
            if canonical:
                member_path = Path(info.filename)
                region_hint = (
                    rn.extract_region_hint(member_path.name)
                    or rn.extract_region_hint(member_path.parent.name)
                    or rn.extract_region_hint(path.name)
                    or rn.extract_region_hint(path.parent.name)
                )
                if region_hint:
                    canonical = rn.find_region_preferred(canonical, no_intro, region_hint)
                source, confidence = "header", "high"
                _set_cached_canonical_name(profile_scope, system, path, match_name, cache_tag, canonical, source, confidence)
                return canonical, source, confidence

    # 4. Parent-folder name lookup for shorthand ROM names / packs
    if path.parent.name:
        canonical = rn.fuzzy_filename_search(path.parent.name, name_index)
        if canonical:
            region_hint = (
                rn.extract_region_hint(lookup_name)
                or rn.extract_region_hint(path.parent.name)
            )
            if region_hint:
                canonical = rn.find_region_preferred(canonical, no_intro, region_hint)
            source, confidence = "folder", "low"
            _set_cached_canonical_name(profile_scope, system, path, match_name, cache_tag, canonical, source, confidence)
            return canonical, source, confidence

    _set_cached_canonical_name(profile_scope, system, path, match_name, cache_tag, None, source, confidence)
    return None, source, confidence


def _make_sync_title_id(system: str, source_name: str, canonical_name: str | None = None) -> str:
    """Build the server title ID, preferring a canonical No-Intro name when found."""
    return _make_title_id_with_region(system, canonical_name or source_name)


def _scan_emudeck(root: Path, progress_callback=None, profile_scope: str = "") -> list[SaveFile]:
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
            # For PS1, use the normalized product code directly (e.g. SLUS01234)
            # so saves match across DuckStation, MemCard Pro, and PSone Classics.
            serial = _normalize_ps1_serial(stem) if system == "PS1" else None
            title_id = serial if serial else make_title_id(system, display_name)
            file_hash = _hash_file(f)
            slug = title_id.split("_", 1)[1] if "_" in title_id else f.stem
            results.append(SaveFile(
                title_id=title_id,
                path=f,
                hash=file_hash,
                mtime=f.stat().st_mtime,
                system=system,
                game_name=slug_to_display_name(slug),
                profile_scope=profile_scope,
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
            # Classify PSone Classics (PSX retail prefixes) separately from PSP games
            system = "PS1" if product_code[:4] in _PSX_RETAIL_PREFIXES else "PSP"
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
                        system=system,
                        game_name=product_code,
                        profile_scope=profile_scope,
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
                        profile_scope=profile_scope,
                    )
        results.extend(ps3_best.values())

    return results


def _scan_memcard_pro(
    root: Path,
    progress_callback=None,
    profile_scope: str = "",
) -> list[SaveFile]:
    """Scan a MemCard Pro SD card for PS1 per-game memory cards.

    Supports two directory layouts:

    Hierarchical (MemCard PRO firmware default):
        <root>/VIRTUAL MEMORY CARDS/<SERIAL>/MemoryCard.mcd
        e.g.  VIRTUAL MEMORY CARDS/SLUS-01234/MemoryCard.mcd

    Flat (some tools export as):
        <root>/<SERIAL>.mcd  or  <root>/<SERIAL>.mcr

    The game serial (e.g. ``SLUS-01234``) is used directly as the title ID
    (``SLUS01234``) so it matches PSone Classics on PSP/Vita.  Shared/global
    memory card names (``shared_card_1``, ``Mcd001``, etc.) are skipped.
    """
    results: list[SaveFile] = []
    mcd_exts = {".mcd", ".mcr"}

    # Hierarchical layout: VIRTUAL MEMORY CARDS/<SERIAL>/MemoryCard.mcd (or any *.mcd)
    vmc_dir = root / "VIRTUAL MEMORY CARDS"
    if vmc_dir.is_dir():
        for serial_dir in sorted(vmc_dir.iterdir()):
            if not serial_dir.is_dir():
                continue
            # Pick the first .mcd/.mcr inside (typically MemoryCard.mcd)
            mcd_files = [f for f in sorted(serial_dir.iterdir())
                         if f.is_file() and f.suffix.lower() in mcd_exts]
            if not mcd_files:
                continue
            mcd_file = max(mcd_files, key=lambda f: f.stat().st_mtime)
            serial = _normalize_ps1_serial(serial_dir.name)
            title_id = serial if serial else make_title_id("PS1", serial_dir.name)
            results.append(SaveFile(
                title_id=title_id,
                path=mcd_file,
                hash=_hash_file(mcd_file),
                mtime=mcd_file.stat().st_mtime,
                system="PS1",
                game_name=serial_dir.name,
                profile_scope=profile_scope,
            ))

    # Flat layout: <root>/<SERIAL>.mcd (or .mcr)
    flat_seen: set[str] = set()
    for mcd_file in sorted(root.iterdir()):
        if not mcd_file.is_file() or mcd_file.suffix.lower() not in mcd_exts:
            continue
        stem = _MCD_SLOT_RE.sub("", mcd_file.stem)
        if stem.lower() in _MCD_SHARED_NAMES:
            continue
        serial = _normalize_ps1_serial(stem)
        title_id = serial if serial else make_title_id("PS1", stem)
        if title_id in flat_seen:
            continue
        flat_seen.add(title_id)
        results.append(SaveFile(
            title_id=title_id,
            path=mcd_file,
            hash=_hash_file(mcd_file),
            mtime=mcd_file.stat().st_mtime,
            system="PS1",
            game_name=stem,
            profile_scope=profile_scope,
        ))

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
    progress_callback=None,
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
    server_titles: dict[str, dict] = {}
    server_loaded = False

    _emit_progress(progress_callback, "Loading server save index…", 0, max(len(saves), 1))
    try:
        resp = requests.get(
            f"{base_url}/api/v1/titles",
            headers=headers,
            timeout=timeout,
        )
        resp.raise_for_status()
        body = resp.json()
        titles_list = body if isinstance(body, list) else body.get("titles", [])
        server_titles = {
            title.get("title_id", ""): title
            for title in titles_list
            if title.get("title_id")
        }
        server_loaded = True
    except requests.RequestException:
        server_titles = {}

    total = len(saves)
    for idx, save in enumerate(saves, start=1):
        effective_title_id, resolution_source, mapping_note = _resolve_effective_title_id(save, server_titles)
        save.title_id = effective_title_id
        seen_title_ids.add(save.title_id)
        if save.legacy_title_id:
            seen_title_ids.add(save.legacy_title_id)
        if save.canonical_title_id:
            seen_title_ids.add(save.canonical_title_id)
        last_synced = state.get(save.title_id)
        meta = server_titles.get(save.title_id)

        if resolution_source != "ambiguous":
            _set_slot_mapping(save, save.title_id)

        if resolution_source == "ambiguous":
            results.append(SyncStatus(
                save=save,
                last_synced_hash=last_synced,
                status="mapping_conflict",
                mapping_note=mapping_note or "",
            ))
            if idx == 1 or idx % 25 == 0 or idx == total:
                _emit_progress(progress_callback, f"Comparing with server… {idx}/{total}", idx, total)
            continue

        if meta is None:
            duplicate_conflict, duplicate_note = _detect_duplicate_local_conflict(save)
            if duplicate_conflict:
                results.append(SyncStatus(
                    save=save,
                    last_synced_hash=last_synced,
                    status="local_duplicate_conflict",
                    mapping_note=duplicate_note,
                ))
                if idx == 1 or idx % 25 == 0 or idx == total:
                    _emit_progress(progress_callback, f"Comparing with server… {idx}/{total}", idx, total)
                continue
            if server_loaded:
                if not save.save_exists:
                    # ROM present but no local save and nothing on server — nothing to do
                    pass
                else:
                    results.append(SyncStatus(
                        save=save,
                        last_synced_hash=last_synced,
                        status="not_on_server",
                        mapping_note=mapping_note or f"Using {resolution_source}: {save.title_id}",
                    ))
            elif save.save_exists:
                results.append(SyncStatus(
                    save=save,
                    status="error",
                    mapping_note=mapping_note or f"Using {resolution_source}: {save.title_id}",
                ))
            if idx == 1 or idx % 25 == 0 or idx == total:
                _emit_progress(progress_callback, f"Comparing with server… {idx}/{total}", idx, total)
            continue

        server_hash = meta.get("save_hash", "")
        server_ts = meta.get("server_timestamp", "")
        server_name = meta.get("name", "") or meta.get("game_name", "")
        duplicate_conflict, duplicate_note = _detect_duplicate_local_conflict(save)

        if not save.save_exists:
            # ROM present, no local save, server has a save — always offer download
            status = "server_newer"
        elif duplicate_conflict:
            status = "local_duplicate_conflict"
        else:
            status = _determine_status(save.hash, server_hash, last_synced)
        results.append(SyncStatus(
            save=save,
            server_hash=server_hash,
            server_timestamp=server_ts,
            server_name=server_name,
            last_synced_hash=last_synced,
            status=status,
            mapping_note=duplicate_note or mapping_note or f"Using {resolution_source}: {save.title_id}",
        ))
        if idx == 1 or idx % 25 == 0 or idx == total:
            _emit_progress(progress_callback, f"Comparing with server… {idx}/{total}", idx, total)

    # Fetch server-only titles (exist on server but not found in any local profile)
    for title in server_titles.values():
        tid = title.get("title_id", "")
        if not tid or tid in seen_title_ids:
            continue
        system = title.get("system") or title.get("platform", "")
        if systems_filter and system.upper() not in systems_filter:
            continue
        name = title.get("name") or title.get("game_name") or tid
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

    _flush_slot_mappings()
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
