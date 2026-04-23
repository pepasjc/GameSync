"""Base utilities shared by all emulator scanners."""

import hashlib
import re
import struct
import sys
from pathlib import Path
from typing import Optional

# Make the repo root importable so 'shared' can be found.
_REPO_ROOT = str(Path(__file__).parent.parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from shared.systems import PSX_RETAIL_PREFIXES  # noqa: E402

# Re-export the canonical ROM name / title_id helpers from the shared package
# so every scanner uses the SAME normalisation rules as the server and the
# other Python clients.  Having two variants was the #1 source of sync bugs —
# region-stripped slugs on one side, region-preserving on the other meant the
# same ROM could land under two different server keys.
from shared.rom_id import (  # noqa: E402  (re-exported)
    make_title_id,
    normalize_rom_name,
)


# ──────────────────────────────────────────────────────────────────────────────
# PS1/PS2 serial + shared card constants
# ──────────────────────────────────────────────────────────────────────────────

# PS1 product code pattern: 4 letters + 5+ digits (e.g. SLUS01234)
PS1_SERIAL_RE = re.compile(r"^[A-Z]{4}\d{5,}$")

# PSX_RETAIL_PREFIXES imported from shared.systems above.

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
NDS_ROM_DIRS = ["nds", "NDS", "DS", "Nintendo DS", "Nintendo - Nintendo DS"]
NDS_ROM_EXTENSIONS = {".nds", ".dsi"}


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


def nds_gamecode_to_title_id(gamecode: str) -> Optional[str]:
    """Build the canonical 16-char hex NDS title_id from a 4-char gamecode.

    Matches the format used by the 3DS/NDS homebrew clients and the Android
    client so a save uploaded from Steam Deck ends up in the same server slot:

        "AMKJ"  →  "00048000414D4B4A"

    Returns None for invalid input.
    """
    if not gamecode or len(gamecode) != 4:
        return None
    if not all(0x20 <= ord(c) < 0x7F for c in gamecode):
        return None
    return "00048000" + "".join(f"{ord(c):02X}" for c in gamecode)


def find_matching_nds_rom(rom_stem: str, search_dirs: list[Path]) -> Optional[Path]:
    """Find an NDS/DSi ROM whose stem matches ``rom_stem`` (case-insensitive).

    Searches each directory in ``search_dirs`` recursively; returns the first
    hit.  Used by the melonDS scanner to promote slug title_ids to the
    canonical hex form whenever the matching ROM is available on the device.
    """
    if not rom_stem:
        return None
    lowered = rom_stem.lower()
    for d in search_dirs:
        if not d.exists() or not d.is_dir():
            continue
        try:
            for f in d.rglob("*"):
                if (
                    f.is_file()
                    and f.suffix.lower() in NDS_ROM_EXTENSIONS
                    and f.stem.lower() == lowered
                ):
                    return f
        except (PermissionError, OSError):
            continue
    return None


def find_paths(*candidates: Path) -> Optional[Path]:
    """Return the first existing path from candidates."""
    for p in candidates:
        if p.exists():
            return p
    return None
