#!/usr/bin/env python3
"""Download and merge the 3dsdb.com XML export.

Generates the 4-char 3DS code database and injects ``title_id`` lines into the
3DS DATs:
  3dstdb.txt                      - 4-char game code -> Name
  dats/Nintendo - Nintendo 3DS.dat
  dats/Nintendo - Nintendo 3DS (Digital).dat

Source of truth:
  https://3dsdb.com/xml.php

Usage:
    python scrape_3dsdb.py
"""

from __future__ import annotations

import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

XML_URL = "https://3dsdb.com/xml.php"

# Strip parenthetical groups that contain non-ASCII characters e.g. (マリオカート7)
_NON_ASCII_PARENS_RE = re.compile(r"\s*\([^)]*[^\x00-\x7F][^)]*\)")

DATA_DIR = Path(__file__).parent.parent / "server" / "data"
DATS_DIR = DATA_DIR / "dats"

# DAT product code formats: CTR-P-ABCD, KTR-N-ABCD, TWL-P-ABCD, etc.
_DAT_PRODUCT_CODE_RE = re.compile(r"^(?:CTR|KTR|TWL|SPR)-[A-Z]-([A-Z0-9]{4})$")

# 3dsdb.com XML serial format: CTR-ABCD, KTR-ABCD, etc.
_SITE_SERIAL_RE = re.compile(r"^(?:CTR|KTR|TWL|SPR)-([A-Z0-9]{4})$")
_DAT_SERIAL_CODE_RE = re.compile(r"^(?:CTR|KTR|TWL|SPR)-([A-Z])-([A-Z0-9]{4})$")

_TYPE_PRIORITY = {
    # 3DS retail / card releases
    "1": 0,
    # eShop / digital releases
    "4": 1,
    # demos
    "2": 2,
    # updates
    "3": 3,
}


def fetch_releases() -> list[dict[str, str]]:
    print(f"  Fetching {XML_URL} ...", end=" ", flush=True)
    req = urllib.request.Request(XML_URL, headers={"User-Agent": "3dssync-scraper/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        root = ET.fromstring(resp.read())

    releases: list[dict[str, str]] = []
    for release in root.findall("release"):
        entry = {child.tag: (child.text or "").strip() for child in release}
        if entry:
            releases.append(entry)

    print(f"{len(releases)} entries")
    return releases


def sanitize_name(name: str) -> str:
    """Remove non-ASCII parenthetical groups, then strip to ASCII-safe string."""
    name = _NON_ASCII_PARENS_RE.sub("", name)
    # Drop any remaining non-ASCII characters (e.g. bare Japanese with no parens)
    name = name.encode("ascii", errors="ignore").decode("ascii")
    return name.strip()


def extract_code4(serial: str) -> str:
    serial = serial.upper().strip()
    dat_match = _DAT_PRODUCT_CODE_RE.match(serial)
    if dat_match:
        return dat_match.group(1)

    site_match = _SITE_SERIAL_RE.match(serial)
    if site_match:
        return site_match.group(1)

    return ""


def parse_dat_serial(serial: str) -> tuple[str, str]:
    serial = serial.upper().strip()
    match = _DAT_SERIAL_CODE_RE.match(serial)
    if not match:
        return "", ""
    return match.group(1), match.group(2)


def _entry_rank(entry: dict[str, str]) -> tuple[int, int, int, int]:
    raw_name = entry.get("name", "").strip()
    clean_name = sanitize_name(raw_name)
    type_code = entry.get("type", "").strip()
    try:
        release_id = int(entry.get("id", "") or 0)
    except ValueError:
        release_id = 0

    # Prefer:
    # 1. entries with a usable ASCII name
    # 2. full/digital releases over demos/updates
    # 3. non-revision labels over scene rev labels
    # 4. earlier release ids as a stable final tiebreaker
    return (
        0 if clean_name else 1,
        _TYPE_PRIORITY.get(type_code, 99),
        1 if "REV" in raw_name.upper() else 0,
        release_id,
    )


def group_releases_by_code4(
    releases: list[dict[str, str]],
) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for entry in releases:
        tid = entry.get("titleid", "").upper().strip()
        code4 = extract_code4(entry.get("serial", ""))
        if not code4:
            continue
        if len(tid) != 16 or any(c not in "0123456789ABCDEF" for c in tid):
            continue
        grouped[code4].append(entry)
    return grouped


def select_expected_release(
    serial_kind: str,
    code4_releases: list[dict[str, str]],
) -> dict[str, str] | None:
    if serial_kind == "U":
        candidates = [entry for entry in code4_releases if entry.get("type") == "3"]
    elif serial_kind == "T":
        candidates = [entry for entry in code4_releases if entry.get("type") == "2"]
    else:
        candidates = [
            entry for entry in code4_releases if entry.get("type") in {"1", "4"}
        ]

    if not candidates:
        return None

    return sorted(candidates, key=_entry_rank)[0]


def build_mappings(
    releases: list[dict[str, str]],
) -> tuple[dict[str, str], dict[str, str], dict[str, str], dict[str, str]]:
    """Return (title_id_map, game_code_map, serial_title_id_map, code4_title_id_map)."""
    title_ids: dict[str, str] = {}
    title_id_ranks: dict[str, tuple[int, int, int, int]] = {}

    game_codes: dict[str, str] = {}
    game_code_ranks: dict[str, tuple[int, int, int, int]] = {}

    serial_title_ids: dict[str, str] = {}
    serial_ranks: dict[str, tuple[int, int, int, int]] = {}

    code4_title_ids: dict[str, str] = {}
    code4_ranks: dict[str, tuple[int, int, int, int]] = {}

    for entry in releases:
        tid = entry.get("titleid", "").upper().strip()
        serial = entry.get("serial", "").upper().strip()
        code4 = extract_code4(serial)
        name = sanitize_name(entry.get("name", "").strip())
        rank = _entry_rank(entry)

        valid_tid = tid and len(tid) == 16 and all(c in "0123456789ABCDEF" for c in tid)
        if not name:
            continue

        if valid_tid:
            existing = title_id_ranks.get(tid)
            if existing is None or rank < existing:
                title_ids[tid] = name
                title_id_ranks[tid] = rank

        if code4:
            existing = game_code_ranks.get(code4)
            if existing is None or rank < existing:
                game_codes[code4] = name
                game_code_ranks[code4] = rank

        if serial and valid_tid:
            existing = serial_ranks.get(serial)
            if existing is None or rank < existing:
                serial_title_ids[serial] = tid
                serial_ranks[serial] = rank

        if code4 and valid_tid:
            existing = code4_ranks.get(code4)
            if existing is None or rank < existing:
                code4_title_ids[code4] = tid
                code4_ranks[code4] = rank

    return title_ids, game_codes, serial_title_ids, code4_title_ids


def write_db(mapping: dict[str, str], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"{key},{val.replace(',', ';')}\n"
        for key, val in sorted(mapping.items())
    ]
    output_path.write_text("".join(lines), encoding="utf-8")
    print(f"  Wrote {len(lines):,} entries -> {output_path}")


_DAT_NAME_LINE_RE = re.compile(r'^\s*name\s+"(.+?)"')
_DAT_SERIAL_LINE_RE = re.compile(r'^\s*serial\s+"(.+?)"')
_DAT_TITLE_ID_LINE_RE = re.compile(r'^\s*title_id\s+"([0-9A-Fa-f]{16})"')


def rewrite_title_ids_in_dat(
    source_dat: Path,
    releases_by_code4: dict[str, list[dict[str, str]]],
) -> tuple[int, int]:
    """Inject or correct ``title_id`` lines directly in an existing 3DS DAT."""
    if not source_dat.exists():
        print(f"  Skipping missing DAT: {source_dat}")
        return 0, 0

    inserted = 0
    corrected = 0
    output_lines: list[str] = []
    current_name: str | None = None
    current_serial: str | None = None
    current_title_id: str | None = None
    current_title_id_line: str | None = None

    with open(source_dat, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            stripped = line.strip()
            m = _DAT_NAME_LINE_RE.match(line)
            if m:
                current_name = m.group(1).strip()
                current_serial = None
                current_title_id = None
                current_title_id_line = None
                output_lines.append(line)
                continue

            m = _DAT_SERIAL_LINE_RE.match(line)
            if m and current_serial is None:
                current_serial = m.group(1).strip().upper()
                output_lines.append(line)
                continue

            m = _DAT_TITLE_ID_LINE_RE.match(line)
            if m:
                current_title_id = m.group(1).strip().upper()
                current_title_id_line = line
                continue

            if stripped == ")" and current_name and current_serial:
                serial_kind, code4 = parse_dat_serial(current_serial)
                expected_release = select_expected_release(
                    serial_kind,
                    releases_by_code4.get(code4, []),
                )
                expected_title_id = (
                    expected_release.get("titleid", "").upper().strip()
                    if expected_release is not None
                    else ""
                )

                if current_title_id_line is not None:
                    title_id_to_write = current_title_id
                    if expected_title_id and expected_title_id != current_title_id:
                        title_id_to_write = expected_title_id
                        corrected += 1
                    output_lines.append(f'\ttitle_id "{title_id_to_write}"\n')
                elif expected_title_id:
                    output_lines.append(f'\ttitle_id "{expected_title_id}"\n')
                    inserted += 1
                output_lines.append(line)
                current_name = None
                current_serial = None
                current_title_id = None
                current_title_id_line = None
                continue

            output_lines.append(line)

    source_dat.write_text("".join(output_lines), encoding="utf-8")
    print(
        f"  Rewrote title-id lines -> {source_dat} "
        f"(inserted {inserted:,}, corrected {corrected:,})"
    )
    return inserted, corrected


def main() -> None:
    print("Downloading 3dsdb.com XML export...")
    try:
        releases = fetch_releases()
    except Exception as e:
        print(f"ERROR: failed to fetch {XML_URL}: {e}")
        sys.exit(1)

    if not releases:
        print("ERROR: No releases fetched.")
        sys.exit(1)

    print("\nBuilding lookup tables...")
    title_ids, game_codes, serial_title_ids, code4_title_ids = build_mappings(releases)
    releases_by_code4 = group_releases_by_code4(releases)
    print(f"  {len(title_ids):,} unique TitleIDs")
    print(f"  {len(game_codes):,} unique 4-char game codes")
    print(f"  {len(serial_title_ids):,} unique XML serial -> TitleID mappings")
    print(f"  {len(code4_title_ids):,} preferred 4-char code -> TitleID mappings")

    print("\nWriting databases...")
    write_db(game_codes, DATA_DIR / "3dstdb.txt")

    print("\nUpdating 3DS DATs...")
    rewrite_title_ids_in_dat(
        DATS_DIR / "Nintendo - Nintendo 3DS.dat",
        releases_by_code4,
    )
    rewrite_title_ids_in_dat(
        DATS_DIR / "Nintendo - Nintendo 3DS (Digital).dat",
        releases_by_code4,
    )
    print("\nDone. Run this script again to refresh the databases.")


if __name__ == "__main__":
    main()
