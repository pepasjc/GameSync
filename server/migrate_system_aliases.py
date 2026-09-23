"""Merge alias-prefixed slug title IDs into their canonical system code.

Background
----------
``SYSTEM_CODES`` deliberately contains system aliases (``GEN``, ``SCD``,
``WS``, ``ATARI2600`` …) so that a client sending one still validates.  But
``make_title_id`` only checked membership and never resolved the alias, so
whichever spelling a client happened to use went straight into the storage
key.  The same game therefore landed in two slots that never converged:

    GEN_phantasy_star_iv_usa   <->   MD_phantasy_star_iv_usa
    SCD_snatcher_usa           <->   SEGACD_snatcher_usa

``shared.rom_id.make_title_id`` and ``shared.sync_id.canonicalize_slug_title_id``
now resolve aliases at both the client and the API boundary.  This migration
folds the pre-existing duplicates together, following the same rules as
``migrate_gc_title_case``:

- the variant with the newest ``client_timestamp`` wins and becomes ``current/``
- the loser's save is preserved under the winner's ``history/`` (never deleted)
- the loser's own history versions are moved across as well
- the loser's DB row and directory are removed; ``metadata.json`` is rewritten

An alias-prefixed save with no canonical twin is simply renamed, so nothing is
lost either way.

Idempotent: a second run finds nothing to do.  Defaults to a dry run; pass
``--apply`` to write.
"""

from __future__ import annotations

import argparse
from collections import defaultdict

from app.config import settings
from app.services import db, storage
from shared.systems import SYSTEM_ALIASES

# The GC merge solved exactly this shape of problem; reuse its machinery
# rather than growing a second copy that can drift.
from migrate_gc_title_case import (
    _merge_loser,
    _rewrite_metadata_json,
    _row_for,
    _same_dir,
    _variant_sort_key,
)


def canonical_alias_title_id(title_id: str) -> str | None:
    """``GEN_sonic`` -> ``MD_sonic``.  None when nothing needs changing."""
    text = str(title_id or "")
    if "_" not in text:
        return None
    prefix, slug = text.split("_", 1)
    canonical = SYSTEM_ALIASES.get(prefix.upper())
    if not canonical or not slug:
        return None
    return f"{canonical}_{slug}"


def _collect_variants() -> dict[str, list[str]]:
    """Map canonical title_id -> variants of it found on this server."""
    seen: dict[str, set[str]] = defaultdict(set)

    def note(title_id: str) -> None:
        canonical = canonical_alias_title_id(title_id)
        if canonical:
            seen[canonical].add(title_id)

    for row in db.list_all():
        note(str(row.get("title_id") or ""))

    save_dir = settings.save_dir
    if save_dir.exists():
        for entry in save_dir.iterdir():
            if entry.is_dir():
                note(entry.name)

    # Pull in the canonical id itself when it already exists — that is the
    # duplicate case, and it has to take part in the newest-wins comparison.
    groups: dict[str, list[str]] = {}
    for canonical, variants in seen.items():
        members = set(variants)
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
        system = canonical.split("_", 1)[0]
        if winning_row.get("system"):
            winning_row["system"] = system
        if winning_row.get("platform") in SYSTEM_ALIASES:
            winning_row["platform"] = SYSTEM_ALIASES[winning_row["platform"]]
        print(f"    upsert DB row {canonical}")
        if apply:
            db.upsert(winning_row)
            _rewrite_metadata_json(canonical, apply)
            storage.rebuild_metadata_from_current(
                canonical,
                source=winning_row.get("last_sync_source") or "migration_alias",
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
    print(f"\n{mode}: {changed} of {examined} alias-prefixed title(s) needing merge.")
    if not args.apply and changed:
        print("Re-run with --apply to write.")
