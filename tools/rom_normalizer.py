#!/usr/bin/env python3
"""ROM Normalizer — renames ROMs (and their matching saves) to a canonical form.

Usage:
    python rom_normalizer.py <folder> [--system SNES] [--apply] [--dat file.dat]

By default shows a preview of proposed renames. Use --apply to rename files.

Normalization rules (same as the server's rom_id.py):
  - Strip extension
  - Strip region tags: (USA), (Europe), (Japan), etc.
  - Strip revision tags: (Rev 1), (v1.1), (Beta), (Demo), etc.
  - Strip disc tags: (Disc 1), (Disk 2), etc.
  - Strip any remaining parenthetical tags
  - Lowercase, replace non-alphanumeric with underscores

Save files (.sav, .srm, .mcr, .frz) with the same stem as the ROM are renamed alongside it.

Optional: if a No-Intro XML DAT file is provided (--dat), canonical No-Intro names are
used instead of the automatic normalization, giving exact cross-device consistency.
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
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_MULTI_UNDERSCORE_RE = re.compile(r"_+")

SAVE_EXTENSIONS = {".sav", ".srm", ".mcr", ".frz", ".fs", ".rtc"}
ROM_EXTENSIONS = {
    ".gba", ".gb", ".gbc", ".sfc", ".smc", ".nes", ".md", ".gen",
    ".n64", ".z64", ".v64", ".gg", ".sms", ".pce", ".ngp", ".ngc",
    ".ws", ".wsc", ".lnx", ".nds", ".a26", ".a78", ".rom", ".bin", ".iso",
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


def find_roms(folder: Path) -> list[Path]:
    """Return all ROM files in folder (non-recursive, by extension)."""
    return sorted(
        f for f in folder.iterdir()
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
    parser.add_argument("folder", type=Path, help="Folder containing ROMs")
    parser.add_argument(
        "--apply", action="store_true",
        help="Apply renames (default: preview only)",
    )
    parser.add_argument(
        "--dat", type=Path, default=None,
        help="No-Intro XML DAT file for canonical names (optional)",
    )
    parser.add_argument(
        "--no-crc", action="store_true",
        help="Skip CRC32 lookup even if --dat is provided (use filename normalization only)",
    )
    args = parser.parse_args()

    if not args.folder.exists():
        print(f"ERROR: Folder not found: {args.folder}")
        sys.exit(1)

    no_intro: dict[str, str] = {}
    if args.dat:
        print(f"Loading No-Intro DAT: {args.dat}")
        no_intro = load_no_intro_dat(args.dat)
        print(f"  Loaded {len(no_intro)} entries")

    use_crc = bool(no_intro) and not args.no_crc

    print(f"Scanning: {args.folder}")
    renames = plan_renames(args.folder, no_intro, use_crc)

    if args.apply:
        apply_renames(renames)
    else:
        preview(renames)


if __name__ == "__main__":
    main()
