"""Pair split MSU-MD releases - cart zip + ``(Soundpack)`` zip - into
complete, flat packs the catalog can index.

    python tools/msumd_compose.py "<set dir>" <out dir> [--report file]

The curated Mega Drive set ships two shapes side by side.  The old one is
a complete zip (``Game (USA) (MSU-MD) [Hack by X].zip``: ``.md`` + ``.cue``
+ ``.bin``).  The new one splits it: ``Game (USA) [MSU-MD hack by X].zip``
holds only the patched cart, ``(Soundpack) Game (USA) [MSU-MD … Arranged by
Y].zip`` only the audio - so several soundtracks can share one cart and a
soundtrack can serve two carts (``Game (USA) ~ Game (Europe)``).  A game
with a complete zip is left alone; for every other cart a soundpack is
found by title and the pair is written as ``<out dir>/<cart stem>.zip``
with the cue's FILE line pointed at the bin under the cart's stem, so the
three files agree the way every MSU-MD player wants.

Choices when a game offers more than one:

* cart - the least modified one (no colour hack / cheat / region hack),
  then the shortest name;
* soundpack - a plain ``Arranged by`` over a remix / vocal / alternate
  arrangement, then the format author (ArcadeTV), then the newest date.

Every pairing and every alternative not taken goes in the report.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

_BRACKET_RE = re.compile(r"\s*\[[^\]]*\]")
_REV_RE = re.compile(r"\s*\(Rev [^)]*\)")
_CART_MOD_RE = re.compile(
    r"Color hack|Invincibility|cheat|region hacked|Edition by|\[m |port\]|\[Add by",
    re.IGNORECASE,
)
_SOUND_PLAIN_RE = re.compile(r"\[MSU-MD Arranged by [^\]]*\]$")
_DATE_RE = re.compile(r"v(\d{8})")


def game_title(name: str) -> str:
    """Title with tags gone: what a cart and its soundpack share."""
    stem = name[:-4] if name.lower().endswith(".zip") else name
    stem = stem.replace("(Soundpack) ", "")
    stem = _BRACKET_RE.sub("", stem)
    stem = stem.replace("(MSU-MD)", "")
    return re.sub(r"\s{2,}", " ", stem).strip()


def title_keys(name: str) -> set[str]:
    """Match keys: a ``~``-joined soundpack title yields one per game, and
    revision tags are ignored so ``(Rev A)`` carts find an untagged pack."""
    keys = set()
    for part in game_title(name).split(" ~ "):
        part = _REV_RE.sub("", part).strip()
        keys.add(re.sub(r"[^a-z0-9]+", "", part.lower()))
    return keys


def cart_rank(name: str) -> tuple:
    return (len(_CART_MOD_RE.findall(name)), name.count("["), len(name))


def sound_rank(name: str) -> tuple:
    plain = 0 if _SOUND_PLAIN_RE.search(name[:-4]) else 1
    by_author = 0 if "Arranged by ArcadeTV" in name else 1
    date = _DATE_RE.search(name)
    return (plain, by_author, -int(date.group(1)) if date else 0, len(name))


def classify(folder: Path):
    full, carts, sounds = {}, defaultdict(list), defaultdict(list)
    for f in sorted(folder.iterdir()):
        if f.suffix.lower() != ".zip":
            continue
        with zipfile.ZipFile(f) as zf:
            exts = {Path(n).suffix.lower() for n in zf.namelist()}
        keys = title_keys(f.name)
        if ".md" in exts and ".cue" in exts:
            for k in keys:
                full.setdefault(k, []).append(f)
        elif ".md" in exts:
            for k in keys:
                carts[k].append(f)
        elif ".cue" in exts:
            for k in keys:
                sounds[k].append(f)
    return full, carts, sounds


def compose(cart_zip: Path, sound_zip: Path, out: Path) -> str:
    with zipfile.ZipFile(cart_zip) as zc, zipfile.ZipFile(sound_zip) as zs:
        cart = next(n for n in zc.namelist() if n.lower().endswith(".md"))
        stem = Path(cart).stem
        cue = next(n for n in zs.namelist() if n.lower().endswith(".cue"))
        bins = [n for n in zs.namelist() if n.lower().endswith((".bin", ".wav"))]
        cue_text = zs.read(cue).decode("utf-8", "replace")
        tmp = out.with_suffix(".zip.part")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED, allowZip64=True) as zo:
            zo.writestr(f"{stem}.md", zc.read(cart))
            renamed = {}
            for i, b in enumerate(bins):
                new = f"{stem}{Path(b).suffix}" if len(bins) == 1 else f"{stem} - {i + 1:02d}{Path(b).suffix}"
                renamed[Path(b).name] = new
                zo.writestr(new, zs.read(b))
            for old, new in renamed.items():
                cue_text = cue_text.replace(f'"{old}"', f'"{new}"')
            zo.writestr(f"{stem}.cue", cue_text)
        tmp.replace(out)
        return stem


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("set_dir")
    ap.add_argument("out_dir")
    ap.add_argument("--report", default="msumd_compose.md")
    args = ap.parse_args()
    folder, out_dir = Path(args.set_dir), Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    full, carts, sounds = classify(folder)
    lines = ["# MSU-MD pairing", ""]
    built = skipped = 0
    for key in sorted(carts):
        if key in full:
            lines.append(f"- skip (complete zip exists): {carts[key][0].name}")
            skipped += 1
            continue
        packs = sounds.get(key)
        if not packs:
            lines.append(f"- **no soundpack**: {carts[key][0].name}")
            continue
        cart = sorted(carts[key], key=lambda p: cart_rank(p.name))[0]
        sound = sorted(packs, key=lambda p: sound_rank(p.name))[0]
        with zipfile.ZipFile(cart) as zc:
            stem = Path(next(n for n in zc.namelist() if n.lower().endswith(".md"))).stem
        target = out_dir / f"{stem}.zip"
        if not target.exists():
            compose(cart, sound, target)
        built += 1
        lines.append(f"- **{target.name}**")
        lines.append(f"    - cart: {cart.name}")
        for other in carts[key]:
            if other != cart:
                lines.append(f"    - cart not taken: {other.name}")
        lines.append(f"    - soundpack: {sound.name}")
        for other in packs:
            if other != sound:
                lines.append(f"    - soundpack not taken: {other.name}")
    orphan_sounds = [s[0].name for k, s in sounds.items() if k not in carts and k not in full]
    if orphan_sounds:
        lines += ["", "## Soundpacks with no cart", ""] + [f"- {n}" for n in sorted(set(orphan_sounds))]
    Path(args.report).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{built} composed, {skipped} had a complete zip -> {out_dir}; report {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
