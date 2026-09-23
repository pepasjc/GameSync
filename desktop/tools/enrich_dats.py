#!/usr/bin/env python3
"""Enrich libretro clrmamepro DAT files with ``cloneof`` fields from No-Intro XML DATs.

For each matching pair of DATs (matched by system name / CRC overlap):
1. Parse the No-Intro XML to extract clone groups via ``id`` / ``cloneofid``.
2. Pick a USA-preferred group leader for each clone group.
3. Match entries between the two DATs by CRC.
4. Inject ``cloneof "Leader Name"`` into each libretro DAT entry that belongs
   to a clone group (non-leaders only).

Usage:
    python enrich_dats.py --nointro-dir <path> --libretro-dir <path> [--dry-run]
"""

from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


# ---------------------------------------------------------------------------
# Region priority for picking the group leader (lower = better)
# ---------------------------------------------------------------------------
_REGION_ORDER = [
    re.compile(r"\(USA\b", re.IGNORECASE),
    re.compile(r"\(USA,\s*Europe\b", re.IGNORECASE),
    re.compile(r"\(World\b", re.IGNORECASE),
    re.compile(r"\(Europe\b", re.IGNORECASE),
    re.compile(r"\(Japan\b", re.IGNORECASE),
]

_SPECIAL_TAG_RE = re.compile(
    r"\((Beta|Proto|Sample|Demo|Promo|Preview|Test Program|Competition Cart)\b",
    re.IGNORECASE,
)

_VIRTUAL_CONSOLE_RE = re.compile(
    r"\((Virtual Console|Advance Collection|Classic NES|Ambassador)\b",
    re.IGNORECASE,
)


def _leader_sort_key(name: str) -> tuple:
    """Return a sort key that prefers USA, non-special, non-VC entries."""
    is_special = 1 if _SPECIAL_TAG_RE.search(name) else 0
    is_vc = 1 if _VIRTUAL_CONSOLE_RE.search(name) else 0
    region_rank = len(_REGION_ORDER)
    for i, pat in enumerate(_REGION_ORDER):
        if pat.search(name):
            region_rank = i
            break
    # Fewer parenthetical tags = simpler = preferred
    tag_count = len(re.findall(r"\([^)]+\)", name))
    return (is_special, is_vc, region_rank, tag_count, name)


# ---------------------------------------------------------------------------
# No-Intro XML parsing
# ---------------------------------------------------------------------------


def parse_nointro_xml(dat_path: Path) -> tuple[dict[str, str], set[str]]:
    """Parse a No-Intro XML DAT and return clone groups.

    Returns:
        clone_map:  { crc -> leader_name }   (non-leader entries only)
        leader_crcs: { crc }                 (leader entries, no cloneof needed)
    """
    tree = ET.parse(dat_path)
    root = tree.getroot()

    # Step 1: collect all game entries
    # id -> (name, cloneofid | None, crc)
    entries: dict[str, tuple[str, str | None, str]] = {}
    for game in root.findall(".//game"):
        gid = game.get("id", "")
        name = game.get("name", "")
        cloneofid = game.get("cloneofid")
        crc = ""
        for rom in game.findall("rom"):
            crc = rom.get("crc", "").upper()
            break
        if gid and name:
            entries[gid] = (name, cloneofid, crc)

    # Step 2: build clone groups
    # group_root_id -> set of all member ids (including root)
    children: dict[str, list[str]] = {}  # parent_id -> [child_ids]
    for gid, (_, cloneofid, _) in entries.items():
        if cloneofid is not None:
            children.setdefault(cloneofid, []).append(gid)

    # Step 3: for each group, pick the best leader
    clone_map: dict[str, str] = {}  # crc -> leader_name
    leader_crcs: set[str] = set()

    for parent_id, child_ids in children.items():
        # All members of this group: parent + children
        all_ids = [parent_id] + child_ids
        # Only include ids that actually exist in entries
        members = [(gid, entries[gid]) for gid in all_ids if gid in entries]
        if len(members) < 2:
            continue  # not really a group

        # Pick the leader by region priority
        leader_id, (leader_name, _, leader_crc) = min(
            members, key=lambda x: _leader_sort_key(x[1][0])
        )

        if leader_crc:
            leader_crcs.add(leader_crc)

        # Map all non-leader CRCs to the leader name
        for gid, (name, _, crc) in members:
            if crc and gid != leader_id:
                clone_map[crc] = leader_name

    return clone_map, leader_crcs


# ---------------------------------------------------------------------------
# Libretro DAT rewriting
# ---------------------------------------------------------------------------

_CLRMAME_CRC_RE = re.compile(r"\bcrc\s+([0-9A-Fa-f]+)", re.IGNORECASE)
_CLRMAME_NAME_RE = re.compile(r'^\s*name\s+"(.+?)"')
_GAME_OPEN_RE = re.compile(r"^game\s*\(")
_BLOCK_CLOSE_RE = re.compile(r"^\)")


def enrich_libretro_dat(
    dat_path: Path,
    clone_map: dict[str, str],
    leader_crcs: set[str],
    dry_run: bool = False,
) -> tuple[int, int]:
    """Inject ``cloneof`` fields into a libretro clrmamepro DAT.

    Returns (groups_injected, entries_injected).
    """
    lines = dat_path.read_text(encoding="utf-8", errors="replace").splitlines(True)
    new_lines: list[str] = []
    entries_injected = 0
    leaders_seen: set[str] = set()

    # Parse in blocks: each "game (" ... ")" block
    i = 0
    while i < len(lines):
        line = lines[i]
        if _GAME_OPEN_RE.match(line):
            # Collect the entire game block
            block = [line]
            i += 1
            while i < len(lines) and not _BLOCK_CLOSE_RE.match(lines[i]):
                block.append(lines[i])
                i += 1
            if i < len(lines):
                block.append(lines[i])  # the closing ")"
                i += 1

            # Extract CRC and name from this block
            block_crc = ""
            block_name = ""
            for bline in block:
                m = _CLRMAME_NAME_RE.match(bline)
                if m:
                    block_name = m.group(1).strip()
                m2 = _CLRMAME_CRC_RE.search(bline)
                if m2:
                    block_crc = m2.group(1).upper()

            # Check if this entry needs a cloneof
            leader_name = clone_map.get(block_crc)
            if leader_name and leader_name != block_name:
                # Inject cloneof after the name line
                injected_block: list[str] = []
                done = False
                for bline in block:
                    injected_block.append(bline)
                    if not done and _CLRMAME_NAME_RE.match(bline):
                        injected_block.append(f'\tcloneof "{leader_name}"\n')
                        done = True
                block = injected_block
                entries_injected += 1
                leaders_seen.add(leader_name)

            new_lines.extend(block)
        else:
            new_lines.append(line)
            i += 1

    if entries_injected > 0 and not dry_run:
        dat_path.write_text("".join(new_lines), encoding="utf-8")

    return len(leaders_seen), entries_injected


# ---------------------------------------------------------------------------
# Matching No-Intro XML to libretro DAT by system name
# ---------------------------------------------------------------------------

# Explicit mapping: libretro DAT stem -> glob pattern to find in No-Intro dir.
# Use the most specific pattern possible to avoid false matches.
_EXPLICIT_MATCH: dict[str, str] = {
    "Atari - 2600": "Atari - Atari 2600 (*).dat",
    "Atari - 7800": "Atari - Atari 7800 (A78) (*).dat",
    "Atari - Lynx": "Atari - Atari Lynx (LNX) (*).dat",
    "Bandai - WonderSwan Color": "Bandai - WonderSwan Color (*).dat",
    "NEC - PC Engine - TurboGrafx 16": "NEC - PC Engine - TurboGrafx-16 (*).dat",
    "Nintendo - Game Boy Advance": "Nintendo - Game Boy Advance (2*).dat",
    "Nintendo - Game Boy Color": "Nintendo - Game Boy Color (*).dat",
    "Nintendo - Game Boy": "Nintendo - Game Boy (2*).dat",
    "Nintendo - Nintendo 3DS (Digital)": "Nintendo - Nintendo 3DS (Digital) (CDN) (*).dat",
    "Nintendo - Nintendo 3DS": "Nintendo - Nintendo 3DS (Decrypted) (*).dat",
    "Nintendo - Nintendo 64": "Nintendo - Nintendo 64 (BigEndian) (*).dat",
    "Nintendo - Nintendo DS": "Nintendo - Nintendo DS (Decrypted) (2*).dat",
    "Nintendo - Nintendo DSi": "Nintendo - Nintendo DSi (Decrypted) (*).dat",
    "Nintendo - Nintendo Entertainment System": "Nintendo - Nintendo Entertainment System (Headered) (*).dat",
    "Nintendo - Super Nintendo Entertainment System": "Nintendo - Super Nintendo Entertainment System (*).dat",
    "Nintendo - Virtual Boy": "Nintendo - Virtual Boy (*).dat",
    "Sega - 32X": "Sega - 32X (*).dat",
    "Sega - Game Gear": "Sega - Game Gear (*).dat",
    "Sega - Master System - Mark III": "Sega - Master System - Mark III (*).dat",
    "Sega - Mega Drive - Genesis": "Sega - Mega Drive - Genesis (*).dat",
    "SNK - Neo Geo Pocket Color": "SNK - NeoGeo Pocket Color (*).dat",
}


def find_matching_nointro_dat(libretro_stem: str, nointro_dir: Path) -> Path | None:
    """Find the best No-Intro XML DAT matching a libretro DAT system name."""
    pattern = _EXPLICIT_MATCH.get(libretro_stem)
    if pattern is None:
        return None
    matches = sorted(nointro_dir.glob(pattern))
    return matches[0] if matches else None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--nointro-dir",
        type=Path,
        required=True,
        help="Directory containing No-Intro XML DAT files",
    )
    parser.add_argument(
        "--libretro-dir",
        type=Path,
        required=True,
        help="Directory containing libretro clrmamepro DAT files",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without modifying files",
    )
    args = parser.parse_args()

    libretro_dats = sorted(args.libretro_dir.glob("*.dat"))
    if not libretro_dats:
        print(f"No .dat files found in {args.libretro_dir}")
        sys.exit(1)

    total_files = 0
    total_entries = 0
    total_groups = 0

    for lib_dat in libretro_dats:
        noi_dat = find_matching_nointro_dat(lib_dat.stem, args.nointro_dir)
        if noi_dat is None:
            print(f"  SKIP  {lib_dat.name}  (no matching No-Intro DAT)")
            continue

        print(f"  MATCH {lib_dat.name}")
        print(f"     -> {noi_dat.name}")

        try:
            clone_map, leader_crcs = parse_nointro_xml(noi_dat)
        except Exception as e:
            print(f"     ERROR parsing No-Intro DAT: {e}")
            continue

        if not clone_map:
            print(f"     No clone groups found")
            continue

        groups, entries = enrich_libretro_dat(
            lib_dat, clone_map, leader_crcs, dry_run=args.dry_run
        )
        action = "would inject" if args.dry_run else "injected"
        print(f"     {action} cloneof into {entries} entries ({groups} clone groups)")
        total_files += 1
        total_entries += entries
        total_groups += groups

    print(
        f"\n{'DRY RUN: ' if args.dry_run else ''}Enriched {total_files} DAT files, "
        f"{total_entries} entries across {total_groups} clone groups"
    )


if __name__ == "__main__":
    main()
