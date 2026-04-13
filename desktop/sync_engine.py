"""Sync engine for ROM-based save syncing (RetroArch, MiSTer, Analogue Pocket, etc.).

Standalone module — does not import from the server codebase.
Uses dedicated server save endpoints when a platform needs format-aware
conversion (for example PS1/PS2 memory cards), and `/raw` for simpler systems.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import time
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests

# ---------------------------------------------------------------------------
# ROM name normalization (mirrors server/app/services/rom_id.py)
# ---------------------------------------------------------------------------

SYSTEM_CODES = frozenset(
    {
        "GBA",
        "SNES",
        "NES",
        "MD",
        "N64",
        "GB",
        "GBC",
        "GG",
        "NGP",
        "NGPC",
        "PCE",
        "PCSG",
        "PS1",
        "PS2",
        "SMS",
        "A2600",
        "A7800",
        "LYNX",
        "NEOGEO",
        "32X",
        "SEGACD",
        "SAT",
        "TG16",
        "WSWAN",
        "WSWANC",
        "VB",
        "DC",
        "NDS",
        "GC",
        "ARCADE",
        "MAME",
        "CPS1",
        "CPS2",
        "CPS3",
        "FDS",
    }
)

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
    "usa",
    "europe",
    "japan",
    "world",
    "germany",
    "france",
    "italy",
    "spain",
    "australia",
    "brazil",
    "korea",
    "china",
    "netherlands",
    "sweden",
    "denmark",
    "norway",
    "finland",
    "asia",
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
    """Strip extension and revision/disc tags; append region to the slug.

    Mirrors server/app/services/rom_id.py — must stay in sync.

    Examples:
        "Super Mario World (USA).sfc"            -> "super_mario_world_usa"
        "Sonic the Hedgehog (USA, Europe).md"    -> "sonic_the_hedgehog_usa_europe"
        "Final Fantasy VII (Rev 1) (USA).bin"    -> "final_fantasy_vii_usa"
        "Homebrew Game.sfc"                      -> "homebrew_game"
    """
    name = filename
    for _ in range(3):
        dot_idx = name.rfind(".")
        if dot_idx <= 0:
            break
        suffix = name[dot_idx + 1 :]
        if 1 <= len(suffix) <= 5 and suffix.isalnum():
            name = name[:dot_idx]
        else:
            break

    # Extract region before stripping all parenthetical tags
    region_match = _REGION_RE.search(name)
    region_parts = ""
    if region_match:
        region_text = region_match.group(0).strip(" ()")
        region_parts = "_".join(region_text.lower().replace(",", " ").split())

    name = _REV_RE.sub("", name)
    name = _DISC_RE.sub("", name)
    name = _EXTRA_RE.sub("", name)
    name = name.lower()
    name = _NON_ALNUM_RE.sub("_", name)
    name = _MULTI_UNDERSCORE_RE.sub("_", name).strip("_")

    if region_parts:
        name = f"{name}_{region_parts}"

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
    "Beetle Saturn": "SAT",
    "Kronos": "SAT",
    "YabaSanshiro": "SAT",
    "YabaSanshiro 2": "SAT",
    "SMS Plus GX": "SMS",
    "Stella": "A2600",
    "ProSystem": "A7800",
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
    "Atari2600": "A2600",
    "Atari7800": "A7800",
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
    "NGPC": "NGPC",
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
    "ngpc": "NGPC",
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
SAVE_EXTENSIONS = {
    ".sav",
    ".srm",
    ".bkr",
    ".mcr",
    ".frz",
    ".fs",
    ".mcd",
    ".dsv",
    ".ps2",
    ".mc2",
    ".raw",
}

# CD game image extensions — presence of any of these inside a subfolder marks it as a CD game
CD_ROM_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".cue",
        ".iso",
        ".bin",
        ".img",
        ".mdf",
        ".chd",
    }
)

# ROM file extensions used when scanning ROM folders (Pocket openFPGA etc.)
ROM_EXTENSIONS = {
    ".sfc",
    ".smc",  # SNES
    ".gba",  # GBA
    ".gb",
    ".gbc",  # GB/GBC
    ".nes",  # NES
    ".md",
    ".smd",
    ".gen",  # Genesis/MD
    ".32x",  # 32X
    ".n64",
    ".z64",
    ".v64",  # N64
    ".ndd",  # N64DD
    ".gg",  # Game Gear
    ".sms",  # SMS
    ".vb",  # Virtual Boy
    ".pce",  # PC Engine
    ".lnx",  # Lynx
    ".ws",
    ".wsc",  # WonderSwan
    ".ngp",
    ".ngc",  # NGP
    ".nds",  # NDS
    ".fds",  # Famicom Disk System
    ".qd",   # Famicom Disk System Quick Disk
    ".chd",  # CD-compressed images when scanned as standalone ROM files
}
ZIP_ROM_EXTENSIONS = {".zip"}

SYSTEM_DEFAULT_SAVE_EXTENSIONS = {
    "SAT": ".bkr",
}

_LEGACY_GENERIC_SAVE_EXTENSIONS = {"", ".sav", ".srm"}


def resolve_save_ext(system: str, save_ext: str | None, fallback: str = ".sav") -> str:
    """Normalize a configured save extension for a given system.

    Saturn emulator saves are typically stored as ``.bkr``. Older multi-system
    profiles often inherited generic defaults like ``.sav`` or ``.srm``, so we
    coerce only those legacy generic values to the Saturn-native extension.
    """
    system_code = (system or "").upper().strip()
    ext = (save_ext or fallback or ".sav").strip()
    if not ext.startswith("."):
        ext = "." + ext

    default_ext = SYSTEM_DEFAULT_SAVE_EXTENSIONS.get(system_code)
    if default_ext and ext.lower() in _LEGACY_GENERIC_SAVE_EXTENSIONS:
        return default_ext
    return ext


def _resolve_saroo_native_payload(
    title_id: str, path: Path | None = None
) -> tuple[bytes, float] | None:
    """Return the canonical per-game Saroo payload when available.

    Saroo stores all games inside a shared ``SS_SAVE.BIN`` container, but the
    server should receive the individual mednafen-compatible 32 KB save image
    for the selected title. If a matching ``.bkr`` exists and is newer than the
    container file, prefer that for true bidirectional emulator <-> Saroo sync.
    """
    meta = _SAROO_META.get(title_id) or {}
    if not meta:
        return None

    container_mtime = 0.0
    if path is not None:
        try:
            container_mtime = path.stat().st_mtime
        except OSError:
            container_mtime = 0.0

    bkr_path_str = str(meta.get("bkr_path") or "").strip()
    if bkr_path_str:
        bkr_path = Path(bkr_path_str)
        try:
            if bkr_path.exists():
                bkr_mtime = bkr_path.stat().st_mtime
                if bkr_mtime > container_mtime:
                    return bkr_path.read_bytes(), bkr_mtime
        except OSError:
            pass

    native_bytes = meta.get("native_bytes")
    if isinstance(native_bytes, (bytes, bytearray)) and native_bytes:
        return bytes(native_bytes), container_mtime
    return None

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class SaveFile:
    title_id: str  # e.g. GBA_zelda_the_minish_cap
    path: Optional[
        Path
    ]  # local save path (or expected path); None for server-only saves
    hash: str  # sha256 hex (empty string when no local save exists)
    mtime: float  # modification time (unix timestamp; 0 when no local save)
    system: str  # e.g. "GBA"
    game_name: str  # display name
    save_exists: bool = (
        True  # False when ROM is present but no local save file exists yet
    )
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

# Per-title Saroo metadata populated by _scan_saroo().
# Keys are title_id strings; values are dicts with:
#   game_id:      original Saroo game ID string (16 chars, may have trailing spaces)
#   slot_index:   1-based slot index within SS_SAVE.BIN
#   native_bytes: mednafen-compatible 32KB image (bytes) — the payload to upload
#   bkr_path:     path to mednafen .bkr file, if found (str, may be empty)
_SAROO_META: dict[str, dict] = {}


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
# Filesystem helpers
# ---------------------------------------------------------------------------


def _safe_walk(folder: Path, recursive: bool = True) -> list[Path]:
    """Return a sorted list of Paths under *folder*, skipping unreadable entries.

    Uses os.walk so that a single corrupted directory entry (WinError 1392 etc.)
    only skips that entry instead of crashing the whole scan.
    """
    results: list[Path] = []
    if recursive:
        for dirpath, dirnames, filenames in os.walk(folder, onerror=lambda _: None):
            dp = Path(dirpath)
            for name in filenames:
                results.append(dp / name)
            # also yield subdirectories so callers that check is_dir() still work
            for name in dirnames:
                results.append(dp / name)
    else:
        try:
            for entry in os.scandir(folder):
                results.append(Path(entry.path))
        except OSError:
            pass
    return sorted(results)


# Hash helpers
# ---------------------------------------------------------------------------


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _iter_dir_files(path: Path) -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []
    for fp in sorted(path.rglob("*")):
        if fp.is_file():
            rel = fp.relative_to(path).as_posix()
            files.append((rel, fp))
    return files


def _hash_dir_files(path: Path) -> str:
    """Match the server's multi-file bundle hash: sorted file contents only."""
    h = hashlib.sha256()
    for _, fp in _iter_dir_files(path):
        with open(fp, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    return h.hexdigest()


def _hash_ps3_dir_files(path: Path) -> str:
    """PS3 emulator hash that ignores disposable PS3 metadata/media files."""
    h = hashlib.sha256()
    for rel_path, fp in _iter_dir_files(path):
        name = Path(rel_path).name.upper()
        if name in {"PARAM.SFO", "PARAM.PFD"} or Path(rel_path).suffix.upper() == ".PNG":
            continue
        with open(fp, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    return h.hexdigest()


def _hash_path(path: Path) -> str:
    return _hash_dir_files(path) if path.is_dir() else _hash_file(path)


def _create_dir_bundle(
    title_id: str,
    root_dir: Path,
    skip_names: set[str] | None = None,
) -> bytes:
    files: list[tuple[str, bytes, bytes]] = []
    skip = {name.upper() for name in (skip_names or set())}
    for rel_path, fp in _iter_dir_files(root_dir):
        if fp.name.upper() in skip:
            continue
        data = fp.read_bytes()
        files.append((rel_path, data, hashlib.sha256(data).digest()))

    if not files:
        raise ValueError(f"No files found in {root_dir}")

    file_table = bytearray()
    file_data = bytearray()
    for rel_path, data, sha256 in files:
        path_bytes = rel_path.encode("utf-8")
        file_table += struct.pack("<H", len(path_bytes))
        file_table += path_bytes
        file_table += struct.pack("<I", len(data))
        file_table += sha256
        file_data += data

    payload = bytes(file_table) + bytes(file_data)
    compressed = zlib.compress(payload, 6)
    title_id_bytes = title_id.encode("ascii")

    header = bytearray(b"3DSS")
    if len(title_id_bytes) <= 31:
        header += struct.pack("<I", 4)
        header += title_id_bytes[:31].ljust(32, b"\x00")
    else:
        header += struct.pack("<I", 5)
        header += title_id_bytes[:63].ljust(64, b"\x00")
    header += struct.pack("<I", int(time.time()))
    header += struct.pack("<I", len(files))
    header += struct.pack("<I", len(payload))
    return bytes(header) + compressed


def _parse_dir_bundle(data: bytes) -> list[tuple[str, bytes]]:
    if len(data) < 8 or data[:4] != b"3DSS":
        raise ValueError("Not a valid 3DSS bundle")

    (version,) = struct.unpack_from("<I", data, 4)
    if version == 5:
        offset = 4 + 4 + 64 + 4
    elif version == 4:
        offset = 4 + 4 + 32 + 4
    elif version == 3:
        offset = 4 + 4 + 16 + 4
    elif version in (1, 2):
        offset = 4 + 4 + 8 + 4
    else:
        raise ValueError(f"Unknown bundle version: {version}")

    file_count = struct.unpack_from("<I", data, offset)[0]
    offset += 4
    size_field = struct.unpack_from("<I", data, offset)[0]
    offset += 4

    if version == 1:
        payload = data[offset:]
    else:
        payload = zlib.decompress(data[offset:])
        if len(payload) != size_field:
            raise ValueError("Bundle payload size mismatch")

    pos = 0
    entries: list[tuple[str, int]] = []
    for _ in range(file_count):
        path_len = struct.unpack_from("<H", payload, pos)[0]
        pos += 2
        rel_path = payload[pos : pos + path_len].decode("utf-8")
        pos += path_len
        size = struct.unpack_from("<I", payload, pos)[0]
        pos += 4
        pos += 32
        entries.append((rel_path, size))

    files: list[tuple[str, bytes]] = []
    for rel_path, size in entries:
        files.append((rel_path, payload[pos : pos + size]))
        pos += size
    return files


def _clear_dir_contents(path: Path) -> None:
    if not path.exists():
        return
    for fp in sorted(path.rglob("*"), reverse=True):
        if fp.is_file():
            fp.unlink()
        elif fp.is_dir():
            fp.rmdir()


def _extract_bundle_to_dir(data: bytes, dest_dir: Path) -> None:
    files = _parse_dir_bundle(data)
    dest_dir.mkdir(parents=True, exist_ok=True)
    _clear_dir_contents(dest_dir)
    for rel_path, content in files:
        target = dest_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


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
            hash_val = _hash_path(path)
        hashes_by_path.append((path, hash_val))
        seen_hashes.add(hash_val)

    if len(seen_hashes) <= 1:
        return False, ""

    lines = ["Multiple local save copies differ for this game:"]
    lines.extend(str(path) for path, _ in hashes_by_path)
    lines.append(
        "Download from server to overwrite all copies, or align them manually before upload."
    )
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
        return {s["system"]: s for s in profile["systems"] if s.get("enabled", True)}
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


def scan_profile(
    profile: dict,
    progress_callback=None,
    enable_auto_normalize: bool = True,
    saves_only: bool = False,
) -> list[SaveFile]:
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
    save_ext = resolve_save_ext(system_override, save_ext)

    # Per-system config map: {system_code: {save_ext, save_folder, …}}
    # Empty dict means "no filter / no overrides".
    systems_config = _parse_systems_config(profile)
    enabled_systems = list(systems_config.keys())

    save_folder = Path(save_folder_str) if save_folder_str else None
    rom_folder = Path(rom_folder_str) if rom_folder_str else None
    profile_scope = _profile_runtime_scope(profile)
    # Convenience: the "active" folder for legacy save-based scanners
    folder = (
        save_folder
        if (save_folder and save_folder.exists())
        else (rom_folder or Path("."))
    )

    if not folder.exists() and not (rom_folder and rom_folder.exists()):
        return []

    results: list[SaveFile] = []
    profile_name = profile.get("name", "Profile")
    _emit_progress(
        progress_callback, f"Scanning local files for {profile_name}…", 0, None
    )

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
        and rom_folder
        and rom_folder.exists()
        and save_folder
        and save_folder.exists()
        and len(enabled_systems) == 1
    ):
        # Single-system Pocket profiles sometimes point directly at a mirrored
        # sub-root like Assets/gba/common and Saves/gba/common rather than the
        # global Assets/ and Saves/ roots. Scan those as direct ROM/save trees.
        sys_code = enabled_systems[0]
        sys_info = systems_config.get(sys_code, {})
        sys_ext = resolve_save_ext(sys_code, sys_info.get("save_ext", save_ext) or save_ext)
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
            saves_only=saves_only,
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
                sys_ext = resolve_save_ext(
                    sys_code, sys_info.get("save_ext", save_ext) or save_ext
                )
                sys_sv_str = sys_info.get("save_folder", "")
                sv_dir = Path(sys_sv_str) if sys_sv_str else save_folder / sys_dir.name
                results.extend(
                    _scan_roms_match_saves(
                        sys_dir,
                        sv_dir,
                        sys_code,
                        save_ext=sys_ext,
                        progress_callback=progress_callback,
                        enable_auto_normalize=enable_auto_normalize,
                        profile_scope=profile_scope,
                        saves_only=saves_only,
                    )
                )
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
                rom_folder,
                save_folder,
                save_ext=save_ext,
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
        results = _scan_emudeck(
            folder, progress_callback=progress_callback, profile_scope=profile_scope
        )

    elif device_type == "MemCard Pro":
        # MemCard Pro is a card-manager profile, not a ROM folder. The selected
        # system determines which card layout to scan inside the chosen root.
        if system_override in {"PS1", "PS2", "GC", "DC"}:
            results = _scan_memcard_pro(
                folder,
                system_override,
                progress_callback=progress_callback,
                profile_scope=profile_scope,
            )

    elif device_type == "MEGA EverDrive":
        # MEGA EverDrive Pro: gamedata/<Game Name>/bram.srm layout.
        # path (or save_folder) points to the gamedata/ folder.
        gamedata = save_folder if (save_folder and save_folder.exists()) else rom_folder
        if (
            gamedata
            and gamedata.exists()
            and system_override
            and system_override in SYSTEM_CODES
        ):
            results = _scan_mega_everdrive(
                gamedata,
                system_override,
                progress_callback=progress_callback,
                enable_auto_normalize=enable_auto_normalize,
                profile_scope=profile_scope,
            )

    elif device_type == "SAROO":
        # Saroo ODE: all saves in SS_SAVE.BIN at the SD card root.
        # path = Saroo SD card root folder (must contain SS_SAVE.BIN).
        # save_folder (optional) = mednafen save folder for emulator sync.
        if rom_folder and rom_folder.exists():
            mednafen_folder = (
                save_folder if (save_folder and save_folder.exists()) else None
            )
            results = _scan_saroo(
                rom_folder,
                mednafen_folder,
                progress_callback=progress_callback,
                profile_scope=profile_scope,
            )

    elif device_type == "CD Folder":
        # CD Folder: game_root/<Game Name (Region) (Disc N)>/ structure.
        # Each subfolder containing a .cue/.iso/.bin/.chd file is one disc.
        # Multi-disc games share a single server slot (disc tags stripped from title_id).
        # Optional Redump DAT provides canonical game names.
        if rom_folder and rom_folder.exists():
            redump_index: Optional[dict[str, str]] = None
            dat_path_str = profile.get("dat_path", "")
            if dat_path_str:
                dat_file = Path(dat_path_str)
                if dat_file.exists():
                    try:
                        import rom_normalizer as _rn

                        _, redump_index = _rn.load_redump_dat(dat_file)
                    except Exception:
                        pass
            results = _scan_cd_game_folders(
                rom_folder,
                save_folder=save_folder
                if (save_folder and save_folder.exists())
                else None,
                system=system_override if system_override in SYSTEM_CODES else "PS1",
                redump_index=redump_index,
                save_ext=resolve_save_ext(
                    system_override if system_override in SYSTEM_CODES else "PS1",
                    save_ext,
                ),
                progress_callback=progress_callback,
                profile_scope=profile_scope,
            )

    else:
        # Generic / Everdrive — single system
        if system_override and system_override in SYSTEM_CODES:
            if rom_folder and rom_folder.exists() and save_folder is not None:
                results = _scan_roms_match_saves(
                    rom_folder,
                    save_folder,
                    system_override,
                    save_ext=save_ext,
                    progress_callback=progress_callback,
                    enable_auto_normalize=enable_auto_normalize,
                    profile_scope=profile_scope,
                    saves_only=saves_only,
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
    _emit_progress(
        progress_callback,
        f"Found {len(results)} local save entries.",
        len(results),
        len(results),
    )
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
    stem = Path(filename).stem
    regions = _extract_regions(stem)
    base_name = _REV_RE.sub("", stem)
    base_name = _DISC_RE.sub("", base_name)
    base_name = _EXTRA_RE.sub("", base_name).strip()
    base = make_title_id(system, base_name)
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


def _resolve_effective_title_id(
    save: SaveFile, server_titles: dict[str, dict]
) -> tuple[str, str, str | None]:
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
        return (
            legacy,
            "ambiguous",
            f"Both legacy and canonical server slots already exist: {legacy} and {canonical}",
        )

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
    candidates = _safe_walk(folder, recursive=recursive)
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
            _emit_progress(
                progress_callback, f"Scanning {system} files… {idx}/{total}", idx, total
            )
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
            if (
                existing.path
                and existing.path != sf.path
                and existing.path not in sf.alternate_paths
            ):
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
                if (
                    candidate != existing.path
                    and candidate not in existing.alternate_paths
                ):
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
    saves_only: bool = False,
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
        save_candidates = _safe_walk(save_folder, recursive=True)
        save_total = len(save_candidates)
        for idx, f in enumerate(save_candidates, start=1):
            if not f.is_file():
                continue
            ext = f.suffix.lower()
            if mirror_relative_path:
                try:
                    rel_key = (
                        f.relative_to(save_folder).with_suffix("").as_posix().lower()
                    )
                except ValueError:
                    rel_key = f.stem.lower()
                if ext == save_ext.lower() or (
                    ext in SAVE_EXTENSIONS and rel_key not in save_index
                ):
                    save_index[rel_key] = f
            else:
                if ext == save_ext.lower():
                    save_index[f.stem.lower()] = f  # exact extension wins
                elif ext in SAVE_EXTENSIONS and f.stem.lower() not in save_index:
                    save_index[f.stem.lower()] = f  # fallback if no exact match yet
            if idx == 1 or idx % 100 == 0 or idx == save_total:
                _emit_progress(
                    progress_callback,
                    f"Indexing {system} save files… {idx}/{save_total}",
                    idx,
                    save_total,
                )

    # Fast path: skip ROM walk entirely, return only saves that already exist.
    # Used for the quick first-pass scan before the full ROM library walk.
    if saves_only:
        fast_results: list[SaveFile] = []
        items = list(save_index.items())
        total = len(items)
        for idx, (stem, save_path) in enumerate(items, start=1):
            try:
                file_hash = _hash_file(save_path)
                mtime = save_path.stat().st_mtime
            except OSError:
                continue
            sf = _build_save_file(
                system=system,
                game_name=save_path.stem,
                source_name=save_path.name,
                path=save_path,
                file_hash=file_hash,
                mtime=mtime,
                save_exists=True,
                enable_auto_normalize=enable_auto_normalize,
                match_name=save_path.stem,  # fuzzy lookup uses stem, not "name.sav"
                profile_scope=profile_scope,
            )
            if idx == 1 or idx % 25 == 0 or idx == total:
                _emit_progress(
                    progress_callback,
                    f"Indexing {system} saves… {idx}/{total}",
                    idx,
                    total,
                )
            fast_results.append(sf)
        return _dedup_saves(fast_results)

    results: list[SaveFile] = []
    matched_save_paths: set[Path] = set()
    candidates = _safe_walk(rom_folder, recursive=recursive)
    total = len(candidates)
    for idx, rom_file in enumerate(candidates, start=1):
        try:
            if not rom_file.is_file():
                continue
        except OSError:
            continue
        if (
            rom_file.suffix.lower() not in ROM_EXTENSIONS
            and rom_file.suffix.lower() not in ZIP_ROM_EXTENSIONS
        ):
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
            save_path = (
                save_folder / rel_parent / (rom_file.stem + save_ext)
                if mirror_relative_path
                else save_folder / (rom_file.stem + save_ext)
            )
            file_hash = ""
            mtime = 0.0
            save_exists = False
        else:
            matched_save_paths.add(save_path)
            try:
                file_hash = _hash_file(save_path)
                mtime = save_path.stat().st_mtime
                save_exists = True
            except OSError:
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
            _emit_progress(
                progress_callback, f"Scanning {system} ROMs… {idx}/{total}", idx, total
            )

    # Include saves that exist in the save folder but have no matching ROM file.
    # This handles saves for games whose ROM was removed from the card — they
    # should still sync with the server rather than appear as server-only.
    unmatched = [p for p in save_index.values() if p not in matched_save_paths]
    for save_path in unmatched:
        try:
            file_hash = _hash_file(save_path)
            mtime = save_path.stat().st_mtime
        except OSError:
            continue
        sf = _build_save_file(
            system=system,
            game_name=save_path.stem,
            source_name=save_path.name,
            path=save_path,
            file_hash=file_hash,
            mtime=mtime,
            save_exists=True,
            enable_auto_normalize=enable_auto_normalize,
            match_name=save_path.stem,
            profile_scope=profile_scope,
        )
        results.append(sf)

    return _dedup_saves(results)


def _scan_retroarch(
    root: Path,
    progress_callback=None,
    enable_auto_normalize: bool = True,
    profile_scope: str = "",
) -> list[SaveFile]:
    """Scan RetroArch saves/CoreName/game.srm structure."""
    results = []
    for core_dir in sorted(root.iterdir()):
        if not core_dir.is_dir():
            continue
        system = RETROARCH_CORE_MAP.get(core_dir.name)
        if not system:
            continue
        results.extend(
            _scan_flat(
                core_dir,
                system,
                progress_callback=progress_callback,
                enable_auto_normalize=enable_auto_normalize,
                profile_scope=profile_scope,
            )
        )
    return results


def _scan_mega_everdrive(
    gamedata_folder: Path,
    system: str,
    progress_callback=None,
    enable_auto_normalize: bool = True,
    profile_scope: str = "",
) -> list[SaveFile]:
    """Scan MEGA EverDrive Pro gamedata/ structure.

    Structure (cartridge games — MD, SMS, 32X, etc.):
        gamedata/<Game Name (Region).ext>/bram.srm   ← save file (sync this)
        gamedata/<Game Name (Region).ext>/*.sav       ← save states (ignore)

    Structure (Sega CD):
        gamedata/<Game Name (Region).cue>/cd-bram.brm  ← CD backup RAM
        Folders are named after the .cue file including extension.

    Each subfolder is named after the ROM (or .cue for CD games).  Only
    subfolders that contain the appropriate save file are returned; folders
    without it have no battery save to sync.
    """
    # Sega CD uses a different save filename
    _CD_SYSTEMS = {"SEGACD"}
    is_cd_system = system in _CD_SYSTEMS
    save_filename = "cd-bram.brm" if is_cd_system else "bram.srm"

    results = []
    candidates = sorted(gamedata_folder.iterdir())
    total = len(candidates)
    for idx, game_dir in enumerate(candidates, start=1):
        if not game_dir.is_dir():
            continue
        save_file = game_dir / save_filename
        if not save_file.exists():
            continue
        # For CD games the folder is named after the .cue (e.g. "Sonic CD (USA).cue");
        # strip the .cue extension to get the clean game name for display & title_id.
        dir_name = game_dir.name
        if is_cd_system and dir_name.lower().endswith(".cue"):
            dir_name = dir_name[:-4]
        results.append(
            _build_save_file(
                system=system,
                game_name=dir_name,
                source_name=dir_name,
                path=save_file,
                file_hash=_hash_file(save_file),
                mtime=save_file.stat().st_mtime,
                save_exists=True,
                enable_auto_normalize=enable_auto_normalize,
                match_name=dir_name,
                profile_scope=profile_scope,
            )
        )
        if idx == 1 or idx % 25 == 0 or idx == total:
            _emit_progress(
                progress_callback,
                f"Scanning {system} EverDrive folders… {idx}/{total}",
                idx,
                total,
            )
    return _dedup_saves(results)


def _scan_saroo(
    saroo_root: Path,
    mednafen_save_folder: Optional[Path],
    progress_callback=None,
    profile_scope: str = "",
) -> list[SaveFile]:
    """Scan a Saroo SD card root for SS_SAVE.BIN and produce per-game SaveFiles.

    The Saroo stores all internal-memory saves in a single file:
        <saroo_root>/SS_SAVE.BIN

    Each game's saves are held in a dedicated 64KB slot identified by a 16-char
    Game ID (the disc's product code region field).  We expose each slot as one
    SaveFile with system="SAT" and a title_id derived from the game ID.

    The server stores the raw mednafen 32KB image (native Saturn format) for
    cross-emulator compatibility.  On upload we convert Saroo→mednafen; on
    download the caller converts mednafen→Saroo.

    mednafen_save_folder:
        If provided, we also look for a matching <game_id>.bkr file there and
        use it as the save source when it is newer than the Saroo slot.  This
        allows the desktop to sync from mednafen directly when the Saroo SD is
        not inserted.
    """
    from saroo_format import (
        parse_ss_save_bin,
        saroo_slot_to_mednafen,
        slot_content_hash,
    )

    ss_save = saroo_root / "SS_SAVE.BIN"
    if not ss_save.exists():
        # Auto-detect: if path is the SD card root and SAROO/ subfolder exists, use it
        candidate = saroo_root / "SAROO" / "SS_SAVE.BIN"
        if candidate.exists():
            ss_save = candidate
            saroo_root = saroo_root / "SAROO"
        else:
            _emit_progress(
                progress_callback,
                f"SS_SAVE.BIN not found in {saroo_root} (also checked SAROO/ subfolder).",
                0,
                0,
            )
            return []

    try:
        data = ss_save.read_bytes()
        slots = parse_ss_save_bin(data)
    except Exception as exc:
        _emit_progress(progress_callback, f"Error reading SS_SAVE.BIN: {exc}", 0, 0)
        return []

    if not slots:
        # Provide a diagnostic hint so the user sees something in the status bar
        if len(data) < 0x10000:
            reason = f"SS_SAVE.BIN too small ({len(data)} bytes, need ≥65536)"
        elif data[:16] != b"Saroo Save File\x00":
            actual = data[:16]
            reason = f"Unrecognised magic: {actual!r}"
        else:
            reason = "No game slots found in SS_SAVE.BIN"
        _emit_progress(progress_callback, f"Saroo scan: {reason}", 0, 0)
        return []

    results: list[SaveFile] = []
    total = len(slots)

    # Load libretro Saturn DAT for serial → game name lookups (best-effort).
    _libretro_serial_index: dict[str, str] = {}
    try:
        import rom_normalizer as _rn_saroo

        _libretro_dat_path = _rn_saroo.find_libretro_dat_for_system("SAT")
        if _libretro_dat_path:
            _libretro_serial_index = _rn_saroo.load_libretro_dat(_libretro_dat_path)
    except Exception:
        pass

    for idx, slot in enumerate(slots, start=1):
        game_id = slot.game_id.strip()
        if not game_id:
            continue

        # The Saroo stores the full 16-byte disc header product code, which
        # looks like "T-10604G  V1.002" — the product code is the part before
        # the first run of spaces (or "V" version marker).
        # Strip the version suffix so the title_id is stable across firmware
        # updates and the display name is clean.
        product_code = re.split(r"\s{2,}|(?<=\w)\s*V\d", game_id)[0].strip()
        if not product_code:
            product_code = game_id.split()[0] if game_id.split() else game_id

        # Build a stable title_id from the product code only.
        safe_id = re.sub(r"[^A-Za-z0-9_-]", "", product_code.replace(" ", "_")).upper()
        title_id = f"SAT_{safe_id}"

        # Convert the Saroo slot bytes to a mednafen 32KB image for hashing
        # and for use as the canonical payload on the server.
        # idx starts at 1 (slot 0 is reserved); parse_ss_save_bin breaks at the
        # first empty entry so slots are always contiguous, making idx the correct
        # file slot number.
        slot_byte_offset = (
            idx * 0x10000
        )  # byte offset in SS_SAVE.BIN (0x10000 per slot)
        slot_bytes = data[slot_byte_offset : slot_byte_offset + 0x10000]
        try:
            native_bytes = saroo_slot_to_mednafen(slot_bytes)
        except Exception:
            native_bytes = b"\x00" * 0x8000

        # Resolve display name: prefer libretro DAT lookup by product code,
        # fall back to the raw game_id string.
        display_name = _libretro_serial_index.get(product_code) or game_id.strip()

        # Check if there's a matching mednafen .bkr file that might be newer
        bkr_path: Optional[Path] = None
        if mednafen_save_folder and mednafen_save_folder.exists():
            # Mednafen names Saturn saves as <game_id>.bkr (spaces replaced with _)
            candidate = mednafen_save_folder / f"{safe_id}.bkr"
            if candidate.exists():
                bkr_path = candidate

        selected_bytes = native_bytes
        selected_mtime = ss_save.stat().st_mtime
        if bkr_path is not None:
            try:
                bkr_mtime = bkr_path.stat().st_mtime
                if bkr_mtime > selected_mtime:
                    selected_bytes = bkr_path.read_bytes()
                    selected_mtime = bkr_mtime
            except OSError:
                pass

        file_hash = hashlib.sha256(selected_bytes).hexdigest()
        mtime = selected_mtime

        # Store Saroo-specific metadata for use during upload/download
        _SAROO_META[title_id] = {
            "game_id": game_id,
            "slot_index": slot_byte_offset,
            "native_bytes": selected_bytes,
            "bkr_path": str(bkr_path) if bkr_path else "",
        }

        results.append(
            SaveFile(
                system="SAT",
                title_id=title_id,
                game_name=display_name,
                path=ss_save,  # canonical source is SS_SAVE.BIN
                hash=file_hash,
                mtime=mtime,
                save_exists=True,
                profile_scope=profile_scope,
            )
        )

        _emit_progress(
            progress_callback,
            f"Scanning Saroo slots… {idx}/{total}",
            idx,
            total,
        )

    return results


def _scan_cd_game_folders(
    game_root: Path,
    save_folder: Optional[Path],
    system: str,
    redump_index: Optional[dict[str, str]] = None,
    save_ext: str = ".mcd",
    progress_callback=None,
    profile_scope: str = "",
) -> list[SaveFile]:
    """Scan a folder-per-game CD ROM structure.

    Expected layout::

        game_root/
            Parasite Eve (USA) (Disc 1)/     ← subfolder = one disc
                Parasite Eve (USA) (Disc 1).cue
                Parasite Eve (USA) (Disc 1) (Track 01).bin
                ...
            Parasite Eve (USA) (Disc 2)/
                ...
            Final Fantasy VII (USA)/         ← single-disc game
                Final Fantasy VII (USA).iso

    Each subdirectory that contains at least one CD image file (.cue, .iso, .bin,
    .img, .mdf, .chd) is treated as a single disc.  Multi-disc games are grouped by
    their disc-agnostic title ID (all parenthetical tags stripped via
    ``normalize_rom_name()``) so all discs of a game share one server slot.

    Title IDs use the same format as Android's ``toPs1TitleId()``:
        ``"Parasite Eve (USA) (Disc 1)"`` → ``"PS1_parasite_eve"``

    If a Redump disc-agnostic name index is provided (``{slug: canonical_name}``),
    it is used for display names; otherwise the folder name is used (disc tag stripped).

    The save file is located by matching the disc-agnostic slug against files in
    ``save_folder`` (if given), or as a file inside the first disc subfolder.
    Slot suffixes like ``_1``, ``_2`` are stripped from save stems before matching.
    """
    if not save_ext.startswith("."):
        save_ext = "." + save_ext

    all_save_exts = frozenset({save_ext, ".mcd", ".mcr", ".sav", ".srm", ".frz"})

    # ── Index save_folder by disc-agnostic slug ───────────────────────────────
    # slug -> best save path (prefer save_ext match, then others)
    save_index: dict[str, Path] = {}
    if save_folder and save_folder.exists():
        try:
            for f in sorted(save_folder.iterdir()):
                if not f.is_file() or f.suffix.lower() not in all_save_exts:
                    continue
                # Strip slot suffix e.g. "_1" before normalizing
                stem_no_slot = _MCD_SLOT_RE.sub("", f.stem)
                slug = normalize_rom_name(stem_no_slot)
                if not slug or slug == "unknown":
                    continue
                # Prefer save_ext match; otherwise keep first found
                if slug not in save_index or f.suffix.lower() == save_ext:
                    save_index[slug] = f
        except OSError:
            pass

    # ── Discover disc subdirectories ──────────────────────────────────────────
    # groups: disc_agnostic_slug -> {"display_name": str, "folders": [Path]}
    groups: dict[str, dict] = {}
    try:
        candidates = sorted(game_root.iterdir())
    except OSError:
        return []

    for entry in candidates:
        if not entry.is_dir():
            continue
        # Check that this subfolder actually contains a CD image file
        try:
            has_cd = any(
                f.is_file() and f.suffix.lower() in CD_ROM_EXTENSIONS
                for f in entry.iterdir()
            )
        except OSError:
            continue
        if not has_cd:
            continue

        slug = normalize_rom_name(entry.name)
        if not slug or slug == "unknown":
            continue

        if slug not in groups:
            # Display name: folder name with disc tag stripped but region kept
            display = _DISC_RE.sub("", entry.name).strip()
            groups[slug] = {"display_name": display, "folders": [entry]}
        else:
            groups[slug]["folders"].append(entry)

    if not groups:
        return []

    # ── Build one SaveFile per grouped game ───────────────────────────────────
    results: list[SaveFile] = []
    group_list = sorted(groups.items())
    total = len(group_list)

    for idx, (slug, info) in enumerate(group_list, start=1):
        display_name = info["display_name"]
        first_folder: Path = info["folders"][0]

        # Canonical name from Redump index (e.g. "Parasite Eve (USA)") or folder name
        canonical_name: Optional[str] = redump_index.get(slug) if redump_index else None
        game_name = canonical_name or display_name

        # Title ID: SYSTEM_slug — disc-agnostic, no region (matches Android toPs1TitleId)
        title_id = f"{system}_{slug}"

        # ── Locate save file ─────────────────────────────────────────────────
        save_path: Optional[Path] = None
        save_exists = False
        file_hash = ""
        mtime = 0.0

        if slug in save_index:
            # Found an existing save file in the save_folder
            save_path = save_index[slug]
            save_exists = save_path.exists()
        elif save_folder is not None:
            # Save_folder configured but no save yet — derive expected path
            save_path = save_folder / f"{display_name}{save_ext}"
        else:
            # No separate save folder — look inside the first disc subfolder
            for ext_try in (save_ext, ".mcd", ".mcr", ".sav", ".srm"):
                candidate = first_folder / f"{first_folder.name}{ext_try}"
                if candidate.exists():
                    save_path = candidate
                    save_exists = True
                    break
            if save_path is None:
                # Expected path for future download
                save_path = first_folder / f"{first_folder.name}{save_ext}"

        if save_exists and save_path is not None and save_path.exists():
            try:
                file_hash = _hash_file(save_path)
                mtime = save_path.stat().st_mtime
            except OSError:
                save_exists = False

        results.append(
            SaveFile(
                title_id=title_id,
                path=save_path,
                hash=file_hash,
                mtime=mtime,
                system=system,
                game_name=game_name,
                save_exists=save_exists,
                legacy_title_id=title_id,
                canonical_title_id=title_id,
                title_id_source="cd_folder",
                title_id_confidence="high" if canonical_name else "filename",
                profile_scope=profile_scope,
            )
        )

        if idx == 1 or idx % 25 == 0 or idx == total:
            _emit_progress(
                progress_callback,
                f"Scanning {system} CD folders… {idx}/{total}",
                idx,
                total,
            )

    return results


def _scan_mister(
    root: Path,
    progress_callback=None,
    enable_auto_normalize: bool = True,
    profile_scope: str = "",
) -> list[SaveFile]:
    """Scan MiSTer saves/<System>/ structure."""
    results = []
    for sys_dir in sorted(root.iterdir()):
        if not sys_dir.is_dir():
            continue
        system = MISTER_FOLDER_MAP.get(sys_dir.name)
        if not system:
            continue
        results.extend(
            _scan_flat(
                sys_dir,
                system,
                progress_callback=progress_callback,
                enable_auto_normalize=enable_auto_normalize,
                profile_scope=profile_scope,
            )
        )
    return results


def _scan_pocket(
    root: Path,
    progress_callback=None,
    enable_auto_normalize: bool = True,
    profile_scope: str = "",
) -> list[SaveFile]:
    """Scan Analogue Pocket Memories/<Platform>/ structure."""
    results = []
    for plat_dir in sorted(root.iterdir()):
        if not plat_dir.is_dir():
            continue
        system = POCKET_FOLDER_MAP.get(plat_dir.name)
        if not system:
            continue
        results.extend(
            _scan_flat(
                plat_dir,
                system,
                progress_callback=progress_callback,
                enable_auto_normalize=enable_auto_normalize,
                profile_scope=profile_scope,
            )
        )
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
        results.extend(
            _scan_flat(
                sys_dir,
                system,
                recursive=True,
                progress_callback=progress_callback,
                enable_auto_normalize=enable_auto_normalize,
                profile_scope=profile_scope,
            )
        )
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
            if (
                rom_file.suffix.lower() not in ROM_EXTENSIONS
                and rom_file.suffix.lower() not in ZIP_ROM_EXTENSIONS
            ):
                continue
            if rom_file.name.startswith("."):
                continue
            try:
                rel = rom_file.relative_to(sys_dir)
            except ValueError:
                continue
            # Mirror: saves_root/<sys>/<same subpath>/<rom_stem><save_ext>
            save_path = (
                saves_root / sys_folder_name / rel.parent / (rom_file.stem + save_ext)
            )
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
                _emit_progress(
                    progress_callback,
                    f"Scanning {system} Assets… {idx}/{total}",
                    idx,
                    total,
                )
    return _dedup_saves(results)


# EmuDeck: emulator subfolder -> (saves subfolder, system code)
# Emulators with file-per-game saves that map cleanly to our slug format.
EMUDECK_EMULATOR_MAP: dict[str, tuple[str, str]] = {
    "duckstation": ("saves", "PS1"),  # .mcd memory card files, named by game
    "pcsx2": ("saves", "PS2"),  # .ps2 shared memory cards (Mcd001.ps2 etc.)
    "melonds": ("saves", "NDS"),  # .sav/.dsv per-game saves
    "flycast": ("saves", "DC"),  # .sav Dreamcast VMU saves
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
_PSX_RETAIL_PREFIXES: frozenset[str] = frozenset(
    {
        # North America
        "SLUS",
        "SCUS",
        "PAPX",
        # Europe
        "SLES",
        "SCES",
        "SCED",
        # Japan
        "SLPS",
        "SLPM",
        "SCPS",
        "SCPM",
        # Other
        "SLAJ",
        "SLEJ",
        "SCAJ",
    }
)

_PS1_SERIAL_RE = re.compile(r"^([A-Z]{4})(\d{5,})$")

# MemCard Pro GC: disc folders named e.g. DL-DOL-GBZE-USA
_GC_DISC_ID_RE = re.compile(r"^DL-DOL-([A-Z0-9]{4})-[A-Z]{2,3}$")


def _gc_code_from_folder(folder_name: str) -> str | None:
    """Extract the 4-char GC game code from a MemCard Pro disc folder name.

    "DL-DOL-GBZE-USA" → "GBZE"
    Returns None if the folder name doesn't match the expected pattern.
    """
    m = _GC_DISC_ID_RE.match(folder_name.upper())
    return m.group(1) if m else None


def _normalize_ps1_serial(stem: str) -> str | None:
    """Normalize a PS1 memory-card filename stem to a bare product code.

    Examples: "SLUS-01234" → "SLUS01234", "SCUS_94163" → "SCUS94163".
    Returns None if the result doesn't look like a PS1 product code
    (4 uppercase letters followed by 5+ digits).
    """
    code = re.sub(r"[^A-Z0-9]", "", stem.upper())
    return code if _PS1_SERIAL_RE.match(code) else None


# ---------------------------------------------------------------------------
# GC memory card helpers (MemCard Pro ↔ Dolphin .gci conversion)
# ---------------------------------------------------------------------------

_GC_BLOCK_SIZE = 0x2000  # 8 192 bytes per GC block
_GC_DIR1_OFFSET = 0x2000  # block 1 — primary directory
_GC_DIR2_OFFSET = 0x4000  # block 2 — directory backup
_GC_DENTRY_SIZE = 64  # bytes per directory entry
_GC_MAX_ENTRIES = 127  # directory holds at most 127 entries
# Within each 64-byte DEntry:
#   [0:4]   game code (ASCII)
#   [50:52] first_block (big-endian uint16) — absolute block index in card
#   [52:54] block_count (big-endian uint16)
_GC_DENTRY_GAMECODE_OFF = 0
_GC_DENTRY_FIRST_BLOCK_OFF = (
    54  # 0x36  (filename field is 0x20 = 32 bytes, per Dolphin source)
)
_GC_DENTRY_BLOCK_COUNT_OFF = 56  # 0x38


def gc_extract_gci(card_bytes: bytes, game_code: str) -> bytes | None:
    """Extract a single game's save data from an 8 MB GC memory card image.

    Returns the canonical GCI layout: 64-byte directory entry header followed
    by the raw data blocks, or None if the game code is not found.

    ``game_code`` is the 4-character GC identifier (e.g. "GM4E").
    """
    code_bytes = game_code.upper().encode("ascii")
    if len(code_bytes) != 4:
        return None
    if len(card_bytes) < _GC_DIR1_OFFSET + _GC_MAX_ENTRIES * _GC_DENTRY_SIZE:
        return None

    for i in range(_GC_MAX_ENTRIES):
        entry_off = _GC_DIR1_OFFSET + i * _GC_DENTRY_SIZE
        entry = card_bytes[entry_off : entry_off + _GC_DENTRY_SIZE]
        if len(entry) < _GC_DENTRY_SIZE:
            break
        # Unused/deleted entries are 0xFF-filled
        if entry[0:4] == b"\xff\xff\xff\xff":
            continue
        if entry[_GC_DENTRY_GAMECODE_OFF : _GC_DENTRY_GAMECODE_OFF + 4] != code_bytes:
            continue
        # Found the entry
        first_block = struct.unpack_from(">H", entry, _GC_DENTRY_FIRST_BLOCK_OFF)[0]
        block_count = struct.unpack_from(">H", entry, _GC_DENTRY_BLOCK_COUNT_OFF)[0]
        data_start = first_block * _GC_BLOCK_SIZE
        data_end = data_start + block_count * _GC_BLOCK_SIZE
        if data_end > len(card_bytes):
            return None
        return entry + card_bytes[data_start:data_end]

    return None


def _should_use_ps1_card_endpoint(title_id: str, system: str | None = None) -> bool:
    """Return True when this save should use the dedicated PS1 card endpoints.

    PS1 and PS2 retail serials share the same basic shape (four letters plus
    digits), so endpoint selection must prefer the explicit system when the
    caller has it. The title-id-only fallback is kept for older call sites.
    """
    if system:
        return system.upper() == "PS1"
    return _normalize_ps1_serial(title_id) is not None


def _should_use_ps2_card_endpoint(system: str | None = None) -> bool:
    """Return True when this save should use the dedicated PS2 card endpoints.

    PS2 retail serials overlap PS1 prefixes, so the explicit system is the only
    safe signal here. The PS2 card API defaults to canonical `.mc2`, which is
    what MemCard Pro expects locally.
    """
    return (system or "").upper() == "PS2"


# MemCard Pro: known shared/global card names that hold all games (skip during per-title scan)
_MCD_SHARED_NAMES: frozenset[str] = frozenset(
    {
        "shared_card_1",
        "shared_card_2",
        "shared_card_3",
        "shared_card_4",
        "mcd001",
        "mcd002",
        "mcd003",
        "mcd004",
        "epsxe000",
        "epsxe001",
        "memorycard",
        "memory card",
    }
)


def _emit_progress(
    callback, message: str, current: int | None = None, total: int | None = None
) -> None:
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
                    info
                    for info in zf.infolist()
                    if not info.is_dir()
                    and Path(info.filename).suffix.lower() in ROM_EXTENSIONS
                ],
                key=lambda info: info.filename.lower(),
            )
    except (OSError, zipfile.BadZipFile):
        return []


def _read_zip_member_header_title(
    path: Path, info: zipfile.ZipInfo, system: str
) -> str | None:
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
                chunk = data[addr : addr + 21]
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


def _scan_cache_key(
    profile_scope: str, system: str, path: Path, match_name: str | None
) -> str:
    try:
        canonical_path = str(path.resolve())
    except OSError:
        canonical_path = str(path)
    return f"{profile_scope}|{system.upper()}|{canonical_path}|{match_name or ''}"


def _get_cached_canonical_name(
    profile_scope: str, system: str, path: Path, match_name: str | None, cache_tag: str
) -> tuple[str | None, str, str] | object:
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


def _resolve_canonical_sync_name(
    system: str, path: Path, match_name: str | None = None, profile_scope: str = ""
) -> tuple[str | None, str, str]:
    """Return canonical name plus match source/confidence for sync-time title mapping.

    This mirrors the ROM Normalizer matching pipeline, but does not rename any
    local files. The resolved canonical name is used only to decide which
    server slot the save belongs to.

    CRC32 is intentionally skipped here — reading entire ROM files from a slow
    device (USB flash, SD card) for every game makes the scan unbearably slow.
    Fuzzy filename matching is fast (in-memory) and accurate enough for the
    sync use case. The ROM Normalizer tab uses CRC when renaming files.
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
    cached = _get_cached_canonical_name(
        profile_scope, system, path, match_name, cache_tag
    )
    if cached is not _CACHE_MISS:
        return cached

    canonical: str | None = None
    source = "legacy"
    confidence = "legacy"
    suffix = path.suffix.lower()

    # Fuzzy filename lookup (in-memory — no file I/O)
    canonical = rn.fuzzy_filename_search(lookup_name, name_index)
    if canonical:
        region_hint = rn.extract_region_hint(lookup_name) or rn.extract_region_hint(
            path.parent.name
        )
        if region_hint:
            canonical = rn.find_region_preferred(canonical, no_intro, region_hint)
        source, confidence = "fuzzy", "low"
        _set_cached_canonical_name(
            profile_scope,
            system,
            path,
            match_name,
            cache_tag,
            canonical,
            source,
            confidence,
        )
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
                    canonical = rn.find_region_preferred(
                        canonical, no_intro, region_hint
                    )
                source, confidence = "fuzzy", "low"
                _set_cached_canonical_name(
                    profile_scope,
                    system,
                    path,
                    match_name,
                    cache_tag,
                    canonical,
                    source,
                    confidence,
                )
                return canonical, source, confidence

    # 3. ROM header title lookup
    if suffix in ROM_EXTENSIONS:
        header_title = rn.read_rom_header_title(path, system)
        if header_title:
            canonical = rn.lookup_header_in_index(header_title, name_index)
            if canonical:
                region_hint = rn.extract_region_hint(
                    path.name
                ) or rn.extract_region_hint(path.parent.name)
                if region_hint:
                    canonical = rn.find_region_preferred(
                        canonical, no_intro, region_hint
                    )
                source, confidence = "header", "high"
                _set_cached_canonical_name(
                    profile_scope,
                    system,
                    path,
                    match_name,
                    cache_tag,
                    canonical,
                    source,
                    confidence,
                )
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
                    canonical = rn.find_region_preferred(
                        canonical, no_intro, region_hint
                    )
                source, confidence = "header", "high"
                _set_cached_canonical_name(
                    profile_scope,
                    system,
                    path,
                    match_name,
                    cache_tag,
                    canonical,
                    source,
                    confidence,
                )
                return canonical, source, confidence

    # 4. Parent-folder name lookup for shorthand ROM names / packs
    if path.parent.name:
        canonical = rn.fuzzy_filename_search(path.parent.name, name_index)
        if canonical:
            region_hint = rn.extract_region_hint(lookup_name) or rn.extract_region_hint(
                path.parent.name
            )
            if region_hint:
                canonical = rn.find_region_preferred(canonical, no_intro, region_hint)
            source, confidence = "folder", "low"
            _set_cached_canonical_name(
                profile_scope,
                system,
                path,
                match_name,
                cache_tag,
                canonical,
                source,
                confidence,
            )
            return canonical, source, confidence

    _set_cached_canonical_name(
        profile_scope, system, path, match_name, cache_tag, None, source, confidence
    )
    return None, source, confidence


def _make_sync_title_id(
    system: str, source_name: str, canonical_name: str | None = None
) -> str:
    """Build the server title ID, preferring a canonical No-Intro name when found."""
    return _make_title_id_with_region(system, canonical_name or source_name)


def _scan_emudeck(
    root: Path, progress_callback=None, profile_scope: str = ""
) -> list[SaveFile]:
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
            results.append(
                SaveFile(
                    title_id=title_id,
                    path=f,
                    hash=file_hash,
                    mtime=f.stat().st_mtime,
                    system=system,
                    game_name=slug_to_display_name(slug),
                    profile_scope=profile_scope,
                )
            )

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
        for save_dir in sorted(rpcs3_saves.iterdir()):
            if not save_dir.is_dir():
                continue
            m = _PS3_CODE_RE.match(save_dir.name)
            if not m:
                continue
            product_code = m.group(1)  # e.g. "BLJM60055"
            files = _iter_dir_files(save_dir)
            if not files:
                continue
            latest_mtime = max(fp.stat().st_mtime for _, fp in files)
            results.append(
                SaveFile(
                    title_id=save_dir.name.upper(),
                    path=save_dir,
                    hash=_hash_ps3_dir_files(save_dir),
                    mtime=latest_mtime,
                    system="PS3",
                    game_name=save_dir.name,
                    profile_scope=profile_scope,
                )
            )

    return results


def _scan_memcard_pro(
    root: Path,
    system: str = "PS1",
    progress_callback=None,
    profile_scope: str = "",
) -> list[SaveFile]:
    """Scan a MemCard Pro root for per-title memory cards.

    The profile points at the card manager root, not at a ROM folder.

    PS1 layout under ``MemoryCards/``:

        <root>/MemoryCards/SLUS-00594/
            SLUS-00594.txt
            SLUS-00594-1.mcd
            ...
            SLUS-00594-8.mcd

    PS2 layout under ``PS2/``:

        <root>/PS2/SLUS-20002/
            name.txt
            SLUS-20002-1.mc2

    We only sync slot 1 with the server for now. Shared/global card folders
    like ``MemoryCard1`` are ignored because they do not map to a single title.
    Older PS1 export layouts are still accepted as fallbacks.
    """
    if system not in {"PS1", "PS2", "GC"}:
        return []

    results: list[SaveFile] = []

    if system == "GC":
        candidates = sorted(root.iterdir())
        total = len(candidates)
        for idx, disc_dir in enumerate(candidates, start=1):
            if not disc_dir.is_dir():
                continue
            gc_code = _gc_code_from_folder(disc_dir.name)
            if not gc_code:
                continue

            # Find the slot-1 .raw file: <folder>/<folder>-1.raw
            slot1: Path | None = None
            preferred_name = f"{disc_dir.name}-1"
            for raw_file in sorted(disc_dir.iterdir()):
                if not raw_file.is_file() or raw_file.suffix.lower() != ".raw":
                    continue
                stem = raw_file.stem
                if stem == preferred_name or stem.endswith("-1"):
                    slot1 = raw_file
                    if stem == preferred_name:
                        break
            if slot1 is None:
                continue

            title_id = f"GC_{gc_code.lower()}"
            # Hash only the extracted GCI bytes so the hash matches what
            # we actually upload to the server (and what Dolphin stores).
            try:
                card_bytes = slot1.read_bytes()
                gci_bytes = gc_extract_gci(card_bytes, gc_code)
            except OSError:
                gci_bytes = None
            if gci_bytes is not None:
                save_hash = hashlib.sha256(gci_bytes).hexdigest()
            else:
                save_hash = _hash_file(slot1)
            results.append(
                SaveFile(
                    title_id=title_id,
                    path=slot1,
                    hash=save_hash,
                    mtime=slot1.stat().st_mtime,
                    system="GC",
                    game_name=disc_dir.name,
                    profile_scope=profile_scope,
                )
            )
            if idx == 1 or idx % 25 == 0 or idx == total:
                _emit_progress(
                    progress_callback,
                    f"Scanning GC MemCard Pro folders. {idx}/{total}",
                    idx,
                    total,
                )
        return results

    if system == "PS2":
        ps2_root = root / "PS2" if (root / "PS2").is_dir() else root
        if not ps2_root.is_dir():
            return []

        candidates = sorted(ps2_root.iterdir())
        total = len(candidates)
        for idx, serial_dir in enumerate(candidates, start=1):
            if not serial_dir.is_dir():
                continue
            serial = _normalize_ps1_serial(serial_dir.name)
            if not serial:
                continue

            slot1: Path | None = None
            preferred_name = f"{serial_dir.name}-1"
            for card_file in sorted(serial_dir.iterdir()):
                if not card_file.is_file() or card_file.suffix.lower() not in {
                    ".mc2",
                    ".ps2",
                }:
                    continue
                stem = card_file.stem
                if stem == preferred_name or stem.endswith("-1"):
                    slot1 = card_file
                    if stem == preferred_name:
                        break
            if slot1 is None:
                continue

            name_txt = serial_dir / "name.txt"
            if name_txt.is_file():
                try:
                    game_name = (
                        name_txt.read_text(encoding="utf-8", errors="ignore").strip()
                        or serial_dir.name
                    )
                except OSError:
                    game_name = serial_dir.name
            else:
                game_name = serial_dir.name

            results.append(
                SaveFile(
                    title_id=serial,
                    path=slot1,
                    hash=_hash_file(slot1),
                    mtime=slot1.stat().st_mtime,
                    system="PS2",
                    game_name=game_name,
                    profile_scope=profile_scope,
                )
            )
            if idx == 1 or idx % 25 == 0 or idx == total:
                _emit_progress(
                    progress_callback,
                    f"Scanning PS2 MemCard Pro folders. {idx}/{total}",
                    idx,
                    total,
                )
        return results

    mcd_exts = {".mcd", ".mcr"}
    card_root = root / "MemoryCards" if (root / "MemoryCards").is_dir() else root

    # Card-manager layout: MemoryCards/<SERIAL>/<SERIAL>-1.mcd
    if card_root.is_dir():
        candidates = sorted(card_root.iterdir())
        total = len(candidates)
        for idx, serial_dir in enumerate(candidates, start=1):
            if not serial_dir.is_dir():
                continue
            serial = _normalize_ps1_serial(serial_dir.name)
            if not serial:
                continue

            slot1: Path | None = None
            preferred_name = f"{serial_dir.name}-1"
            for mcd_file in sorted(serial_dir.iterdir()):
                if not mcd_file.is_file() or mcd_file.suffix.lower() not in mcd_exts:
                    continue
                stem = mcd_file.stem
                if stem == preferred_name or stem.endswith("-1"):
                    slot1 = mcd_file
                    if stem == preferred_name:
                        break
            if slot1 is None:
                continue

            results.append(
                SaveFile(
                    title_id=serial,
                    path=slot1,
                    hash=_hash_file(slot1),
                    mtime=slot1.stat().st_mtime,
                    system="PS1",
                    game_name=serial_dir.name,
                    profile_scope=profile_scope,
                )
            )
            if idx == 1 or idx % 25 == 0 or idx == total:
                _emit_progress(
                    progress_callback,
                    f"Scanning PS1 MemCard Pro folders. {idx}/{total}",
                    idx,
                    total,
                )

    if results:
        return results

    # Hierarchical layout: VIRTUAL MEMORY CARDS/<SERIAL>/MemoryCard.mcd (or any *.mcd)
    vmc_dir = card_root / "VIRTUAL MEMORY CARDS"
    if vmc_dir.is_dir():
        for serial_dir in sorted(vmc_dir.iterdir()):
            if not serial_dir.is_dir():
                continue
            # Pick the first .mcd/.mcr inside (typically MemoryCard.mcd)
            mcd_files = [
                f
                for f in sorted(serial_dir.iterdir())
                if f.is_file() and f.suffix.lower() in mcd_exts
            ]
            if not mcd_files:
                continue
            mcd_file = max(mcd_files, key=lambda f: f.stat().st_mtime)
            serial = _normalize_ps1_serial(serial_dir.name)
            title_id = serial if serial else make_title_id("PS1", serial_dir.name)
            results.append(
                SaveFile(
                    title_id=title_id,
                    path=mcd_file,
                    hash=_hash_file(mcd_file),
                    mtime=mcd_file.stat().st_mtime,
                    system="PS1",
                    game_name=serial_dir.name,
                    profile_scope=profile_scope,
                )
            )

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
        results.append(
            SaveFile(
                title_id=title_id,
                path=mcd_file,
                hash=_hash_file(mcd_file),
                mtime=mcd_file.stat().st_mtime,
                system="PS1",
                game_name=stem,
                profile_scope=profile_scope,
            )
        )

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
    ps1_meta_cache: dict[str, dict[str, str] | None] = {}
    gc_meta_cache: dict[str, dict[str, str] | None] = {}

    _emit_progress(
        progress_callback, "Loading server save index…", 0, max(len(saves), 1)
    )
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
        effective_title_id, resolution_source, mapping_note = (
            _resolve_effective_title_id(save, server_titles)
        )
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
            results.append(
                SyncStatus(
                    save=save,
                    last_synced_hash=last_synced,
                    status="mapping_conflict",
                    mapping_note=mapping_note or "",
                )
            )
            if idx == 1 or idx % 25 == 0 or idx == total:
                _emit_progress(
                    progress_callback,
                    f"Comparing with server… {idx}/{total}",
                    idx,
                    total,
                )
            continue

        if meta is None:
            duplicate_conflict, duplicate_note = _detect_duplicate_local_conflict(save)
            if duplicate_conflict:
                results.append(
                    SyncStatus(
                        save=save,
                        last_synced_hash=last_synced,
                        status="local_duplicate_conflict",
                        mapping_note=duplicate_note,
                    )
                )
                if idx == 1 or idx % 25 == 0 or idx == total:
                    _emit_progress(
                        progress_callback,
                        f"Comparing with server… {idx}/{total}",
                        idx,
                        total,
                    )
                continue
            if server_loaded:
                if not save.save_exists:
                    # ROM present but no local save and nothing on server — nothing to do
                    pass
                else:
                    results.append(
                        SyncStatus(
                            save=save,
                            last_synced_hash=last_synced,
                            status="not_on_server",
                            mapping_note=mapping_note
                            or f"Using {resolution_source}: {save.title_id}",
                        )
                    )
            elif save.save_exists:
                results.append(
                    SyncStatus(
                        save=save,
                        status="error",
                        mapping_note=mapping_note
                        or f"Using {resolution_source}: {save.title_id}",
                    )
                )
            if idx == 1 or idx % 25 == 0 or idx == total:
                _emit_progress(
                    progress_callback,
                    f"Comparing with server… {idx}/{total}",
                    idx,
                    total,
                )
            continue

        server_hash = meta.get("save_hash", "")
        server_ts = meta.get("server_timestamp", "")
        server_name = meta.get("name", "") or meta.get("game_name", "")
        ps1_meta = _load_ps1_card_meta(
            save.title_id, base_url, headers, timeout, ps1_meta_cache
        )
        if ps1_meta:
            server_hash = ps1_meta.get("save_hash", server_hash)
            server_ts = ps1_meta.get("server_timestamp", server_ts)
        gc_meta = _load_gc_card_meta(
            save.title_id, base_url, headers, timeout, gc_meta_cache
        )
        if gc_meta:
            server_hash = gc_meta.get("save_hash", server_hash)
            server_ts = gc_meta.get("server_timestamp", server_ts)
        duplicate_conflict, duplicate_note = _detect_duplicate_local_conflict(save)

        if not save.save_exists:
            # ROM present, no local save, server has a save — always offer download
            status = "server_newer"
        elif duplicate_conflict:
            status = "local_duplicate_conflict"
        else:
            status = _determine_status(save.hash, server_hash, last_synced)
        results.append(
            SyncStatus(
                save=save,
                server_hash=server_hash,
                server_timestamp=server_ts,
                server_name=server_name,
                last_synced_hash=last_synced,
                status=status,
                mapping_note=duplicate_note
                or mapping_note
                or f"Using {resolution_source}: {save.title_id}",
            )
        )
        if idx == 1 or idx % 25 == 0 or idx == total:
            _emit_progress(
                progress_callback, f"Comparing with server… {idx}/{total}", idx, total
            )

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
        ps1_meta = _load_ps1_card_meta(tid, base_url, headers, timeout, ps1_meta_cache)
        if ps1_meta:
            server_hash = ps1_meta.get("save_hash", server_hash)
            server_ts = ps1_meta.get("server_timestamp", server_ts)
        gc_meta = _load_gc_card_meta(tid, base_url, headers, timeout, gc_meta_cache)
        if gc_meta:
            server_hash = gc_meta.get("save_hash", server_hash)
            server_ts = gc_meta.get("server_timestamp", server_ts)
        phantom = SaveFile(
            title_id=tid,
            path=None,
            hash="",
            mtime=0.0,
            system=system,
            game_name=name,
        )
        results.append(
            SyncStatus(
                save=phantom,
                server_hash=server_hash,
                server_timestamp=server_ts,
                server_name=name,
                status="server_only",
            )
        )

    _flush_slot_mappings()
    return results


def _load_ps1_card_meta(
    title_id: str,
    base_url: str,
    headers: dict,
    timeout: int,
    cache: dict[str, dict[str, str]],
) -> dict[str, str] | None:
    """Fetch raw-card metadata for PS1 titles.

    The generic `/api/v1/titles` index reports the PSP/Vita-visible save hash for
    PS1 titles, but desktop PS1 clients compare raw `.mcd` memory cards. Use the
    dedicated `ps1-card/meta` endpoint so MemCard Pro and DuckStation-style profiles
    compare like-for-like and do not appear perpetually out of date.
    """
    if title_id in cache:
        return cache[title_id]
    if not _normalize_ps1_serial(title_id):
        cache[title_id] = None
        return None
    try:
        resp = requests.get(
            f"{base_url}/api/v1/saves/{title_id}/ps1-card/meta",
            headers=headers,
            params={"slot": 0},
            timeout=timeout,
        )
        resp.raise_for_status()
        meta = resp.json()
        cache[title_id] = meta
        return meta
    except requests.RequestException:
        cache[title_id] = None
        return None


def _load_gc_card_meta(
    title_id: str,
    base_url: str,
    headers: dict,
    timeout: int,
    cache: dict[str, dict[str, str]],
) -> dict[str, str] | None:
    """Fetch GCI-based metadata for GC titles.

    The generic ``/titles`` index reports the hash of the stored file (which
    may be an 8 MB card image) but desktop GC profiles compare GCI bytes.
    Use the dedicated ``gc-card/meta`` endpoint so both desktop (card image)
    and Android (gci) profiles compare the same GCI-derived hash.
    """
    if title_id in cache:
        return cache[title_id]
    if not title_id.upper().startswith("GC_"):
        cache[title_id] = None
        return None
    try:
        resp = requests.get(
            f"{base_url}/api/v1/saves/{title_id}/gc-card/meta",
            headers=headers,
            timeout=timeout,
        )
        resp.raise_for_status()
        meta = resp.json()
        cache[title_id] = meta
        return meta
    except requests.RequestException:
        cache[title_id] = None
        return None


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
        return "local_newer"  # Only local changed
    if last_synced_hash == local_hash:
        return "server_newer"  # Only server changed
    return "conflict"  # Both changed


# ---------------------------------------------------------------------------
# Upload / download
# ---------------------------------------------------------------------------


def upload_save(
    title_id: str,
    path: Path,
    base_url: str,
    headers: dict,
    system: str | None = None,
    force: bool = False,
    timeout: int = 30,
) -> None:
    """Upload a local save file to the correct server endpoint.

    PS1 and PS2 memory-card clients use dedicated card endpoints so the server
    can convert formats as needed. PS1 regenerates PSP/Vita-compatible VMP
    files, while PS2 stores canonical `.mc2` and converts to `.ps2` on demand
    for PCSX2/Aether clients. PS3 save folders use a 3DSS directory bundle.
    Other systems still use `/raw`.
    """
    params = {"force": "true"} if force else {}
    is_ps3_dir = (system or "").upper() == "PS3" and path.is_dir()
    if is_ps3_dir:
        data = _create_dir_bundle(title_id, path, skip_names={"PARAM.PFD"})
        resp = requests.post(
            f"{base_url}/api/v1/saves/{title_id}",
            headers={**headers, "Content-Type": "application/octet-stream"},
            params=params,
            data=data,
            timeout=timeout,
        )
        local_hash = _hash_ps3_dir_files(path)
    else:
        saroo_payload = (
            _resolve_saroo_native_payload(title_id, path)
            if (system or "").upper() == "SAT"
            else None
        )
        if saroo_payload is not None:
            data = saroo_payload[0]
        else:
            data = path.read_bytes()
        local_hash = hashlib.sha256(data).hexdigest()
        if _should_use_ps1_card_endpoint(title_id, system):
            resp = requests.post(
                f"{base_url}/api/v1/saves/{title_id}/ps1-card",
                headers={**headers, "Content-Type": "application/octet-stream"},
                params=params,
                data=data,
                timeout=timeout,
            )
        elif _should_use_ps2_card_endpoint(system):
            resp = requests.post(
                f"{base_url}/api/v1/saves/{title_id}/ps2-card",
                headers={**headers, "Content-Type": "application/octet-stream"},
                params=params,
                data=data,
                timeout=timeout,
            )
        elif (system or "").upper() == "GC":
            resp = requests.post(
                f"{base_url}/api/v1/saves/{title_id}/gc-card",
                headers={**headers, "Content-Type": "application/octet-stream"},
                params={**params, "format": "raw"},
                data=data,
                timeout=timeout,
            )
        else:
            resp = requests.post(
                f"{base_url}/api/v1/saves/{title_id}/raw",
                headers={**headers, "Content-Type": "application/octet-stream"},
                params=params,
                data=data,
                timeout=timeout,
            )
    resp.raise_for_status()
    _update_state(title_id, local_hash)


def download_save(
    title_id: str,
    dest_path: Path,
    base_url: str,
    headers: dict,
    system: str | None = None,
    timeout: int = 30,
) -> str:
    """Download a save to dest_path and return the server-side hash.

    PS1 and PS2 memory-card clients use dedicated card endpoints so desktop
    profiles receive emulator/native card images instead of generic raw blobs.
    PS3 save folders use the bundle endpoint and are extracted into dest_path.
    """
    if (system or "").upper() == "PS3":
        resp = requests.get(
            f"{base_url}/api/v1/saves/{title_id}",
            headers=headers,
            timeout=timeout,
        )
    elif _should_use_ps1_card_endpoint(title_id, system):
        resp = requests.get(
            f"{base_url}/api/v1/saves/{title_id}/ps1-card",
            headers=headers,
            params={"slot": 0},
            timeout=timeout,
        )
    elif _should_use_ps2_card_endpoint(system):
        resp = requests.get(
            f"{base_url}/api/v1/saves/{title_id}/ps2-card",
            headers=headers,
            timeout=timeout,
        )
    elif (system or "").upper() == "GC":
        resp = requests.get(
            f"{base_url}/api/v1/saves/{title_id}/gc-card",
            headers=headers,
            params={"format": "raw"},
            timeout=timeout,
        )
    else:
        resp = requests.get(
            f"{base_url}/api/v1/saves/{title_id}/raw",
            headers=headers,
            timeout=timeout,
        )
    resp.raise_for_status()
    if (system or "").upper() == "PS3":
        _extract_bundle_to_dir(resp.content, dest_path)
        server_hash = resp.headers.get("X-Save-Hash", _hash_ps3_dir_files(dest_path))
    else:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(resp.content)
        server_hash = resp.headers.get(
            "X-Save-Hash", hashlib.sha256(resp.content).hexdigest()
        )
    _update_state(title_id, server_hash)
    return server_hash
