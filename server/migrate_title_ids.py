#!/usr/bin/env python3
"""Migration script: rename emulator save directories to include region in title_id.

Before: SNES_castlevania_dracula_x   (stored name had region stripped)
After:  SNES_castlevania_dracula_x_usa

Two-pass lookup per save:
  1. Stored name in metadata.db has region  →  derive title_id directly from name
     e.g. name="Castlevania - Dracula X (USA)" → SNES_castlevania_dracula_x_usa

  2. Stored name has no region (was stored without it)  →  DAT file fallback
     Look up old slug in a bare-key DAT index to get the canonical name with region
     e.g. slug "super_metroid" → DAT: "Super Metroid (Japan, USA) (En,Ja)"
          → normalize → SNES_super_metroid_japan_usa

In both cases only acts when the change is purely "region tag appended to identical
base slug" — rejects GC 4-char code saves, bad name data, etc.

Also handled:
  - Updates title_id primary key in metadata.db
  - Updates title_id in legacy metadata.json (if present)
  - Deletes roms.db so the ROM catalog rebuilds on next server start

Usage:
    cd server/
    python migrate_title_ids.py [--dry-run] [--save-dir PATH] [--dat-dir PATH]

Always run with --dry-run first to review what will change.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.services.rom_id import normalize_rom_name

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Matches emulator-style title_ids like SNES_super_mario_world
# Does NOT match 16-char hex TitleIDs (3DS/NDS hardware) or PSX serials
_EMULATOR_ID_RE = re.compile(r"^([A-Z0-9]{2,8})_([a-z0-9][a-z0-9_]*)$")

# Region tokens that may appear at the end of a migrated slug
_KNOWN_REGIONS = frozenset({
    "usa", "europe", "japan", "world", "germany", "france", "italy",
    "spain", "australia", "brazil", "korea", "china", "asia",
    "en", "ja", "fr", "de", "es", "it", "nl", "pt", "sv", "no", "da",
    "fi", "ko", "zh",
})

_ALL_PARENS_RE = re.compile(r"\s*\([^)]+\)")
_NON_ALNUM_RE  = re.compile(r"[^a-z0-9]+")
_MULTI_US_RE   = re.compile(r"_+")


def _bare_normalize(name: str) -> str:
    """Normalize by stripping ALL parenthetical groups (region, language, rev…).

    Used to build the DAT fallback index so we can look up 'super_metroid'
    and find 'Super Metroid (Japan, USA) (En,Ja)'.
    """
    n = _ALL_PARENS_RE.sub("", name).strip()
    n = n.lower()
    n = _NON_ALNUM_RE.sub("_", n)
    n = _MULTI_US_RE.sub("_", n).strip("_")
    return n or "unknown"


def _is_region_appended(old_slug: str, new_slug: str) -> bool:
    """Return True iff new_slug == old_slug + '_' + <one or more known region tokens>.

    Catches:  castlevania_dracula_x   ->  castlevania_dracula_x_usa
              super_metroid           ->  super_metroid_japan_usa
              sonic_the_hedgehog      ->  sonic_the_hedgehog_usa_europe
    Rejects:  gale -> super_smash_bros_melee      (GC 4-char code format)
              dino_crisis_2_usa -> retro_dino_crisis_2_usa  (bad name data)
    """
    if not new_slug.startswith(old_slug + "_"):
        return False
    suffix = new_slug[len(old_slug) + 1:]
    return bool(suffix) and all(p in _KNOWN_REGIONS for p in suffix.split("_"))


# ---------------------------------------------------------------------------
# DAT fallback index
# ---------------------------------------------------------------------------

_PAREN_CONTENT_RE = re.compile(r"\(([^)]+)\)")


def _region_score(name: str) -> tuple:
    """Prefer USA > World > Europe > Japan for picking the best canonical name.

    Handles multi-region entries like '(Japan, USA)' by splitting on commas
    and checking each token, so 'Super Metroid (Japan, USA)' scores as USA.
    """
    priority = {"usa": 0, "world": 1, "europe": 2, "japan": 3}
    tokens = [
        p.strip().lower()
        for m in _PAREN_CONTENT_RE.finditer(name)
        for p in m.group(1).split(",")
    ]
    best = min((priority[t] for t in tokens if t in priority), default=99)
    return (best, name.count("("))


def _build_dat_barekey_index(dats_dir: Path) -> dict[str, dict[str, str]]:
    """Build {system: {bare_slug: best_canonical}} from the DAT files.

    bare_slug  = all-parens-stripped & normalized (e.g. "super_metroid")
    canonical  = full DAT name with region (e.g. "Super Metroid (Japan, USA) (En,Ja)")

    When multiple canonical names map to the same bare slug, the USA/World/Europe/
    Japan region-priority winner is kept.
    """
    from app.services.dat_normalizer import _system_from_dat_stem, _parse_dat

    index: dict[str, dict[str, str]] = {}

    if not dats_dir.is_dir():
        return index

    for dat_path in sorted(dats_dir.glob("*.dat")):
        system = _system_from_dat_stem(dat_path.stem)
        if not system:
            continue

        _, slug_cands = _parse_dat(dat_path)
        sys_idx = index.setdefault(system, {})

        for names in slug_cands.values():
            for canonical in names:
                bare = _bare_normalize(canonical)
                # Keep the best (highest-priority region) canonical per bare slug
                existing = sys_idx.get(bare)
                if existing is None or _region_score(canonical) < _region_score(existing):
                    sys_idx[bare] = canonical

    return index


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _load_metadata_db(save_dir: Path) -> sqlite3.Connection:
    db_path = save_dir / "metadata.db"
    if not db_path.exists():
        print(f"ERROR: metadata.db not found at {db_path}")
        sys.exit(1)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--save-dir",
        type=Path,
        default=Path(__file__).parent.parent / "saves",
        help="Path to the saves directory (default: ../saves)",
    )
    parser.add_argument(
        "--dat-dir",
        type=Path,
        default=Path(__file__).parent / "data" / "dats",
        help="Path to DAT files for fallback lookup (default: data/dats/)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without modifying anything",
    )
    args = parser.parse_args()

    save_dir: Path = args.save_dir.resolve()
    dat_dir:  Path = args.dat_dir.resolve()
    dry_run:  bool = args.dry_run

    if not save_dir.is_dir():
        print(f"ERROR: saves directory not found: {save_dir}")
        sys.exit(1)

    print(f"{'DRY RUN — ' if dry_run else ''}Scanning: {save_dir}")
    print(f"DAT dir:  {dat_dir}")
    print()

    conn = _load_metadata_db(save_dir)

    print("Loading DAT files for fallback lookup…")
    dat_index = _build_dat_barekey_index(dat_dir)
    dat_systems = sorted(dat_index)
    total_dat_entries = sum(len(v) for v in dat_index.values())
    print(f"  {total_dat_entries} entries across {len(dat_systems)} systems\n")

    renamed          = 0
    skipped_no_meta  = 0
    skipped_no_change = 0
    skipped_conflict = 0
    errors           = 0

    # Collect all candidate directories
    candidates: list[tuple[str, str, Path]] = []
    for entry in sorted(save_dir.iterdir()):
        if not entry.is_dir():
            continue
        m = _EMULATOR_ID_RE.match(entry.name)
        if not m:
            continue
        candidates.append((m.group(1), m.group(2), entry))

    if not candidates:
        print("No emulator-style save directories found — nothing to migrate.")
        return

    print(f"Found {len(candidates)} emulator save director{'y' if len(candidates)==1 else 'ies'}.\n")
    header = f"  {'SOURCE':8s}  {'OLD TITLE_ID':<40} {'NEW TITLE_ID':<40} NAME"
    print(header)
    print("  " + "─" * (len(header) - 2))

    for system, old_slug, dir_path in candidates:
        old_id = dir_path.name

        # --- Step 1: look up stored name in metadata.db ---
        row = conn.execute(
            "SELECT name FROM saves WHERE title_id = ?", (old_id,)
        ).fetchone()

        if row is None:
            print(f"  {'SKIP':8s}  {old_id}  (no metadata)")
            skipped_no_meta += 1
            continue

        game_name: str = row["name"] or ""
        if not game_name:
            print(f"  {'SKIP':8s}  {old_id}  (empty name)")
            skipped_no_meta += 1
            continue

        # --- Step 2: derive new title_id from stored name ---
        new_id = f"{system}_{normalize_rom_name(game_name)}"
        source = "name"

        # --- Step 3: if stored name has no region, try DAT fallback ---
        if new_id == old_id:
            canonical = dat_index.get(system, {}).get(old_slug)
            if canonical:
                dat_new_id = f"{system}_{normalize_rom_name(canonical)}"
                dat_new_slug = dat_new_id[len(system) + 1:]
                if _is_region_appended(old_slug, dat_new_slug):
                    new_id = dat_new_id
                    game_name = canonical  # use full canonical for display & DB update
                    source = "dat"

        # Still no change after both passes → already correct, skip silently
        if new_id == old_id:
            skipped_no_change += 1
            continue

        # --- Step 4: guard — only allow pure region-append changes ---
        new_slug = new_id[len(system) + 1:]
        if not _is_region_appended(old_slug, new_slug):
            print(
                f"  {'SKIP':8s}  {old_id:<40} -> {new_id}  "
                f"(format change, not region-append — manual review needed)"
            )
            skipped_no_change += 1
            continue

        # --- Step 5: conflict check ---
        new_dir = save_dir / new_id
        if new_dir.exists() and new_dir != dir_path:
            print(f"  {'CONFLICT':8s}  {old_id:<40} -> {new_id}  (target already exists)")
            skipped_conflict += 1
            continue

        action = "(dry run)" if dry_run else "RENAME"
        print(f"  {source:8s}  {old_id:<40} -> {new_id:<40} {game_name!r}")

        if dry_run:
            renamed += 1
            continue

        # --- Step 6: apply changes ---
        try:
            # a. Rename directory on disk
            dir_path.rename(new_dir)

            # b. Update metadata.db primary key (insert new row, delete old)
            conn.execute(
                """
                INSERT INTO saves
                SELECT
                    ? AS title_id,
                    ? AS name,
                    system, last_sync, last_sync_source, save_hash, save_size,
                    file_count, client_timestamp, server_timestamp, console_id, platform
                FROM saves WHERE title_id = ?
                """,
                (new_id, game_name, old_id),
            )
            conn.execute("DELETE FROM saves WHERE title_id = ?", (old_id,))
            conn.commit()

            # c. Update legacy metadata.json if present
            meta_json = new_dir / "metadata.json"
            if meta_json.exists():
                try:
                    data = json.loads(meta_json.read_text(encoding="utf-8"))
                    if data.get("title_id") == old_id:
                        data["title_id"] = new_id
                        meta_json.write_text(
                            json.dumps(data, indent=2), encoding="utf-8"
                        )
                except Exception as exc:
                    print(f"    WARNING: could not update metadata.json: {exc}")

            renamed += 1

        except Exception as exc:
            print(f"  ERROR renaming {old_id}: {exc}")
            errors += 1

    conn.close()

    print()
    print(
        f"{'Would rename' if dry_run else 'Renamed'}:   {renamed}"
        f"  |  no change: {skipped_no_change}"
        f"  |  no metadata: {skipped_no_meta}"
        f"  |  conflicts: {skipped_conflict}"
        f"  |  errors: {errors}"
    )

    if dry_run:
        print("\nRe-run without --dry-run to apply.")
        return

    # Delete roms.db so the ROM catalog rebuilds with new title_ids
    roms_db = save_dir / "roms.db"
    if roms_db.exists():
        roms_db.unlink()
        print("Deleted roms.db — ROM catalog will rebuild on next server start.")

    if renamed > 0:
        print("\nDone. Restart the server to pick up the changes.")
    else:
        print("\nNothing changed.")


if __name__ == "__main__":
    main()
