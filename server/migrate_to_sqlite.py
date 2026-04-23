#!/usr/bin/env python3
"""One-time migration script: read all metadata.json files and insert into SQLite.

Usage:
    python migrate_to_sqlite.py [--dry-run] [--save-dir /path/to/saves]

Steps:
  1. Finds all saves/*/metadata.json files
  2. Parses each and inserts into metadata.db
  3. Prints a summary of migrated / skipped / errored rows
  4. On success, renames each metadata.json -> metadata.json.bak
  5. --dry-run: shows what would be migrated without writing anything

Run once before deploying the updated server. If it fails, JSON files are
untouched and the server will still serve saves via the JSON fallback path.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running from the server/ directory
sys.path.insert(0, str(Path(__file__).parent))

from app.services import db as _db, game_names as _game_names


def _load_name_databases() -> int:
    """Load the title name databases so we can re-lookup names during migration."""
    data_dir = Path(__file__).parent / "data"
    dats_dir = data_dir / "dats"
    total = 0
    total += _game_names.load_libretro_dat_to_dicts(
        dats_dir / "Nintendo - Nintendo 3DS.dat"
    )
    total += _game_names.load_libretro_dat_to_dicts(
        dats_dir / "Nintendo - Nintendo 3DS (Digital).dat"
    )
    total += _game_names.load_database(data_dir / "3dstdb.txt")
    total += _game_names.load_database(data_dir / "dstdb.txt")
    total += _game_names.load_database(data_dir / "pspdb.txt")
    total += _game_names.load_database(data_dir / "vitadb.txt")
    total += _game_names.load_database(data_dir / "psxdb.txt")
    total += _game_names.load_database(data_dir / "unsorted_psx.txt")
    return total


def migrate(save_dir: Path, dry_run: bool) -> None:
    if not save_dir.exists():
        print(f"Save directory does not exist: {save_dir}")
        sys.exit(1)

    json_files = sorted(save_dir.glob("*/metadata.json"))
    if not json_files:
        print("No metadata.json files found — nothing to migrate.")
        return

    db_count = _load_name_databases()
    print(f"Loaded {db_count:,} game name entries from DAT/database sources")
    print()

    if not dry_run:
        _db.init_db(save_dir)

    migrated = 0
    skipped = 0
    errors = 0

    for json_path in json_files:
        title_id = json_path.parent.name
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            data.setdefault("system", "")
            data.setdefault("platform", "")
            data.setdefault("console_id", "")
            data.setdefault("name", title_id)
            data.setdefault("last_sync", "")
            data.setdefault("last_sync_source", "")
            data.setdefault("save_hash", "")
            data.setdefault("save_size", 0)
            data.setdefault("file_count", 0)
            data.setdefault("client_timestamp", 0)
            data.setdefault("server_timestamp", "")

            # Re-lookup name from the new comprehensive database.
            # Always prefer the DB name over whatever was stored in JSON —
            # old metadata may have raw title IDs or stale/partial names.
            fresh_name, fresh_platform = _game_names.lookup_name_and_platform(title_id)
            old_name = data.get("name", title_id)
            if fresh_name != title_id:
                # DB found a real name — use it
                data["name"] = fresh_name
                if not data.get("platform"):
                    data["platform"] = fresh_platform
            elif not data.get("platform"):
                data["platform"] = fresh_platform

            if dry_run:
                name_note = f"{old_name!r} -> {data['name']!r}" if old_name != data["name"] else f"{data['name']!r}"
                print(
                    f"  [dry-run] {title_id:40s}  name={name_note}  platform={data.get('platform', '')!r}"
                )
                migrated += 1
                continue

            _db.upsert(data)

            # Validate: read back and compare key fields
            row = _db.get(title_id)
            if row is None:
                raise ValueError("Row not found after insert")
            if row["save_hash"] != data["save_hash"]:
                raise ValueError(f"Hash mismatch after insert: {row['save_hash']!r} != {data['save_hash']!r}")

            # Rename metadata.json -> metadata.json.bak
            bak_path = json_path.with_suffix(".json.bak")
            json_path.rename(bak_path)
            name_note = f" ({old_name!r} -> {data['name']!r})" if old_name != data["name"] else ""
            print(f"  Migrated: {title_id}{name_note}")
            migrated += 1

        except Exception as exc:
            print(f"  ERROR migrating {title_id}: {exc}")
            errors += 1

    print()
    if dry_run:
        print(f"Dry run complete: {migrated} saves would be migrated.")
    else:
        print(f"Migration complete: {migrated} migrated, {skipped} skipped, {errors} errors.")
        if errors:
            print("Some saves failed to migrate. JSON files for those saves are untouched.")
            print("The server will fall back to reading those JSON files automatically.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate save metadata from JSON to SQLite")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument(
        "--save-dir",
        type=Path,
        default=None,
        help="Path to saves directory (default: server/saves)",
    )
    args = parser.parse_args()

    if args.save_dir:
        save_dir = args.save_dir
    else:
        # Default: saves/ relative to the server/ directory
        save_dir = Path(__file__).parent / "saves"

    print(f"Save directory : {save_dir}")
    print(f"Dry run        : {args.dry_run}")
    print()

    migrate(save_dir, args.dry_run)


if __name__ == "__main__":
    main()
