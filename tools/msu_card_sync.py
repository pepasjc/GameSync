"""Bring an FXPak / SD2SNES card's MSU folder in line with the server's packs.

    python tools/msu_card_sync.py <manifest.json> <card MSU dir> <saves dir> [--apply]

``manifest.json`` maps each server zip name to ``[[member, size, crc32], ...]``
(dump it on the server from ``snes/msu1``).  The target layout is one folder
per server zip, named after the zip's stem, holding exactly its members - the
same thing every client unpacks a pack to.

Card files are reused wherever possible: every file under the card's MSU dir
is fingerprinted by size + CRC32, and a member whose bytes are already on the
card is *moved* into place (same volume: instant, no flash wear).  Only
members that are nowhere on the card are fetched, via the per-member endpoint
``GET /api/v1/roms/{rom_id}/file/{name}`` - but that needs the catalog, so the
missing members are written to ``<card MSU dir>/.fetch.tsv`` for
``--fetch`` (see ``fetch_missing``).

Saves: the FXPak names a save after the cart it loaded.  When a pack's cart
changes name, an existing save under the old cart name is copied to the new
one (copied, not moved - the old name may also belong to the plain ROM in
``roms/``).

Folders that end up empty are removed.  What is left in other folders is an
old version of a pack the server now supplies: it is moved to
``<card>/../_MSU_replaced/`` for the user to delete, unless the folder is
named in ``--keep`` (a game the server has no pack for).

Card fingerprints are cached in ``--cache`` (path, size, mtime -> CRC) so the
apply pass doesn't reread tens of GB off the card.

Dry run by default.
"""

from __future__ import annotations

import argparse
import binascii
import json
import os
import shutil
import sys
from collections import defaultdict
from pathlib import Path

CART_EXTS = (".sfc", ".smc")


def crc32(path: Path) -> int:
    h = 0
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1 << 20)
            if not chunk:
                return h & 0xFFFFFFFF
            h = binascii.crc32(chunk, h)


sys.path.insert(0, str(Path(__file__).resolve().parent))
from msu_pick import title_key  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest")
    ap.add_argument("card_dir")
    ap.add_argument("saves_dir")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--fetch-list", default=None)
    ap.add_argument("--cache", default=None)
    ap.add_argument("--keep", action="append", default=[],
                    help="card folder to leave in place (no server pack)")
    args = ap.parse_args()
    cache: dict = {}
    if args.cache and Path(args.cache).exists():
        cache = json.loads(Path(args.cache).read_text(encoding="utf-8"))

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    card = Path(args.card_dir)
    saves = Path(args.saves_dir)

    wanted_sizes = {size for members in manifest.values() for _, size, _ in members}

    # Fingerprint only card files whose size some server member has.
    by_key: dict[tuple[int, int], list[Path]] = defaultdict(list)
    old_carts: dict[str, list[Path]] = defaultdict(list)  # folder -> carts
    all_files = [p for p in card.rglob("*") if p.is_file() and not p.name.startswith(".")]
    for p in all_files:
        if p.suffix.lower() in CART_EXTS:
            old_carts[p.relative_to(card).parts[0]].append(p)
        st = p.stat()
        size = st.st_size
        if size in wanted_sizes:
            ck = f"{p}|{size}|{int(st.st_mtime)}"
            if ck not in cache:
                cache[ck] = crc32(p)
            by_key[(size, cache[ck])].append(p)
    if args.cache:
        Path(args.cache).write_text(json.dumps(cache), encoding="utf-8")

    moves: list[tuple[Path, Path]] = []
    fetch: list[tuple[str, str, int]] = []
    claimed: set[Path] = set()
    cart_renames: list[tuple[str, str]] = []  # old cart stem -> new cart stem
    for zip_name, members in sorted(manifest.items()):
        target = card / zip_name[:-4]
        new_cart = next((n for n, _, _ in members if n.lower().endswith(CART_EXTS)), None)
        for name, size, crc in members:
            dest = target / name
            sources = [p for p in by_key.get((size, crc), []) if p not in claimed]
            already = next((p for p in sources if p == dest), None)
            if already is not None:
                claimed.add(already)
                continue
            if sources:
                src = sources[0]
                claimed.add(src)
                moves.append((src, dest))
                if name == new_cart and src.suffix.lower() in CART_EXTS and src.stem != Path(name).stem:
                    cart_renames.append((src.stem, Path(name).stem))
            else:
                fetch.append((zip_name, name, size))
        # A pack whose cart is being fetched fresh still inherits the save of
        # whatever cart the card had for the same game folder.
    fetch_bytes = sum(s for _, _, s in fetch)

    # Saves: the old cart of the same game (by title), wherever it sat.
    moved_srcs = {src for src, _ in moves}
    for zip_name, members in manifest.items():
        new_cart = next((n for n, _, _ in members if n.lower().endswith(CART_EXTS)), None)
        if not new_cart:
            continue
        key = title_key(zip_name)
        for folder_name, carts in old_carts.items():
            if folder_name in args.keep:
                continue
            for c in carts:
                if title_key(c.stem + ".zip") != key and title_key(c.parent.name + ".zip") != key:
                    continue
                pair = (c.stem, Path(new_cart).stem)
                if pair[0] != pair[1] and pair not in cart_renames:
                    cart_renames.append(pair)

    leftovers = sorted({p.relative_to(card).parts[0] for p in all_files if p not in claimed}
                       - {z[:-4] for z in manifest})

    print(f"move/rename in place: {len(moves)} files")
    print(f"fetch from server:    {len(fetch)} files, {fetch_bytes / 2**30:.1f} GB "
          f"in {len({z for z, _, _ in fetch})} packs")
    free = shutil.disk_usage(card).free
    print(f"card free: {free / 2**30:.1f} GB")
    print(f"cart renames (save copies): {len(cart_renames)}")
    for old, new in cart_renames:
        srm = saves / f"{old}.srm"
        exists = srm.exists() or (saves / f"{old}.SRM").exists()
        print(f"   {old}  ->  {new}{'   [save]' if exists else ''}")
    print("folders with files no server pack uses (left alone unless all their files moved):")
    for d in leftovers:
        print("  ", d)

    if args.fetch_list:
        Path(args.fetch_list).write_text(
            "".join(f"{z}\t{n}\t{s}\n" for z, n, s in fetch), encoding="utf-8")

    if not args.apply:
        return 0

    for src, dest in moves:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest.unlink()
        os.replace(src, dest)
    for old, new in cart_renames:
        for ext in (".srm", ".SRM"):
            srm = saves / f"{old}{ext}"
            if srm.exists() and not (saves / f"{new}.srm").exists():
                shutil.copy2(srm, saves / f"{new}.srm")
    # Drop folders emptied by the moves, and stale non-pack files inside
    # target folders (old sidecars the new pack doesn't have).
    targets = {z[:-4] for z in manifest}
    replaced = card.parent / "_MSU_replaced"
    for zip_name, members in manifest.items():
        folder = card / zip_name[:-4]
        keep = {n for n, _, _ in members}
        if folder.is_dir():
            for p in folder.iterdir():
                if p.is_file() and p.name not in keep:
                    (replaced / folder.name).mkdir(parents=True, exist_ok=True)
                    os.replace(p, replaced / folder.name / p.name)
    for d in sorted(card.iterdir()):
        if d.is_dir() and d.name not in targets:
            if not any(p.is_file() for p in d.rglob("*")):
                shutil.rmtree(d)
            elif d.name not in args.keep:
                replaced.mkdir(exist_ok=True)
                os.replace(d, replaced / d.name)
    print("applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
