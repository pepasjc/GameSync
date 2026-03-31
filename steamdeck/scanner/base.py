"""Base class and utilities shared by all emulator scanners."""

import hashlib
import re
import struct
from pathlib import Path
from typing import Optional

# Region/revision tags to strip when normalizing ROM names
_REGION_RE = re.compile(
    r"\s*\((?:USA|Europe|Japan|World|En|Fr|De|Es|It|Pt|Nl|Sv|Da|No|Pl|"
    r"En,\w+(?:,\w+)*|JPN|EUR|USA|PAL|NTSC|Rev \w+|v\d+[\.\d]*)\)",
    re.IGNORECASE,
)
_REV_RE = re.compile(r"\s*\(Rev \w+\)|\s*\(v\d[\d.]*\)", re.IGNORECASE)
_DISC_RE = re.compile(r"\s*[\(\[](Disc|Disk|CD)\s*\d+[\)\]]", re.IGNORECASE)
_EXTRA_RE = re.compile(r"\s*\([^)]*(?:Beta|Demo|Proto|Sample|Pirate|Unl)[^)]*\)", re.IGNORECASE)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_MULTI_UNDERSCORE_RE = re.compile(r"_+")


def normalize_rom_name(filename: str) -> str:
    """Strip extension, region/revision tags → lowercase slug."""
    name = filename
    # Strip up to 3 extensions (handles .zip.gba etc.)
    for _ in range(3):
        dot = name.rfind(".")
        if dot <= 0:
            break
        suffix = name[dot + 1:]
        if 1 <= len(suffix) <= 5 and suffix.isalnum():
            name = name[:dot]
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


def sha256_file(path: Path) -> str:
    """SHA-256 of a single file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_dir(path: Path) -> str:
    """SHA-256 of all files in a directory (sorted by name)."""
    h = hashlib.sha256()
    for fp in sorted(path.rglob("*")):
        if fp.is_file():
            h.update(fp.name.encode())
            with open(fp, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
    return h.hexdigest()


def read_nds_gamecode(rom_path: Path) -> Optional[str]:
    """Read 4-char game code from NDS ROM header at offset 0x0C."""
    try:
        with open(rom_path, "rb") as f:
            f.seek(0x0C)
            code = f.read(4)
        if len(code) == 4 and all(32 <= b < 127 for b in code):
            return code.decode("ascii")
    except Exception:
        pass
    return None


def read_ps1_serial(iso_path: Path) -> Optional[str]:
    """
    Try to read PS1 product serial from ISO SYSTEM.CNF.
    Handles plain ISO (2048-byte sectors) and raw (2352-byte sectors).
    """
    SECTOR_SIZES = [2048, 2352]
    SYSTEM_CNF_LBA = 23  # Typical location for SYSTEM.CNF

    for sector_size in SECTOR_SIZES:
        try:
            with open(iso_path, "rb") as f:
                header_offset = 16 if sector_size == 2048 else 16
                offset = SYSTEM_CNF_LBA * sector_size
                if sector_size == 2352:
                    offset += 24  # Skip sync/header bytes in raw sectors
                f.seek(offset)
                data = f.read(512).decode("latin-1", errors="ignore")
                m = re.search(r"BOOT\s*=\s*cdrom:\\?([A-Z]{4}[\-_]\d{5})", data)
                if m:
                    serial = re.sub(r"[-_]", "", m.group(1))
                    return serial
        except Exception:
            pass
    return None


def find_paths(*candidates: Path) -> Optional[Path]:
    """Return the first existing path from candidates."""
    for p in candidates:
        if p.exists():
            return p
    return None
