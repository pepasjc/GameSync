"""EmuDeck save scanner — aggregates all emulator scanners."""

from pathlib import Path
from typing import Callable, Optional

from .models import GameEntry, SyncStatus
from . import retroarch, duckstation, pcsx2, ppsspp, rpcs3, dolphin, melonds


def scan_all(
    emulation_path: str,
    rom_scan_dir: str = "",
    progress_cb: Optional[Callable[[str], None]] = None,
    saturn_sync_format: str = "mednafen",
) -> list[GameEntry]:
    """
    Scan all supported emulators under the given EmuDeck base path.
    Returns a list of GameEntry objects (unsorted; sort/filter in UI).

    rom_scan_dir: optional additional directory to scan for ROMs
                  (e.g. external drive, separate from emulation saves).
    saturn_sync_format: user-selected Saturn emulator format — controls
                       which Saturn save location the RetroArch scanner
                       prefers.
    """
    base = Path(emulation_path)
    results: list[GameEntry] = []
    rsd = rom_scan_dir or None

    # Scanners that accept rom_scan_dir
    scanners_with_roms = [
        ("DuckStation", lambda b: duckstation.scan(b, rom_scan_dir=rsd)),
        ("PCSX2", lambda b: pcsx2.scan(b, rom_scan_dir=rsd)),
        ("PPSSPP", lambda b: ppsspp.scan(b, rom_scan_dir=rsd)),
        ("Dolphin", lambda b: dolphin.scan(b, rom_scan_dir=rsd)),
    ]

    # Scanners that don't need rom_scan_dir
    scanners_basic = [
        (
            "RetroArch",
            lambda b: retroarch.scan(b, saturn_sync_format=saturn_sync_format),
        ),
        ("RPCS3", rpcs3.scan),
        ("melonDS", melonds.scan),
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
