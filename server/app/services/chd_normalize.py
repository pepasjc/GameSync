"""Re-compress zstd CHDs in the ROM library to the standard codecs.

chdman 0.264+ writes zstd (``zstd`` / ``cdzs``) when asked, and plenty of
CHDs in the wild use it.  The server's own CHD reader (``shared.chd``,
used for RetroAchievements disc hashes) and a lot of older tooling cannot
decode zstd, so such a disc is effectively opaque: no exact RA badge, and
it may not play everywhere.

After each catalog scan every CHD's header is checked (64 bytes - cheap) and
any zstd one is rewritten with ``chdman copy`` to the codecs chdman itself
defaults to for that kind of image (CD: cdlz/cdzl/cdfl; DVD / hard disk:
lzma/zlib/huff/flac).  The original is replaced only when ``chdman
verify`` passes on the new file *and* its raw-data SHA-1 - which chdman
computes over the uncompressed image - equals the original's: the disc
is bit-identical, only its compression changed.  Anything else leaves the
original alone and remembers the failure so it is not retried every scan.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable, Iterable, Optional

logger = logging.getLogger(__name__)

_MAGIC = b"MComprHD"
_ZSTD_CODECS = (b"zstd", b"cdzs")
CD_CODECS = "cdlz,cdzl,cdfl"
DVD_CODECS = "lzma,zlib,huff,flac"

#: Paths that failed once this process - not retried until restart.
_failed: set[str] = set()
_lock = threading.Lock()


def read_header(path: Path) -> bytes:
    with open(path, "rb") as fh:
        return fh.read(124)


def _codecs(header: bytes) -> list[bytes]:
    """The four compressor tags of a v5 header (older versions have none)."""
    if header[:8] != _MAGIC or int.from_bytes(header[12:16], "big") < 5:
        return []
    return [header[16 + 4 * i: 20 + 4 * i] for i in range(4)]


def is_zstd(header: bytes) -> bool:
    return any(c in _ZSTD_CODECS for c in _codecs(header))


def raw_sha1(header: bytes) -> bytes:
    """SHA-1 of the uncompressed image, as recorded in a v5 header."""
    return header[64:84]


def _chdman_cmd(*args: str) -> list[str]:
    cmd = ["chdman", *args]
    # Low priority where the OS offers it: this is background housekeeping
    # on the same box that serves the API.
    if os.name == "posix" and shutil.which("nice"):
        cmd = ["nice", "-n", "19", *cmd]
    return cmd


def recompress(path: Path, threads: int = 2) -> tuple[bool, str]:
    """Rewrite one zstd CHD in place.  ``(changed, message)``."""
    try:
        header = read_header(path)
    except OSError as exc:
        return False, str(exc)
    if not is_zstd(header):
        return False, "not zstd"
    cd = any(c.startswith(b"cd") for c in _codecs(header))
    tmp = path.with_name(path.name + ".recompress.tmp")
    try:
        result = subprocess.run(
            _chdman_cmd("copy", "-f", "-np", str(threads), "-i", str(path), "-o", str(tmp),
                        "-c", CD_CODECS if cd else DVD_CODECS),
            capture_output=True, text=True,
        )
        if result.returncode != 0 or not tmp.exists():
            return False, "chdman copy: " + (result.stderr or result.stdout).strip()[-200:]
        verify = subprocess.run(_chdman_cmd("verify", "-i", str(tmp)), capture_output=True, text=True)
        new_header = read_header(tmp)
        if verify.returncode != 0:
            return False, "chdman verify failed: " + (verify.stderr or verify.stdout).strip()[-200:]
        if is_zstd(new_header) or raw_sha1(new_header) != raw_sha1(header):
            return False, "re-compressed image does not match the original"
        # Keep the file's mode; the mtime changes, which is what tells the
        # catalog and the RA index to look at it again.
        try:
            shutil.copymode(path, tmp)
        except OSError:
            pass
        os.replace(tmp, path)
        return True, "re-compressed"
    except OSError as exc:
        return False, str(exc)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def normalize(paths: Iterable[Path], should_stop: Optional[Callable[[], bool]] = None,
              threads: int = 2) -> dict[str, int]:
    """Re-compress every zstd CHD among ``paths``.  Counts by outcome."""
    counts = {"checked": 0, "recompressed": 0, "failed": 0}
    if not shutil.which("chdman"):
        return counts
    with _lock:                       # one pass at a time
        for path in paths:
            if should_stop and should_stop():
                break
            if path.suffix.lower() != ".chd" or str(path) in _failed:
                continue
            counts["checked"] += 1
            try:
                if not is_zstd(read_header(path)):
                    continue
            except OSError:
                continue
            changed, message = recompress(path, threads)
            if changed:
                counts["recompressed"] += 1
                logger.info("[chd_normalize] %s: %s", path.name, message)
            else:
                counts["failed"] += 1
                _failed.add(str(path))
                logger.warning("[chd_normalize] left %s as it was: %s", path.name, message)
    return counts


def catalog_chds(entries: Iterable, rom_dir: Optional[Path]) -> list[Path]:
    """Absolute paths of the single-file CHDs in a catalog listing."""
    out = []
    for entry in entries:
        rel = getattr(entry, "path", "") or ""
        if not rel.lower().endswith(".chd") or getattr(entry, "is_bundle", False):
            continue
        path = Path(rel)
        if not path.is_absolute() and rom_dir is not None:
            path = rom_dir / path
        out.append(path)
    return out
