#!/usr/bin/env python3
"""Scrape Xbox (original) disc -> Title ID mapping from dbox.tools.

For each disc in the dbox database, derives the 8-char hex Title ID from the
disc's XMID. The Title ID is the directory name used under ``UDATA\<TitleID>\``
on the Xbox HDD where save files live.

XMID format (8 ASCII chars stamped in the disc header):
    PPNNNVVR
        PP  = 2-char publisher code (e.g. ``EA``, ``MS``)
        NNN = 3-digit game number (decimal)
        VV  = version
        R   = region

Title ID format (32-bit, shown as 8 hex chars in the XBE certificate):
    [ascii_hex(P1)][ascii_hex(P2)][game_number_as_4_hex_digits]

    e.g. xmid ``EA01301A`` -> publisher ``EA`` + game 13
         title_id = 0x45 0x41 0x000D = ``4541000D``

The script writes two artefacts under ``server/data/``:
  * ``xbox_titleids.json`` -- raw scrape: list of {disc_id, datname, xmid, title_id, ...}
  * ``xbox_titleid_map.txt`` -- ``TITLE_ID,Name`` lookup similar to ``3dstdb.txt``

Then it injects ``title_id "XXXXXXXX"`` lines into ``Microsoft - Xbox.dat``
under each matching ``game ( ... )`` block, keyed by the ``name`` field
(== ``redump_datname`` in the dbox API).

Usage:
    python tools/scrape_xbox_titleids.py
    python tools/scrape_xbox_titleids.py --no-inject       # only refresh JSON/txt
    python tools/scrape_xbox_titleids.py --verify-sample 25 # API-verify N entries
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API_BASE = "https://dbox.tools/api"
USER_AGENT = "3dssync-scraper/1.0 (+https://github.com/pepasjc/3dssync)"

DATA_DIR = Path(__file__).parent.parent / "server" / "data"
DATS_DIR = DATA_DIR / "dats"
XBOX_DAT = DATS_DIR / "Microsoft - Xbox.dat"
RAW_JSON = DATA_DIR / "xbox_titleids.json"
LOOKUP_TXT = DATA_DIR / "xbox_titleid_map.txt"

PAGE_SIZE = 100


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _http_get_json(url: str, retries: int = 3, backoff: float = 1.5) -> dict:
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read())
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last_err = e
            time.sleep(backoff ** attempt)
    raise RuntimeError(f"GET {url} failed after {retries} retries: {last_err}")


def fetch_all_xbox_discs() -> list[dict]:
    """Page through /api/discs/?system=XBOX and collect all entries."""
    all_items: list[dict] = []
    offset = 0
    print(f"  Fetching Xbox discs from {API_BASE}/discs/...")
    while True:
        url = f"{API_BASE}/discs/?system=XBOX&limit={PAGE_SIZE}&offset={offset}"
        data = _http_get_json(url)
        items = data.get("items", [])
        count = data.get("count", 0)
        all_items.extend(items)
        print(f"    page offset={offset:5d}  got {len(items):3d}  total {len(all_items):4d} / {count}")
        if not items or len(all_items) >= count:
            break
        offset += PAGE_SIZE
    return all_items


def fetch_title_id(title_id: str) -> dict | None:
    """Lookup a single title_id (used for spot-verification)."""
    try:
        return _http_get_json(f"{API_BASE}/title_ids/{title_id}")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# XMID -> Title ID conversion
# ---------------------------------------------------------------------------

_XMID_RE = re.compile(r"^([A-Z0-9]{2})(\d{3})[A-Z0-9]{3}$")


def xmid_to_title_id(xmid: str) -> str | None:
    """Convert an 8-char XMID to the 8-hex-char Xbox Title ID.

    Returns ``None`` for unparseable XMIDs (rare malformed entries).
    """
    if not xmid or len(xmid) != 8:
        return None
    xmid = xmid.upper()
    m = _XMID_RE.match(xmid)
    if not m:
        return None
    pub, num = m.group(1), m.group(2)
    pub_hex = f"{ord(pub[0]):02X}{ord(pub[1]):02X}"
    num_hex = f"{int(num):04X}"
    return pub_hex + num_hex


# ---------------------------------------------------------------------------
# DAT injection (style copied from scrape_3dsdb.py)
# ---------------------------------------------------------------------------

_DAT_NAME_LINE_RE = re.compile(r'^\s*name\s+"(.+?)"')
_DAT_SERIAL_LINE_RE = re.compile(r'^\s*serial\s+"(.+?)"')
_DAT_TITLE_ID_LINE_RE = re.compile(r'^\s*title_id\s+"([0-9A-Fa-f]{8})"')


def inject_title_ids_into_dat(
    dat_path: Path,
    name_to_title_id: dict[str, str],
) -> tuple[int, int, int]:
    """Insert/correct ``title_id "XXXXXXXX"`` lines per game block.

    Match key: the game block's ``name`` field (matches dbox ``redump_datname``).

    Returns ``(inserted, corrected, missing)``.
    """
    if not dat_path.exists():
        print(f"  Skipping missing DAT: {dat_path}")
        return 0, 0, 0

    inserted = 0
    corrected = 0
    missing = 0
    output_lines: list[str] = []
    current_name: str | None = None
    current_serial: str | None = None
    existing_title_id: str | None = None
    existing_title_id_line_idx: int | None = None
    block_indent = "\t"

    with open(dat_path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            stripped = line.strip()

            m = _DAT_NAME_LINE_RE.match(line)
            if m:
                current_name = m.group(1).strip()
                current_serial = None
                existing_title_id = None
                existing_title_id_line_idx = None
                # detect indent (tab vs spaces) from this line
                if line.startswith("\t"):
                    block_indent = "\t"
                else:
                    leading = len(line) - len(line.lstrip(" "))
                    block_indent = " " * leading if leading else "\t"
                output_lines.append(line)
                continue

            m = _DAT_SERIAL_LINE_RE.match(line)
            if m and current_serial is None:
                current_serial = m.group(1).strip()
                output_lines.append(line)
                continue

            m = _DAT_TITLE_ID_LINE_RE.match(line)
            if m:
                existing_title_id = m.group(1).upper()
                existing_title_id_line_idx = len(output_lines)
                output_lines.append(line)
                continue

            if stripped == ")" and current_name is not None:
                expected = name_to_title_id.get(current_name)
                if expected is None:
                    if existing_title_id is None:
                        missing += 1
                else:
                    expected = expected.upper()
                    if existing_title_id is None:
                        # insert before the closing paren
                        output_lines.append(f'{block_indent}title_id "{expected}"\n')
                        inserted += 1
                    elif existing_title_id != expected:
                        output_lines[existing_title_id_line_idx] = (
                            f'{block_indent}title_id "{expected}"\n'
                        )
                        corrected += 1
                output_lines.append(line)
                current_name = None
                current_serial = None
                existing_title_id = None
                existing_title_id_line_idx = None
                continue

            output_lines.append(line)

    dat_path.write_text("".join(output_lines), encoding="utf-8")
    print(
        f"  Rewrote {dat_path.name}: "
        f"inserted {inserted:,}, corrected {corrected:,}, missing-mapping {missing:,}"
    )
    return inserted, corrected, missing


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-inject",
        action="store_true",
        help="Refresh JSON/txt outputs but don't touch the .dat",
    )
    parser.add_argument(
        "--verify-sample",
        type=int,
        default=0,
        metavar="N",
        help="API-verify the first N derived title_ids (slow, optional)",
    )
    parser.add_argument(
        "--from-cache",
        action="store_true",
        help="Reuse existing xbox_titleids.json instead of re-scraping",
    )
    args = parser.parse_args()

    if args.from_cache and RAW_JSON.exists():
        print(f"Loading cached scrape from {RAW_JSON}...")
        discs = json.loads(RAW_JSON.read_text(encoding="utf-8"))
    else:
        print("Scraping Xbox disc database from dbox.tools...")
        try:
            discs = fetch_all_xbox_discs()
        except Exception as e:
            print(f"ERROR: scrape failed: {e}", file=sys.stderr)
            sys.exit(1)

    print(f"\nGot {len(discs):,} Xbox disc entries.")

    # Derive title_id for each disc
    enriched: list[dict] = []
    no_xmid = 0
    bad_xmid = 0
    for d in discs:
        xmid = (d.get("xmid") or "").strip().upper()
        if not xmid:
            no_xmid += 1
            tid = None
        else:
            tid = xmid_to_title_id(xmid)
            if tid is None:
                bad_xmid += 1
        enriched.append({
            "disc_id": d.get("id"),
            "redump_id": d.get("redump_id"),
            "datname": d.get("redump_datname"),
            "name": d.get("redump_name"),
            "region": d.get("redump_region"),
            "edition": d.get("redump_edition"),
            "xmid": xmid or None,
            "title_id": tid,
        })

    print(f"  Derived title_id for {sum(1 for e in enriched if e['title_id']):,} entries")
    print(f"  Discs without xmid:   {no_xmid:,}")
    print(f"  Unparseable xmids:    {bad_xmid:,}")

    # Optional API spot-check
    if args.verify_sample > 0:
        sample = [e for e in enriched if e["title_id"]][: args.verify_sample]
        print(f"\nVerifying {len(sample)} derived title_ids against /api/title_ids/...")
        ok = 0
        for e in sample:
            res = fetch_title_id(e["title_id"])
            if res and res.get("title_id", "").upper() == e["title_id"]:
                ok += 1
            else:
                print(f"  MISS  {e['title_id']}  derived from xmid={e['xmid']}  ({e['datname']})")
        print(f"  Verified: {ok}/{len(sample)} present in dbox title_ids table")

    # Persist raw enriched scrape
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RAW_JSON.write_text(json.dumps(enriched, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  Wrote {RAW_JSON}")

    # Build TITLE_ID,Name lookup (one entry per unique title_id, prefer USA->World->any)
    region_priority = {"USA": 0, "World": 1, "Europe": 2, "Japan": 3, "Asia": 4}
    best: dict[str, tuple[int, str]] = {}
    for e in enriched:
        tid = e["title_id"]
        if not tid:
            continue
        name = (e.get("name") or e.get("datname") or "").strip()
        if not name:
            continue
        rank = region_priority.get(e.get("region") or "", 99)
        prev = best.get(tid)
        if prev is None or rank < prev[0]:
            best[tid] = (rank, name)
    LOOKUP_TXT.write_text(
        "".join(f"{tid},{name.replace(',', ';')}\n" for tid, (_, name) in sorted(best.items())),
        encoding="utf-8",
    )
    print(f"  Wrote {LOOKUP_TXT}  ({len(best):,} unique title IDs)")

    if args.no_inject:
        print("\n--no-inject given; skipping DAT rewrite.")
        return

    # Build name -> title_id map for DAT injection (datname is unique per disc)
    name_to_title_id: dict[str, str] = {}
    for e in enriched:
        if not e["title_id"] or not e["datname"]:
            continue
        # If multiple discs share a datname (shouldn't happen, but defensive),
        # last-write-wins is fine.
        name_to_title_id[e["datname"]] = e["title_id"]

    print(f"\nInjecting title_id lines into {XBOX_DAT}...")
    inject_title_ids_into_dat(XBOX_DAT, name_to_title_id)
    print("\nDone.")


if __name__ == "__main__":
    main()
