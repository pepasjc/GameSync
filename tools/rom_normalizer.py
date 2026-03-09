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
import re
import sys
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

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
_BRACKET_RE = re.compile(r"\s*\[[^\]]*\]")   # strip [T+Eng], [Hack], etc.
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_MULTI_UNDERSCORE_RE = re.compile(r"_+")

# Trailing patch/translation metadata to strip when matching header titles.
# Handles patterns like: _eng_v31, _ger, _v1_0, _r2, _rev2, _patch, _hack, etc.
_MSU_TRACK_RE = re.compile(r"^(.+)-(\d+)\.pcm$", re.IGNORECASE)

_PATCH_SUFFIX_RE = re.compile(
    r"(?:_(?:eng|ger|jpn|fre|fra|spa|por|ita|pol|rus|chi|kor|dut|swe|nor|dan|fin|"
    r"v\d[\d_a-z]*|r\d+|rev\d+|patch\d*|translation|fix\d*|hack))+$",
    re.IGNORECASE,
)

# Roman numeral ↔ arabic mapping (1–15 covers virtually all game sequels)
_ROMAN_TO_ARABIC: dict[str, str] = {
    "i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5",
    "vi": "6", "vii": "7", "viii": "8", "ix": "9", "x": "10",
    "xi": "11", "xii": "12", "xiii": "13", "xiv": "14", "xv": "15",
}
_ARABIC_TO_ROMAN: dict[str, str] = {v: k for k, v in _ROMAN_TO_ARABIC.items()}

SAVE_EXTENSIONS = {".sav", ".srm", ".mcr", ".frz", ".fs", ".rtc"}
# Companion file extensions handled by find_companion_files (not scanned as ROMs)
COMPANION_EXTENSIONS = {".msu", ".pcm", ".cue"}
ROM_EXTENSIONS = {
    ".gba", ".gb", ".gbc", ".sfc", ".smc", ".nes", ".md", ".gen",
    ".n64", ".z64", ".v64", ".gg", ".sms", ".pce", ".ngp", ".ngc",
    ".ws", ".wsc", ".lnx", ".nds", ".a26", ".a78", ".rom", ".bin",
    ".iso", ".cso", ".pbp", ".pkg",   # PSP / PS3
    ".mdf",                            # Saturn / Alcohol 120%
}


def normalize_name(filename: str) -> str:
    """Strip extension, tags; return lowercase slug."""
    name = filename
    # Strip extension(s)
    for _ in range(3):
        dot_idx = name.rfind(".")
        if dot_idx <= 0:
            break
        suffix = name[dot_idx + 1:]
        if 1 <= len(suffix) <= 5 and suffix.isalnum():
            name = name[:dot_idx]
        else:
            break

    name = _BRACKET_RE.sub("", name)   # strip [T+Eng], [Hack], [!] etc.
    name = _REGION_RE.sub("", name)
    name = _REV_RE.sub("", name)
    name = _DISC_RE.sub("", name)
    name = _EXTRA_RE.sub("", name)
    name = name.lower()
    name = _NON_ALNUM_RE.sub("_", name)
    name = _MULTI_UNDERSCORE_RE.sub("_", name).strip("_")
    return name or "unknown"


def _crc32_file(path: Path) -> str:
    """Compute CRC32 of file contents, returned as 8-char uppercase hex."""
    crc = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            crc = zlib.crc32(chunk, crc)
    return f"{crc & 0xFFFFFFFF:08X}"


def load_no_intro_dat(dat_path: Path) -> dict[str, str]:
    """Parse a No-Intro XML DAT file.
    Returns a dict mapping CRC32 (uppercase 8-char hex) -> canonical name.
    """
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


# Maps --system code to keywords to search for in DAT filenames
_SYSTEM_DAT_KEYWORDS: dict[str, list[str]] = {
    "SNES":      ["Super Nintendo"],
    "NES":       ["Nintendo Entertainment System"],
    "GBA":       ["Game Boy Advance"],
    "GBC":       ["Game Boy Color"],
    "GB":        ["Game Boy"],
    "N64":       ["Nintendo 64"],
    "MD":        ["Mega Drive", "Genesis"],
    "GG":        ["Game Gear"],
    "SMS":       ["Master System"],
    "PCE":       ["PC Engine", "TurboGrafx"],
    "NGP":       ["Neo Geo Pocket"],
    "LYNX":      ["Lynx"],
    "WSWAN":     ["WonderSwan"],
    "SAT":       ["Sega - Saturn", "Saturn"],
    "PS1":       ["Sony - PlayStation"],
    "PS2":       ["Sony - PlayStation 2"],
    "PSP":       ["Sony - PlayStation Portable"],
    "PS3":       ["Sony - PlayStation 3"],
    "DC":        ["Dreamcast"],
    "GC":        ["GameCube", "Gamecube"],
    "NDS":       ["Nintendo DS"],
    "ATARI2600": ["Atari 2600"],
    "ATARI7800": ["Atari 7800"],
}

DATS_DIR = Path(__file__).parent / "dats"


def find_dat_for_system(system: str) -> Path | None:
    """Search the dats/ folder for a DAT matching the given system code."""
    if not DATS_DIR.exists():
        return None
    keywords = _SYSTEM_DAT_KEYWORDS.get(system.upper(), [system])
    for dat_file in sorted(DATS_DIR.glob("*.dat")):
        for kw in keywords:
            if kw.lower() in dat_file.name.lower():
                return dat_file
    return None


# When multiple region variants share the same base name, prefer in this order.
# Lower index = higher priority.  Any region not listed gets priority len(_REGION_PRIORITY).
_REGION_PRIORITY: list[str] = ["(USA)", "(Japan)", "(Europe)", "(World)"]


def _region_priority(canonical: str) -> int:
    for i, tag in enumerate(_REGION_PRIORITY):
        if tag in canonical:
            return i
    return len(_REGION_PRIORITY)


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
        if base not in index or p < priority[base]:
            index[base] = canonical
            priority[base] = p
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
            variants.append("_".join(parts[:i] + [replacement] + parts[i + 1:]))
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

    candidates: list[str] = [slug]
    if stripped != slug:
        candidates.append(stripped)
    for base in (slug, stripped):
        candidates.extend(_slug_roman_variants(base))

    # Progressive suffix removal for unknown translator/patch tags
    parts = stripped.split("_")
    for n_remove in range(1, min(3, len(parts))):
        prefix = "_".join(parts[:-n_remove])
        if "_" not in prefix:   # require at least 2 word-segments to limit false positives
            break
        candidates.append(prefix)
        candidates.extend(_slug_roman_variants(prefix))

    seen: set[str] = set()
    for c in candidates:
        if c in seen or not c:
            continue
        seen.add(c)
        result = name_index.get(c)
        if result:
            return result

    # Forward prefix match: try each candidate as a prefix of No-Intro keys
    for c in (slug, stripped):
        result = _prefix_match(c, name_index)
        if result:
            return result

    return None


def fuzzy_filename_search(filename: str, name_index: dict[str, str]) -> str | None:
    """Find a unique No-Intro match by prefix-matching the filename slug.

    For translated ROMs whose filename matches the English name but the No-Intro
    entry has an additional Japanese subtitle:
      "Chaos Seed.sfc" → "chaos_seed" → uniquely prefixes "chaos_seed_fuusui_kairoki"
      → returns "Chaos Seed - Fuusui Kairoki (Japan)"

    Also handles the reverse — when the No-Intro key is a prefix of the slug —
    as a safety net for cases missed by lookup_header_in_index.

    Returns None if zero or multiple entries match (ambiguous).
    """
    slug = normalize_name(filename)

    # Try exact match first
    if slug in name_index:
        return name_index[slug]

    # Slug is a unique prefix of a No-Intro key
    result = _prefix_match(slug, name_index)
    if result:
        return result

    # No-Intro key is a unique prefix of slug (reverse direction)
    reverse = [v for k, v in name_index.items() if slug.startswith(k + "_") and "_" in k]
    if len(reverse) == 1:
        return reverse[0]

    return None


def _parse_sfo_title(sfo_data: bytes) -> str | None:
    """Extract the TITLE string from a PARAM.SFO binary blob (PSP/PS3 format)."""
    import struct
    if len(sfo_data) < 20 or sfo_data[:4] != b"\x00PSF":
        return None
    try:
        key_table_off = struct.unpack_from("<I", sfo_data, 8)[0]
        val_table_off = struct.unpack_from("<I", sfo_data, 12)[0]
        num_entries   = struct.unpack_from("<I", sfo_data, 16)[0]
        for i in range(min(num_entries, 64)):
            entry = 20 + i * 16
            if entry + 16 > len(sfo_data):
                break
            key_off  = struct.unpack_from("<H",  sfo_data, entry)[0]
            dtype    = sfo_data[entry + 3]          # 2 = UTF8 null-terminated
            data_len = struct.unpack_from("<I",  sfo_data, entry + 4)[0]
            val_off  = struct.unpack_from("<I",  sfo_data, entry + 12)[0]
            key_start = key_table_off + key_off
            key_end   = sfo_data.find(b"\x00", key_start)
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
        # Game name: 0x0020, 20 bytes, ASCII
        if len(data) >= 0x0034:
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
                chunk = data[addr:addr + 21]
                printable = sum(1 for b in chunk if 0x20 <= b <= 0x7E)
                candidates.append((printable, chunk))
        if candidates:
            title_bytes = max(candidates, key=lambda x: x[0])[1]

    elif system == "SAT":
        # Saturn IP.BIN header: "SEGA SEGASATURN" at sector start, product name at +0x60 (32 bytes).
        # ISO (2048 B/sector): sector 0 starts at file offset 0x000.
        # Raw BIN (2352 B/sector): sector 0 data starts at file offset 0x010 (after 16-byte sync header).
        sat_magic = b"SEGA SEGASATURN "
        sector_offsets = [0x000, 0x010]   # ISO, then raw BIN
        for sec_off in sector_offsets:
            if len(data) >= sec_off + 0x60 + 32 and data[sec_off:sec_off + 16] == sat_magic:
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
    r"\((USA|Europe|Japan|World|Australia|Brazil|Korea|China|"
    r"En|Ja|Fr|De|Es|It|Nl|Pt|Sv|No|Da|Fi|Ko|Zh)\)",
    re.IGNORECASE,
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
    # Try full No-Intro style first
    m = _REGION_TAG_RE.search(filename)
    if m:
        return m.group(1)

    # Fall back to short-code style: scan left-to-right for first recognised letter
    for m in _SHORT_REGION_RE.finditer(filename):
        for ch in m.group(1).upper():
            region = _SHORT_REGION_MAP.get(ch)
            if region:
                return region

    return None


def find_region_preferred(canonical: str, no_intro: dict[str, str], region_hint: str) -> str:
    """Return a same-base No-Intro entry that matches region_hint.

    If the hinted region has no entry, falls back in priority order:
    USA → World → (canonical as-is).

    Used to correct cases where the name index returns e.g. 'Final Fight 2 (Europe)'
    when the source ROM filename clearly indicates '(USA)'.
    """
    if f"({region_hint})" in canonical:
        return canonical  # already the right region
    base = normalize_name(canonical)

    # Try the hinted region first, then fall through priority list
    for tag in [f"({region_hint})", "(USA)", "(Japan)", "(Europe)"]:
        for c in no_intro.values():
            if tag in c and normalize_name(c) == base:
                return c

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
        f for f in folder.rglob("*")
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
    parser.add_argument("folder", type=Path, help="Folder containing ROMs (searched recursively)")
    parser.add_argument(
        "--system", default=None,
        help="System code (e.g. SNES, GBA, NES) — auto-discovers matching DAT from dats/ folder",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Apply renames (default: preview only)",
    )
    parser.add_argument(
        "--dat", type=Path, default=None,
        help="No-Intro XML DAT file for canonical names (overrides --system DAT discovery)",
    )
    parser.add_argument(
        "--no-crc", action="store_true",
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
