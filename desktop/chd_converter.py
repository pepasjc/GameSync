"""CHD conversion helpers.

Wraps the MAME `chdman` tool (expected to live in `<repo>/tools/chdman.exe`) so
the desktop app can convert .cue/.gdi/.iso sources into .chd archives for CD
based systems (PS1, Saturn, Sega CD, Dreamcast, PSP, ...).

Only `createcd` is used — it accepts all three source formats on chdman 0.251.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal


# Source extensions that chdman's `createcd` can consume.
CONVERTIBLE_EXTENSIONS = {".cue", ".gdi", ".iso"}

# Windows-only flag to suppress the console window when invoking chdman.
_NO_WINDOW_FLAG = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def find_chdman() -> Path | None:
    """Locate the chdman executable.

    Search order:
      1. `<repo_root>/tools/chdman(.exe)` — anchored on this file's location,
         not the CWD, so the app works regardless of how it was launched.
      2. `chdman` on PATH.

    Returns the first match, or None if nothing is found.
    """
    # `desktop/chd_converter.py` → parents[0] = desktop/, parents[1] = repo root
    repo_root = Path(__file__).resolve().parents[1]
    tools_dir = repo_root / "tools"
    for name in ("chdman.exe", "chdman"):
        candidate = tools_dir / name
        if candidate.is_file():
            return candidate

    path_hit = shutil.which("chdman") or shutil.which("chdman.exe")
    if path_hit:
        return Path(path_hit)
    return None


def get_chdman_version(chdman: Path) -> str:
    """Return a short version string pulled from `chdman`'s banner.

    chdman prints "chdman - MAME Compressed Hunks of Data (CHD) manager 0.251 (mame0251)"
    on its first line when run with no arguments.  Returns "unknown" on any failure.
    """
    try:
        result = subprocess.run(
            [str(chdman)],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=_NO_WINDOW_FLAG,
        )
    except Exception:
        return "unknown"
    banner = (result.stdout or result.stderr or "").splitlines()
    if not banner:
        return "unknown"
    match = re.search(r"(\d+\.\d+[\w.-]*)", banner[0])
    return match.group(1) if match else "unknown"


def _resolve_reference(base_dir: Path, ref: str) -> Path:
    """Resolve a filename reference from a cue/gdi relative to its parent dir."""
    candidate = Path(ref)
    if not candidate.is_absolute():
        candidate = base_dir / candidate
    return candidate


def parse_cue_tracks(path: Path) -> list[Path]:
    """Return the list of data files referenced by a `.cue` sheet.

    Handles both `FILE "name.bin" BINARY` and `FILE name.bin BINARY` forms.
    Only returns paths that exist on disk.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    tracks: list[Path] = []
    seen: set[str] = set()
    base_dir = path.parent
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.upper().startswith("FILE"):
            continue
        # Prefer the quoted filename when present; otherwise take the second token.
        if '"' in stripped:
            parts = stripped.split('"')
            if len(parts) < 2:
                continue
            name = parts[1]
        else:
            parts = stripped.split()
            if len(parts) < 2:
                continue
            name = parts[1]
        resolved = _resolve_reference(base_dir, name)
        key = os.path.normcase(str(resolved))
        if key in seen:
            continue
        seen.add(key)
        if resolved.exists():
            tracks.append(resolved)
    return tracks


def parse_gdi_tracks(path: Path) -> list[Path]:
    """Return the list of track files referenced by a `.gdi` file.

    GDI format: first line = track count, subsequent lines =
    `<num> <lba> <type> <sector> <filename> [offset]` (filenames may be quoted).
    """
    try:
        # utf-8-sig gracefully strips any BOM from dumpers that add one.
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return []

    tracks: list[Path] = []
    seen: set[str] = set()
    base_dir = path.parent
    for i, line in enumerate(text.splitlines()):
        if i == 0:
            continue  # first line is the track count
        stripped = line.strip()
        if not stripped:
            continue
        try:
            parts = shlex.split(stripped)
        except ValueError:
            parts = stripped.split()
        if len(parts) < 5:
            continue
        name = parts[4]
        resolved = _resolve_reference(base_dir, name)
        key = os.path.normcase(str(resolved))
        if key in seen:
            continue
        seen.add(key)
        if resolved.exists():
            tracks.append(resolved)
    return tracks


def get_source_files_to_delete(source: Path) -> list[Path]:
    """Return the complete set of files that make up a convertible source.

    For `.cue` and `.gdi` this is the sheet plus each referenced track; for
    `.iso` it's just the iso.  Duplicates (case-insensitive on Windows) are
    removed.  Useful when the user opts to delete originals after a successful
    conversion.
    """
    ext = source.suffix.lower()
    files: list[Path] = [source]
    if ext == ".cue":
        files.extend(parse_cue_tracks(source))
    elif ext == ".gdi":
        files.extend(parse_gdi_tracks(source))

    result: list[Path] = []
    seen: set[str] = set()
    for f in files:
        key = os.path.normcase(str(f))
        if key in seen:
            continue
        seen.add(key)
        result.append(f)
    return result


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def find_convertible_sources(folder: Path, recursive: bool = True) -> list[dict]:
    """Scan ``folder`` for files chdman can turn into .chd archives.

    A `.bin` that's already referenced by a `.cue` in the same folder is NOT
    reported as a standalone source — the cue owns it.  This avoids double
    counting multi-track CDs.

    Each dict looks like:
        {
            "source": Path,          # the .cue / .gdi / .iso
            "ext": str,              # e.g. ".cue"
            "output": Path,          # where the .chd will land
            "related": list[Path],   # referenced track files (empty for .iso)
            "all_files": list[Path], # source + related
            "source_size": int,      # size of just the cue/gdi/iso
            "total_size": int,       # size of all files that make up the source
            "output_exists": bool,   # .chd already present
        }
    """
    if not folder.exists() or not folder.is_dir():
        return []

    iterator = folder.rglob("*") if recursive else folder.iterdir()
    files = [f for f in iterator if f.is_file()]

    # First pass: gather every track referenced by a .cue or .gdi so we can
    # skip reporting those as independent sources.
    owned: set[str] = set()
    for f in files:
        ext = f.suffix.lower()
        if ext == ".cue":
            for track in parse_cue_tracks(f):
                owned.add(os.path.normcase(str(track)))
        elif ext == ".gdi":
            for track in parse_gdi_tracks(f):
                owned.add(os.path.normcase(str(track)))

    results: list[dict] = []
    for source in sorted(files):
        ext = source.suffix.lower()
        if ext not in CONVERTIBLE_EXTENSIONS:
            continue
        # An .iso is its own data file, so it's fine if it's "owned" (it won't be).
        # A standalone .bin is skipped by CONVERTIBLE_EXTENSIONS anyway.
        if ext == ".cue":
            related = parse_cue_tracks(source)
        elif ext == ".gdi":
            related = parse_gdi_tracks(source)
        else:
            related = []
        output = source.with_suffix(".chd")
        all_files = [source, *related]
        total_size = sum(_file_size(f) for f in all_files)
        results.append(
            {
                "source": source,
                "ext": ext,
                "output": output,
                "related": related,
                "all_files": all_files,
                "source_size": _file_size(source),
                "total_size": total_size,
                "output_exists": output.exists(),
            }
        )
    return results


def convert_to_chd(
    chdman: Path, source: Path, output: Path, force: bool = True
) -> tuple[bool, str]:
    """Invoke ``chdman createcd`` to produce a .chd from ``source``.

    Returns (success, stderr_output).  On failure the partial output file, if
    any was created, is removed so the caller can retry cleanly.
    """
    args: list[str] = [
        str(chdman),
        "createcd",
        "-i",
        str(source),
        "-o",
        str(output),
    ]
    if force:
        args.append("-f")

    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            creationflags=_NO_WINDOW_FLAG,
            # cd into the source directory so any relative references in the
            # cue/gdi resolve correctly even if chdman needs them.
            cwd=str(source.parent),
        )
    except Exception as exc:
        return False, str(exc)

    ok = (
        result.returncode == 0
        and output.exists()
        and output.stat().st_size > 0
    )
    if not ok:
        # Clean up partial output so the folder doesn't fill with empty .chd files.
        try:
            if output.exists():
                output.unlink()
        except OSError:
            pass
    stderr = (result.stderr or "").strip() or (result.stdout or "").strip()
    return ok, stderr


class ConvertToChdWorker(QThread):
    """Background worker that converts a batch of sources to .chd.

    Signals
    -------
    progress(str)
        Human-readable status line, e.g. "Converting 3/12: Sonic CD (USA).cue".
    file_done(dict)
        Per-file result:
            {
                "source": Path,
                "output": Path,
                "ok": bool,
                "error": str,
                "deleted": list[Path],
                "skipped": bool,
            }
    finished(list)
        The full list of per-file result dicts when the batch completes.
    """

    progress = pyqtSignal(str)
    file_done = pyqtSignal(dict)
    finished = pyqtSignal(list)

    def __init__(
        self,
        chdman: Path,
        sources: list[dict],
        delete_originals: bool,
        parent=None,
    ):
        super().__init__(parent)
        self.chdman = chdman
        self.sources = sources
        self.delete_originals = delete_originals
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        results: list[dict] = []
        total = len(self.sources)
        for i, entry in enumerate(self.sources, start=1):
            if self._stop:
                break
            source: Path = entry["source"]
            output: Path = entry["output"]

            self.progress.emit(f"Converting {i}/{total}: {source.name}")

            ok, err = convert_to_chd(self.chdman, source, output)
            deleted: list[Path] = []
            skipped = False

            if ok and self.delete_originals:
                # Only delete when we're confident the chd is good: it exists
                # AND is non-empty (already checked inside convert_to_chd).
                for f in entry.get("all_files", [source]):
                    try:
                        if f.exists() and f != output:
                            f.unlink()
                            deleted.append(f)
                    except OSError as exc:
                        # Log but keep going; a failed delete isn't a failed conversion.
                        err = (err + f"\nFailed to delete {f.name}: {exc}").strip()

            record = {
                "source": source,
                "output": output,
                "ok": ok,
                "error": err,
                "deleted": deleted,
                "skipped": skipped,
            }
            results.append(record)
            self.file_done.emit(record)

        self.finished.emit(results)
