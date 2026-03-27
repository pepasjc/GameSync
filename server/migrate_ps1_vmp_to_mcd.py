"""Materialize raw PS1 memory cards alongside legacy PSP/Vita VMP files.

This migration is additive and idempotent:
- keeps existing SCEVMC0/1.VMP files untouched
- creates slot0.mcd / slot1.mcd if missing
- refreshes metadata file_count/save_size/save_hash in SQLite
"""

from __future__ import annotations

from pathlib import Path

from app.services import storage
from app.services.ps1_cards import extract_raw_card, is_ps1_title_id, slot_raw_name, slot_vmp_name


def migrate() -> tuple[int, int]:
    root = Path("saves")
    scanned = 0
    changed = 0
    for title_dir in sorted(root.iterdir() if root.exists() else []):
        title_id = title_dir.name
        if not is_ps1_title_id(title_id):
            continue
        current = title_dir / "current"
        if not current.is_dir():
            continue

        scanned += 1
        title_changed = False
        for slot in (0, 1):
            raw_path = current / slot_raw_name(slot)
            vmp_path = current / slot_vmp_name(slot)
            if raw_path.exists() or not vmp_path.exists():
                continue
            raw_path.write_bytes(extract_raw_card(vmp_path.read_bytes()))
            title_changed = True

        if title_changed:
            storage.rebuild_metadata_from_current(title_id, source="migration_ps1_vmp_to_mcd")
            changed += 1

    return scanned, changed


if __name__ == "__main__":
    scanned, changed = migrate()
    print(f"Scanned {scanned} PS1 title(s), updated {changed}.")
