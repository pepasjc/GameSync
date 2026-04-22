"""Discover and manage user-installed ROMs.

Walks the EmuDeck-style ``~/Emulation/roms/<system>/`` layout (plus any
user-configured ``rom_scan_dir``), groups multi-file disc sets into a
single logical entry, and exposes a delete helper.  Kept PyQt-free so
the Installed Games tab can be unit tested without Qt.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from .retroarch import ROM_EXTENSIONS
from .rom_target import SYSTEM_ROM_DIRS


# Primary-file extensions — these identify a disc or cart, and any
# sibling with the same stem gets grouped under them (e.g. the .bin
# tracks of a .cue, or the track files next to a .gdi).
_PRIMARY_PRIORITY: dict[str, int] = {
    ".cue": 10,
    ".gdi": 10,
    ".chd": 9,
    ".rvz": 9,
    ".iso": 8,
    ".cso": 8,
    ".cdi": 8,
    ".m3u": 7,
}

# Cartridge-style primary extensions — when only a single extension
# shows up for a given stem, this list helps pick the "real" ROM over
# an unrelated .bin / .sav sitting next to it.
_CART_PRIORITY: dict[str, int] = {
    ".gba": 6,
    ".nds": 6,
    ".3ds": 6,
    ".gb":  6,
    ".gbc": 6,
    ".nes": 6,
    ".smc": 6,
    ".sfc": 6,
    ".md":  6,
    ".gen": 6,
    ".smd": 6,
    ".n64": 6,
    ".z64": 6,
    ".v64": 6,
    ".pce": 6,
    ".a26": 6,
    ".a78": 6,
    ".lnx": 6,
    ".ngp": 6,
    ".ngc": 6,
    ".ws":  6,
    ".wsc": 6,
    ".32x": 6,
    ".sms": 6,
    ".gg":  6,
    ".min": 6,
    ".vb":  6,
    ".j64": 6,
}

_TAG_RE = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")


@dataclass
class InstalledRom:
    path: Path                 # Primary on-disk file (what we show in the list)
    system: str
    display_name: str          # Pretty-printed name with region tags preserved
    filename: str              # Primary filename (path.name)
    size: int                  # Sum of primary + companion sizes
    # Root folder for this rom's system (e.g. ``~/Emulation/roms/psx``).
    # Used by ``delete_installed`` to decide whether the primary's parent
    # directory is a dedicated per-game subfolder we can delete whole,
    # or the system root itself (which we never remove).
    system_root: Optional[Path] = None
    companion_files: list[Path] = field(default_factory=list)

    @property
    def total_files(self) -> int:
        return 1 + len(self.companion_files)


@dataclass
class DeleteResult:
    """Outcome of a ``delete_installed`` call.

    ``removed_dir`` is set when we collapsed a dedicated per-game
    subfolder (cue/bin in its own folder, chd in a titled subfolder)
    into a single ``rmtree`` call — the UI surfaces this so the user
    sees that the whole folder is gone, not just the tracked files.
    """

    deleted_count: int
    errors: list[str]
    removed_dir: Optional[Path] = None


def scan_installed(
    emulation_path: Optional[str],
    rom_scan_dir: Optional[str] = None,
) -> list[InstalledRom]:
    """Return the full list of installed ROMs under *emulation_path* +
    any extra *rom_scan_dir*.

    Mirrors ``DetailDialog._rom_roots_base`` so the Installed tab scans
    the exact same directories the catalog download flow writes into:
    ``<rom_scan_dir>`` (if set) contains per-system folders directly,
    while ``<emulation_path>/roms`` is the EmuDeck convention.  Roots
    that resolve to the same real path (symlinked SD-card mounts) are
    de-duplicated so discs aren't counted twice.
    """
    roots: list[Path] = []
    seen: set[Path] = set()
    for candidate in _rom_roots(emulation_path, rom_scan_dir):
        if not candidate.is_dir():
            continue
        try:
            real = candidate.resolve()
        except OSError:
            real = candidate
        if real in seen:
            continue
        seen.add(real)
        roots.append(candidate)

    results: list[InstalledRom] = []
    for root in roots:
        results.extend(_scan_root(root))
    results.sort(
        key=lambda r: (r.system, r.display_name.lower(), r.filename.lower())
    )
    return results


def _rom_roots(
    emulation_path: Optional[str], rom_scan_dir: Optional[str]
) -> list[Path]:
    out: list[Path] = []
    if rom_scan_dir:
        out.append(Path(rom_scan_dir))
    if emulation_path:
        out.append(Path(emulation_path) / "roms")
    return out


def delete_installed(rom: InstalledRom) -> DeleteResult:
    """Delete *rom* and every companion file that was grouped with it.

    If the primary file lives inside a dedicated per-game subfolder
    (e.g. ``psx/FinalFantasy7/FF7.cue`` + its .bin tracks), the whole
    subfolder is removed in one ``shutil.rmtree`` call — users expect
    deleting "Final Fantasy VII" to clean up the folder too, not leave
    an empty directory + a leftover readme behind.  The system root
    itself (``psx/``) is never removed.

    Falls back to file-by-file ``unlink`` when:
      - the primary sits directly in the system root, or
      - the parent folder holds more than just this ROM's group
        (another game lives alongside, so removing the folder would
        nuke unrelated data).
    """
    parent = rom.path.parent
    group_paths = [rom.path, *rom.companion_files]

    if _can_remove_whole_folder(parent, rom.system_root, group_paths):
        try:
            file_count = _count_files_recursive(parent)
            shutil.rmtree(parent)
            return DeleteResult(
                deleted_count=file_count,
                errors=[],
                removed_dir=parent,
            )
        except OSError as exc:
            # Surface the exact failure and fall back to file-by-file.
            return _delete_files(group_paths, initial_errors=[f"{parent}: {exc}"])

    return _delete_files(group_paths)


def _delete_files(
    files: list[Path], initial_errors: Optional[list[str]] = None
) -> DeleteResult:
    deleted = 0
    errors = list(initial_errors or [])
    for target in files:
        try:
            target.unlink()
            deleted += 1
        except FileNotFoundError:
            deleted += 1  # already gone — not an error
        except OSError as exc:
            errors.append(f"{target.name}: {exc}")
    return DeleteResult(deleted_count=deleted, errors=errors, removed_dir=None)


def would_remove_whole_folder(rom: InstalledRom) -> bool:
    """Public mirror of the internal rmtree check — the UI uses this
    to phrase the confirm dialog accurately before the deletion runs.
    """
    return _can_remove_whole_folder(
        rom.path.parent,
        rom.system_root,
        [rom.path, *rom.companion_files],
    )


def _can_remove_whole_folder(
    parent: Path,
    system_root: Optional[Path],
    group_paths: list[Path],
) -> bool:
    """True when *parent* is a dedicated per-game subfolder we can rmtree.

    Guards against nuking the system root itself and against folders
    that hold multiple games side by side.
    """
    # Never remove the system folder (psx/, gba/, saturn/, …).
    if system_root is not None:
        try:
            if parent.resolve() == system_root.resolve():
                return False
        except OSError:
            if parent == system_root:
                return False

    # Only remove if every *ROM* file in the parent belongs to this
    # game's group.  Non-rom clutter (readmes, box art, box image
    # caches) rides along — the user asked for a whole-folder delete
    # and leaving random files behind defeats the point.  Another
    # game living in the same folder does block the whole-folder
    # delete so we never nuke unrelated ROMs.
    try:
        group_real = {p.resolve() for p in group_paths if p.exists()}
    except OSError:
        group_real = {p for p in group_paths if p.exists()}

    try:
        for entry in parent.rglob("*"):
            if not entry.is_file():
                continue
            if entry.suffix.lower() not in ROM_EXTENSIONS:
                continue
            try:
                real = entry.resolve()
            except OSError:
                return False
            if real not in group_real:
                return False
    except OSError:
        return False

    return True


def _count_files_recursive(folder: Path) -> int:
    count = 0
    try:
        for entry in folder.rglob("*"):
            if entry.is_file():
                count += 1
    except OSError:
        pass
    return count


# ── Internals ──────────────────────────────────────────────────────


def _scan_root(root: Path) -> list[InstalledRom]:
    out: list[InstalledRom] = []
    for system, candidates in SYSTEM_ROM_DIRS.items():
        for candidate in candidates:
            folder = root / candidate
            if folder.is_dir():
                out.extend(_scan_folder(folder, system))
                break  # First-existing folder per system wins
    return out


def _scan_folder(folder: Path, system: str) -> list[InstalledRom]:
    # Walk the tree once with os.scandir — ~5× faster than Path.rglob
    # on big ROM libraries and keeps symlinked SD-card mounts fast.
    all_files = list(_walk_rom_files(folder))

    # First pass: parse any cue/gdi files for the exact list of track
    # files they reference.  Multi-track rips use suffixes like
    # ``Game (Track 01).bin`` whose stem doesn't match the cue, so the
    # naive (parent, stem) grouping would otherwise orphan them.
    sheet_groups: list[tuple[Path, list[Path]]] = []
    owned: set[Path] = set()
    for f in all_files:
        ext = f.suffix.lower()
        if ext not in (".cue", ".gdi"):
            continue
        referenced = _parse_sheet_companions(f)
        group = [f, *referenced]
        sheet_groups.append((f, referenced))
        owned.update(group)

    # Second pass: fall back to (parent, stem) grouping for every ROM
    # file that wasn't already claimed by a cue/gdi.
    stem_groups: dict[tuple[str, str], list[Path]] = {}
    for file in all_files:
        if file in owned:
            continue
        key = (str(file.parent), file.stem.lower())
        stem_groups.setdefault(key, []).append(file)

    results: list[InstalledRom] = []

    for primary, companions in sheet_groups:
        results.append(_build_entry(primary, companions, system, folder))

    for files in stem_groups.values():
        primary = _pick_primary(files)
        companions = [f for f in files if f != primary]
        results.append(_build_entry(primary, companions, system, folder))

    return results


def _build_entry(
    primary: Path,
    companions: list[Path],
    system: str,
    system_root: Path,
) -> InstalledRom:
    total_size = _safe_total_size([primary, *companions])
    return InstalledRom(
        path=primary,
        system=system,
        display_name=_pretty_name(primary.stem),
        filename=primary.name,
        size=total_size,
        system_root=system_root,
        companion_files=companions,
    )


_CUE_FILE_RE = re.compile(r'FILE\s+"([^"]+)"', re.IGNORECASE)
_GDI_LINE_RE = re.compile(r'^\s*\d+\s+\d+\s+\d+\s+\d+\s+(?:"([^"]+)"|(\S+))')


def _parse_sheet_companions(sheet: Path) -> list[Path]:
    """Return track files referenced by a .cue / .gdi sheet.

    Missing or unreadable referenced files are silently skipped — the
    goal is "group what's on disk", not "validate the sheet".
    """
    try:
        text = sheet.read_text(errors="ignore")
    except OSError:
        return []

    names: list[str] = []
    ext = sheet.suffix.lower()
    if ext == ".cue":
        names = _CUE_FILE_RE.findall(text)
    elif ext == ".gdi":
        for line in text.splitlines():
            match = _GDI_LINE_RE.match(line)
            if match:
                names.append(match.group(1) or match.group(2))

    companions: list[Path] = []
    seen: set[Path] = set()
    for name in names:
        candidate = sheet.parent / name
        if not candidate.is_file():
            continue
        resolved = candidate
        if resolved == sheet or resolved in seen:
            continue
        seen.add(resolved)
        companions.append(resolved)
    return companions


def _walk_rom_files(folder: Path) -> Iterable[Path]:
    try:
        with os.scandir(folder) as it:
            for entry in it:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        yield from _walk_rom_files(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        p = Path(entry.path)
                        if p.suffix.lower() in ROM_EXTENSIONS:
                            yield p
                except OSError:
                    continue
    except OSError:
        return


def _pick_primary(files: list[Path]) -> Path:
    """Pick the "shown" file for a stem group.

    Priority order:
      1. Disc/container formats (.cue, .gdi, .chd, .rvz, .iso, .m3u…)
      2. Cartridge extensions (.gba, .nes, .smc…)
      3. Fall back to the largest file.
    """
    def score(f: Path) -> tuple[int, int]:
        ext = f.suffix.lower()
        pri = _PRIMARY_PRIORITY.get(ext, _CART_PRIORITY.get(ext, 0))
        # Prefer non-.bin over .bin when everything else ties — .bin is
        # typically a track companion, not the disc itself.
        non_bin_bonus = 0 if ext == ".bin" else 1
        try:
            size = f.stat().st_size
        except OSError:
            size = 0
        return (pri, non_bin_bonus), size  # type: ignore[return-value]

    return max(files, key=score)  # type: ignore[arg-type]


def _safe_total_size(files: list[Path]) -> int:
    total = 0
    for f in files:
        try:
            total += f.stat().st_size
        except OSError:
            continue
    return total


def _pretty_name(stem: str) -> str:
    """Turn ``"Breath of Fire IV (USA)"`` / ``"pokemon_emerald"`` into a
    display string.  Keeps region tags so users can tell duplicate
    dumps apart (a USA vs EU copy).
    """
    cleaned = stem.replace("_", " ").strip()
    # Collapse multiple spaces but preserve bracketed region tags
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or stem
