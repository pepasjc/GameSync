"""Base utilities shared by all emulator scanners."""

import hashlib
import re
import struct
from pathlib import Path
from typing import Optional


# ──────────────────────────────────────────────────────────────────────────────
# Region / normalization
# ──────────────────────────────────────────────────────────────────────────────

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

_TAG_RE = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")
_PAREN_CONTENT_RE = re.compile(r"\(([^)]+)\)")
_DISC_RE = re.compile(
    r"\s*[\(\[]\s*(?:disc|disk|cd)\s*\d+(?:\s*of\s*\d+)?\s*[\)\]]",
    re.IGNORECASE,
)

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_MULTI_UNDERSCORE_RE = re.compile(r"_+")

# PS1 product code pattern: 4 letters + 5+ digits (e.g. SLUS01234)
PS1_SERIAL_RE = re.compile(r"^[A-Z]{4}\d{5,}$")

# PS1 retail disc prefixes (match Android's psxRetailPrefixes)
PSX_RETAIL_PREFIXES = {
    "SLUS",
    "SCUS",
    "PAPX",  # NA
    "SLES",
    "SCES",
    "SCED",  # EU
    "SLPS",
    "SLPM",
    "SCPS",
    "SCPM",  # JP
    "SLAJ",
    "SLEJ",
    "SCAJ",  # Other
}

# Shared / global memory card names to skip (DuckStation / ePSXe)
SHARED_CARD_NAMES = {
    "shared_card_1",
    "shared_card_2",
    "shared_card_3",
    "shared_card_4",
    "mcd001",
    "mcd002",
    "epsxe000",
    "epsxe001",
}

# PS2 shared memory card name pattern
PS2_SHARED_CARD_RE = re.compile(r"(?i)^mcd\d{3}$")


def _extract_regions(name: str) -> list[str]:
    """Extract geographic region tokens from parenthetical tags."""
    regions: list[str] = []
    seen: set[str] = set()
    for m in _PAREN_CONTENT_RE.finditer(name):
        for part in m.group(1).split(","):
            token = part.strip().lower()
            if token in _REGION_NAMES and token not in seen:
                seen.add(token)
                regions.append(token)
    return regions


def normalize_rom_name(filename: str) -> str:
    """Strip extension, region/revision tags -> lowercase slug."""
    name = filename
    for _ in range(3):
        dot = name.rfind(".")
        if dot <= 0:
            break
        suffix = name[dot + 1 :]
        if 1 <= len(suffix) <= 5 and suffix.isalnum():
            name = name[:dot]
        else:
            break

    name = _TAG_RE.sub("", name).strip()
    name = name.lower()
    name = _NON_ALNUM_RE.sub("_", name)
    name = _MULTI_UNDERSCORE_RE.sub("_", name).strip("_")
    return name or "unknown"


def to_title_id(rom_name: str, system: str) -> str:
    """
    Convert a ROM name to a title ID slug, preserving region names.
    Matches Android's EmulatorBase.toTitleId() logic.

    "Shining Force CD (USA) (3R)" -> "SEGACD_shining_force_cd_usa"
    "Sonic (USA, Europe)"         -> "MD_sonic_usa_europe"
    """
    regions = _extract_regions(rom_name)
    stripped = _TAG_RE.sub("", rom_name).strip()
    slug = stripped.lower()
    slug = _NON_ALNUM_RE.sub("_", slug)
    slug = _MULTI_UNDERSCORE_RE.sub("_", slug).strip("_")
    base = f"{system}_{slug}"
    if regions:
        return f"{base}_{'_'.join(regions)}"
    return base


def to_ps1_title_id(name: str) -> str:
    """
    Build a PS1 title ID slug, stripping disc tags but preserving region so
    multi-disc games share one ID and region variants stay separate.

    "Parasite Eve (USA) (Disc 1)" -> "PS1_parasite_eve_usa"
    "Final Fantasy VII (Japan) (Disc 2)" -> "PS1_final_fantasy_vii_japan"
    """
    regions = _extract_regions(name)
    # Strip disc tags specifically, then all remaining parenthetical tags
    stripped = _DISC_RE.sub("", name)
    stripped = _TAG_RE.sub("", stripped).strip()
    slug = stripped.lower()
    slug = _NON_ALNUM_RE.sub("_", slug)
    slug = _MULTI_UNDERSCORE_RE.sub("_", slug).strip("_")
    base = f"PS1_{slug}"
    if regions:
        return f"{base}_{'_'.join(regions)}"
    return base


def normalize_serial(stem: str) -> Optional[str]:
    """
    Normalize a potential PS1/PS2 serial from a filename stem.
    "SLUS-01234" -> "SLUS01234", "SCUS_94163" -> "SCUS94163"
    Returns None if it doesn't match the serial pattern.
    """
    code = re.sub(r"[-_]", "", stem.upper())
    # Allow only letters then digits
    code = re.sub(r"[^A-Z0-9]", "", code)
    if PS1_SERIAL_RE.match(code):
        return code
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Hashing
# ──────────────────────────────────────────────────────────────────────────────


def sha256_file(path: Path) -> str:
    """SHA-256 of a single file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_dir(path: Path) -> str:
    """
    SHA-256 of all files in a directory, sorted by filename.
    Hash includes filename + file content (matches server bundle hash).
    """
    h = hashlib.sha256()
    for fp in sorted(path.rglob("*")):
        if fp.is_file():
            h.update(fp.name.encode())
            with open(fp, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
    return h.hexdigest()


def sha256_dir_tree_files(path: Path) -> str:
    """
    SHA-256 of all files in a directory tree, sorted by relative path.
    Hash includes only file contents so it matches the server's multi-file
    bundle hash for PS3 save folders.
    """
    h = hashlib.sha256()
    for fp in sorted(path.rglob("*")):
        if not fp.is_file():
            continue
        with open(fp, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    return h.hexdigest()


def sha256_dir_files(path: Path) -> str:
    """
    SHA-256 of all direct-child file *contents* sorted by filename.
    No path info in hash. Matches server PSP bundle hash and Android's
    HashUtils.sha256DirFiles().
    """
    h = hashlib.sha256()
    files = sorted(
        (f for f in path.iterdir() if f.is_file()),
        key=lambda f: f.name,
    )
    for fp in files:
        with open(fp, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    return h.hexdigest()


def sha256_files(paths: list[Path]) -> str:
    """SHA-256 of multiple files, concatenated in the given order."""
    h = hashlib.sha256()
    for p in paths:
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    return h.hexdigest()


# ──────────────────────────────────────────────────────────────────────────────
# ISO 9660 disc serial extraction (ported from Android EmulatorBase)
# ──────────────────────────────────────────────────────────────────────────────


def _resolve_disc_image(file_path: Path) -> Optional[Path]:
    """If file is a .cue, resolve to the referenced .bin file."""
    if file_path.suffix.lower() != ".cue":
        return file_path
    try:
        for line in file_path.read_text(errors="ignore").splitlines():
            if line.strip().upper().startswith("FILE"):
                m = re.search(r'FILE\s+"(.+?)"', line, re.IGNORECASE)
                if m:
                    referenced = file_path.parent / m.group(1)
                    if referenced.exists():
                        return referenced
    except Exception:
        pass
    return None


def _build_disc_offsets(file_path: Path) -> list[tuple[int, int]]:
    """
    Return (sector_size, data_offset) pairs to try for ISO parsing.
    Raw BIN images use 2352-byte sectors; data starts at offset 24 (MODE2)
    or 16 (MODE1). Standard ISO uses 2048-byte sectors, data at offset 0.
    """
    ext = file_path.suffix.lower()
    if ext in (".bin", ".img", ".mdf"):
        return [(2352, 24), (2352, 16), (2048, 0)]
    else:
        return [(2048, 0), (2352, 24), (2352, 16)]


def _read_serial_from_iso(
    file_path: Path,
    sector_size: int,
    data_offset: int,
) -> Optional[str]:
    """
    Parse ISO 9660 PVD -> root directory -> find SYSTEM.CNF -> extract BOOT serial.
    Full port of Android's readPsSerialFromIso() method.
    """
    try:
        with open(file_path, "rb") as f:
            file_size = f.seek(0, 2)

            def le32(buf: bytes, off: int) -> int:
                return struct.unpack_from("<I", buf, off)[0]

            def read_sector(lba: int) -> Optional[bytes]:
                pos = lba * sector_size + data_offset
                if pos < 0 or pos + 2048 > file_size:
                    return None
                f.seek(pos)
                data = f.read(2048)
                return data if len(data) == 2048 else None

            # Read Primary Volume Descriptor at LBA 16
            pvd = read_sector(16)
            if pvd is None:
                return None
            # Verify "CD001" signature at offset 1
            if pvd[1:6] != b"CD001":
                return None

            # Root directory record starts at offset 156
            root_lba = le32(pvd, 156 + 2)
            root_size = le32(pvd, 156 + 10)
            if root_lba <= 0 or root_size <= 0:
                return None

            # Read the entire root directory
            root_data = bytearray()
            lba = root_lba
            while len(root_data) < root_size:
                sec = read_sector(lba)
                if sec is None:
                    break
                lba += 1
                remaining = root_size - len(root_data)
                root_data.extend(sec[:remaining])

            if not root_data:
                return None

            # Walk directory records looking for SYSTEM.CNF
            pos = 0
            while pos < len(root_data):
                rec_len = root_data[pos]
                if rec_len == 0:
                    # Jump to next sector boundary
                    pos = ((pos // 2048) + 1) * 2048
                    continue
                if pos + rec_len > len(root_data):
                    break

                flags = root_data[pos + 25]
                name_len = root_data[pos + 32]
                if name_len > 0 and (flags & 0x02) == 0:  # Not a directory
                    raw_name = root_data[pos + 33 : pos + 33 + name_len].decode(
                        "ascii", errors="replace"
                    )
                    name = raw_name.split(";")[0].upper()
                    if name == "SYSTEM.CNF":
                        file_lba = le32(root_data, pos + 2)
                        file_size_val = le32(root_data, pos + 10)
                        if file_lba <= 0 or file_size_val <= 0:
                            return None

                        # Read SYSTEM.CNF content
                        cnf_data = bytearray()
                        cnf_lba = file_lba
                        read_size = min(file_size_val, 4096)
                        while len(cnf_data) < read_size:
                            sec = read_sector(cnf_lba)
                            if sec is None:
                                break
                            cnf_lba += 1
                            remaining = read_size - len(cnf_data)
                            cnf_data.extend(sec[:remaining])

                        cnf_text = cnf_data.decode("ascii", errors="ignore")
                        # Match BOOT or BOOT2 line
                        m = re.search(
                            r"BOOT\d?\s*=\s*cdrom\d*[:\\]+([A-Z]{4})[_-](\d{5})",
                            cnf_text,
                            re.IGNORECASE,
                        )
                        if m:
                            return m.group(1).upper() + m.group(2)
                        return None

                pos += rec_len

    except Exception:
        pass
    return None


def read_ps1_serial(iso_path: Path) -> Optional[str]:
    """
    Read PS1/PS2 product serial from an ISO/BIN/CUE disc image.
    Parses ISO 9660 filesystem to find SYSTEM.CNF and extract the BOOT line.

    Returns a bare product code (e.g. "SLUS01234"), or None.
    Works for both PS1 and PS2 discs (PS2 uses BOOT2 in SYSTEM.CNF).
    """
    resolved = _resolve_disc_image(iso_path)
    if resolved is None:
        return None

    for sector_size, data_offset in _build_disc_offsets(resolved):
        serial = _read_serial_from_iso(resolved, sector_size, data_offset)
        if serial:
            return serial

    return None


# Alias: PS2 uses the same SYSTEM.CNF/BOOT mechanism
read_ps2_serial = read_ps1_serial


# ──────────────────────────────────────────────────────────────────────────────
# ROM file helpers
# ──────────────────────────────────────────────────────────────────────────────

PS1_ROM_EXTENSIONS = {".iso", ".bin", ".cue", ".img", ".mdf", ".chd", ".pbp"}
PS2_ROM_EXTENSIONS = {".iso", ".bin", ".img", ".mdf", ".cue", ".chd"}

PS1_ROM_DIRS = [
    "PS1",
    "ps1",
    "PSX",
    "psx",
    "PlayStation",
    "playstation",
    "PlayStation 1",
    "PlayStation1",
]
PS2_ROM_DIRS = ["PS2", "ps2", "PlayStation2", "PlayStation 2", "Sony - PlayStation 2"]


def find_rom_dirs(base_paths: list[Path], dir_names: list[str]) -> list[Path]:
    """Find existing ROM directories under one or more base paths."""
    dirs: list[Path] = []
    seen: set[str] = set()
    for base in base_paths:
        if not base.exists():
            continue
        for name in dir_names:
            d = base / name
            key = str(d.resolve())
            if d.exists() and d.is_dir() and key not in seen:
                seen.add(key)
                dirs.append(d)
    return dirs


def scan_rom_files(dirs: list[Path], extensions: set[str]) -> list[Path]:
    """
    Walk directories and return all ROM files matching extensions.

    Deduplicates .cue/.bin pairs: if both a .cue and its referenced .bin exist,
    only the .cue is returned (since _resolve_disc_image handles .cue -> .bin).
    """
    roms: list[Path] = []
    # Collect all matching files first
    all_files: list[Path] = []
    for d in dirs:
        for f in d.rglob("*"):
            if f.is_file() and f.suffix.lower() in extensions:
                all_files.append(f)

    # Build a set of .bin/.img files that are referenced by .cue files
    cue_referenced: set[str] = set()
    for f in all_files:
        if f.suffix.lower() == ".cue":
            try:
                for line in f.read_text(errors="ignore").splitlines():
                    if line.strip().upper().startswith("FILE"):
                        m = re.search(r'FILE\s+"(.+?)"', line, re.IGNORECASE)
                        if m:
                            ref = (f.parent / m.group(1)).resolve()
                            cue_referenced.add(str(ref))
            except Exception:
                pass

    # Filter: skip .bin/.img files that are already covered by a .cue
    for f in all_files:
        if f.suffix.lower() in (".bin", ".img") and str(f.resolve()) in cue_referenced:
            continue
        roms.append(f)

    return roms


# ──────────────────────────────────────────────────────────────────────────────
# NDS helpers (kept for compatibility)
# ──────────────────────────────────────────────────────────────────────────────


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


def find_paths(*candidates: Path) -> Optional[Path]:
    """Return the first existing path from candidates."""
    for p in candidates:
        if p.exists():
            return p
    return None
