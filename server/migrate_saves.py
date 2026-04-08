#!/usr/bin/env python3
"""migrate_saves.py — one-shot migration script for the GameSync save directory.

What it does
============
1. Stamps `platform` and `name` into every existing metadata.json that is
   missing them (or where `name` is still just the title_id).

2. For PSP / Vita saves that were uploaded while the broken per-console
   subfolder layout was active, moves the save files from the nested
   subfolder (e.g. saves/ULUS12345/psp/) up to the flat layout
   (saves/ULUS12345/), merging history if needed.

Usage
=====
    cd /path/to/GameSync/server
    python3 migrate_saves.py [--saves-dir PATH] [--dry-run]

The script is idempotent: running it multiple times is safe.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Minimal inline copies of the detection / lookup logic so this script can
# be run standalone without starting the full FastAPI app.
# ---------------------------------------------------------------------------

import re

_PSP_PREFIX_RE = re.compile(r"^[A-Z]{4}\d{5}")
_VITA_CODE_RE  = re.compile(r"^PCS[A-Z]\d{5}$")
_NDS_HIGH_PREFIXES = {"00048"}

_3ds_names: dict[str, str] = {}
_ds_names:  dict[str, str] = {}
_psp_names: dict[str, str] = {}
_psx_names: dict[str, str] = {}
_vita_names: dict[str, str] = {}


def _load_db(path: Path) -> None:
    name = path.name.lower()
    if "vita" in name:
        target = _vita_names
    elif "psx" in name:
        target = _psx_names
    elif "psp" in name:
        target = _psp_names
    elif "ds" in name and "3ds" not in name:
        target = _ds_names
    else:
        target = _3ds_names

    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or "," not in line:
                continue
            code, _, game_name = line.partition(",")
            code = code.strip().upper()
            game_name = game_name.strip()
            if code and game_name:
                target[code] = game_name


def _detect_platform(title_id: str) -> str:
    tid = title_id.upper().strip()
    if len(tid) == 16 and all(c in "0123456789ABCDEF" for c in tid):
        return "NDS" if tid[:5] in _NDS_HIGH_PREFIXES else "3DS"
    if _VITA_CODE_RE.match(tid) or (len(tid) >= 3 and tid[:3] == "PCS"):
        return "VITA"
    if _PSP_PREFIX_RE.match(tid):
        base = tid[:9]
        return "PSX" if base in _psx_names else "PSP"
    return "NDS"


def _lookup_name(title_id: str) -> str:
    tid = title_id.upper().strip()

    if _VITA_CODE_RE.match(tid):
        return _vita_names.get(tid, title_id)

    if _PSP_PREFIX_RE.match(tid):
        base = tid[:9]
        return _psx_names.get(base) or _psp_names.get(base) or title_id

    # 3DS / DS: extract 4-char game code from the 16-char hex title ID
    # The game code is bytes 4-7 of the title ID (chars 8-11 of the hex string,
    # then decoded as ASCII). But the DB is keyed by the 4-char ASCII code,
    # which corresponds to positions 8-15 in the hex string as two bytes each.
    # Actually the DB uses the last 4 chars of the 8-char "unique ID" portion.
    # Simplest: extract chars at positions 8:12 and interpret as the 4-char code
    # using the low byte of each of the 4 hex-pairs.
    if len(tid) == 16:
        # title ID layout: TTTTTTTT UUUUUUUU (type / unique-id, each 8 hex chars)
        # The 4-char game code is the ASCII representation of bytes 4-7 (0-indexed)
        # of the full 8-byte title_id — i.e. hex chars 8-15 decoded as 4 bytes.
        try:
            raw = bytes.fromhex(tid[8:16])
            # First 4 bytes of the unique-id are the game code in ASCII
            game_code = raw[:4].decode("ascii", errors="replace").upper()
            name = _3ds_names.get(game_code) or _ds_names.get(game_code)
            if name:
                return name
        except Exception:
            pass

    return title_id


# ---------------------------------------------------------------------------
# Known per-console subdir names that were created by the broken layout
# ---------------------------------------------------------------------------
_PS_SUBDIRS = {"psp", "vita"}  # canonical names used in the broken layout


def _is_ps_subdir(name: str) -> bool:
    """True if a subdirectory name looks like a broken per-console slot."""
    n = name.lower()
    if n in _PS_SUBDIRS:
        return True
    # Also catch psp_XXXXXXXX / vita_XXXXXXXX style IDs
    if n.startswith("psp_") or n.startswith("vita_"):
        return True
    return False


def migrate(saves_dir: Path, dry_run: bool = False) -> None:
    if not saves_dir.exists():
        print(f"Saves directory not found: {saves_dir}")
        sys.exit(1)

    changed = 0
    skipped = 0
    errors = 0

    for title_dir in sorted(saves_dir.iterdir()):
        if not title_dir.is_dir():
            continue

        title_id = title_dir.name

        # ------------------------------------------------------------------
        # 1. Detect and fix nested per-console subfolder layout (PSP/Vita).
        #    The broken layout put saves under saves/<title_id>/<console_id>/
        #    instead of saves/<title_id>/.
        # ------------------------------------------------------------------
        flat_meta = title_dir / "metadata.json"
        flat_current = title_dir / "current"

        # Find any subdir that looks like a broken console slot
        nested_slots = [
            d for d in title_dir.iterdir()
            if d.is_dir() and _is_ps_subdir(d.name)
        ]

        if nested_slots and not flat_current.exists():
            # Pick the most recently modified slot as the canonical one
            best = max(nested_slots, key=lambda d: (d / "metadata.json").stat().st_mtime
                       if (d / "metadata.json").exists() else 0)
            print(f"[FIX LAYOUT] {title_id}: promoting {best.name}/ → flat")

            if not dry_run:
                # Move current/ and history/ up
                nested_current = best / "current"
                nested_history = best / "history"
                nested_meta = best / "metadata.json"

                if nested_current.exists():
                    shutil.move(str(nested_current), str(flat_current))

                if nested_history.exists():
                    flat_history = title_dir / "history"
                    if flat_history.exists():
                        # Merge: move each version subfolder
                        for ver in nested_history.iterdir():
                            dest = flat_history / ver.name
                            if not dest.exists():
                                shutil.move(str(ver), str(dest))
                    else:
                        shutil.move(str(nested_history), str(flat_history))

                if nested_meta.exists():
                    shutil.copy2(str(nested_meta), str(flat_meta))

                # Remove all nested slot dirs
                for slot in nested_slots:
                    if slot.exists():
                        shutil.rmtree(slot)

            changed += 1

        # ------------------------------------------------------------------
        # 2. Stamp platform + name into metadata.json if missing / stale.
        # ------------------------------------------------------------------
        if not flat_meta.exists():
            print(f"[SKIP]       {title_id}: no metadata.json (even after layout fix)")
            skipped += 1
            continue

        try:
            data = json.loads(flat_meta.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[ERROR]      {title_id}: could not read metadata.json: {e}")
            errors += 1
            continue

        needs_update = False

        # Stamp platform
        if not data.get("platform"):
            platform = _detect_platform(title_id)
            data["platform"] = platform
            needs_update = True
            print(f"[PLATFORM]   {title_id}: → {platform}")

        # Stamp name (only if it's still just the title_id or blank)
        stored_name = data.get("name", "")
        if not stored_name or stored_name == title_id:
            name = _lookup_name(title_id)
            if name != title_id:
                data["name"] = name
                needs_update = True
                print(f"[NAME]       {title_id}: → {name}")

        if needs_update and not dry_run:
            flat_meta.write_text(json.dumps(data, indent=2), encoding="utf-8")

        if not needs_update:
            pass  # already up to date, no noise

    print(f"\nDone. changed={changed}  skipped={skipped}  errors={errors}")
    if dry_run:
        print("(dry-run mode — no files were modified)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate GameSync save directory")
    parser.add_argument(
        "--saves-dir",
        default=str(Path(__file__).parent / "saves"),
        help="Path to the saves directory (default: ./saves next to this script)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without making any changes",
    )
    args = parser.parse_args()

    saves_dir = Path(args.saves_dir)
    data_dir = Path(__file__).parent / "data"

    print(f"Loading game databases from {data_dir} …")
    for db_file in ("3dstdb.txt", "dstdb.txt", "pspdb.txt", "psxdb.txt", "vitadb.txt"):
        p = data_dir / db_file
        if p.exists():
            _load_db(p)
            print(f"  loaded {db_file}")
        else:
            print(f"  {db_file} not found, skipping")

    print(f"\nMigrating saves in {saves_dir} …")
    if args.dry_run:
        print("(dry-run mode)\n")

    migrate(saves_dir, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
