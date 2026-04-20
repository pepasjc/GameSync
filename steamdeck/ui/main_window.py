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

from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
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
from scanner import scan_all, rpcs3, dolphin
from sync_client import SyncClient, _find_server_save
from config import load_config, save_config
from . import theme
from .game_list import GameListView
from .controls_bar import ControlsBar
from .settings_dialog import SettingsDialog
from .detail_dialog import DetailDialog
from .confirm_dialog import ConfirmDialog, ResultDialog

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

    def __init__(self, emulation_path: str, rom_scan_dir: str = ""):
        super().__init__()
        self._path = emulation_path
        self._rom_scan_dir = rom_scan_dir

    def run(self):
        results = scan_all(
            self._path,
            rom_scan_dir=self._rom_scan_dir,
            progress_cb=self.progress.emit,
        )
        self.finished.emit(results)


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
        For slug-based entries with a known ROM filename, ask the server to
        resolve translated ROM names to the canonical server title ID.

        This keeps translated ROM dumps aligned with the same save slot as the
        original DAT title without forcing users to rename files on disk.
        """
        skip_systems = {"GC", "SAT", "PS3", "PSP", "3DS", "WII", "NSW", "?"}
        needs_lookup: list[GameEntry] = []
        rom_entries: list[dict[str, str]] = []
        for entry in self._entries:
            system = entry.system.upper().strip()
            lookup_filename = entry.rom_filename or (
                entry.rom_path.name if entry.rom_path else None
            )
            if (
                lookup_filename
                and system not in skip_systems
                and entry.title_id.startswith(f"{system}_")
            ):
                needs_lookup.append(entry)
                rom_entries.append({"system": system, "filename": lookup_filename})

        if not rom_entries:
            return

        # Batch lookup via server
        resolved = self._client.normalize_batch(rom_entries)
        if not resolved:
            return

        # Apply resolved serial title_ids
        for entry in needs_lookup:
            system = entry.system.upper().strip()
            lookup_filename = entry.rom_filename or (
                entry.rom_path.name if entry.rom_path else None
            )
            if not lookup_filename:
                continue
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
        # ── Enrich slug-based PS1/PS2 title_ids with server serial lookup ──
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

        self.finished.emit(updated)


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
        )

        self._all_entries: list[GameEntry] = []
        self._filtered_entries: list[GameEntry] = []
        self._system_filter = ALL_SYSTEMS
        self._status_filter = ALL_STATUSES
        self._search_visible = False
        self._search_text = ""
        self._systems: list[str] = [ALL_SYSTEMS]

        # Build UI
        central = QWidget()
        central.setObjectName("centralWidget")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(0)
        root.setContentsMargins(0, 0, 0, 0)

        root.addWidget(self._build_topbar())
        root.addWidget(self._build_filterbar())
        self._search_bar = self._build_searchbar()
        root.addWidget(self._search_bar)
        self._search_bar.hide()

        self._list_view = GameListView()
        root.addWidget(self._list_view, 1)

        self._controls = ControlsBar()
        root.addWidget(self._controls)

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
        self._apply_filters()

    def _on_search_changed(self, text: str):
        self._search_text = text
        self._apply_filters()

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

    def _open_settings(self):
        dlg = SettingsDialog(self._config, self)
        if dlg.exec():
            self._config.update(dlg.get_config())
            save_config(self._config)
            self._client = SyncClient(
                self._config["host"],
                self._config["port"],
                self._config["api_key"],
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
            self._list_view.move_selection(-1)
        elif key in (Qt.Key.Key_Down, Qt.Key.Key_S):
            self._list_view.move_selection(1)
        elif key in (Qt.Key.Key_PageUp,):
            self._list_view.page_up()
        elif key in (Qt.Key.Key_PageDown,):
            self._list_view.page_down()
        elif key == Qt.Key.Key_Left:
            self._cycle_system(-1)
        elif key == Qt.Key.Key_Right:
            self._cycle_system(1)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_A, Qt.Key.Key_Space):
            self._action_detail()
        elif key == Qt.Key.Key_B:
            self._confirm_close()
        elif key == Qt.Key.Key_X:
            self._action_sync()
        elif key == Qt.Key.Key_Y:
            self._action_refresh()
        elif key == Qt.Key.Key_F1 or key == Qt.Key.Key_BracketLeft:
            self._cycle_system(-1)
        elif key == Qt.Key.Key_F2 or key == Qt.Key.Key_BracketRight:
            self._cycle_system(1)
        elif key == Qt.Key.Key_F3:
            self._cycle_status(-1)
        elif key == Qt.Key.Key_F4:
            self._cycle_status(1)
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
            self._list_view.move_selection(-1)
        if Qt.Key.Key_Down in self._held_keys or Qt.Key.Key_S in self._held_keys:
            self._list_view.move_selection(1)

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
            self._action_detail()  # A — open save info
        if btn_pressed(1):
            self._confirm_close()  # B — close app
        if btn_pressed(2):
            self._action_sync()  # X
        if btn_pressed(3):
            self._action_refresh()  # Y
        if btn_pressed(4):
            if dialog_target is None:
                self._cycle_system(-1)  # L1
        if btn_pressed(5):
            if dialog_target is None:
                self._cycle_system(1)  # R1
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
        # L2/R2 pressed detection via axis crossing threshold
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
                    self._list_view.move_selection(nav_y)
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
