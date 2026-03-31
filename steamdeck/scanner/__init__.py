"""EmuDeck save scanner — aggregates all emulator scanners."""

from pathlib import Path
from typing import Callable

from .models import GameEntry, SyncStatus
from . import retroarch, duckstation, pcsx2, ppsspp, rpcs3, dolphin, melonds


def scan_all(
    emulation_path: str,
    progress_cb: Callable[[str], None] | None = None,
) -> list[GameEntry]:
    """
    Scan all supported emulators under the given EmuDeck base path.
    Returns a list of GameEntry objects (unsorted; sort/filter in UI).
    """
    base = Path(emulation_path)
    results: list[GameEntry] = []

    scanners = [
        ("RetroArch", retroarch.scan),
        ("DuckStation", duckstation.scan),
        ("PCSX2", pcsx2.scan),
        ("PPSSPP", ppsspp.scan),
        ("RPCS3", rpcs3.scan),
        ("Dolphin", dolphin.scan),
        ("melonDS", melonds.scan),
    ]

    for name, scanner_fn in scanners:
        if progress_cb:
            progress_cb(f"Scanning {name}…")
        try:
            for entry in scanner_fn(base):
                results.append(entry)
        except Exception as exc:
            # Never let one emulator crash the whole scan
            print(f"[Scanner] {name} error: {exc}")

    return results


__all__ = ["scan_all", "GameEntry", "SyncStatus"]
