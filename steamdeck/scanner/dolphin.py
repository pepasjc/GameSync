"""
Dolphin GameCube scanner for EmuDeck on Steam Deck.

Ported from Android's DolphinEmulator.kt:
  - Extracts 4-char game code from GCI filename regex pattern
    (e.g. "01-GM4E-MarioKart Double Dash!!.gci")
  - Groups multiple .gci files per game code
  - Most recently modified file is the primary, rest are extra_files
  - Display name extracted from filename description part
  - Title ID: GC_<code_lowercase>
"""

import re
from pathlib import Path
from typing import Generator, Optional

from .base import sha256_file, find_paths
from .models import GameEntry, SyncStatus

# Dolphin on Flatpak writes to this path; EmuDeck may symlink `dolphin-emu`
# to it from ~/Emulation/saves.
FLATPAK_DOLPHIN_DATA = (
    Path.home() / ".var/app/org.DolphinEmu.dolphin-emu/data/dolphin-emu"
)

# EmuDeck's canonical path is `dolphin-emu`; we also check the older `dolphin`
# name just in case a user has a non-standard setup.
EMUDECK_DOLPHIN_EMU_SAVES = Path.home() / "Emulation/saves/dolphin-emu"
EMUDECK_DOLPHIN_SAVES = Path.home() / "Emulation/saves/dolphin"

# GC memory card regions and card names
GC_REGIONS = ["USA", "EUR", "JAP", "PAL", "NTSC"]
GC_CARD_NAMES = ["Card A", "Card B"]

# GCI filename regex: "<hex>-<GAMECODE>-<description>.gci"
# e.g. "01-GM4E-MarioKart Double Dash!!.gci"
_GCI_NAME_RE = re.compile(r"^[0-9A-Fa-f]+-([A-Z0-9]{4})-")


def _gci_game_code(filename: str) -> Optional[str]:
    """Extract 4-char game code from a .gci filename."""
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    m = _GCI_NAME_RE.match(stem)
    if m:
        return m.group(1).upper()
    return None


def _gci_description(name_without_ext: str) -> str:
    """
    Extract human-readable description from a .gci filename.
    "01-GM4E-MarioKart Double Dash!!" -> "MarioKart Double Dash!!"
    """
    parts = name_without_ext.split("-", maxsplit=2)
    if len(parts) >= 3:
        desc = parts[2].strip()
        return desc if desc else name_without_ext
    return name_without_ext


def _find_gc_base(emulation_path: Path) -> Optional[Path]:
    """Return the GameCube memcard root (…/GC) for the current install."""
    emu_saves = emulation_path / "saves" / "dolphin-emu"
    legacy_emu_saves = emulation_path / "saves" / "dolphin"
    return find_paths(
        emu_saves / "GC",
        legacy_emu_saves / "GC",
        EMUDECK_DOLPHIN_EMU_SAVES / "GC",
        EMUDECK_DOLPHIN_SAVES / "GC",
        FLATPAK_DOLPHIN_DATA / "GC",
    )


def _default_card_dir(gc_base: Path) -> Path:
    """
    Pick a sensible default card directory for writing server-only downloads.

    Prefers USA/Card A, then the first existing region's Card A, then just
    creates USA/Card A as a fresh path.
    """
    candidates = []
    for region in GC_REGIONS:
        for card in GC_CARD_NAMES:
            candidates.append(gc_base / region / card)
    # Existing directory wins
    for c in candidates:
        if c.exists() and c.is_dir():
            return c
    # Otherwise use USA/Card A (will be created on write)
    return gc_base / "USA" / "Card A"


def _predicted_filename(game_code: str, display_name: str) -> str:
    """
    Build a Dolphin-style GCI filename for a server-only download.
    Format: "01-<GAMECODE>-<display>.gci"
    """
    safe_desc = re.sub(r"[\\/:*?\"<>|]", "", display_name).strip() or game_code
    return f"01-{game_code.upper()}-{safe_desc}.gci"


def scan(
    emulation_path: Path,
    rom_scan_dir: Optional[str] = None,
) -> Generator[GameEntry, None, None]:
    """
    Scan Dolphin GameCube saves (.gci files).

    Structure: GC/<REGION>/Card <X>/<HH>-<GAMECODE>-<description>.gci

    Strategy (mirrors Android's DolphinEmulator):
    1. Walk region/card directories
    2. Group .gci files by 4-char game code (from filename regex)
    3. Most recently modified file is primary, rest are extra_files
    4. Hash is over the primary file only — that's what gets uploaded via the
       `/gc-card?format=gci` endpoint, so the local hash must match the
       server-computed hash to avoid a perpetual "out of sync" state.
    """
    gc_base = _find_gc_base(emulation_path)
    if gc_base is None or not gc_base.exists():
        return

    # Collect all GCI files grouped by game code
    by_code: dict[str, list[Path]] = {}

    for region in GC_REGIONS:
        region_dir = gc_base / region
        if not region_dir.exists():
            continue

        # List all card directories (Card A, Card B, etc.)
        try:
            card_dirs = [d for d in region_dir.iterdir() if d.is_dir()]
        except Exception:
            continue

        for card_dir in card_dirs:
            try:
                gci_files = [
                    f
                    for f in card_dir.iterdir()
                    if f.is_file() and f.suffix.lower() == ".gci"
                ]
            except Exception:
                continue

            for gci_file in gci_files:
                code = _gci_game_code(gci_file.name)
                if code is None:
                    continue
                by_code.setdefault(code, []).append(gci_file)

    # Create one entry per game code
    for code, files in by_code.items():
        # Sort by last modified descending
        sorted_files = sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)
        primary = sorted_files[0]
        extras = sorted_files[1:]

        title_id = f"GC_{code.lower()}"
        display_name = _gci_description(primary.stem)

        entry = GameEntry(
            title_id=title_id,
            display_name=display_name,
            system="GC",
            emulator="Dolphin",
            save_path=primary,
            extra_files=extras,
        )
        try:
            # Hash the primary GCI only — this is the single file that gets
            # POSTed to /gc-card?format=gci, so it's the file the server
            # hashes too.  Hashing concatenated extras would make the local
            # hash permanently differ from the server one.
            entry.save_hash = sha256_file(primary)
            entry.save_mtime = primary.stat().st_mtime
            entry.save_size = primary.stat().st_size
        except Exception:
            pass
        yield entry


def build_server_only_entries(
    server_saves: dict[str, dict],
    seen_ids: set[str],
    emulation_path: Path,
) -> list[GameEntry]:
    """
    Create downloadable GameCube placeholders for GC saves only on the server.

    Without this, games the user has never run locally (no .gci on disk) can
    never be downloaded because entry.save_path is None and the UI skips them.
    """
    results: list[GameEntry] = []

    gc_base = _find_gc_base(emulation_path)
    if gc_base is None:
        # Create a sensible default location so downloads can still happen.
        emu_saves = emulation_path / "saves" / "dolphin-emu"
        gc_base = emu_saves / "GC"

    card_dir = _default_card_dir(gc_base)

    for title_id, info in server_saves.items():
        if title_id in seen_ids:
            continue
        upper = title_id.upper()
        if not upper.startswith("GC_") or len(upper) < 7:
            continue
        code = upper[3:7]
        display_name = info.get("name") or info.get("game_name") or title_id
        filename = _predicted_filename(code, display_name)

        results.append(
            GameEntry(
                title_id=title_id,
                display_name=display_name,
                system="GC",
                emulator="Dolphin",
                save_path=card_dir / filename,
                status=SyncStatus.SERVER_ONLY,
                server_hash=info.get("save_hash"),
                server_title_id=title_id,
                server_timestamp=info.get("client_timestamp"),
                server_size=info.get("save_size"),
            )
        )

    return results
