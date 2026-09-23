"""RetroAchievements tab — which ROM hacks / translations RA recognises."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QThread, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QGuiApplication
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import retroachievements as ra
from config import SYSTEM_CHOICES, get_retroachievements_credentials
from shared.ra_hash import ra_hash_supported

_STATUS_COLORS = {
    ra.STATUS_SUPPORTED: QColor(198, 239, 206),
    ra.STATUS_REGISTERED: QColor(221, 235, 247),
    ra.STATUS_NOT_FOUND: QColor(255, 199, 206),
    ra.STATUS_UNSUPPORTED: QColor(255, 235, 156),
    ra.STATUS_NO_SYSTEM: QColor(255, 235, 156),
    ra.STATUS_ERROR: QColor(255, 199, 206),
}

_COLUMNS = ("File", "System", "Kind", "RetroAchievements", "Game", "Achievements", "RA ID", "Hash", "Detail")
(
    _COL_FILE, _COL_SYSTEM, _COL_KIND, _COL_STATUS, _COL_GAME,
    _COL_ACHIEVEMENTS, _COL_ID, _COL_HASH, _COL_DETAIL,
) = range(9)


class RaScanWorker(QThread):
    finished = pyqtSignal(list)
    progress = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, folder: Path, system: str | None, only_hacks: bool, force_refresh: bool):
        super().__init__()
        self.folder = folder
        self.system = system
        self.only_hacks = only_hacks
        self.force_refresh = force_refresh
        self.username, self.api_key = get_retroachievements_credentials()
        self._stop = False
        self._libraries: dict[int, ra.RaLibrary] = {}

    def stop(self):
        self._stop = True

    def _library(self, console_id: int) -> ra.RaLibrary:
        lib = self._libraries.get(console_id)
        if lib is None:
            self.progress.emit(f"Fetching RetroAchievements hash library for console {console_id}…")
            lib = ra.fetch_library(
                console_id,
                force_refresh=self.force_refresh,
                api_key=self.api_key,
                username=self.username,
            )
            self._libraries[console_id] = lib
            # Only hit the network once per scan even when forced.
            self.force_refresh = False
        return lib

    def run(self):
        try:
            entries = ra.scan_folder(
                self.folder,
                self._library,
                system=self.system,
                only_hacks=self.only_hacks,
                progress=self.progress.emit,
                should_stop=lambda: self._stop,
            )
            self.finished.emit(entries)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user
            self.error.emit(str(exc))


class RetroAchievementsTab(QWidget):
    def __init__(self):
        super().__init__()
        self._entries: list[ra.RaScanEntry] = []
        self._worker: RaScanWorker | None = None
        self._init_ui()

    # ------------------------------------------------------------------ UI

    def _init_ui(self):
        layout = QVBoxLayout(self)

        intro = QLabel(
            "Hashes ROMs the way RetroAchievements does and checks its public hash "
            "library. Meant for hacks and fan translations — a hack only earns "
            "achievements when RA has registered that exact patched ROM. "
            "Add a web API key under Tools → Config to also see achievement counts."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        grid = QGridLayout()
        grid.addWidget(QLabel("ROM Folder:"), 0, 0)
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText("Folder of ROMs, or a roms root with per-system subfolders…")
        grid.addWidget(self.folder_edit, 0, 1)
        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(self._browse_folder)
        grid.addWidget(browse_btn, 0, 2)

        grid.addWidget(QLabel("System:"), 1, 0)
        self.system_combo = QComboBox()
        self.system_combo.addItem("Auto (from folder names)", "")
        for code in SYSTEM_CHOICES:
            label = code if ra_hash_supported(code) else f"{code} (not hashable)"
            self.system_combo.addItem(label, code)
        self.system_combo.setToolTip(
            "Auto reads each file's system from its parent folder (snes, genesis, …). "
            "Pick a system when the folder holds one system with no such subfolders."
        )
        grid.addWidget(self.system_combo, 1, 1)

        self.only_hacks_check = QCheckBox("Only hacks, translations and ROMs missing from the No-Intro DAT")
        self.only_hacks_check.setChecked(True)
        self.only_hacks_check.setToolTip(
            "Skips retail dumps (CRC found in the system's No-Intro DAT). "
            "Uncheck to check every ROM in the folder."
        )
        grid.addWidget(self.only_hacks_check, 2, 1, 1, 2)
        layout.addLayout(grid)

        buttons = QHBoxLayout()
        self.scan_btn = QPushButton("Scan")
        self.scan_btn.clicked.connect(self._start_scan)
        buttons.addWidget(self.scan_btn)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop_scan)
        buttons.addWidget(self.stop_btn)
        self.refresh_btn = QPushButton("Refresh RA data")
        self.refresh_btn.setToolTip(
            "Discard the cached hash library (kept for 24 h) and download it again on the next scan."
        )
        self.refresh_btn.clicked.connect(self._refresh_cache)
        buttons.addWidget(self.refresh_btn)
        self.export_btn = QPushButton("Export CSV…")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._export_csv)
        buttons.addWidget(self.export_btn)
        buttons.addStretch()
        self.filter_combo = QComboBox()
        self.filter_combo.addItem("Show all", "")
        for status, label in ra.STATUS_LABELS.items():
            self.filter_combo.addItem(label, status)
        self.filter_combo.currentIndexChanged.connect(self._populate_table)
        buttons.addWidget(QLabel("Filter:"))
        buttons.addWidget(self.filter_combo)
        layout.addLayout(buttons)

        self.table = QTableWidget(0, len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSortingEnabled(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(_COL_FILE, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(_COL_GAME, QHeaderView.ResizeMode.Stretch)
        for col in (_COL_SYSTEM, _COL_KIND, _COL_STATUS, _COL_ACHIEVEMENTS, _COL_ID):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.cellDoubleClicked.connect(self._open_game_page)
        layout.addWidget(self.table)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

    # ------------------------------------------------------------- actions

    def _browse_folder(self):
        start = self.folder_edit.text() or str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "Select ROM folder", start)
        if folder:
            self.folder_edit.setText(folder)

    def _start_scan(self, force_refresh: bool = False):
        folder = Path(self.folder_edit.text().strip())
        if not folder.is_dir():
            QMessageBox.warning(self, "RetroAchievements", "Select an existing ROM folder first.")
            return
        system = self.system_combo.currentData() or None
        self._entries = []
        self.table.setRowCount(0)
        self.export_btn.setEnabled(False)
        self.scan_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.status_label.setText("Scanning…")

        self._worker = RaScanWorker(folder, system, self.only_hacks_check.isChecked(), force_refresh)
        self._worker.progress.connect(self.status_label.setText)
        self._worker.finished.connect(self._on_scan_done)
        self._worker.error.connect(self._on_scan_error)
        self._worker.start()

    def _stop_scan(self):
        if self._worker:
            self._worker.stop()
            self.status_label.setText("Stopping…")

    def _refresh_cache(self):
        removed = ra.clear_cache()
        self.status_label.setText(
            f"Cleared {removed} cached RetroAchievements librar{'y' if removed == 1 else 'ies'}; "
            "the next scan downloads fresh data."
        )

    def _on_scan_done(self, entries: list):
        self._entries = entries
        self._worker = None
        self.scan_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.export_btn.setEnabled(bool(entries))
        self._populate_table()
        self.status_label.setText(ra.summarize(entries))

    def _on_scan_error(self, message: str):
        self._worker = None
        self.scan_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.status_label.setText("Scan failed.")
        QMessageBox.critical(self, "RetroAchievements", message)

    def _export_csv(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export results", "retroachievements.csv", "CSV files (*.csv)"
        )
        if not path:
            return
        try:
            ra.write_csv(self._visible_entries(), Path(path))
        except OSError as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        self.status_label.setText(f"Exported to {path}")

    # --------------------------------------------------------------- table

    def _visible_entries(self) -> list[ra.RaScanEntry]:
        wanted = self.filter_combo.currentData()
        if not wanted:
            return list(self._entries)
        return [e for e in self._entries if e.status == wanted]

    def _populate_table(self):
        entries = self._visible_entries()
        positions = {id(e): i for i, e in enumerate(self._entries)}
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            values = [
                entry.label,
                entry.system,
                ra.KIND_LABELS.get(entry.kind, entry.kind),
                ra.STATUS_LABELS.get(entry.status, entry.status),
                entry.title or "",
                "" if entry.achievements is None else str(entry.achievements),
                str(entry.game_id) if entry.game_id else "",
                entry.md5 or "",
                entry.detail,
            ]
            color = _STATUS_COLORS.get(entry.status)
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, positions[id(entry)])
                if col == _COL_FILE:
                    item.setToolTip(str(entry.path))
                if color is not None and col == _COL_STATUS:
                    item.setBackground(color)
                self.table.setItem(row, col, item)
        self.table.setSortingEnabled(True)

    def _entry_at(self, row: int) -> ra.RaScanEntry | None:
        item = self.table.item(row, _COL_FILE)
        if item is None:
            return None
        index = item.data(Qt.ItemDataRole.UserRole)
        if index is None or index >= len(self._entries):
            return None
        return self._entries[index]

    def _open_game_page(self, row: int, _col: int):
        entry = self._entry_at(row)
        if entry and entry.game_url:
            QDesktopServices.openUrl(QUrl(entry.game_url))

    def _show_context_menu(self, pos):
        row = self.table.rowAt(pos.y())
        entry = self._entry_at(row) if row >= 0 else None
        if entry is None:
            return
        menu = QMenu(self)
        if entry.game_url:
            menu.addAction("Open on RetroAchievements", lambda: QDesktopServices.openUrl(QUrl(entry.game_url)))
        if entry.md5:
            menu.addAction("Copy hash", lambda: QGuiApplication.clipboard().setText(entry.md5))
        menu.addAction("Copy path", lambda: QGuiApplication.clipboard().setText(str(entry.path)))
        menu.exec(self.table.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------ ui state

    def save_ui_state(self) -> dict:
        return {
            "folder": self.folder_edit.text(),
            "system": self.system_combo.currentData() or "",
            "only_hacks": self.only_hacks_check.isChecked(),
        }

    def load_ui_state(self, state: dict):
        self.folder_edit.setText(state.get("folder", ""))
        index = self.system_combo.findData(state.get("system", ""))
        self.system_combo.setCurrentIndex(max(index, 0))
        self.only_hacks_check.setChecked(bool(state.get("only_hacks", True)))
