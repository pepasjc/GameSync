#!/usr/bin/env python3
"""Scan EN-Dat translation files and find entries that cannot be auto-matched
to a standard No-Intro entry via slug comparison.

These are ROMs whose title was renamed during translation (e.g.
"Ganbare Goemon 2 - Kiteretsu Shougun McGuiness (Japan)" was renamed to
"Go for it! Goemon 2 - The Strange General McGuinness (Japan)"), so their
normalized slug differs from any entry in the standard No-Intro DAT.

Generates (or updates) server/data/dats/EN-Dats/aliases.json, where each key
is the translation's clean name (brackets stripped) and the value is the
canonical No-Intro name from the standard DAT whose save slot it should share.

Filled entries are ALWAYS preserved across runs — never lost.
Empty entries represent games still awaiting a manual lookup.
Unlicensed ROMs (Unl) and documentation entries are skipped automatically.

Usage:
    cd desktop
    python find_unmatched_translations.py           # scan all systems
    python find_unmatched_translations.py --hints   # show suggested matches
    python find_unmatched_translations.py --system SNES  # one system only
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
DATS_DIR = SCRIPT_DIR.parent / "server" / "data" / "dats"
EN_DATS_DIR = DATS_DIR / "EN-Dats"
ALIASES_PATH = EN_DATS_DIR / "aliases.json"

sys.path.insert(0, str(SCRIPT_DIR))
import rom_normalizer as rn

# ---------------------------------------------------------------------------
# File extension filter — skip documentation/patch-info entries (.txt, .nfo)
# ---------------------------------------------------------------------------
_ROM_EXTS = frozenset({
    ".sfc", ".smc", ".nes", ".fds",
    ".gb", ".gbc", ".gba",
    ".nds", ".3ds", ".cia",
    ".n64", ".z64", ".v64", ".ndd",
    ".md", ".gen", ".32x",
    ".sms", ".gg",
    ".pce", ".sgx",
    ".vb", ".ws", ".wsc",
    ".ngp", ".ngc",
    ".bin", ".cue", ".chd", ".iso", ".cso", ".pbp", ".mdf",
    ".lnx", ".jag", ".a26", ".a78",
    ".rom", ".img",
})

# ---------------------------------------------------------------------------
# EN-Dat filename -> system code
# Most-specific phrases must appear before less-specific ones.
# None means the system is not in our SYSTEM_CODES; those EN-Dats are skipped.
# ---------------------------------------------------------------------------
_EN_DAT_SYSTEM: list[tuple[str, str | None]] = [
    ("WonderSwan Color",             "WSWANC"),
    ("WonderSwan",                   "WSWAN"),
    ("Super Famicom",                "SNES"),
    ("Family Computer Disk System",  "FDS"),
    ("Famicom Disk System",          "FDS"),
    ("Famicom",                      "NES"),
    ("Game Boy Color",               "GBC"),
    ("Game Boy Advance",             "GBA"),
    ("Game Boy",                     "GB"),
    ("Nintendo 3DS",                 "3DS"),
    ("Nintendo 64DD",                "N64DD"),
    ("Nintendo DSi",                 "NDS"),   # DSi saves share the NDS format
    ("Nintendo DS",                  "NDS"),
    ("Nintendo 64",                  "N64"),
    ("GameCube",                     "GC"),
    ("Wii",                          "WII"),
    ("Virtual Boy",                  "VB"),
    ("Pokemon Mini",                 "POKEMINI"),
    ("PC Engine CD",                 "PCECD"),
    ("PC Engine SuperGrafx",         "PCSG"),
    ("PC Engine",                    "PCE"),
    ("PC-FX",                        "PCFX"),
    ("Mega CD",                      "SEGACD"),
    ("Mega Drive",                   "MD"),
    ("Game Gear",                    "GG"),
    ("Master System",                "SMS"),
    ("Saturn",                       "SAT"),
    ("Dreamcast",                    "DC"),
    ("SG-1000",                      None),
    ("Neo Geo CD",                   "NEOCD"),
    ("Neo Geo Pocket Color",         "NGPC"),
    ("Neo Geo Pocket",               "NGP"),
    ("PlayStation 2",                "PS2"),
    ("PlayStation 3",                "PS3"),
    ("PlayStation Portable",         "PSP"),
    ("PlayStation",                  "PS1"),   # after PS2/PS3/PSP
    ("3DO",                          "3DO"),
    ("X68000",                       "X68K"),
    ("X1",                           "X1"),
    ("MSX2",                         None),
    ("MSX",                          None),
    ("FM-Towns",                     None),
    ("PC-6001",                      None),
    ("PC-8001",                      None),
    ("PC-8801",                      None),
    ("PC-9801",                      None),
    ("N-Gage",                       None),
    ("J2ME",                         None),
    ("Zeebo",                        None),
    ("XBOX 360",                     None),
    ("XBOX",                         None),
]

_BRACKETS_RE = re.compile(r"\s*\[[^\]]*\]")


def _system_code(dat_stem: str) -> str | None:
    """Return the system code for an EN-Dat file, or None to skip it."""
    stem_lower = dat_stem.lower()
    for keyword, code in _EN_DAT_SYSTEM:
        if keyword.lower() in stem_lower:
            return code  # None means "explicitly unsupported"
    return None  # unknown -> skip


def _clean_machine_name(name: str) -> str | None:
    """Strip [...] brackets from an EN-Dat machine name.

    Returns None for entries that should be skipped:
      - Collection headers (names starting with '_')
      - BIOS entries
      - Unlicensed bootlegs '(Unl)' — no standard DAT entry will ever exist
    """
    if name.startswith("_"):
        return None
    lower = name.lower()
    if "[bios]" in lower:
        return None
    if "(unl)" in lower:
        return None
    clean = _BRACKETS_RE.sub("", name).strip()
    return clean or None


def _parse_en_dat(dat_path: Path) -> list[str]:
    """Parse an EN-Dat XML file and return clean machine names (brackets stripped).

    EN-Dats use <machine> elements instead of the standard <game> elements.
    Documentation-only entries (.txt/.pdf/etc.), BIOS entries, and unlicensed
    bootlegs are skipped.

    CD-based EN-Dats often wrap actual titles in a machine called ``Discs`` and
    store the translated entries as ``<disk name="...">`` children. In that
    case we extract the disk names directly instead of treating ``Discs`` as a
    game title.
    """
    try:
        tree = ET.parse(dat_path)
    except Exception as exc:
        print(f"  WARNING: cannot parse {dat_path.name}: {exc}", file=sys.stderr)
        return []

    root = tree.getroot()
    results: list[str] = []
    seen: set[str] = set()

    # EN-Dats use <machine>; standard No-Intro DATs use <game>. Handle both.
    for tag in ("machine", "game"):
        for elem in root.findall(f".//{tag}"):
            disk_names = [d.get("name", "").strip() for d in elem.findall("disk") if d.get("name")]
            if disk_names:
                for dname in disk_names:
                    clean = _clean_machine_name(dname)
                    if clean and clean not in seen:
                        seen.add(clean)
                        results.append(clean)
                continue

            mname = elem.get("name", "").strip()
            if not mname:
                continue

            # Collect the file extensions of all <rom> children
            rom_exts = {
                Path(r.get("name", "")).suffix.lower()
                for r in elem.findall("rom")
                if r.get("name")
            }
            # Skip documentation-only entries (.txt, .nfo, etc.)
            if rom_exts and not (rom_exts & _ROM_EXTS):
                continue

            clean = _clean_machine_name(mname)
            if clean and clean not in seen:
                seen.add(clean)
                results.append(clean)

    return results


def _to_base_slug(clean_name: str) -> str:
    """Produce the same base slug that build_name_index uses as its dict key.

    The input already has [...] brackets stripped. This mirrors the logic
    inside build_name_index so the comparison is apples-to-apples.
    """
    s = rn._REGION_RE.sub("", clean_name)
    s = rn._REV_RE.sub("", s)
    s = rn._DISC_RE.sub("", s)
    s = rn._EXTRA_RE.sub("", s)
    s = s.lower()
    s = rn._NON_ALNUM_RE.sub("_", s)
    s = rn._MULTI_UNDERSCORE_RE.sub("_", s).strip("_")
    return s


def _is_auto_matched(slug: str, name_index: dict[str, str]) -> bool:
    """Return True if slug or any variant (roman<->arabic, article) is in name_index."""
    if slug in name_index:
        return True
    for variant in rn._matching_slug_variants(slug):
        if variant in name_index:
            return True
    return False


def _find_hints(clean_name: str, name_index: dict[str, str], max_hints: int = 5) -> list[str]:
    """Return standard-DAT entries that share significant words with clean_name.

    Ranks candidates by number of shared keywords (4+ char words). This lets
    the user quickly spot the original ROM (e.g. 'goemon' appears in both the
    translated title and the Japanese original).
    """
    slug = _to_base_slug(clean_name)
    keywords = {w for w in slug.split("_") if len(w) >= 4}
    if not keywords:
        return []

    scored: list[tuple[int, str]] = []
    seen_names: set[str] = set()
    for std_slug, std_name in name_index.items():
        std_words = set(std_slug.split("_"))
        shared = len(keywords & std_words)
        if shared > 0 and std_name not in seen_names:
            seen_names.add(std_name)
            scored.append((-shared, std_name))  # negative -> descending

    scored.sort(key=lambda t: (t[0], t[1]))
    return [name for _, name in scored[:max_hints]]


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--output", type=Path, default=ALIASES_PATH,
        help="Path to write aliases.json (default: EN-Dats/aliases.json)",
    )
    ap.add_argument(
        "--hints", action="store_true",
        help="Print suggested standard-DAT matches for each unmatched entry",
    )
    ap.add_argument(
        "--system", metavar="CODE",
        help="Process only this system code (e.g. SNES, NDS, MD)",
    )
    args = ap.parse_args()

    if not EN_DATS_DIR.exists():
        print(f"ERROR: EN-Dats directory not found: {EN_DATS_DIR}")
        sys.exit(1)

    # Always load existing aliases — filled entries are never discarded
    existing: dict[str, dict[str, str]] = {}
    if args.output.exists():
        try:
            existing = json.loads(args.output.read_text(encoding="utf-8"))
            total_ex = sum(len(v) for v in existing.values())
            filled_ex = sum(1 for sv in existing.values() for v in sv.values() if v)
            print(f"Loaded aliases.json: {total_ex} entries, {filled_ex} filled\n")
        except Exception as exc:
            print(f"WARNING: could not load existing aliases.json: {exc}\n")

    # Preserve the existing file by default. When a system is scanned below, its
    # section is rebuilt from the latest unmatched set plus any already-filled
    # aliases for that system. This avoids dropping empty entries for unrelated
    # systems when running with --system.
    output: dict[str, dict[str, str]] = {
        sys_code: dict(sys_map) for sys_code, sys_map in existing.items()
    }

    grand_total = grand_matched = grand_unmatched = 0
    processed_systems: set[str] = set()
    pending_unmatched: dict[str, dict[str, str]] = {}

    en_dat_files = sorted(EN_DATS_DIR.glob("*.dat"))
    print(f"Scanning {len(en_dat_files)} EN-Dat files in {EN_DATS_DIR}\n")
    print(f"  {'':2} {'[system]':<10} {'EN-Dat file':<52}  matched  unmatched")
    print(f"  {'-'*2} {'-'*10} {'-'*52}  {'-'*7}  {'-'*9}")

    for dat_path in en_dat_files:
        code = _system_code(dat_path.stem)

        if code is None:
            print(f"  -- {'(skip)':<10} {dat_path.stem[:52]:<52}  (no system mapping)")
            continue

        if args.system and code.upper() != args.system.upper():
            continue

        std_path = rn.find_dat_for_system(code)
        if std_path is None:
            print(f"  -- [{code:<8}] {dat_path.stem[:52]:<52}  (no standard DAT found)")
            continue

        processed_systems.add(code)

        no_intro = rn.load_no_intro_dat(std_path)
        if not no_intro:
            print(f"  -- [{code:<8}] {dat_path.stem[:52]:<52}  (standard DAT is empty)")
            continue
        name_index = rn.build_name_index(no_intro)

        entries = _parse_en_dat(dat_path)
        if not entries:
            print(f"  -- [{code:<8}] {dat_path.stem[:52]:<52}  (EN-Dat parsed 0 entries)")
            continue

        matched = 0
        unmatched: dict[str, str] = {}

        for clean in entries:
            grand_total += 1
            slug = _to_base_slug(clean)
            if _is_auto_matched(slug, name_index):
                matched += 1
                grand_matched += 1
            else:
                grand_unmatched += 1
                # Carry forward any value already filled for this entry
                prev = existing.get(code, {}).get(clean, "")
                unmatched[clean] = prev

        tag = "+" if not unmatched else " "
        print(
            f"  {tag} [{code:<8}] {dat_path.stem[:52]:<52}"
            f"  {matched:>7}  {len(unmatched):>9}"
        )

        if unmatched and args.hints:
            shown = 0
            for clean, existing_val in unmatched.items():
                status = f'  [-> "{existing_val}"]' if existing_val else ""
                print(f'        "{clean}"{status}')
                for h in _find_hints(clean, name_index):
                    print(f'            -> "{h}"')
                shown += 1
                if shown >= 10:
                    remaining = len(unmatched) - shown
                    if remaining > 0:
                        print(f"        ... and {remaining} more (use --system {code} for full list)")
                    break
            print()

        if unmatched:
            sys_pending = pending_unmatched.setdefault(code, {})
            for clean, val in unmatched.items():
                sys_pending[clean] = val

    for code in processed_systems:
        # Rebuild each processed system from scratch using the current unmatched
        # entries from every EN-Dat file that maps to it, while preserving
        # previously-filled aliases for the same system.
        sys_out = {k: v for k, v in existing.get(code, {}).items() if v}
        for clean, val in pending_unmatched.get(code, {}).items():
            sys_out[clean] = val
        if sys_out:
            output[code] = sys_out
        elif code in output:
            del output[code]

    # Sort each system's entries alphabetically
    for sys_code in output:
        output[sys_code] = dict(sorted(output[sys_code].items()))

    # Summary
    print(f"\n{'-'*76}")
    print(f"  Total entries scanned : {grand_total:>6}")
    print(f"  Auto-matched          : {grand_matched:>6}  (will sync correctly with no extra config)")
    print(f"  Need manual alias     : {grand_unmatched:>6}  (title was renamed during translation)")
    print(f"{'-'*76}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    total_written = sum(len(v) for v in output.values())
    filled_written = sum(1 for sv in output.values() for v in sv.values() if v)
    empty_written = total_written - filled_written
    print(f"\nWritten: {args.output}")
    print(f"  {total_written} entries total  |  {filled_written} filled  |  {empty_written} awaiting lookup")
    if empty_written > 0:
        print()
        print("  For each empty entry, set the value to the canonical No-Intro name")
        print("  from the standard DAT - the name the translated ROM's save slot")
        print("  should share with the original ROM.")
        print()
        print("  Example:")
        print('    "Go for it! Goemon 2 - The Strange General McGuinness (Japan)"')
        print('      -> "Ganbare Goemon 2 - Kiteretsu Shougun McGuiness (Japan)"')
        print()
        print("  Tip: re-run with --hints to see suggested matches for each entry.")


if __name__ == "__main__":
    main()
