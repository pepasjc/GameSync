"""Build a Wii U ``title_id,name,game_code`` map from wiiubrew's Title database.

Why this exists
---------------
A Wii U save is keyed by its 16-hex title id, and nothing the server ships can
name it: the Wii U DAT is keyed by 4-char product codes with no title-id
column.  Normally the console (or a Cemu install) supplies the name from the
title's meta.xml — but when neither is reachable, wiiubrew's community-
maintained Title database has the same mapping:

    |-
    | 00050000-10101D00
    | New SUPER MARIO BROS. U
    | WUP-P-ARPE
    ...

Only the ``00050000`` (game application) section is read; DLC (0005000C) and
update (0005000E) rows repeat the same names against ids that hold no saves.

The product code is emitted alongside the name so the importer can prefer the
server's own DAT name — the wiki writes titles as the console does
("SUPER MARIO 3D WORLD"), while the DAT uses the catalogue's casing.

Usage
-----
    # Whole database
    python tools/wiiu_titles_from_wiiubrew.py -o wiiu_names.csv

    # Only the ids you actually need (a --dump-unnamed skeleton)
    python tools/wiiu_titles_from_wiiubrew.py --ids wiiu_unnamed.csv -o wiiu_names.csv

    # Offline, from a previously downloaded copy
    python tools/wiiu_titles_from_wiiubrew.py --wikitext titles.wiki -o wiiu_names.csv

Then feed the result to the server:

    uv run python migrate_wiiu_names.py --map wiiu_names.csv --apply
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import urllib.request
from pathlib import Path

SOURCE_URL = "https://wiiubrew.org/w/index.php?title=Title_database&action=raw"

# Section the game applications live in.  The heading is a wiki anchor like
# "== 00050000: Game Applications ==" — match on the id so a reworded heading
# doesn't silently yield zero rows.
_SECTION_RE = re.compile(r"^==+\s*(0005[0-9A-Fa-f]{4})\b", re.MULTILINE)

_ROW_ID_RE = re.compile(r"^\|\s*(00050000)-([0-9A-Fa-f]{8})\s*$")
_PRODUCT_CODE_RE = re.compile(r"^\|\s*(WUP-[A-Z]-([A-Z0-9]{4}))\s*$")

# "Newスーパーマリオブラザーズ U (New SUPER MARIO BROS. U)" — Japanese rows put
# the romanised/English title in trailing parentheses.  Prefer that: a name
# nobody on the client side can render is no better than the raw hex id.
_PAREN_ENGLISH_RE = re.compile(r"^(.*?)\s*\(([^()]+)\)\s*$")

_SKIP_NAMES = {"todo", "unknown", "?", "-", ""}


def fetch_wikitext(url: str = SOURCE_URL) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "savesync-title-lookup"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _strip_wiki_markup(value: str) -> str:
    """Reduce a table cell to plain text."""
    text = value.strip()
    text = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", text)  # [[a|b]] -> b
    text = re.sub(r"\[[^\s\]]+\s+([^\]]*)\]", r"\1", text)  # [url label] -> label
    text = re.sub(r"'{2,}", "", text)  # bold / italic
    text = re.sub(r"<[^>]+>", "", text)  # <ref>, <br/>
    text = re.sub(r"\{\{[^}]*\}\}", "", text)  # templates
    return re.sub(r"\s+", " ", text).strip()


def _prefer_ascii_name(name: str) -> str:
    """Take the parenthesised English title when the main one is not Latin."""
    m = _PAREN_ENGLISH_RE.match(name)
    if not m:
        return name
    main, paren = m.group(1).strip(), m.group(2).strip()
    if not main:
        return paren
    main_ascii = sum(1 for c in main if ord(c) < 128) / len(main)
    if main_ascii < 0.6 and paren:
        return paren
    return name


def _game_section(wikitext: str) -> str:
    """Slice out the 00050000 section, so DLC/update rows can't leak in."""
    sections = list(_SECTION_RE.finditer(wikitext))
    for i, m in enumerate(sections):
        if m.group(1).upper() != "00050000":
            continue
        end = sections[i + 1].start() if i + 1 < len(sections) else len(wikitext)
        return wikitext[m.end() : end]
    # No recognisable heading — fall back to the whole page; the row regex only
    # accepts 00050000 ids anyway.
    return wikitext


def parse_titles(wikitext: str) -> dict[str, tuple[str, str]]:
    """Return ``title_id -> (name, game_code)`` for game application titles.

    A title id can appear more than once (re-releases, lotcheck rows); the
    first row carrying a real name wins, and a later row may still fill in a
    missing product code.
    """
    result: dict[str, tuple[str, str]] = {}
    lines = _game_section(wikitext).splitlines()

    for idx, line in enumerate(lines):
        m = _ROW_ID_RE.match(line.strip())
        if not m:
            continue
        title_id = (m.group(1) + m.group(2)).upper()

        # The name is the next cell; the product code follows within a couple
        # of cells.  Scan forward until the row ends ("|-" or a new row id).
        name = ""
        code = ""
        for follow in lines[idx + 1 : idx + 8]:
            stripped = follow.strip()
            if stripped.startswith("|-") or _ROW_ID_RE.match(stripped):
                break
            if not stripped.startswith("|"):
                continue
            pc = _PRODUCT_CODE_RE.match(stripped)
            if pc:
                code = pc.group(2).upper()
                continue
            if not name:
                candidate = _strip_wiki_markup(stripped.lstrip("|"))
                if candidate.lower() not in _SKIP_NAMES:
                    name = _prefer_ascii_name(candidate)

        if not name and not code:
            continue

        prev_name, prev_code = result.get(title_id, ("", ""))
        result[title_id] = (prev_name or name, prev_code or code)

    return {tid: v for tid, v in result.items() if v[0]}


def read_ids(path: Path) -> list[str]:
    """Read the title ids from a --dump-unnamed skeleton (or any CSV/text)."""
    ids: list[str] = []
    for row in csv.reader(path.read_text(encoding="utf-8").splitlines()):
        if not row:
            continue
        tid = row[0].strip().upper()
        if re.fullmatch(r"0005[0-9A-F]{12}", tid):
            ids.append(tid)
    return ids


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-o", "--output", default="wiiu_names.csv", help="CSV to write")
    parser.add_argument(
        "--ids",
        metavar="FILE",
        help="Only emit these title ids (a migrate_wiiu_names.py "
        "--dump-unnamed skeleton works as-is).",
    )
    parser.add_argument(
        "--wikitext",
        metavar="FILE",
        help="Parse a local copy instead of downloading.",
    )
    args = parser.parse_args()

    if args.wikitext:
        wikitext = Path(args.wikitext).read_text(encoding="utf-8", errors="replace")
    else:
        print(f"Downloading {SOURCE_URL}")
        wikitext = fetch_wikitext()

    titles = parse_titles(wikitext)
    print(f"Parsed {len(titles)} game titles from the database")

    wanted = read_ids(Path(args.ids)) if args.ids else sorted(titles)
    missing = [tid for tid in wanted if tid not in titles]

    out = Path(args.output)
    with open(out, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["title_id", "name", "game_code"])
        for tid in wanted:
            name, code = titles.get(tid, ("", ""))
            writer.writerow([tid, name, code])

    print(f"Wrote {len(wanted) - len(missing)}/{len(wanted)} named title(s) to {out}")
    if missing:
        print(f"{len(missing)} not in the database (left blank):")
        for tid in missing:
            print(f"  {tid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
