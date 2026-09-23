#!/usr/bin/env python3
"""Inject ``cloneof`` fields into clrmamepro DATs using Retool clonelist JSONs.

This is intended for DATs in ``server/data/dats`` that do not already contain
``cloneof`` lines, especially Redump/CD-based sets where we have Retool
clonelists but no XML clone metadata.

The script is intentionally conservative:
- It only injects missing ``cloneof`` lines.
- It skips entries that match multiple clone groups.
- It supports simple filter redirects from the clonelists.
- It prefers leaders from normal title entries before supersets/compilations,
  then prefers non-beta/non-demo releases, then USA/World/Europe/Japan.

Usage:
    python desktop/tools/enrich_dats_from_clonelists.py
    python desktop/tools/enrich_dats_from_clonelists.py --apply
    python desktop/tools/enrich_dats_from_clonelists.py --dat "Sony - PlayStation.dat" --apply
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DATS_DIR = ROOT_DIR / "server" / "data" / "dats"
DEFAULT_CLONELISTS_DIR = DEFAULT_DATS_DIR / "clonelists"


_REGION_ORDER = {
    "usa": 0,
    "world": 1,
    "europe": 2,
    "japan": 3,
    "asia": 4,
    "australia": 5,
    "france": 6,
    "germany": 7,
    "spain": 8,
    "italy": 9,
    "korea": 10,
}

_SPECIAL_TAG_RE = re.compile(
    r"\((?:beta|proto|sample|demo|promo|preview|test program|competition cart)\b",
    re.IGNORECASE,
)
_TRAILING_TAG_RE = re.compile(r"^(?P<base>.+?)\s*(?:\([^)]+\)|\[[^\]]+\])\s*$")
_NAME_LINE_RE = re.compile(r'^\s*name\s+"(.+?)"')
_REGION_LINE_RE = re.compile(r'^\s*region\s+"(.+?)"')
_CLONEOF_LINE_RE = re.compile(r'^\s*cloneof\s+"(.+?)"')
_GAME_OPEN_RE = re.compile(r"^game\s*\(")
_BLOCK_CLOSE_RE = re.compile(r"^\)")
_LOOSE_NON_ALNUM_RE = re.compile(r"[^0-9a-z]+", re.IGNORECASE)


# DAT filename -> clonelist filename
_CLONELIST_MAP: dict[str, str] = {
    "Atari - 7800.dat": "Atari - Atari 7800 (No-Intro).json",
    "Atari - Jaguar.dat": "Atari - Atari Jaguar (No-Intro).json",
    "Bandai - WonderSwan.dat": "Bandai - WonderSwan (No-Intro).json",
    "Commodore - CD32.dat": "Commodore - Amiga CD32 (Redump).json",
    "Mattel - Intellivision.dat": "Mattel - Intellivision (No-Intro).json",
    "Microsoft - MSX.dat": "Microsoft - MSX (No-Intro).json",
    "Microsoft - MSX2.dat": "Microsoft - MSX2 (No-Intro).json",
    "Microsoft - Xbox.dat": "Microsoft - Xbox (Redump).json",
    "Microsoft - Xbox 360.dat": "Microsoft - Xbox 360 (Redump).json",
    "NEC - PC Engine CD - TurboGrafx-CD.dat": "NEC - PC Engine CD & TurboGrafx CD (Redump).json",
    "NEC - PC-98.dat": "NEC - PC-98 series (Redump).json",
    "NEC - PC-FX.dat": "NEC - PC-FX & PC-FXGA (Redump).json",
    "Nintendo - Family Computer Disk System.dat": "Nintendo - Family Computer Disk System (No-Intro).json",
    "Nintendo - GameCube.dat": "Nintendo - GameCube (Redump).json",
    "Nintendo - Nintendo 3DS (Digital).dat": "Nintendo - Nintendo 3DS (Digital) (CDN) (No-Intro).json",
    "Nintendo - Nintendo 64DD.dat": "Nintendo - Nintendo 64DD (No-Intro).json",
    "Nintendo - Nintendo DSi.dat": "Nintendo - Nintendo DSi (No-Intro).json",
    "Nintendo - Pokemon Mini.dat": "Nintendo - Pokemon Mini (No-Intro).json",
    "Nintendo - Virtual Boy.dat": "Nintendo - Virtual Boy (No-Intro).json",
    "Nintendo - Wii.dat": "Nintendo - Wii (Redump).json",
    "Sega - 32X.dat": "Sega - 32X (No-Intro).json",
    "Sega - Dreamcast.dat": "Sega - Dreamcast (Redump).json",
    "Sega - Mega-CD - Sega CD.dat": "Sega - Mega CD & Sega CD (Redump).json",
    "Sega - Naomi.dat": "Arcade - Sega - Naomi (Redump).json",
    "Sega - Saturn.dat": "Sega - Saturn (Redump).json",
    "Sony - PlayStation.dat": "Sony - PlayStation (Redump).json",
    "Sony - PlayStation 2.dat": "Sony - PlayStation 2 (Redump).json",
    "Sony - PlayStation 3.dat": "Sony - PlayStation 3 (Redump).json",
    "Sony - PlayStation Portable.dat": "Sony - PlayStation Portable (Redump).json",
    "Sony - PlayStation Portable (PSN).dat": "Non-Redump - Sony - PlayStation Portable (No-Intro).json",
    "Sony - PlayStation Vita.dat": "Unofficial - Sony - PlayStation Vita (No-Intro).json",
    "The 3DO Company - 3DO.dat": "Panasonic - 3DO Interactive Multiplayer (Redump).json",
}


@dataclass(frozen=True)
class MatchFilter:
    match_regions: tuple[str, ...]
    match_string: str | None
    result_group: str | None


@dataclass(frozen=True)
class MatchRule:
    group: str
    section: str
    search_term: str
    normalized_term: str
    priority: int
    name_type: str
    filters: tuple[MatchFilter, ...]


@dataclass
class GameBlock:
    index: int
    original_length: int
    lines: list[str]
    name: str
    region: str
    has_cloneof: bool


@dataclass
class GameMatch:
    group: str
    section: str
    priority: int
    search_term_length: int


@dataclass
class EnrichStats:
    matched_blocks: int = 0
    ambiguous_blocks: int = 0
    unmatched_blocks: int = 0
    injected_entries: int = 0
    groups_with_injections: int = 0
    skipped_existing_cloneof: int = 0
    groups_matched: int = 0


def normalize_text(value: str) -> str:
    return " ".join(value.casefold().split())


def normalize_loose(value: str) -> str:
    normalized = _LOOSE_NON_ALNUM_RE.sub(" ", value.casefold())
    return " ".join(normalized.split())


def build_candidate_names(name: str) -> list[str]:
    """Return progressively stripped title candidates for exact lookup.

    Example:
      "Foo (Europe) (PSN)" -> ["Foo (Europe) (PSN)", "Foo (Europe)", "Foo"]
    """
    candidates: list[str] = []
    current = name.strip()
    while current:
        candidates.append(current)
        match = _TRAILING_TAG_RE.match(current)
        if not match:
            break
        next_value = match.group("base").strip()
        if not next_value or next_value == current:
            break
        current = next_value
    return candidates


def _leader_sort_key(name: str, region: str, section_rank: int, priority: int) -> tuple:
    region_rank = _REGION_ORDER.get(region.casefold(), len(_REGION_ORDER))
    is_special = 1 if _SPECIAL_TAG_RE.search(name) else 0
    tag_count = len(re.findall(r"\([^)]+\)|\[[^\]]+\]", name))
    return (section_rank, priority, is_special, region_rank, tag_count, name.casefold())


def load_clonelist_rules(
    clonelist_path: Path,
) -> tuple[
    dict[str, list[MatchRule]],
    dict[str, list[MatchRule]],
    dict[str, list[MatchRule]],
    dict[str, list[MatchRule]],
]:
    data = json.loads(clonelist_path.read_text(encoding="utf-8"))

    prefix_rules: dict[str, list[MatchRule]] = {}
    full_rules: dict[str, list[MatchRule]] = {}
    prefix_rules_loose: dict[str, list[MatchRule]] = {}
    full_rules_loose: dict[str, list[MatchRule]] = {}

    for variant in data.get("variants", []):
        group = str(variant.get("group", "")).strip()
        if not group:
            continue

        for section in ("titles", "supersets", "compilations"):
            for item in variant.get(section, []) or []:
                search_terms = [str(item.get("searchTerm", "")).strip()]
                local_names = item.get("localNames", {}) or {}
                search_terms.extend(
                    str(value).strip()
                    for value in local_names.values()
                    if isinstance(value, str) and value.strip()
                )

                filters = tuple(
                    MatchFilter(
                        match_regions=tuple(
                            str(region).casefold()
                            for region in flt.get("conditions", {}).get("matchRegions", [])
                        ),
                        match_string=flt.get("conditions", {}).get("matchString"),
                        result_group=flt.get("results", {}).get("group"),
                    )
                    for flt in item.get("filters", []) or []
                )

                for search_term in search_terms:
                    if not search_term:
                        continue

                    rule = MatchRule(
                        group=group,
                        section=section,
                        search_term=search_term,
                        normalized_term=normalize_text(search_term),
                        priority=int(item.get("priority", 0) or 0),
                        name_type=str(item.get("nameType", "prefix")),
                        filters=filters,
                    )

                    if rule.name_type == "full":
                        full_rules.setdefault(rule.normalized_term, []).append(rule)
                        full_rules_loose.setdefault(normalize_loose(search_term), []).append(rule)
                    else:
                        prefix_rules.setdefault(rule.normalized_term, []).append(rule)
                        prefix_rules_loose.setdefault(normalize_loose(search_term), []).append(rule)

    return prefix_rules, full_rules, prefix_rules_loose, full_rules_loose


def parse_dat_blocks(dat_path: Path) -> tuple[list[str], list[GameBlock]]:
    lines = dat_path.read_text(encoding="utf-8", errors="replace").splitlines(True)
    blocks: list[GameBlock] = []

    i = 0
    while i < len(lines):
        line = lines[i]
        if not _GAME_OPEN_RE.match(line):
            i += 1
            continue

        start = i
        block_lines = [line]
        i += 1
        while i < len(lines) and not _BLOCK_CLOSE_RE.match(lines[i]):
            block_lines.append(lines[i])
            i += 1
        if i < len(lines):
            block_lines.append(lines[i])
            i += 1

        name = ""
        region = ""
        has_cloneof = False
        for block_line in block_lines:
            name_match = _NAME_LINE_RE.match(block_line)
            if name_match and not name:
                name = name_match.group(1).strip()
                continue
            region_match = _REGION_LINE_RE.match(block_line)
            if region_match and not region:
                region = region_match.group(1).strip()
                continue
            if _CLONEOF_LINE_RE.match(block_line):
                has_cloneof = True

        blocks.append(
            GameBlock(
                index=start,
                original_length=len(block_lines),
                lines=block_lines,
                name=name,
                region=region,
                has_cloneof=has_cloneof,
            )
        )

    return lines, blocks


def match_block(
    block: GameBlock,
    prefix_rules: dict[str, list[MatchRule]],
    full_rules: dict[str, list[MatchRule]],
    prefix_rules_loose: dict[str, list[MatchRule]],
    full_rules_loose: dict[str, list[MatchRule]],
) -> tuple[str | None, GameMatch | None]:
    candidates: dict[str, GameMatch] = {}

    normalized_full_name = normalize_text(block.name)
    normalized_full_name_loose = normalize_loose(block.name)
    for rules in (
        full_rules.get(normalized_full_name, []),
        full_rules_loose.get(normalized_full_name_loose, []),
    ):
        for rule in rules:
            group = apply_filters(rule, block)
            current = candidates.get(group)
            proposal = GameMatch(
                group=group,
                section=rule.section,
                priority=rule.priority,
                search_term_length=len(rule.search_term),
            )
            if current is None or is_better_match(proposal, current):
                candidates[group] = proposal

    for candidate_name in build_candidate_names(block.name):
        normalized_candidate = normalize_text(candidate_name)
        normalized_candidate_loose = normalize_loose(candidate_name)
        for rules in (
            prefix_rules.get(normalized_candidate, []),
            prefix_rules_loose.get(normalized_candidate_loose, []),
        ):
            for rule in rules:
                group = apply_filters(rule, block)
                current = candidates.get(group)
                proposal = GameMatch(
                    group=group,
                    section=rule.section,
                    priority=rule.priority,
                    search_term_length=len(rule.search_term),
                )
                if current is None or is_better_match(proposal, current):
                    candidates[group] = proposal

    if len(candidates) != 1:
        return None, None

    group = next(iter(candidates))
    return group, candidates[group]


def apply_filters(rule: MatchRule, block: GameBlock) -> str:
    full_name = block.name
    region = block.region.casefold()

    for flt in rule.filters:
        if flt.match_regions and region not in flt.match_regions:
            continue
        if flt.match_string and not re.search(flt.match_string, full_name, re.IGNORECASE):
            continue
        if flt.result_group:
            return flt.result_group
    return rule.group


def is_better_match(left: GameMatch, right: GameMatch) -> bool:
    section_rank = {"titles": 0, "supersets": 1, "compilations": 2}
    left_key = (
        section_rank.get(left.section, 9),
        left.priority,
        -left.search_term_length,
    )
    right_key = (
        section_rank.get(right.section, 9),
        right.priority,
        -right.search_term_length,
    )
    return left_key < right_key


def inject_cloneof(block: GameBlock, leader_name: str) -> None:
    injected: list[str] = []
    inserted = False
    for line in block.lines:
        injected.append(line)
        if not inserted and _NAME_LINE_RE.match(line):
            injected.append(f'\tcloneof "{leader_name}"\n')
            inserted = True
    block.lines = injected


def enrich_dat(dat_path: Path, clonelist_path: Path, apply: bool) -> EnrichStats:
    stats = EnrichStats()
    original_lines, blocks = parse_dat_blocks(dat_path)
    prefix_rules, full_rules, prefix_rules_loose, full_rules_loose = load_clonelist_rules(
        clonelist_path
    )

    grouped_blocks: dict[str, list[tuple[GameBlock, GameMatch]]] = {}
    for block in blocks:
        if block.has_cloneof:
            stats.skipped_existing_cloneof += 1
            continue
        if not block.name:
            stats.unmatched_blocks += 1
            continue

        group, match = match_block(
            block,
            prefix_rules,
            full_rules,
            prefix_rules_loose,
            full_rules_loose,
        )
        if group is None or match is None:
            # Distinguish ambiguous vs unmatched by checking whether there were any candidates.
            full_name = normalize_text(block.name)
            full_name_loose = normalize_loose(block.name)
            candidate_groups: set[str] = set()
            for rules in (
                full_rules.get(full_name, []),
                full_rules_loose.get(full_name_loose, []),
            ):
                for rule in rules:
                    candidate_groups.add(apply_filters(rule, block))
            for candidate_name in build_candidate_names(block.name):
                for rules in (
                    prefix_rules.get(normalize_text(candidate_name), []),
                    prefix_rules_loose.get(normalize_loose(candidate_name), []),
                ):
                    for rule in rules:
                        candidate_groups.add(apply_filters(rule, block))
            if len(candidate_groups) > 1:
                stats.ambiguous_blocks += 1
            else:
                stats.unmatched_blocks += 1
            continue

        stats.matched_blocks += 1
        grouped_blocks.setdefault(group, []).append((block, match))

    stats.groups_matched = len(grouped_blocks)

    groups_with_injections = 0
    section_rank = {"titles": 0, "supersets": 1, "compilations": 2}
    for _, items in grouped_blocks.items():
        leader_block, _ = min(
            items,
            key=lambda item: _leader_sort_key(
                item[0].name,
                item[0].region,
                section_rank.get(item[1].section, 9),
                item[1].priority,
            ),
        )
        leader_name = leader_block.name
        injected_here = 0
        for block, _ in items:
            if block.name == leader_name:
                continue
            inject_cloneof(block, leader_name)
            injected_here += 1
            stats.injected_entries += 1
        if injected_here:
            groups_with_injections += 1

    stats.groups_with_injections = groups_with_injections

    if apply and stats.injected_entries:
        rebuilt: list[str] = []
        block_by_start = {block.index: block for block in blocks}
        i = 0
        while i < len(original_lines):
            block = block_by_start.get(i)
            if block is None:
                rebuilt.append(original_lines[i])
                i += 1
                continue
            rebuilt.extend(block.lines)
            i += block.original_length

        dat_path.write_text("".join(rebuilt), encoding="utf-8")

    return stats


def find_dat_paths(dats_dir: Path, requested_dat: str | None) -> list[Path]:
    if requested_dat:
        path = dats_dir / requested_dat
        if not path.exists():
            raise FileNotFoundError(f"DAT not found: {path}")
        return [path]

    return sorted(path for path in dats_dir.glob("*.dat") if path.is_file())


def resolve_clonelist(dat_path: Path, clonelists_dir: Path) -> Path | None:
    clonelist_name = _CLONELIST_MAP.get(dat_path.name)
    if not clonelist_name:
        return None
    path = clonelists_dir / clonelist_name
    return path if path.exists() else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dats-dir", type=Path, default=DEFAULT_DATS_DIR)
    parser.add_argument("--clonelists-dir", type=Path, default=DEFAULT_CLONELISTS_DIR)
    parser.add_argument("--dat", help="Specific DAT filename in the DAT directory")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes back to the DAT files. Default is dry-run.",
    )
    parser.add_argument(
        "--only-missing",
        action="store_true",
        default=True,
        help="Skip DATs that already contain at least one cloneof line (default: on).",
    )
    parser.add_argument(
        "--include-existing",
        action="store_true",
        help="Also process DATs that already contain cloneof lines.",
    )
    args = parser.parse_args()

    dat_paths = find_dat_paths(args.dats_dir, args.dat)
    processed = 0
    modified = 0
    skipped_no_clonelist = 0
    skipped_has_cloneof = 0

    for dat_path in dat_paths:
        text = dat_path.read_text(encoding="utf-8", errors="ignore")
        if not args.include_existing and args.only_missing and 'cloneof "' in text:
            skipped_has_cloneof += 1
            continue

        clonelist_path = resolve_clonelist(dat_path, args.clonelists_dir)
        if clonelist_path is None:
            skipped_no_clonelist += 1
            print(f"SKIP {dat_path.name}: no mapped clonelist")
            continue

        stats = enrich_dat(dat_path, clonelist_path, apply=args.apply)
        processed += 1
        if stats.injected_entries:
            modified += 1

        action = "APPLY" if args.apply else "DRY-RUN"
        print(
            f"{action} {dat_path.name}: "
            f"injected={stats.injected_entries}, "
            f"groups={stats.groups_with_injections}, "
            f"matched={stats.matched_blocks}, "
            f"ambiguous={stats.ambiguous_blocks}, "
            f"unmatched={stats.unmatched_blocks}, "
            f"existing={stats.skipped_existing_cloneof}, "
            f"clonelist={clonelist_path.name}"
        )

    print(
        f"SUMMARY processed={processed}, modified={modified}, "
        f"skipped_no_clonelist={skipped_no_clonelist}, skipped_has_cloneof={skipped_has_cloneof}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
