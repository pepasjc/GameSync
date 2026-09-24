"""Write Mega Drive MSU-MD / MD+ packs to a Mega EverDrive Pro card, named
after the plain ROM so pack and ROM share one save.

    cd server && uv run python ../tools/msu_card_md.py <plan.txt> <card MSU dir> <card roms dir> [--apply]

``plan.txt`` is msu_pick.py's output (``<source zip or folder>\\t<name>``).
Each pack lands in ``<card MSU dir>/<name>/``, hoisted out of any wrapping
folder, junk dropped.  The EverDrive saves to ``EDMD/SAVE/<rom stem>.srm``
and pairs a ROM with the ``.cue`` of the same stem, so the cart and its cue
take the plain ROM's name - the ROM on the card (any subfolder of ``roms
dir``) whose DAT identity matches the pack's, else the pack name with the
``(MD+)`` / ``(MSU-MD)`` / ``[Hack by …]`` tags removed.  An MSU-MD audio
``.bin`` named after the cart is renamed with it and the cue's FILE line
rewritten; MD+ WAV tracks keep their names.

Dry run by default.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for c in (ROOT, ROOT / "server"):
    if str(c) not in sys.path:
        sys.path.insert(0, str(c))

from app.services import dat_normalizer  # noqa: E402
from app.services.rom_scanner import _identify_rom_slug  # noqa: E402
from shared import msu  # noqa: E402

ROM_EXTS = (".md", ".gen", ".bin", ".smd")
_FORMAT_TAG_RE = re.compile(r"\s*\((?:MD\+|MSU-MD)\)")


def plain_name(pack: str) -> str:
    name = msu.strip_hack_tags(_FORMAT_TAG_RE.sub("", pack))
    return re.sub(r"\s{2,}", " ", name).strip()


def members(src: Path) -> list[tuple[str, callable]]:
    """(relative name, opener) for every file worth writing."""
    if src.is_dir():
        return [(p.name, (lambda p=p: open(p, "rb"))) for p in sorted(src.iterdir())
                if p.is_file() and not msu.is_junk(p.name)]
    zf = zipfile.ZipFile(src)
    names = [i.filename for i in zf.infolist() if not i.is_dir()]
    return [(rel, (lambda m=m: zf.open(m)))
            for m, rel in msu.plan_extraction(msu.MD_PLUS, names)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("card_dir")
    ap.add_argument("roms_dir")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    dat_normalizer.init(ROOT / "server" / "data" / "dats")
    norm = dat_normalizer.get()

    by_tid: dict[str, list[str]] = {}
    for dirpath, _dirs, files in os.walk(args.roms_dir):
        for f in files:
            if f.lower().endswith(ROM_EXTS):
                tid, _c, _s = _identify_rom_slug("MD", Path(f), norm)
                by_tid.setdefault(tid, []).append(Path(f).stem)

    card = Path(args.card_dir)
    for line in Path(args.plan).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        src_s, dest = line.split("\t")
        pack = dest[:-4] if dest.lower().endswith(".zip") else dest
        target = plain_name(pack)
        tid, _c, _s = _identify_rom_slug("MD", Path(target + ".md"), norm)
        # Closest to the pack's own name wins: same leading tags, no
        # beta / compilation variants, then the shortest.
        head = target.split(" [")[0]
        oncard = sorted(by_tid.get(tid, []), key=lambda s: (
            s != target, not s.startswith(head), bool(re.search(r"Beta|Proto|Smash Pack|Collection", s)),
            "(USA" not in s, len(s)))
        new = oncard[0] if oncard else target
        src = Path(src_s)
        files = members(src)
        cart = msu.rom_member(msu.MD_PLUS, [n for n, _ in files]) or ""
        cue = next((n for n, _ in files if n.lower().endswith(".cue")), "")
        old = Path(cue or cart).stem
        print(f"{pack}\n    -> {new}{'' if oncard else '   (no plain ROM on card)'}")
        if not args.apply:
            continue
        folder = card / pack
        folder.mkdir(parents=True, exist_ok=True)
        renamed: dict[str, str] = {}
        for name, opener in files:
            stem, ext = os.path.splitext(name)
            if name == cart:
                out = new + ext
            elif ext.lower() == ".cue" or stem == old:
                out = new + ext
            else:
                out = name
            renamed[name] = out
            tmp = folder / (out + ".part")
            with opener() as fi, open(tmp, "wb") as fo:
                shutil.copyfileobj(fi, fo, 1 << 20)
            os.replace(tmp, folder / out)
        cue_out = folder / renamed.get(cue, "") if cue else None
        if cue_out and cue_out.is_file():
            text = cue_out.read_text(encoding="utf-8", errors="replace")
            for a, b in renamed.items():
                if a != b and a != cue:
                    text = text.replace(f'"{a}"', f'"{b}"')
            cue_out.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
