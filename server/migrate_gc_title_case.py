"""Merge case-variant GameCube/Wii title IDs into their canonical uppercase form.

Background
----------
``GC_<gamecode>`` IDs were emitted in two casings: uppercase by the GameCube
and Wii U homebrew clients and by the server's ``/saves/gc-vmc/import``, and
lowercase by the Dolphin scanners in the Android, Steam Deck and desktop
clients.  Nothing normalised the case, so the same game landed under two
storage keys (``GC_GRSE`` and ``GC_grse``) and never converged.

``shared.sync_id.canonicalize_code_form_title_id`` now uppercases these IDs at
the API boundary.  This migration folds the pre-existing duplicates together:

- the variant with the newest ``client_timestamp`` wins and becomes ``current/``
- the loser's save is preserved under the winner's ``history/`` (never deleted)
- the loser's own history versions are moved across as well
- the loser's DB row and directory are removed; ``metadata.json`` is rewritten

Idempotent: a second run finds nothing to do.  Defaults to a dry run; pass
``--apply`` to write.
"""

from __future__ import annotations

import argparse
import json
import shutil
from collections import defaultdict
from pathlib import Path

from app.config import settings
from app.services import db, storage
from shared.sync_id import canonicalize_code_form_title_id, is_code_form_title_id


def _variant_sort_key(row: dict) -> tuple:
    """Newest-wins ordering: client_timestamp, then server_timestamp/last_sync."""
    return (
        int(row.get("client_timestamp") or 0),
        str(row.get("server_timestamp") or ""),
        str(row.get("last_sync") or ""),
    )


def _archive_label(row: dict, title_id: str) -> str:
    """Directory name for the losing save inside the winner's history/."""
    stamp = str(row.get("last_sync") or row.get("server_timestamp") or "")
    stamp = stamp.replace(":", "_").replace("+", "_") or "unknown"
    return f"{stamp}_{title_id}"


def _collect_variants() -> dict[str, list[str]]:
    """Map canonical title_id -> the distinct case variants found on this server."""
    seen: dict[str, set[str]] = defaultdict(set)

    for row in db.list_all():
        tid = str(row.get("title_id") or "")
        if is_code_form_title_id(tid):
            seen[canonicalize_code_form_title_id(tid)].add(tid)

    save_dir = settings.save_dir
    if save_dir.exists():
        for entry in save_dir.iterdir():
            if entry.is_dir() and is_code_form_title_id(entry.name):
                seen[canonicalize_code_form_title_id(entry.name)].add(entry.name)

    # In scope: several casings of the same game, or a lone non-canonical one.
    return {
        canonical: sorted(variants)
        for canonical, variants in seen.items()
        if len(variants) > 1 or variants != {canonical}
    }


def _row_for(title_id: str) -> dict:
    row = db.get(title_id)
    if row is not None:
        return row
    meta_path = settings.save_dir / title_id / "metadata.json"
    if meta_path.exists():
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"title_id": title_id}


def _same_dir(a: Path, b: Path) -> bool:
    """True when two paths are the same directory (case-insensitive filesystem)."""
    try:
        return a.exists() and b.exists() and a.samefile(b)
    except OSError:
        return False


def _rename_dir_to_canonical(src: Path, dst: Path) -> None:
    """Rename a directory, handling case-only renames on Windows/macOS."""
    # NB: compare names as strings — Path equality is case-insensitive on
    # Windows, so ``src == dst`` is True for exactly the case-only rename
    # this function exists to perform.
    if src.name == dst.name or not src.exists():
        return
    if _same_dir(src, dst):
        # Case-only difference on a case-insensitive filesystem: two-step rename.
        tmp = src.with_name(src.name + "__case_tmp")
        src.rename(tmp)
        tmp.rename(dst)
        return
    src.rename(dst)


def _merge_loser(loser: str, winner_dir: Path, loser_row: dict, apply: bool) -> None:
    """Move the losing variant's current/ and history/ into the winner's history/."""
    loser_dir = settings.save_dir / loser
    if not loser_dir.exists():
        return

    history = winner_dir / "history"
    loser_current = loser_dir / "current"
    if loser_current.is_dir() and any(loser_current.iterdir()):
        archive = history / _archive_label(loser_row, loser)
        print(f"    archive {loser}/current -> history/{archive.name}")
        if apply:
            archive.mkdir(parents=True, exist_ok=True)
            for item in loser_current.iterdir():
                if item.is_file():
                    shutil.copy2(item, archive / item.name)
                else:
                    shutil.copytree(item, archive / item.name, dirs_exist_ok=True)

    loser_history = loser_dir / "history"
    if loser_history.is_dir():
        for version in sorted(loser_history.iterdir()):
            if not version.is_dir():
                continue
            dest = history / f"{version.name}_{loser}"
            print(f"    carry history/{version.name} -> history/{dest.name}")
            if apply:
                shutil.copytree(version, dest, dirs_exist_ok=True)

    print(f"    remove duplicate dir {loser_dir}")
    if apply:
        shutil.rmtree(loser_dir)


def _rewrite_metadata_json(title_id: str, apply: bool) -> None:
    meta_path = settings.save_dir / title_id / "metadata.json"
    if not meta_path.exists():
        return
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return
    if data.get("title_id") == title_id:
        return
    data["title_id"] = title_id
    print(f"    rewrite metadata.json title_id -> {title_id}")
    if apply:
        meta_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


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

        # Losers that are physically the SAME directory (case-insensitive FS)
        # have no separate save data — only a stale DB row to drop.
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
            if apply:
                _rename_dir_to_canonical(winner_dir, canonical_dir)

        for variant in variants:
            if variant != canonical and db.exists(variant):
                print(f"    drop DB row {variant}")
                if apply:
                    db.delete(variant)

        winning_row = dict(rows[winner])
        winning_row["title_id"] = canonical
        print(f"    upsert DB row {canonical}")
        if apply:
            db.upsert(winning_row)
            _rewrite_metadata_json(canonical, apply)
            # Recompute hash/size from the merged current/ so sync comparisons
            # match the files that actually survived the merge.
            storage.rebuild_metadata_from_current(
                canonical, source=winning_row.get("last_sync_source") or "migration_gc_case"
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
    print(f"\n{mode}: {changed} of {examined} gamecode-form title(s) needing merge.")
    if not args.apply and changed:
        print("Re-run with --apply to write.")
