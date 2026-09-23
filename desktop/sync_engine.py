"""Sync engine for ROM-based save syncing (RetroArch, MiSTer, Analogue Pocket, etc.).

Uses the shared package for cross-app ROM/title-id helpers while keeping the
device sync workflow independent from the server package layout.
"""

from __future__ import annotations

import hashlib
import ftplib
import io
import json
import os
import posixpath
import re
import socket
import stat as stat_module
import struct
import time
import zipfile
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

from systems import (
    CD_ALL_EXTENSIONS,
    MEGA_EVERDRIVE_CD_SYSTEMS,
    MISTER_FOLDER_MAP,
    ROM_EXTENSIONS,
    SAVE_EXTENSIONS,
    SYSTEM_CODES,
    SYSTEM_DEFAULT_SAVE_EXT,
)
from shared import mister as _shared_mister_mod
from shared import mister_saves as _mister_saves
from shared.rom_id import make_title_id, normalize_rom_name
from shared.sync_id import canonicalize_code_form_title_id

# ---------------------------------------------------------------------------
# ROM name normalization helpers layered on top of shared.rom_id
# ---------------------------------------------------------------------------

# SYSTEM_CODES imported from systems
_REV_RE = re.compile(
    r"\s*\((?:Rev\s*\w+|v\d[\d.]*|Version\s*\w+|Beta\s*\d*|Proto\s*\d*|Demo|Sample|Unl)\)",
    re.IGNORECASE,
)
_DISC_RE = re.compile(r"\s*\((?:Disc|Disk|CD)\s*\d+\)", re.IGNORECASE)
_EXTRA_RE = re.compile(r"\s*\([^)]+\)")
_BRACKET_TAG_RE = re.compile(r"\s*\[[^\]]*\]")

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


def _scan_debug_enabled() -> bool:
    return os.environ.get("SYNC_DEBUG_SCAN", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _scan_debug_log_path() -> Path:
    override = os.environ.get("SYNC_DEBUG_SCAN_FILE", "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parent / "scan_debug.log"


def _reset_debug_scan_log() -> None:
    if not _scan_debug_enabled():
        return
    path = _scan_debug_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    except OSError:
        pass


def _debug_scan(message: str) -> None:
    if not _scan_debug_enabled():
        return
    line = f"[sync-debug] {message}"
    print(line)
    path = _scan_debug_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


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


def _normalize_alias_lookup_name(filename: str) -> str:
    """Normalize translated names while ignoring [patch] metadata."""
    return normalize_rom_name(_BRACKET_TAG_RE.sub("", filename).strip())


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
    "Beetle PC-FX": "PCFX",
    "Beetle GG": "GG",
    "Beetle PSX": "PS1",
    "PCSX-ReARMed": "PS1",
    "PPSSPP": "PSP",
    "LRPS2": "PS2",
    "Play!": "PS2",
    "Beetle Saturn": "SAT",
    "Kronos": "SAT",
    "YabaSanshiro": "SAT",
    "YabaSanshiro 2": "SAT",
    "Flycast": "DC",
    "Dolphin": "GC",
    "DeSmuME": "NDS",
    "melonDS": "NDS",
    "melonDS DS": "NDS",
    "Beetle Cygne": "WSWAN",
    "Beetle VB": "VB",
    "Beetle SuperGrafx": "PCSG",
    "SMS Plus GX": "SMS",
    "Stella": "A2600",
    "ProSystem": "A7800",
    "Beetle Lynx": "LYNX",
    "FinalBurn Neo": "ARCADE",
    "MAME": "MAME",
}

RETROARCH_SYSTEM_CORES: dict[str, list[str]] = {
    "SNES": ["Snes9x", "bsnes", "bsnes-mercury"],
    "GBA": ["mGBA", "VBA-M"],
    "NES": ["Nestopia", "FCEUmm"],
    "MD": ["Genesis Plus GX", "PicoDrive"],
    "N64": ["Mupen64Plus-Next", "ParaLLEl N64"],
    "N64DD": ["Mupen64Plus-Next", "ParaLLEl N64"],
    "GB": ["Gambatte", "SameBoy", "TGB Dual", "Gearboy"],
    "GBC": ["SameBoy", "Gambatte", "Gearboy"],
    "GG": ["Genesis Plus GX", "Beetle GG"],
    "NGP": ["Mednafen NGP"],
    "NGPC": ["Mednafen NGP"],
    "PCE": ["Beetle PCE"],
    "TG16": ["Beetle PCE"],
    "PCSG": ["Beetle SuperGrafx", "Beetle PCE"],
    "PCECD": ["Beetle PCE"],
    "PCFX": ["Beetle PC-FX"],
    "PS1": ["Beetle PSX", "PCSX-ReARMed"],
    "PS2": ["LRPS2", "Play!"],
    "PSP": ["PPSSPP"],
    "SMS": ["SMS Plus GX", "Genesis Plus GX", "PicoDrive"],
    "A2600": ["Stella"],
    "A7800": ["ProSystem"],
    "LYNX": ["Beetle Lynx"],
    "NEOGEO": ["FinalBurn Neo", "MAME"],
    "32X": ["PicoDrive"],
    "SEGACD": ["Genesis Plus GX", "PicoDrive"],
    "SAT": ["Beetle Saturn", "Yabause", "YabaSanshiro"],
    "WSWAN": ["Beetle Cygne"],
    "WSWANC": ["Beetle Cygne"],
    "VB": ["Beetle VB"],
    "DC": ["Flycast"],
    "NDS": ["melonDS DS", "DeSmuME", "melonDS"],
    "GC": ["Dolphin"],
    "ARCADE": ["FinalBurn Neo", "MAME"],
    "MAME": ["MAME", "FinalBurn Neo"],
    "CPS1": ["FinalBurn Neo", "MAME"],
    "CPS2": ["FinalBurn Neo", "MAME"],
    "CPS3": ["FinalBurn Neo", "MAME"],
    "FDS": ["Nestopia", "FCEUmm"],
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

# ROM_EXTENSIONS, SAVE_EXTENSIONS, SYSTEM_CODES, SYSTEM_DEFAULT_SAVE_EXT imported from systems
# CD_ALL_EXTENSIONS replaces the old CD_ROM_EXTENSIONS (now also includes .cso/.pbp)
CD_ROM_EXTENSIONS = CD_ALL_EXTENSIONS  # backwards-compat alias
ZIP_ROM_EXTENSIONS = {".zip"}
SYSTEM_DEFAULT_SAVE_EXTENSIONS = SYSTEM_DEFAULT_SAVE_EXT  # backwards-compat alias

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

    default_ext = SYSTEM_DEFAULT_SAVE_EXT.get(system_code)
    if default_ext and ext.lower() in _LEGACY_GENERIC_SAVE_EXTENSIONS:
        return default_ext
    return ext


def _retroarch_saturn_format(
    save_ext: str | None = None,
    save_folder: str | None = None,
) -> str:
    """Infer the Saturn RetroArch target format from the profile settings."""
    ext = (save_ext or "").strip().lower()
    folder_name = ""
    if save_folder:
        try:
            folder_name = Path(save_folder).name.strip().lower()
        except Exception:
            folder_name = ""

    if ext == ".bin" or folder_name == "yabasanshiro":
        return "yabasanshiro"
    if ext == ".srm":
        return "yabause"
    return "mednafen"


def _retroarch_saturn_mednafen_save_root(save_root: Path) -> Path:
    """Return the RetroArch Beetle Saturn save root.

    We intentionally keep Beetle Saturn on the default RetroArch save root.
    Only YabaSanshiro uses a dedicated subfolder/container by default.
    """
    return save_root


def _retroarch_selected_core(sys_info: dict | None) -> str:
    if not sys_info:
        return ""
    return str(sys_info.get("core", "")).strip()


def _is_shared_saturn_backup(path: Path | None) -> bool:
    if path is None:
        return False
    return path.name.strip().lower() == "backup.bin"


def _saturn_format_for_path(path: Path | None) -> str:
    if path is None:
        return "mednafen"
    # MiSTer's Saturn core reads/writes the internal backup RAM byte-expanded
    # to 64 KB (0xFF padding at even offsets) — the same layout Yabause uses —
    # and always names it ``.sav``.
    if is_ssh_save_path(path):
        return _shared_mister_mod.MISTER_SATURN_FORMAT
    if _is_shared_saturn_backup(path) or path.suffix.lower() == ".bin":
        return "yabasanshiro"
    if path.suffix.lower() == ".srm":
        return "yabause"
    return "mednafen"


def _lookup_saturn_archive_candidates(
    title_id: str,
    archive_names: list[str],
    base_url: str,
    headers: dict,
    timeout: int = 30,
) -> list[dict]:
    resp = requests.post(
        f"{base_url}/api/v1/titles/saturn-archives",
        headers={**headers, "Content-Type": "application/json"},
        json={"title_id": title_id, "archive_names": archive_names},
        timeout=timeout,
    )
    resp.raise_for_status()
    body = resp.json()
    return body.get("results", []) if isinstance(body, dict) else []


def _resolve_saturn_archive_selection(
    title_id: str,
    path: Path,
    base_url: str,
    headers: dict,
    timeout: int = 30,
) -> list[str]:
    from saroo_format import list_saturn_archive_names

    data = path.read_bytes()
    archive_names = list_saturn_archive_names(data)
    persisted = [
        name
        for name in _get_saturn_archive_names(title_id)
        if name in {archive.upper() for archive in archive_names}
    ]
    if persisted:
        return persisted

    results = _lookup_saturn_archive_candidates(
        title_id, archive_names, base_url, headers, timeout=timeout
    )
    selected: list[str] = []
    for result in results:
        if result.get("status") not in {"exact_current", "includes_current"}:
            continue
        for archive_name in result.get("archive_names", []):
            normalized = str(archive_name).strip().upper()
            if normalized and normalized not in selected:
                selected.append(normalized)

    if selected:
        _set_saturn_archive_names(title_id, selected)
    return selected


def _canonical_saturn_payload(
    title_id: str,
    path: Path,
    base_url: str | None = None,
    headers: dict | None = None,
    timeout: int = 30,
) -> tuple[bytes, list[str] | None]:
    from saroo_format import (
        extract_saturn_save_set,
        list_saturn_archive_names,
        normalize_saturn_save,
    )

    data = path.read_bytes()
    if _is_shared_saturn_backup(path):
        archive_names = _get_saturn_archive_names(title_id)
        if not archive_names and base_url and headers is not None:
            archive_names = _resolve_saturn_archive_selection(
                title_id, path, base_url, headers, timeout=timeout
            )
        if not archive_names:
            return b"", None
        return extract_saturn_save_set(data, archive_names), archive_names

    canonical = normalize_saturn_save(data)
    return canonical, [name.upper() for name in list_saturn_archive_names(canonical)]


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


class SyncUserError(RuntimeError):
    """Raised for profile/setup issues that should be shown without a traceback."""


# ---------------------------------------------------------------------------
# FTP-backed save paths
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FtpEntry:
    name: str
    path: str
    is_dir: bool
    size: int = 0
    mtime: float = 0.0


@dataclass(frozen=True)
class FtpSavePath:
    """Small path-like object for save files stored on a plain FTP server."""

    host: str
    port: int
    username: str
    password: str
    remote_path: str
    passive: bool = True
    timeout: int = 30

    @property
    def name(self) -> str:
        return posixpath.basename(self.remote_path.rstrip("/"))

    @property
    def stem(self) -> str:
        return posixpath.splitext(self.name)[0]

    @property
    def suffix(self) -> str:
        return posixpath.splitext(self.name)[1]

    def __str__(self) -> str:
        host = self.host or "unknown"
        port = f":{self.port}" if self.port and self.port != 21 else ""
        return f"ftp://{host}{port}{self.remote_path}"

    def sync_key(self) -> str:
        return str(self)

    def exists(self) -> bool:
        try:
            with _ftp_connection(
                self.host,
                self.port,
                self.username,
                self.password,
                self.passive,
                self.timeout,
            ) as ftp:
                return _ftp_path_exists(ftp, self.remote_path)
        except Exception:
            return False

    def is_file(self) -> bool:
        return self.exists()

    def is_dir(self) -> bool:
        return False

    def read_bytes(self) -> bytes:
        with _ftp_connection(
            self.host,
            self.port,
            self.username,
            self.password,
            self.passive,
            self.timeout,
        ) as ftp:
            expected_size = _ftp_size(ftp, self.remote_path)
            return _ftp_download_bytes(
                ftp,
                self.remote_path,
                expected_size=expected_size if expected_size > 0 else None,
            )

    def write_bytes(self, data: bytes) -> None:
        with _ftp_connection(
            self.host,
            self.port,
            self.username,
            self.password,
            self.passive,
            self.timeout,
        ) as ftp:
            _ftp_upload_bytes(ftp, self.remote_path, data)
        _invalidate_remote_hash_path(self.remote_path)


# ---------------------------------------------------------------------------
# SSH/SFTP-backed save paths (MiSTer)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SshSavePath:
    """Path-like object for save files reached over SSH/SFTP (MiSTer).

    ``assume_exists`` avoids one SSH round-trip per ``exists()`` call: scan
    results are built from a live listing so existence is already known, and
    download targets flip to existing once written.
    """

    host: str
    port: int
    username: str
    password: str
    key_path: str
    remote_path: str
    assume_exists: bool = True

    @property
    def name(self) -> str:
        return posixpath.basename(self.remote_path.rstrip("/"))

    @property
    def stem(self) -> str:
        return posixpath.splitext(self.name)[0]

    @property
    def suffix(self) -> str:
        return posixpath.splitext(self.name)[1]

    def __str__(self) -> str:
        host = self.host or "unknown"
        port = f":{self.port}" if self.port and self.port != 22 else ""
        return f"ssh://{host}{port}{self.remote_path}"

    def sync_key(self) -> str:
        return str(self)

    def _connection(self):
        from mister_ssh import MiSTerSSH

        return MiSTerSSH(
            host=self.host,
            port=self.port,
            username=self.username,
            password=self.password,
            key_path=self.key_path,
        )

    def exists(self) -> bool:
        if self.assume_exists:
            return True
        try:
            with self._connection() as ssh:
                ssh._sftp.stat(self.remote_path)
                return True
        except Exception:
            return False

    def is_file(self) -> bool:
        return self.exists()

    def is_dir(self) -> bool:
        return False

    def read_bytes(self) -> bytes:
        with self._connection() as ssh:
            return ssh.read_file(self.remote_path)

    def write_bytes(self, data: bytes) -> None:
        with self._connection() as ssh:
            ssh.makedirs(posixpath.dirname(self.remote_path))
            ssh.write_file(self.remote_path, data)
        _invalidate_remote_hash_path(self.remote_path)


def is_ssh_save_path(path: object) -> bool:
    return isinstance(path, SshSavePath)


def mister_profile_uses_ssh(profile: dict) -> bool:
    """True when a MiSTer profile carries SSH connection details."""
    return (
        str(profile.get("device_type", "")).strip() == "MiSTer"
        and bool(str(profile.get("ssh_host", "") or "").strip())
    )


def _mister_ssh_from_profile(profile: dict):
    from mister_ssh import MiSTerSSH

    host = str(profile.get("ssh_host", "") or "").strip()
    if not host:
        raise SyncUserError(
            "MiSTer SSH host is not set — edit the profile and fill in the "
            "SSH connection fields."
        )
    return MiSTerSSH(
        host=host,
        port=int(profile.get("ssh_port", 22) or 22),
        username=str(profile.get("ssh_username", "root") or "root"),
        password=str(profile.get("ssh_password", "") or ""),
        key_path=str(profile.get("ssh_key_path", "") or ""),
    )


def _mister_ssh_save_path(profile: dict, remote_path: str, assume_exists: bool = True) -> SshSavePath:
    return SshSavePath(
        host=str(profile.get("ssh_host", "") or "").strip(),
        port=int(profile.get("ssh_port", 22) or 22),
        username=str(profile.get("ssh_username", "root") or "root"),
        password=str(profile.get("ssh_password", "") or ""),
        key_path=str(profile.get("ssh_key_path", "") or ""),
        remote_path=remote_path,
        assume_exists=assume_exists,
    )


class _FtpSession:
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        passive: bool,
        timeout: int,
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.passive = passive
        self.timeout = timeout
        self.ftp: ftplib.FTP | None = None

    def __enter__(self) -> ftplib.FTP:
        ftp = ftplib.FTP()
        try:
            ftp.connect(self.host, self.port, timeout=self.timeout)
            if self.username:
                ftp.login(self.username, self.password)
            else:
                ftp.login()
            ftp.set_pasv(self.passive)
        except ftplib.error_perm as exc:
            try:
                ftp.close()
            except Exception:
                pass
            message = str(exc)
            if message.startswith("530"):
                username = self.username or "(blank)"
                raise SyncUserError(
                    "MemCard Pro FTP login failed (530).\n\n"
                    f"The desktop client sent username '{username}', but the "
                    "MemCard Pro FTP server rejected the password. This usually "
                    "means the credentials saved on the card are different from "
                    "this profile, the FTP settings were saved but the card has "
                    "not been power-cycled yet, or another FTP client is holding "
                    "the card's single allowed connection.\n\n"
                    "In the MemCard Pro WebUI, enable FTP Server, set a username "
                    "and password, click Save, then manually power-cycle the "
                    "console/card. Use the same username and password here. FTP "
                    "settings should be plain FTP, passive mode, one connection."
                ) from exc
            raise SyncUserError(f"FTP login failed: {message}") from exc
        except OSError as exc:
            try:
                ftp.close()
            except Exception:
                pass
            raise SyncUserError(
                f"Could not connect to FTP host {self.host}:{self.port}: {exc}"
            ) from exc
        self.ftp = ftp
        return ftp

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.ftp is None:
            return
        try:
            self.ftp.quit()
        except Exception:
            try:
                self.ftp.close()
            except Exception:
                pass


def _ftp_connection(
    host: str,
    port: int = 21,
    username: str = "",
    password: str = "",
    passive: bool = True,
    timeout: int = 30,
) -> _FtpSession:
    if not host:
        raise OSError("FTP host is required.")
    return _FtpSession(host, int(port or 21), username, password, passive, timeout)


def _ftp_norm(path: str) -> str:
    value = (path or "/").replace("\\", "/").strip()
    if not value:
        value = "/"
    if not value.startswith("/"):
        value = "/" + value
    normalized = posixpath.normpath(value)
    return "/" if normalized in {"", "."} else normalized


def _ftp_join(base: str, *parts: str) -> str:
    current = _ftp_norm(base)
    for part in parts:
        piece = str(part or "").strip("/")
        if piece:
            current = posixpath.join(current, piece)
    return _ftp_norm(current)


def _ftp_child_unless_named(base: str, child: str) -> str:
    base = _ftp_norm(base)
    if posixpath.basename(base).lower() == child.lower():
        return base
    return _ftp_join(base, child)


def _ftp_parse_modify(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return (
            datetime.strptime(value[:14], "%Y%m%d%H%M%S")
            .replace(tzinfo=timezone.utc)
            .timestamp()
        )
    except ValueError:
        return 0.0


def _ftp_size(ftp: ftplib.FTP, path: str) -> int:
    try:
        ftp.voidcmd("TYPE I")
    except Exception:
        pass
    try:
        size = ftp.size(path)
        return int(size or 0)
    except Exception:
        return 0


def _ftp_mtime(ftp: ftplib.FTP, path: str) -> float:
    try:
        response = ftp.sendcmd(f"MDTM {path}")
        if response.startswith("213 "):
            return _ftp_parse_modify(response[4:].strip())
    except Exception:
        pass
    return 0.0


def _ftp_is_dir(ftp: ftplib.FTP, path: str) -> bool:
    try:
        current = ftp.pwd()
    except Exception:
        current = ""
    try:
        ftp.cwd(path)
        if current:
            try:
                ftp.cwd(current)
            except Exception:
                pass
        return True
    except Exception:
        return False


def _ftp_dir_exists(ftp: ftplib.FTP, path: str) -> bool:
    return _ftp_is_dir(ftp, _ftp_norm(path))


def _ftp_path_exists(ftp: ftplib.FTP, path: str) -> bool:
    path = _ftp_norm(path)
    if _ftp_dir_exists(ftp, path):
        return True
    try:
        ftp.voidcmd("TYPE I")
    except Exception:
        pass
    try:
        ftp.size(path)
        return True
    except Exception:
        return False


_FTP_PASV_RE = re.compile(
    r"\((\d+),(\d+),(\d+),(\d+),(\d+),(\d+)\)"
)


def _ftp_pasv_endpoint(ftp: ftplib.FTP) -> tuple[str, int]:
    response = ftp.sendcmd("PASV")
    match = _FTP_PASV_RE.search(response)
    if not match:
        raise ftplib.error_proto(response)
    numbers = [int(part) for part in match.groups()]
    host = ".".join(str(part) for part in numbers[:4])
    port = numbers[4] * 256 + numbers[5]
    return host, port


def _ftp_transfer_bytes(
    ftp: ftplib.FTP,
    command: str,
    expected_size: int | None = None,
) -> bytes:
    """Run a passive data-transfer command without ftplib's TYPE A/NLST path.

    MemCard Pro's FTP server is intentionally tiny and can stall on the command
    sequence Python's ``ftplib.nlst`` uses. This mirrors the sequence that works
    reliably against the card: binary mode, PASV, open one data socket, command.
    """
    try:
        ftp.voidcmd("TYPE I")
        host, port = _ftp_pasv_endpoint(ftp)
        timeout = None
        sock = getattr(ftp, "sock", None)
        if sock is not None:
            timeout = sock.gettimeout()
        with socket.create_connection((host, port), timeout=timeout) as data_sock:
            response = ftp.sendcmd(command)
            if not response.startswith(("125", "150")):
                raise ftplib.error_reply(response)
            chunks: list[bytes] = []
            received = 0
            while True:
                chunk = data_sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                received += len(chunk)
                if expected_size is not None and received >= expected_size:
                    break
        ftp.voidresp()
        data = b"".join(chunks)
        if expected_size is not None:
            return data[:expected_size]
        return data
    except (TimeoutError, socket.timeout) as exc:
        raise SyncUserError(
            "MemCard Pro FTP data connection timed out.\n\n"
            "Close FileZilla or any other FTP client, cancel any running scan, "
            "then try again. The MemCard Pro FTP server supports only one "
            "connection/transfer at a time and can require a card power-cycle "
            "after a stalled transfer."
        ) from exc


def _ftp_store_transfer_bytes(ftp: ftplib.FTP, command: str, data: bytes) -> None:
    try:
        ftp.voidcmd("TYPE I")
        host, port = _ftp_pasv_endpoint(ftp)
        timeout = None
        sock = getattr(ftp, "sock", None)
        if sock is not None:
            timeout = sock.gettimeout()
        with socket.create_connection((host, port), timeout=timeout) as data_sock:
            response = ftp.sendcmd(command)
            if not response.startswith(("125", "150")):
                raise ftplib.error_reply(response)
            data_sock.sendall(data)
        ftp.voidresp()
    except TimeoutError as exc:
        raise SyncUserError(
            "MemCard Pro FTP upload timed out.\n\n"
            "Close FileZilla or any other FTP client, cancel any running scan, "
            "then try again. The MemCard Pro FTP server supports only one "
            "connection/transfer at a time and can require a card power-cycle "
            "after a stalled transfer."
        ) from exc


def _ftp_nlst(ftp: ftplib.FTP, path: str) -> list[str]:
    path = _ftp_norm(path)
    if not hasattr(ftp, "sock"):
        try:
            return [
                name
                for name, _facts in ftp.mlsd(path)
                if name not in {".", ".."}
            ]
        except Exception:
            return []
    current = ""
    try:
        current = ftp.pwd()
    except Exception:
        pass
    try:
        ftp.cwd(path)
        payload = _ftp_transfer_bytes(ftp, "NLST")
    finally:
        if current:
            try:
                ftp.cwd(current)
            except Exception:
                pass
    text = payload.decode("utf-8", errors="replace")
    names = [line.strip() for line in re.split(r"[\r\n]+", text) if line.strip()]
    return names


def _ftp_entries_from_names(path: str, names: list[str], is_dir: bool) -> list[FtpEntry]:
    entries: list[FtpEntry] = []
    for name in names:
        clean_name = posixpath.basename(str(name).strip().rstrip("/"))
        if not clean_name or clean_name in {".", ".."}:
            continue
        entries.append(
            FtpEntry(
                name=clean_name,
                path=_ftp_join(path, clean_name),
                is_dir=is_dir,
            )
        )
    return sorted(entries, key=lambda item: item.name.lower())


def _ftp_list_entries(ftp: ftplib.FTP, path: str) -> list[FtpEntry]:
    path = _ftp_norm(path)
    banner = getattr(ftp, "welcome", "") or ""
    if "MemCardPRO FTP Server" not in banner:
        try:
            entries = []
            for name, facts in ftp.mlsd(path):
                if name in {".", ".."}:
                    continue
                full = _ftp_join(path, name)
                entry_type = (facts.get("type") or "").lower()
                is_dir = entry_type == "dir"
                entries.append(
                    FtpEntry(
                        name=name,
                        path=full,
                        is_dir=is_dir,
                        size=int(facts.get("size") or 0),
                        mtime=_ftp_parse_modify(facts.get("modify")),
                    )
                )
            return sorted(entries, key=lambda item: item.name.lower())
        except Exception:
            pass

    try:
        names = _ftp_nlst(ftp, path)
    except SyncUserError:
        raise
    except Exception:
        entries = []
        try:
            names = ftp.nlst(path)
        except Exception:
            return []

    entries: list[FtpEntry] = []
    for raw in names:
        raw = str(raw).rstrip("/")
        if not raw:
            continue
        if raw.startswith("/"):
            full = _ftp_norm(raw)
            name = posixpath.basename(full)
        else:
            name = posixpath.basename(raw)
            full = _ftp_join(path, raw)
        if name in {".", ".."}:
            continue
        is_dir = _ftp_is_dir(ftp, full)
        entries.append(
            FtpEntry(
                name=name,
                path=full,
                is_dir=is_dir,
                size=0 if is_dir else _ftp_size(ftp, full),
                mtime=0.0 if is_dir else _ftp_mtime(ftp, full),
            )
        )
    return sorted(entries, key=lambda item: item.name.lower())


def _ftp_download_bytes(
    ftp: ftplib.FTP,
    path: str,
    expected_size: int | None = None,
) -> bytes:
    if not hasattr(ftp, "sock"):
        buffer = io.BytesIO()
        ftp.retrbinary(f"RETR {_ftp_norm(path)}", buffer.write)
        data = buffer.getvalue()
        if expected_size is not None:
            return data[:expected_size]
        return data
    return _ftp_transfer_bytes(
        ftp,
        f"RETR {_ftp_norm(path)}",
        expected_size=expected_size,
    )


def _ftp_makedirs(ftp: ftplib.FTP, remote_dir: str) -> None:
    remote_dir = _ftp_norm(remote_dir)
    if remote_dir == "/":
        return
    current = ""
    for part in remote_dir.strip("/").split("/"):
        current = _ftp_join(current or "/", part)
        if _ftp_dir_exists(ftp, current):
            continue
        try:
            ftp.mkd(current)
        except Exception:
            pass


def _ftp_upload_bytes(ftp: ftplib.FTP, path: str, data: bytes) -> None:
    path = _ftp_norm(path)
    parent = posixpath.dirname(path) or "/"
    _ftp_makedirs(ftp, parent)
    if not hasattr(ftp, "sock"):
        ftp.storbinary(f"STOR {path}", io.BytesIO(data))
        return
    _ftp_store_transfer_bytes(ftp, f"STOR {path}", data)


def _ftp_profile_root(profile: dict) -> str:
    return _ftp_norm(profile.get("path", "/") or "/")


def _ftp_profile_save_path(profile: dict, remote_path: str) -> FtpSavePath:
    return FtpSavePath(
        host=str(profile.get("ftp_host", "")).strip(),
        port=int(profile.get("ftp_port", 21) or 21),
        username=str(profile.get("ftp_username", "")),
        password=str(profile.get("ftp_password", "")),
        remote_path=_ftp_norm(remote_path),
        passive=bool(profile.get("ftp_passive", True)),
        timeout=int(profile.get("ftp_timeout", 30) or 30),
    )


def _ftp_existing_or_default_dir(
    ftp: ftplib.FTP,
    candidates: list[str],
) -> str:
    for candidate in candidates:
        if _ftp_dir_exists(ftp, candidate):
            return _ftp_norm(candidate)
    return _ftp_norm(candidates[0] if candidates else "/")


def _memcard_ftp_roots(root: str, system: str) -> list[str]:
    system = system.upper()
    root = _ftp_norm(root)
    if system == "PS1":
        return [_ftp_child_unless_named(root, "MemoryCards"), root]
    if system == "PS2":
        if posixpath.basename(root).lower() == "ps2":
            return [root]
        return [
            _ftp_join(root, "PS2"),
            _ftp_join(_ftp_child_unless_named(root, "MemoryCards"), "PS2"),
            root,
        ]
    if system == "GC":
        if posixpath.basename(root).lower() in {"gc", "gamecube"}:
            return [root]
        return [
            _ftp_join(root, "GC"),
            _ftp_join(_ftp_child_unless_named(root, "MemoryCards"), "GC"),
            root,
        ]
    return [root]


def is_ftp_save_path(path: object) -> bool:
    return isinstance(path, FtpSavePath)


def build_memcard_pro_ftp_path(
    profile: dict,
    title_id: str,
    system: str,
) -> FtpSavePath | None:
    """Resolve the remote MemCard Pro FTP destination for a server-only save."""
    system = (system or "").upper()
    root = _ftp_profile_root(profile)
    if system not in {"PS1", "PS2", "GC"}:
        return None

    with _ftp_connection(
        str(profile.get("ftp_host", "")).strip(),
        int(profile.get("ftp_port", 21) or 21),
        str(profile.get("ftp_username", "")),
        str(profile.get("ftp_password", "")),
        bool(profile.get("ftp_passive", True)),
        int(profile.get("ftp_timeout", 30) or 30),
    ) as ftp:
        base_dir = _ftp_existing_or_default_dir(
            ftp, _memcard_ftp_roots(root, system)
        )
        if system in {"PS1", "PS2"}:
            serial_dir = _memcard_serial_dirname(title_id)
            ext = ".mcd" if system == "PS1" else ".mc2"
            remote = _ftp_join(base_dir, serial_dir, f"{serial_dir}-1{ext}")
            return _ftp_profile_save_path(profile, remote)

        gc_code = title_id[3:].upper() if title_id.upper().startswith("GC_") else ""
        if len(gc_code) != 4:
            return None
        existing = next(
            (
                entry
                for entry in _ftp_list_entries(ftp, base_dir)
                if entry.is_dir
                and entry.name.upper().startswith(f"DL-DOL-{gc_code}-")
            ),
            None,
        )
        folder_name = existing.name if existing else f"DL-DOL-{gc_code}-USA"
        remote = _ftp_join(base_dir, folder_name, f"{folder_name}-1.raw")
        return _ftp_profile_save_path(profile, remote)


def finalize_memcard_pro_download(path: object, game_name: str) -> None:
    """Write MemCard Pro companion metadata for local or FTP card downloads."""
    if isinstance(path, FtpSavePath):
        remote_dir = posixpath.dirname(path.remote_path)
        serial_dir = posixpath.basename(remote_dir)
        if not serial_dir:
            return
        if path.suffix.lower() == ".mc2":
            txt_remote = _ftp_join(remote_dir, "name.txt")
        else:
            safe_name = (
                re.sub(r'[<>:"/\\|?*]', "_", (game_name or "").strip())
                or serial_dir
            )
            txt_remote = _ftp_join(remote_dir, f"{safe_name}.txt")
        meta_path = _ftp_profile_save_path(
            {
                "ftp_host": path.host,
                "ftp_port": path.port,
                "ftp_username": path.username,
                "ftp_password": path.password,
                "ftp_passive": path.passive,
                "ftp_timeout": path.timeout,
            },
            txt_remote,
        )
        meta_path.write_bytes(((game_name or serial_dir).strip() + "\n").encode())
        return

    local_path = Path(path)
    serial_dir = local_path.parent.name
    if not serial_dir:
        return
    if local_path.suffix.lower() == ".mc2":
        txt_path = local_path.parent / "name.txt"
    else:
        txt_name = (
            re.sub(r'[<>:"/\\|?*]', "_", (game_name or "").strip()) or serial_dir
        )
        txt_path = local_path.parent / f"{txt_name}.txt"
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.write_text((game_name or serial_dir).strip() + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# State file (tracks last-synced hash per title, like 3DS client's state/)
# ---------------------------------------------------------------------------

STATE_FILE = Path(__file__).parent / ".sync_state.json"
SCAN_CACHE_FILE = Path(__file__).parent / ".scan_cache.json"
SLOT_MAPPING_FILE = Path(__file__).parent / ".slot_mappings.json"
SATURN_ARCHIVE_STATE_FILE = Path(__file__).parent / ".saturn_archives.json"
REMOTE_HASH_CACHE_FILE = Path(__file__).parent / ".remote_hash_cache.json"
_SCAN_CACHE: dict[str, dict[str, object]] | None = None
_SCAN_CACHE_DIRTY = False
_SLOT_MAPPINGS: dict[str, dict[str, str]] | None = None
_SLOT_MAPPINGS_DIRTY = False
_SATURN_ARCHIVE_STATE: dict[str, list[str]] | None = None
_SATURN_ARCHIVE_STATE_DIRTY = False
_REMOTE_HASH_CACHE: dict[str, dict[str, object]] | None = None
_REMOTE_HASH_CACHE_DIRTY = False

# Per-title Saroo metadata populated by _scan_saroo().
# Keys are title_id strings; values are dicts with:
#   game_id:      original Saroo game ID string (16 chars, may have trailing spaces)
#   slot_index:   byte offset of the physical slot within SS_SAVE.BIN
#   native_bytes: mednafen-compatible 32KB image (bytes) — the payload to upload
#   bkr_path:     path to mednafen .bkr file, if found (str, may be empty)
_SAROO_META: dict[str, dict] = {}


def _canonicalize_state_keys(state: dict[str, str]) -> dict[str, str]:
    """Fold gamecode-form keys (GC_grse) onto their canonical form (GC_GRSE).

    Builds before the GC title-id canonicalisation wrote lowercase keys.  Left
    alone, every GameCube game would lose its last_synced_hash on upgrade and
    come back as a spurious conflict.  An existing canonical entry wins.
    """
    out: dict[str, str] = {}
    for key, value in state.items():
        canonical = canonicalize_code_form_title_id(key)
        if canonical == key or canonical not in state:
            out[canonical] = value
    return out


def _load_state() -> dict[str, str]:
    if STATE_FILE.exists():
        try:
            return _canonicalize_state_keys(
                json.loads(STATE_FILE.read_text(encoding="utf-8"))
            )
        except Exception:
            pass
    return {}


def _save_state(state: dict[str, str]) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _update_state(title_id: str, hash_val: str) -> None:
    state = _load_state()
    state[canonicalize_code_form_title_id(title_id)] = hash_val
    _save_state(state)


def _load_saturn_archive_state() -> dict[str, list[str]]:
    global _SATURN_ARCHIVE_STATE
    if _SATURN_ARCHIVE_STATE is not None:
        return _SATURN_ARCHIVE_STATE
    if SATURN_ARCHIVE_STATE_FILE.exists():
        try:
            data = json.loads(SATURN_ARCHIVE_STATE_FILE.read_text(encoding="utf-8"))
            entries = data.get("entries", {})
            if isinstance(entries, dict):
                normalized: dict[str, list[str]] = {}
                for title_id, archive_names in entries.items():
                    if not isinstance(archive_names, list):
                        continue
                    values = [
                        str(name).strip().upper()
                        for name in archive_names
                        if str(name).strip()
                    ]
                    if values:
                        normalized[str(title_id).strip().upper()] = sorted(set(values))
                _SATURN_ARCHIVE_STATE = normalized
                return _SATURN_ARCHIVE_STATE
        except Exception:
            pass
    _SATURN_ARCHIVE_STATE = {}
    return _SATURN_ARCHIVE_STATE


def _mark_saturn_archive_state_dirty() -> None:
    global _SATURN_ARCHIVE_STATE_DIRTY
    _SATURN_ARCHIVE_STATE_DIRTY = True


def _flush_saturn_archive_state() -> None:
    global _SATURN_ARCHIVE_STATE_DIRTY
    if not _SATURN_ARCHIVE_STATE_DIRTY:
        return
    state = _load_saturn_archive_state()
    SATURN_ARCHIVE_STATE_FILE.write_text(
        json.dumps({"entries": state}, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    _SATURN_ARCHIVE_STATE_DIRTY = False


def _get_saturn_archive_names(title_id: str) -> list[str]:
    return list(_load_saturn_archive_state().get((title_id or "").upper(), []))


def _set_saturn_archive_names(title_id: str, archive_names: list[str]) -> None:
    normalized = sorted(
        {
            str(name).strip().upper()
            for name in archive_names
            if str(name).strip()
        }
    )
    state = _load_saturn_archive_state()
    key = (title_id or "").upper()
    if normalized:
        state[key] = normalized
    else:
        state.pop(key, None)
    _mark_saturn_archive_state_dirty()
    _flush_saturn_archive_state()


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


def _load_remote_hash_cache() -> dict[str, dict[str, object]]:
    global _REMOTE_HASH_CACHE
    if _REMOTE_HASH_CACHE is not None:
        return _REMOTE_HASH_CACHE
    if REMOTE_HASH_CACHE_FILE.exists():
        try:
            data = json.loads(REMOTE_HASH_CACHE_FILE.read_text(encoding="utf-8"))
            entries = data.get("entries", {})
            if isinstance(entries, dict):
                _REMOTE_HASH_CACHE = entries
                return _REMOTE_HASH_CACHE
        except Exception:
            pass
    _REMOTE_HASH_CACHE = {}
    return _REMOTE_HASH_CACHE


def _mark_remote_hash_cache_dirty() -> None:
    global _REMOTE_HASH_CACHE_DIRTY
    _REMOTE_HASH_CACHE_DIRTY = True


def _flush_remote_hash_cache() -> None:
    global _REMOTE_HASH_CACHE_DIRTY
    if not _REMOTE_HASH_CACHE_DIRTY:
        return
    cache = _load_remote_hash_cache()
    REMOTE_HASH_CACHE_FILE.write_text(
        json.dumps({"entries": cache}, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    _REMOTE_HASH_CACHE_DIRTY = False


def _remote_hash_cache_key(profile_scope: str, remote_path: str) -> str:
    return f"ftp|{profile_scope}|{_ftp_norm(remote_path)}"


def _memcard_slot_id_from_name(name: str) -> str:
    stem = posixpath.splitext(str(name or ""))[0]
    match = re.search(r"[-_](\d+)$", stem)
    if not match:
        return "slot1"
    try:
        return f"slot{int(match.group(1))}"
    except ValueError:
        return "slot1"


def _memcard_hash_cache_key(system: str, title_id: str, card_name: str) -> str | None:
    system = (system or "").upper()
    suffix = posixpath.splitext(str(card_name or ""))[1].lower()
    if system in {"PS1", "PS2"}:
        normalized_title = _normalize_ps1_serial(title_id or "")
        mode = "card"
    elif system == "GC":
        normalized_title = re.sub(
            r"^GC[_-]?", "", str(title_id or "").upper()
        )
        mode = "gci"
    else:
        return None
    if not normalized_title:
        return None
    slot_id = _memcard_slot_id_from_name(card_name)
    return f"memcard-pro|{system}|{normalized_title}|{slot_id}|{suffix}|{mode}"


def _memcard_hash_cache_key_from_path(
    path: str | Path,
    system: str | None = None,
    title_id: str | None = None,
) -> str | None:
    path_text = str(path).replace("\\", "/")
    name = posixpath.basename(path_text)
    suffix = posixpath.splitext(name)[1].lower()
    parent = posixpath.basename(posixpath.dirname(path_text))
    resolved_system = (system or "").upper()
    resolved_title = title_id or ""

    if not resolved_system:
        if suffix in {".mc2", ".ps2"}:
            resolved_system = "PS2"
        elif suffix in {".mcd", ".mcr"}:
            resolved_system = "PS1"
        elif suffix == ".raw":
            resolved_system = "GC"

    if not resolved_title:
        if resolved_system == "GC":
            gc_code = _gc_code_from_folder(parent)
            resolved_title = f"GC_{gc_code.upper()}" if gc_code else ""
        else:
            resolved_title = parent

    return _memcard_hash_cache_key(resolved_system, resolved_title, name)


def _get_cached_hash_for_key(
    cache_key: str | None,
    size: int,
    mtime: float,
) -> str | None:
    if not cache_key or mtime <= 0:
        return None
    entry = _load_remote_hash_cache().get(cache_key)
    if not isinstance(entry, dict):
        return None
    hash_val = str(entry.get("hash") or "")
    if not hash_val:
        return None
    try:
        cached_size = int(entry.get("size", -1))
        cached_mtime = float(entry.get("mtime", 0))
    except (TypeError, ValueError):
        return None
    if cached_size != int(size):
        return None
    if abs(cached_mtime - float(mtime)) > 0.001:
        return None
    return hash_val


def _set_cached_hash_for_key(
    cache_key: str | None,
    size: int,
    mtime: float,
    hash_val: str,
) -> None:
    if not cache_key or not hash_val or mtime <= 0:
        return
    cache = _load_remote_hash_cache()
    cache[cache_key] = {
        "hash": hash_val,
        "size": int(size),
        "mtime": float(mtime),
        "updated_at": time.time(),
    }
    _mark_remote_hash_cache_dirty()


def _get_cached_remote_hash(
    profile_scope: str,
    remote_path: str,
    size: int,
    mtime: float,
) -> str | None:
    return _get_cached_hash_for_key(
        _remote_hash_cache_key(profile_scope, remote_path),
        size,
        mtime,
    )


def _set_cached_remote_hash(
    profile_scope: str,
    remote_path: str,
    size: int,
    mtime: float,
    hash_val: str,
) -> None:
    _set_cached_hash_for_key(
        _remote_hash_cache_key(profile_scope, remote_path),
        size,
        mtime,
        hash_val,
    )


def _invalidate_remote_hash_path(remote_path: str) -> None:
    normalized = _ftp_norm(remote_path)
    memcard_key = _memcard_hash_cache_key_from_path(normalized)
    cache = _load_remote_hash_cache()
    stale_keys = [
        key
        for key in cache
        if key.endswith("|" + normalized) or (memcard_key and key == memcard_key)
    ]
    if not stale_keys:
        return
    for key in stale_keys:
        cache.pop(key, None)
    _mark_remote_hash_cache_dirty()


def _invalidate_memcard_hash_path(
    path: str | Path,
    system: str | None = None,
    title_id: str | None = None,
) -> None:
    memcard_key = _memcard_hash_cache_key_from_path(path, system, title_id)
    if not memcard_key:
        return
    cache = _load_remote_hash_cache()
    if memcard_key not in cache:
        return
    cache.pop(memcard_key, None)
    _mark_remote_hash_cache_dirty()


def _clear_remote_hash_cache() -> None:
    global _REMOTE_HASH_CACHE, _REMOTE_HASH_CACHE_DIRTY
    _REMOTE_HASH_CACHE = {}
    _REMOTE_HASH_CACHE_DIRTY = False
    try:
        REMOTE_HASH_CACHE_FILE.unlink(missing_ok=True)
    except Exception:
        pass


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


def clear_mister_catalog_cache() -> None:
    """Drop the cached ROM-catalog name→title_id index (see scan helpers)."""
    _mister_catalog_cache.clear()


def clear_scan_cache() -> None:
    """Remove cached canonical scan matches so they can be recomputed."""
    global _SCAN_CACHE, _SCAN_CACHE_DIRTY
    _SCAN_CACHE = {}
    _SCAN_CACHE_DIRTY = False
    try:
        SCAN_CACHE_FILE.unlink(missing_ok=True)
    except Exception:
        pass
    _clear_remote_hash_cache()
    clear_mister_catalog_cache()


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


def _is_unsafe_bundle_path(rel_path: str) -> bool:
    """True if a server-supplied bundle member would escape its dest dir."""
    p = rel_path.replace("\\", "/")
    return (
        p.startswith("/")
        or ".." in Path(p).parts
        or (len(p) >= 2 and p[1] == ":")  # drive-letter absolute (Windows)
    )


def _extract_bundle_to_dir(data: bytes, dest_dir: Path) -> None:
    files = _parse_dir_bundle(data)
    dest_dir.mkdir(parents=True, exist_ok=True)
    _clear_dir_contents(dest_dir)
    for rel_path, content in files:
        if _is_unsafe_bundle_path(rel_path):
            raise RuntimeError(f"Refusing unsafe bundle member: {rel_path}")
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

    New format:  profile["systems"] = [{system, enabled, save_ext, save_folder, rom_folder}, …]
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
        "ftp_host": profile.get("ftp_host", ""),
        "ftp_port": profile.get("ftp_port", ""),
        "ftp_username": profile.get("ftp_username", ""),
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
                             [{system, enabled, save_ext, save_folder, rom_folder}, …]
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
    profile_scope = _profile_runtime_scope(profile)

    if device_type == "MemCard Pro FTP":
        if system_override in {"PS1", "PS2", "GC"}:
            results = _scan_memcard_pro_ftp(
                profile,
                system_override,
                progress_callback=progress_callback,
                profile_scope=profile_scope,
            )
        else:
            results = []
        _flush_scan_cache()
        _flush_remote_hash_cache()
        _emit_progress(
            progress_callback,
            f"Found {len(results)} remote save entries.",
            len(results),
            len(results),
        )
        return _dedup_saves(results)

    if device_type == "MiSTer" and mister_profile_uses_ssh(profile):
        results = _scan_mister_ssh(
            profile,
            systems_config,
            progress_callback=progress_callback,
            profile_scope=profile_scope,
        )
        _flush_scan_cache()
        _flush_remote_hash_cache()
        _emit_progress(
            progress_callback,
            f"Found {len(results)} MiSTer save entries.",
            len(results),
            len(results),
        )
        # No _dedup_saves() here: every entry is a real file the core wrote,
        # so two cards for one game (folder-named + CD/disc-serial-named) must
        # stay separate rows instead of merging into one multi-path entry.
        return results

    save_folder = Path(save_folder_str) if save_folder_str else None
    rom_folder = Path(rom_folder_str) if rom_folder_str else None
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
            rom_root=rom_folder if rom_folder and rom_folder.exists() else None,
            saturn_config=systems_config.get("SAT", {}),
            systems_config=systems_config,
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
        if system_override == "DC":
            results = _scan_memcard_pro_dc(
                folder,
                progress_callback=progress_callback,
                profile_scope=profile_scope,
            )
        elif system_override in {"PS1", "PS2", "GC"}:
            results = _scan_memcard_pro(
                folder,
                system_override,
                progress_callback=progress_callback,
                profile_scope=profile_scope,
            )

    elif device_type == "MemCard Pro DC":
        # Dreamcast card manager: <root>/Dreamcast/<GAMEID>/<GAMEID>-1.vmu
        results = _scan_memcard_pro_dc(
            folder,
            progress_callback=progress_callback,
            profile_scope=profile_scope,
        )

    elif device_type == "openMenu":
        # openMenu's Serial VMU feature backs each game's VMU up to a serial SD
        # adapter — a different card from the GDEMU one holding the games, so
        # the save folder is scanned, falling back to the game folder.
        results = _scan_openmenu_vmu(
            save_folder if (save_folder and save_folder.exists()) else folder,
            progress_callback=progress_callback,
            profile_scope=profile_scope,
        )

    elif device_type == "GDEMU":
        # GDEMU stores no saves of its own — the VMU does.  This profile exists
        # to install games onto the card; pair it with an openMenu or MemCard
        # PRO DC profile to sync the saves.
        results = []

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

    elif device_type == "Super SD System 3":
        # Super SD System 3: fixed card layout — HuCard/, Cd/<Game>/ and bup/
        # all hang off the SD card root, so only `path` is configurable.
        if rom_folder and rom_folder.exists():
            results = _scan_supersd3(
                rom_folder,
                systems_config,
                progress_callback=progress_callback,
                enable_auto_normalize=enable_auto_normalize,
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
    _flush_remote_hash_cache()
    _emit_progress(
        progress_callback,
        f"Found {len(results)} local save entries.",
        len(results),
        len(results),
    )
    return _dedup_saves(results)


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
    if hasattr(save.path, "sync_key"):
        return f"{save.profile_scope}|{save.path.sync_key()}"
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


def _resolve_saturn_server_hash_match(
    save: SaveFile, server_titles: dict[str, dict]
) -> tuple[str, str] | None:
    if save.system != "SAT" or not save.hash:
        return None
    matches = [
        title_id
        for title_id, meta in server_titles.items()
        if (meta.get("system") or meta.get("platform", "")).upper() == "SAT"
        and meta.get("save_hash", "") == save.hash
    ]
    if len(matches) != 1:
        return None
    matched_id = matches[0]
    if matched_id == save.title_id:
        return None
    return matched_id, f"Matched Saturn server hash: {matched_id}"


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
    save_recursive: bool = True,
    progress_callback=None,
    enable_auto_normalize: bool = True,
    mirror_relative_path: bool = False,
    profile_scope: str = "",
    saves_only: bool = False,
    allow_fallback_exts: bool = True,
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
        save_candidates = _safe_walk(save_folder, recursive=save_recursive)
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
                    allow_fallback_exts
                    and ext in SAVE_EXTENSIONS
                    and rel_key not in save_index
                ):
                    save_index[rel_key] = f
            else:
                if ext == save_ext.lower():
                    save_index[f.stem.lower()] = f  # exact extension wins
                elif (
                    allow_fallback_exts
                    and ext in SAVE_EXTENSIONS
                    and f.stem.lower() not in save_index
                ):
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
    if system == "SAT":
        _debug_scan(
            "ROM/save match scan: "
            f"system={system} rom_folder={rom_folder} save_folder={save_folder} "
            f"save_ext={save_ext} recursive={recursive} candidates={total}"
        )
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
        if system == "SAT":
            _debug_scan(
                "ROM/save matched: "
                f"rom={rom_file.name} expected_save={save_path} "
                f"save_exists={save_exists} hash={file_hash[:12] if file_hash else ''}"
            )
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
        if system == "SAT":
            _debug_scan(
                "ROM/save unmatched local save kept: "
                f"save={save_path} hash={file_hash[:12] if file_hash else ''}"
            )

    return _dedup_saves(results)


def _scan_retroarch(
    root: Path,
    rom_root: Path | None = None,
    saturn_config: dict | None = None,
    systems_config: dict[str, dict] | None = None,
    progress_callback=None,
    enable_auto_normalize: bool = True,
    profile_scope: str = "",
) -> list[SaveFile]:
    """Scan RetroArch saves/CoreName/game.srm structure."""
    results = []
    systems_config = systems_config or {}

    def _system_config(system_code: str) -> dict:
        return systems_config.get(system_code, {}) or {}

    def _system_save_override(system_code: str) -> Path | None:
        folder_str = _system_config(system_code).get("save_folder", "")
        if not folder_str:
            return None
        candidate = Path(folder_str)
        return candidate if candidate.exists() else None

    def _system_rom_root(system_code: str) -> Path | None:
        candidate_root = rom_root
        folder_str = _system_config(system_code).get("rom_folder", "")
        if folder_str:
            candidate = Path(folder_str)
            if candidate.exists():
                candidate_root = candidate
        return candidate_root

    saturn_format = _retroarch_saturn_format(
        (saturn_config or {}).get("save_ext", ""),
        (saturn_config or {}).get("save_folder", ""),
    )
    saturn_rom_root = _system_rom_root("SAT")
    saturn_save_root = _system_save_override("SAT") or root
    saturn_mednafen_save_root = _retroarch_saturn_mednafen_save_root(saturn_save_root)
    _debug_scan(
        "RetroArch scan start: "
        f"root={root} rom_root={rom_root} systems={sorted(systems_config.keys())}"
    )
    _debug_scan(
        "RetroArch SAT config: "
        f"format={saturn_format} saturn_rom_root={saturn_rom_root} "
        f"saturn_save_root={saturn_save_root} "
        f"saturn_mednafen_save_root={saturn_mednafen_save_root}"
    )

    handled_override_systems: set[str] = set()
    handled_configured_root_systems: set[str] = set()
    for system_code, sys_info in sorted(systems_config.items()):
        if system_code == "SAT":
            continue
        save_override_str = sys_info.get("save_folder", "")
        if not save_override_str:
            continue
        save_override = Path(save_override_str)
        if not save_override.exists():
            continue
        handled_override_systems.add(system_code)
        selected_core = _retroarch_selected_core(sys_info)
        _debug_scan(
            "RetroArch override scan: "
            f"system={system_code} core={selected_core or 'Auto'} "
            f"save_root={save_override}"
        )
        results.extend(
            _scan_flat(
                save_override,
                system_code,
                progress_callback=progress_callback,
                enable_auto_normalize=enable_auto_normalize,
                profile_scope=profile_scope,
            )
        )

    for system_code, sys_info in sorted(systems_config.items()):
        if system_code == "SAT" or system_code in handled_override_systems:
            continue
        sys_rom_root = _system_rom_root(system_code)
        if not sys_rom_root or not sys_rom_root.exists():
            continue
        sys_ext = resolve_save_ext(
            system_code,
            sys_info.get("save_ext", ".srm") or ".srm",
            fallback=".srm",
        )
        selected_core = _retroarch_selected_core(sys_info)
        handled_configured_root_systems.add(system_code)
        _debug_scan(
            "RetroArch configured root scan: "
            f"system={system_code} core={selected_core or 'Auto'} "
            f"rom_root={sys_rom_root} save_root={root} save_ext={sys_ext}"
        )
        results.extend(
            _scan_roms_match_saves(
                sys_rom_root,
                root,
                system_code,
                save_ext=sys_ext,
                recursive=True,
                save_recursive=False,
                progress_callback=progress_callback,
                enable_auto_normalize=enable_auto_normalize,
                profile_scope=profile_scope,
            )
        )

    for core_dir in sorted(root.iterdir()):
        if not core_dir.is_dir():
            continue
        system = RETROARCH_CORE_MAP.get(core_dir.name)
        if not system:
            continue
        if system in handled_override_systems:
            continue
        if system in handled_configured_root_systems:
            continue
        if system == "SAT":
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

    if saturn_format == "mednafen":
        _debug_scan(
            "RetroArch SAT mednafen scan: "
            f"rom_root={saturn_rom_root} save_root={saturn_mednafen_save_root}"
        )
        if saturn_rom_root and saturn_rom_root.exists():
            results.extend(
                _scan_roms_match_saves(
                    saturn_rom_root,
                    saturn_mednafen_save_root,
                    "SAT",
                    save_ext=".bkr",
                    recursive=True,
                    save_recursive=False,
                    progress_callback=progress_callback,
                    enable_auto_normalize=enable_auto_normalize,
                    profile_scope=profile_scope,
                    allow_fallback_exts=False,
                )
            )
        else:
            results.extend(
                _scan_flat(
                    saturn_mednafen_save_root,
                    "SAT",
                    progress_callback=progress_callback,
                    enable_auto_normalize=enable_auto_normalize,
                    profile_scope=profile_scope,
                )
            )
    elif saturn_format == "yabause":
        _debug_scan(
            "RetroArch SAT yabause scan: "
            f"rom_root={saturn_rom_root} save_root={saturn_save_root}"
        )
        if saturn_rom_root and saturn_rom_root.exists():
            results.extend(
                _scan_roms_match_saves(
                    saturn_rom_root,
                    saturn_save_root,
                    "SAT",
                    save_ext=".srm",
                    recursive=True,
                    save_recursive=False,
                    progress_callback=progress_callback,
                    enable_auto_normalize=enable_auto_normalize,
                    profile_scope=profile_scope,
                    allow_fallback_exts=False,
                )
            )
        else:
            results.extend(
                _scan_flat(
                    saturn_save_root,
                    "SAT",
                    progress_callback=progress_callback,
                    enable_auto_normalize=enable_auto_normalize,
                    profile_scope=profile_scope,
                )
            )
    elif saturn_format == "yabasanshiro" and saturn_rom_root and saturn_rom_root.exists():
        saturn_backup_root = saturn_save_root / "yabasanshiro"
        if saturn_save_root != root:
            saturn_backup_root = saturn_save_root
        if not saturn_backup_root.suffix:
            backup_path = saturn_backup_root / "backup.bin"
        else:
            backup_path = saturn_backup_root
        candidates = _safe_walk(saturn_rom_root, recursive=True)
        total = len(candidates)
        _debug_scan(
            "RetroArch SAT yabasanshiro scan: "
            f"rom_root={saturn_rom_root} backup_path={backup_path} "
            f"rom_candidates={total}"
        )
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
            sf = _build_save_file(
                system="SAT",
                game_name=rom_file.stem,
                source_name=rom_file.name,
                path=backup_path,
                file_hash="",
                mtime=backup_path.stat().st_mtime if backup_path.exists() else 0.0,
                save_exists=backup_path.exists(),
                enable_auto_normalize=enable_auto_normalize,
                profile_scope=profile_scope,
            )
            sf.path = backup_path
            sf.hash = ""
            sf.save_exists = backup_path.exists()
            results.append(sf)
            if idx == 1 or idx % 25 == 0 or idx == total:
                _emit_progress(
                    progress_callback, f"Scanning SAT ROMs… {idx}/{total}", idx, total
                )
    sat_results = [r for r in results if r.system == "SAT"]
    if sat_results:
        _debug_scan(f"RetroArch SAT results: count={len(sat_results)}")
        for save in sat_results:
            _debug_scan(
                "RetroArch SAT entry: "
                f"title_id={save.title_id} game={save.game_name} path={save.path} "
                f"save_exists={save.save_exists} hash={save.hash[:12] if save.hash else ''}"
            )
    else:
        _debug_scan("RetroArch SAT results: none")
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
    # Sega CD uses a different save filename on the MEGA EverDrive
    is_cd_system = system in MEGA_EVERDRIVE_CD_SYSTEMS
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


# ---------------------------------------------------------------------------
# Super SD System 3 (TerraOnion PC Engine / TurboGrafx ODE)
#
# Card layout::
#
#     <root>/HuCard/<Game (Region)>.pce      cartridge dumps (flat)
#     <root>/Cd/<Game>/<Image>.cue + .bin    one folder per CD game
#     <root>/bup/<Image stem>.bup            per-game 2 KB backup RAM
#     <root>/bup/backram.bup                 the console's shared BRAM
#
# A save is named after the *ROM file* it belongs to, not the folder: the CD
# game in ``Cd/SR/Super_Raiden_(NTSC-J)_[HCD2023].cue`` saves to
# ``bup/Super_Raiden_(NTSC-J)_[HCD2023].bup``.  The payload is a raw 2048-byte
# PC Engine BRAM image (``HUBM`` magic) — byte-identical to what Mednafen,
# Beetle PCE and the MiSTer TurboGrafx16 core write, so it syncs as-is.
# ---------------------------------------------------------------------------

SUPERSD3_HUCARD_DIR = "HuCard"
SUPERSD3_CD_DIR = "Cd"
SUPERSD3_SAVE_DIR = "bup"
SUPERSD3_SAVE_EXT = ".bup"
# The shared system BRAM, not a per-game save — never synced to a title slot.
SUPERSD3_SHARED_BRAM_STEM = "backram"
SUPERSD3_HUCARD_EXTENSIONS = frozenset({".pce", ".tg16", ".pc2"})
SUPERSD3_SUPERGRAFX_EXTENSIONS = frozenset({".sgx"})
# Which file in a Cd/<Game>/ folder names the save.  The cue sheet wins: a
# multi-track rip has one cue but a dozen "(Track NN).bin" files.
SUPERSD3_DISC_PRIORITY = (".cue", ".ccd", ".chd", ".iso", ".img", ".mdf", ".bin")


def _child_dir(root: Path, name: str) -> Path:
    """``root/name``, matched case-insensitively against existing children.

    The card is FAT32 (case-insensitive), but a Linux desktop mounting it is
    not, so ``bup`` must still find a folder written as ``BUP``.
    """
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


def _supersd3_disc_image(game_dir: Path) -> Optional[Path]:
    """The disc file whose stem names this CD game's ``.bup`` save."""
    try:
        files = sorted(f for f in game_dir.iterdir() if f.is_file())
    except OSError:
        return None
    for ext in SUPERSD3_DISC_PRIORITY:
        for f in files:
            if f.suffix.lower() == ext:
                return f
    return None


def _scan_supersd3(
    root: Path,
    systems_config: Optional[dict[str, dict]] = None,
    progress_callback=None,
    enable_auto_normalize: bool = True,
    profile_scope: str = "",
) -> list[SaveFile]:
    """Scan a Super SD System 3 SD card root for HuCard / CD games and saves."""
    save_dir = _child_dir(root, SUPERSD3_SAVE_DIR)
    hucard_dir = _child_dir(root, SUPERSD3_HUCARD_DIR)
    cd_dir = _child_dir(root, SUPERSD3_CD_DIR)

    # bup/ index: save stem (lowercased) -> path.  ".bup.bak" backups written by
    # the ODE end in ".bak" and are skipped by the suffix test.
    save_index: dict[str, Path] = {}
    for f in _safe_walk(save_dir, recursive=False):
        try:
            if not f.is_file():
                continue
        except OSError:
            continue
        if f.suffix.lower() != SUPERSD3_SAVE_EXT:
            continue
        if f.stem.lower() == SUPERSD3_SHARED_BRAM_STEM:
            continue
        save_index.setdefault(f.stem.lower(), f)

    enabled = set(systems_config or {})
    results: list[SaveFile] = []
    matched: set[Path] = set()

    def _add(system: str, rom_path: Path, stem: str) -> None:
        if enabled and system not in enabled:
            return
        save_path = save_index.get(stem.lower())
        if save_path is None:
            save_path = save_dir / (stem + SUPERSD3_SAVE_EXT)
            file_hash, mtime, save_exists = "", 0.0, False
        else:
            matched.add(save_path)
            try:
                file_hash = _hash_file(save_path)
                mtime = save_path.stat().st_mtime
                save_exists = True
            except OSError:
                file_hash, mtime, save_exists = "", 0.0, False
        # The ROM path drives canonical-name resolution; the save path is what
        # actually gets uploaded/downloaded.
        sf = _build_save_file(
            system=system,
            game_name=stem,
            source_name=rom_path.name,
            path=rom_path,
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

    # ── HuCard/ — flat cartridge dumps ────────────────────────────────────
    if hucard_dir.is_dir():
        rom_files = [f for f in _safe_walk(hucard_dir, recursive=True)]
        total = len(rom_files)
        for idx, rom_file in enumerate(rom_files, start=1):
            try:
                if not rom_file.is_file():
                    continue
            except OSError:
                continue
            if rom_file.name.startswith("."):
                continue
            ext = rom_file.suffix.lower()
            if ext in SUPERSD3_SUPERGRAFX_EXTENSIONS:
                _add("PCSG", rom_file, rom_file.stem)
            elif ext in SUPERSD3_HUCARD_EXTENSIONS:
                _add("PCE", rom_file, rom_file.stem)
            if idx == 1 or idx % 25 == 0 or idx == total:
                _emit_progress(
                    progress_callback,
                    f"Scanning HuCard ROMs… {idx}/{total}",
                    idx,
                    total,
                )

    # ── Cd/<Game>/ — one folder per disc ──────────────────────────────────
    if cd_dir.is_dir():
        try:
            game_dirs = sorted(d for d in cd_dir.iterdir() if d.is_dir())
        except OSError:
            game_dirs = []
        total = len(game_dirs)
        for idx, game_dir in enumerate(game_dirs, start=1):
            image = _supersd3_disc_image(game_dir)
            if image is not None:
                _add("PCECD", image, image.stem)
            if idx == 1 or idx % 25 == 0 or idx == total:
                _emit_progress(
                    progress_callback,
                    f"Scanning PC Engine CD folders… {idx}/{total}",
                    idx,
                    total,
                )

    # Saves whose game is no longer on the card still sync, so removing a ROM
    # never strands its BRAM.  There is no ROM to tell HuCard from CD apart, so
    # they are reported as PCECD — CD games are what actually write BRAM.
    for save_path in save_index.values():
        if save_path in matched:
            continue
        if enabled and "PCECD" not in enabled:
            continue
        try:
            file_hash = _hash_file(save_path)
            mtime = save_path.stat().st_mtime
        except OSError:
            continue
        results.append(
            _build_save_file(
                system="PCECD",
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
        parse_ss_save_bin_slots,
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
        slots = parse_ss_save_bin_slots(data)
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

    for idx, (slot_num, slot) in enumerate(slots, start=1):
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
        # ``parse_ss_save_bin_slots`` preserves the physical slot number from the
        # reserved-slot index. We must use that real slot location because the
        # reserved table can point at invalid/unparseable slots that are skipped
        # from the parsed results.
        slot_byte_offset = slot_num * 0x10000
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


# In-card PS1 save filenames look like ``BASLUS-01324DRACULA``: ``B`` + region
# letter (A/E/I) + product code.  The product code is this project's PS1 sync
# key (see CLAUDE.md — PS1 saves are keyed by the in-card code, not the disc
# serial in the filename).
_PS1_INCARD_SERIAL_RE = _mister_saves._PS1_INCARD_SERIAL_RE

# A MiSTer PSX card written while booting a real CD is named after the disc
# serial (``SLPM-86219.sav``) rather than the game folder.
_PS1_FILENAME_SERIAL_RE = _mister_saves._PS1_FILENAME_SERIAL_RE

# Cache marker for "formatted PS1 card with no save blocks at all".
_PS1_EMPTY_CARD_MARKER = "EMPTY"

# The MiSTer save format and identity rules live in shared/mister_saves.py so
# the on-device MiSTer client runs the same code instead of re-deriving it.
_ps1_serial_from_filename = _mister_saves.ps1_serial_from_filename
_ps1_card_serial = _mister_saves.ps1_card_serial
_segacd_bram_is_empty = _mister_saves.is_segacd_bram_blank
_md_to_mister = _mister_saves.md_to_mister
_MISTER_MD_SAVE_SIZE = _mister_saves.MISTER_MD_SAVE_SIZE


def _server_save_size(
    title_id: str, base_url: str, headers: dict, timeout: int = 30
) -> int:
    """Size of the save already on the server, or 0 when there is none."""
    try:
        resp = requests.get(
            f"{base_url}/api/v1/titles", headers=headers, timeout=timeout
        )
        resp.raise_for_status()
        body = resp.json()
        titles = body if isinstance(body, list) else body.get("titles", [])
        for entry in titles:
            if str(entry.get("title_id", "")) == title_id:
                return int(entry.get("save_size") or 0)
    except Exception:
        pass
    return 0


_md_from_mister = _mister_saves.md_from_mister
_ps1_card_is_empty = _mister_saves.is_ps1_card_blank
_md_packed_to_expanded = _mister_saves._md_packed_to_expanded
_md_expanded_to_packed = _mister_saves._md_expanded_to_packed
_md_sram_size = _mister_saves._md_sram_size


_mister_catalog_cache: dict[str, dict[str, str]] = {}


def _mister_catalog_index(system: str):
    """A name → server ``title_id`` matcher for one system.

    MiSTer names a save after the game file/folder, which for a translation
    patch bears no resemblance to the server's canonical title (``Castlevania
    - Symphony of the Night …`` vs ``Akumajou Dracula X …``).  The ROM catalog
    already carries the disc serial for both, so it is the bridge between the
    on-device name and the server's key.  Cached per process; call
    ``clear_scan_cache()`` to refresh.

    Matching is region-aware rather than an exact slug comparison, because the
    two sides routinely spell the same game differently — ``Final Fantasy IX
    (USA)`` on the device against ``Final Fantasy IX (USA, Canada) (Disc 1)``
    on the server.  See ``shared/title_match.py`` for the rules.
    """
    from shared.title_match import TitleMatcher

    system = (system or "").upper()
    cached = _mister_catalog_cache.get(system)
    if cached is not None:
        return cached

    matcher = TitleMatcher()
    try:
        from rom_installer import fetch_rom_catalog

        for rom in fetch_rom_catalog(system):
            matcher.add(rom.get("title_id"), rom.get("filename"),
                        rom.get("name"))
    except Exception as exc:  # offline / server down — fall back to slug ids
        _debug_scan(f"MiSTer: {system} catalog lookup unavailable: {exc}")

    _mister_catalog_cache[system] = matcher
    return matcher


def _mister_catalog_title_id(system: str, save_stem: str) -> str | None:
    """Server title_id for a MiSTer save named after its game, else None.

    Only serial-keyed systems (PS1, Saturn, …) may be resolved loosely. For a
    slug-keyed system the name *is* the identity, so a near miss would file
    two different games under one save slot - only an exact file-name hit
    counts there, for the ROM the server keyed under a title id that is not
    the file's own slug (a translation patch resolved through a DAT alias).
    Mirrors ``mister/gamesync/sync.py::_catalog_lookup``.
    """
    from shared.sync_id import uses_serial_identity

    if not uses_serial_identity(system):
        return _mister_catalog_index(system).lookup_exact(str(save_stem or ""))
    return _mister_catalog_index(system).lookup(str(save_stem or ""))


def _scan_mister_ssh(
    profile: dict,
    systems_config: dict[str, dict],
    progress_callback=None,
    profile_scope: str = "",
) -> list[SaveFile]:
    """Scan /media/fat/saves on a MiSTer over SSH/SFTP.

    Hashes are computed from a remote read and cached by (size, mtime) so
    rescans only touch changed files.  MiSTer PSX ``.sav`` files are raw 128KB
    PS1 memory cards: they are re-keyed to the in-card product code (e.g.
    ``SLUS01324``) so they share a server slot with every other PS1 client,
    and up/downloads then flow through the ``/ps1-card`` endpoints.  Cards the
    core wrote while booting a real CD are named after the disc serial
    (``SLPM-86219.sav``); that serial identifies the card until it holds a
    save block of its own.  A folder-named and a serial-named card for the
    same game can coexist — every file stays its own row (no dedup by
    title_id) so each can be synced against the server slot on its own.
    Blank cards (the core writes one on first boot) are listed with
    ``save_exists=False``: they can receive a download but never upload.
    """
    ssh = _mister_ssh_from_profile(profile)
    results: list[SaveFile] = []
    host = ssh.host

    _emit_progress(progress_callback, f"Connecting to MiSTer {host}…", 0, 0)
    with ssh:
        _emit_progress(progress_callback, f"Connected to MiSTer {host}.", 0, 0)
        saves = ssh.scan_saves()
        total = len(saves)
        for idx, sv in enumerate(saves, start=1):
            if systems_config and sv.system not in systems_config:
                continue
            remote = _mister_ssh_save_path(profile, sv.remote_path)
            title_id = sv.title_id
            cache_key = _remote_hash_cache_key(profile_scope, sv.remote_path)
            save_hash = _get_cached_hash_for_key(cache_key, sv.size, sv.mtime)
            stem = posixpath.splitext(sv.filename)[0]
            serial: str | None = None
            serials: tuple[str, ...] = ()
            filename_serial: str | None = None
            empty_card = False
            if sv.system == "PS1":
                filename_serial = _ps1_serial_from_filename(stem)
                # Cached markers: "-" = has data but no in-card serial,
                # "" would be falsy so blank cards use "EMPTY". Otherwise
                # every product code on the card, comma-joined, first save
                # first - a shared card holds several, and which one keys the
                # save depends on the file name (see resolve_title_id).
                cached_serial = _get_cached_hash_for_key(
                    f"{cache_key}|ps1serials", sv.size, sv.mtime
                )
                empty_card = cached_serial == _PS1_EMPTY_CARD_MARKER
                serials = (
                    ()
                    if cached_serial in (None, "-", _PS1_EMPTY_CARD_MARKER)
                    else tuple(cached_serial.split(","))
                )
                serial = serials[0] if serials else None
                need_read = not save_hash or cached_serial is None
            elif sv.system in ("SAT", "SEGACD") or (
                sv.system == "MD" and sv.size == _MISTER_MD_SAVE_SIZE
            ):
                cached_marker = _get_cached_hash_for_key(
                    f"{cache_key}|blank", sv.size, sv.mtime
                )
                empty_card = cached_marker == _PS1_EMPTY_CARD_MARKER
                need_read = not save_hash or cached_marker is None
            else:
                need_read = not save_hash
            if need_read:
                _emit_progress(
                    progress_callback,
                    f"Hashing MiSTer save {idx}/{total}: {sv.folder}/{sv.filename}",
                    idx,
                    total,
                )
                try:
                    if _mister_saves.needs_payload_read(sv.system, sv.size):
                        # One read serves the hash, the in-card serial and the
                        # blank check.  Which bytes get hashed is per-system and
                        # lives in shared/mister_saves.py, so the on-device
                        # client computes byte-identical hashes.
                        data = ssh.read_file(sv.remote_path)
                        identity = _mister_saves.resolve_save_identity(
                            sv.system, data
                        )
                        save_hash = hashlib.sha256(
                            identity.hash_payload
                        ).hexdigest()
                        serial = identity.serial
                        serials = identity.serials
                        empty_card = identity.is_blank
                        marker_key = (
                            "ps1serials" if sv.system == "PS1" else "blank"
                        )
                        _set_cached_hash_for_key(
                            f"{cache_key}|{marker_key}",
                            sv.size,
                            sv.mtime,
                            ",".join(serials)
                            or (_PS1_EMPTY_CARD_MARKER if empty_card else "-"),
                        )
                    else:
                        save_hash = ssh.hash_file(sv.remote_path)
                except Exception:
                    save_hash = save_hash or ""
                if save_hash:
                    _set_cached_hash_for_key(
                        cache_key, sv.size, sv.mtime, save_hash
                    )
            # One shared rule decides the key, so the desktop and the
            # on-device client always agree: in-card code, then a disc-serial
            # filename, then a catalogue hit on the game name (serial-keyed
            # systems only), then the slug.
            title_id = _mister_saves.resolve_title_id(
                sv.system,
                stem,
                _mister_saves.SaveIdentity(b"", serial=serial,
                                           is_blank=empty_card,
                                           serials=serials),
                title_id,
                catalog_lookup=_mister_catalog_title_id,
            )
            # A formatted-but-blank card (PS1 memory card, Saturn or Sega CD
            # backup RAM with no entries) means "here but hasn't saved yet":
            # keep the row visible so a server save can be downloaded into it,
            # but report it as having no save data so it can never upload an
            # empty card over a real one.
            if empty_card:
                _debug_scan(f"MiSTer: blank card (no save data) {sv.remote_path}")
                save_hash = ""
            results.append(
                SaveFile(
                    title_id=title_id,
                    path=remote,
                    hash=save_hash,
                    mtime=sv.mtime or time.time(),
                    system=sv.system,
                    game_name=posixpath.splitext(sv.filename)[0],
                    save_exists=not empty_card,
                    profile_scope=profile_scope,
                )
            )
            if idx == 1 or idx % 10 == 0 or idx == total:
                _emit_progress(
                    progress_callback,
                    f"Scanning MiSTer saves. {idx}/{total}",
                    idx,
                    total,
                )
    return results


def _mister_matching_rom_stem(
    ssh,
    folder: str,
    game_name: str,
    system: str = "",
    title_id: str = "",
) -> str | None:
    """Name the installed MiSTer game a save belongs to, else None.

    Cores load ``<name>.sav`` beside the game they booted, so a save
    downloaded for a game that is already installed must take that game's
    on-device name — not the server's display name.  The two diverge badly
    in practice: the server stores ``Ganbare Goemon 2 Kiteretsu Shougun
    Mcguiness Japan`` while the card holds ``Ganbare Goemon 2 - Kiteretsu
    Shougun McGuiness (Japan).sfc``.

    Matching is by ``make_title_id`` — the same function that keys the save
    in the first place — so a hit is exact by construction; a normalized
    name comparison is kept as a fallback.  USB is searched before SD
    because the cores prefer it.  CD games live in per-game subfolders and
    are named after the *folder*, so directories are matched first.
    """
    target_id = str(title_id or "").strip()
    target_name = normalize_rom_name(str(game_name or ""))
    if not target_id and (not target_name or target_name == "unknown"):
        return None

    def _matches(name: str, stem: str) -> bool:
        if target_id and system:
            try:
                if make_title_id(system, name) == target_id:
                    return True
            except Exception:
                pass
            # Serial-keyed systems (Saturn, PS1) can't derive their id from a
            # filename, and a translation patch shares no words with the
            # server's title — the ROM catalog knows both, so ask it.
            if _mister_catalog_title_id(system, stem) == target_id:
                return True
        return bool(target_name) and normalize_rom_name(stem) == target_name

    for root in ("/media/usb0/games", "/media/fat/games"):
        try:
            entries = ssh._sftp.listdir_attr(f"{root}/{folder}")
        except Exception:
            continue
        loose_match = None
        for attr in sorted(entries, key=lambda a: a.filename):
            name = attr.filename
            if stat_module.S_ISDIR(attr.st_mode or 0):
                # A folder name is already the stem — never split it, game
                # folders routinely contain dots ("… v1.021+hotfix").
                if _matches(name, name):
                    return name
                continue
            stem, ext = posixpath.splitext(name)
            if ext.lower() not in ROM_EXTENSIONS:
                continue
            if loose_match is None and _matches(name, stem):
                loose_match = stem
        if loose_match:
            return loose_match
    return None


def build_mister_ssh_save_path(
    profile: dict,
    title_id: str,
    system: str,
    game_name: str,
    save_ext: str = ".sav",
) -> SshSavePath | None:
    """Remote path for downloading a server-only save onto a MiSTer.

    Prefers an existing ``/media/fat/saves/<Folder>`` matching the system
    (folder names drifted across MiSTer releases), else the modern name.
    MiSTer cores always write ``.sav`` regardless of the profile's save
    extension.  For PS1 the name must be what the core will look for: the
    game's folder when that game is installed, otherwise the disc serial in
    ``SLPM-86219`` form, which is what the core uses when booting a real CD.
    """
    from mister_ssh import MISTER_SAVES_DIR
    from systems import mister_system_save_folder_candidates

    system = (system or "").upper()
    # Save folders are not always named after the games folder: the
    # TurboGrafx-16 core writes CD saves into saves/TGFX16 even though its CD
    # games live in games/TGFX16-CD.
    candidates = mister_system_save_folder_candidates(system)
    if not candidates:
        return None
    folder = candidates[0]
    stem = re.sub(r'[<>:"/\\|?*]', "_", str(game_name or "").strip()) or title_id
    serial = _normalize_ps1_serial(title_id) if system == "PS1" else None

    try:
        with _mister_ssh_from_profile(profile) as ssh:
            existing = set(ssh._sftp.listdir(MISTER_SAVES_DIR))
            folder = next((c for c in candidates if c in existing), candidates[0])
            # Every core loads ``<game>.sav`` beside the game it booted, so
            # an installed game's on-device name wins over the server's.
            rom_stem = _mister_matching_rom_stem(
                ssh, folder, game_name, system, title_id
            )
            if rom_stem:
                stem = rom_stem
            elif serial:
                # PS1 game not installed — assume it will be played from CD,
                # where the core names the card after the disc serial.
                stem = _memcard_serial_dirname(serial)
    except Exception:
        if serial and stem == title_id:
            stem = _memcard_serial_dirname(serial)

    remote_path = f"{MISTER_SAVES_DIR}/{folder}/{stem}.sav"
    return _mister_ssh_save_path(profile, remote_path, assume_exists=False)


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
_PSX_RETAIL_PREFIXES: frozenset[str] = _mister_saves.PSX_RETAIL_PREFIXES

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


def _memcard_serial_dirname(title_id: str) -> str:
    """Convert compact PS1/PS2 title IDs like ``SLUS00594`` to ``SLUS-00594``."""
    compact = re.sub(r"[^A-Z0-9]", "", (title_id or "").upper())
    if _PS1_SERIAL_RE.match(compact):
        return f"{compact[:4]}-{compact[4:]}"
    return compact or "UNKNOWN"


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
        cached = {"no_intro": {}, "name_index": {}, "alias_index": {}}
        _NOINTRO_CACHE[system] = cached
        return cached

    dat_path = rn.find_dat_for_system(system)
    if dat_path is None:
        cached = {
            "no_intro": {},
            "name_index": {},
            "alias_index": {},
            "cache_tag": f"{system}:none",
        }
    else:
        no_intro = rn.load_no_intro_dat(dat_path)
        alias_index: dict[str, str] = {}
        try:
            dat_stat = dat_path.stat()
            dat_sig = f"{dat_path.name}:{dat_stat.st_mtime_ns}:{dat_stat.st_size}"
        except OSError:
            dat_sig = dat_path.name
        aliases_path = rn.DATS_DIR / "EN-Dats" / "aliases.json"
        alias_sig = "aliases:none"
        if aliases_path.is_file():
            try:
                aliases_payload = json.loads(aliases_path.read_text(encoding="utf-8"))
                system_aliases = aliases_payload.get(system, {})
                if isinstance(system_aliases, dict):
                    valid_canonicals = set(no_intro.values())
                    for alias_name, canonical_name in system_aliases.items():
                        alias = str(alias_name or "").strip()
                        canonical = str(canonical_name or "").strip()
                        if not alias or not canonical or canonical not in valid_canonicals:
                            continue
                        alias_index[_normalize_alias_lookup_name(alias)] = canonical
                alias_stat = aliases_path.stat()
                alias_sig = (
                    f"{aliases_path.name}:{alias_stat.st_mtime_ns}:{alias_stat.st_size}"
                )
            except (OSError, ValueError, TypeError):
                alias_index = {}
        cached = {
            "no_intro": no_intro,
            "name_index": rn.build_name_index(no_intro) if no_intro else {},
            "alias_index": alias_index,
            "cache_tag": f"{system}:{dat_sig}:{alias_sig}",
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
    alias_index = cache.get("alias_index", {})
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

    alias_canonical = alias_index.get(_normalize_alias_lookup_name(lookup_name))
    if alias_canonical:
        source, confidence = "alias", "high"
        _set_cached_canonical_name(
            profile_scope,
            system,
            path,
            match_name,
            cache_tag,
            alias_canonical,
            source,
            confidence,
        )
        return alias_canonical, source, confidence

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
            alias_canonical = alias_index.get(
                _normalize_alias_lookup_name(member_path.name)
            )
            if alias_canonical:
                source, confidence = "alias", "high"
                _set_cached_canonical_name(
                    profile_scope,
                    system,
                    path,
                    match_name,
                    cache_tag,
                    alias_canonical,
                    source,
                    confidence,
                )
                return alias_canonical, source, confidence
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
        alias_canonical = alias_index.get(_normalize_alias_lookup_name(path.parent.name))
        if alias_canonical:
            source, confidence = "alias", "high"
            _set_cached_canonical_name(
                profile_scope,
                system,
                path,
                match_name,
                cache_tag,
                alias_canonical,
                source,
                confidence,
            )
            return alias_canonical, source, confidence
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
    """Build the server title ID, preferring a canonical No-Intro name when found.

    Dreamcast is keyed by disc serial rather than by name slug (the card devices
    file saves that way), so its ids go through the DAT; a disc the DAT doesn't
    know still falls back to the name slug.
    """
    name = canonical_name or source_name
    if (system or "").upper() == "DC":
        from dreamcast import title_id_for_name

        title_id = title_id_for_name(name)
        if title_id:
            return title_id
    return _make_title_id_with_region(system, name)


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


def _hash_memcard_file_cached(path: Path, system: str, title_id: str) -> str:
    try:
        stat = path.stat()
    except OSError:
        return _hash_file(path)
    cache_key = _memcard_hash_cache_key(system, title_id, path.name)
    cached = _get_cached_hash_for_key(cache_key, stat.st_size, stat.st_mtime)
    if cached:
        return cached
    save_hash = _hash_file(path)
    _set_cached_hash_for_key(cache_key, stat.st_size, stat.st_mtime, save_hash)
    return save_hash


def _hash_memcard_gc_file_cached(path: Path, gc_code: str) -> str:
    title_id = f"GC_{gc_code.upper()}"
    try:
        stat = path.stat()
    except OSError:
        stat = None
    cache_key = _memcard_hash_cache_key("GC", title_id, path.name)
    if stat is not None:
        cached = _get_cached_hash_for_key(cache_key, stat.st_size, stat.st_mtime)
        if cached:
            return cached
    try:
        card_bytes = path.read_bytes()
        gci_bytes = gc_extract_gci(card_bytes, gc_code)
    except OSError:
        gci_bytes = None
    if gci_bytes is not None:
        save_hash = hashlib.sha256(gci_bytes).hexdigest()
    else:
        save_hash = _hash_file(path)
    if stat is not None:
        _set_cached_hash_for_key(cache_key, stat.st_size, stat.st_mtime, save_hash)
    return save_hash


def _select_ftp_slot_file(
    entries: list[FtpEntry],
    preferred_stem: str,
    suffixes: set[str],
) -> FtpEntry | None:
    fallback: FtpEntry | None = None
    for entry in entries:
        if entry.is_dir:
            continue
        suffix = posixpath.splitext(entry.name)[1].lower()
        if suffix not in suffixes:
            continue
        stem = posixpath.splitext(entry.name)[0]
        if stem == preferred_stem:
            return entry
        if stem.endswith("-1") and fallback is None:
            fallback = entry
    return fallback


def _ftp_file_with_metadata(ftp: ftplib.FTP, entry: FtpEntry) -> FtpEntry:
    size = entry.size if entry.size > 0 else _ftp_size(ftp, entry.path)
    mtime = entry.mtime if entry.mtime > 0 else _ftp_mtime(ftp, entry.path)
    return FtpEntry(
        name=entry.name,
        path=entry.path,
        is_dir=entry.is_dir,
        size=size,
        mtime=mtime,
    )


def _hash_ftp_card_bytes_cached(
    ftp: ftplib.FTP,
    entry: FtpEntry,
    profile_scope: str,
    system: str,
    title_id: str,
) -> str:
    entry = _ftp_file_with_metadata(ftp, entry)
    cache_keys = [
        _remote_hash_cache_key(profile_scope, entry.path),
        _memcard_hash_cache_key(system, title_id, entry.name),
    ]
    for cache_key in cache_keys:
        cached = _get_cached_hash_for_key(cache_key, entry.size, entry.mtime)
        if cached:
            for hydrate_key in cache_keys:
                if hydrate_key != cache_key:
                    _set_cached_hash_for_key(
                        hydrate_key, entry.size, entry.mtime, cached
                    )
            return cached
    data = _ftp_download_bytes(
        ftp,
        entry.path,
        expected_size=entry.size if entry.size > 0 else None,
    )
    save_hash = hashlib.sha256(data).hexdigest()
    for cache_key in cache_keys:
        _set_cached_hash_for_key(
            cache_key, entry.size or len(data), entry.mtime, save_hash
        )
    return save_hash


def _hash_ftp_gc_card_cached(
    ftp: ftplib.FTP,
    entry: FtpEntry,
    gc_code: str,
    profile_scope: str,
) -> str:
    entry = _ftp_file_with_metadata(ftp, entry)
    title_id = f"GC_{gc_code.upper()}"
    cache_keys = [
        _remote_hash_cache_key(profile_scope, entry.path),
        _memcard_hash_cache_key("GC", title_id, entry.name),
    ]
    for cache_key in cache_keys:
        cached = _get_cached_hash_for_key(cache_key, entry.size, entry.mtime)
        if cached:
            for hydrate_key in cache_keys:
                if hydrate_key != cache_key:
                    _set_cached_hash_for_key(
                        hydrate_key, entry.size, entry.mtime, cached
                    )
            return cached
    card_bytes = _ftp_download_bytes(
        ftp,
        entry.path,
        expected_size=entry.size if entry.size > 0 else None,
    )
    gci_bytes = gc_extract_gci(card_bytes, gc_code)
    save_hash = hashlib.sha256(gci_bytes or card_bytes).hexdigest()
    for cache_key in cache_keys:
        _set_cached_hash_for_key(
            cache_key, entry.size or len(card_bytes), entry.mtime, save_hash
        )
    return save_hash


def _scan_memcard_pro_ftp(
    profile: dict,
    system: str = "PS1",
    progress_callback=None,
    profile_scope: str = "",
) -> list[SaveFile]:
    """Scan PS1/PS2/GC MemCard Pro card images through the device FTP server."""
    system = (system or "").upper()
    if system not in {"PS1", "PS2", "GC"}:
        return []

    host = str(profile.get("ftp_host", "")).strip()
    port = int(profile.get("ftp_port", 21) or 21)
    username = str(profile.get("ftp_username", ""))
    password = str(profile.get("ftp_password", ""))
    passive = bool(profile.get("ftp_passive", True))
    timeout = int(profile.get("ftp_timeout", 30) or 30)
    root = _ftp_profile_root(profile)
    results: list[SaveFile] = []

    _emit_progress(progress_callback, f"Connecting to MemCard Pro FTP {host}…", 0, 0)
    with _ftp_connection(host, port, username, password, passive, timeout) as ftp:
        _emit_progress(progress_callback, f"Connected to MemCard Pro FTP {host}.", 0, 0)
        base_dir = _ftp_existing_or_default_dir(
            ftp, _memcard_ftp_roots(root, system)
        )
        _emit_progress(
            progress_callback,
            f"Listing {system} MemCard Pro FTP folder {base_dir}…",
            0,
            0,
        )

        if system == "GC":
            disc_dirs = _ftp_entries_from_names(
                base_dir,
                _ftp_nlst(ftp, base_dir),
                is_dir=True,
            )
            total = len(disc_dirs)
            for idx, disc_dir in enumerate(disc_dirs, start=1):
                if idx == 1 or idx % 5 == 0 or idx == total:
                    _emit_progress(
                        progress_callback,
                        f"Checking GC MemCard Pro FTP folders. {idx}/{total}",
                        idx,
                        total,
                    )
                gc_code = _gc_code_from_folder(disc_dir.name)
                if not gc_code:
                    continue
                files = _ftp_entries_from_names(
                    disc_dir.path,
                    _ftp_nlst(ftp, disc_dir.path),
                    is_dir=False,
                )
                slot1 = _select_ftp_slot_file(
                    files,
                    f"{disc_dir.name}-1",
                    {".raw"},
                )
                if slot1 is None:
                    continue
                remote_path = _ftp_profile_save_path(profile, slot1.path)
                try:
                    _emit_progress(
                        progress_callback,
                        f"Hashing GC MemCard Pro FTP card {idx}/{total}: {disc_dir.name}",
                        idx,
                        total,
                    )
                    save_hash = _hash_ftp_gc_card_cached(
                        ftp, slot1, gc_code, profile_scope
                    )
                except SyncUserError:
                    raise
                except Exception:
                    save_hash = ""
                results.append(
                    SaveFile(
                        title_id=f"GC_{gc_code.upper()}",
                        path=remote_path,
                        hash=save_hash,
                        mtime=slot1.mtime or time.time(),
                        system="GC",
                        game_name=disc_dir.name,
                        profile_scope=profile_scope,
                    )
                )
                if idx == 1 or idx % 10 == 0 or idx == total:
                    _emit_progress(
                        progress_callback,
                        f"Scanning GC MemCard Pro FTP folders. {idx}/{total}",
                        idx,
                        total,
                    )
            return results

        card_dirs = _ftp_entries_from_names(
            base_dir,
            _ftp_nlst(ftp, base_dir),
            is_dir=True,
        )
        total = len(card_dirs)
        suffixes = {".mcd", ".mcr"} if system == "PS1" else {".mc2", ".ps2"}
        for idx, serial_dir in enumerate(card_dirs, start=1):
            if idx == 1 or idx % 5 == 0 or idx == total:
                _emit_progress(
                    progress_callback,
                    f"Checking {system} MemCard Pro FTP folders. {idx}/{total}",
                    idx,
                    total,
                )
            serial = _normalize_ps1_serial(serial_dir.name)
            if not serial:
                continue
            files = _ftp_entries_from_names(
                serial_dir.path,
                _ftp_nlst(ftp, serial_dir.path),
                is_dir=False,
            )
            slot1 = _select_ftp_slot_file(
                files,
                f"{serial_dir.name}-1",
                suffixes,
            )
            if slot1 is None:
                continue

            game_name = serial_dir.name
            if system == "PS2":
                name_entry = next(
                    (e for e in files if not e.is_dir and e.name.lower() == "name.txt"),
                    None,
                )
                if name_entry is not None:
                    try:
                        name_entry = _ftp_file_with_metadata(ftp, name_entry)
                        text = _ftp_download_bytes(
                            ftp,
                            name_entry.path,
                            expected_size=(
                                name_entry.size if name_entry.size > 0 else None
                            ),
                        ).decode(
                            "utf-8",
                            errors="ignore",
                        )
                        game_name = text.strip() or game_name
                    except Exception:
                        pass

            remote_path = _ftp_profile_save_path(profile, slot1.path)
            try:
                _emit_progress(
                    progress_callback,
                    f"Hashing {system} MemCard Pro FTP card {idx}/{total}: {serial_dir.name}",
                    idx,
                    total,
                )
                save_hash = _hash_ftp_card_bytes_cached(
                    ftp, slot1, profile_scope, system, serial
                )
            except SyncUserError:
                raise
            except Exception:
                save_hash = ""
            results.append(
                SaveFile(
                    title_id=serial,
                    path=remote_path,
                    hash=save_hash,
                    mtime=slot1.mtime or time.time(),
                    system=system,
                    game_name=game_name,
                    profile_scope=profile_scope,
                )
            )
            if idx == 1 or idx % 10 == 0 or idx == total:
                _emit_progress(
                    progress_callback,
                    f"Scanning {system} MemCard Pro FTP folders. {idx}/{total}",
                    idx,
                    total,
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

            title_id = f"GC_{gc_code.upper()}"
            # Hash only the extracted GCI bytes so the hash matches what
            # we actually upload to the server (and what Dolphin stores).
            save_hash = _hash_memcard_gc_file_cached(slot1, gc_code)
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
                    hash=_hash_memcard_file_cached(slot1, "PS2", serial),
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
                    hash=_hash_memcard_file_cached(slot1, "PS1", serial),
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
                    hash=_hash_memcard_file_cached(mcd_file, "PS1", title_id),
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
            hash=_hash_memcard_file_cached(mcd_file, "PS1", title_id),
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


# ---------------------------------------------------------------------------
# Dreamcast virtual VMUs — MemCard PRO DC and openMenu Serial VMU
#
# Both devices store one 128 KB VMU image per game, in a folder named after the
# disc's Game ID (the IP.BIN "Product number" with dashes and spaces stripped):
#
#   MemCard PRO DC   <root>/Dreamcast/MK5106450/MK5106450-1.vmu
#   openMenu         <serial SD>/OPENMENU/SAVES/MK5106450/SLOT1.VMU
#                                                         TITLE.TXT
#
# Only slot / channel 1 syncs, matching the PS1/PS2/GC MemCard Pro profiles.
# The payload is a bare VMU image — the same bytes Flycast writes — so it
# uploads through /raw and interchanges with an emulator profile as long as the
# title id agrees, which is what ``dreamcast.resolve_save_identity`` arranges.
#
# Layout references: https://www.8bitmods.wiki/importing-saves and
# https://github.com/DerekPascarella/openMenu-Virtual-Folder-Bundle
# ---------------------------------------------------------------------------

MEMCARD_PRO_DC_DIR = "Dreamcast"
OPENMENU_SAVES_DIRS = ("OPENMENU", "SAVES")


def _child_dir_ci(root: Path, *names: str) -> Optional[Path]:
    """Walk ``root`` down ``names``, matching each component case-insensitively."""
    current = root
    for name in names:
        found = _child_dir(current, name)
        if not found.is_dir():
            return None
        current = found
    return current


def _vmu_slot_file(folder: Path, preferred_stems: tuple[str, ...]) -> Optional[Path]:
    """Slot-1 ``.vmu`` inside a per-game folder.

    ``preferred_stems`` are tried in order (case-insensitively).  A folder that
    holds only higher slots is skipped rather than syncing an arbitrary one —
    slot 1 is the slot every device fills first.
    """
    try:
        vmus = [
            f
            for f in sorted(folder.iterdir())
            if f.is_file() and f.suffix.lower() == ".vmu"
        ]
    except OSError:
        return None
    for stem in preferred_stems:
        for vmu in vmus:
            if vmu.stem.lower() == stem.lower():
                return vmu
    return None


def _scan_memcard_pro_dc(
    root: Path,
    progress_callback=None,
    profile_scope: str = "",
) -> list[SaveFile]:
    """Scan a MemCard PRO DC microSD for per-game virtual VMUs.

    ``root`` may be the card root (which holds ``Dreamcast/``) or the
    ``Dreamcast`` folder itself.
    """
    from dreamcast import is_game_folder, normalize_game_id, resolve_save_identity

    # Require the Dreamcast folder (or a root that already is it).  Falling back
    # to the whole root would walk every directory on whatever volume the
    # profile's drive letter currently points at — card readers reassign those
    # on every reconnect.
    base = _child_dir_ci(root, MEMCARD_PRO_DC_DIR)
    if base is None:
        base = root if root.name.lower() == MEMCARD_PRO_DC_DIR.lower() else None
    if base is None or not base.is_dir():
        return []

    results: list[SaveFile] = []
    game_dirs = [d for d in sorted(base.iterdir()) if d.is_dir()]
    total = len(game_dirs)
    for idx, game_dir in enumerate(game_dirs, start=1):
        # A real card also holds MemoryCard1 (the shared card for discs with no
        # GameID) and openmenu (the menu's own VMU) — neither is a game.
        if not is_game_folder(game_dir.name):
            continue
        game_id = normalize_game_id(game_dir.name)
        slot1 = _vmu_slot_file(game_dir, (f"{game_dir.name}-1", f"{game_id}-1"))
        if slot1 is None:
            continue
        title_id, game_name = resolve_save_identity(game_id)
        if not title_id:
            continue
        results.append(
            SaveFile(
                title_id=title_id,
                path=slot1,
                hash=_hash_memcard_file_cached(slot1, "DC", title_id),
                mtime=slot1.stat().st_mtime,
                system="DC",
                game_name=game_name,
                profile_scope=profile_scope,
            )
        )
        if idx == 1 or idx % 25 == 0 or idx == total:
            _emit_progress(
                progress_callback,
                f"Scanning MemCard PRO DC folders. {idx}/{total}",
                idx,
                total,
            )
    return results


def _scan_openmenu_vmu(
    root: Path,
    progress_callback=None,
    profile_scope: str = "",
) -> list[SaveFile]:
    """Scan an openMenu Serial VMU SD card for per-game virtual VMUs.

    ``root`` may be the serial SD root (holding ``OPENMENU/SAVES/``), the
    ``OPENMENU`` folder, or the ``SAVES`` folder itself.
    """
    from dreamcast import is_game_folder, normalize_game_id, resolve_save_identity

    # As for the card above: only scan a folder that really is the Serial VMU
    # store, never a bare drive root.
    base = _child_dir_ci(root, *OPENMENU_SAVES_DIRS) or _child_dir_ci(
        root, OPENMENU_SAVES_DIRS[1]
    )
    if base is None and root.name.lower() in {d.lower() for d in OPENMENU_SAVES_DIRS}:
        base = root
    if base is None or not base.is_dir():
        return []

    results: list[SaveFile] = []
    game_dirs = [d for d in sorted(base.iterdir()) if d.is_dir()]
    total = len(game_dirs)
    for idx, game_dir in enumerate(game_dirs, start=1):
        if not is_game_folder(game_dir.name):
            continue
        game_id = normalize_game_id(game_dir.name)
        slot1 = _vmu_slot_file(game_dir, ("SLOT1", f"{game_dir.name}-1"))
        if slot1 is None:
            continue
        title_id, game_name = resolve_save_identity(
            game_id, _openmenu_title_label(game_dir)
        )
        if not title_id:
            continue
        results.append(
            SaveFile(
                title_id=title_id,
                path=slot1,
                hash=_hash_memcard_file_cached(slot1, "DC", title_id),
                mtime=slot1.stat().st_mtime,
                system="DC",
                game_name=game_name,
                profile_scope=profile_scope,
            )
        )
        if idx == 1 or idx % 25 == 0 or idx == total:
            _emit_progress(
                progress_callback,
                f"Scanning openMenu Serial VMU folders. {idx}/{total}",
                idx,
                total,
            )
    return results


def _openmenu_title_label(game_dir: Path) -> str:
    """openMenu's own display name for a game folder (``TITLE.TXT``)."""
    for entry in (game_dir / "TITLE.TXT", game_dir / "title.txt"):
        if entry.is_file():
            try:
                return entry.read_text(encoding="utf-8", errors="ignore").strip()
            except OSError:
                return ""
    return ""


def build_dreamcast_vmu_path(profile: dict, title_id: str) -> Optional[Path]:
    """Local destination for a server-only Dreamcast save, or ``None``.

    Needs a Game ID: these folders are named after the disc, not the game, so a
    title the Dreamcast DAT can't resolve has nowhere to go and the caller falls
    back to a manual download.  An existing folder for any of the title's Game
    IDs wins over creating a new one, so a downloaded save lands in the folder
    the console already writes to instead of beside it.
    """
    from dreamcast import game_ids_for_title_id

    device_type = str(profile.get("device_type", "")).strip()
    game_ids = game_ids_for_title_id(title_id)
    if not game_ids:
        return None

    if device_type == "openMenu":
        root_str = profile.get("save_folder") or profile.get("path", "")
        if not root_str:
            return None
        root = Path(root_str)
        base = (
            _child_dir_ci(root, *OPENMENU_SAVES_DIRS)
            or _child_dir_ci(root, OPENMENU_SAVES_DIRS[1])
            or root.joinpath(*OPENMENU_SAVES_DIRS)
        )
        for game_id in game_ids:
            existing = _child_dir(base, game_id)
            if existing.is_dir():
                return existing / "SLOT1.VMU"
        return base / game_ids[0] / "SLOT1.VMU"

    root_str = profile.get("path") or profile.get("save_folder", "")
    if not root_str:
        return None
    root = Path(root_str)
    base = _child_dir_ci(root, MEMCARD_PRO_DC_DIR) or root / MEMCARD_PRO_DC_DIR
    for game_id in game_ids:
        existing = _child_dir(base, game_id)
        if existing.is_dir():
            return existing / f"{existing.name}-1.vmu"
    return base / game_ids[0] / f"{game_ids[0]}-1.vmu"


def finalize_openmenu_download(path: Path, game_name: str) -> None:
    """Write openMenu's ``TITLE.TXT`` next to a freshly downloaded Serial VMU."""
    label = str(game_name or "").strip()
    if not label:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        (path.parent / "TITLE.TXT").write_text(label + "\n", encoding="utf-8")
    except OSError:
        pass


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
        if (
            save.system == "SAT"
            and isinstance(save.path, Path)
            and save.path.exists()
            and save.save_exists
        ):
            try:
                canonical_saturn, archive_names = _canonical_saturn_payload(
                    save.title_id,
                    save.path,
                    base_url=base_url,
                    headers=headers,
                    timeout=timeout,
                )
                if canonical_saturn:
                    save.hash = hashlib.sha256(canonical_saturn).hexdigest()
                if archive_names:
                    _set_saturn_archive_names(save.title_id, archive_names)
            except Exception:
                pass
        saturn_hash_match = _resolve_saturn_server_hash_match(save, server_titles)
        if saturn_hash_match is not None:
            matched_id, matched_note = saturn_hash_match
            _debug_scan(
                "Compare SAT remap by hash: "
                f"from={save.title_id} to={matched_id} path={save.path}"
            )
            save.title_id = matched_id
            mapping_note = matched_note
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
                    if save.system == "SAT":
                        _debug_scan(
                            "Compare SAT local-only: "
                            f"title_id={save.title_id} path={save.path} "
                            f"save_exists={save.save_exists} local_hash={save.hash[:12] if save.hash else ''}"
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
                if save.system == "SAT":
                    _debug_scan(
                        "Compare SAT error/no server index: "
                        f"title_id={save.title_id} path={save.path}"
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
        if save.system == "SAT":
            _debug_scan(
                "Compare SAT status: "
                f"title_id={save.title_id} path={save.path} save_exists={save.save_exists} "
                f"local_hash={save.hash[:12] if save.hash else ''} "
                f"server_hash={server_hash[:12] if server_hash else ''} status={status}"
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
        if system.upper() == "SAT":
            _debug_scan(
                "Compare SAT server-only: "
                f"title_id={tid} name={name} server_hash={server_hash[:12] if server_hash else ''}"
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
        # Saturn container/archive handling needs a local filesystem path;
        # remote (SSH/FTP) Saturn saves upload as raw bytes.
        is_local_sat = (system or "").upper() == "SAT" and isinstance(path, Path)
        saroo_payload = (
            _resolve_saroo_native_payload(title_id, path) if is_local_sat else None
        )
        if saroo_payload is not None:
            data = saroo_payload[0]
        elif is_local_sat:
            data, archive_names = _canonical_saturn_payload(
                title_id,
                path,
                base_url=base_url,
                headers=headers,
                timeout=timeout,
            )
            if not data:
                raise ValueError(
                    f"Could not resolve Saturn save archives for {title_id} from {path}"
                )
            if archive_names:
                _set_saturn_archive_names(title_id, archive_names)
        elif (system or "").upper() == "SAT" and isinstance(path, SshSavePath):
            # MiSTer keeps the byte-expanded 64 KB image — store the canonical
            # 32 KB internal BRAM so every Saturn client shares one payload.
            from saroo_format import normalize_saturn_save

            data = normalize_saturn_save(path.read_bytes())
        elif (system or "").upper() == "MD" and isinstance(path, SshSavePath):
            # The core stores packed SRAM padded to 64 KB; store the expanded
            # layout emulators use, keeping the size a counterpart already has.
            data = _md_from_mister(
                path.read_bytes(),
                target_size=_server_save_size(title_id, base_url, headers, timeout),
            )
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
    if isinstance(dest_path, (FtpSavePath, SshSavePath)):
        content = resp.content
        if isinstance(dest_path, SshSavePath):
            if (system or "").upper() == "SAT":
                # Server stores canonical 32 KB internal BRAM; the MiSTer core
                # wants it byte-expanded to 64 KB.
                from saroo_format import convert_saturn_save_format

                content = convert_saturn_save_format(
                    content, _saturn_format_for_path(dest_path)
                )
            elif (system or "").upper() == "MD":
                content = _md_to_mister(content)
        dest_path.write_bytes(content)
        headers_obj = getattr(resp, "headers", {}) or {}
        server_hash = headers_obj.get(
            "X-Save-Hash", hashlib.sha256(resp.content).hexdigest()
        )
        _update_state(title_id, server_hash)
        return server_hash
    if (system or "").upper() == "PS3":
        _extract_bundle_to_dir(resp.content, dest_path)
        server_hash = resp.headers.get("X-Save-Hash", _hash_ps3_dir_files(dest_path))
    else:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        if (system or "").upper() == "SAT":
            from saroo_format import (
                convert_saturn_save_format,
                list_saturn_archive_names,
                merge_saturn_save_set,
            )

            archive_names = [name.upper() for name in list_saturn_archive_names(resp.content)]
            if archive_names:
                _set_saturn_archive_names(title_id, archive_names)

            saturn_format = _saturn_format_for_path(dest_path)
            if saturn_format == "yabasanshiro":
                existing_data = dest_path.read_bytes() if dest_path.exists() else None
                dest_path.write_bytes(
                    merge_saturn_save_set(
                        existing_data,
                        resp.content,
                        "yabasanshiro",
                    )
                )
            else:
                dest_path.write_bytes(
                    convert_saturn_save_format(resp.content, saturn_format)
                )
        else:
            dest_path.write_bytes(resp.content)
        _invalidate_memcard_hash_path(dest_path, system, title_id)
        server_hash = resp.headers.get(
            "X-Save-Hash", hashlib.sha256(resp.content).hexdigest()
        )
    _update_state(title_id, server_hash)
    return server_hash
