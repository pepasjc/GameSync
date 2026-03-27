"""Migration: update stored platform values to use "PS1" instead of "PSX".

Also re-classifies saves stored as "PSP" whose title ID has a known PS1
retail disc prefix (SLUS, SLES, SLPS, SCUS, SCES, SCPS, etc.) so they
are correctly tagged as "PS1".

Run from the server/ directory:
    python migrate_platform_to_ps1.py [--db PATH] [--dry-run]

Default DB path is derived from settings (SAVE_DIR/metadata.db).
"""

import argparse
import sqlite3
from pathlib import Path

# Known PS1 retail disc product-code prefixes.
_PSX_RETAIL_PREFIXES = frozenset({
    "SLUS", "SCUS", "PAPX",          # North America
    "SLES", "SCES", "SCED",          # Europe
    "SLPS", "SLPM", "SCPS", "SCPM",  # Japan
    "SLAJ", "SLEJ", "SCAJ",          # Other
})


def _is_ps1_title(title_id: str) -> bool:
    """Return True if title_id has a known PS1 retail prefix."""
    return len(title_id) >= 4 and title_id[:4].upper() in _PSX_RETAIL_PREFIXES


def migrate(db_path: Path, dry_run: bool = False) -> None:
    if not db_path.exists():
        print(f"ERROR: DB not found: {db_path}")
        return

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    rows = conn.execute("SELECT title_id, platform FROM saves").fetchall()

    updates: list[tuple[str, str]] = []  # (new_platform, title_id)

    for row in rows:
        title_id: str = row["title_id"]
        platform: str = row["platform"]

        if platform == "PSX":
            # Old label — rename to PS1
            updates.append(("PS1", title_id))
        elif platform == "PSP" and _is_ps1_title(title_id):
            # Misclassified PSone Classic — reclassify
            updates.append(("PS1", title_id))

    if not updates:
        print("Nothing to migrate.")
        conn.close()
        return

    print(f"{'[DRY RUN] ' if dry_run else ''}Updating {len(updates)} row(s):")
    for new_platform, tid in updates:
        old = next(r["platform"] for r in rows if r["title_id"] == tid)
        print(f"  {tid}: {old!r} -> {new_platform!r}")

    if not dry_run:
        conn.executemany(
            "UPDATE saves SET platform = ? WHERE title_id = ?",
            updates,
        )
        conn.commit()
        print("Done.")
    else:
        print("[DRY RUN] No changes written.")

    conn.close()


def _default_db_path() -> Path:
    """Try to derive the DB path from server settings."""
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from app.config import settings
        return Path(settings.save_dir) / "metadata.db"
    except Exception:
        return Path("saves/metadata.db")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Path to metadata.db (default: derived from settings)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would change without modifying the DB",
    )
    args = parser.parse_args()

    db = args.db or _default_db_path()
    print(f"DB: {db}")
    migrate(db, dry_run=args.dry_run)
