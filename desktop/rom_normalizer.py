#!/usr/bin/env python3
"""ROM Normalizer — renames ROMs (and their matching saves) to a canonical form.

Usage:
    # Auto-discover DAT from dats/ folder by system name:
    python rom_normalizer.py <folder> --system SNES
    python rom_normalizer.py <folder> --system SNES --apply

    # Provide DAT file explicitly:
    python rom_normalizer.py <folder> --dat path/to/snes.dat

    # Normalize by filename only (no DAT):
    python rom_normalizer.py <folder> --no-crc

By default shows a preview of proposed renames. Use --apply to rename files.
Scanning is recursive — subfolders like USA/, Japan/, #-C/ are all processed.

DAT auto-discovery looks for .dat files in a dats/ folder next to this script.
It matches by system keyword (e.g. --system SNES finds a DAT containing
"Super Nintendo" in its filename).

Normalization rules (same as the server's rom_id.py):
  - Strip extension
  - Strip region tags: (USA), (Europe), (Japan), etc.
  - Strip revision tags: (Rev 1), (v1.1), (Beta), (Demo), etc.
  - Strip disc tags: (Disc 1), (Disk 2), etc.
  - Strip any remaining parenthetical tags
  - Lowercase, replace non-alphanumeric with underscores

Save files (.sav, .srm, .mcr, .frz) with the same stem as the ROM are renamed alongside it.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

from systems import (
    COMPANION_EXTENSIONS,
    ROM_EXTENSIONS,
    SAVE_EXTENSIONS,
    SYSTEM_DAT_KEYWORDS,
)

# Same normalization as sync_engine.py / server/app/services/rom_id.py
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
_BRACKET_RE = re.compile(r"\s*\[[^\]]*\]")  # strip [T+Eng], [Hack], etc.
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_MULTI_UNDERSCORE_RE = re.compile(r"_+")
_TRAILING_ARTICLE_SLUG_RE = re.compile(
    r"^(?P<main>.+)_(?P<article>the|a|an)$", re.IGNORECASE
)
_LETTER_DIGIT_BOUNDARY_RE = re.compile(
    r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])", re.IGNORECASE
)

# Trailing patch/translation metadata to strip when matching header titles.
# Handles patterns like: _eng_v31, _ger, _v1_0, _r2, _rev2, _patch, _hack, etc.
_MSU_TRACK_RE = re.compile(r"^(.+)-(\d+)\.pcm$", re.IGNORECASE)

_SPECIAL_TAG_RE = re.compile(
    r"\((?:Beta\s*\d*|Proto\s*\d*|Demo|Sample)\)",
    re.IGNORECASE,
)


def _has_special_tag(name: str) -> bool:
    """Return True if name contains a Beta/Proto/Demo/Sample/Unl tag."""
    return bool(_SPECIAL_TAG_RE.search(name))


_PATCH_SUFFIX_RE = re.compile(
    r"(?:_(?:eng|ger|jpn|fre|fra|spa|por|ita|pol|rus|chi|kor|dut|swe|nor|dan|fin|"
    r"v\d[\d_a-z]*|r\d+|rev\d+|patch\d*|translation|fix\d*|hack))+$",
    re.IGNORECASE,
)

# Roman numeral ↔ arabic mapping (1–15 covers virtually all game sequels)
_ROMAN_TO_ARABIC: dict[str, str] = {
    "i": "1",
    "ii": "2",
    "iii": "3",
    "iv": "4",
    "v": "5",
    "vi": "6",
    "vii": "7",
    "viii": "8",
    "ix": "9",
    "x": "10",
    "xi": "11",
    "xii": "12",
    "xiii": "13",
    "xiv": "14",
    "xv": "15",
}
_ARABIC_TO_ROMAN: dict[str, str] = {v: k for k, v in _ROMAN_TO_ARABIC.items()}

# ROM_EXTENSIONS, SAVE_EXTENSIONS, COMPANION_EXTENSIONS imported from systems


def normalize_name(filename: str) -> str:
    """Strip extension, tags; return lowercase slug."""
    name = filename
    # Strip extension(s)
    for _ in range(3):
        dot_idx = name.rfind(".")
        if dot_idx <= 0:
            break
        suffix = name[dot_idx + 1 :]
        if 1 <= len(suffix) <= 5 and suffix.isalnum():
            name = name[:dot_idx]
        else:
            break

    name = _BRACKET_RE.sub("", name)  # strip [T+Eng], [Hack], [!] etc.
    name = _REGION_RE.sub("", name)
    name = _REV_RE.sub("", name)
    name = _DISC_RE.sub("", name)
    name = _EXTRA_RE.sub("", name)
    name = name.lower()
    name = _NON_ALNUM_RE.sub("_", name)
    name = _MULTI_UNDERSCORE_RE.sub("_", name).strip("_")
    return name or "unknown"


def _article_slug_variants(slug: str) -> list[str]:
    """Return variants for titles that move an article to/from the end.

    Example:
      "the_revenge_of_shinobi" -> ["revenge_of_shinobi_the"]
      "revenge_of_shinobi_the" -> ["the_revenge_of_shinobi"]
    """
    variants: list[str] = []
    parts = slug.split("_")
    if len(parts) >= 2 and parts[0] in {"the", "a", "an"}:
        variants.append("_".join(parts[1:] + [parts[0]]))
    m = _TRAILING_ARTICLE_SLUG_RE.match(slug)
    if m:
        variants.append(f"{m.group('article')}_{m.group('main')}")
    return variants


def _digit_boundary_variants(slug: str) -> list[str]:
    """Return variants that add/remove underscores between letter/digit boundaries.

    Example:
      "ex2" -> ["ex_2"]
      "ex_2" -> ["ex2"]
    """
    variants: list[str] = []
    separated = _LETTER_DIGIT_BOUNDARY_RE.sub("_", slug)
    separated = _MULTI_UNDERSCORE_RE.sub("_", separated).strip("_")
    compact = slug.replace("_", "")
    if separated and separated != slug:
        variants.append(separated)
    if compact and compact != slug:
        variants.append(compact)
    return variants


def _matching_slug_variants(slug: str) -> list[str]:
    """Return de-duplicated match-time slug variants for fuzzy/header lookup."""
    seen: set[str] = set()
    queue = [slug]
    variants: list[str] = []
    while queue:
        current = queue.pop(0)
        for variant in (
            _slug_roman_variants(current)
            + _article_slug_variants(current)
            + _digit_boundary_variants(current)
        ):
            if variant and variant != slug and variant not in seen:
                seen.add(variant)
                variants.append(variant)
                queue.append(variant)
    return variants


def _collapsed_slug(slug: str) -> str:
    """Return a compact slug for tolerant comparisons.

    Example:
      "super_dodge_ball_advance" -> "superdodgeballadvance"
      "super_dodgeball_advance"  -> "superdodgeballadvance"
    """
    return slug.replace("_", "")


# ---------------------------------------------------------------------------
# N64 byte-order detection and conversion
# ---------------------------------------------------------------------------
# N64 ROMs come in three byte orders, identified by the first 4 bytes:
#   .z64 (big-endian / native):  80 37 12 40
#   .v64 (byte-swapped):         37 80 40 12  — every pair of bytes swapped
#   .n64 (word-swapped / little-endian):  40 12 37 80  — every 4-byte word reversed
# No-Intro DATs store CRCs for the .z64 (big-endian) byte order.  The DAT
# also has separate entries with CRCs for the .v64 byte order, but NOT for
# .n64.  To get CRC matches for .n64 (and .v64 if the DAT only has .z64
# entries), we convert to big-endian before computing the CRC.

_N64_MAGIC_Z64 = b"\x80\x37\x12\x40"  # big-endian (native)
_N64_MAGIC_V64 = b"\x37\x80\x40\x12"  # byte-swapped
_N64_MAGIC_N64 = b"\x40\x12\x37\x80"  # word-swapped (little-endian)

_N64_EXTENSIONS = frozenset((".n64", ".v64", ".z64"))


def detect_n64_byte_order(header4: bytes) -> str | None:
    """Return ``'z64'``, ``'v64'``, or ``'n64'`` based on the first 4 bytes.

    Returns ``None`` if the header doesn't match any known N64 magic.
    """
    if header4[:4] == _N64_MAGIC_Z64:
        return "z64"
    if header4[:4] == _N64_MAGIC_V64:
        return "v64"
    if header4[:4] == _N64_MAGIC_N64:
        return "n64"
    return None


def _byteswap_v64(data: bytes) -> bytes:
    """Convert v64 (byte-swapped) data to z64 (big-endian).

    Swaps every pair of adjacent bytes: AB CD -> BA DC.
    """
    arr = bytearray(data)
    # Ensure even length by truncating last odd byte (shouldn't happen in practice)
    end = len(arr) & ~1
    for i in range(0, end, 2):
        arr[i], arr[i + 1] = arr[i + 1], arr[i]
    return bytes(arr)


def _wordswap_n64(data: bytes) -> bytes:
    """Convert n64 (word-swapped / little-endian) data to z64 (big-endian).

    Reverses every group of 4 bytes: DCBA -> ABCD.
    """
    arr = bytearray(data)
    end = len(arr) & ~3  # align to 4-byte boundary
    for i in range(0, end, 4):
        arr[i], arr[i + 1], arr[i + 2], arr[i + 3] = (
            arr[i + 3],
            arr[i + 2],
            arr[i + 1],
            arr[i],
        )
    return bytes(arr)


def n64_to_z64(data: bytes, byte_order: str) -> bytes:
    """Convert N64 ROM *data* to big-endian (z64) byte order.

    *byte_order* should be one of ``'z64'``, ``'v64'``, ``'n64'``.
    If already ``'z64'``, returns *data* unchanged.
    """
    if byte_order == "z64":
        return data
    if byte_order == "v64":
        return _byteswap_v64(data)
    if byte_order == "n64":
        return _wordswap_n64(data)
    return data


def _crc32_file(path: Path) -> str:
    """Compute CRC32 of file contents, returned as 8-char uppercase hex.

    For N64 ROMs in non-native byte order (``.v64``, ``.n64``), the data is
    converted to big-endian (``.z64``) before computing the CRC so that the
    result matches the No-Intro DAT entries.
    """
    ext = path.suffix.lower()
    is_n64 = ext in _N64_EXTENSIONS

    if is_n64:
        # Read the whole file to detect byte order and convert
        raw = path.read_bytes()
        order = detect_n64_byte_order(raw[:4])
        if order and order != "z64":
            raw = n64_to_z64(raw, order)
        crc = zlib.crc32(raw) & 0xFFFFFFFF
        return f"{crc:08X}"

    crc = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            crc = zlib.crc32(chunk, crc)
    return f"{crc & 0xFFFFFFFF:08X}"


# ---------------------------------------------------------------------------
# Sony disc-serial extraction (PS1 / PS2 / PSP / PS3)
# ---------------------------------------------------------------------------

# Systems that ship with a 4-letter + 5-digit Sony disc serial in their DAT
# and whose ROM filenames commonly encode the serial.  Used to gate the
# serial-based lookup so we don't waste cycles on, say, SNES files.
_SERIAL_LOOKUP_SYSTEMS: frozenset[str] = frozenset({"PS1", "PS2", "PSP", "PS3"})

# Every 4-letter prefix that the libretro PS1/PS2/PSP/PS3 DATs actually emit.
# Kept here (rather than in systems.py) so the normalizer has zero run-time
# deps on the config layer.
_PS_DISC_SERIAL_PREFIXES: frozenset[str] = frozenset(
    {
        # PS1 / PS2 (Sony)
        "SLUS", "SCUS", "SCED", "PAPX", "PBPX",
        "SLES", "SCES",
        "SLPS", "SLPM", "SCPS", "SCPM", "SLPN",
        # Other-region Sony
        "SCAJ", "SLAJ", "SLKA", "SCKA", "SLEJ",
        # PSP UMD
        "ULUS", "ULES", "ULJM", "ULJS", "UCUS", "UCES", "UCJS", "UCJM",
        "UCKS", "UCAS", "ULKS", "ULAS",
        # PSP / PSN downloadable
        "NPUG", "NPEG", "NPJG", "NPUH", "NPEH", "NPJH",
        # PS3 retail Blu-ray (BL** = retail, BC** = first-party Sony retail)
        "BLUS", "BLES", "BLJM", "BLJS", "BLAS", "BLKS",
        "BCUS", "BCES", "BCJS", "BCAS", "BCKS",
        # PS3 PSN downloadable (NP*B*)
        "NPUB", "NPEB", "NPJB", "NPHB", "NPIB",
        # PS3 PSN demos / addons (less common but appear in DATs)
        "NPUA", "NPEA", "NPJA", "NPHA", "NPIA",
    }
)

# Matches a PlayStation disc serial embedded in a filename.
#
# Handles the three separator conventions seen in the wild:
#     SCES_538.51.game name.iso   -> underscore + dot
#     SCES-53851 - Foo.iso        -> bare hyphen
#     SLUS20265 - Bar.iso         -> no separator
#
# The leading lookbehind prevents matching inside longer tokens (e.g. a CRC
# like "ASLUS20265F").  The trailing lookahead keeps us from eating into
# suffixes such as "SLUS-20265GH" – we match the 5-digit base and the lookup
# table holds both the bare and suffixed variants.
_PS_SERIAL_RE = re.compile(
    r"(?:^|(?<=[^A-Za-z0-9]))"
    r"([A-Za-z]{4})[-_ ]?(\d{3})[._\- ]?(\d{2})"
    r"(?=$|[^0-9])",
)


def extract_ps_serial(filename: str) -> str | None:
    """Extract a Sony PlayStation disc serial from a ROM filename.

    Returns the canonical ``PREFIX-NNNNN`` form (matching the libretro DAT
    representation), or None when no recognised serial is present.

    Examples
    --------
    >>> extract_ps_serial("SCES_538.51.game name.iso")
    'SCES-53851'
    >>> extract_ps_serial("SLUS-20265 - Agent Under Fire.iso")
    'SLUS-20265'
    >>> extract_ps_serial("SLPM65002 - 0 Story.iso")
    'SLPM-65002'
    >>> extract_ps_serial("Super Mario 64.z64") is None
    True
    """
    # Drop the extension so numeric extensions (e.g. ".z64") can't trip the
    # digit portion of the match.
    stem = Path(filename).stem
    for match in _PS_SERIAL_RE.finditer(stem):
        prefix = match.group(1).upper()
        if prefix in _PS_DISC_SERIAL_PREFIXES:
            return f"{prefix}-{match.group(2)}{match.group(3)}"
    return None


def supports_serial_lookup(system: str) -> bool:
    """True when the given system code has a usable serial-based lookup."""
    return (system or "").upper() in _SERIAL_LOOKUP_SYSTEMS


def lookup_serial(serial: str, serial_map: dict[str, str]) -> str | None:
    """Look up a serial in a libretro ``{serial: name}`` map.

    Falls back to matching a variant with trailing region / disc index suffixes
    when the bare serial is absent, e.g. ``SLUS-20265`` → ``SLUS-20265GH``.
    """
    if not serial or not serial_map:
        return None
    if serial in serial_map:
        return serial_map[serial]
    prefix = serial + "-"
    prefix_slash = serial + "/"
    for candidate, name in serial_map.items():
        if candidate.startswith(prefix) or candidate.startswith(prefix_slash):
            return name
    # Some DATs pad the digit portion with a suffix letter (SLUS-20265GH).
    # Accept that too.
    for candidate, name in serial_map.items():
        if candidate.startswith(serial) and len(candidate) > len(serial):
            tail = candidate[len(serial) :]
            if tail and not tail[0].isdigit():
                return name
    return None


def load_serial_map(dat_path: Path) -> dict[str, str]:
    """Return a ``{serial: canonical_name}`` map from ``dat_path``.

    Thin wrapper over :func:`load_libretro_dat` kept for clarity at call
    sites — libretro clrmamepro DATs carry explicit ``serial "..."`` fields,
    while No-Intro XML DATs generally do not, so callers can rely on an empty
    result being a real answer.
    """
    try:
        return load_libretro_dat(dat_path)
    except Exception:
        return {}


def load_no_intro_dat(dat_path: Path) -> dict[str, str]:
    """Parse a No-Intro DAT file (XML or libretro clrmamepro text format).

    Auto-detects format by inspecting the first non-empty line.

    Returns a dict mapping CRC32 (uppercase 8-char hex) -> canonical name.
    """
    try:
        with open(dat_path, "r", encoding="utf-8", errors="replace") as fh:
            for first_line in fh:
                stripped = first_line.strip()
                if stripped:
                    break
            else:
                stripped = ""
    except Exception as e:
        print(f"WARNING: Could not read DAT file: {e}")
        return {}

    if stripped.startswith("<"):
        return _load_no_intro_xml(dat_path)
    else:
        return _load_clrmamepro_dat(dat_path)


def _load_no_intro_xml(dat_path: Path) -> dict[str, str]:
    """Parse a No-Intro/Redump XML DAT.  Returns CRC32 → canonical name."""
    try:
        tree = ET.parse(dat_path)
    except Exception as e:
        print(f"WARNING: Could not parse DAT file: {e}")
        return {}

    crc_to_name: dict[str, str] = {}
    root = tree.getroot()
    for game in root.findall(".//game"):
        name = game.get("name", "")
        for rom in game.findall("rom"):
            crc = rom.get("crc", "").upper()
            if crc and name:
                crc_to_name[crc] = name
    return crc_to_name


_CLRMAME_ROM_RE = re.compile(
    r"\brom\s*\(.*?\bcrc\s+([0-9A-Fa-f]{1,8})\b", re.IGNORECASE
)
_CLRMAME_NAME_RE = re.compile(r'^\s*name\s+"(.+?)"')
_CLRMAME_CLONEOF_RE = re.compile(r'^\s*cloneof\s+"(.+?)"')


def _load_clrmamepro_dat(dat_path: Path) -> dict[str, str]:
    """Parse a libretro clrmamepro text-format DAT.  Returns CRC32 → canonical name."""
    crc_to_name: dict[str, str] = {}
    current_name: str | None = None
    try:
        with open(dat_path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = _CLRMAME_NAME_RE.match(line)
                if m:
                    current_name = m.group(1).strip()
                    continue
                rm = _CLRMAME_ROM_RE.search(line)
                if rm and current_name:
                    crc = rm.group(1).upper().zfill(8)
                    if crc and crc != "00000000":
                        crc_to_name[crc] = current_name
                if line.strip() == ")":
                    current_name = None
    except Exception as e:
        print(f"WARNING: Could not parse clrmamepro DAT file: {e}")
    return crc_to_name


def load_cloneof_map(dat_path: Path) -> dict[str, str]:
    """Parse ``cloneof`` fields from a DAT file.

    Returns a dict mapping canonical name → clone-group leader name.
    Only entries that have a ``cloneof`` field are included; leaders
    (entries without ``cloneof``) are omitted.

    Works with both clrmamepro text-format and No-Intro XML DATs.
    """
    try:
        with open(dat_path, "r", encoding="utf-8", errors="replace") as fh:
            for first_line in fh:
                stripped = first_line.strip()
                if stripped:
                    break
            else:
                stripped = ""
    except Exception:
        return {}

    if stripped.startswith("<"):
        return _load_cloneof_xml(dat_path)
    return _load_cloneof_clrmamepro(dat_path)


def _load_cloneof_xml(dat_path: Path) -> dict[str, str]:
    """Extract clone-of relationships from a No-Intro XML DAT."""
    try:
        tree = ET.parse(dat_path)
    except Exception:
        return {}
    clone_map: dict[str, str] = {}
    id_to_name: dict[str, str] = {}
    root = tree.getroot()
    for game in root.findall(".//game"):
        gid = game.get("id", "")
        name = game.get("name", "")
        if gid and name:
            id_to_name[gid] = name
    for game in root.findall(".//game"):
        name = game.get("name", "")
        cloneofid = game.get("cloneofid")
        if name and cloneofid and cloneofid in id_to_name:
            clone_map[name] = id_to_name[cloneofid]
    return clone_map


def _load_cloneof_clrmamepro(dat_path: Path) -> dict[str, str]:
    """Extract ``cloneof`` fields from a clrmamepro text-format DAT."""
    clone_map: dict[str, str] = {}
    current_name: str | None = None
    try:
        with open(dat_path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = _CLRMAME_NAME_RE.match(line)
                if m:
                    current_name = m.group(1).strip()
                    continue
                cm = _CLRMAME_CLONEOF_RE.match(line)
                if cm and current_name:
                    clone_map[current_name] = cm.group(1).strip()
                if line.strip() == ")":
                    current_name = None
    except Exception:
        pass
    return clone_map


# DAT keyword lookup — imported from systems; alias kept for internal use
_SYSTEM_DAT_KEYWORDS = SYSTEM_DAT_KEYWORDS

DATS_DIR = Path(__file__).parent.parent / "server" / "data" / "dats"


def normalize_alias_lookup_name(filename: str) -> str:
    """Normalize translated ROM names while ignoring patch-style [] tags."""
    return normalize_name(_BRACKET_RE.sub("", filename).strip())


def load_alias_index(system: str, no_intro: dict[str, str]) -> dict[str, str]:
    """Load the alias map for one system, filtered to valid DAT targets only."""
    aliases_path = DATS_DIR / "EN-Dats" / "aliases.json"
    if not aliases_path.is_file():
        return {}

    try:
        payload = json.loads(aliases_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}

    system_aliases = payload.get(system.upper().strip(), {})
    if not isinstance(system_aliases, dict):
        return {}

    valid_canonicals = set(no_intro.values())
    alias_index: dict[str, str] = {}
    for alias_name, canonical_name in system_aliases.items():
        alias = str(alias_name or "").strip()
        canonical = str(canonical_name or "").strip()
        if not alias or not canonical or canonical not in valid_canonicals:
            continue
        alias_index[normalize_alias_lookup_name(alias)] = canonical
    return alias_index


def load_redump_dat(dat_path: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Parse a Redump DAT file for disc-based systems (PS1, Saturn, Dreamcast, etc.).

    Accepts both Redump XML format and libretro clrmamepro text format.
    Auto-detects format by inspecting the first non-empty line.

    Returns:
        (crc_to_name, disc_agnostic_index)

        crc_to_name:
            CRC32 (8-char uppercase hex) → canonical Redump game name.
            ANY track's CRC maps to the full game name (including disc tag).
            e.g. ``"AB12CD34"`` → ``"Parasite Eve (USA) (Disc 1)"``

        disc_agnostic_index:
            Disc-agnostic slug → canonical name with disc tag stripped.
            Used for folder-name matching without a CRC.
            e.g. ``"parasite_eve"`` → ``"Parasite Eve (USA)"``
            Multi-disc duplicates are resolved by region priority (USA > Japan > Europe).
    """
    # Get CRC→name using the format-detecting loader, then build disc-agnostic index
    crc_to_name = load_no_intro_dat(dat_path)
    disc_agnostic = build_redump_name_index(crc_to_name)
    return crc_to_name, disc_agnostic


def build_redump_name_index(crc_to_name: dict[str, str]) -> dict[str, str]:
    """Build a disc-agnostic slug → canonical name index from a Redump CRC dict.

    Convenience wrapper: accepts the first value returned by ``load_redump_dat``
    and produces the same disc-agnostic index as the second return value.

    Useful when you already have a ``crc_to_name`` dict and need the folder-name
    lookup index separately.

    Returns:
        ``{disc_agnostic_slug: canonical_name_without_disc_tag}``
        e.g. ``{"parasite_eve": "Parasite Eve (USA)"}``
    """
    disc_agnostic: dict[str, str] = {}
    disc_priority: dict[str, int] = {}
    for name in crc_to_name.values():
        name_no_disc = _DISC_RE.sub("", name).strip()
        slug = normalize_name(name_no_disc)
        if not slug:
            continue
        p = _region_priority(name)
        if slug not in disc_agnostic:
            disc_agnostic[slug] = name_no_disc
            disc_priority[slug] = p
        else:
            existing_p = disc_priority[slug]
            if p < existing_p or (
                p == existing_p
                and _tag_count(name_no_disc) < _tag_count(disc_agnostic[slug])
            ):
                disc_agnostic[slug] = name_no_disc
                disc_priority[slug] = p
    return disc_agnostic


def find_dat_for_system(system: str) -> Path | None:
    """Search the dats/ folder for a DAT matching the given system code."""
    if not DATS_DIR.exists():
        return None
    keywords = _SYSTEM_DAT_KEYWORDS.get(system.upper(), [system])
    dat_files = sorted(DATS_DIR.glob("*.dat"))

    def _score(dat_file: Path) -> tuple[int, int, int, str] | None:
        name = dat_file.stem.lower()
        normalized = re.sub(r"[^a-z0-9]+", " ", name).strip()
        best: tuple[int, int, int, str] | None = None
        for kw in keywords:
            kw_lower = kw.lower()
            kw_normalized = re.sub(r"[^a-z0-9]+", " ", kw_lower).strip()
            if kw_lower == name or kw_normalized == normalized:
                score = (0, len(normalized), len(name), dat_file.name.lower())
            elif re.search(rf"\b{re.escape(kw_normalized)}\b", normalized):
                score = (1, len(normalized), len(name), dat_file.name.lower())
            elif kw_lower in name:
                score = (2, len(normalized), len(name), dat_file.name.lower())
            else:
                continue
            if best is None or score < best:
                best = score
        return best

    ranked = [(score, dat_file) for dat_file in dat_files if (score := _score(dat_file))]
    if ranked:
        ranked.sort(key=lambda item: item[0])
        return ranked[0][1]
    return None


# Maps system code to a keyword fragment that identifies a libretro DAT filename.
_LIBRETRO_DAT_KEYWORDS: dict[str, str] = {
    "SAT": "Sega - Saturn",
    "PS1": "Sony - PlayStation",
    "PS2": "Sony - PlayStation 2",
    "SEGACD": "Sega - Mega-CD",
    "DC": "Sega - Dreamcast",
    "GC": "Nintendo - GameCube",
    "NDS": "Nintendo - Nintendo DS",
    "GBA": "Nintendo - Game Boy Advance",
}

# Region priority for libretro DAT name selection (lower index = higher priority).
# For Japanese product codes (ending in G) we prefer Japan; for H codes prefer USA/Europe.
_LIBRETRO_REGION_PRIORITY: list[str] = [
    "Japan",
    "USA",
    "Europe",
    "Germany",
    "France",
    "Spain",
]


def _libretro_region_priority(region: str, serial: str) -> int:
    """Return priority score for a libretro DAT entry. Lower = better match."""
    # Japanese product codes end in G; prefer Japan for those.
    # North-American codes end in H; prefer USA for those.
    serial_upper = serial.upper()
    if serial_upper.endswith("G") or serial_upper.startswith("GS-"):
        preferred = ["Japan", "USA", "Europe"]
    elif serial_upper.endswith("H") or serial_upper.endswith("H-50"):
        preferred = ["USA", "Europe", "Japan"]
    else:
        preferred = _LIBRETRO_REGION_PRIORITY
    try:
        return preferred.index(region)
    except ValueError:
        return len(preferred)


def load_libretro_dat(dat_path: Path) -> dict[str, str]:
    """Parse a libretro clrmamepro DAT file and return serial → game name mapping.

    The clrmamepro format looks like::

        game (
            name "Game Title (Region)"
            region "Japan"
            serial "T-12345G"
            rom ( ... )
        )

    When multiple entries share the same serial (e.g. multi-disc or revisions),
    the entry with the best region match for that serial's origin is kept.
    Disc tags like ``(Disc 1)`` are stripped from the stored name.

    Returns:
        ``{serial_string: canonical_game_name}``
    """
    try:
        text = dat_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        print(f"WARNING: Could not read libretro DAT: {e}")
        return {}

    serial_to_name: dict[str, str] = {}
    serial_priority: dict[str, int] = {}

    # Parse game blocks line-by-line
    in_game = False
    cur_name = ""
    cur_region = ""
    cur_serials: list[str] = []

    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "game (":
            in_game = True
            cur_name = ""
            cur_region = ""
            cur_serials = []
        elif stripped == ")" and in_game:
            # End of game block — register all serials found
            if cur_name and cur_serials:
                name_no_disc = _DISC_RE.sub("", cur_name).strip()
                for serial in cur_serials:
                    # Skip disc-index suffixes like T-21301G-0, GS-9076-2.
                    # The disc-index is a single digit appended after a letter
                    # (e.g. ...G-0, ...H-1).  Do NOT skip serials that naturally
                    # end in digits like GS-9169 or T-18003G (the number is part
                    # of the code itself).
                    if re.search(r"[A-Za-z]-\d$", serial):
                        continue
                    priority = _libretro_region_priority(cur_region, serial)
                    if (
                        serial not in serial_to_name
                        or priority < serial_priority[serial]
                    ):
                        serial_to_name[serial] = name_no_disc
                        serial_priority[serial] = priority
            in_game = False
        elif in_game:
            m = re.match(r'\s+name\s+"(.+)"', line)
            if m:
                cur_name = m.group(1)
                continue
            m = re.match(r'\s+region\s+"(.+)"', line)
            if m:
                cur_region = m.group(1)
                continue
            m = re.match(r'\s+serial\s+"(.+)"', line)
            if m:
                cur_serials.append(m.group(1))

    return serial_to_name


def find_libretro_dat_for_system(system: str) -> Path | None:
    """Search the dats/ folder for a libretro clrmamepro DAT for the given system.

    Libretro DATs are identified by containing the system keyword in the filename
    and are expected to be named like ``Sega - Saturn (libretro).dat``.
    Falls back to any filename match if the explicit ``(libretro)`` tag is absent.

    Returns the Path if found, else None.
    """
    if not DATS_DIR.exists():
        return None
    keyword = _LIBRETRO_DAT_KEYWORDS.get(system.upper())
    if not keyword:
        return None

    kw_lower = keyword.lower()
    candidates = [f for f in DATS_DIR.glob("*.dat") if kw_lower in f.name.lower()]
    if not candidates:
        return None

    # Prefer files that explicitly carry "(libretro)" in the name
    libretro_candidates = [f for f in candidates if "libretro" in f.name.lower()]
    return libretro_candidates[0] if libretro_candidates else candidates[0]


# When multiple region variants share the same base name, prefer in this order.
# Lower index = higher priority.  Any region not listed gets priority len(_REGION_PRIORITY).
# Each entry is checked as a regex so multi-region tags like (USA, Europe) are matched.
_REGION_PRIORITY_RE: list[re.Pattern] = [
    re.compile(r"\(USA[,)]"),
    re.compile(r"\(Japan[,)]"),
    re.compile(r"\(Europe[,)]"),
    re.compile(r"\(World[,)]"),
]


def _region_priority(canonical: str) -> int:
    for i, pat in enumerate(_REGION_PRIORITY_RE):
        if pat.search(canonical):
            return i
    return len(_REGION_PRIORITY_RE)


def _tag_count(canonical: str) -> int:
    """Count parenthetical groups in canonical name.  Fewer = simpler = preferred."""
    return len(re.findall(r"\([^)]+\)", canonical))


def build_name_index(no_intro: dict[str, str]) -> dict[str, str]:
    """Build a normalized-base-name → canonical-name index from a No-Intro dict.

    Strips region/revision from each canonical name so patched ROMs whose
    header title matches the base game name can be looked up.
    Example: "Seiken Densetsu 3 (Japan)" → key "seiken_densetsu_3"

    When multiple region variants share the same base name the entry with the
    highest region priority is kept: USA > World > Europe > Japan > others.
    """
    index: dict[str, str] = {}
    priority: dict[str, int] = {}
    for canonical in no_intro.values():
        base = _BRACKET_RE.sub("", canonical)
        base = _REGION_RE.sub("", base)
        base = _REV_RE.sub("", base)
        base = _DISC_RE.sub("", base)
        base = _EXTRA_RE.sub("", base)
        base = base.lower()
        base = _NON_ALNUM_RE.sub("_", base)
        base = _MULTI_UNDERSCORE_RE.sub("_", base).strip("_")
        if not base:
            continue
        p = _region_priority(canonical)
        special = _has_special_tag(canonical)
        if base not in index:
            index[base] = canonical
            priority[base] = p
        else:
            existing_special = _has_special_tag(index[base])
            # Prefer non-special over special; within same special-ness prefer higher
            # region priority; break ties by fewest extra tags (simplest canonical).
            if existing_special and not special:
                index[base] = canonical
                priority[base] = p
            elif special == existing_special and p < priority[base]:
                index[base] = canonical
                priority[base] = p
            elif special == existing_special and p == priority[base]:
                if _tag_count(canonical) < _tag_count(index[base]):
                    index[base] = canonical
                    # priority unchanged
    return index


def _slug_roman_variants(slug: str) -> list[str]:
    """Return slug variants with roman↔arabic number substitutions on each segment.

    Example: "final_fantasy_v"  → ["final_fantasy_5"]
             "final_fantasy_5"  → ["final_fantasy_v"]
             "bahamut_lagoon"   → []   (no number segments)
    """
    parts = slug.split("_")
    variants = []
    for i, part in enumerate(parts):
        replacement = _ROMAN_TO_ARABIC.get(part) or _ARABIC_TO_ROMAN.get(part)
        if replacement:
            variants.append("_".join(parts[:i] + [replacement] + parts[i + 1 :]))
    return variants


def _prefix_match(slug: str, name_index: dict[str, str]) -> str | None:
    """Return the canonical name if exactly one No-Intro key starts with `slug + '_'`.

    Used when the ROM title is shorter than the No-Intro name, e.g.:
      "chaos_seed" uniquely prefixes "chaos_seed_fuusui_kairoki" → match.
    Returns None if zero or multiple entries match (ambiguous).
    """
    if len(slug) < 4:
        return None
    prefix = slug + "_"
    matches = [v for k, v in name_index.items() if k.startswith(prefix)]
    return matches[0] if len(matches) == 1 else None


def lookup_header_in_index(header_title: str, name_index: dict[str, str]) -> str | None:
    """Find the canonical No-Intro name matching a ROM header title string.

    Tries candidates in order of specificity:
      1. Exact normalized slug            ("bahamut_lagoon_eng_v31")
      2. Known patch/translation suffixes stripped ("bahamut_lagoon")
      3. Roman↔arabic variants of (1) and (2) ("final_fantasy_5" ↔ "final_fantasy_v")
      4. Progressive suffix removal for unknown translator tags ("clock_tower_sfx" →
         "clock_tower"). Requires at least two segments to remain.
      5. Forward prefix match: slug is a unique prefix of a No-Intro key
         ("chaos_seed" uniquely → "chaos_seed_fuusui_kairoki").

    Returns the first matching canonical name, or None.
    """
    slug = normalize_name(header_title)
    stripped = _PATCH_SUFFIX_RE.sub("", slug)
    src_special = _has_special_tag(header_title)

    def _allow(canonical: str) -> bool:
        """Reject match if canonical has special tags that source header does not."""
        return src_special or not _has_special_tag(canonical)

    candidates: list[str] = [slug]
    if stripped != slug:
        candidates.append(stripped)
    for base in (slug, stripped):
        candidates.extend(_matching_slug_variants(base))

    # Progressive suffix removal for unknown translator/patch tags
    parts = stripped.split("_")
    for n_remove in range(1, min(3, len(parts))):
        prefix = "_".join(parts[:-n_remove])
        if (
            "_" not in prefix
        ):  # require at least 2 word-segments to limit false positives
            break
        candidates.append(prefix)
        candidates.extend(_matching_slug_variants(prefix))

    seen: set[str] = set()
    for c in candidates:
        if c in seen or not c:
            continue
        seen.add(c)
        result = name_index.get(c)
        if result and _allow(result):
            return result
        result = _prefix_match(c, name_index)
        if result and _allow(result):
            return result

    # Forward prefix match: try each candidate as a prefix of No-Intro keys
    for c in (slug, stripped):
        result = _prefix_match(c, name_index)
        if result and _allow(result):
            return result

    return None


def fuzzy_filename_search(filename: str, name_index: dict[str, str]) -> str | None:
    """Find a No-Intro match by slug-matching the filename.

    Pre-processes the filename by truncating at the first '[' so that
    GoodTools / translation markers like "[T-En by ...]", "[n]", "[b]", "[h]"
    are excluded before slug generation.  No-Intro never uses '[' in canonical
    names, so this is always safe.

    Search strategy (ordered by confidence):
      1. Exact slug match
      2. Slug is a unique prefix of a No-Intro key  ("chaos_seed" → "chaos_seed_fuusui_kairoki")
      3. No-Intro key is a unique prefix of slug     (reverse direction)
      4. Known patch/translation suffixes stripped   ("_eng", "_v31", …)
      5. Roman ↔ arabic numeral variants             ("final_fantasy_v" ↔ "_5")
      6. Progressive suffix removal (up to 3 parts)  ("thunder_pro_wrestling_story"
                                                       → "thunder_pro_wrestling")

    Returns None if zero or multiple entries match (ambiguous).
    """
    # Truncate at first '[' — everything after is a GoodTools/hack/translation marker
    bracket_idx = filename.find("[")
    base = filename[:bracket_idx].strip() if bracket_idx >= 0 else filename

    slug = normalize_name(base if base else filename)
    src_special = _has_special_tag(filename)

    def _allow(canonical: str) -> bool:
        """Reject match if canonical has Beta/Proto/Demo/Sample tag that source lacks."""
        return src_special or not _has_special_tag(canonical)

    stripped = _PATCH_SUFFIX_RE.sub("", slug)

    candidates: list[str] = [slug]
    if stripped != slug:
        candidates.append(stripped)
    for base_slug in (slug, stripped):
        candidates.extend(_matching_slug_variants(base_slug))

    seen: set[str] = set()
    for c in candidates:
        if c in seen or not c:
            continue
        seen.add(c)
        result = name_index.get(c)
        if result and _allow(result):
            return result
        result = _prefix_match(c, name_index)
        if result and _allow(result):
            return result

    # Compact comparison: ignore underscore boundaries entirely so cases like
    # "dodgeball" vs "dodge_ball" still match. Only accept a unique hit.
    compact = _collapsed_slug(stripped if stripped != slug else slug)
    compact_matches = [
        v for k, v in name_index.items() if _collapsed_slug(k) == compact and _allow(v)
    ]
    if len(compact_matches) == 1:
        return compact_matches[0]

    # Prefix matches (forward and reverse) on the primary slug
    result = _prefix_match(slug, name_index)
    if result and _allow(result):
        return result

    reverse = [
        v
        for k, v in name_index.items()
        if slug.startswith(k + "_") and "_" in k and _allow(v)
    ]
    if len(reverse) == 1:
        return reverse[0]

    # Progressive suffix removal — handles translated titles where the translated
    # word differs ("_story" → drop → "thunder_pro_wrestling" → prefix match)
    parts = (stripped if stripped != slug else slug).split("_")
    for n_remove in range(1, min(3, len(parts))):
        prefix = "_".join(parts[:-n_remove])
        if (
            "_" not in prefix
        ):  # require at least 2 word-segments to limit false positives
            break
        if prefix in seen:
            continue
        seen.add(prefix)
        result = name_index.get(prefix)
        if result and _allow(result):
            return result
        result = _prefix_match(prefix, name_index)
        if result and _allow(result):
            return result
        for variant in _matching_slug_variants(prefix):
            if variant in seen:
                continue
            seen.add(variant)
            result = name_index.get(variant)
            if result and _allow(result):
                return result
            result = _prefix_match(variant, name_index)
            if result and _allow(result):
                return result

    # Trailing word match — handles romanization differences where the first
    # word(s) of the ROM name differ from the No-Intro key but the remaining
    # words uniquely identify the game.
    # Example: "tougiou_king_colossus" → tail "king_colossus"
    #          uniquely matches "tougi_ou_king_colossus" (endswith _king_colossus)
    slug_parts = slug.split("_")
    for n_keep in range(min(len(slug_parts) - 1, 3), 1, -1):
        tail = "_".join(slug_parts[-n_keep:])
        if len(tail) < 5:  # require enough characters to avoid accidental matches
            continue
        tail_matches = [
            v for k, v in name_index.items() if k.endswith("_" + tail) and _allow(v)
        ]
        if len(tail_matches) == 1:
            return tail_matches[0]

    return None


def _parse_sfo_title(sfo_data: bytes) -> str | None:
    """Extract the TITLE string from a PARAM.SFO binary blob (PSP/PS3 format)."""
    import struct

    if len(sfo_data) < 20 or sfo_data[:4] != b"\x00PSF":
        return None
    try:
        key_table_off = struct.unpack_from("<I", sfo_data, 8)[0]
        val_table_off = struct.unpack_from("<I", sfo_data, 12)[0]
        num_entries = struct.unpack_from("<I", sfo_data, 16)[0]
        for i in range(min(num_entries, 64)):
            entry = 20 + i * 16
            if entry + 16 > len(sfo_data):
                break
            key_off = struct.unpack_from("<H", sfo_data, entry)[0]
            dtype = sfo_data[entry + 3]  # 2 = UTF8 null-terminated
            data_len = struct.unpack_from("<I", sfo_data, entry + 4)[0]
            val_off = struct.unpack_from("<I", sfo_data, entry + 12)[0]
            key_start = key_table_off + key_off
            key_end = sfo_data.find(b"\x00", key_start)
            if key_end < 0:
                continue
            key = sfo_data[key_start:key_end].decode("ascii", errors="ignore")
            if key == "TITLE" and dtype == 2:
                val_start = val_table_off + val_off
                raw = sfo_data[val_start : val_start + data_len]
                title = raw.rstrip(b"\x00").decode("utf-8", errors="replace").strip()
                return title if title else None
    except Exception:
        return None
    return None


def read_rom_header_title(path: Path, system: str) -> str | None:
    """Read the internal game title from a ROM header.

    Supported: GBA, SNES, MD (Mega Drive/Genesis), N64, GB, GBC, PSP, PS3.
    Returns None for unsupported systems, unreadable files, or empty headers.
    """
    system = system.upper()

    # PSP/PS3 ISO images may have PARAM.SFO anywhere in the first several hundred KB;
    # read 512 KB so we can find it via magic search. Cartridge systems need only ~66 KB.
    read_size = 0x80000 if system in ("PSP", "PS3") else 0x10200

    try:
        file_size = path.stat().st_size
        with open(path, "rb") as f:
            data = f.read(read_size)
    except Exception:
        return None

    title_bytes: bytes | None = None

    if system == "GBA":
        # ROM header: 0x00A0, 12 bytes, ASCII
        if len(data) >= 0x00AC:
            title_bytes = data[0x00A0:0x00AC]

    elif system in ("MD", "GEN"):
        # Domestic title: 0x0120, 48 bytes, ASCII (Shift-JIS for JP titles)
        if len(data) >= 0x0150:
            title_bytes = data[0x0120:0x0150]

    elif system == "N64":
        # Game name: 0x0020, 20 bytes, ASCII (in big-endian / z64 byte order).
        # If the ROM is in .v64 or .n64 byte order, convert to z64 first.
        if len(data) >= 0x0034:
            order = detect_n64_byte_order(data[:4])
            if order and order != "z64":
                # Only need to convert enough for the header (first 0x40 bytes)
                data = n64_to_z64(data[: max(0x40, 0x0034)], order)
            title_bytes = data[0x0020:0x0034]

    elif system in ("GB", "GBC"):
        # Title: 0x0134, 16 bytes (11 for GBC with manufacturer code, but 16 is safe)
        if len(data) >= 0x0144:
            title_bytes = data[0x0134:0x0144]

    elif system == "SNES":
        # SMC dump may have a 512-byte copier header — detect via actual file size,
        # not the read buffer size (which would always be 0x10200 % 1024 == 512).
        offset = 512 if file_size % 1024 == 512 else 0
        data = data[offset:]
        # Try LoROM (0x7FC0) then HiROM (0xFFC0), pick whichever looks more like text
        candidates = []
        for addr in (0x7FC0, 0xFFC0):
            if len(data) >= addr + 21:
                chunk = data[addr : addr + 21]
                printable = sum(1 for b in chunk if 0x20 <= b <= 0x7E)
                candidates.append((printable, chunk))
        if candidates:
            title_bytes = max(candidates, key=lambda x: x[0])[1]

    elif system == "SAT":
        # Saturn IP.BIN header: "SEGA SEGASATURN" at sector start, product name at +0x60 (32 bytes).
        # ISO (2048 B/sector): sector 0 starts at file offset 0x000.
        # Raw BIN (2352 B/sector): sector 0 data starts at file offset 0x010 (after 16-byte sync header).
        sat_magic = b"SEGA SEGASATURN "
        sector_offsets = [0x000, 0x010]  # ISO, then raw BIN
        for sec_off in sector_offsets:
            if (
                len(data) >= sec_off + 0x60 + 32
                and data[sec_off : sec_off + 16] == sat_magic
            ):
                title_bytes = data[sec_off + 0x60 : sec_off + 0x80]
                break

    elif system in ("PSP", "PS3"):
        ext = path.suffix.lower()
        if ext == ".pbp":
            # PBP header: magic \x00PBP at offset 0, PARAM.SFO offset at bytes 8–11
            import struct

            if len(data) >= 12 and data[:4] == b"\x00PBP":
                sfo_off = struct.unpack_from("<I", data, 8)[0]
                if sfo_off < len(data):
                    return _parse_sfo_title(data[sfo_off:])
        elif ext == ".iso":
            # Search for PARAM.SFO magic anywhere in the data window
            pos = data.find(b"\x00PSF")
            if pos >= 0:
                return _parse_sfo_title(data[pos:])
        # CSO (compressed ISO) and PKG: PARAM.SFO is not directly accessible
        return None

    if title_bytes is None:
        return None

    # Decode: keep printable ASCII only
    title = title_bytes.decode("ascii", errors="ignore")
    title = re.sub(r"[^\x20-\x7E]", " ", title).strip()
    title = re.sub(r"\s+", " ", title).strip()
    return title if len(title) >= 2 else None


_REGION_TAG_RE = re.compile(
    r"\((USA|Europe|Japan|World|Australia|Brazil|Korea|China)\)",
    re.IGNORECASE,
)
_PAREN_GROUP_RE = re.compile(r"\(([^)]+)\)")
_FULL_REGION_MAP: dict[str, str] = {
    "usa": "USA",
    "europe": "Europe",
    "japan": "Japan",
    "world": "World",
    "australia": "Australia",
    "brazil": "Brazil",
    "korea": "Korea",
    "china": "China",
    "france": "France",
    "germany": "Germany",
    "spain": "Spain",
    "italy": "Italy",
    "netherlands": "Netherlands",
    "sweden": "Sweden",
    "denmark": "Denmark",
    "norway": "Norway",
    "finland": "Finland",
    "asia": "Asia",
}
_REGION_PREFERENCE: tuple[str, ...] = (
    "USA",
    "World",
    "Japan",
    "Europe",
    "Australia",
    "Brazil",
    "Korea",
    "China",
    "France",
    "Germany",
    "Spain",
    "Italy",
    "Netherlands",
    "Sweden",
    "Denmark",
    "Norway",
    "Finland",
    "Asia",
)

# Old-style single/double letter region codes used before No-Intro standardisation.
# Multi-char codes like (UE) or (JU) are read left-to-right; first recognised letter wins.
_SHORT_REGION_RE = re.compile(r"\(([A-Z]{1,4})\)", re.IGNORECASE)
_SHORT_REGION_MAP: dict[str, str] = {
    "U": "USA",
    "E": "Europe",
    "J": "Japan",
    "W": "World",
    "F": "France",
    "G": "Germany",
    "S": "Spain",
    "I": "Italy",
    "K": "Korea",
    "C": "China",
    "B": "Brazil",
    "A": "Australia",
}


def extract_region_hint(filename: str) -> str | None:
    """Return a normalised region name from a filename.

    Understands both No-Intro style  ``(USA)`` / ``(Europe)``
    and old GoodTools/TOSEC style    ``(U)`` / ``(E)`` / ``(J)`` / ``(UE)``.
    """
    hints = extract_region_hints(filename)
    if not hints:
        return None

    for preferred in _REGION_PREFERENCE:
        if preferred in hints:
            return preferred
    return hints[0]


def extract_region_hints(filename: str) -> list[str]:
    """Return all normalised region names found in a filename.

    Supports both No-Intro multi-region groups like ``(Japan, USA)`` and
    short GoodTools/TOSEC codes like ``(UE)``.
    """
    hints: list[str] = []
    seen: set[str] = set()

    for match in _PAREN_GROUP_RE.finditer(filename):
        group = match.group(1).strip()
        parts = [part.strip() for part in group.split(",")]

        matched_full = False
        for part in parts:
            region = _FULL_REGION_MAP.get(part.lower())
            if region and region not in seen:
                seen.add(region)
                hints.append(region)
                matched_full = True

        if matched_full:
            continue

        short_match = _SHORT_REGION_RE.fullmatch(f"({group})")
        if not short_match:
            continue

        for ch in short_match.group(1).upper():
            region = _SHORT_REGION_MAP.get(ch)
            if region and region not in seen:
                seen.add(region)
                hints.append(region)

    return hints


def find_region_preferred(
    canonical: str, no_intro: dict[str, str], region_hint: str
) -> str:
    """Return a same-base No-Intro entry that matches region_hint.

    If the hinted region has no entry, falls back in priority order:
    USA → World → (canonical as-is).

    Used to correct cases where the name index returns e.g. 'Final Fight 2 (Europe)'
    when the source ROM filename clearly indicates '(USA)'.
    """
    canonical_regions = extract_region_hints(canonical)
    if region_hint in canonical_regions:
        return canonical  # already the right region
    base = normalize_name(canonical)

    same_base = [c for c in no_intro.values() if normalize_name(c) == base]
    if not same_base:
        return canonical

    fallback_order = [region_hint, "USA", "Japan", "Europe", "World"]

    def _score(candidate: str) -> tuple[int, int, int, int, str]:
        regions = extract_region_hints(candidate)
        best_rank = len(fallback_order)
        for i, preferred in enumerate(fallback_order):
            if preferred in regions:
                best_rank = i
                break
        exact_match = 0 if regions == [region_hint] else 1
        return (best_rank, exact_match, len(regions), _tag_count(candidate), candidate)

    best = min(same_base, key=_score)
    if _score(best)[0] < len(fallback_order):
        return best

    return canonical


def find_companion_files(rom_path: Path, new_stem: str) -> list[tuple[Path, Path]]:
    """Return companion files that should be renamed alongside the given ROM.

    Handles:
    - SNES MSU-1: ``{stem}.msu`` + ``{stem}-N.pcm`` audio tracks
    - CUE/BIN:    ``{stem}.cue`` sheet alongside a ``{stem}.bin`` ROM

    Returns a list of (old_path, new_path) pairs (only existing files).
    """
    companions: list[tuple[Path, Path]] = []
    ext = rom_path.suffix.lower()
    stem = rom_path.stem
    parent = rom_path.parent

    if ext in (".sfc", ".smc"):
        # MSU-1 manifest
        msu = parent / (stem + ".msu")
        if msu.exists():
            companions.append((msu, parent / (new_stem + ".msu")))
        # PCM audio tracks: stem-1.pcm, stem-2.pcm, …
        try:
            for f in sorted(parent.iterdir()):
                m = _MSU_TRACK_RE.match(f.name)
                if m and m.group(1) == stem:
                    companions.append((f, parent / f"{new_stem}-{m.group(2)}.pcm"))
        except OSError:
            pass

    elif ext == ".bin":
        # CUE sheet that references this .bin
        cue = parent / (stem + ".cue")
        if cue.exists():
            companions.append((cue, parent / (new_stem + ".cue")))

    return companions


def patch_cue_references(cue_path: Path, old_stem: str, new_stem: str) -> None:
    """Update FILE references inside a .cue sheet after the bin files are renamed.

    Replaces ``FILE "OldStem.ext"`` (or without quotes) with ``FILE "NewStem.ext"``.
    """
    try:
        text = cue_path.read_text(encoding="utf-8", errors="replace")
        escaped = re.escape(old_stem)
        patched = re.sub(
            r'(?i)(FILE\s+"?)' + escaped + r'(\.[^"\s]+)',
            lambda m: m.group(1) + new_stem + m.group(2),
            text,
        )
        if patched != text:
            cue_path.write_text(patched, encoding="utf-8")
    except Exception:
        pass


def find_roms(folder: Path) -> list[Path]:
    """Return all ROM files in folder and subfolders (recursive, by extension)."""
    return sorted(
        f
        for f in folder.rglob("*")
        if f.is_file() and f.suffix.lower() in ROM_EXTENSIONS
    )


def plan_renames(
    folder: Path,
    no_intro: dict[str, str],
    use_crc: bool,
) -> list[tuple[Path, Path]]:
    """Return list of (old_path, new_path) pairs for files that need renaming.

    Includes matching save files alongside each ROM.
    """
    roms = find_roms(folder)
    if not roms:
        return []

    renames: list[tuple[Path, Path]] = []

    for rom in roms:
        ext = rom.suffix.lower()

        if use_crc and no_intro:
            crc = _crc32_file(rom)
            canonical_name = no_intro.get(crc)
            if canonical_name:
                new_stem = normalize_name(canonical_name)
            else:
                new_stem = normalize_name(rom.name)
        else:
            new_stem = normalize_name(rom.name)

        new_rom = folder / (new_stem + ext)
        if new_rom != rom:
            renames.append((rom, new_rom))

        # Rename matching save files (same stem, save extension)
        for save_ext in SAVE_EXTENSIONS:
            save_file = folder / (rom.stem + save_ext)
            if save_file.exists():
                new_save = folder / (new_stem + save_ext)
                if new_save != save_file:
                    renames.append((save_file, new_save))

    return renames


def preview(renames: list[tuple[Path, Path]]) -> None:
    if not renames:
        print("No renames needed — all files already normalized.")
        return
    print(f"{'CURRENT':<60}  {'NEW NAME'}")
    print("-" * 120)
    for old, new in renames:
        print(f"{old.name:<60}  {new.name}")
    print(f"\n{len(renames)} file(s) would be renamed. Use --apply to proceed.")


def apply_renames(renames: list[tuple[Path, Path]]) -> None:
    for old, new in renames:
        if new.exists() and new != old:
            print(f"SKIP (target exists): {old.name} -> {new.name}")
            continue
        old.rename(new)
        print(f"Renamed: {old.name} -> {new.name}")
    print(f"\n{len(renames)} file(s) renamed.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize ROM (and save) filenames")
    parser.add_argument(
        "folder", type=Path, help="Folder containing ROMs (searched recursively)"
    )
    parser.add_argument(
        "--system",
        default=None,
        help="System code (e.g. SNES, GBA, NES) — auto-discovers matching DAT from dats/ folder",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply renames (default: preview only)",
    )
    parser.add_argument(
        "--dat",
        type=Path,
        default=None,
        help="No-Intro XML DAT file for canonical names (overrides --system DAT discovery)",
    )
    parser.add_argument(
        "--no-crc",
        action="store_true",
        help="Skip CRC32 lookup, use filename normalization only",
    )
    args = parser.parse_args()

    if not args.folder.exists():
        print(f"ERROR: Folder not found: {args.folder}")
        sys.exit(1)

    no_intro: dict[str, str] = {}
    dat_path = args.dat

    # Auto-discover DAT from dats/ folder if --system given and --dat not explicit
    if dat_path is None and args.system and not args.no_crc:
        dat_path = find_dat_for_system(args.system)
        if dat_path:
            print(f"Found DAT for {args.system.upper()}: {dat_path.name}")
        else:
            print(f"No DAT found for system '{args.system}' in {DATS_DIR}")
            print("  Falling back to filename normalization only.")

    if dat_path:
        print(f"Loading No-Intro DAT: {dat_path.name}")
        no_intro = load_no_intro_dat(dat_path)
        print(f"  Loaded {len(no_intro):,} entries")

    use_crc = bool(no_intro) and not args.no_crc

    print(f"Scanning: {args.folder}")
    renames = plan_renames(args.folder, no_intro, use_crc)

    if args.apply:
        apply_renames(renames)
    else:
        preview(renames)


if __name__ == "__main__":
    main()
