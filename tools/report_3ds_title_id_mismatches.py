#!/usr/bin/env python3
"""Cross-check 3DS DAT title_ids against the 3dsdb.com XML export.

Outputs:
  - server/data/reports/3ds_title_id_mismatches.csv
  - server/data/reports/3ds_title_id_mismatches.md

The goal is to catch rows where an existing DAT ``title_id`` likely points to
the wrong title for the DAT serial/code. The report compares rows against the
same 3dsdb.com XML source used by ``scrape_3dsdb.py``, but it does so with
serial-kind-aware matching:

  - ``CTR-P`` / ``CTR-N`` rows prefer base/eShop XML releases (types 1/4)
  - ``CTR-U`` rows prefer XML update entries (type 3)
  - ``CTR-T`` rows prefer XML demo entries (type 2)
"""

from __future__ import annotations

import csv
import datetime as dt
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.scrape_3dsdb import (
    fetch_releases,
    group_releases_by_code4,
    parse_dat_serial,
    sanitize_name,
    select_expected_release,
)


DATA_DIR = ROOT / "server" / "data"
DATS_DIR = DATA_DIR / "dats"
REPORTS_DIR = DATA_DIR / "reports"

CSV_PATH = REPORTS_DIR / "3ds_title_id_mismatches.csv"
MD_PATH = REPORTS_DIR / "3ds_title_id_mismatches.md"

DAT_PATHS = [
    DATS_DIR / "Nintendo - Nintendo 3DS.dat",
    DATS_DIR / "Nintendo - Nintendo 3DS (Digital).dat",
]

_NAME_RE = re.compile(r'^\s*name\s+"(.+?)"')
_SERIAL_RE = re.compile(r'^\s*serial\s+"(.+?)"')
_TITLE_ID_RE = re.compile(r'^\s*title_id\s+"([0-9A-Fa-f]{16})"')
_TYPE_PRIORITY = {
    "1": 0,
    "4": 1,
    "2": 2,
    "3": 3,
}


@dataclass
class DatRow:
    dat_file: str
    line_no: int
    name: str
    serial: str
    serial_kind: str
    code4: str
    title_id: str


@dataclass
class MismatchRow:
    dat_file: str
    line_no: int
    name: str
    serial: str
    serial_kind: str
    code4: str
    dat_title_id: str
    expected_title_id: str
    expected_name: str
    expected_type: str
    category: str


def parse_dat_rows(dat_path: Path) -> list[DatRow]:
    rows: list[DatRow] = []

    current_name: str | None = None
    current_serial: str | None = None
    current_title_id: str | None = None
    current_line_no = 0

    for line_no, line in enumerate(
        dat_path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
    ):
        stripped = line.strip()
        if stripped == "game (":
            current_name = None
            current_serial = None
            current_title_id = None
            current_line_no = line_no
            continue

        if current_line_no == 0:
            continue

        match = _NAME_RE.match(line)
        if match and current_name is None:
            current_name = match.group(1).strip()
            continue

        match = _SERIAL_RE.match(line)
        if match and current_serial is None:
            current_serial = match.group(1).strip().upper()
            continue

        match = _TITLE_ID_RE.match(line)
        if match:
            current_title_id = match.group(1).strip().upper()
            continue

        if stripped == ")":
            if current_serial and current_title_id:
                serial_kind, code4 = parse_dat_serial(current_serial)
                rows.append(
                    DatRow(
                        dat_file=dat_path.name,
                        line_no=current_line_no,
                        name=current_name or "",
                        serial=current_serial,
                        serial_kind=serial_kind,
                        code4=code4,
                        title_id=current_title_id,
                    )
                )
            current_name = None
            current_serial = None
            current_title_id = None
            current_line_no = 0

    return rows


def _release_rank(release: dict[str, str]) -> tuple[int, int, int, int]:
    raw_name = release.get("name", "").strip()
    clean_name = sanitize_name(raw_name)
    type_code = release.get("type", "").strip()
    try:
        release_id = int(release.get("id", "") or 0)
    except ValueError:
        release_id = 0

    return (
        0 if clean_name else 1,
        _TYPE_PRIORITY.get(type_code, 99),
        1 if "REV" in raw_name.upper() else 0,
        release_id,
    )


def classify_mismatch(row: DatRow, expected_title_id: str) -> str:
    dat_high = row.title_id[:8]
    expected_high = expected_title_id[:8]

    if row.serial_kind in {"P", "N"} and dat_high != expected_high:
        return "base_or_digital_row_has_nonbase_tid"
    if row.serial_kind == "U" and dat_high != expected_high:
        return "update_row_has_nonupdate_tid"
    if row.serial_kind == "T" and dat_high != expected_high:
        return "demo_row_has_nondemo_tid"
    return "same_kind_tid_conflict"


def build_report_rows() -> tuple[list[MismatchRow], Counter[str], int, int]:
    releases = fetch_releases()
    releases_by_code4 = group_releases_by_code4(releases)

    mismatch_rows: list[MismatchRow] = []
    summary = Counter()

    for dat_path in DAT_PATHS:
        for row in parse_dat_rows(dat_path):
            expected = select_expected_release(row.serial_kind, releases_by_code4.get(row.code4, []))
            if expected is None:
                summary["no_xml_candidate"] += 1
                continue

            expected_title_id = expected["titleid"].strip().upper()
            if row.title_id == expected_title_id:
                summary["match"] += 1
                continue

            category = classify_mismatch(row, expected_title_id)
            summary[category] += 1
            mismatch_rows.append(
                MismatchRow(
                    dat_file=row.dat_file,
                    line_no=row.line_no,
                    name=row.name,
                    serial=row.serial,
                    serial_kind=row.serial_kind,
                    code4=row.code4,
                    dat_title_id=row.title_id,
                    expected_title_id=expected_title_id,
                    expected_name=expected.get("name", "").strip(),
                    expected_type=expected.get("type", "").strip(),
                    category=category,
                )
            )

    total_checked = sum(summary.values())
    return mismatch_rows, summary, total_checked, len(releases_by_code4)


def write_csv(rows: list[MismatchRow]) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(CSV_PATH, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "dat_file",
                "line_no",
                "name",
                "serial",
                "serial_kind",
                "code4",
                "dat_title_id",
                "expected_title_id",
                "expected_name",
                "expected_type",
                "category",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row.__dict__)


def write_markdown(
    rows: list[MismatchRow],
    summary: Counter[str],
    total_checked: int,
    total_xml_codes: int,
) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    by_category = Counter(row.category for row in rows)
    by_dat = Counter(row.dat_file for row in rows)
    sample_rows = sorted(rows, key=lambda row: (row.category, row.dat_file, row.line_no))

    lines: list[str] = []
    lines.append("# 3DS `title_id` Mismatch Report")
    lines.append("")
    lines.append(f"Generated: {dt.datetime.now(dt.timezone.utc).isoformat()}")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- DAT rows with `title_id` checked: `{total_checked}`")
    lines.append(f"- XML code groups available: `{total_xml_codes}`")
    lines.append(f"- Exact matches: `{summary['match']}`")
    lines.append(f"- No matching XML candidate for row kind/code: `{summary['no_xml_candidate']}`")
    lines.append(f"- Suspicious mismatches: `{len(rows)}`")
    lines.append("")
    lines.append("### Suspicious Mismatches By Category")
    lines.append("")
    for category, count in by_category.most_common():
        lines.append(f"- `{category}`: `{count}`")
    lines.append("")
    lines.append("### Suspicious Mismatches By DAT")
    lines.append("")
    for dat_file, count in sorted(by_dat.items()):
        lines.append(f"- `{dat_file}`: `{count}`")
    lines.append("")
    lines.append("## Sample Rows")
    lines.append("")
    for row in sample_rows[:40]:
        lines.append(
            f"- `{row.dat_file}:{row.line_no}` `{row.serial}` `{row.dat_title_id}` -> "
            f"`{row.expected_title_id}` ({row.category}) "
            f"| DAT: {row.name} | XML: {row.expected_name}"
        )
    lines.append("")
    lines.append("## Files")
    lines.append("")
    lines.append(f"- CSV: `{CSV_PATH.relative_to(ROOT)}`")
    lines.append(f"- This summary: `{MD_PATH.relative_to(ROOT)}`")
    lines.append("")

    MD_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    rows, summary, total_checked, total_xml_codes = build_report_rows()
    write_csv(rows)
    write_markdown(rows, summary, total_checked, total_xml_codes)
    print(f"Wrote {len(rows)} rows -> {CSV_PATH}")
    print(f"Wrote summary -> {MD_PATH}")


if __name__ == "__main__":
    main()
