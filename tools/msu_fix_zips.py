"""Rebuild MSU pack zips that wrap a folder, so every pack is flat.

    python tools/msu_fix_zips.py <plan.txt> <out dir> <new plan.txt>

Reads msu_pick.py's plan.  A zip whose members all sit under one folder,
or that carries pack junk (author's ``.srm``, ``.asm`` sources…), is
rewritten into ``<out dir>/<destination name>`` with the folder hoisted and
the junk gone — the layout ``shared.msu.plan_extraction`` would produce on a
device, now baked into the archive itself.  Members are stored rather than
deflated: PCM does not compress, and stored zips are what the curated sets
ship anyway.  The new plan points those entries at the rebuilt file; the
rest are untouched and pass through.  Folder-shaped packs (Mega Drive) pass
through too.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared import msu  # noqa: E402


def needs_fix(zf: zipfile.ZipFile) -> bool:
    members = [i.filename for i in zf.infolist() if not i.is_dir()]
    root, stripped = msu.strip_common_root(members)
    return bool(root) or any(msu.is_junk(n) for n in stripped)


def rebuild(src: Path, dst: Path) -> int:
    with zipfile.ZipFile(src) as zin:
        members = [i.filename for i in zin.infolist() if not i.is_dir()]
        layout = msu.plan_extraction(msu.MSU1, members)
        tmp = dst.with_suffix(".zip.part")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED, allowZip64=True) as zout:
            for member, rel in layout:
                info = zin.getinfo(member)
                out = zipfile.ZipInfo(rel, date_time=info.date_time)
                out.compress_type = zipfile.ZIP_STORED
                out.external_attr = info.external_attr
                with zin.open(info) as fh:
                    zout.writestr(out, fh.read())
        tmp.replace(dst)
        return len(layout)


def main() -> int:
    plan, out_dir, new_plan = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = []
    fixed = 0
    for line in plan.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        src_s, dest = line.split("\t")
        src = Path(src_s)
        if src.is_dir() or src.suffix.lower() != ".zip":
            lines.append(f"{src}\t{dest}")
            continue
        with zipfile.ZipFile(src) as zf:
            fix = needs_fix(zf)
        if not fix:
            lines.append(f"{src}\t{dest}")
            continue
        target = out_dir / dest
        if not target.exists():
            n = rebuild(src, target)
            print(f"rebuilt {dest}  ({n} members, {target.stat().st_size / 2**20:.0f} MB)")
        fixed += 1
        lines.append(f"{target}\t{dest}")
    new_plan.write_text("".join(l + "\n" for l in lines), encoding="utf-8")
    print(f"{fixed} rebuilt, {len(lines) - fixed} untouched -> {new_plan}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
