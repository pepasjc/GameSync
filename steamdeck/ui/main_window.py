"""
Main window for the Steam Deck SaveSync client.

Layout (1280 × 800 full-screen):
  ┌───────────────────────────────────────────────────────┐
  │  Top bar: title · server status · scan spinner        │
  ├───────────────────────────────────────────────────────┤
  │  Filter bar: [< System >]  [< Status >]  search box  │
  ├───────────────────────────────────────────────────────┤
  │                                                       │
  │            Game list (LazyColumn equivalent)          │
  │                                                       │
  ├───────────────────────────────────────────────────────┤
  │  Controls: [A] Info  [B] Exit  [X] Sync  …           │
  └───────────────────────────────────────────────────────┘

Gamepad mapping (polled via pygame in a QTimer):
  D-pad / L-stick ↑↓  →  navigate list
  D-pad ←→            →  cycle system filter
  A                   →  open save info dialog (upload/download from there)
  B                   →  close app (with confirmation)
  X                   →  sync selected (upload OR download depending on status)
  Y                   →  refresh
  L1                  →  prev system filter
  R1                  →  next system filter
  L2                  →  prev status filter
  R2                  →  next status filter
  Start               →  settings
  Select              →  toggle search
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
    QLabel,
    QLineEdit,
    QPushButton,
    QProgressBar,
)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, QObject
from PyQt6.QtGui import QFont, QKeyEvent

from scanner.models import GameEntry, SyncStatus, STATUS_LABEL
from scanner import scan_all, rpcs3, dolphin, server_only
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
from config import load_config, save_config
from . import theme
from .catalog_view import CatalogView
from .game_list import GameListView
from .installed_view import InstalledView
from .controls_bar import ControlsBar
from .settings_dialog import SettingsDialog
from .detail_dialog import DetailDialog
from .confirm_dialog import ConfirmDialog, ResultDialog
from .download_dialog import DownloadProgressDialog
from .detail_dialog import _NATIVE_COMPRESSED_FORMAT_SYSTEMS as _NATIVE_EXTRACT_SKIP

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
    ):
        super().__init__()
        self._path = emulation_path
        self._rom_scan_dir = rom_scan_dir
        self._saturn_sync_format = saturn_sync_format

    def run(self):
        results = scan_all(
            self._path,
            rom_scan_dir=self._rom_scan_dir,
            progress_cb=self.progress.emit,
            saturn_sync_format=self._saturn_sync_format,
        )
        self.finished.emit(results)


class CatalogWorker(QObject):
    """Fetches the server's full ROM catalog off the main thread."""

    finished = pyqtSignal(list, str)  # roms, error_detail

    def __init__(self, client: SyncClient):
        super().__init__()
        self._client = client

    def run(self) -> None:
        try:
            roms = self._client.list_roms() or []
        except Exception as exc:  # list_roms already swallows most errors
            self.finished.emit([], str(exc) or exc.__class__.__name__)
            return
        self.finished.emit(roms, "")


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

    def __init__(self, entries: list[GameEntry], client: SyncClient, emulation_path: str):
        super().__init__()
        self._entries = entries
        self._client = client
        self._emulation_path = Path(emulation_path)

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
        skip_systems = {"GC", "PS3", "PSP", "WII", "NSW", "?"}
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
                or entry.title_id.startswith("GC_")  # GCI descriptions < server DB
            )
            if not needs_name:
                continue

            # Use title_id as the lookup code (server resolves by code).
            # For PS3 saves with slot suffixes (e.g. BLJS10001GAME), trim to
            # the 9-char base code so the server DB can resolve the name.
            code = entry.title_id
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

    def run(self):
        # ── Pre-fetch the server's ROM catalog so we can (a) re-key local
        # slug entries to whatever title_id the server uses for the same
        # ROM, and (b) flag each entry with the ROMs the server can hand
        # back, so the UI can hide Download-ROM when nothing's available.
        catalog = self._client.list_roms() or []
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
        # Generic placeholders for every other system — lets the user see
        # (and Download-ROM for) server saves on systems without a dedicated
        # scanner-level builder (PS1, PS2, PSP, GBA, SNES, NES, ...).
        seen_ids = {entry.title_id for entry in updated}
        updated.extend(
            server_only.build_server_only_entries(
                server_saves, seen_ids, self._emulation_path
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


# ──────────────────────────────────────────────────────────────────────────────
# Main Window
# ──────────────────────────────────────────────────────────────────────────────


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SaveSync")
        self.setStyleSheet(theme.STYLESHEET)

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
        # system list here so the top bar can cycle through the
        # systems the active tab actually has entries for.
        self._catalog_systems: list[str] = [CatalogView.ALL_SYSTEMS]
        self._installed_systems: list[str] = [InstalledView.ALL_SYSTEMS]
        self._active_tab = 0  # 0 = saves, 1 = catalog, 2 = installed

        # Build UI
        central = QWidget()
        central.setObjectName("centralWidget")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(0)
        root.setContentsMargins(0, 0, 0, 0)

        root.addWidget(self._build_topbar())
        root.addWidget(self._build_tabbar())
        root.addWidget(self._build_filterbar())
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

        self._stack = QStackedWidget()
        self._stack.addWidget(self._list_view)       # idx 0 — saves
        self._stack.addWidget(self._catalog_view)    # idx 1 — catalog
        self._stack.addWidget(self._installed_view)  # idx 2 — installed
        root.addWidget(self._stack, 1)

        self._controls = ControlsBar()
        root.addWidget(self._controls)

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

    def _build_topbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("topBar")
        bar.setFixedHeight(theme.TOPBAR_H)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(12)

        icon = QLabel("💾")
        icon.setFont(_font(18))
        layout.addWidget(icon)

        title = QLabel("SaveSync")
        title.setFont(_font(16, bold=True))
        layout.addWidget(title)

        layout.addStretch()

        self._status_dot = QLabel("●")
        self._status_dot.setStyleSheet(f"color: {theme.STATUS_NO_SAVE};")
        layout.addWidget(self._status_dot)

        self._status_label = QLabel("Checking…")
        self._status_label.setFont(_font(11))
        self._status_label.setStyleSheet(f"color: {theme.TEXT_SECONDARY};")
        layout.addWidget(self._status_label)

        layout.addSpacing(16)

        self._scan_bar = QProgressBar()
        self._scan_bar.setFixedWidth(160)
        self._scan_bar.setFixedHeight(6)
        self._scan_bar.setRange(0, 0)  # indeterminate
        self._scan_bar.setTextVisible(False)
        self._scan_bar.hide()
        self._scan_bar.setStyleSheet(
            f"QProgressBar {{ background:{theme.TEXT_DIM}; border-radius:3px; }}"
            f"QProgressBar::chunk {{ background:{theme.ACCENT}; border-radius:3px; }}"
        )
        layout.addWidget(self._scan_bar)

        self._scan_label = QLabel("")
        self._scan_label.setFont(_font(10))
        self._scan_label.setStyleSheet(f"color:{theme.TEXT_SECONDARY};")
        layout.addWidget(self._scan_label)

        return bar

    def _build_tabbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("tabBar")
        bar.setFixedHeight(40)
        bar.setStyleSheet(
            f"QWidget#tabBar {{ background: {theme.BG_TOPBAR}; "
            f"border-top: 1px solid {theme.TEXT_DIM}; }}"
        )
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 4, 12, 4)
        layout.setSpacing(8)

        self._tab_buttons: list[QPushButton] = []
        for idx, label in enumerate(("My Games", "ROM Catalog", "Installed")):
            btn = QPushButton(label)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setCheckable(True)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.clicked.connect(lambda _=False, i=idx: self._set_active_tab(i))
            self._tab_buttons.append(btn)
            layout.addWidget(btn)

        layout.addStretch()

        hint = QLabel("L1/R1  ·  Tab")
        hint.setStyleSheet(f"color:{theme.TEXT_DIM}; font-size:10pt;")
        layout.addWidget(hint)

        return bar

    def _tab_button_style(self, active: bool) -> str:
        if active:
            return (
                f"QPushButton {{ background: {theme.ACCENT}; "
                f"color: {theme.BG_WINDOW}; border: none; "
                f"border-radius: 6px; padding: 4px 18px; font-weight: bold; }}"
            )
        return (
            f"QPushButton {{ background: transparent; "
            f"color: {theme.TEXT_SECONDARY}; border: 1px solid {theme.TEXT_DIM}; "
            f"border-radius: 6px; padding: 4px 18px; }}"
            f"QPushButton:hover {{ color: {theme.TEXT_PRIMARY}; "
            f"border-color: {theme.ACCENT}; }}"
        )

    def _build_filterbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("filterBar")
        bar.setFixedHeight(theme.FILTERBAR_H)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(24)

        sys_lbl = QLabel("System:")
        sys_lbl.setStyleSheet(f"color:{theme.TEXT_SECONDARY};")
        layout.addWidget(sys_lbl)

        self._system_label = QLabel(ALL_SYSTEMS)
        self._system_label.setFont(_font(12, bold=True))
        layout.addWidget(self._system_label)

        layout.addSpacing(32)

        stat_lbl = QLabel("Status:")
        stat_lbl.setStyleSheet(f"color:{theme.TEXT_SECONDARY};")
        layout.addWidget(stat_lbl)

        self._status_filter_label = QLabel(ALL_STATUSES)
        self._status_filter_label.setFont(_font(12, bold=True))
        layout.addWidget(self._status_filter_label)

        layout.addStretch()

        self._count_label = QLabel("")
        self._count_label.setStyleSheet(f"color:{theme.TEXT_SECONDARY};")
        layout.addWidget(self._count_label)

        return bar

    def _build_searchbar(self) -> QWidget:
        container = QWidget()
        container.setFixedHeight(44)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(14, 4, 14, 4)

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
        )
        self._scan_worker.moveToThread(self._scan_thread)
        self._scan_thread.started.connect(self._scan_worker.run)
        self._scan_worker.progress.connect(self._on_scan_progress)
        self._scan_worker.finished.connect(self._on_scan_finished)
        self._scan_worker.finished.connect(self._scan_thread.quit)
        self._scan_thread.start()

    def _on_scan_progress(self, msg: str):
        self._scan_label.setText(msg)

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

    def _update_system_list(self):
        systems = sorted({e.system for e in self._all_entries if e.system != "?"})
        self._systems = [ALL_SYSTEMS] + systems

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
        if self._active_tab == 0:
            self._count_label.setText(f"{len(filtered)} games")

    # ──────────────────────────────────────────────────────────────
    # Server status
    # ──────────────────────────────────────────────────────────────

    def _check_server_status(self):
        connected = self._client.check_connection()
        if connected:
            self._status_dot.setStyleSheet(f"color:{theme.STATUS_SYNCED};")
            host = self._config["host"]
            self._status_label.setText(f"Connected · {host}")
        else:
            self._status_dot.setStyleSheet(f"color:{theme.STATUS_CONFLICT};")
            self._status_label.setText("Disconnected")

    def _set_scanning(self, active: bool, msg: str = ""):
        if active:
            self._scan_bar.show()
            self._scan_label.setText(msg)
        else:
            self._scan_bar.hide()
            self._scan_label.setText("")

    # ──────────────────────────────────────────────────────────────
    # Filter cycling
    # ──────────────────────────────────────────────────────────────

    def _cycle_system(self, delta: int):
        if self._active_tab == 1:
            self._catalog_view.cycle_system(delta, self._catalog_systems)
            self._system_label.setText(self._catalog_view.system_filter())
            return
        if self._active_tab == 2:
            self._installed_view.cycle_system(delta, self._installed_systems)
            self._system_label.setText(self._installed_view.system_filter())
            return
        if not self._systems:
            return
        try:
            idx = self._systems.index(self._system_filter)
        except ValueError:
            idx = 0
        idx = (idx + delta) % len(self._systems)
        self._system_filter = self._systems[idx]
        self._system_label.setText(self._system_filter)
        self._apply_filters()

    def _cycle_status(self, delta: int):
        # Status filter only applies to the My Games tab — catalog /
        # installed rows don't carry a sync status.
        if self._active_tab != 0:
            return
        try:
            idx = STATUS_FILTER_CYCLE.index(self._status_filter)
        except ValueError:
            idx = 0
        idx = (idx + delta) % len(STATUS_FILTER_CYCLE)
        self._status_filter = STATUS_FILTER_CYCLE[idx]
        self._status_filter_label.setText(self._status_filter)
        self._apply_filters()

    # ──────────────────────────────────────────────────────────────
    # Search
    # ──────────────────────────────────────────────────────────────

    def _toggle_search(self):
        if self._search_visible:
            self._hide_search()
        else:
            self._search_visible = True
            self._search_bar.show()
            self._search_edit.setFocus()

    def _hide_search(self):
        self._search_visible = False
        self._search_bar.hide()
        self._search_text = ""
        self._search_edit.clear()
        if self._active_tab == 1:
            self._catalog_view.set_search_text("")
        elif self._active_tab == 2:
            self._installed_view.set_search_text("")
        else:
            self._apply_filters()

    def _on_search_changed(self, text: str):
        self._search_text = text
        if self._active_tab == 1:
            self._catalog_view.set_search_text(text)
        elif self._active_tab == 2:
            self._installed_view.set_search_text(text)
        else:
            self._apply_filters()

    # ──────────────────────────────────────────────────────────────
    # Tab switching
    # ──────────────────────────────────────────────────────────────

    TAB_COUNT = 3

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
        if idx == 1 and not self._catalog_view.is_loaded and not self._catalog_view.is_loading:
            self._fetch_catalog()
        if idx == 2 and not self._installed_view.is_loaded and not self._installed_view.is_loading:
            self._fetch_installed()

    def _cycle_tab(self, delta: int) -> None:
        self._set_active_tab((self._active_tab + delta) % self.TAB_COUNT)

    def _refresh_tab_ui(self) -> None:
        for i, btn in enumerate(self._tab_buttons):
            btn.setChecked(i == self._active_tab)
            btn.setStyleSheet(self._tab_button_style(i == self._active_tab))

        if self._active_tab == 1:
            self._controls.set_mode(ControlsBar.MODE_CATALOG)
            # Catalog has no "status filter", but keep the label row wired
            # so the UI doesn't jump around when switching tabs.
            self._status_filter_label.setText("—")
            current = self._catalog_view.system_filter()
            self._system_label.setText(current)
            self._count_label.setText(
                f"{self._catalog_view.visible_count()} ROMs"
                if self._catalog_view.is_loaded
                else "Loading…"
            )
            self._search_edit.setPlaceholderText(
                "Search ROMs (name, system, filename)…"
            )
        elif self._active_tab == 2:
            self._controls.set_mode(ControlsBar.MODE_INSTALLED)
            self._status_filter_label.setText("—")
            self._system_label.setText(self._installed_view.system_filter())
            self._count_label.setText(
                f"{self._installed_view.visible_count()} ROMs"
                if self._installed_view.is_loaded
                else "Scanning…"
            )
            self._search_edit.setPlaceholderText(
                "Search installed ROMs (name, system, filename)…"
            )
        else:
            self._controls.set_mode(ControlsBar.MODE_SAVES)
            self._system_label.setText(self._system_filter)
            self._status_filter_label.setText(self._status_filter)
            self._count_label.setText(f"{len(self._filtered_entries)} games")
            self._search_edit.setPlaceholderText("Search by name or title ID…")

    # ──────────────────────────────────────────────────────────────
    # Catalog wiring
    # ──────────────────────────────────────────────────────────────

    def _fetch_catalog(self) -> None:
        self._catalog_view.mark_loading(True)
        if self._active_tab == 1:
            self._count_label.setText("Loading…")
        self._catalog_thread = QThread()
        self._catalog_worker = CatalogWorker(self._client)
        self._catalog_worker.moveToThread(self._catalog_thread)
        self._catalog_thread.started.connect(self._catalog_worker.run)
        self._catalog_worker.finished.connect(self._on_catalog_loaded)
        self._catalog_worker.finished.connect(self._catalog_thread.quit)
        self._catalog_thread.finished.connect(self._catalog_worker.deleteLater)
        self._catalog_thread.finished.connect(self._catalog_thread.deleteLater)
        self._catalog_thread.start()

    def _on_catalog_loaded(self, roms: list, error_detail: str) -> None:
        self._catalog_view.mark_loading(False)
        if error_detail and not roms:
            ResultDialog(
                False,
                f"Failed to load ROM catalog.\n\n{error_detail}",
                parent=self,
            ).exec()
        self._catalog_view.set_catalog(roms)
        if self._active_tab == 1:
            self._refresh_tab_ui()

    def _on_catalog_systems(self, systems: list) -> None:
        self._catalog_systems = [CatalogView.ALL_SYSTEMS] + list(systems)

    def _on_catalog_status_changed(self, text: str) -> None:
        if self._active_tab == 1:
            self._count_label.setText(text or "0 ROMs")

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
        target_path = target_dir / target_filename

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

        progress_dlg = DownloadProgressDialog(
            client=self._client,
            rom_id=rom_id,
            target_path=target_path,
            extract_format=extract_format,
            display_name=display,
            parent=self,
        )
        progress_dlg.exec()
        ok = progress_dlg.success
        if ok:
            msg_done = f"ROM '{display}' downloaded to {target_path}."
        else:
            detail = progress_dlg.error_detail or getattr(
                self._client, "last_download_error", ""
            ) or ""
            msg_done = f"Download failed for '{display}'."
            if detail:
                msg_done += f"\n\n{detail}"
        ResultDialog(ok, msg_done, parent=self).exec()

        if ok:
            # Mirror the detail-dialog flow so a freshly downloaded ROM
            # shows up in the scanner list (and flips its save status).
            self._start_scan()
            # And invalidate the Installed tab cache — the freshly
            # downloaded ROM belongs in there too.
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
        if self._active_tab == 2:
            self._count_label.setText("Scanning…")
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
        if self._active_tab == 2:
            self._refresh_tab_ui()

    def _on_installed_systems(self, systems: list) -> None:
        self._installed_systems = [InstalledView.ALL_SYSTEMS] + list(systems)

    def _on_installed_status_changed(self, text: str) -> None:
        if self._active_tab == 2:
            self._count_label.setText(text or "0 ROMs")

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
        if entry.save_path and entry.save_path.exists():
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
        """Smart sync: upload if LOCAL_NEWER/LOCAL_ONLY, download if SERVER_NEWER/SERVER_ONLY."""
        entry = self._list_view.selected_entry()
        if not entry:
            return
        if entry.status in (SyncStatus.LOCAL_NEWER, SyncStatus.LOCAL_ONLY):
            self._action_upload()
        elif entry.status in (SyncStatus.SERVER_NEWER, SyncStatus.SERVER_ONLY):
            self._action_download()
        elif entry.status == SyncStatus.CONFLICT:
            # For conflicts, show detail dialog which has both upload/download options
            self._action_detail()
        else:
            # Open detail dialog for synced / unknown / no-save
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
        )
        dlg.exec()
        # A freshly downloaded ROM doesn't show up in the current scan result,
        # so fall through to a full rescan instead of just reapplying filters.
        if getattr(dlg, "rom_downloaded", False):
            self._start_scan()
        else:
            self._apply_filters()

    def _action_refresh(self):
        self._start_scan()

    def _action_y(self):
        """Y button — tab-specific.

        Saves tab rescans the local emulator folders (server + local
        state can drift between launches).  Catalog and Installed tabs
        pop up the search field since their data is already loaded.
        """
        if self._active_tab in (1, 2):
            if not self._search_visible:
                self._toggle_search()
            self._search_edit.setFocus()
        else:
            self._action_refresh()

    def _action_primary(self):
        """Route A/Enter to the right action for the active tab."""
        if self._active_tab == 1:
            self._download_catalog_rom(self._catalog_view.selected_rom())
        elif self._active_tab == 2:
            self._delete_installed_rom(self._installed_view.selected_rom())
        else:
            self._action_detail()

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
            self._start_scan()

    def _confirm_close(self):
        dlg = ConfirmDialog(
            title="Exit SaveSync",
            message="Close the SaveSync app?",
            confirm_label="Exit",
            confirm_color=theme.STATUS_CONFLICT,
            parent=self,
        )
        if dlg.exec() == dlg.DialogCode.Accepted:
            self.close()

    # ──────────────────────────────────────────────────────────────
    # Keyboard input (also catches gamepad-via-keyboard mapping)
    # ──────────────────────────────────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent):
        if self._search_visible and event.key() == Qt.Key.Key_Escape:
            self._hide_search()
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
        elif key in (Qt.Key.Key_PageUp,):
            self._active_view_page(-1)
        elif key in (Qt.Key.Key_PageDown,):
            self._active_view_page(1)
        elif key == Qt.Key.Key_Left:
            self._cycle_system(-1)
        elif key == Qt.Key.Key_Right:
            self._cycle_system(1)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_A, Qt.Key.Key_Space):
            self._action_primary()
        elif key == Qt.Key.Key_B:
            self._confirm_close()
        elif key == Qt.Key.Key_X:
            if self._active_tab == 0:
                self._action_sync()
        elif key == Qt.Key.Key_Y:
            self._action_y()
        elif key == Qt.Key.Key_Tab:
            self._cycle_tab(1)
        elif key == Qt.Key.Key_Backtab:
            self._cycle_tab(-1)
        elif key == Qt.Key.Key_F1 or key == Qt.Key.Key_BracketLeft:
            self._cycle_system(-1)
        elif key == Qt.Key.Key_F2 or key == Qt.Key.Key_BracketRight:
            self._cycle_system(1)
        elif key == Qt.Key.Key_F3:
            self._cycle_status(-1)
        elif key == Qt.Key.Key_F4:
            self._cycle_status(1)
        elif key == Qt.Key.Key_F5:
            self._cycle_tab(-1)
        elif key == Qt.Key.Key_F6:
            self._cycle_tab(1)
        elif key == Qt.Key.Key_Escape:
            self._toggle_search()
        elif key == Qt.Key.Key_F10 or key == Qt.Key.Key_Menu:
            self._open_settings()
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
        view = self._current_list_view()
        view.move_selection(delta)

    def _active_view_page(self, direction: int) -> None:
        view = self._current_list_view()
        if direction > 0:
            view.page_down()
        else:
            view.page_up()

    def _current_list_view(self):
        if self._active_tab == 1:
            return self._catalog_view
        if self._active_tab == 2:
            return self._installed_view
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

        if btn_pressed(0):
            self._action_primary()  # A — info on saves tab / download on catalog
        if btn_pressed(1):
            self._confirm_close()  # B — close app
        if btn_pressed(2):
            # X — Sync is save-only.  On the catalog tab X is a no-op so
            # the user doesn't accidentally kick a sync when they meant
            # to download.
            if self._active_tab == 0:
                self._action_sync()
        if btn_pressed(3):
            self._action_y()  # Y — saves rescan / catalog search
        if btn_pressed(4):
            if dialog_target is None:
                self._cycle_tab(-1)  # L1 — previous tab
        if btn_pressed(5):
            if dialog_target is None:
                self._cycle_tab(1)  # R1 — next tab
        if btn_pressed(6):
            if dialog_target is None:
                self._toggle_search()  # Select/View
        if btn_pressed(7):
            if dialog_target is None:
                self._open_settings()  # Start/Menu

        # ── D-pad (HAT) ───────────────────────────────────────────
        # ── Left stick Y axis ─────────────────────────────────────
        try:
            axis_y = self._joystick.get_axis(1)  # +1 = down
        except Exception:
            axis_y = 0.0

        # ── L2 / R2 for status filter ─────────────────────────────
        if btn_pressed(8):
            pass  # Steam button — ignore
        # L2/R2 pressed detection via axis crossing threshold.  Both
        # triggers drive page-wise list scrolling so navigating the
        # catalog (and large synced-save lists) doesn't require holding
        # the d-pad for minutes.
        l2_prev = self._btn_state.get("l2", False)
        r2_prev = self._btn_state.get("r2", False)
        l2_cur = l2 > 0.5
        r2_cur = r2 > 0.5
        self._btn_state["l2"] = l2_cur
        self._btn_state["r2"] = r2_cur
        if dialog_target is None and l2_cur and not l2_prev:
            self._active_view_page(-1)
        if dialog_target is None and r2_cur and not r2_prev:
            self._active_view_page(1)

        # ── Navigation with repeat ────────────────────────────────
        DEADZONE = 0.4
        REPEAT_DELAY = 0.15

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

        if nav_y != 0 or nav_x != 0:
            if now - self._axis_nav_time >= REPEAT_DELAY:
                self._axis_nav_time = now
                if nav_y != 0:
                    self._active_view_move(nav_y)
                if nav_x != 0:
                    self._cycle_system(nav_x)
        elif nav_y == 0 and nav_x == 0:
            self._axis_nav_time = 0.0  # reset so next press fires immediately


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
