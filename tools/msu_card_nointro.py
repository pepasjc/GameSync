"""Rename the MSU packs on a flashcard to the plain ROM's No-Intro name, so
the pack and the regular ROM share one save file.

    python tools/msu_card_nointro.py <catalog.json> <card MSU dir> <card roms dir> <saves dir> [--apply]

A flashcard names its save after the ROM file it booted, and nothing like
GameSync sits in between to map ``Game (USA) (MSU1).srm`` onto
``Game (USA).srm``.  So on the card the pack's cart - and the ``.msu`` /
``-N.pcm`` / sidecars, which MSU-1 finds by the cart's stem - are renamed to
the plain ROM's name.  Folder names stay as they are.

The name comes from, in order: a plain ROM on the card with the pack's
server ``title_id``; the server's plain ROM with that id; an existing save on
the card for the same game; the pack name without its ``(MSU1)`` and
``[Hack by …]`` tags.  ``catalog.json`` is ``GET /api/v1/roms?system=SNES``.

Saves under a pack's old cart name are folded into the plain name: renamed
when the plain save doesn't exist, deleted when it does and the two are
identical, and left in place (reported) when they differ.
"""

from __future__ import annotations

import argparse
import filecmp
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from msu_pick import title_key  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from shared import msu  # noqa: E402

CART_EXTS = (".sfc", ".smc")


def _tk(name: str) -> str:
    return title_key(re.sub(r"\.(sfc|smc|srm)$", "", name, flags=re.I) + ".zip")


def fallback_name(pack: str) -> str:
    name = msu.strip_hack_tags(pack.replace("_", " "))
    name = re.sub(r"\s*\(MSU-?1\)", "", name)
    return re.sub(r"\s{2,}", " ", name).strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("catalog")
    ap.add_argument("card_dir")
    ap.add_argument("roms_dir")
    ap.add_argument("saves_dir")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    roms = json.loads(Path(args.catalog).read_text(encoding="utf-8"))["roms"]
    plain_by_tid = defaultdict(list)
    for r in roms:
        if not r.get("bundle_kind"):
            plain_by_tid[r["title_id"]].append(Path(r["filename"]).stem)
    pack_tid = {r["filename"][:-4]: r["title_id"] for r in roms if r.get("bundle_kind")}
    card_roms = {Path(f).stem for f in os.listdir(args.roms_dir)}
    saves = Path(args.saves_dir)
    save_stems = {Path(f).stem: f for f in os.listdir(saves)}

    card = Path(args.card_dir)
    report_conflicts = []
    for folder in sorted(p for p in card.iterdir() if p.is_dir()):
        files = [p for p in folder.iterdir() if p.is_file()]
        carts = [p for p in files if p.suffix.lower() in CART_EXTS]
        msus = [p for p in files if p.suffix.lower() == ".msu"]
        if not carts:
            continue
        cart = carts[0]
        old = msus[0].stem if msus else cart.stem

        tid = pack_tid.get(folder.name)
        cands = plain_by_tid.get(tid, []) if tid else []
        on_card = [c for c in cands if c in card_roms]
        by_save = [s for s in save_stems if _tk(s) == _tk(folder.name.replace("_", " "))]
        if tid is None:
            # Not a server pack (a card-only game): its cart already carries
            # whatever name the user gave it.
            new = cart.stem
        else:
            new = (on_card or cands or by_save or [fallback_name(folder.name)])[0]

        renames = []
        for p in files:
            if p == cart:
                target = new + p.suffix
            elif p.name.startswith(old + "-") or p.name.startswith(old + "."):
                target = new + p.name[len(old):]
            else:
                continue
            if p.name != target:
                renames.append((p, folder / target))
        if renames:
            print(f"{folder.name}\n    {old}  ->  {new}   ({len(renames)} files)")
        if args.apply:
            for src, dst in renames:
                os.replace(src, dst)

        # Saves under any old cart name of this pack fold into the plain one.
        plain_save = saves / f"{new}.srm"
        stale_names = {cart.stem.lower(), old.lower()} - {new.lower()}
        for fname in sorted(os.listdir(saves)):
            stem, ext = os.path.splitext(fname)
            if ext.lower() != ".srm" or stem.lower() not in stale_names:
                continue
            s = saves / fname
            if not plain_save.exists():
                print(f"    save {s.name} -> {plain_save.name}")
                if args.apply:
                    os.replace(s, plain_save)
            elif filecmp.cmp(s, plain_save, shallow=False):
                print(f"    save {s.name}: duplicate of {plain_save.name}, removed")
                if args.apply:
                    s.unlink()
            else:
                report_conflicts.append((s.name, plain_save.name))
    if report_conflicts:
        print("\nsaves that differ from the plain ROM's save (left alone):")
        for a, b in report_conflicts:
            print(f"   {a}  vs  {b}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
