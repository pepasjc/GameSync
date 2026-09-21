from __future__ import annotations

import os
import re
import shutil
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable

import requests

from config import get_api_headers, get_base_url, load_config
from openmenu_image import GDI_LINE_RE
from systems import (
    MISTER_CD_SYSTEMS,
    MISTER_GAMES_ROOTS,
    mister_system_folder_candidates,
)
# ``systems`` has already put the repo root on sys.path.
from shared import msu  # noqa: E402
from shared.mister_install import msu_pack_layout  # noqa: E402


ROM_FORMAT_OPTIONS: list[tuple[str, str]] = [
    ("auto", "Auto"),
    ("raw", "Raw"),
    ("cue", "CUE/BIN"),
    ("psio", "PSIO BIN/CU2"),
    ("gdi", "GDI"),
    ("iso", "ISO"),
    ("cso", "CSO"),
    ("cia", "CIA"),
    ("decrypted_cci", "Decrypted CCI"),
    ("eboot", "PSP EBOOT.PBP"),
    ("vcd", "PS1 VCD (POPStarter)"),
    ("cci", "Xbox CCI"),
    ("folder", "Extracted Folder"),
]
ROM_FORMAT_LABELS = dict(ROM_FORMAT_OPTIONS)
MSU_PACK_LABELS = {msu.MSU1: "MSU-1 pack", msu.MSU_MD: "MSU-MD pack", msu.MD_PLUS: "MD+ pack"}

ARCHIVE_EXTRACT_FORMATS = {"cue", "psio", "gdi", "cci", "folder"}
# Formats where the server stitches every disc of a multi-disc game into a
# single output (one PSIO BIN/CU2 set, one multi-disc EBOOT.PBP).  For these
# we collapse the per-disc catalog rows into one installable entry; for raw /
# cue / chd each disc stays its own file.
COMBINED_DISC_FORMATS = {"psio", "eboot"}
_DISC_TAG_RE = re.compile(
    r"\s*[\(\[]\s*Dis[ck]\s*\d+(?:\s*of\s*\d+)?\s*[\)\]]", re.IGNORECASE
)
_BRACKET_TAG_RE = re.compile(r"\s*\[[^\]]*\]")
DISC_CUE_SYSTEMS = {
    "PS1",
    "PSX",
    "SAT",
    "SEGACD",
    "SCD",
    "PCECD",
    "TGCD",
    "NEOCD",
    "NGCD",
    "3DO",
    "PCFX",
    "JAGCD",
    "AMIGACD32",
}
DISC_ISO_SYSTEMS = {"PS2", "PSP", "GC", "WII", "XBOX", "X360", "XBOX360"}
EMULATOR_DEVICE_TYPES = {"RetroArch", "EmuDeck"}
HARDWARE_3DS_DEVICE_TYPES = {"Generic", "Everdrive", "CD Folder"}
XBOX_SYSTEMS = {"XBOX", "X360", "XBOX360"}
OPL_SYSTEMS = {"PS1", "PS2"}

# Super SD System 3 (TerraOnion PC Engine ODE): a fixed card layout, not the
# EmuDeck-style per-system folders.  HuCard dumps go in HuCard/ as loose files;
# each CD game gets its own Cd/<Game>/ folder holding a CUE/BIN set.
SUPERSD3_DEVICE = "Super SD System 3"
SUPERSD3_CD_SYSTEMS = {"PCECD", "TGCD"}
SUPERSD3_ROM_SUBDIRS: dict[str, str] = {
    "PCE": "HuCard",
    "PCSG": "HuCard",
    "PCECD": "Cd",
    "TGCD": "Cd",
}

# PSIO's menu refuses filenames > 60 chars and cannot render non-ASCII bytes.
# We keep folder + member names within this limit for PSIO installs.
PSIO_MAX_NAME = 60

# ── GDEMU / openMenu (Dreamcast ODE) ────────────────────────────────────────
#
# GDEMU reads games from plain numbered folders at the SD card root — 01, 02,
# 03 — one disc image per folder, with 01 reserved for the menu disc (GDMENU or
# openMenu).  Managers write a name.txt in each folder holding the display
# name; GDEMU ignores it, the menus and every other manager read it.  openMenu
# uses the same card layout, so both profiles install identically.
#
# GDEMU cannot read CHD, so a catalog CHD is always fetched as a converted GDI
# set (see ``default_rom_format``).  Layout reference:
# https://github.com/sonik-br/GDMENUCardManager
GDEMU_DEVICE_TYPES = {"GDEMU", "openMenu"}
GDEMU_MENU_FOLDER = "01"
GDEMU_NAME_FILE = "name.txt"


ROM_SUBDIRS: dict[str, list[str]] = {
    "PS1": ["psx", "PSX", "PS1", "ps1", "PlayStation"],
    "PS2": ["ps2", "PS2"],
    "PS3": ["ps3", "PS3"],
    "PSP": ["psp", "PSP"],
    "GBA": ["gba", "GBA"],
    "GB": ["gb", "GB"],
    "GBC": ["gbc", "GBC"],
    "NES": ["nes", "NES"],
    "FDS": ["fds", "FDS"],
    "SNES": ["snes", "SNES"],
    "N64": ["n64", "N64"],
    "NDS": ["nds", "NDS"],
    "3DS": ["3ds", "n3ds", "Nintendo 3DS"],
    "GC": ["gc", "GC", "GameCube"],
    "WII": ["wii", "Wii"],
    "MD": ["megadrive", "genesis", "MD"],
    "SEGACD": ["segacd", "megacd", "Sega CD"],
    "SMS": ["mastersystem", "SMS"],
    "GG": ["gamegear", "GG"],
    "SAT": ["saturn", "Saturn"],
    "DC": ["dreamcast", "dc", "Dreamcast"],
    "32X": ["sega32x", "32x", "32X"],
    "PCE": ["pcengine", "tg16", "PCE"],
    "PCECD": ["pcenginecd", "tgcd", "PCECD"],
    "NEOGEO": ["neogeo", "NeoGeo"],
    "NGP": ["ngp", "NGP"],
    "NGPC": ["ngpc", "NGPC"],
    "WSWAN": ["wonderswan", "WSWAN"],
    "WSWANC": ["wonderswancolor", "WSWANC"],
    "A2600": ["atari2600", "A2600"],
    "A7800": ["atari7800", "A7800"],
    "LYNX": ["lynx", "Lynx"],
    "MAME": ["mame", "MAME", "arcade"],
    "ARCADE": ["arcade", "mame"],
    "XBOX": ["xbox", "ogxbox", "XBOX"],
}


@dataclass(frozen=True)
class InstallPlan:
    rom_id: str
    display_name: str
    system: str
    source_filename: str
    target_path: Path
    extract_format: str | None
    extract_archive: bool = False
    target_is_directory: bool = False
    # PS1 → OPL POPStarter (Applications-menu method): after writing the .VCD,
    # drop a renamed POPSTARTER.ELF launcher beside it and register the game in
    # the USB-root conf_apps.cfg so it shows in OPL's Applications menu.
    opl_popstarter: bool = False
    # MiSTer network install: "sd" or "usb" storage key.  When set,
    # ``target_path`` is a POSIX path on the MiSTer and the install goes over
    # SSH/SFTP using ``mister_ssh`` (profile SSH fields; legacy global
    # ``mister_ssh`` config as fallback).
    mister_remote: str = ""
    mister_ssh: dict | None = None
    # GDEMU / openMenu: display name to write into the folder's name.txt after
    # the image lands.  Empty for every other device.
    gdemu_name: str = ""
    # MSU pack (``shared/msu.py`` kind) being unpacked, and the name its
    # cartridge must take on the target — MiSTer's MegaCD core wants an
    # MSU-MD cart as ``cart.rom``.  Empty / None for everything else.
    bundle_kind: str = ""
    rom_rename: str | None = None

    @property
    def format_label(self) -> str:
        if self.bundle_kind:
            label = MSU_PACK_LABELS.get(self.bundle_kind, self.bundle_kind)
            return f"{label} (MegaCD)" if self.rom_rename else label
        return ROM_FORMAT_LABELS.get(self.extract_format or "raw", "Raw")


def fetch_rom_catalog(system: str = "", search: str = "") -> list[dict]:
    params: dict[str, str | int] = {"limit": 20000}
    if system:
        params["system"] = system.upper()
    if search:
        params["search"] = search
    resp = requests.get(
        f"{get_base_url()}/api/v1/roms",
        headers=get_api_headers(),
        params=params,
        timeout=30,
    )
    resp.raise_for_status()
    return list(resp.json().get("roms", []))


_catalog_cache: dict[str, list[dict]] = {}


def catalog_for_system(system: str, refresh: bool = False) -> list[dict]:
    """Catalog rows for one system, fetched once per process.

    The Sync tab asks per system while deciding which saves have an
    installable game, so the same list would otherwise be pulled repeatedly.
    """
    key = (system or "").upper()
    if refresh:
        _catalog_cache.pop(key, None)
    if key not in _catalog_cache:
        _catalog_cache[key] = fetch_rom_catalog(key)
    return _catalog_cache[key]


def clear_catalog_cache() -> None:
    _catalog_cache.clear()


def index_catalog_by_title(roms: Iterable[dict]) -> dict[str, list[dict]]:
    """``{title_id: [rom rows]}`` — several rows share one title_id when a
    game has multiple discs or several dumps (translations, revisions)."""
    index: dict[str, list[dict]] = {}
    for rom in roms:
        title_id = str(rom.get("title_id") or "").strip()
        if title_id:
            index.setdefault(title_id, []).append(rom)
    return index


def catalog_roms_for_title(system: str, title_id: str) -> list[dict]:
    """Catalog rows whose title_id matches a save's, i.e. the game itself."""
    title_id = str(title_id or "").strip()
    if not title_id:
        return []
    return index_catalog_by_title(catalog_for_system(system)).get(title_id, [])


def catalog_install_groups(
    profile: dict,
    roms: list[dict],
    system: str,
    override_format: str = "",
) -> list[dict]:
    """Installable entries for one title: one per distinct dump.

    Multi-disc sets collapse into a single entry (every disc installs
    together); different dumps of the same game — a translation patch and
    the original, say — stay separate so the caller can pick one.
    """
    return group_multidisc_roms(profile, roms, system, override_format)


def build_title_install_plans(
    profile: dict,
    rom: dict,
    system: str,
    override_format: str = "",
) -> list[InstallPlan]:
    """Every plan needed to install one catalog entry (all of its discs)."""
    return build_install_plans(profile, rom, system, override_format)


def profile_can_install(profile: dict) -> bool:
    """True when a profile has somewhere to put a ROM."""
    if not isinstance(profile, dict):
        return False
    if str(profile.get("path") or "").strip():
        return True
    return bool(mister_remote_target(profile))


def profile_systems(profile: dict) -> list[str]:
    if "systems" in profile:
        systems = [
            str(s.get("system", "")).upper()
            for s in profile.get("systems", [])
            if s.get("enabled", True) and str(s.get("system", "")).strip()
        ]
    else:
        system = str(profile.get("system", "")).upper()
        systems = [system] if system else []
    # OPL is a PS1/PS2 USB loader — always offer both in the ROM Installer
    # regardless of the per-system enabled toggles, so the user never has to
    # flip a setting to install PS1/PS2 onto an OPL drive.
    if str(profile.get("device_type", "")).strip() == "OPL":
        for sys_id in ("PS1", "PS2"):
            if sys_id not in systems:
                systems.append(sys_id)
    return systems


def system_profile_info(profile: dict, system: str) -> dict:
    system = system.upper()
    for entry in profile.get("systems", []) or []:
        if str(entry.get("system", "")).upper() == system:
            return entry
    return {}


def profile_rom_format(profile: dict, system: str) -> str:
    info = system_profile_info(profile, system)
    return str(info.get("rom_format") or profile.get("rom_format") or "auto").strip().lower() or "auto"


def default_rom_format(profile: dict, rom: dict, system: str) -> str | None:
    system_up = (system or rom.get("system") or "").upper()
    device_type = str(profile.get("device_type", "Generic"))
    source_ext = Path(str(rom.get("filename") or "")).suffix.lower()
    advertised = {
        str(v).strip().lower()
        for v in (rom.get("extract_formats") or [])
        if str(v).strip()
    }
    legacy = str(rom.get("extract_format") or "").strip().lower()

    if system_up == "3DS":
        if device_type in EMULATOR_DEVICE_TYPES:
            return "decrypted_cci"
        if device_type in HARDWARE_3DS_DEVICE_TYPES or "cia" in advertised:
            return "cia"
        return "decrypted_cci" if "decrypted_cci" in advertised else None

    if device_type == "PSIO" and system_up in {"PS1", "PSX"}:
        return "psio"

    # OPL: PS1 → POPStarter VCD, PS2 → ISO (both CD and DVD media).
    if device_type == "OPL":
        if system_up == "PS1":
            return "vcd"
        if system_up == "PS2":
            return "iso"

    # MiSTer CD cores (PSX, Saturn, MegaCD, TGFX16-CD, NeoGeo-CD, ...) read
    # CHD natively — install the catalog file as-is, no conversion.
    if device_type == "MiSTer" and source_ext == ".chd":
        return None

    # GDEMU / openMenu read GDI (and CDI) only.  A CHD must be converted; a
    # loose .cdi / .gdi set installs as-is.
    if device_type in GDEMU_DEVICE_TYPES and system_up == "DC":
        return "gdi" if source_ext == ".chd" else (legacy or None)

    if system_up in XBOX_SYSTEMS:
        return "iso"

    if source_ext == ".rvz" and system_up in {"GC", "WII"}:
        return "iso"

    if source_ext != ".chd":
        if legacy in {"cue", "gdi", "iso", "cso", "rvz", "eboot"}:
            return legacy
        return None

    if system_up == "DC":
        return "gdi"
    if system_up in DISC_ISO_SYSTEMS:
        return "iso"
    if system_up in DISC_CUE_SYSTEMS:
        return "cue"
    if legacy:
        return legacy
    return None


def choose_extract_format(
    profile: dict,
    rom: dict,
    system: str,
    override_format: str = "",
) -> str | None:
    selected = (override_format or profile_rom_format(profile, system)).strip().lower()
    if selected in {"", "auto"}:
        return default_rom_format(profile, rom, system)
    if selected == "raw":
        return None
    return selected


def strip_disc_tag(name: str) -> str:
    """Drop ``(Disc N)`` / ``(Disk N of M)`` tokens from a game name."""
    cleaned = _DISC_TAG_RE.sub("", str(name or ""))
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def _ascii_only(value: str) -> str:
    """Best-effort ASCII: decompose accents, drop combining marks, drop the rest.

    PSIO cannot render non-ASCII bytes; NFKD salvages accented Latin names
    (``Pokémon`` -> ``Pokemon``) while remaining CJK / symbol bytes are dropped.
    """
    decomposed = unicodedata.normalize("NFKD", str(value or ""))
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.encode("ascii", "ignore").decode("ascii")


def clean_ps1_title(name: str) -> str:
    """PSIO install folder name: drop disc tags and ``[..]`` tags (translation /
    dump flags) but keep region parens like ``(USA)``.  Strips non-ASCII bytes
    and caps at ``PSIO_MAX_NAME`` so PSIO's menu can list the folder.  Mirrors
    the on-disk PSIO convention where the game folder is the bare title."""
    cleaned = _DISC_TAG_RE.sub("", str(name or ""))
    cleaned = _BRACKET_TAG_RE.sub("", cleaned)
    cleaned = _ascii_only(cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ._")
    if len(cleaned) > PSIO_MAX_NAME:
        cleaned = cleaned[:PSIO_MAX_NAME].rstrip(" ._")
    return cleaned or "game"


def group_multidisc_roms(
    profile: dict,
    roms: list[dict],
    system: str,
    override_format: str = "",
) -> list[dict]:
    """Collapse multi-disc PS1 groups into one catalog entry per game.

    Two cases collapse:

    * the effective install format produces a single combined output (see
      ``COMBINED_DISC_FORMATS``) — the merged entry keeps the group's
      ``primary_rom_id`` as its ``rom_id`` and one request returns the whole
      set (the server groups siblings by title_id);
    * folder-per-game CD devices (MiSTer CD cores, Super SD System 3) — each
      disc stays its own file, but they install side by side into one
      disc-tag-free game folder.  The entry is flagged ``install_members``
      so the installer expands it back into one plan per disc.

    A synthetic ``disc_members`` list and ``disc_total`` are attached for
    display.  Single-disc / non-collapsing rows pass through unchanged and
    keep their original order.
    """
    result: list[dict] = []
    aggregates: dict[str, dict] = {}
    device_type = str(profile.get("device_type", "")).strip()
    system_up = (system or "").upper()
    # Devices that install each disc as its own file into one shared game folder.
    cd_folder_device = (
        device_type == "MiSTer" and system_up in MISTER_CD_SYSTEMS
    ) or (device_type == SUPERSD3_DEVICE and system_up in SUPERSD3_CD_SYSTEMS)
    for rom in roms:
        try:
            fmt = choose_extract_format(profile, rom, system, override_format)
        except Exception:
            fmt = None
        total = int(rom.get("disc_total") or 1)
        primary = str(rom.get("primary_rom_id") or "")
        has_disc_tag = bool(
            _DISC_TAG_RE.search(str(rom.get("filename") or rom.get("name") or ""))
        )
        combined = fmt in COMBINED_DISC_FORMATS
        # The server only computes disc groups (primary_rom_id / disc_total)
        # for PS1, so a Saturn or Mega CD set arrives as unrelated rows.  For
        # a device that installs discs side by side, the disc tag in the
        # filename is enough: rows of one title whose names match once the
        # tag is stripped are the same game.  Different dumps keep different
        # stripped names, so they stay apart.
        group_key = primary
        if cd_folder_device and has_disc_tag and not (total > 1 and primary):
            title_key = str(rom.get("title_id") or "").strip()
            stripped = strip_disc_tag(
                Path(str(rom.get("filename") or rom.get("name") or "")).stem
            )
            if title_key and stripped:
                group_key = f"{title_key}|{stripped}"
        if (combined or cd_folder_device) and has_disc_tag and group_key:
            primary = group_key
            agg = aggregates.get(primary)
            if agg is None:
                agg = dict(rom)
                agg["rom_id"] = primary
                agg["disc_members"] = []
                if not combined:
                    # Discs install individually into the shared game folder.
                    agg["install_members"] = True
                aggregates[primary] = agg
                result.append(agg)
            agg["disc_members"].append(rom)
        else:
            result.append(rom)

    for agg in aggregates.values():
        # Systems without server-side disc metadata have no disc_index, so
        # fall back to the filename to keep Disc 1 first.
        members = sorted(
            agg["disc_members"],
            key=lambda r: (
                int(r.get("disc_index") or 0),
                str(r.get("filename") or ""),
            ),
        )
        primary_member = next(
            (m for m in members if str(m.get("rom_id")) == str(agg["rom_id"])),
            members[0],
        )
        if agg.get("install_members"):
            # The group key is synthetic for filename-grouped sets; keep a
            # real rom_id on the row so anything reading it still works.
            agg["rom_id"] = primary_member.get("rom_id") or agg["rom_id"]
        agg["disc_members"] = members
        agg["disc_total"] = len(members)
        agg["size"] = sum(int(m.get("size") or 0) for m in members)
        agg["filename"] = primary_member.get("filename") or agg.get("filename")
        agg["name"] = strip_disc_tag(
            primary_member.get("name") or primary_member.get("filename") or ""
        )
    return result


def derive_download_filename(filename: str, extract_format: str | None) -> str:
    fmt = (extract_format or "").strip().lower()
    if not fmt:
        return filename

    stem = filename
    lower = stem.lower()
    if lower.endswith(".zip"):
        stem = stem[:-4]
        lower = stem.lower()
    for ext in (".3ds", ".cci", ".cia", ".chd", ".rvz", ".iso", ".cso", ".cue", ".gdi", ".bin", ".img"):
        if lower.endswith(ext):
            stem = stem[: -len(ext)]
            break

    if fmt == "decrypted_cci":
        return f"{stem}.cci"
    if fmt == "cia":
        return f"{stem}.cia"
    if fmt in {"iso", "rvz"}:
        return f"{stem}.iso"
    if fmt == "cso":
        return f"{stem}.cso"
    if fmt == "gdi":
        return f"{stem}.gdi"
    if fmt == "cue":
        return f"{stem}.cue"
    if fmt == "psio":
        return f"{stem}.cu2"
    if fmt == "eboot":
        return "EBOOT.PBP"
    if fmt == "vcd":
        return f"{stem}.vcd"
    if fmt == "cci":
        return f"{stem}.cci"
    return filename


# Extensions ``safe_folder_name`` drops when it's handed a filename.  An
# explicit list — ``Path.stem`` alone truncates any trailing dot group, which
# mangles version/translation tags like ``[T-En … v1.1]`` into ``[T-En … v1``.
_FOLDER_NAME_STRIP_EXTS = frozenset(
    {
        ".chd", ".cue", ".bin", ".iso", ".img", ".mdf", ".gdi", ".ccd", ".sub",
        ".zip", ".7z", ".rar", ".rvz", ".wbfs", ".cso", ".wua", ".wux",
        ".nes", ".sfc", ".smc", ".md", ".gen", ".gg", ".sms", ".pce", ".gba",
        ".gb", ".gbc", ".n64", ".z64", ".v64", ".nds", ".3ds", ".cci", ".cia",
        ".exe", ".pbp", ".vcd", ".cu2",
    }
)


def safe_folder_name(value: str) -> str:
    """Sanitize a game name (or filename) for use as a folder name.

    A trailing extension is dropped only when it is a recognised ROM/disc
    extension, so names ending in a version tag keep their final segment.
    """
    text = str(value or "download").strip()
    suffix = Path(text).suffix
    if suffix.lower() in _FOLDER_NAME_STRIP_EXTS:
        text = text[: -len(suffix)]
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text.strip())
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text or "download"


def safe_file_name(value: str) -> str:
    """Reduce a server-supplied filename to a safe basename (keeps extension).

    Strips any directory components so a crafted catalog ``filename`` (e.g.
    ``../../x.rom``) can't escape the resolved target folder.
    """
    base = Path(str(value or "").replace("\\", "/")).name.strip()
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", base).strip(" .")
    return base or "download.rom"


def _existing_child_dir(root: Path, name: str) -> Path:
    """``root/name``, matched case-insensitively against existing children."""
    direct = root / name
    if direct.is_dir():
        return direct
    try:
        for entry in os.scandir(root):
            if entry.is_dir() and entry.name.lower() == name.lower():
                return Path(entry.path)
    except OSError:
        pass
    return direct


def _system_subdir(root: Path, system: str, device_type: str) -> Path:
    system_up = system.upper()
    if device_type == SUPERSD3_DEVICE:
        name = SUPERSD3_ROM_SUBDIRS.get(system_up)
        if name:
            return _existing_child_dir(root, name)

    if device_type == "MiSTer":
        mister_candidates = mister_system_folder_candidates(system_up)
        for name in mister_candidates:
            candidate = root / name
            if candidate.is_dir():
                return candidate
        if mister_candidates:
            return root / mister_candidates[0]

    candidates = ROM_SUBDIRS.get(system_up, [system_up.lower(), system_up])
    for name in candidates:
        candidate = root / name
        if candidate.is_dir():
            return candidate

    if device_type in {"EmuDeck", "MiSTer"}:
        return root / candidates[0]
    return root


def mister_remote_target(profile: dict) -> str:
    """``"sd"`` / ``"usb"`` when this MiSTer profile installs over the network,
    ``""`` for the classic local-folder (mounted card) mode."""
    if str(profile.get("device_type", "")).strip() != "MiSTer":
        return ""
    target = str(profile.get("mister_target", "") or "").strip().lower()
    return target if target in MISTER_GAMES_ROOTS else ""


def mister_remote_rom_dir(profile: dict, system: str) -> PurePosixPath:
    """POSIX game folder on the MiSTer for a network install.

    A per-system ROM-folder override starting with ``/`` is honored as a
    remote path; otherwise ``<games root>/<core folder>``.  The folder is
    created on the MiSTer at install time.
    """
    info = system_profile_info(profile, system)
    override = str(info.get("rom_folder", "")).strip()
    if override.startswith("/"):
        return PurePosixPath(override)
    root = PurePosixPath(MISTER_GAMES_ROOTS[mister_remote_target(profile)])
    candidates = mister_system_folder_candidates(system)
    if not candidates:
        # No core folder for this system — refuse rather than dumping the
        # file loose in the games root, where no core would ever find it.
        raise ValueError(f"MiSTer has no games folder for {system or 'this system'}.")
    return root / candidates[0]


def resolve_profile_rom_folder(profile: dict, system: str) -> Path:
    info = system_profile_info(profile, system)
    override = str(info.get("rom_folder", "")).strip()
    if override:
        return Path(override).expanduser()

    base = Path(str(profile.get("path") or ".")).expanduser()
    device_type = str(profile.get("device_type", ""))
    if profile.get("systems") or device_type == SUPERSD3_DEVICE:
        return _system_subdir(base, system, device_type)
    return base


# ── OPL (Open PS2 Loader) install layout ────────────────────────────────────
#
# OPL reads PS2 games from ``DVD/`` (DVD-ROM) or ``CD/`` (CD-ROM) folders on a
# FAT32 USB drive, and PS1 games (via POPStarter) from ``POPS/``.  Filenames
# use the Sony disc serial in an OPL-parsed form: ``SLPS_204.36`` — the first
# four letters, an underscore, then the 5-digit number split as ``DDD.DD``.
# OPL treats the dot-separated tokens as ``<startup>.<display name>.<ext>``
# for PS2, so PS2 keeps the game name: ``SLPS_204.36.Game Name.iso``.  PS1
# POPStarter VCDs use the SAME scheme: ``SLUS_012.34.Game Name.VCD``.  Modern
# OPL (136_DB-TA and newer) auto-lists VCDs from POPS/ in its PS1 page with no
# config file, but it *requires* the ``SERIAL.Name.VCD`` form — a serial-only
# name is ignored.  POPStarter then creates the per-game VMC in a folder named
# after the full VCD filename, so the name token is needed there too.

_SERIAL_RE = re.compile(r"^[A-Z]{4}\d{5}$")


def opl_disc_id(serial: str | None) -> str:
    """Convert a Sony PS1/PS2 disc serial to the OPL filename prefix.

    ``SLPS20436`` / ``SLPS-20436`` / ``slps_204.36`` → ``SLPS_204.36``.
    Returns ``""`` when the value doesn't resolve to a 4-letter + 5-digit
    Sony code (so callers can fall back to the bare game name).
    """
    compact = re.sub(r"[^A-Za-z0-9]", "", str(serial or "")).upper()
    if _SERIAL_RE.match(compact):
        letters = compact[:4]
        digits = compact[4:]
        return f"{letters}_{digits[:3]}.{digits[3:]}"
    return ""


def opl_ps2_media(rom: dict) -> str:
    """Return ``"CD"`` or ``"DVD"`` for a PS2 catalog row.

    The server's native ``extract_format`` recommendation encodes the media:
    DVD CHDs advertise ``iso``, CD CHDs advertise ``cue``.  Defaults to DVD
    (the overwhelming majority of PS2 titles) when the catalog can't tell us.
    """
    native = str(rom.get("extract_format") or "").strip().lower()
    if native == "cue":
        return "CD"
    return "DVD"


def clean_opl_name(name: str) -> str:
    """Sanitize a game name for the OPL filename middle token.

    Keeps spaces (OPL displays them) but strips characters illegal on FAT32
    and collapses ``(Disc N)`` tags (each disc is its own file).
    """
    cleaned = strip_disc_tag(str(name or ""))
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned or "game"


def _build_opl_install_plan(
    profile: dict,
    rom: dict,
    system_up: str,
    override_format: str,
) -> InstallPlan:
    rom_id = str(rom.get("rom_id") or rom.get("title_id") or "")
    if not rom_id:
        raise ValueError("Catalog entry is missing a ROM id.")
    filename = safe_file_name(str(rom.get("filename") or f"{rom_id}.rom"))
    extract = choose_extract_format(profile, rom, system_up, override_format)
    target_root = resolve_profile_rom_folder(profile, system_up)
    display_name = str(rom.get("name") or Path(filename).stem or rom_id)
    serial_source = str(rom.get("title_id") or rom_id)
    disc_id = opl_disc_id(serial_source)
    name = clean_opl_name(display_name)

    if system_up == "PS1":
        # PS1 → POPStarter .VCD in POPS/.  Extension is UPPERCASE: POPStarter
        # matches its ``XX.<name>.ELF`` launcher to ``<name>.VCD`` case-
        # sensitively, and a lowercase ``.vcd`` isn't found (it then falls back
        # to uLaunchELF).  Name uses the SERIAL.Name scheme so the launcher and
        # VCD share a base; the APPS/ app folder gives the display name.
        folder = "POPS"
        ext = "VCD"
        if extract is None:
            extract = "vcd"
    else:  # PS2 — DVD/ vs CD/ by media, both served as .iso
        folder = "CD" if opl_ps2_media(rom) == "CD" else "DVD"
        ext = "iso"
        if extract is None:
            extract = "iso"
    # OPL parses ``<startup>.<display name>.<ext>``; serial first so it boots
    # correctly, name second for the OPL game list.
    if disc_id:
        target_path = target_root / folder / f"{disc_id}.{name}.{ext}"
    else:
        target_path = target_root / folder / f"{name}.{ext}"

    return InstallPlan(
        rom_id=rom_id,
        display_name=display_name,
        system=system_up,
        source_filename=filename,
        target_path=target_path,
        extract_format=extract,
        opl_popstarter=(system_up == "PS1"),
    )


def _find_popstarter_elf(pops_dir: Path) -> Path | None:
    """Locate POPSTARTER.ELF in the POPS folder, case-insensitively.

    The user supplies POPSTARTER.ELF (+ POPS.ELF / IOPRP) once; we copy it per
    game.  FAT32 is case-insensitive but the host OS may not be.
    """
    try:
        for entry in pops_dir.iterdir():
            if entry.is_file() and entry.name.lower() == "popstarter.elf":
                return entry
    except OSError:
        return None
    return None


# OPL's Apps page auto-scans ``APPS/<folder>/`` subfolders that contain a
# ``title.cfg`` on each game device (USB) — independent of which device holds
# OPL's main config.  So each PS1 game gets its own APPS subfolder with a
# renamed POPSTARTER.ELF + a title.cfg; no shared conf_apps.cfg, no memory-card
# dependency.  The folder is named after the VCD stem (serial-prefixed) so our
# folders are easy to tell apart from the user's own apps when pruning.
#
# POPStarter matches its launcher ELF to a VCD purely by filename, but on USB
# the launcher MUST carry an ``XX.`` prefix (SMB uses ``SB.``, HDD none) — it
# strips that prefix, then loads ``<rest>.VCD`` from POPS/.  Without the prefix
# POPStarter doesn't treat it as a POPS launch and falls back to uLaunchELF.
OPL_POPSTARTER_USB_PREFIX = "XX."


def _popstarter_launcher_name(vcd_stem: str) -> str:
    return f"{OPL_POPSTARTER_USB_PREFIX}{vcd_stem}.ELF"


def _title_cfg_bytes(title: str, boot_name: str) -> bytes:
    safe_title = re.sub(r"[\r\n]", " ", str(title)).strip() or boot_name
    return f"title={safe_title}\nboot={boot_name}\n".encode("utf-8")


def _install_popstarter_app(vcd_path: Path, display_name: str) -> list[Path]:
    """Make a PS1 VCD show + launch from OPL's Apps page.

    Creates ``APPS/<vcd_stem>/`` containing a renamed POPSTARTER.ELF (POPStarter
    loads the VCD in POPS/ whose name matches its own ELF filename) and a
    ``title.cfg`` giving the display name.  The VCD itself stays in POPS/.
    Skips silently if POPSTARTER.ELF isn't in the POPS folder.
    """
    written: list[Path] = []
    pops_dir = vcd_path.parent
    src = _find_popstarter_elf(pops_dir)
    if src is None:
        return written
    base = vcd_path.stem  # SERIAL.Name
    app_dir = pops_dir.parent / "APPS" / base
    try:
        app_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return written
    launcher = app_dir / _popstarter_launcher_name(base)  # XX.<stem>.ELF (USB)
    try:
        shutil.copy2(src, launcher)
    except OSError:
        return written
    written.append(launcher)

    title_cfg = app_dir / "title.cfg"
    label = _opl_display_from_vcd(vcd_path) if not str(display_name).strip() else display_name
    try:
        title_cfg.write_bytes(_title_cfg_bytes(label, launcher.name))
        written.append(title_cfg)
    except OSError:
        pass
    return written


# ── GDEMU / openMenu install layout ─────────────────────────────────────────


def _gdemu_folder_width(number: int) -> int:
    """GDEMU folder names are zero-padded to two digits until they can't be."""
    return 2 if number < 100 else len(str(number))


def gdemu_folder_name(number: int) -> str:
    return str(number).zfill(_gdemu_folder_width(number))


def _gdemu_numbered_dirs(root: Path) -> dict[int, Path]:
    """``{folder number: path}`` for every numeric folder at the card root."""
    found: dict[int, Path] = {}
    try:
        entries = list(os.scandir(root))
    except OSError:
        return found
    for entry in entries:
        if not entry.is_dir() or not entry.name.isdigit():
            continue
        found[int(entry.name)] = Path(entry.path)
    return found


def gdemu_installed_name(folder: Path) -> str:
    """Display name recorded in a game folder's ``name.txt`` (``""`` if none)."""
    try:
        for entry in os.scandir(folder):
            if entry.is_file() and entry.name.lower() == GDEMU_NAME_FILE:
                return (
                    Path(entry.path)
                    .read_text(encoding="utf-8", errors="ignore")
                    .strip()
                )
    except OSError:
        pass
    return ""


def gdemu_target_folder(root: Path, display_name: str) -> Path:
    """Folder this game installs into: its existing one, else the next number.

    Reinstalling a game reuses the folder it already occupies (matched on
    ``name.txt``) so the menu keeps its position.  New games take the first
    number above every folder present — never ``01``, which holds the menu
    disc, and never a gap, so the numbering stays contiguous for managers that
    assume it.
    """
    numbered = _gdemu_numbered_dirs(root)
    wanted = safe_folder_name(display_name).casefold()
    for number, path in sorted(numbered.items()):
        if number == int(GDEMU_MENU_FOLDER):
            continue
        existing = gdemu_installed_name(path)
        if existing and safe_folder_name(existing).casefold() == wanted:
            return path
    next_number = max([*numbered, int(GDEMU_MENU_FOLDER)]) + 1
    return root / gdemu_folder_name(next_number)


def _build_gdemu_install_plan(
    profile: dict,
    rom: dict,
    system_up: str,
    override_format: str,
) -> InstallPlan:
    """Install one Dreamcast disc into its own numbered folder on the ODE card.

    Multi-disc games are *not* collapsed: GDEMU boots one image per folder, so
    each disc keeps its own folder (and its own ``(Disc N)`` name.txt label).
    """
    rom_id = str(rom.get("rom_id") or rom.get("title_id") or "")
    if not rom_id:
        raise ValueError("Catalog entry is missing a ROM id.")
    filename = safe_file_name(str(rom.get("filename") or f"{rom_id}.rom"))
    extract = choose_extract_format(profile, rom, system_up, override_format)
    display_name = str(rom.get("name") or Path(filename).stem or rom_id)
    root = resolve_profile_rom_folder(profile, system_up)
    target_dir = gdemu_target_folder(root, display_name)

    if extract in ARCHIVE_EXTRACT_FORMATS or bool(rom.get("is_bundle")):
        # A GDI is a sheet plus its tracks — the whole set lands in the folder.
        return InstallPlan(
            rom_id=rom_id,
            display_name=display_name,
            system=system_up,
            source_filename=filename,
            target_path=target_dir,
            extract_format=extract,
            extract_archive=True,
            target_is_directory=True,
            gdemu_name=display_name,
        )

    # Single-file image (.cdi, or a .gdi already stored flat in the catalog).
    return InstallPlan(
        rom_id=rom_id,
        display_name=display_name,
        system=system_up,
        source_filename=filename,
        target_path=target_dir / derive_download_filename(filename, extract),
        extract_format=extract,
        gdemu_name=display_name,
    )


def _write_gdemu_name_file(folder: Path, display_name: str) -> Path | None:
    """Write ``name.txt`` so GDMENU / openMenu (and other managers) can label
    the folder.  Best-effort: a read-only card must not fail the install."""
    name = str(display_name or "").strip()
    if not name:
        return None
    path = folder / GDEMU_NAME_FILE
    try:
        folder.mkdir(parents=True, exist_ok=True)
        path.write_text(name + "\n", encoding="utf-8")
    except OSError:
        return None
    return path


# Metadata caches GD MENU Card Manager keeps beside each game so it needn't
# re-read the disc header.  Writing the same files means the manager (and our
# own list generator) sees a GameSync install exactly as one of its own.
GDEMU_META_FILES = (
    "name.txt",
    "serial.txt",
    "disc.txt",
    "region.txt",
    "version.txt",
    "date.txt",
    "vga.txt",
    "type.txt",
    "folder.txt",
)


def _normalize_gdemu_filenames(folder: Path) -> None:
    """Rename the installed image to ``disc.gdi`` + ``trackNN.*``.

    GDEMU parses the ``.gdi`` sheet itself, and a card written by GD MENU Card
    Manager only ever holds short, unquoted, space-free track names.  Catalog
    filenames carry the full game title, which means quoted entries in the sheet
    — so they get renamed to the canonical form the firmware is known to read.
    """
    sheets = sorted(folder.glob("*.gdi"))
    if not sheets:
        for image in sorted(folder.glob("*.cdi")):
            target = folder / "disc.cdi"
            if image != target and not target.exists():
                image.rename(target)
            break
        return

    sheet = sheets[0]
    try:
        lines = sheet.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return

    rewritten: list[str] = []
    for index, line in enumerate(lines):
        if index == 0:
            rewritten.append(line.strip())
            continue
        parts = GDI_LINE_RE.match(line)
        if parts is None:
            rewritten.append(line.rstrip())
            continue
        source = folder / parts.group("name").strip('"')
        number = int(parts.group("num"))
        new_name = f"track{number:02d}{source.suffix.lower()}"
        target = folder / new_name
        if source != target:
            if not source.exists():
                return  # sheet doesn't match what's on disk — leave it alone
            if target.exists():
                target.unlink()
            source.rename(target)
        rewritten.append(
            f"{number} {parts.group('lba')} {parts.group('type')} "
            f"{parts.group('sector')} {new_name} {parts.group('offset')}"
        )

    text = "\n".join(rewritten) + "\n"
    disc_gdi = folder / "disc.gdi"
    try:
        sheet.write_text(text, encoding="utf-8")
        if sheet != disc_gdi:
            if disc_gdi.exists():
                disc_gdi.unlink()
            sheet.rename(disc_gdi)
    except OSError:
        pass


def _write_gdemu_metadata(folder: Path, display_name: str) -> list[Path]:
    """Write the per-folder metadata caches, reading the disc header for them."""
    written: list[Path] = []
    values = {"name.txt": str(display_name or "").strip()}
    try:
        from dreamcast_ipbin import read_folder_ip_bin

        ip = read_folder_ip_bin(folder)
    except Exception:
        ip = None
    if ip is not None:
        values.update(
            {
                "serial.txt": ip.product,
                "disc.txt": ip.disc,
                "region.txt": ip.region,
                "version.txt": ip.version,
                "date.txt": ip.date,
                "vga.txt": "1" if ip.vga else "0",
                "type.txt": "game",
                "folder.txt": "",
            }
        )
        values["name.txt"] = values["name.txt"] or ip.name
    for filename in GDEMU_META_FILES:
        if filename not in values:
            continue
        path = folder / filename
        try:
            path.write_text(values[filename], encoding="utf-8")
            written.append(path)
        except OSError:
            continue
    return written


def _finish_gdemu_install(game_folder: Path, display_name: str) -> list[Path]:
    """Make the new folder look like one GD MENU Card Manager wrote, then
    update the menu's game list.

    Three steps, all best-effort — none may fail an install that already copied
    the game onto the card:

    1. canonical ``disc.gdi`` / ``trackNN.*`` filenames;
    2. the ``name.txt`` + friends metadata caches;
    3. the game list — staged at the card root *and* patched into folder 01's
       menu image, which is the copy the console actually reads.
    """
    _normalize_gdemu_filenames(game_folder)
    written: list[Path] = _write_gdemu_metadata(game_folder, display_name)
    try:
        from gdemu_menu import update_card_menu

        result = update_card_menu(game_folder.parent) or {}
    except Exception:
        result = {}
    staged = result.get("staged")
    if staged is not None:
        written.append(staged)
    return written


def build_install_plan(
    profile: dict,
    rom: dict,
    system: str | None = None,
    override_format: str = "",
) -> InstallPlan:
    system_up = (system or rom.get("system") or "").upper()
    # OPL (Open PS2 Loader) has its own folder/naming layout (DVD/, CD/,
    # POPS/ with serial-prefixed filenames) — handle it before the generic
    # extract/archive logic below.
    if str(profile.get("device_type", "")).strip() == "OPL" and system_up in OPL_SYSTEMS:
        return _build_opl_install_plan(profile, rom, system_up, override_format)
    # GDEMU / openMenu: numbered folder per disc at the SD card root.
    if str(profile.get("device_type", "")).strip() in GDEMU_DEVICE_TYPES:
        return _build_gdemu_install_plan(profile, rom, system_up, override_format)
    rom_id = str(rom.get("rom_id") or rom.get("title_id") or "")
    if not rom_id:
        raise ValueError("Catalog entry is missing a ROM id.")
    filename = safe_file_name(str(rom.get("filename") or f"{rom_id}.rom"))
    extract = choose_extract_format(profile, rom, system_up, override_format)
    remote = mister_remote_target(profile)
    remote_ssh = mister_ssh_config(profile) if remote else None
    display_name = str(rom.get("name") or Path(filename).stem or rom_id)

    # An MSU pack is a folder wherever it goes.  On a MiSTer the folder's
    # parent depends on which core plays the pack (an MSU-MD title belongs to
    # the MegaCD core, cart renamed), so the system folder is chosen by the
    # pack kind rather than the catalog system.
    bundle_kind = str(rom.get("bundle_kind") or "").lower()
    rom_rename: str | None = None
    folder_system = system_up
    if bundle_kind in msu.MSU_SYSTEMS.get(system_up, ()) and bool(rom.get("is_bundle")):
        if str(profile.get("device_type", "")).strip() == "MiSTer":
            layout = msu_pack_layout(bundle_kind, system_up)
            if layout is None:
                raise ValueError(f"MiSTer cannot play a {bundle_kind} pack.")
            folder_system, rom_rename = layout
    else:
        bundle_kind = ""

    target_root = (
        mister_remote_rom_dir(profile, folder_system)
        if remote
        else resolve_profile_rom_folder(profile, folder_system)
    )
    target_filename = derive_download_filename(filename, extract)

    if extract == "eboot":
        target_path = target_root / safe_folder_name(display_name) / "EBOOT.PBP"
        return InstallPlan(
            rom_id,
            display_name,
            system_up,
            filename,
            target_path,
            extract,
            mister_remote=remote,
            mister_ssh=remote_ssh,
        )

    # MiSTer CD cores name the autosave memory card / backup RAM after the
    # game's subfolder, so each CD game installs into its own folder.  The
    # disc tag is stripped from the folder name so all discs of a multi-disc
    # game land together and share one card.  The Super SD System 3 wants the
    # same shape for a different reason: it only lists CD games that sit in
    # their own Cd/<Game>/ folder.
    device_type = str(profile.get("device_type", "")).strip()
    cd_game_folder = (device_type == "MiSTer" and system_up in MISTER_CD_SYSTEMS) or (
        device_type == SUPERSD3_DEVICE and system_up in SUPERSD3_CD_SYSTEMS
    )

    if extract in ARCHIVE_EXTRACT_FORMATS or bool(rom.get("is_bundle")):
        # PSIO installs into a bare game folder (translation/disc tags stripped);
        # the server already names the BIN/CU2 inside it.
        folder_name = clean_ps1_title(display_name) if extract == "psio" else display_name
        if cd_game_folder:
            folder_name = strip_disc_tag(display_name)
        target_dir = target_root / safe_folder_name(folder_name)
        return InstallPlan(
            rom_id=rom_id,
            display_name=display_name,
            system=system_up,
            source_filename=filename,
            target_path=target_dir,
            extract_format=extract,
            extract_archive=True,
            target_is_directory=True,
            mister_remote=remote,
            mister_ssh=remote_ssh,
            bundle_kind=bundle_kind,
            rom_rename=rom_rename,
        )

    if cd_game_folder:
        game_folder = safe_folder_name(strip_disc_tag(Path(target_filename).stem))
        target_root = target_root / game_folder

    return InstallPlan(
        rom_id=rom_id,
        display_name=display_name,
        system=system_up,
        source_filename=filename,
        target_path=target_root / target_filename,
        extract_format=extract,
        mister_remote=remote,
        mister_ssh=remote_ssh,
    )


def build_install_plans(
    profile: dict,
    rom: dict,
    system: str | None = None,
    override_format: str = "",
) -> list[InstallPlan]:
    """Plans for one catalog row — several when the row is a disc group.

    Rows collapsed by ``group_multidisc_roms`` for a device that installs each
    disc separately (MiSTer CD cores) carry ``install_members``: every disc
    gets its own plan, and they all resolve into the same disc-tag-free game
    folder.  Every other row yields exactly one plan.
    """
    members = rom.get("disc_members") or []
    if rom.get("install_members") and len(members) > 1:
        return [
            build_install_plan(profile, member, system, override_format)
            for member in members
        ]
    return [build_install_plan(profile, rom, system, override_format)]


def _safe_extract_zip(
    zip_path: Path,
    target_dir: Path,
    bundle_kind: str = "",
    rom_rename: str | None = None,
) -> list[Path]:
    """Unpack a downloaded bundle into ``target_dir``.

    Server-built bundles are flat.  An MSU pack is the zip the operator
    dropped on the server, usually wrapping one folder, so its members go
    through :func:`shared.msu.plan_extraction`, which hoists that folder,
    drops junk and applies the cart rename.
    """
    written: list[Path] = []
    target_root = target_dir.resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if bundle_kind:
            root, _ = msu.strip_common_root(i.filename for i in infos)

            def _read(name: str) -> str:
                member = f"{root}/{name}" if root else name
                return zf.read(member).decode("utf-8", "replace")

            layout = msu.plan_extraction(
                bundle_kind, [i.filename for i in infos], rom_rename, _read
            )
        else:
            layout = [(i.filename, i.filename) for i in infos]
        for member, rel in layout:
            info = zf.getinfo(member)
            destination = (target_dir / rel).resolve()
            try:
                destination.relative_to(target_root)
            except ValueError as exc:
                raise ValueError(f"Refusing unsafe ZIP member: {info.filename}") from exc
            destination.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(destination, "wb") as dst:
                shutil.copyfileobj(src, dst)
            written.append(destination)
    return written


def _install_tmp_dir() -> Path | None:
    """Scratch directory for staging downloads, or ``None`` for the system temp.

    Optional ``install_tmp_dir`` config key, for when the system temp lives on
    a small or slow volume.
    """
    raw = str(load_config().get("install_tmp_dir", "") or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return path


def _copy_tree_with_progress(
    src_root: Path,
    dst_root: Path,
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[Path]:
    """Copy every file under ``src_root`` into ``dst_root``, reporting bytes."""
    files = [p for p in sorted(src_root.rglob("*")) if p.is_file()]
    total = sum(p.stat().st_size for p in files)
    copied = 0
    written: list[Path] = []
    for src in files:
        dest = dst_root / src.relative_to(src_root)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(src, "rb") as sfh, open(dest, "wb") as dfh:
            while True:
                chunk = sfh.read(1024 * 1024)
                if not chunk:
                    break
                dfh.write(chunk)
                copied += len(chunk)
                if progress_callback:
                    progress_callback(copied, total)
        shutil.copystat(src, dest)
        written.append(dest)
    if progress_callback:
        progress_callback(total, total)
    return written


def _install_archive_local(
    plan: InstallPlan,
    target: Path,
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[Path]:
    """Download and unzip on local disk, then copy the result to the target.

    Unzipping straight onto an SD card is slow — many small random writes on
    slow flash, and the zip itself is written and re-read there.  Staging in a
    temp dir keeps the card down to one sequential copy pass.
    """
    with tempfile.TemporaryDirectory(
        prefix="3dssync-rom-", dir=_install_tmp_dir()
    ) as td:
        tmp_dir = Path(td)
        zip_path = tmp_dir / "download.zip"
        _download_rom(plan, zip_path, progress_callback)

        extract_dir = (tmp_dir / "extracted").resolve()
        _safe_extract_zip(zip_path, extract_dir, plan.bundle_kind, plan.rom_rename)
        zip_path.unlink(missing_ok=True)  # free the space before the copy

        target.mkdir(parents=True, exist_ok=True)
        return _copy_tree_with_progress(extract_dir, target, progress_callback)


def _download_rom(
    plan: InstallPlan,
    tmp_path: Path,
    progress_callback: Callable[[int, int], None] | None = None,
) -> None:
    """Stream the (optionally server-converted) ROM into ``tmp_path``."""
    params = {}
    if plan.extract_format:
        params["extract"] = plan.extract_format
    downloaded = 0
    with requests.get(
        f"{get_base_url()}/api/v1/roms/{plan.rom_id}",
        headers=get_api_headers(),
        params=params,
        stream=True,
        timeout=(30, 900),
    ) as resp:
        if resp.status_code != 200:
            detail = ""
            try:
                detail = resp.text.strip()
            except Exception:
                pass
            raise RuntimeError(
                f"HTTP {resp.status_code}" + (f": {detail}" if detail else "")
            )
        total = int(resp.headers.get("Content-Length", "0") or 0)
        with open(tmp_path, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=256 * 1024):
                if not chunk:
                    continue
                fh.write(chunk)
                downloaded += len(chunk)
                if progress_callback:
                    progress_callback(downloaded, total)


def mister_ssh_config(profile: dict) -> dict:
    """SSH connection dict for a MiSTer profile.

    Profile ``ssh_*`` fields win; profiles saved before the fields existed
    fall back to the legacy global ``mister_ssh`` config key.
    """
    host = str(profile.get("ssh_host", "") or "").strip()
    if host:
        return {
            "host": host,
            "port": int(profile.get("ssh_port", 22) or 22),
            "username": str(profile.get("ssh_username", "root") or "root"),
            "password": str(profile.get("ssh_password", "") or ""),
            "key_path": str(profile.get("ssh_key_path", "") or ""),
        }
    return dict(load_config().get("mister_ssh") or {})


def _mister_ssh_client(cfg: dict | None):
    """MiSTerSSH from a connection dict (see ``mister_ssh_config``)."""
    from mister_ssh import MiSTerSSH  # deferred — paramiko is optional

    cfg = cfg or {}
    host = str(cfg.get("host", "") or "").strip()
    if not host:
        raise RuntimeError(
            "MiSTer SSH is not configured.\n"
            "Edit the profile and fill in the SSH host/credentials."
        )
    return MiSTerSSH(
        host=host,
        port=int(cfg.get("port", 22) or 22),
        username=str(cfg.get("username", "root") or "root"),
        password=str(cfg.get("password", "") or ""),
        key_path=str(cfg.get("key_path", "") or ""),
    )


def _prepare_mister_usb_game_dir(ssh, sys_dir: PurePosixPath) -> None:
    """Create a USB game folder and seed it with the SD folder's BIOS files.

    MiSTer cores use ``/media/usb0/games/<Core>`` *instead of* the SD folder
    once it exists, so a CD core (PSX, Saturn, MegaCD, ...) would lose its
    ``boot.rom`` BIOS.  ``cp -n`` never overwrites files the user put there.
    """
    fat_dir = f"/media/fat/games/{sys_dir.name}"
    try:
        ssh.exec(
            f"mkdir -p '{sys_dir}' && "
            f"cp -n '{fat_dir}'/boot*.rom '{sys_dir}/' 2>/dev/null; true"
        )
    except Exception:
        pass  # best-effort — the game upload itself creates the folder too


def _install_rom_mister_remote(
    plan: InstallPlan,
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[Path]:
    """Download to a local temp file, then push to the MiSTer over SFTP.

    The per-system game folder (e.g. ``/media/usb0/games/PSX``) is created on
    the MiSTer as needed.
    """
    target = PurePosixPath(str(plan.target_path).replace("\\", "/"))
    with tempfile.TemporaryDirectory(prefix="3dssync-rom-") as td:
        tmp_dir = Path(td)
        tmp_path = tmp_dir / f"{safe_folder_name(target.name)}.download"
        _download_rom(plan, tmp_path, progress_callback)

        ssh = _mister_ssh_client(plan.mister_ssh)
        with ssh:
            if plan.mister_remote == "usb":
                # BIOS files belong in the system folder (games/PSX), which for
                # CD cores is the *grandparent* of a per-game subfolder target.
                games_root = PurePosixPath(MISTER_GAMES_ROOTS[plan.mister_remote])
                try:
                    sys_dir = games_root / target.relative_to(games_root).parts[0]
                except ValueError:
                    sys_dir = target.parent
                _prepare_mister_usb_game_dir(ssh, sys_dir)
            if plan.extract_archive:
                extract_dir = tmp_dir / "extracted"
                files = _safe_extract_zip(
                    tmp_path, extract_dir, plan.bundle_kind, plan.rom_rename
                )
                written: list[Path] = []
                for f in files:
                    rel = f.relative_to(extract_dir.resolve())
                    remote_file = target / PurePosixPath(*rel.parts)
                    ssh.upload_file(f, str(remote_file), progress_callback)
                    written.append(remote_file)
                return written
            ssh.upload_file(tmp_path, str(target), progress_callback)
            return [target]


def install_rom(
    plan: InstallPlan,
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[Path]:
    if plan.mister_remote:
        return _install_rom_mister_remote(plan, progress_callback)

    target = plan.target_path
    if plan.extract_archive:
        written = _install_archive_local(plan, target, progress_callback)
        if plan.gdemu_name:
            written.extend(_finish_gdemu_install(target, plan.gdemu_name))
        return written

    tmp_parent = target if plan.target_is_directory else target.parent
    tmp_parent.mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_parent / f".{safe_folder_name(target.name)}.part"

    _download_rom(plan, tmp_path, progress_callback)

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            tmp_path.replace(target)
        except OSError:
            shutil.copy2(tmp_path, target)
            tmp_path.unlink(missing_ok=True)
        written = [target]
        if plan.opl_popstarter:
            written.extend(_install_popstarter_app(target, plan.display_name))
        if plan.gdemu_name:
            written.extend(_finish_gdemu_install(target.parent, plan.gdemu_name))
        return written
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def available_systems_for_profiles(profiles: Iterable[dict]) -> list[str]:
    systems: set[str] = set()
    for profile in profiles:
        systems.update(profile_systems(profile))
    return sorted(s for s in systems if s)


# ── PSIO install sanitization ────────────────────────────────────────────────
#
# PSIO's menu refuses filenames > 60 chars and cannot render non-ASCII bytes.
# Games installed before these limits were enforced (or copied onto the SD
# card by hand) need renaming.  ``sanitize_installed_files`` walks a profile's
# ROM folder and renames every offending file / folder, keeping each game's
# ``.bin``/``.cu2`` pair on a matching stem and rewriting ``MULTIDISC.LST``.

_PSIO_PAIR_EXTS = (".bin", ".cu2")


def psio_safe_name(name: str, reserve: int = 0) -> str:
    """ASCII-only, ``PSIO_MAX_NAME - reserve`` chars.  ``reserve`` is the number
    of characters to leave for a suffix the caller appends (e.g. an extension
    or `` (Disc N)``)."""
    text = _ascii_only(name)
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text)
    text = re.sub(r"\s+", " ", text).strip(" ._")
    limit = max(1, PSIO_MAX_NAME - reserve)
    if len(text) > limit:
        text = text[:limit].rstrip(" ._")
    return text or "game"


def _free_pair_stem(parent: Path, desired: str, own: set[Path]) -> str:
    """A stem whose ``.bin`` and ``.cu2`` are both free in ``parent``.

    Collisions (two different source stems collapsing to the same ASCII name)
    get a ``~n`` suffix, re-truncated so the final filename still fits.
    """
    if _pair_stem_free(parent, desired, own):
        return desired
    n = 2
    while n < 1000:
        suffix = f"~{n}"
        limit = max(1, PSIO_MAX_NAME - 4 - len(suffix))
        candidate = desired[:limit].rstrip(" ._") + suffix
        if _pair_stem_free(parent, candidate, own):
            return candidate
        n += 1
    return desired


def _pair_stem_free(parent: Path, stem: str, own: set[Path]) -> bool:
    for ext in _PSIO_PAIR_EXTS:
        path = parent / f"{stem}{ext}"
        if path.exists() and path not in own:
            return False
    return True


def _free_single_name(parent: Path, desired: str, own: Path) -> Path:
    candidate = parent / desired
    if candidate == own or not candidate.exists():
        return candidate
    stem = Path(desired).stem
    ext = Path(desired).suffix
    n = 2
    while n < 1000:
        suffix = f"~{n}"
        limit = max(1, PSIO_MAX_NAME - len(ext) - len(suffix))
        candidate = parent / f"{stem[:limit].rstrip(' ._')}{suffix}{ext}"
        if candidate == own or not candidate.exists():
            return candidate
        n += 1
    return parent / desired


def _sanitize_dir_files(directory: Path, renames: list[tuple[str, str]]) -> None:
    """Rename ``.bin``/``.cu2`` pairs + loose files in ``directory`` to PSIO-safe
    names, keeping paired stems aligned and rewriting ``MULTIDISC.LST``."""
    pairs: dict[str, dict[str, Path]] = {}
    loose: list[Path] = []
    for entry in sorted(directory.iterdir()):
        if not entry.is_file():
            continue
        ext = entry.suffix.lower()
        if ext in _PSIO_PAIR_EXTS:
            pairs.setdefault(entry.stem, {})[ext] = entry
        elif entry.name.upper() != "MULTIDISC.LST":
            loose.append(entry)

    bin_renames: dict[str, str] = {}

    for stem, files in sorted(pairs.items()):
        desired = psio_safe_name(stem, reserve=4)
        if desired == stem:
            continue
        own = {p for p in files.values()}
        final_stem = _free_pair_stem(directory, desired, own)
        if final_stem == stem:
            continue
        for ext in _PSIO_PAIR_EXTS:
            old = files.get(ext)
            if not old:
                continue
            new_path = directory / f"{final_stem}{ext}"
            old.rename(new_path)
            renames.append((str(old), str(new_path)))
            if ext == ".bin":
                bin_renames[old.name] = new_path.name

    for old in loose:
        ext = old.suffix
        desired = psio_safe_name(old.stem, reserve=len(ext)) + ext
        if desired == old.name:
            continue
        new_path = _free_single_name(old.parent, desired, old)
        if new_path == old:
            continue
        old.rename(new_path)
        renames.append((str(old), str(new_path)))

    lst = directory / "MULTIDISC.LST"
    if bin_renames and lst.is_file():
        # newline="" disables platform translation so the on-disk CRLF
        # endings PSIO expects are preserved exactly.
        with lst.open("r", encoding="utf-8", errors="replace", newline="") as fh:
            lines = [
                bin_renames.get(line.strip(), line.strip())
                for line in fh.read().splitlines()
            ]
        with lst.open("w", encoding="utf-8", newline="") as fh:
            fh.write("\r\n".join(lines) + "\r\n")


def _sanitize_folder_name(folder: Path, renames: list[tuple[str, str]]) -> None:
    desired = psio_safe_name(folder.name, reserve=0)
    if desired == folder.name:
        return
    new_path = _free_single_name(folder.parent, desired, folder)
    if new_path == folder:
        return
    folder.rename(new_path)
    renames.append((str(folder), str(new_path)))


def sanitize_installed_files(profile: dict, system: str) -> list[tuple[str, str]]:
    """Rename PSIO-installed files/folders that break the ASCII + 60-char limits.

    Walks the profile's ROM folder: each game subfolder has its ``.bin``/``.cu2``
    pairs aligned to a matching PSIO-safe stem (with ``MULTIDISC.LST`` rewritten)
    and is itself renamed; loose files under the root are fixed too.  Returns a
    list of ``(old_path, new_path)`` records.  Does nothing if the folder is
    missing.  Never raises on individual rename failures — problems are skipped
    so one locked file doesn't abort the whole sweep.
    """
    renames: list[tuple[str, str]] = []
    try:
        root = resolve_profile_rom_folder(profile, system)
    except Exception:
        return renames
    if not root.is_dir():
        return renames

    try:
        _sanitize_dir_files(root, renames)
    except OSError:
        pass
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        try:
            _sanitize_dir_files(child, renames)
            _sanitize_folder_name(child, renames)
        except OSError:
            continue
    return renames


_OPL_SERIAL_PREFIX_RE = re.compile(r"^[A-Z]{4}_\d{3}\.\d{2}\.")


def _opl_display_from_vcd(vcd_path: Path) -> str:
    """Recover a game's display name from its VCD filename.

    ``SLUS_012.34.Castlevania SOTN.vcd`` → ``Castlevania SOTN``.  Falls back to
    the whole stem when there's no recognisable serial prefix.
    """
    stem = vcd_path.stem
    match = _OPL_SERIAL_PREFIX_RE.match(stem)
    return stem[match.end():] if match else stem


def _vcd_present(pops_dir: Path, base: str) -> bool:
    """True if a ``<base>.vcd`` (any case) exists in the POPS folder."""
    return (pops_dir / f"{base}.vcd").exists() or (pops_dir / f"{base}.VCD").exists()


def _remove_orphan_popstarter_apps(apps_dir: Path, pops_dir: Path) -> list[str]:
    """Delete APPS/<folder>/ app folders whose game VCD is gone.

    Only serial-named folders (``SLUS_xxx.xx.*``) are considered ours, so the
    user's own app folders / loose ELFs are never touched.
    """
    removed: list[str] = []
    if not apps_dir.is_dir():
        return removed
    for child in sorted(apps_dir.iterdir()):
        if not child.is_dir():
            continue
        if not _OPL_SERIAL_PREFIX_RE.match(child.name):
            continue
        if _vcd_present(pops_dir, child.name):
            continue
        try:
            shutil.rmtree(child)
            removed.append(str(child))
        except OSError:
            pass
    return removed


def repair_opl_popstarter(profile: dict, system: str) -> list[tuple[str, str]]:
    """Backfill POPStarter app folders for already-installed VCDs.

    For each ``POPS/*.VCD`` missing its ``APPS/<stem>/`` folder (renamed
    POPSTARTER.ELF + title.cfg), creates it so the game shows on OPL's Apps
    page.  Also removes orphan app folders for games whose VCD was deleted.
    Idempotent.  Returns ``(path, detail)`` records.  Raises ``ValueError`` if
    VCDs exist but POPSTARTER.ELF is absent.
    """
    fixed: list[tuple[str, str]] = []
    try:
        root = resolve_profile_rom_folder(profile, system)
    except Exception:
        return fixed
    pops = root / "POPS"
    if not pops.is_dir():
        return fixed
    vcds = sorted(
        p for p in pops.iterdir() if p.is_file() and p.suffix.lower() == ".vcd"
    )
    apps_dir = root / "APPS"

    if vcds:
        src = _find_popstarter_elf(pops)
        if src is None:
            raise ValueError(
                "POPSTARTER.ELF not found in the POPS folder.\n"
                "Add POPSTARTER.ELF (plus POPS.ELF and IOPRP252.IMG) to POPS/ and retry."
            )
        for vcd in vcds:
            app_dir = apps_dir / vcd.stem
            launcher = app_dir / _popstarter_launcher_name(vcd.stem)
            title_cfg = app_dir / "title.cfg"
            if launcher.exists() and title_cfg.exists():
                continue
            made = _install_popstarter_app(vcd, _opl_display_from_vcd(vcd))
            if made:
                fixed.append((str(vcd), str(app_dir)))

    # Clean up app folders for games whose VCD was removed.
    for orphan in _remove_orphan_popstarter_apps(apps_dir, pops):
        fixed.append((orphan, "(removed stale app folder)"))
    return fixed


# Staging prefix for the two-phase folder renumber.  A folder can only be
# renamed onto a free name, and sorting a card generally means every number
# moves at once, so each mover parks under this prefix first.
GDEMU_SORT_TMP_PREFIX = ".gs_sort_"


def gdemu_sort_key(name: str) -> tuple[str, str]:
    """Case-insensitive ordering for a game's display name.

    Ties break on the raw name so the order is stable across runs regardless of
    how the filesystem enumerated the folders.
    """
    text = str(name or "").strip()
    return (text.casefold(), text)


def _gdemu_game_label(folder: Path) -> str:
    """Best display name for a game folder: ``name.txt``, else the disc header."""
    from gdemu_menu import folder_name_txt

    label = folder_name_txt(folder)
    if label:
        return label
    try:
        from dreamcast_ipbin import read_folder_ip_bin

        ip = read_folder_ip_bin(folder)
    except Exception:
        ip = None
    return ip.name if ip is not None else ""


def sort_gdemu_folders(root: Path) -> list[tuple[str, str]]:
    """Renumber the card's game folders so they run alphabetically from ``02``.

    The folder number *is* the menu's sort order — the list is emitted in
    folder order — so putting the games in alphabetical order on the card is
    what puts them in alphabetical order in the menu.

    Folder ``01`` is left where it is: that is the menu disc, and every manager
    expects it at ``01``.  Renumbering happens in two phases (each mover parks
    under a temporary name first) because a folder can only be renamed onto a
    name nothing else holds, and a sort usually shifts every number at once.

    Returns ``[(old folder, new folder)]`` for the folders that moved.
    """
    from gdemu_menu import numbered_folders

    root = Path(root)
    games = [
        (number, folder)
        for number, folder in numbered_folders(root)
        if int(number) != int(GDEMU_MENU_FOLDER)
    ]
    if not games:
        return []

    leftovers = [d for d in root.glob(f"{GDEMU_SORT_TMP_PREFIX}*") if d.is_dir()]
    if leftovers:
        # A previous run died between the two phases.  Park the strays back into
        # free numbers so this run sees a consistent card.
        used = {int(number) for number, _folder in games}
        next_free = max(used) + 1 if used else int(GDEMU_MENU_FOLDER) + 1
        for stray in sorted(leftovers):
            while next_free in used:
                next_free += 1
            target = root / gdemu_folder_name(next_free)
            stray.rename(target)
            used.add(next_free)
            games.append((target.name, target))
        games.sort(key=lambda item: int(item[0]))

    ordered = sorted(games, key=lambda item: gdemu_sort_key(_gdemu_game_label(item[1])))
    desired = {
        old_number: gdemu_folder_name(index)
        for index, (old_number, _folder) in enumerate(
            ordered, start=int(GDEMU_MENU_FOLDER) + 1
        )
    }
    movers = [
        (old_number, folder)
        for old_number, folder in games
        if desired[old_number] != old_number
    ]
    if not movers:
        return []

    staged: list[tuple[Path, str]] = []
    for old_number, folder in movers:
        parked = root / f"{GDEMU_SORT_TMP_PREFIX}{old_number}"
        folder.rename(parked)
        staged.append((parked, desired[old_number]))
    for parked, new_number in staged:
        parked.rename(root / new_number)

    return [
        (f"{old_number}/", f"{desired[old_number]}/ {_gdemu_game_label(Path(root) / desired[old_number])}")
        for old_number, _folder in movers
    ]


def repair_gdemu_card(profile: dict, system: str = "DC") -> list[tuple[str, str]]:
    """Bring every game folder on a GDEMU card up to the layout we install.

    Fixes cards holding games installed before this layout existed, or copied
    on by hand: canonical ``disc.gdi`` / ``trackNN.*`` filenames, the metadata
    caches, an alphabetical renumber of the game folders, and finally the
    menu's game list (staged at the root and patched into the folder-01 menu
    image).

    Folder ``01`` is never touched — that is the menu disc itself, and only its
    embedded list is rewritten, by ``update_card_menu``.

    Returns ``[(before, after)]`` pairs describing what changed.
    """
    from gdemu_menu import folder_name_txt, numbered_folders, update_card_menu

    root = resolve_profile_rom_folder(profile, system)
    if not root or not Path(root).is_dir():
        return []

    changes: list[tuple[str, str]] = []
    for number, folder in numbered_folders(Path(root)):
        if int(number) == int(GDEMU_MENU_FOLDER):
            continue  # the menu disc itself
        try:
            before = sorted(f.name for f in folder.iterdir() if f.is_file())
        except OSError:
            continue
        _normalize_gdemu_filenames(folder)
        _write_gdemu_metadata(folder, folder_name_txt(folder))
        try:
            after = sorted(f.name for f in folder.iterdir() if f.is_file())
        except OSError:
            continue
        for name in before:
            if name not in after:
                changes.append((f"{number}/{name}", f"{number}/ (renamed)"))
        for name in after:
            if name not in before:
                changes.append((f"{number}/", f"{number}/{name}"))

    # Sort after normalizing: the labels the order is built from come from the
    # metadata caches written above.
    changes.extend(sort_gdemu_folders(Path(root)))

    result = update_card_menu(Path(root)) or {}
    if result.get("patched"):
        patched = result["patched"]
        changes.append(
            (
                "menu game list",
                f"patched {patched['copies']} cop(y/ies) in 01/ "
                f"({patched['written']} bytes, {len(result.get('entries') or [])} games)",
            )
        )
    elif result.get("error"):
        changes.append(("menu game list", f"NOT patched — {result['error']}"))
    return changes


def repair_installed_files(profile: dict, system: str) -> list[tuple[str, str]]:
    """Dispatch the "Sanitize/Repair Installed Files" action by device type.

    PSIO → enforce filename limits; OPL → backfill POPStarter launchers and
    conf_apps.cfg entries; GDEMU / openMenu → normalize game folders and
    refresh the menu list.  Other devices → no-op.
    """
    device_type = str(profile.get("device_type", "")).strip().upper()
    if device_type == "OPL":
        return repair_opl_popstarter(profile, system)
    if device_type == "PSIO":
        return sanitize_installed_files(profile, system)
    if device_type in {d.upper() for d in GDEMU_DEVICE_TYPES}:
        return repair_gdemu_card(profile, system or "DC")
    return []
