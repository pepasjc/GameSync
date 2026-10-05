"""
Main window for the Steam Deck GameSync client.

Layout (1280 × 800 full-screen), the shared GameSync design:
  ┌───────────────────────────────────────────────────────┐
  │ GameSync   [L1] Saves Catalog Installed … [R1]   ● srv │  header
  ├───────────────────────────────────────────────────────┤
  │ [SELECT] (All) (GBA) (PS1) …                 123 saves │  sub-tab chips
  ├──────────────────────────────────────┬────────────────┤
  │  list of the active tab              │  details of    │
  │                                      │  the selected  │
  │                                      │  row           │
  ├──────────────────────────────────────┴────────────────┤
  │ ● status banner                                       │
  │ (A) Sync (X) Sync all (Y) Details …      L1/R1 START  │  footer hints
  └───────────────────────────────────────────────────────┘

Controls (pygame-polled gamepad; the same scheme on every GameSync client):
  D-pad / L-stick ↑↓  →  move one row (held repeats)
  D-pad ←→            →  page (held accelerates, then jumps by letter)
  L1 / R1             →  previous / next tab (wraps)
  SELECT              →  next sub-tab (system filter)
  A                   →  confirm / the row's main action
  B                   →  cancel / back (clear search, pause a download)
  X                   →  the tab's secondary action
  Y                   →  details (Saves) / search (Catalog, Installed)
  START               →  exit, after a confirmation
  L2 / R2             →  Saves status filter
"""

import time
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QStackedWidget,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, QObject
from PyQt6.QtGui import QFont, QKeyEvent

from scanner.models import GameEntry, SyncStatus, STATUS_COLOR, STATUS_LABEL
from scanner import scan_all, rpcs3, dolphin, citra, cemu, server_only
from scanner.rom_match import (
    DISC_SLUG_SYSTEMS as _DISC_SLUG_SYSTEMS,
    RomIndex as _RomIndex,
    dedup_disc_slug_entries as _dedup_disc_slug_entries,
    is_disc_slug_title_id as _is_disc_slug_title_id,
)
from scanner.installed_roms import (
    InstalledRom,
    delete_installed,
    scan_installed,
    would_remove_whole_folder,
)
from scanner.rom_target import resolve_rom_target_dir
from sync_client import SyncClient, _find_server_save
from config import (
    CEMU_SAVE_DIR_KEY,
    load_config,
    save_config,
    save_dir_override as _save_dir_override,
    CATALOG_CACHE_PATH,
    DOWNLOADS_DB_PATH,
)
from catalog_store import CatalogResult, load_catalog
from download_manager import ACTIVE_STATUSES, DownloadManager
from shared.systems import DEFAULT_SYSTEM_COLOR, SYSTEM_COLOR
from . import theme
from .catalog_view import CatalogView
from .game_list import GameListView
from .installed_view import InstalledView
from .chrome import ChipBar, DetailPanel, HeaderBar, StatusBanner
from .controls_bar import ControlsBar
from .settings_view import SettingsRow, SettingsView
from .settings_dialog import SettingsDialog
from .detail_dialog import DetailDialog
from .confirm_dialog import ConfirmDialog, ResultDialog
from .downloads_view import DownloadsView
from .detail_dialog import _NATIVE_COMPRESSED_FORMAT_SYSTEMS as _NATIVE_EXTRACT_SKIP

# What the confirm prompt calls a bundle, by the server's ``bundle_kind``.
_BUNDLE_KIND_LABELS = {
    "msu1": "MSU-1 pack",
    "msu-md": "MSU-MD pack",
    "mdplus": "MD+ pack",
}

try:
    import pygame

    _PYGAME_OK = True
except ImportError:
    _PYGAME_OK = False

# ──────────────────────────────────────────────────────────────────────────────
# Background workers
# ──────────────────────────────────────────────────────────────────────────────


class ScanWorker(QObject):
    progress = pyqtSignal(str)
    finished = pyqtSignal(list)  # list[GameEntry]

    def __init__(
        self,
        emulation_path: str,
        rom_scan_dir: str = "",
        saturn_sync_format: str = "mednafen",
        save_dir_overrides: Optional[dict] = None,
    ):
        super().__init__()
        self._path = emulation_path
        self._rom_scan_dir = rom_scan_dir
        self._saturn_sync_format = saturn_sync_format
        self._save_dir_overrides = dict(save_dir_overrides or {})

    def run(self):
        results = scan_all(
            self._path,
            rom_scan_dir=self._rom_scan_dir,
            progress_cb=self.progress.emit,
            saturn_sync_format=self._saturn_sync_format,
            save_dir_overrides=self._save_dir_overrides,
        )
        self.finished.emit(results)


class CatalogWorker(QObject):
    """Loads the server's ROM catalog through the on-disk cache, off the
    main thread (see catalog_store: only systems whose fingerprint moved
    are fetched)."""

    progress = pyqtSignal(str)
    finished = pyqtSignal(object)  # CatalogResult

    def __init__(self, client: SyncClient, force: bool = False):
        super().__init__()
        self._client = client
        self._force = force

    def run(self) -> None:
        try:
            result = load_catalog(
                self._client, CATALOG_CACHE_PATH, force=self._force,
                progress=self.progress.emit,
            )
        except Exception as exc:  # never leave the tab stuck on "Loading"
            result = CatalogResult(error=str(exc) or exc.__class__.__name__)
        self.finished.emit(result)


class SyncAllWorker(QObject):
    """Runs Sync all's uploads and downloads one at a time."""

    progress = pyqtSignal(int, int, str)  # done, total, name
    finished = pyqtSignal(int, list)  # ok count, failure lines

    def __init__(self, client: SyncClient, uploads: list, downloads: list):
        super().__init__()
        self._client = client
        self._jobs = [(e, True) for e in uploads] + [(e, False) for e in downloads]

    def run(self) -> None:
        ok, failed = 0, []
        total = len(self._jobs)
        for index, (entry, upload) in enumerate(self._jobs, 1):
            self.progress.emit(index, total, entry.display_name)
            try:
                done = (self._client.upload_save(entry, force=True) if upload
                        else self._client.download_save(entry, force=True))
            except Exception as exc:
                done = False
                print(f"[SyncAll] {entry.title_id}: {exc}")
            if done:
                ok += 1
            else:
                failed.append(f"{'Upload' if upload else 'Download'}: {entry.display_name}")
        self.finished.emit(ok, failed)


class InstalledWorker(QObject):
    """Walks the local ROM directories off the main thread.

    rglob on a large EmuDeck library over an SD card blocks for a
    couple of seconds on first run, so we push it to a worker so the
    UI can keep rendering the "Scanning…" placeholder.
    """

    finished = pyqtSignal(list)  # list[InstalledRom]

    def __init__(self, emulation_path: str, rom_scan_dir: str):
        super().__init__()
        self._emulation_path = emulation_path
        self._rom_scan_dir = rom_scan_dir

    def run(self) -> None:
        try:
            roms = scan_installed(self._emulation_path, self._rom_scan_dir)
        except Exception as exc:
            print(f"[Installed] scan failed: {exc}")
            roms = []
        self.finished.emit(roms)


class ServerWorker(QObject):
    """Fetches server saves and enriches GameEntry objects with sync status."""

    finished = pyqtSignal(list)  # updated list[GameEntry]

    def __init__(
        self,
        entries: list[GameEntry],
        client: SyncClient,
        emulation_path: str,
        save_dir_overrides: Optional[dict] = None,
    ):
        super().__init__()
        self._entries = entries
        self._client = client
        self._emulation_path = Path(emulation_path)
        self._save_dir_overrides = dict(save_dir_overrides or {})

    def _enrich_title_ids(self):
        """
        For slug-based entries, ask the server's /normalize/batch endpoint
        to resolve translated/CHD ROM filenames (and card-only display
        names for disc systems) to the canonical server title_id.

        Disc-system entries (PS1, PS2, SAT) that only carry a memory-card
        with no ROM on disk still need this step — the user's policy is
        that we never sync PS1 saves under ``PS1_<slug>``, so we treat
        the card's display_name as a stand-in filename when there is no
        rom_filename available.
        """
        skip_systems = {"GC", "PS3", "PSP", "WII", "WIIU", "NSW", "?"}
        disc_systems = _DISC_SLUG_SYSTEMS
        needs_lookup: list[tuple[GameEntry, str]] = []
        rom_entries: list[dict[str, str]] = []
        for entry in self._entries:
            system = entry.system.upper().strip()
            if system in skip_systems:
                continue
            if not entry.title_id.startswith(f"{system}_"):
                continue
            lookup_filename = entry.rom_filename or (
                entry.rom_path.name if entry.rom_path else None
            )
            if not lookup_filename and system in disc_systems:
                # Card-only PS1/PS2/SAT row — feed the display_name in so
                # the server's PSX/Saturn slug index can still resolve it.
                lookup_filename = entry.display_name
            if not lookup_filename:
                continue
            needs_lookup.append((entry, lookup_filename))
            rom_entries.append({"system": system, "filename": lookup_filename})

        if not rom_entries:
            return

        # Batch lookup via server
        resolved = self._client.normalize_batch(rom_entries)
        if not resolved:
            return

        # Apply resolved serial title_ids
        for entry, lookup_filename in needs_lookup:
            system = entry.system.upper().strip()
            new_tid = resolved.get((system, lookup_filename))
            if new_tid and new_tid != entry.title_id:
                old_tid = entry.title_id
                entry.title_id = new_tid
                print(f"[Enrich] {old_tid} -> {new_tid} (from {lookup_filename})")

    def _enrich_display_names(self):
        """
        Resolve product-code display names to real game names via the server.

        Mirrors Android MainViewModel's enrichment step: collects entries
        where display_name is a raw code (PSP/PS serial, 3DS/NDS hex ID)
        OR where the title_id starts with "GC_" (Dolphin saves whose GCI
        filename descriptions are less clean than the server's database).
        Batch-queries POST /api/v1/titles/names, updates display_name and
        system.
        """
        import re

        # Patterns that indicate the display name is just a code, not a real name
        _PRODUCT_CODE_RE = re.compile(r"^[A-Z]{4}\d{5}")  # PSP/PS1/PS2/PS3/VITA
        _HEX16_RE = re.compile(r"^[0-9A-Fa-f]{16}$")  # 3DS title IDs
        _HEX8_RE = re.compile(r"^[0-9A-Fa-f]{8}$")  # NDS title IDs

        codes_to_lookup: list[str] = []
        code_to_entries: dict[str, list[GameEntry]] = {}

        for entry in self._entries:
            name = entry.display_name
            needs_name = (
                name == entry.title_id  # scanner just used title_id as name
                or bool(_PRODUCT_CODE_RE.match(name))
                or bool(_HEX16_RE.match(name))
                or bool(_HEX8_RE.match(name))
                # GCI descriptions < server DB.  Case-insensitive: saves
                # scanned before the GC_<CODE> canonicalisation may still be
                # cached under the old lowercase form.
                or entry.title_id.upper().startswith("GC_")
            )
            if not needs_name:
                continue

            # Use title_id as the lookup code (server resolves by code).
            # For PS3 saves with slot suffixes (e.g. BLJS10001GAME), trim to
            # the 9-char base code so the server DB can resolve the name.
            #
            # Wii U is the exception: its title id's low word is not the
            # product code, so the DAT can only be keyed by the code the
            # scanner pulled out of meta.xml (WIIU_ARDE).
            code = entry.game_code or entry.title_id
            if (
                entry.system == "PS3"
                and len(code) > 9
                and _PRODUCT_CODE_RE.match(code)
            ):
                code = code[:9]
            if code not in code_to_entries:
                code_to_entries[code] = []
                codes_to_lookup.append(code)
            code_to_entries[code].append(entry)

        if not codes_to_lookup:
            return

        result = self._client.lookup_names(codes_to_lookup)
        names = result.get("names", {})
        types = result.get("types", {})

        # Server platform label -> our system code mapping
        _PLATFORM_TO_SYSTEM = {
            "PSP": "PSP",
            "PSX": "PS1",
            "PS1": "PS1",
            "PS2": "PS2",
            "PS3": "PS3",
            "VITA": "VITA",
            "3DS": "3DS",
            "NDS": "NDS",
        }

        for code, entries in code_to_entries.items():
            resolved_name = names.get(code)
            resolved_type = types.get(code)
            for entry in entries:
                if resolved_name:
                    entry.display_name = resolved_name
                    print(f"[Names] {code} -> {resolved_name}")
                if resolved_type and entry.system == "?":
                    mapped = _PLATFORM_TO_SYSTEM.get(resolved_type, resolved_type)
                    entry.system = mapped

    def _push_wiiu_name_hints(
        self, entries: list[GameEntry], server_saves: dict[str, dict]
    ) -> None:
        """Send locally-resolved Wii U names/codes for server rows still raw."""
        codes: dict[str, str] = {}
        names: dict[str, str] = {}
        for entry in entries:
            if entry.system.upper() != "WIIU":
                continue
            info = _find_server_save(server_saves, entry.title_id)
            if info is None:
                continue
            server_name = info.get("game_name") or info.get("name") or ""
            if server_name and server_name != entry.title_id:
                continue  # server already has a real name
            if entry.game_code:
                codes[entry.title_id] = entry.game_code
            if entry.display_name and entry.display_name != entry.title_id:
                names[entry.title_id] = entry.display_name

        if codes or names:
            self._client.push_name_hints(codes=codes, names=names)

    def run(self):
        # ── Pre-fetch the server's ROM catalog so we can (a) re-key local
        # slug entries to whatever title_id the server uses for the same
        # ROM, and (b) flag each entry with the ROMs the server can hand
        # back, so the UI can hide Download-ROM when nothing's available.
        # Through the on-disk cache: unchanged systems cost nothing.
        catalog = load_catalog(self._client, CATALOG_CACHE_PATH).rows
        rom_index = _RomIndex.build(catalog)

        # ── Enrich local slug title_ids by looking the ROM up in the
        # server's catalog (filename match wins, then fuzzy name match).
        # Falls back to /normalize/batch for filenames the catalog doesn't
        # know.  We never want PS1 entries living under PS1_<slug> when
        # the server already knows them as SLUS01324.
        self._enrich_title_ids_from_catalog(rom_index)
        self._enrich_title_ids()

        # ── Enrich display names (product codes -> real game names) ──
        self._enrich_display_names()

        server_saves = self._client.get_server_saves()
        updated = []
        for entry in self._entries:
            entry.status = self._client.compute_status(entry, server_saves)
            info = _find_server_save(server_saves, entry.title_id)
            if info:
                entry.server_title_id = info.get("title_id") or entry.title_id
                entry.server_hash = info.get("save_hash")
                entry.server_timestamp = info.get("client_timestamp")
                entry.server_size = info.get("save_size")
                # Also pick up server game_name if we still don't have a good one
                if entry.display_name == entry.title_id and info.get("game_name"):
                    entry.display_name = info["game_name"]
            updated.append(entry)

        seen_ids = {entry.title_id for entry in updated}
        updated.extend(
            rpcs3.build_server_only_entries(server_saves, seen_ids, self._emulation_path)
        )
        seen_ids = {entry.title_id for entry in updated}
        updated.extend(
            dolphin.build_server_only_entries(server_saves, seen_ids, self._emulation_path)
        )
        seen_ids = {entry.title_id for entry in updated}
        updated.extend(
            citra.build_server_only_entries(server_saves, seen_ids, self._emulation_path)
        )
        seen_ids = {entry.title_id for entry in updated}
        updated.extend(
            cemu.build_server_only_entries(
                server_saves,
                seen_ids,
                self._emulation_path,
                save_dir_override=_save_dir_override(
                    self._save_dir_overrides, CEMU_SAVE_DIR_KEY
                ),
            )
        )

        # Wii U saves reach the server named after their own title id: no DAT
        # can resolve a 16-hex Wii U id, so the console client has nothing to
        # send.  This machine may have the game's meta.xml — hand the server
        # what we read so every other client (the desktop app above all) stops
        # showing raw hex.
        self._push_wiiu_name_hints(updated, server_saves)
        # Generic placeholders for every other system — lets the user see
        # (and Download-ROM for) server saves on systems without a dedicated
        # scanner-level builder (PS1, PS2, PSP, GBA, SNES, NES, ...).
        #
        # Filename-derived systems (PS1 .mcd, NES/SNES/GBA .srm, ...) need
        # the canonical No-Intro name to construct a save path the emulator
        # can find.  Batch-ask the server for canonical names so we use the
        # exact stem the emulator uses on disk, rather than reverse-deriving
        # one from the slug.
        seen_ids = {entry.title_id for entry in updated}
        unseen_title_ids = [
            tid for tid in server_saves.keys() if tid not in seen_ids
        ]
        canonical_names = (
            self._client.lookup_canonical_names(unseen_title_ids)
            if unseen_title_ids
            else {}
        )
        updated.extend(
            server_only.build_server_only_entries(
                server_saves,
                seen_ids,
                self._emulation_path,
                canonical_names=canonical_names,
            )
        )

        # ── Collapse duplicate rows for the same game.  Disc-system slug
        # entries (PS1_/PS2_/SAT_<slug>) only exist when the local scanner
        # couldn't extract a serial; if a serial-keyed sibling is also
        # present (because the server returned a save under SLUS01324),
        # merge them so the user sees a single row keyed by the serial.
        updated = _dedup_disc_slug_entries(updated)

        # Status for any merged winner now reflects both local and server
        # data — recompute so a SERVER_ONLY placeholder that just absorbed
        # a local save flips to SYNCED / LOCAL_NEWER / CONFLICT correctly.
        for entry in updated:
            entry.status = self._client.compute_status(entry, server_saves)

        # ── Annotate each entry with the ROM catalog rows it can pull
        # down.  Empty list => Download-ROM button stays hidden.
        for entry in updated:
            entry.available_roms = rom_index.matches_for(entry)

        self.finished.emit(updated)

    def _enrich_title_ids_from_catalog(self, rom_index: "_RomIndex") -> None:
        """Re-key local entries to whatever title_id the server's ROM
        catalog uses for the same ROM/save.

        Filename match is exact and trustworthy: if the user has
        ``Breath of Fire IV (USA).chd`` and the server's catalog lists
        the same filename under ``SLUS01324``, the local entry gets
        re-keyed without needing the (less reliable) /normalize lookup.
        Card-only PS1 rows with no local ROM still get re-keyed via the
        display-name fallback so the slug doesn't survive into the UI.
        """
        for entry in self._entries:
            # Only re-key slug-style title_ids; serial-format IDs are
            # already canonical.
            if not _is_disc_slug_title_id(entry.title_id, entry.system) and \
                    not entry.title_id.startswith(f"{entry.system}_"):
                continue
            filename = entry.rom_filename or (
                entry.rom_path.name if entry.rom_path else None
            )
            new_tid = None
            if filename:
                new_tid = rom_index.title_id_for_filename(entry.system, filename)
            if not new_tid and filename:
                new_tid = rom_index.title_id_for_name(entry.system, filename)
            if not new_tid:
                new_tid = rom_index.title_id_for_name(entry.system, entry.display_name)
            if new_tid and new_tid != entry.title_id:
                entry.title_id = new_tid


def _infer_system(title_id: str) -> str:
    """Best-effort system detection from a title_id string."""
    parts = title_id.split("_", 1)
    if len(parts) == 2 and 2 <= len(parts[0]) <= 8 and parts[0].isupper():
        return parts[0]
    # PlayStation product codes: SLUS, SCES, UCUS, BLUS, etc.
    for prefix, sys in [
        ("SLUS", "PS1"),
        ("SLES", "PS1"),
        ("SCUS", "PS1"),
        ("SCES", "PS1"),
        ("SLPS", "PS1"),
        ("SLPM", "PS1"),
        ("SCPS", "PS1"),
        ("SCPM", "PS1"),
        ("NPJH", "PSP"),
        ("UCUS", "PSP"),
        ("ULUS", "PSP"),
        ("UCES", "PSP"),
        ("ULJS", "PSP"),
        ("NPUG", "PSP"),
        ("NPJG", "PSP"),
        ("BLUS", "PS3"),
        ("BLES", "PS3"),
        ("BCUS", "PS3"),
        ("BCES", "PS3"),
    ]:
        if title_id.startswith(prefix):
            return sys
    return "?"


# ──────────────────────────────────────────────────────────────────────────────
# Filter helpers
# ──────────────────────────────────────────────────────────────────────────────

ALL_SYSTEMS = "All Systems"
ALL_STATUSES = "All"

STATUS_FILTER_CYCLE = [
    ALL_STATUSES,
    "Needs Action",  # upload + download + conflict
    STATUS_LABEL[SyncStatus.LOCAL_NEWER],
    STATUS_LABEL[SyncStatus.SERVER_NEWER],
    STATUS_LABEL[SyncStatus.CONFLICT],
    STATUS_LABEL[SyncStatus.SYNCED],
    STATUS_LABEL[SyncStatus.LOCAL_ONLY],
    STATUS_LABEL[SyncStatus.SERVER_ONLY],
    STATUS_LABEL[SyncStatus.NO_SAVE],
]

NEEDS_ACTION_STATUSES = {
    SyncStatus.LOCAL_NEWER,
    SyncStatus.SERVER_NEWER,
    SyncStatus.CONFLICT,
    SyncStatus.LOCAL_ONLY,
}

STATUS_LABEL_TO_ENUM = {v: k for k, v in STATUS_LABEL.items()}


def _matches_status_filter(entry: GameEntry, filt: str) -> bool:
    if filt == ALL_STATUSES:
        return True
    if filt == "Needs Action":
        return entry.status in NEEDS_ACTION_STATUSES
    target = STATUS_LABEL_TO_ENUM.get(filt)
    return entry.status == target


def _app_version() -> str:
    """The root VERSION file, the single source of truth for every client."""
    try:
        return (Path(__file__).resolve().parents[2] / "VERSION").read_text().strip()
    except OSError:
        return ""


def _sync_all_plan(entries: list[GameEntry]) -> tuple[list, list, list]:
    """Split saves into ``(uploads, downloads, conflicts)`` for Sync all.

    A server-only save is downloaded only when its game's ROM is on this
    machine (the scanner found the ROM and predicted where the emulator
    keeps the save); rows built purely from the server's list are left for
    the user to fetch from the details, so Sync all never fills save folders
    for games that aren't installed.
    """
    uploads, downloads, conflicts = [], [], []
    for entry in entries:
        status = entry.status
        if status in (SyncStatus.LOCAL_NEWER, SyncStatus.LOCAL_ONLY):
            if entry.save_path is not None and entry.save_path.exists():
                uploads.append(entry)
        elif status == SyncStatus.SERVER_NEWER:
            if entry.save_path is not None and entry.server_hash:
                downloads.append(entry)
        elif status == SyncStatus.SERVER_ONLY:
            if entry.save_path is not None and entry.server_hash and entry.rom_path:
                downloads.append(entry)
        elif status == SyncStatus.CONFLICT:
            conflicts.append(entry)
    return uploads, downloads, conflicts


def _fmt_bytes(num: Optional[int]) -> str:
    if not num:
        return "—"
    if num < 1024:
        return f"{num} B"
    if num < 1024 * 1024:
        return f"{num / 1024:.1f} KB"
    if num < 1024 ** 3:
        return f"{num / (1024 * 1024):.1f} MB"
    return f"{num / 1024 ** 3:.2f} GB"


def _fmt_time(stamp: Optional[float]) -> str:
    if not stamp:
        return "—"
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(stamp)))
    except (TypeError, ValueError, OverflowError, OSError):
        return "—"


_SAVE_HINTS = {
    SyncStatus.SYNCED: "Up to date with the server.",
    SyncStatus.LOCAL_NEWER: "This copy changed since the last sync: A uploads it.",
    SyncStatus.LOCAL_ONLY: "The server has no copy yet: A uploads it.",
    SyncStatus.SERVER_NEWER: "The server's copy is newer: A downloads it.",
    SyncStatus.SERVER_ONLY: "Only the server has this save: A downloads it.",
    SyncStatus.CONFLICT: "Both copies changed since the last sync: A opens the "
                         "comparison so you can pick one.",
    SyncStatus.NO_SAVE: "No save on this machine or the server yet.",
}


def _save_detail(entry: GameEntry) -> tuple:
    status_color = STATUS_COLOR.get(entry.status, theme.MUTED)
    pills = [
        (STATUS_LABEL.get(entry.status, "?"), status_color),
        (entry.system, SYSTEM_COLOR.get(entry.system, DEFAULT_SYSTEM_COLOR)),
    ]
    rows = [
        ("Title ID", entry.title_id),
        ("Emulator", entry.emulator),
        ("Local save", entry.save_path.name if entry.save_path else "none"),
        ("Local size", _fmt_bytes(entry.save_size)),
        ("Local time", _fmt_time(entry.save_mtime)),
        ("Server size", _fmt_bytes(entry.server_size)),
        ("Server time", _fmt_time(entry.server_timestamp)),
    ]
    if entry.save_hash:
        rows.append(("Local hash", entry.save_hash[:12]))
    if entry.server_hash:
        rows.append(("Server hash", entry.server_hash[:12]))
    return (f"{entry.system} save", entry.display_name, pills, rows,
            _SAVE_HINTS.get(entry.status, "Y shows the details."))


def _catalog_detail(rom: dict) -> tuple:
    system = str(rom.get("system") or "?").upper()
    pills = [(system, SYSTEM_COLOR.get(system, DEFAULT_SYSTEM_COLOR))]
    try:
        ra = int(rom.get("ra_achievements") or 0)
    except (TypeError, ValueError):
        ra = 0
    if ra > 0:
        title_only = str(rom.get("ra_match") or "hash").lower() == "title"
        pills.append(("RA?" if title_only else f"RA {ra}",
                      theme.RA_BADGE_WEAK if title_only else theme.RA_BADGE))
    rows = [
        ("File", str(rom.get("filename") or "—")),
        ("Size", _fmt_bytes(int(rom.get("size") or 0))),
        ("Title ID", str(rom.get("title_id") or "—")),
    ]
    discs = int(rom.get("disc_total") or 0)
    if discs > 1:
        rows.append(("Disc", f"{rom.get('disc_index') or '?'} of {discs}"))
    if rom.get("bundle_kind"):
        rows.append(("Pack", _BUNDLE_KIND_LABELS.get(str(rom["bundle_kind"]),
                                                     str(rom["bundle_kind"]))))
    elif rom.get("is_bundle"):
        rows.append(("Files", str(len(rom.get("files") or []))))
    return ("Catalog", str(rom.get("name") or rom.get("filename") or "?"), pills,
            rows, "A queues the download; it runs in the background on the "
                  "Downloads tab.")


def _installed_detail(rom: "InstalledRom") -> tuple:
    pills = [(rom.system, SYSTEM_COLOR.get(rom.system, DEFAULT_SYSTEM_COLOR))]
    rows = [
        ("File", rom.filename),
        ("Folder", str(rom.path.parent)),
        ("Size", _fmt_bytes(rom.size)),
        ("Files", str(rom.total_files)),
    ]
    return ("Installed", rom.display_name, pills, rows,
            "A deletes it from this machine (asks first).")


# ──────────────────────────────────────────────────────────────────────────────
# Main Window
# ──────────────────────────────────────────────────────────────────────────────


class MainWindow(QMainWindow):
    # Top-level tabs, in L1/R1 order (wrapping), as on every GameSync client.
    TAB_SAVES, TAB_CATALOG, TAB_INSTALLED, TAB_DOWNLOADS, TAB_SETTINGS = range(5)
    TAB_LABELS = ("Saves", "Catalog", "Installed", "Downloads", "Settings")
    TAB_COUNT = len(TAB_LABELS)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("GameSync")
        self.setStyleSheet(theme.STYLESHEET)
        # Keys must reach keyPressEvent once the search box gives focus back.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._config = load_config()
        self._client = SyncClient(
            self._config["host"],
            self._config["port"],
            self._config["api_key"],
            saturn_sync_format=self._config.get("saturn_sync_format", "mednafen"),
        )

        self._all_entries: list[GameEntry] = []
        self._filtered_entries: list[GameEntry] = []
        self._system_filter = ALL_SYSTEMS
        self._status_filter = ALL_STATUSES
        self._search_visible = False
        self._search_text = ""
        self._systems: list[str] = [ALL_SYSTEMS]
        # Tab filter state lives on each per-tab view.  We mirror the
        # system list here so SELECT can cycle through the systems the
        # active tab actually has entries for.
        self._catalog_systems: list[str] = [CatalogView.ALL_SYSTEMS]
        self._installed_systems: list[str] = [InstalledView.ALL_SYSTEMS]
        self._active_tab = self.TAB_SAVES
        self._server_online = False
        # Set while Sync all runs, so A / X can't start a second transfer.
        self._sync_all_thread: Optional[QThread] = None

        # Background download queue.  The Downloads tab + every
        # ROM-download trigger site (catalog A button, save-detail
        # dialog) feed this manager instead of opening modal
        # progress dialogs that lock the UI.
        self._download_manager = DownloadManager(self._client, DOWNLOADS_DB_PATH, parent=self)
        # When a download lands, refresh the saves list (so the
        # save-status flips) and the Installed tab (so the new file
        # appears).  Mirrors what the old modal flow did on success.
        self._download_manager.completed.connect(self._on_download_completed)
        self._download_manager.list_changed.connect(self._on_downloads_list_changed)

        # Build UI
        central = QWidget()
        central.setObjectName("centralWidget")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(0)
        root.setContentsMargins(0, 0, 0, 0)

        self._header = HeaderBar(self.TAB_LABELS, version=_app_version())
        self._header.tab_clicked.connect(self._set_active_tab)
        root.addWidget(self._header)

        self._chips = ChipBar()
        self._chips.chip_clicked.connect(self._on_chip_clicked)
        root.addWidget(self._chips)

        self._search_bar = self._build_searchbar()
        root.addWidget(self._search_bar)
        self._search_bar.hide()

        self._list_view = GameListView()
        self._catalog_view = CatalogView()
        self._catalog_view.download_requested.connect(self._on_catalog_download)
        self._catalog_view.status_changed.connect(self._on_catalog_status_changed)
        self._catalog_view.systems_changed.connect(self._on_catalog_systems)

        self._installed_view = InstalledView()
        self._installed_view.delete_requested.connect(self._on_installed_delete)
        self._installed_view.status_changed.connect(self._on_installed_status_changed)
        self._installed_view.systems_changed.connect(self._on_installed_systems)

        self._downloads_view = DownloadsView(self._download_manager)

        self._settings_view = SettingsView()
        self._settings_view.activated_key.connect(self._on_setting_activated)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._list_view)       # TAB_SAVES
        self._stack.addWidget(self._catalog_view)    # TAB_CATALOG
        self._stack.addWidget(self._installed_view)  # TAB_INSTALLED
        self._stack.addWidget(self._downloads_view)  # TAB_DOWNLOADS
        self._stack.addWidget(self._settings_view)   # TAB_SETTINGS

        # List on the left, details of the highlighted row on the right.
        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(12, 10, 16, 6)
        body_layout.setSpacing(14)
        body_layout.addWidget(self._stack, 1)
        self._detail = DetailPanel()
        body_layout.addWidget(self._detail)
        root.addWidget(body, 1)

        self._banner = StatusBanner()
        root.addWidget(self._banner)

        self._controls = ControlsBar()
        root.addWidget(self._controls)

        for view in (
            self._list_view,
            self._catalog_view.list_widget(),
            self._installed_view.list_widget(),
            self._settings_view,
        ):
            view.selectionModel().currentChanged.connect(self._refresh_detail)

        self._refresh_settings_rows()
        self._refresh_tab_ui()

        # Gamepad polling
        self._gamepad_timer = None
        self._joystick = None
        self._modal_was_active = False
        self._suppress_gamepad_until_release = False
        self._last_nav_time = 0.0
        self._nav_repeat_delay = 0.15  # seconds
        if _PYGAME_OK:
            self._init_pygame()

        # Keyboard-based axis simulation (for d-pad repeat)
        self._held_keys: set[int] = set()
        self._key_repeat_timer = QTimer(self)
        self._key_repeat_timer.setInterval(120)
        self._key_repeat_timer.timeout.connect(self._handle_key_repeat)
        self._key_repeat_timer.start()

        # Start scanning
        QTimer.singleShot(200, self._start_scan)

    # ──────────────────────────────────────────────────────────────
    # UI builders
    # ──────────────────────────────────────────────────────────────

    def _build_searchbar(self) -> QWidget:
        container = QWidget()
        container.setFixedHeight(48)
        container.setStyleSheet(f"background:{theme.BG2};")
        layout = QHBoxLayout(container)
        layout.setContentsMargins(18, 6, 18, 6)

        self._search_edit = QLineEdit()
        self._search_edit.setObjectName("searchBox")
        self._search_edit.setPlaceholderText("Search by name or title ID…")
        self._search_edit.textChanged.connect(self._on_search_changed)
        layout.addWidget(self._search_edit)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(32, 32)
        close_btn.clicked.connect(self._hide_search)
        layout.addWidget(close_btn)

        return container

    def _notify(self, text: str, kind: str = "info", hold_ms: int = 5000) -> None:
        self._banner.show_message(text, kind, hold_ms)

    # ──────────────────────────────────────────────────────────────
    # Scanning
    # ──────────────────────────────────────────────────────────────

    def _start_scan(self):
        self._set_scanning(True, "Scanning emulators…")
        self._check_server_status()

        self._scan_thread = QThread()
        self._scan_worker = ScanWorker(
            self._config["emulation_path"],
            self._config.get("rom_scan_dir", ""),
            saturn_sync_format=self._config.get("saturn_sync_format", "mednafen"),
            save_dir_overrides=self._config.get("save_dir_overrides") or {},
        )
        self._scan_worker.moveToThread(self._scan_thread)
        self._scan_thread.started.connect(self._scan_worker.run)
        self._scan_worker.progress.connect(self._on_scan_progress)
        self._scan_worker.finished.connect(self._on_scan_finished)
        self._scan_worker.finished.connect(self._scan_thread.quit)
        self._scan_thread.start()

    def _on_scan_progress(self, msg: str):
        self._notify(msg, hold_ms=0)

    def _on_scan_finished(self, entries: list[GameEntry]):
        self._all_entries = entries
        self._update_system_list()
        self._apply_filters()
        self._set_scanning(True, "Checking server…")

        # Enrich with server status
        self._server_thread = QThread()
        self._server_worker = ServerWorker(
            list(entries),
            self._client,
            self._config["emulation_path"],
            save_dir_overrides=self._config.get("save_dir_overrides") or {},
        )
        self._server_worker.moveToThread(self._server_thread)
        self._server_thread.started.connect(self._server_worker.run)
        self._server_worker.finished.connect(self._on_server_finished)
        self._server_worker.finished.connect(self._server_thread.quit)
        self._server_thread.start()

    def _on_server_finished(self, entries: list[GameEntry]):
        self._all_entries = entries
        self._update_system_list()
        self._apply_filters()
        self._set_scanning(False)
        pending = sum(1 for e in entries if e.status in NEEDS_ACTION_STATUSES)
        if pending:
            self._notify(
                f"{len(entries)} saves  ·  {pending} need syncing (X syncs all)",
                "warn",
            )
        else:
            self._notify(f"{len(entries)} saves  ·  everything up to date", "ok")

    def _update_system_list(self):
        systems = sorted({e.system for e in self._all_entries if e.system != "?"})
        self._systems = [ALL_SYSTEMS] + systems
        if self._system_filter not in self._systems:
            self._system_filter = ALL_SYSTEMS

    def _apply_filters(self):
        filtered = self._all_entries

        if self._system_filter != ALL_SYSTEMS:
            filtered = [e for e in filtered if e.system == self._system_filter]

        if self._status_filter != ALL_STATUSES:
            filtered = [
                e for e in filtered if _matches_status_filter(e, self._status_filter)
            ]

        if self._search_text:
            q = self._search_text.lower()
            filtered = [
                e
                for e in filtered
                if q in e.display_name.lower() or q in e.title_id.lower()
            ]

        # Sort: needs-action first, then by system + name
        def sort_key(e: GameEntry):
            priority = 0 if e.status in NEEDS_ACTION_STATUSES else 1
            return (priority, e.system, e.display_name.lower())

        filtered.sort(key=sort_key)
        self._filtered_entries = filtered
        self._list_view.set_entries(filtered)
        if self._active_tab == self.TAB_SAVES:
            self._refresh_chips()
            self._refresh_detail()

    # ──────────────────────────────────────────────────────────────
    # Server status
    # ──────────────────────────────────────────────────────────────

    def _check_server_status(self):
        connected = self._client.check_connection()
        self._server_online = connected
        if connected:
            self._header.set_server(True, f"{self._config['host']}")
        else:
            self._header.set_server(False, "Server offline")

    def _set_scanning(self, active: bool, msg: str = ""):
        self._header.set_busy(active)
        if active and msg:
            self._notify(msg, hold_ms=0)
        elif not active:
            self._notify("")

    # ──────────────────────────────────────────────────────────────
    # Sub-tabs (SELECT) and the Saves status filter (L2 / R2)
    # ──────────────────────────────────────────────────────────────

    def _cycle_system(self, delta: int):
        if self._active_tab == self.TAB_CATALOG:
            self._catalog_view.cycle_system(delta, self._catalog_systems)
        elif self._active_tab == self.TAB_INSTALLED:
            self._installed_view.cycle_system(delta, self._installed_systems)
        elif self._active_tab == self.TAB_SAVES and self._systems:
            try:
                idx = self._systems.index(self._system_filter)
            except ValueError:
                idx = 0
            idx = (idx + delta) % len(self._systems)
            self._system_filter = self._systems[idx]
            self._apply_filters()
        self._refresh_chips()
        self._refresh_detail()

    def _on_chip_clicked(self, chip: str) -> None:
        if self._active_tab == self.TAB_CATALOG:
            self._catalog_view.set_system_filter(chip)
        elif self._active_tab == self.TAB_INSTALLED:
            self._installed_view.set_system_filter(chip)
        elif self._active_tab == self.TAB_SAVES:
            self._system_filter = chip
            self._apply_filters()
        self._refresh_chips()
        self._refresh_detail()

    def _cycle_status(self, delta: int):
        # Status filter only applies to the Saves tab — catalog /
        # installed rows don't carry a sync status.
        if self._active_tab != self.TAB_SAVES:
            return
        try:
            idx = STATUS_FILTER_CYCLE.index(self._status_filter)
        except ValueError:
            idx = 0
        idx = (idx + delta) % len(STATUS_FILTER_CYCLE)
        self._status_filter = STATUS_FILTER_CYCLE[idx]
        self._apply_filters()

    def _refresh_chips(self) -> None:
        """Sub-tab chips + the count for the active tab."""
        tab = self._active_tab
        if tab == self.TAB_SAVES:
            self._chips.set_state(
                self._systems,
                self._system_filter,
                left=f"Status: {self._status_filter}  (L2/R2)",
                right=f"{len(self._filtered_entries)} saves",
            )
        elif tab == self.TAB_CATALOG:
            view = self._catalog_view
            right = view.status_text() if view.is_loaded else "Loading…"
            self._chips.set_state(self._catalog_systems, view.system_filter(), right=right)
        elif tab == self.TAB_INSTALLED:
            view = self._installed_view
            right = (f"{view.visible_count()} ROMs" if view.is_loaded else "Scanning…")
            self._chips.set_state(self._installed_systems, view.system_filter(), right=right)
        elif tab == self.TAB_DOWNLOADS:
            count = len(self._download_manager.list_all())
            self._chips.set_state(
                [], "", left="Queue", hint="",
                right=f"{count} download{'' if count == 1 else 's'}",
            )
        else:
            self._chips.set_state(
                [], "", left="Server, folders and maintenance", hint="",
                right=f"GameSync {_app_version()}",
            )

    # ──────────────────────────────────────────────────────────────
    # Search
    # ──────────────────────────────────────────────────────────────

    def _toggle_search(self):
        if self._search_visible:
            self._hide_search()
        else:
            self._show_search()

    def _show_search(self):
        if self._active_tab not in (self.TAB_SAVES, self.TAB_CATALOG, self.TAB_INSTALLED):
            return
        self._search_visible = True
        self._search_bar.show()
        self._search_edit.setFocus()

    def _hide_search(self):
        self._search_visible = False
        self._search_bar.hide()
        self._search_text = ""
        self._search_edit.clear()
        if self._active_tab == self.TAB_CATALOG:
            self._catalog_view.set_search_text("")
        elif self._active_tab == self.TAB_INSTALLED:
            self._installed_view.set_search_text("")
        else:
            self._apply_filters()
        self.setFocus()

    def _on_search_changed(self, text: str):
        self._search_text = text
        if self._active_tab == self.TAB_CATALOG:
            self._catalog_view.set_search_text(text)
        elif self._active_tab == self.TAB_INSTALLED:
            self._installed_view.set_search_text(text)
        else:
            self._apply_filters()

    # ──────────────────────────────────────────────────────────────
    # Tab switching
    # ──────────────────────────────────────────────────────────────

    def _set_active_tab(self, idx: int) -> None:
        idx = max(0, min(self.TAB_COUNT - 1, idx))
        if idx == self._active_tab:
            return
        # Close the search overlay when switching: each tab maintains its
        # own search text via the shared search edit, so a stale query
        # shouldn't bleed across.
        if self._search_visible:
            self._hide_search()
        self._active_tab = idx
        self._stack.setCurrentIndex(idx)
        self._refresh_tab_ui()
        if idx == self.TAB_CATALOG and not self._catalog_view.is_loaded \
                and not self._catalog_view.is_loading:
            self._fetch_catalog()
        if idx == self.TAB_INSTALLED and not self._installed_view.is_loaded \
                and not self._installed_view.is_loading:
            self._fetch_installed()
        if idx == self.TAB_SETTINGS:
            self._refresh_settings_rows()

    def _cycle_tab(self, delta: int) -> None:
        self._set_active_tab((self._active_tab + delta) % self.TAB_COUNT)

    _CONTROL_MODES = {
        TAB_SAVES: ControlsBar.MODE_SAVES,
        TAB_CATALOG: ControlsBar.MODE_CATALOG,
        TAB_INSTALLED: ControlsBar.MODE_INSTALLED,
        TAB_DOWNLOADS: ControlsBar.MODE_DOWNLOADS,
        TAB_SETTINGS: ControlsBar.MODE_SETTINGS,
    }

    _SEARCH_PLACEHOLDERS = {
        TAB_SAVES: "Search by name or title ID…",
        TAB_CATALOG: "Search ROMs (name, system, filename)…",
        TAB_INSTALLED: "Search installed ROMs (name, system, filename)…",
    }

    def _refresh_tab_ui(self) -> None:
        tab = self._active_tab
        self._header.set_active_tab(tab)
        self._controls.set_mode(self._CONTROL_MODES[tab])
        self._search_edit.setPlaceholderText(self._SEARCH_PLACEHOLDERS.get(tab, ""))
        # Downloads rows carry their own progress; there is nothing to
        # describe beside them.
        self._detail.setVisible(tab != self.TAB_DOWNLOADS)
        self._refresh_chips()
        self._refresh_detail()

    # ──────────────────────────────────────────────────────────────
    # Detail panel
    # ──────────────────────────────────────────────────────────────

    def _refresh_detail(self, *_args) -> None:
        tab = self._active_tab
        if tab == self.TAB_SAVES:
            entry = self._list_view.selected_entry()
            if entry is None:
                self._detail.clear("No saves to show")
            else:
                self._detail.show_info(*_save_detail(entry))
        elif tab == self.TAB_CATALOG:
            rom = self._catalog_view.selected_rom()
            if rom is None:
                self._detail.clear("No ROM selected")
            else:
                self._detail.show_info(*_catalog_detail(rom))
        elif tab == self.TAB_INSTALLED:
            rom = self._installed_view.selected_rom()
            if rom is None:
                self._detail.clear("No installed ROM selected")
            else:
                self._detail.show_info(*_installed_detail(rom))
        elif tab == self.TAB_SETTINGS:
            row = self._settings_view.selected_row()
            if row is not None:
                self._detail.show_info("Settings", row.title, (),
                                       (("Current", row.value),) if row.value else (),
                                       row.description)

    # ──────────────────────────────────────────────────────────────
    # Settings tab
    # ──────────────────────────────────────────────────────────────

    def _refresh_settings_rows(self) -> None:
        cfg = self._config
        server = f"{cfg.get('host')}:{cfg.get('port')}"
        self._settings_view.set_rows([
            SettingsRow(
                "edit", "Server and folders", server,
                "Server address, API key, the emulation folder, the ROM "
                "folder and per-system overrides.  Saved when you close it; "
                "the saves are rescanned with the new settings.",
            ),
            SettingsRow(
                "rescan_saves", "Rescan saves", "",
                "Look through every emulator's save folder again and "
                "compare each save with the server.",
            ),
            SettingsRow(
                "refresh_catalog", "Refresh catalog", "",
                "Ask the server to rescan its ROM folder, throw the cached "
                "catalog away and download it again.  Use it after adding "
                "games to the server; otherwise only systems whose catalog "
                "changed are fetched.",
            ),
            SettingsRow(
                "rescan_installed", "Rescan installed games",
                cfg.get("rom_scan_dir") or cfg.get("emulation_path") or "",
                "Walk the local ROM folders again for the Installed tab.",
            ),
            SettingsRow(
                "exit", "Exit GameSync", "", "Close the app (START does the "
                "same from any tab).", danger=True,
            ),
        ])

    def _on_setting_activated(self, key: str) -> None:
        if key == "edit":
            self._open_settings()
        elif key == "rescan_saves":
            self._start_scan()
        elif key == "refresh_catalog":
            self._fetch_catalog(force=True)
        elif key == "rescan_installed":
            self._fetch_installed()
            self._notify("Rescanning installed games…")
        elif key == "exit":
            self._confirm_close()

    # ──────────────────────────────────────────────────────────────
    # Catalog wiring
    # ──────────────────────────────────────────────────────────────

    def _fetch_catalog(self, force: bool = False) -> None:
        """Load the catalog through the on-disk cache (see catalog_store).

        ``force`` is Settings > Refresh catalog: server rescan, cache wipe,
        refetch of every system.
        """
        if getattr(self, "_catalog_thread", None) is not None:
            if force:
                self._notify("The catalog is still loading - try again in a moment",
                             "warn")
            return
        self._catalog_view.mark_loading(True)
        if self._active_tab == self.TAB_CATALOG:
            self._refresh_chips()
        self._header.set_busy(True)
        self._notify("Refreshing the catalog…" if force else "Loading catalog…",
                     hold_ms=0)
        self._catalog_thread = QThread()
        self._catalog_worker = CatalogWorker(self._client, force=force)
        self._catalog_worker.moveToThread(self._catalog_thread)
        self._catalog_thread.started.connect(self._catalog_worker.run)
        self._catalog_worker.progress.connect(lambda text: self._notify(text, hold_ms=0))
        self._catalog_worker.finished.connect(self._on_catalog_loaded)
        self._catalog_worker.finished.connect(self._catalog_thread.quit)
        self._catalog_thread.finished.connect(self._catalog_worker.deleteLater)
        self._catalog_thread.finished.connect(self._catalog_thread.deleteLater)
        self._catalog_thread.finished.connect(self._on_catalog_thread_done)
        self._catalog_thread.start()

    def _on_catalog_thread_done(self) -> None:
        self._catalog_thread = None

    def _on_catalog_loaded(self, result) -> None:
        self._catalog_view.mark_loading(False)
        self._header.set_busy(False)
        if result.error and not result.rows:
            self._notify(result.summary(), "error", hold_ms=8000)
            ResultDialog(
                False,
                f"Failed to load ROM catalog.\n\n{result.error}",
                parent=self,
            ).exec()
        else:
            kind = "warn" if (result.offline or result.notes) else "ok"
            text = result.summary()
            if result.notes:
                text += "  ·  " + "  ·  ".join(result.notes)
            self._notify(text, kind)
        self._catalog_view.set_catalog(result.rows)
        if self._active_tab == self.TAB_CATALOG:
            self._refresh_tab_ui()

    def _on_catalog_systems(self, systems: list) -> None:
        self._catalog_systems = [CatalogView.ALL_SYSTEMS] + list(systems)
        if self._active_tab == self.TAB_CATALOG:
            self._refresh_chips()

    def _on_catalog_status_changed(self, _text: str) -> None:
        if self._active_tab == self.TAB_CATALOG:
            self._refresh_chips()
            self._refresh_detail()

    def _toggle_ra_only(self) -> None:
        if not self._catalog_view.is_loaded:
            return
        enabled = not self._catalog_view.ra_only()
        self._catalog_view.set_ra_only(enabled)
        if enabled:
            self._notify(
                f"RetroAchievements only: {self._catalog_view.visible_count()} "
                "games (X shows all)")
        else:
            self._notify("Showing every game")
        self._refresh_chips()
        self._refresh_detail()

    def _on_catalog_download(self, rom: dict) -> None:
        self._download_catalog_rom(rom)

    def _download_catalog_rom(self, rom: Optional[dict]) -> None:
        if not rom:
            return
        rom_id = str(rom.get("rom_id") or "")
        if not rom_id:
            ResultDialog(
                False, "Catalog entry is missing a rom_id.", parent=self
            ).exec()
            return

        system = (rom.get("system") or "").upper()
        filename = rom.get("filename") or f"{rom_id}.rom"
        display = rom.get("name") or filename
        size = int(rom.get("size") or 0)
        size_txt = f" ({_fmt_catalog_size(size)})" if size else ""

        emulation_path = self._config.get("emulation_path")
        rom_scan_dir = self._config.get("rom_scan_dir", "")
        if not emulation_path and not rom_scan_dir:
            ResultDialog(
                False,
                "No ROM destination is configured.  Set the emulation path or "
                "ROM scan directory in Settings and try again.",
                parent=self,
            ).exec()
            return

        roms_base = self._rom_roots_base(emulation_path, rom_scan_dir)
        target_dir = resolve_rom_target_dir(
            roms_base,
            system or "?",
            self._config.get("rom_dir_overrides") or {},
        )
        target_filename, extract_format = self._client.plan_rom_download(rom, system)
        if system in _NATIVE_EXTRACT_SKIP:
            extract_format = None
            target_filename = filename

        # PS3 bundle entry: server returns a ZIP_STORED archive of the
        # whole subfolder.  Target becomes the per-game directory; the
        # download worker extracts on completion.
        is_bundle = bool(rom.get("is_bundle"))
        # Xbox bundles with extract=iso return a single ISO file (not a
        # ZIP), because the server runs CCI→ISO conversion on the bundled
        # CCI and streams the result directly. xemu only loads ISO, so
        # skipping the bundle path here keeps the download as a single
        # file the emulator can open.
        if is_bundle and extract_format == "iso" and (system or "").upper() in ("XBOX", "X360", "XBOX360"):
            is_bundle = False
        if is_bundle:
            bundle_dir_name = (rom.get("name") or
                               Path(target_filename).stem) or rom_id
            target_path = target_dir / bundle_dir_name
        else:
            target_path = target_dir / target_filename

        bundle_kind = str(rom.get("bundle_kind") or "") if is_bundle else ""
        if is_bundle:
            file_count = len(rom.get("files") or [])
            what = _BUNDLE_KIND_LABELS.get(bundle_kind, "bundle")
            msg = (
                f"Download {what} '{display}'?\n"
                f"System: {system or 'unknown'}\n"
                f"Files: {file_count}{size_txt}\n"
                f"Destination: {target_path}"
            )
        else:
            msg = (
                f"Download ROM '{display}'?\n"
                f"System: {system or 'unknown'}\n"
                f"File: {target_filename}{size_txt}\n"
                f"Destination: {target_dir}"
            )
        if target_path.exists():
            msg += "\n\nA file with this name already exists and will be overwritten."

        dlg = ConfirmDialog(
            title="Download ROM",
            message=msg,
            confirm_label="Download",
            confirm_color=theme.STATUS_DOWNLOAD,
            parent=self,
        )
        if dlg.exec() != dlg.DialogCode.Accepted:
            return

        # Enqueue rather than block — the Downloads tab takes it from
        # here, and the user can keep browsing the catalog while the
        # transfer runs.  ``_on_download_completed`` rescans saves
        # and the Installed tab once a row finishes.
        self._download_manager.enqueue(
            rom_id=rom_id,
            system=system or "?",
            display_name=display,
            target_path=target_path,
            extract_format=None if is_bundle else extract_format,
            expected_size=size,
            is_bundle=is_bundle,
            bundle_kind=bundle_kind,
        )
        # Acknowledge in the banner and stay on the catalog, so the user
        # can keep browsing and queueing (the Downloads tab label shows
        # the queue's progress).
        self._notify(f"Queued '{display}' - see the Downloads tab", "ok")

    def _on_downloads_list_changed(self) -> None:
        """Keep the Downloads tab label and the chip-bar count current."""
        entities = self._download_manager.list_all()
        active = sum(1 for e in entities if e.status in ACTIVE_STATUSES)
        self._header.set_tab_badge(self.TAB_DOWNLOADS, f"({active})" if active else "")
        if self._active_tab == self.TAB_DOWNLOADS:
            self._refresh_chips()

    def _on_download_completed(self, _eid: str) -> None:
        """Refresh the Saves + Installed tabs whenever a download lands.

        The same post-download bookkeeping the modal flow used to do
        inline; centralising it here keeps the manager-driven enqueue
        path identical for catalog-tab and detail-dialog triggers.
        """
        self._start_scan()
        self._installed_view.mark_loading(True)
        self._installed_view.set_roms([])
        self._fetch_installed()

    # ──────────────────────────────────────────────────────────────
    # Installed tab wiring
    # ──────────────────────────────────────────────────────────────

    def _fetch_installed(self) -> None:
        emulation_path = self._config.get("emulation_path") or ""
        rom_scan_dir = self._config.get("rom_scan_dir", "") or ""
        self._installed_view.mark_loading(True)
        if self._active_tab == self.TAB_INSTALLED:
            self._refresh_chips()
        self._installed_thread = QThread()
        self._installed_worker = InstalledWorker(emulation_path, rom_scan_dir)
        self._installed_worker.moveToThread(self._installed_thread)
        self._installed_thread.started.connect(self._installed_worker.run)
        self._installed_worker.finished.connect(self._on_installed_loaded)
        self._installed_worker.finished.connect(self._installed_thread.quit)
        self._installed_thread.finished.connect(self._installed_worker.deleteLater)
        self._installed_thread.finished.connect(self._installed_thread.deleteLater)
        self._installed_thread.start()

    def _on_installed_loaded(self, roms: list) -> None:
        self._installed_view.set_roms(roms)
        if self._active_tab == self.TAB_INSTALLED:
            self._refresh_tab_ui()

    def _on_installed_systems(self, systems: list) -> None:
        self._installed_systems = [InstalledView.ALL_SYSTEMS] + list(systems)
        if self._active_tab == self.TAB_INSTALLED:
            self._refresh_chips()

    def _on_installed_status_changed(self, _text: str) -> None:
        if self._active_tab == self.TAB_INSTALLED:
            self._refresh_chips()
            self._refresh_detail()

    def _on_installed_delete(self, rom) -> None:
        self._delete_installed_rom(rom)

    def _delete_installed_rom(self, rom: "Optional[InstalledRom]") -> None:
        if rom is None:
            return
        size_txt = _fmt_catalog_size(rom.size)
        whole_folder = _whole_folder_delete_target(rom)

        if whole_folder is not None:
            detail = (
                f"Removes the whole folder (and every file inside it):\n"
                f"{whole_folder}"
            )
        else:
            companions_txt = (
                f" + {len(rom.companion_files)} companion file(s)"
                if rom.companion_files
                else ""
            )
            detail = (
                f"File: {rom.filename}{companions_txt}\n"
                f"Location: {rom.path.parent}"
            )

        msg = (
            f"Delete '{rom.display_name}' from disk?\n"
            f"System: {rom.system}\n"
            f"{detail}\n"
            f"Frees: {size_txt}\n\n"
            "This removes the data permanently and cannot be undone."
        )
        dlg = ConfirmDialog(
            title="Delete ROM",
            message=msg,
            confirm_label="Delete",
            confirm_color=theme.STATUS_CONFLICT,
            parent=self,
        )
        if dlg.exec() != dlg.DialogCode.Accepted:
            return

        result = delete_installed(rom)
        if result.errors:
            result_msg = (
                f"Deleted {result.deleted_count} file(s), but "
                f"{len(result.errors)} failed:\n\n"
                + "\n".join(result.errors)
            )
            ResultDialog(
                result.deleted_count > 0, result_msg, parent=self
            ).exec()
        else:
            if result.removed_dir is not None:
                done_msg = (
                    f"Deleted '{rom.display_name}' and its folder "
                    f"({result.deleted_count} file(s))."
                )
            else:
                done_msg = (
                    f"Deleted '{rom.display_name}' "
                    f"({result.deleted_count} file(s))."
                )
            ResultDialog(True, done_msg, parent=self).exec()

        # Refresh both the installed list and the save-sync list — a
        # deleted ROM may flip a synced entry back to "server only".
        self._fetch_installed()
        self._start_scan()

    def _rom_roots_base(self, emulation_path: str, rom_scan_dir: str) -> Path:
        """Mirror DetailDialog._rom_roots_base so destinations match."""
        if rom_scan_dir:
            scan_root = Path(rom_scan_dir)
            if scan_root.is_dir():
                return scan_root
        return (Path(emulation_path) if emulation_path else Path.home()) / "roms"

    # ──────────────────────────────────────────────────────────────
    # Actions on selected entry
    # ──────────────────────────────────────────────────────────────

    def _action_upload(self):
        entry = self._list_view.selected_entry()
        if not entry or not entry.save_path or not entry.save_path.exists():
            return

        # Build confirmation message
        size_str = ""
        if entry.save_size:
            kb = entry.save_size / 1024
            size_str = f"\nLocal save size: {kb:.1f} KB"
        msg = (
            f"Upload local save for '{entry.display_name}' to the server?\n"
            f"Title ID: {entry.title_id}{size_str}"
        )
        if entry.server_hash:
            msg += "\n\nThis will overwrite the existing server save."

        dlg = ConfirmDialog(
            title="Upload Save",
            message=msg,
            confirm_label="Upload",
            confirm_color=theme.STATUS_UPLOAD,
            parent=self,
        )
        if dlg.exec() != dlg.DialogCode.Accepted:
            return

        self._set_scanning(True, f"Uploading {entry.display_name}…")
        ok = self._client.upload_save(entry, force=True)
        self._set_scanning(False)

        ResultDialog(
            ok,
            f"'{entry.display_name}' uploaded successfully."
            if ok
            else f"Upload failed for '{entry.display_name}'.",
            parent=self,
        ).exec()

        if ok:
            entry.status = SyncStatus.SYNCED
            self._apply_filters()

    def _action_download(self):
        entry = self._list_view.selected_entry()
        if not entry or not entry.server_hash:
            return
        if entry.save_path is None:
            # Server-only saves on systems without a predicted save_path
            # (unrecognised platform, missing emulation config) fall through
            # here.  Tell the user instead of silently ignoring the click.
            ResultDialog(
                False,
                f"No local save destination for '{entry.display_name}' on"
                f" {entry.system}.  Install the ROM and rescan, or configure"
                " the emulation path in Settings.",
                parent=self,
            ).exec()
            return

        # Build confirmation message
        size_str = ""
        if entry.server_size:
            kb = entry.server_size / 1024
            size_str = f"\nServer save size: {kb:.1f} KB"
        msg = (
            f"Download server save for '{entry.display_name}'?\n"
            f"Title ID: {entry.title_id}{size_str}"
        )
        if entry.save_path.exists():
            msg += "\n\nThis will overwrite your local save file."

        dlg = ConfirmDialog(
            title="Download Save",
            message=msg,
            confirm_label="Download",
            confirm_color=theme.STATUS_DOWNLOAD,
            parent=self,
        )
        if dlg.exec() != dlg.DialogCode.Accepted:
            return

        self._set_scanning(True, f"Downloading {entry.display_name}…")
        ok = self._client.download_save(entry, force=True)
        self._set_scanning(False)

        ResultDialog(
            ok,
            f"'{entry.display_name}' downloaded successfully."
            if ok
            else f"Download failed for '{entry.display_name}'.",
            parent=self,
        ).exec()

        if ok:
            entry.status = SyncStatus.SYNCED
            self._apply_filters()

    def _action_sync(self):
        """A on Saves — smart sync: upload if the local copy changed,
        download if the server's did; a conflict (or nothing to do) opens
        the details so both copies can be compared."""
        if self._sync_all_thread is not None:
            return
        entry = self._list_view.selected_entry()
        if not entry:
            return
        if entry.status in (SyncStatus.LOCAL_NEWER, SyncStatus.LOCAL_ONLY):
            self._action_upload()
        elif entry.status in (SyncStatus.SERVER_NEWER, SyncStatus.SERVER_ONLY):
            self._action_download()
        else:
            self._action_detail()

    def _action_detail(self):
        entry = self._list_view.selected_entry()
        if not entry:
            return
        dlg = DetailDialog(
            entry,
            self._client,
            self,
            emulation_path=self._config.get("emulation_path"),
            rom_scan_dir=self._config.get("rom_scan_dir", ""),
            rom_dir_overrides=self._config.get("rom_dir_overrides") or {},
            download_manager=self._download_manager,
        )
        dlg.exec()
        # A freshly downloaded ROM doesn't show up in the current scan result,
        # so fall through to a full rescan instead of just reapplying filters.
        if getattr(dlg, "rom_downloaded", False):
            self._start_scan()
        else:
            self._apply_filters()

    def _action_sync_all(self):
        """X on Saves — upload / download every save whose plan is clear.

        Conflicts are left alone (they need the comparison in the details),
        and a server-only save is only fetched for a game whose ROM is on
        this machine, so Sync all never litters save folders for games that
        aren't installed.
        """
        if self._sync_all_thread is not None:
            return
        uploads, downloads, conflicts = _sync_all_plan(self._all_entries)
        if not uploads and not downloads:
            msg = "Nothing to sync"
            if conflicts:
                msg += f" ({len(conflicts)} conflict{'s' if len(conflicts) != 1 else ''}"
                msg += " - open them with Y)"
            self._notify(msg, "warn" if conflicts else "ok")
            return

        lines = [f"Upload {len(uploads)} save(s), download {len(downloads)} save(s)?"]
        if conflicts:
            lines.append(f"\n{len(conflicts)} conflict(s) are skipped - "
                         "resolve them from the details (Y).")
        dlg = ConfirmDialog(
            title="Sync all",
            message="\n".join(lines),
            confirm_label="Sync",
            confirm_color=theme.ACCENT,
            parent=self,
        )
        if dlg.exec() != dlg.DialogCode.Accepted:
            return

        self._header.set_busy(True)
        self._sync_all_thread = QThread()
        self._sync_all_worker = SyncAllWorker(self._client, uploads, downloads)
        self._sync_all_worker.moveToThread(self._sync_all_thread)
        self._sync_all_thread.started.connect(self._sync_all_worker.run)
        self._sync_all_worker.progress.connect(
            lambda done, total, name: self._notify(
                f"Syncing {done}/{total}: {name}", hold_ms=0))
        self._sync_all_worker.finished.connect(self._on_sync_all_finished)
        self._sync_all_worker.finished.connect(self._sync_all_thread.quit)
        self._sync_all_thread.finished.connect(self._sync_all_worker.deleteLater)
        self._sync_all_thread.finished.connect(self._sync_all_thread.deleteLater)
        # Drop the reference only once the thread has really stopped:
        # a QThread destroyed while running aborts the process.
        self._sync_all_thread.finished.connect(self._on_sync_all_thread_done)
        self._sync_all_thread.start()

    def _on_sync_all_thread_done(self) -> None:
        self._sync_all_thread = None

    def _on_sync_all_finished(self, ok: int, failed: list) -> None:
        self._header.set_busy(False)
        if failed:
            ResultDialog(
                ok > 0,
                f"Synced {ok} save(s); {len(failed)} failed:\n\n"
                + "\n".join(failed[:12])
                + ("\n…" if len(failed) > 12 else ""),
                parent=self,
            ).exec()
        # Statuses changed on both sides; recompute them from scratch.
        self._start_scan()
        self._notify(f"Synced {ok} save(s)" + (f", {len(failed)} failed" if failed else ""),
                     "warn" if failed else "ok")

    def _action_primary(self):
        """A — the highlighted row's main action on the active tab."""
        tab = self._active_tab
        if tab == self.TAB_SAVES:
            self._action_sync()
        elif tab == self.TAB_CATALOG:
            self._download_catalog_rom(self._catalog_view.selected_rom())
        elif tab == self.TAB_INSTALLED:
            self._delete_installed_rom(self._installed_view.selected_rom())
        elif tab == self.TAB_DOWNLOADS:
            done = self._downloads_view.activate_selected()
            if done:
                self._notify(f"Download {done}")
        else:
            self._settings_view.activate()

    def _action_secondary(self):
        """X — the tab's secondary action."""
        tab = self._active_tab
        if tab == self.TAB_SAVES:
            self._action_sync_all()
        elif tab == self.TAB_CATALOG:
            self._toggle_ra_only()
        elif tab == self.TAB_INSTALLED:
            self._fetch_installed()
            self._notify("Rescanning installed games…")
        elif tab == self.TAB_DOWNLOADS:
            self._download_manager.clear_finished()
            self._notify("Cleared finished downloads")

    def _action_y(self):
        """Y — details on Saves, search on Catalog / Installed, remove on
        Downloads."""
        tab = self._active_tab
        if tab == self.TAB_SAVES:
            self._action_detail()
        elif tab in (self.TAB_CATALOG, self.TAB_INSTALLED):
            self._show_search()
        elif tab == self.TAB_DOWNLOADS:
            ent = self._downloads_view.selected_entity()
            if ent is None:
                return
            dlg = ConfirmDialog(
                title="Remove download",
                message=f"Remove '{ent.display_name or ent.rom_id}' from the "
                        "queue?\nAn unfinished download is cancelled and its "
                        "partial file deleted.",
                confirm_label="Remove",
                confirm_color=theme.ERR,
                parent=self,
            )
            if dlg.exec() == dlg.DialogCode.Accepted:
                self._downloads_view.remove_selected()

    def _action_back(self):
        """B — cancel / back.  Never starts anything and never exits."""
        if self._search_visible or self._search_text:
            self._hide_search()
        elif self._active_tab == self.TAB_DOWNLOADS:
            if self._downloads_view.pause_running():
                self._notify("Download paused (A resumes it)")

    def _open_settings(self):
        dlg = SettingsDialog(self._config, self)
        if dlg.exec():
            self._config.update(dlg.get_config())
            save_config(self._config)
            self._client = SyncClient(
                self._config["host"],
                self._config["port"],
                self._config["api_key"],
                saturn_sync_format=self._config.get("saturn_sync_format", "mednafen"),
            )
            self._refresh_settings_rows()
            self._start_scan()
            # Another server means another catalog.
            if self._catalog_view.is_loaded:
                self._fetch_catalog()

    def _confirm_close(self):
        dlg = ConfirmDialog(
            title="Exit GameSync",
            message="Close GameSync?",
            confirm_label="Exit",
            confirm_color=theme.ERR,
            parent=self,
        )
        if dlg.exec() == dlg.DialogCode.Accepted:
            self.close()

    # ──────────────────────────────────────────────────────────────
    # Keyboard input (also catches gamepad-via-keyboard mapping)
    # ──────────────────────────────────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent):
        if self._search_visible and event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Return,
                                                    Qt.Key.Key_Enter, Qt.Key.Key_Down):
            if event.key() == Qt.Key.Key_Escape:
                self._hide_search()
            else:
                # Keep the query, hand the d-pad back to the list.
                self._search_visible = False
                self._search_bar.setVisible(bool(self._search_text))
                self.setFocus()
            return
        if self._search_visible:
            super().keyPressEvent(event)
            return

        key = event.key()
        self._held_keys.add(key)

        if key in (Qt.Key.Key_Up, Qt.Key.Key_W):
            self._active_view_move(-1)
        elif key in (Qt.Key.Key_Down, Qt.Key.Key_S):
            self._active_view_move(1)
        elif key in (Qt.Key.Key_PageUp, Qt.Key.Key_Left):
            self._active_view_page(-1)
        elif key in (Qt.Key.Key_PageDown, Qt.Key.Key_Right):
            self._active_view_page(1)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_A, Qt.Key.Key_Space):
            self._action_primary()
        elif key in (Qt.Key.Key_B, Qt.Key.Key_Escape, Qt.Key.Key_Backspace):
            self._action_back()
        elif key == Qt.Key.Key_X:
            self._action_secondary()
        elif key == Qt.Key.Key_Y:
            self._action_y()
        elif key in (Qt.Key.Key_Tab, Qt.Key.Key_E, Qt.Key.Key_F6):
            self._cycle_tab(1)
        elif key in (Qt.Key.Key_Backtab, Qt.Key.Key_Q, Qt.Key.Key_F5):
            self._cycle_tab(-1)
        elif key in (Qt.Key.Key_BracketRight, Qt.Key.Key_F2):
            self._cycle_system(1)
        elif key in (Qt.Key.Key_BracketLeft, Qt.Key.Key_F1):
            self._cycle_system(-1)
        elif key == Qt.Key.Key_F3:
            self._cycle_status(-1)
        elif key == Qt.Key.Key_F4:
            self._cycle_status(1)
        elif key in (Qt.Key.Key_F10, Qt.Key.Key_Menu):
            self._confirm_close()
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent):
        self._held_keys.discard(event.key())
        super().keyReleaseEvent(event)

    def _handle_key_repeat(self):
        if Qt.Key.Key_Up in self._held_keys or Qt.Key.Key_W in self._held_keys:
            self._active_view_move(-1)
        if Qt.Key.Key_Down in self._held_keys or Qt.Key.Key_S in self._held_keys:
            self._active_view_move(1)

    def _active_view_move(self, delta: int) -> None:
        self._current_list_view().move_selection(delta)
        self._refresh_detail()

    def _active_view_page(self, direction: int) -> None:
        view = self._current_list_view()
        if direction > 0:
            view.page_down()
        else:
            view.page_up()
        self._refresh_detail()

    def _active_view_alphabet_jump(self, direction: int) -> None:
        # Last stage of the d-pad hold ramp-up: jump to the next
        # display-name initial so a held LEFT/RIGHT sweeps A → B → C …
        view = self._current_list_view()
        jump = getattr(view, "alphabet_jump", None)
        if callable(jump):
            jump(direction)
            self._refresh_detail()

    def _current_list_view(self):
        tab = self._active_tab
        if tab == self.TAB_CATALOG:
            return self._catalog_view
        if tab == self.TAB_INSTALLED:
            return self._installed_view
        if tab == self.TAB_DOWNLOADS:
            return self._downloads_view
        if tab == self.TAB_SETTINGS:
            return self._settings_view
        return self._list_view

    # ──────────────────────────────────────────────────────────────
    # Pygame gamepad polling
    # ──────────────────────────────────────────────────────────────

    def _init_pygame(self):
        try:
            pygame.init()
            pygame.joystick.init()
            self._btn_state: dict[int | str, bool] = {}
            self._axis_nav_time = 0.0
            # Hold-to-accelerate state for d-pad left/right: ``_nav_x_dir``
            # is the active direction (-1 / 0 / 1), ``_nav_x_held_since``
            # is when that direction started, and ``_nav_x_last_action``
            # is the timestamp of the most recent page-scroll or alphabet
            # jump.  Both timestamps are pygame.time-derived seconds.
            self._nav_x_dir = 0
            self._nav_x_held_since = 0.0
            self._nav_x_last_action = 0.0
            self._try_grab_joystick()

            self._gamepad_timer = QTimer(self)
            self._gamepad_timer.setInterval(16)  # ~60 Hz
            self._gamepad_timer.timeout.connect(self._poll_gamepad)
            self._gamepad_timer.start()

            # Hot-plug: re-scan for joysticks every 2 seconds
            self._hotplug_timer = QTimer(self)
            self._hotplug_timer.setInterval(2000)
            self._hotplug_timer.timeout.connect(self._check_hotplug)
            self._hotplug_timer.start()
        except Exception as e:
            print(f"[Gamepad] pygame init failed: {e}")

    def _try_grab_joystick(self):
        """Grab the first available joystick, or None."""
        try:
            pygame.joystick.quit()
            pygame.joystick.init()
            count = pygame.joystick.get_count()
            if count > 0:
                js = pygame.joystick.Joystick(0)
                js.init()
                if self._joystick is None:
                    name = js.get_name()
                    print(
                        f"[Gamepad] Connected: {name} ({js.get_numbuttons()} buttons, "
                        f"{js.get_numaxes()} axes, {js.get_numhats()} hats)"
                    )
                self._joystick = js
            else:
                if self._joystick is not None:
                    print("[Gamepad] Disconnected")
                self._joystick = None
        except Exception:
            self._joystick = None

    def _check_hotplug(self):
        """Periodically re-scan for joystick connect/disconnect."""
        had_js = self._joystick is not None
        try:
            cur_count = pygame.joystick.get_count()
        except Exception:
            cur_count = 0
        has_js = cur_count > 0

        # Only re-grab if state changed (connected/disconnected)
        if has_js != had_js:
            self._try_grab_joystick()
            self._btn_state.clear()

    def _prime_main_button_state(self) -> None:
        """Capture current button states so modal-close presses don't leak through."""
        if not _PYGAME_OK or self._joystick is None:
            return
        try:
            pygame.event.pump()
        except Exception:
            return

        for idx in range(9):
            try:
                self._btn_state[idx] = bool(self._joystick.get_button(idx))
            except Exception:
                self._btn_state[idx] = False

        try:
            l2 = self._joystick.get_axis(4)
            r2 = self._joystick.get_axis(5)
        except Exception:
            l2, r2 = -1.0, -1.0
        self._btn_state["l2"] = l2 > 0.5
        self._btn_state["r2"] = r2 > 0.5
        self._axis_nav_time = 0.0

    def suppress_gamepad_until_release(self) -> None:
        """Ignore controller input until all active buttons/axes are released."""
        self._suppress_gamepad_until_release = True
        self._prime_main_button_state()

    def _capture_gamepad_state(self) -> tuple[dict[int, bool], tuple[int, int], float, float]:
        buttons: dict[int, bool] = {}
        for idx in range(9):
            try:
                buttons[idx] = bool(self._joystick.get_button(idx))
            except Exception:
                buttons[idx] = False
        try:
            hat = self._joystick.get_hat(0)
            hat_x, hat_y = hat
        except Exception:
            hat_x, hat_y = 0, 0
        try:
            l2 = self._joystick.get_axis(4)
            r2 = self._joystick.get_axis(5)
        except Exception:
            l2, r2 = -1.0, -1.0
        return buttons, (hat_x, hat_y), l2, r2

    def _poll_gamepad(self):
        if not _PYGAME_OK or self._joystick is None:
            return
        try:
            pygame.event.pump()
        except Exception:
            return

        now = time.monotonic()
        modal = QApplication.activeModalWidget()
        dialog_target = modal if modal is not None and modal is not self else None
        buttons, hat, l2, r2 = self._capture_gamepad_state()
        hat_x, hat_y = hat

        if self._suppress_gamepad_until_release:
            for idx, current in buttons.items():
                self._btn_state[idx] = current
            self._btn_state["l2"] = l2 > 0.5
            self._btn_state["r2"] = r2 > 0.5
            self._axis_nav_time = 0.0
            any_pressed = any(buttons.values()) or hat_x != 0 or hat_y != 0 or l2 > 0.5 or r2 > 0.5
            if not any_pressed:
                self._suppress_gamepad_until_release = False
            return

        # ── Buttons (edge-triggered) ──────────────────────────────
        def btn_pressed(idx: int) -> bool:
            cur = buttons.get(idx, False)
            prev = self._btn_state.get(idx, False)
            self._btn_state[idx] = cur
            return cur and not prev

        if dialog_target is not None:
            self._modal_was_active = True
            for idx in range(9):
                btn_pressed(idx)
            self._btn_state["l2"] = l2 > 0.5
            self._btn_state["r2"] = r2 > 0.5
            self._axis_nav_time = 0.0
            return

        if self._modal_was_active:
            self._modal_was_active = False
            self._prime_main_button_state()
            return

        # The shared GameSync scheme: A confirms, B only cancels, X is the
        # tab's secondary action, Y details / search, L1 / R1 switch tabs,
        # SELECT the sub-tab (system), START exits after asking.  L2 / R2
        # step the Saves status filter.
        if btn_pressed(0):
            self._action_primary()  # A
        if btn_pressed(1):
            self._action_back()  # B
        if btn_pressed(2):
            self._action_secondary()  # X
        if btn_pressed(3):
            self._action_y()  # Y
        if btn_pressed(4):
            self._cycle_tab(-1)  # L1
        if btn_pressed(5):
            self._cycle_tab(1)  # R1
        if btn_pressed(6):
            self._cycle_system(1)  # SELECT / View
        if btn_pressed(7):
            self._confirm_close()  # START / Menu
        if btn_pressed(8):
            pass  # Steam button — ignore

        # ── Left stick Y axis ─────────────────────────────────────
        try:
            axis_y = self._joystick.get_axis(1)  # +1 = down
        except Exception:
            axis_y = 0.0

        # L2/R2 pressed detection via axis crossing threshold.
        l2_prev = self._btn_state.get("l2", False)
        r2_prev = self._btn_state.get("r2", False)
        l2_cur = l2 > 0.5
        r2_cur = r2 > 0.5
        self._btn_state["l2"] = l2_cur
        self._btn_state["r2"] = r2_cur
        if dialog_target is None and l2_cur and not l2_prev:
            self._cycle_status(-1)
        if dialog_target is None and r2_cur and not r2_prev:
            self._cycle_status(1)

        # ── Navigation with repeat ────────────────────────────────
        DEADZONE = 0.4
        REPEAT_DELAY = 0.15
        # Hold-to-accelerate thresholds for d-pad left/right.  First press
        # fires one page scroll immediately.  After ``HOLD_FAST_AFTER`` of
        # continuous holding we start auto-paging at ``FAST_CADENCE``.
        # After ``HOLD_ALPHA_AFTER`` we escalate to alphabet jumps at
        # ``ALPHA_CADENCE`` so the user can sweep a long catalog in
        # seconds without breaking their thumb off the d-pad.
        HOLD_FAST_AFTER = 0.5
        HOLD_ALPHA_AFTER = 1.5
        FAST_CADENCE = 0.10
        ALPHA_CADENCE = 0.25

        nav_y = 0
        if hat_y == 1 or axis_y < -DEADZONE:
            nav_y = -1
        elif hat_y == -1 or axis_y > DEADZONE:
            nav_y = 1

        nav_x = 0
        if hat_x == -1:
            nav_x = -1
        elif hat_x == 1:
            nav_x = 1

        if dialog_target is not None:
            return

        # ── Y axis — single-row move at fixed repeat ──────────────
        if nav_y != 0:
            if now - self._axis_nav_time >= REPEAT_DELAY:
                self._axis_nav_time = now
                self._active_view_move(nav_y)
        else:
            self._axis_nav_time = 0.0

        # ── X axis — page scroll ramping into alphabet jump ───────
        if nav_x != 0:
            if self._nav_x_dir != nav_x:
                # Fresh press (or direction reversal): fire once
                # immediately and start the hold clock.
                self._nav_x_dir = nav_x
                self._nav_x_held_since = now
                self._nav_x_last_action = now
                self._active_view_page(nav_x)
            else:
                held = now - self._nav_x_held_since
                since_last = now - self._nav_x_last_action
                if held >= HOLD_ALPHA_AFTER:
                    if since_last >= ALPHA_CADENCE:
                        self._nav_x_last_action = now
                        self._active_view_alphabet_jump(nav_x)
                elif held >= HOLD_FAST_AFTER:
                    if since_last >= FAST_CADENCE:
                        self._nav_x_last_action = now
                        self._active_view_page(nav_x)
                # held < HOLD_FAST_AFTER: do nothing (initial press
                # already fired; user hasn't held long enough for
                # auto-repeat yet)
        else:
            self._nav_x_dir = 0
            self._nav_x_held_since = 0.0
            self._nav_x_last_action = 0.0

    def closeEvent(self, event):
        # Flag any active downloads as "paused" before tearing down
        # the worker threads so the .part files aren't left in
        # ambiguous "downloading" state — the recovery pass on next
        # launch picks them up cleanly.
        try:
            self._download_manager.shutdown()
        except Exception:
            pass
        super().closeEvent(event)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _font(size: int, bold: bool = False) -> QFont:
    f = QFont()
    f.setPointSize(size)
    if bold:
        f.setBold(True)
    return f


def _fmt_catalog_size(num_bytes: int) -> str:
    """Human-readable size used by the catalog download confirmation."""
    if num_bytes <= 0:
        return ""
    units = [("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)]
    for unit, factor in units:
        if num_bytes >= factor:
            return f"{num_bytes / factor:.2f} {unit}"
    return f"{num_bytes} B"


def _whole_folder_delete_target(rom: "InstalledRom") -> "Optional[Path]":
    """Return the folder that ``delete_installed`` would rmtree, or None.

    Used by the confirm dialog so the message can tell the user up
    front that a whole folder is about to disappear, not just the
    tracked files.
    """
    return (
        rom.path.parent
        if would_remove_whole_folder(rom)
        else None
    )
