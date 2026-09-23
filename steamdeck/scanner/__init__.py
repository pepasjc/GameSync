"""EmuDeck save scanner — aggregates all emulator scanners."""

import sys
from pathlib import Path
from typing import Callable, Optional

# ``scanner`` is only importable with the steamdeck root on sys.path, which is
# also where ``config`` lives — but tests import the package directly, so make
# the root explicit rather than relying on the caller's bootstrap.
_STEAMDECK_ROOT = str(Path(__file__).resolve().parent.parent)
if _STEAMDECK_ROOT not in sys.path:
    sys.path.insert(0, _STEAMDECK_ROOT)

from config import CEMU_SAVE_DIR_KEY, save_dir_override  # noqa: E402

from .models import GameEntry, SyncStatus
from . import (
    retroarch,
    duckstation,
    pcsx2,
    ppsspp,
    rpcs3,
    dolphin,
    melonds,
    citra,
    cemu,
)


def scan_all(
    emulation_path: str,
    rom_scan_dir: str = "",
    progress_cb: Optional[Callable[[str], None]] = None,
    saturn_sync_format: str = "mednafen",
    save_dir_overrides: Optional[dict] = None,
) -> list[GameEntry]:
    """
    Scan all supported emulators under the given EmuDeck base path.
    Returns a list of GameEntry objects (unsorted; sort/filter in UI).

    rom_scan_dir: optional additional directory to scan for ROMs
                  (e.g. external drive, separate from emulation saves).
    saturn_sync_format: user-selected Saturn emulator format — controls
                       which Saturn save location the RetroArch scanner
                       prefers.
    save_dir_overrides: per-emulator save folder overrides keyed by emulator
                       name.  Only Cemu reads one — EmuDeck installs it
                       outside the Emulation folder, so auto-detection can
                       miss it entirely.
    """
    base = Path(emulation_path)
    results: list[GameEntry] = []
    rsd = rom_scan_dir or None
    cemu_dir = save_dir_override(save_dir_overrides, CEMU_SAVE_DIR_KEY)

    # Scanners that accept rom_scan_dir
    scanners_with_roms = [
        ("DuckStation", lambda b: duckstation.scan(b, rom_scan_dir=rsd)),
        ("PCSX2", lambda b: pcsx2.scan(b, rom_scan_dir=rsd)),
        ("PPSSPP", lambda b: ppsspp.scan(b, rom_scan_dir=rsd)),
        ("Dolphin", lambda b: dolphin.scan(b, rom_scan_dir=rsd)),
        ("melonDS", lambda b: melonds.scan(b, rom_scan_dir=rsd)),
    ]

    # Scanners that don't need rom_scan_dir
    scanners_basic = [
        ("Azahar", citra.scan),
        ("Cemu", lambda b: cemu.scan(b, save_dir_override=cemu_dir)),
        (
            "RetroArch",
            lambda b: retroarch.scan(b, saturn_sync_format=saturn_sync_format),
        ),
        ("RPCS3", rpcs3.scan),
    ]

    for name, scanner_fn in scanners_with_roms:
        if progress_cb:
            progress_cb(f"Scanning {name}...")
        try:
            for entry in scanner_fn(base):
                results.append(entry)
        except Exception as exc:
            print(f"[Scanner] {name} error: {exc}")

    for name, scanner_fn in scanners_basic:
        if progress_cb:
            progress_cb(f"Scanning {name}...")
        try:
            for entry in scanner_fn(base):
                results.append(entry)
        except Exception as exc:
            print(f"[Scanner] {name} error: {exc}")

    return results


__all__ = ["scan_all", "GameEntry", "SyncStatus"]
