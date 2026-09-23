"""Re-key Dreamcast saves from name slugs to disc serials.

Background
----------
Dreamcast used to be a ``slug`` system: a save was keyed by the ROM's name
(``DC_sonic_adventure_usa``).  It is now a ``serial`` system like PS1, PS2 and
Saturn, keyed by the disc's ``IP.BIN`` product number (``DC_51000``) — see
``shared/rom_id/dreamcast.py``.

That is the identifier every Dreamcast save device already uses: MemCard PRO DC
files a save under ``Dreamcast/T1249M/``, openMenu's Serial VMU under
``OPENMENU/SAVES/T1249M/``.  Under the old scheme those cards could not share a
slot with an emulator save unless both sides derived the same name slug, which a
renamed or oddly-tagged ROM file quietly broke.

New uploads are re-keyed at the API boundary (``canonicalize_slug_title_id``),
so this migration is about saves *already stored* under a slug id.  Each is
renamed to its serial id; where both spellings exist, they are merged with the
same newest-wins rules the GameCube and alias migrations use:

- the variant with the newest ``client_timestamp`` wins and becomes ``current/``
- the loser's save is preserved under the winner's ``history/`` (never deleted)
- the loser's own history versions are moved across as well
- the loser's DB row and directory are removed; ``metadata.json`` is rewritten

A Dreamcast save whose slug the DAT can't resolve to a serial (homebrew, an
unusual dump) is left exactly as it is — it stays addressable under its slug.

Idempotent: a second run finds nothing to do.  Defaults to a dry run; pass
``--apply`` to write.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

from app.config import settings
from app.services import db, storage
from app.services.dat_normalizer import DatNormalizer
from shared.rom_id.dreamcast import parse_dc_title_id
from shared.sync_id import canonicalize_slug_title_id

# The GC merge solved exactly this shape of problem; reuse its machinery
# rather than growing a second copy that can drift.
from migrate_gc_title_case import (
    _merge_loser,
    _rewrite_metadata_json,
    _row_for,
    _same_dir,
    _variant_sort_key,
)

_normalizer: DatNormalizer | None = None


def _dat_normalizer() -> DatNormalizer:
    global _normalizer
    if _normalizer is None:
        # Same directory the server loads at startup (app/main.py).
        _normalizer = DatNormalizer(Path(__file__).parent / "app" / "data" / "dats")
        if not _normalizer._serial_index:
            _normalizer = DatNormalizer(Path(__file__).parent / "data" / "dats")
    return _normalizer


def canonical_dc_title_id(title_id: str) -> str | None:
    """``DC_sonic_adventure_usa`` -> ``DC_51000``.  None when nothing changes.

    Returns None for ids that are already serial-form, for non-Dreamcast ids,
    and for slugs the DAT cannot resolve.
    """
    text = str(title_id or "").strip()
    if not text.upper().startswith("DC_"):
        return None
    if parse_dc_title_id(text):
        return None  # already a serial id
    canonical = canonicalize_slug_title_id(
        text, serial_lookup=_dat_normalizer().lookup_serial
    )
    if canonical == text or not parse_dc_title_id(canonical):
        return None  # the DAT had no serial for this disc
    return canonical


def _collect_variants() -> dict[str, list[str]]:
    """Map canonical serial title_id -> the spellings found on this server."""
    seen: dict[str, set[str]] = defaultdict(set)

    def note(title_id: str) -> None:
        canonical = canonical_dc_title_id(title_id)
        if canonical:
            seen[canonical].add(title_id)

    for row in db.list_all():
        note(str(row.get("title_id") or ""))

    save_dir = settings.save_dir
    if save_dir.exists():
        for entry in save_dir.iterdir():
            if entry.is_dir():
                note(entry.name)

    groups: dict[str, list[str]] = {}
    for canonical, variants in seen.items():
        members = set(variants)
        # The serial id may already exist (a card synced before this ran) — it
        # has to take part in the newest-wins comparison.
        if db.exists(canonical) or (save_dir / canonical).exists():
            members.add(canonical)
        groups[canonical] = sorted(members)
    return groups


def migrate(apply: bool = False) -> tuple[int, int]:
    """Return (groups_examined, groups_changed)."""
    groups = _collect_variants()
    examined = 0
    changed = 0

    for canonical, variants in sorted(groups.items()):
        examined += 1
        rows = {v: _row_for(v) for v in variants}
        winner = max(variants, key=lambda v: _variant_sort_key(rows[v]))
        losers = [v for v in variants if v != winner]

        print(f"{canonical}: variants={variants} winner={winner}")

        winner_dir = settings.save_dir / winner
        canonical_dir = settings.save_dir / canonical

        physical_losers = [
            v
            for v in losers
            if (settings.save_dir / v).exists()
            and not _same_dir(settings.save_dir / v, winner_dir)
        ]
        for loser in physical_losers:
            _merge_loser(loser, winner_dir, rows[loser], apply)

        if winner != canonical:
            print(f"    rename dir {winner} -> {canonical}")
            if apply and winner_dir.exists():
                winner_dir.rename(canonical_dir)

        for variant in variants:
            if variant != canonical and db.exists(variant):
                print(f"    drop DB row {variant}")
                if apply:
                    db.delete(variant)

        winning_row = dict(rows[winner])
        winning_row["title_id"] = canonical
        if winning_row.get("system"):
            winning_row["system"] = "DC"
        print(f"    upsert DB row {canonical}")
        if apply:
            db.upsert(winning_row)
            _rewrite_metadata_json(canonical, apply)
            storage.rebuild_metadata_from_current(
                canonical,
                source=winning_row.get("last_sync_source") or "migration_dc_serial",
            )
        else:
            _rewrite_metadata_json(canonical, apply)

        changed += 1

    return examined, changed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="write changes (default: dry run)"
    )
    args = parser.parse_args()

    examined, changed = migrate(apply=args.apply)
    mode = "Applied" if args.apply else "DRY RUN — would update"
    print(f"\n{mode}: {changed} of {examined} Dreamcast title(s) needing re-keying.")
    if not args.apply and changed:
        print("Re-run with --apply to write.")
