"""Put compiled bytecode inside the zipapp, compiled by the device's Python.

zipimport never writes a bytecode cache, so a plain .pyz recompiles every
module on every launch: 3.5 s of a MiSTer's start before anything is drawn,
against 1.5 s with the bytecode already in the archive.

It has to be done on the device. The build machine's Python is not 3.9, and
bytecode from another version is rejected on its magic number. That
rejection is also the safety net: a MiSTer whose Linux update brings a new
Python simply ignores the .pyc files and compiles from source as before.

The .pyc files are "unchecked hash" ones (PEP 552): zipimport would
otherwise compare them against the zip entry's two-second DOS timestamp.
Nothing can edit a file inside the archive, and the whole archive is
replaced on update and recompiled then, so there is nothing to check.
"""

from __future__ import annotations

import importlib.util
import os
import zipfile
from importlib import _bootstrap_external


def compiles(name: str) -> bool:
    """Does this archive member get a .pyc? __main__ never would be read:
    runpy executes it from source."""
    return name.endswith(".py") and name != "__main__.py"


def compile_archive(path: str) -> int:
    """Rewrite *path* with a .pyc beside every module. Returns how many."""
    with zipfile.ZipFile(path) as source:
        entries = [(info, source.read(info)) for info in source.infolist()
                   if not info.filename.endswith(".pyc")]  # rebuilt below

    # A zipapp starts with a shebang line ahead of the zip data; keep it.
    with open(path, "rb") as handle:
        first = handle.readline()
    shebang = first if first.startswith(b"#!") else b""

    temp = path + ".tmp"
    count = 0
    with open(temp, "wb") as raw:
        # Before the ZipFile opens, so its offsets are counted past it.
        raw.write(shebang)
        with zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as target:
            for info, data in entries:
                target.writestr(info, data)
                if not compiles(info.filename):
                    continue
                code = compile(data, os.path.join(path, info.filename),
                               "exec", dont_inherit=True)
                pyc = _bootstrap_external._code_to_hash_pyc(
                    code, importlib.util.source_hash(data), checked=False)
                entry = zipfile.ZipInfo(info.filename[:-3] + ".pyc",
                                        info.date_time)
                entry.compress_type = zipfile.ZIP_DEFLATED
                target.writestr(entry, bytes(pyc))
                count += 1
    os.replace(temp, path)
    return count
