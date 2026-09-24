"""Fetch the pack members msu_card_sync.py found missing on the card.

    python tools/msu_card_fetch.py <fetch.tsv> <user@host> <server msu dir> <card MSU dir>

One pack at a time: the server's zip is copied to a local stage with scp
(fast over the LAN, and the card only sees sequential writes), the missing
members are extracted into ``<card>/<zip stem>/``, and the stage is emptied
before the next pack.  A pack whose members are all present already is
skipped, so an interrupted run resumes where it stopped.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections import defaultdict
from pathlib import Path


def main() -> int:
    listf, host, remote, card = sys.argv[1:5]
    card_dir = Path(card)
    wanted: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for line in Path(listf).read_text(encoding="utf-8").splitlines():
        if line.strip():
            z, name, size = line.split("\t")
            wanted[z].append((name, int(size)))

    stage = Path(tempfile.gettempdir()) / "msu_stage"
    stage.mkdir(exist_ok=True)
    failed = 0
    for i, (zip_name, members) in enumerate(sorted(wanted.items()), 1):
        dest = card_dir / zip_name[:-4]
        todo = [(n, s) for n, s in members
                if not ((dest / n).exists() and (dest / n).stat().st_size == s)]
        print(f"[{i}/{len(wanted)}] {zip_name} ({len(todo)} files)", flush=True)
        if not todo:
            continue
        local = stage / "pack.zip"
        # OpenSSH 9 scp speaks SFTP: the remote path needs no shell quoting.
        r = subprocess.run(["scp", "-q", f"{host}:{remote}/{zip_name}", str(local)])
        if r.returncode != 0:
            print(f"  FAILED fetch: {zip_name}", flush=True)
            failed += 1
            continue
        try:
            dest.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(local) as zf:
                for name, _size in todo:
                    part = dest / (name + ".part")
                    with zf.open(name) as fi, open(part, "wb") as fo:
                        shutil.copyfileobj(fi, fo, 1 << 20)
                    os.replace(part, dest / name)
        except Exception as exc:  # noqa: BLE001
            print(f"  FAILED extract: {zip_name}: {exc}", flush=True)
            failed += 1
        finally:
            local.unlink(missing_ok=True)
    print(f"done: {len(wanted)} packs, {failed} failed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
