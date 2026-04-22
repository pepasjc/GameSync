"""Discover and manage user-installed ROMs.

Walks the EmuDeck-style ``~/Emulation/roms/<system>/`` layout (plus any
user-configured ``rom_scan_dir``), groups multi-file disc sets into a
single logical entry, and exposes a delete helper.  Kept PyQt-free so
the Installed Games tab can be unit tested without Qt.
"""

from __future__ import annotations

import os
import re
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
    companion_files: list[Path] = field(default_factory=list)

    @property
    def total_files(self) -> int:
        return 1 + len(self.companion_files)


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


def delete_installed(rom: InstalledRom) -> tuple[int, list[str]]:
    """Delete *rom* and every companion file that was grouped with it.

    Returns ``(deleted_count, errors)``.  Errors are returned so the
    caller can surface them in the confirmation dialog instead of
    masking a partial deletion.
    """
    deleted = 0
    errors: list[str] = []
    for target in [rom.path, *rom.companion_files]:
        try:
            target.unlink()
            deleted += 1
        except FileNotFoundError:
            # Already gone (race with another tool) — not an error.
            deleted += 1
        except OSError as exc:
            errors.append(f"{target.name}: {exc}")
    return deleted, errors


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
    # Group rom files by (directory, normalized stem).  Walking the
    # tree once with os.scandir is ~5× faster than Path.rglob on large
    # ROM libraries and keeps symlinked SD-card mounts fast on the Deck.
    groups: dict[tuple[str, str], list[Path]] = {}
    for file in _walk_rom_files(folder):
        key = (str(file.parent), file.stem.lower())
        groups.setdefault(key, []).append(file)

    results: list[InstalledRom] = []
    for files in groups.values():
        primary = _pick_primary(files)
        companions = [f for f in files if f != primary]
        total_size = _safe_total_size([primary, *companions])
        results.append(
            InstalledRom(
                path=primary,
                system=system,
                display_name=_pretty_name(primary.stem),
                filename=primary.name,
                size=total_size,
                companion_files=companions,
            )
        )
    return results


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
