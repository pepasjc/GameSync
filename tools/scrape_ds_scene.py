#!/usr/bin/env python3
"""Scrape ds-scene.net release database to build archive_name -> game_code mapping.

This maps scene release filenames (e.g. "xpa-bbme") to 4-char game codes (e.g. "BBME")
by extracting the game code from the archive name and looking up the game name.

Output: tools/ds_releases.txt with lines of "archive_name,game_code,game_name"
"""

import re
import time
import urllib.request
from pathlib import Path


def fetch_page(page_num: int, max_retries: int = 3) -> str:
    """Fetch a page from ds-scene.net with retries."""
    url = f"https://www.ds-scene.net/?s=releases&p={page_num}"
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/120.0.0.0 Safari/537.36",
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"  Retry {attempt + 1} for page {page_num}: {e}")
                time.sleep(2)
            else:
                print(f"  Failed page {page_num}: {e}")
                return ""


def parse_page(html: str) -> list[dict]:
    """Parse a ds-scene.net release page using regex.

    Each entry has romlistTxt cells. Key patterns:
    - Number: class="romlistTxt"><b>6464</b>
    - Name: <a href="/?s=viewtopic&nid=..."><b>Game Name</b></a>
    - Archive: class="romlistTxt" title="Full_Name"><b>cat-tbgp</b>
    """
    entries = []

    # Extract all romlistTxt cells content (between <div...class="romlistTxt"...> and </div>)
    cells = re.findall(
        r'class="romlistTxt"[^>]*>(.*?)</div>',
        html, re.DOTALL
    )

    # Group cells into rows of 9 (icon, number, name, region, group, size, save, wifi, archive, score, nfo)
    # But some cells may be extra. Instead, identify entries by the number pattern
    # and collect surrounding cells.

    # Alternative: find all entries directly with targeted patterns
    # Find release numbers
    numbers = re.findall(
        r'class="romlistTxt"><b>(\d{1,5})</b>',
        html
    )

    # Find game names (in <a> tags with viewtopic links)
    names = re.findall(
        r'viewtopic&nid=\d+"[^>]*><b>([^<]+)</b></a>',
        html
    )

    # Find archive names (romlistTxt with title attribute containing the full release name)
    archives_raw = re.findall(
        r'class="romlistTxt"\s+title="[^"]*"><b>([^<]+)</b>',
        html
    )
    # Strip .zip suffix from older entries
    archives = [a.removesuffix(".zip") for a in archives_raw]

    # All three lists should have the same length (one per entry)
    count = min(len(numbers), len(names), len(archives))
    for i in range(count):
        entries.append({
            "number": numbers[i],
            "name": names[i],
            "archive": archives[i],
        })

    return entries


def extract_game_code(archive_name: str) -> str:
    """Extract 4-char game code from scene archive name.

    Scene naming convention: group-gamecode (e.g. "xpa-bbme" -> "BBME")
    The game code is typically the last 4 characters after the last hyphen.
    """
    # Remove any trailing numbers like ".1" (backup indicators)
    clean = re.sub(r"\.\d+$", "", archive_name)

    # Split by hyphen, take last part
    parts = clean.split("-")
    if len(parts) >= 2:
        candidate = parts[-1].upper()
        # NDS game codes are exactly 4 alphanumeric chars
        if len(candidate) == 4 and candidate.isalnum():
            return candidate

    return ""


def main():
    total_pages = 325
    all_entries = []

    print(f"Scraping ds-scene.net ({total_pages} pages)...")

    for page in range(1, total_pages + 1):
        html = fetch_page(page)
        if not html:
            continue

        entries = parse_page(html)
        all_entries.extend(entries)

        if page % 10 == 0 or page == 1:
            print(f"  Page {page}/{total_pages} - {len(all_entries)} entries so far")

        # Be polite to the server
        time.sleep(0.5)

    print(f"\nTotal entries scraped: {len(all_entries)}")

    # Build output: archive_name,game_code,game_name
    output_path = Path(__file__).parent / "ds_releases.txt"
    matched = 0
    with open(output_path, "w", encoding="utf-8") as f:
        for entry in all_entries:
            archive = entry["archive"]
            name = entry.get("name", "")
            code = extract_game_code(archive)
            if code:
                f.write(f"{archive},{code},{name}\n")
                matched += 1
            else:
                f.write(f"{archive},,{name}\n")

    print(f"Wrote {len(all_entries)} entries to {output_path}")
    print(f"  {matched} with extracted game codes")
    print(f"  {len(all_entries) - matched} without game code")


if __name__ == "__main__":
    main()
