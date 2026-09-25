"""Re-compress the library's zstd CHDs from a desktop.

The server can re-compress them itself (SYNC_CHD_RECOMPRESS_ZSTD), but
running chdman on the Pi coincided with two crashes on 2026-09-24, so it is
switched off there and done from here instead, with the library's drive
either attached to this machine or reached over the Pi's ``HD`` share.

The new copy is written and verified on a fast local disk (``--stage``),
then copied back in one sequential pass. Writing it next to the original
made the 9 TB USB drive - very likely SMR - collapse to ~2 MB/s after a few
GB, with chdman reading and writing on it at once.

The rules are the server's (``app.services.chd_normalize``): a file is
replaced only when ``chdman verify`` passes on the new copy, it is no longer
zstd, and the uncompressed image's SHA-1 in its header is unchanged. The
copy on the library drive is then compared with the verified local one
before it takes the original's place. Anything else leaves the original as
it was.

    cd server
    uv run python ../tools/recompress_zstd_chds.py --share J:/ --dry-run
    uv run python ../tools/recompress_zstd_chds.py --share J:/ --chdman ../tools/chdman.exe

(From server/ through uv, because it imports the server's own code.)

The list holds the Pi's paths (``/mnt/hd/...``); ``--share`` maps that prefix
to wherever the drive is mounted here. chdman must be MAME 0.264 or newer:
older builds cannot read zstd at all ("Unknown compression type").
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from app.services import chd_normalize as cn  # noqa: E402

DEFAULT_LIST = Path(__file__).with_name("chd_zstd_pending.txt")
CHUNK = 8 * 1024 * 1024


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["chdman", *args], capture_output=True, text=True)


def recompress(path: Path, stage: Path, threads: int) -> tuple[bool, str]:
    """Rewrite one zstd CHD via a local staging copy. ``(changed, message)``."""
    header = cn.read_header(path)
    if not cn.is_zstd(header):
        return False, "not zstd"
    cd = any(c.startswith(b"cd") for c in cn._codecs(header))
    local = stage / (path.name + ".new")
    remote_tmp = path.with_name(path.name + ".recompress.tmp")
    try:
        result = _run("copy", "-f", "-np", str(threads), "-i", str(path),
                      "-o", str(local),
                      "-c", cn.CD_CODECS if cd else cn.DVD_CODECS)
        if result.returncode != 0 or not local.exists():
            return False, "chdman copy: " + (result.stderr or result.stdout).strip()[-200:]
        verify = _run("verify", "-i", str(local))
        if verify.returncode != 0:
            return False, "chdman verify failed: " + (verify.stderr or verify.stdout).strip()[-200:]
        new_header = cn.read_header(local)
        if cn.is_zstd(new_header) or cn.raw_sha1(new_header) != cn.raw_sha1(header):
            return False, "re-compressed image does not match the original"

        # One sequential write back to the library drive, then a check that
        # what landed there is byte-for-byte the copy that was verified.
        shutil.copyfile(local, remote_tmp)
        if _sha256(remote_tmp) != _sha256(local):
            return False, "copy on the library drive differs from the verified one"
        try:
            shutil.copymode(path, remote_tmp)
        except OSError:
            pass
        os.replace(remote_tmp, path)
        return True, "re-compressed"
    except OSError as exc:
        return False, str(exc)
    finally:
        for leftover in (local, remote_tmp):
            try:
                if leftover.exists():
                    leftover.unlink()
            except OSError:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", type=Path, default=DEFAULT_LIST,
                        help="one Pi path per line (default: %(default)s)")
    parser.add_argument("--pi-root", default="/mnt/hd",
                        help="prefix of the paths in the list")
    parser.add_argument("--share", default="Z:/",
                        help="where that prefix is mounted here")
    parser.add_argument("--stage", type=Path,
                        default=Path(tempfile.gettempdir()) / "chd_recompress",
                        help="fast local folder for the new copies (default: %(default)s)")
    parser.add_argument("--chdman", help="chdman executable (0.264+)")
    parser.add_argument("--threads", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    parser.add_argument("--dry-run", action="store_true",
                        help="only report which files are still zstd")
    args = parser.parse_args()

    if args.chdman:
        os.environ["PATH"] = str(Path(args.chdman).resolve().parent) + os.pathsep \
            + os.environ.get("PATH", "")
    if not args.dry_run and not shutil.which("chdman"):
        print("chdman not found; pass --chdman path/to/chdman.exe (MAME 0.264+)")
        return 2

    prefix = args.pi_root.rstrip("/") + "/"
    paths = []
    for line in args.list.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(prefix):
            line = args.share.rstrip("/\\") + "/" + line[len(prefix):]
        paths.append(Path(line))

    pending = []
    for path in paths:
        try:
            if cn.is_zstd(cn.read_header(path)):
                pending.append(path)
        except OSError as exc:
            print("  unreadable: %s (%s)" % (path, exc))
    total = sum(p.stat().st_size for p in pending)
    print("%d of %d still zstd (%.1f GB)" % (len(pending), len(paths), total / 1e9))
    if args.dry_run or not pending:
        return 0

    args.stage.mkdir(parents=True, exist_ok=True)
    done = failed = 0
    for index, path in enumerate(pending, 1):
        started = time.time()
        print("[%d/%d] %s (%.2f GB)" % (index, len(pending), path.name,
                                       path.stat().st_size / 1e9), flush=True)
        changed, message = recompress(path, args.stage, args.threads)
        print("        %s (%.0fs)" % (message, time.time() - started), flush=True)
        if changed:
            done += 1
        else:
            failed += 1
    print("re-compressed %d, left as they were %d" % (done, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
