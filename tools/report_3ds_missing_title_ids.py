#!/usr/bin/env python3
"""Report 3DS DAT rows that still lack an injected title_id.

Outputs:
  - server/data/reports/3ds_missing_title_ids.csv
  - server/data/reports/3ds_missing_title_ids.md

The report is meant to help manual investigation. For each unmatched DAT row
it shows whether:
  - the 4-char product code exists in 3dstdb.txt
  - the DAT title has a possible core-name match in another 3DS DAT row that
    already has a title_id
"""

from __future__ import annotations

import csv
import datetime as dt
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "server" / "data"
DATS_DIR = DATA_DIR / "dats"
REPORTS_DIR = DATA_DIR / "reports"

CSV_PATH = REPORTS_DIR / "3ds_missing_title_ids.csv"
MD_PATH = REPORTS_DIR / "3ds_missing_title_ids.md"

CODE_DB_PATH = DATA_DIR / "3dstdb.txt"
DAT_PATHS = [
    DATS_DIR / "Nintendo - Nintendo 3DS.dat",
    DATS_DIR / "Nintendo - Nintendo 3DS (Digital).dat",
]

_NAME_RE = re.compile(r'^\s*name\s+"(.+?)"')
_DESC_RE = re.compile(r'^\s*description\s+"(.+?)"')
_REGION_RE = re.compile(r'^\s*region\s+"(.+?)"')
_SERIAL_RE = re.compile(r'^\s*serial\s+"(.+?)"')
_TITLE_ID_RE = re.compile(r'^\s*title_id\s+"([0-9A-Fa-f]{16})"')
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_PARENS_RE = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


@dataclass
class MissingEntry:
    dat_file: str
    line_no: int
    name: str
    region: str
    serial: str
    code4: str
    code4_in_3dstdb: bool
    code4_name: str
    core_slug: str
    dat_core_match_count: int
    dat_core_match_type: str
    dat_core_match_examples: str


def core_slug(name: str) -> str:
    if not name:
        return ""
    cleaned = _HTML_TAG_RE.sub(" ", name)
    cleaned = _PARENS_RE.sub(" ", cleaned)
    cleaned = _NON_ALNUM_RE.sub("_", cleaned.lower())
    return re.sub(r"_+", "_", cleaned).strip("_")


def load_code_db() -> dict[str, str]:
    data: dict[str, str] = {}
    for line in CODE_DB_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
        if "," not in line:
            continue
        code4, name = line.split(",", 1)
        data[code4.strip().upper()] = name.strip()
    return data


def load_dat_title_id_core_index() -> dict[str, list[tuple[str, str]]]:
    index: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for dat_path in DAT_PATHS:
        current_name: str | None = None
        current_title_id: str | None = None
        current_line_no = 0
        for line in dat_path.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if stripped == "game (":
                current_name = None
                current_title_id = None
                current_line_no = 1
                continue

            if current_line_no == 0:
                continue

            match = _NAME_RE.match(line) or _DESC_RE.match(line)
            if match and current_name is None:
                current_name = match.group(1).strip()
                continue

            match = _TITLE_ID_RE.match(line)
            if match and current_title_id is None:
                current_title_id = match.group(1).strip().upper()
                continue

            if stripped == ")":
                if current_name and current_title_id:
                    index[core_slug(current_name)].append((current_title_id, current_name))
                current_name = None
                current_title_id = None
                current_line_no = 0
    return index


def parse_missing_entries(
    dat_path: Path,
    code_db: dict[str, str],
    title_core_index: dict[str, list[tuple[str, str]]],
) -> list[MissingEntry]:
    entries: list[MissingEntry] = []

    current_name: str | None = None
    current_region: str | None = None
    current_serial: str | None = None
    current_has_title_id = False
    current_line_no = 0

    for line_no, line in enumerate(
        dat_path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
    ):
        stripped = line.strip()
        if stripped == "game (":
            current_name = None
            current_region = None
            current_serial = None
            current_has_title_id = False
            current_line_no = line_no
            continue

        if current_line_no == 0:
            continue

        m = _NAME_RE.match(line) or _DESC_RE.match(line)
        if m and current_name is None:
            current_name = m.group(1).strip()
            continue

        m = _REGION_RE.match(line)
        if m and current_region is None:
            current_region = m.group(1).strip()
            continue

        m = _SERIAL_RE.match(line)
        if m and current_serial is None:
            current_serial = m.group(1).strip().upper()
            continue

        if _TITLE_ID_RE.match(line):
            current_has_title_id = True
            continue

        if stripped == ")":
            if not current_has_title_id:
                code4 = current_serial.split("-")[-1] if current_serial and "-" in current_serial else ""
                code4_name = code_db.get(code4, "")
                slug = core_slug(current_name or "")
                title_matches = title_core_index.get(slug, [])
                match_count = len(title_matches)
                if match_count == 0:
                    match_type = "none"
                elif match_count == 1:
                    match_type = "unique_core_match"
                else:
                    match_type = "ambiguous_core_match"
                examples = " | ".join(
                    f"{title_id}:{name}" for title_id, name in title_matches[:5]
                )
                entries.append(
                    MissingEntry(
                        dat_file=dat_path.name,
                        line_no=current_line_no,
                        name=current_name or "",
                        region=current_region or "",
                        serial=current_serial or "",
                        code4=code4,
                        code4_in_3dstdb=code4 in code_db,
                        code4_name=code4_name,
                        core_slug=slug,
                        dat_core_match_count=match_count,
                        dat_core_match_type=match_type,
                        dat_core_match_examples=examples,
                    )
                )
            current_name = None
            current_region = None
            current_serial = None
            current_has_title_id = False
            current_line_no = 0

    return entries


def write_csv(entries: list[MissingEntry]) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(CSV_PATH, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "dat_file",
                "line_no",
                "name",
                "region",
                "serial",
                "code4",
                "code4_in_3dstdb",
                "code4_name",
                "core_slug",
                "dat_core_match_count",
                "dat_core_match_type",
                "dat_core_match_examples",
            ],
        )
        writer.writeheader()
        for entry in entries:
            writer.writerow(entry.__dict__)


def write_markdown(entries: list[MissingEntry]) -> None:
    by_dat = Counter(entry.dat_file for entry in entries)
    by_region = Counter(entry.region or "NONE" for entry in entries)
    by_match_type = Counter(entry.dat_core_match_type for entry in entries)
    code4_hits = sum(1 for entry in entries if entry.code4_in_3dstdb)
    unique_matches = [entry for entry in entries if entry.dat_core_match_type == "unique_core_match"]
    ambiguous_matches = [entry for entry in entries if entry.dat_core_match_type == "ambiguous_core_match"]

    lines: list[str] = []
    lines.append("# 3DS Missing `title_id` Report")
    lines.append("")
    lines.append(f"Generated: {dt.datetime.now(dt.timezone.utc).isoformat()}")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Total missing rows: `{len(entries)}`")
    lines.append(f"- Missing rows whose 4-char code exists in `3dstdb.txt`: `{code4_hits}`")
    lines.append(f"- Missing rows with a unique core-name candidate in existing 3DS DAT `title_id` entries: `{len(unique_matches)}`")
    lines.append(f"- Missing rows with ambiguous core-name candidates in existing 3DS DAT `title_id` entries: `{len(ambiguous_matches)}`")
    lines.append("")
    lines.append("### By DAT")
    lines.append("")
    for dat_file, count in sorted(by_dat.items()):
        lines.append(f"- `{dat_file}`: `{count}`")
    lines.append("")
    lines.append("### By Region")
    lines.append("")
    for region, count in by_region.most_common():
        lines.append(f"- `{region}`: `{count}`")
    lines.append("")
    lines.append("### By Name-Match Type")
    lines.append("")
    for match_type, count in sorted(by_match_type.items()):
        lines.append(f"- `{match_type}`: `{count}`")
    lines.append("")
    lines.append("## Sample Unique Core-Name Matches")
    lines.append("")
    if unique_matches:
        for entry in unique_matches[:40]:
            lines.append(
                f"- `{entry.serial}` — {entry.name} -> {entry.dat_core_match_examples}"
            )
    else:
        lines.append("- None")
    lines.append("")
    lines.append("## Files")
    lines.append("")
    lines.append(f"- CSV: `{CSV_PATH.relative_to(ROOT)}`")
    lines.append(f"- This summary: `{MD_PATH.relative_to(ROOT)}`")
    lines.append("")

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    MD_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    code_db = load_code_db()
    title_core_index = load_dat_title_id_core_index()

    all_entries: list[MissingEntry] = []
    for dat_path in DAT_PATHS:
        all_entries.extend(parse_missing_entries(dat_path, code_db, title_core_index))

    write_csv(all_entries)
    write_markdown(all_entries)

    print(f"Wrote {len(all_entries)} rows -> {CSV_PATH}")
    print(f"Wrote summary -> {MD_PATH}")


if __name__ == "__main__":
    main()
