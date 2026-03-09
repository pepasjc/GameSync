import os
import sys
import re
import json
import requests
from datetime import datetime
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QPushButton,
    QLabel,
    QLineEdit,
    QComboBox,
    QMessageBox,
    QDialog,
    QDialogButtonBox,
    QListWidget,
    QStatusBar,
    QMenu,
    QFormLayout,
    QTabWidget,
    QFileDialog,
    QProgressDialog,
    QCheckBox,
    QScrollArea,
    QGridLayout,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QAction, QColor


CONFIG_FILE = Path(__file__).parent / "config.json"

ALL_CONSOLE_TYPES = [
    "All", "3DS", "NDS", "PSP", "PS3", "VITA", "PSX",
    "GBA", "SNES", "NES", "MD", "N64", "GB", "GBC",
    "GG", "SMS", "PCE", "PS1", "PS2", "NGP", "DC", "GC",
    "ATARI2600", "ATARI7800", "LYNX", "NEOGEO", "32X", "SEGACD",
    "WSWAN", "WSWANC", "ARCADE", "MAME",
]

DEVICE_TYPES = ["Generic", "RetroArch", "MiSTer", "Analogue Pocket", "Pocket (openFPGA)", "Everdrive", "EmuDeck"]

SYSTEM_CHOICES = [
    "GBA", "SNES", "NES", "MD", "N64", "GB", "GBC", "GG", "NGP",
    "PCE", "PS1", "PS2", "PSP", "PS3", "SMS", "ATARI2600", "ATARI7800", "LYNX", "NEOGEO",
    "32X", "SAT", "SEGACD", "TG16", "WSWAN", "WSWANC", "DC", "NDS", "GC",
    "ARCADE", "MAME",
]

STATUS_COLORS = {
    "up_to_date":   QColor(0, 200, 0),
    "local_newer":  QColor(0, 160, 255),
    "server_newer": QColor(255, 200, 0),
    "not_on_server": QColor(180, 180, 180),
    "server_only":  QColor(180, 100, 255),
    "conflict":     QColor(220, 60, 60),
    "error":        QColor(200, 0, 200),
    "unknown":      QColor(180, 180, 180),
}

STATUS_LABELS = {
    "up_to_date":    "Up to date",
    "local_newer":   "Local newer",
    "server_newer":  "Server newer",
    "not_on_server": "Not on server",
    "server_only":   "Server only",
    "conflict":      "Conflict",
    "error":         "Error",
    "unknown":       "Unknown",
}


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "host": os.environ.get("SYNC_HOST", "localhost"),
        "port": int(os.environ.get("SYNC_PORT", "8000")),
        "api_key": os.environ.get("SYNC_API_KEY", "anything"),
        "profiles": [],
    }


def save_config(config: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")


def get_api_headers() -> dict:
    config = load_config()
    return {"X-API-Key": config.get("api_key", "anything")}


def get_base_url() -> str:
    config = load_config()
    host = config.get("host", "localhost")
    port = config.get("port", "8000")
    return f"http://{host}:{port}"


# ---------------------------------------------------------------------------
# Console type detection
# ---------------------------------------------------------------------------

_HEX_TITLE_RE = re.compile(r"^[0-9A-Fa-f]{16}$")
_PS_PREFIX_RE = re.compile(r"^[A-Z]{4}\d{5}")
_EMULATOR_RE = re.compile(r"^([A-Z0-9]{2,8})_[a-z0-9]")


_PS3_PREFIXES = {"BCAS", "BCES", "BCJS", "BCKS", "BCUS",
                 "BLAS", "BLES", "BLJM", "BLJS", "BLKS", "BLUS",
                 "NPHA", "NPEA", "NPJA", "NPUA", "NPEB", "NPJB", "NPUB"}


def detect_console_type(title_id: str) -> str:
    title_id = title_id.strip()
    m = _EMULATOR_RE.match(title_id)
    if m:
        return m.group(1)
    uid = title_id.upper()
    if _HEX_TITLE_RE.match(uid):
        return "3DS"
    if _PS_PREFIX_RE.match(uid):
        base = uid[:9]
        if base.startswith("PCS"):
            return "VITA"
        if uid[:4] in _PS3_PREFIXES:
            return "PS3"
        return "PSP"
    return "NDS"


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def fetch_all_saves() -> list[dict]:
    resp = requests.get(
        f"{get_base_url()}/api/v1/titles", headers=get_api_headers(), timeout=30
    )
    resp.raise_for_status()
    return resp.json().get("titles", [])


def fetch_history(title_id: str, console_id: str = "") -> list[dict]:
    params = {"console_id": console_id} if console_id else {}
    resp = requests.get(
        f"{get_base_url()}/api/v1/saves/{title_id}/history",
        headers=get_api_headers(),
        params=params,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("versions", [])


def delete_save(title_id: str, console_id: str = "") -> None:
    params = {"console_id": console_id} if console_id else {}
    resp = requests.delete(
        f"{get_base_url()}/api/v1/saves/{title_id}",
        headers=get_api_headers(),
        params=params,
        timeout=30,
    )
    resp.raise_for_status()


def restore_history(title_id: str, timestamp: int, console_id: str = "") -> None:
    params = {"console_id": console_id} if console_id else {}
    resp = requests.get(
        f"{get_base_url()}/api/v1/saves/{title_id}/history/{timestamp}",
        headers=get_api_headers(),
        params=params,
        timeout=30,
    )
    resp.raise_for_status()

    upload_params = {"force": "true"}
    if console_id:
        upload_params["console_id"] = console_id

    upload_resp = requests.post(
        f"{get_base_url()}/api/v1/saves/{title_id}",
        headers=get_api_headers(),
        params=upload_params,
        data=resp.content,
        timeout=30,
    )
    upload_resp.raise_for_status()


def download_raw_save(title_id: str, dest_path: Path) -> None:
    """Download the raw save bytes to dest_path."""
    resp = requests.get(
        f"{get_base_url()}/api/v1/saves/{title_id}/raw",
        headers=get_api_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_bytes(resp.content)


# ---------------------------------------------------------------------------
# Dialogs
# ---------------------------------------------------------------------------

class ConfigDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Server Configuration")
        self.setMinimumSize(400, 200)
        self._init_ui()
        self._load_config()

    def _init_ui(self):
        layout = QFormLayout(self)

        self.host_edit = QLineEdit()
        self.host_edit.setPlaceholderText("localhost")
        layout.addRow("Server Host:", self.host_edit)

        self.port_edit = QLineEdit()
        self.port_edit.setPlaceholderText("8000")
        layout.addRow("Server Port:", self.port_edit)

        self.api_key_edit = QLineEdit()
        self.api_key_edit.setPlaceholderText("anything")
        layout.addRow("API Key:", self.api_key_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def _load_config(self):
        config = load_config()
        self.host_edit.setText(config.get("host", "localhost"))
        self.port_edit.setText(str(config.get("port", "8000")))
        self.api_key_edit.setText(config.get("api_key", "anything"))

    def _save(self):
        try:
            port = int(self.port_edit.text())
        except ValueError:
            QMessageBox.warning(self, "Invalid Port", "Port must be a number")
            return

        config = load_config()
        config["host"] = self.host_edit.text() or "localhost"
        config["port"] = port
        config["api_key"] = self.api_key_edit.text() or "anything"
        save_config(config)
        self.accept()


class HistoryDialog(QDialog):
    def __init__(self, title_id: str, console_id: str, parent=None):
        super().__init__(parent)
        self.title_id = title_id
        self.console_id = console_id
        self.setWindowTitle(f"History - {title_id}")
        self.setMinimumSize(500, 400)
        self.selected_timestamp = None
        self._init_ui()
        self._load_history()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self.accept)
        layout.addWidget(self.list_widget)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _load_history(self):
        try:
            history = fetch_history(self.title_id, self.console_id)
            for v in history:
                display = v.get("display", "Unknown")
                size = v.get("size", 0)
                files = v.get("file_count", 0)
                self.list_widget.addItem(f"{display} - {size:,} bytes, {files} files")
                self.list_widget.item(self.list_widget.count() - 1).setData(
                    Qt.ItemDataRole.UserRole, v
                )
            if not history:
                self.list_widget.addItem("No history available")
        except Exception as e:
            self.list_widget.addItem(f"Error loading history: {e}")

    def _on_accept(self):
        current = self.list_widget.currentItem()
        if current:
            self.selected_timestamp = current.data(Qt.ItemDataRole.UserRole)
        self.accept()


class ProfileDialog(QDialog):
    """Add / edit a sync profile."""

    def __init__(self, profile: dict | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Sync Profile" if profile else "Add Sync Profile")
        self.setMinimumSize(520, 420)
        self._init_ui()
        if profile:
            self._load(profile)

    def _init_ui(self):
        outer = QVBoxLayout(self)
        layout = QFormLayout()
        outer.addLayout(layout)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("e.g. RetroArch GBA, MiSTer SNES")
        layout.addRow("Profile Name:", self.name_edit)

        self.device_combo = QComboBox()
        self.device_combo.addItems(DEVICE_TYPES)
        self.device_combo.currentTextChanged.connect(self._on_device_changed)
        layout.addRow("Device Type:", self.device_combo)

        path_row = QWidget()
        path_layout = QHBoxLayout(path_row)
        path_layout.setContentsMargins(0, 0, 0, 0)
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("Game / ROM folder path...")
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_path)
        path_layout.addWidget(self.path_edit)
        path_layout.addWidget(browse_btn)
        layout.addRow("Game Folder:", path_row)

        self.separate_save_check = QCheckBox("Saves are in a separate folder")
        self.separate_save_check.setToolTip(
            "Enable when saves are stored in a different folder than the ROMs\n"
            "(e.g. Everdrive SAVE/ subfolder). Leave unchecked when saves and\n"
            "ROMs share the same directory."
        )
        self.separate_save_check.stateChanged.connect(self._on_separate_save_changed)
        layout.addRow("", self.separate_save_check)

        self._save_folder_label = QLabel("Save Folder:")
        self.save_folder_edit = QLineEdit()
        self.save_folder_edit.setPlaceholderText("Separate save folder path...")
        save_folder_row = QWidget()
        save_folder_layout = QHBoxLayout(save_folder_row)
        save_folder_layout.setContentsMargins(0, 0, 0, 0)
        browse_save_btn = QPushButton("Browse...")
        browse_save_btn.clicked.connect(self._browse_save_folder)
        save_folder_layout.addWidget(self.save_folder_edit)
        save_folder_layout.addWidget(browse_save_btn)
        layout.addRow(self._save_folder_label, save_folder_row)
        self._save_folder_row_widget = save_folder_row

        self.system_combo = QComboBox()
        self.system_combo.addItems(SYSTEM_CHOICES)
        layout.addRow("System:", self.system_combo)
        self._system_row_label = layout.labelForField(self.system_combo)

        # Systems filter — which systems to include when syncing
        self._systems_filter_label = QLabel("Systems to sync:")
        systems_widget = QWidget()
        systems_outer = QVBoxLayout(systems_widget)
        systems_outer.setContentsMargins(0, 0, 0, 0)
        systems_outer.setSpacing(2)

        # Select all / none buttons
        sel_row = QHBoxLayout()
        sel_all_btn = QPushButton("All")
        sel_all_btn.setFixedWidth(45)
        sel_none_btn = QPushButton("None")
        sel_none_btn.setFixedWidth(45)
        sel_all_btn.clicked.connect(lambda: self._set_all_systems(True))
        sel_none_btn.clicked.connect(lambda: self._set_all_systems(False))
        sel_row.addWidget(sel_all_btn)
        sel_row.addWidget(sel_none_btn)
        sel_row.addStretch()
        systems_outer.addLayout(sel_row)

        # Scrollable checkbox grid (4 columns)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFixedHeight(130)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        grid_widget = QWidget()
        self._systems_grid = QGridLayout(grid_widget)
        self._systems_grid.setContentsMargins(4, 4, 4, 4)
        self._systems_grid.setHorizontalSpacing(12)
        self._systems_grid.setVerticalSpacing(2)
        self._system_checks: dict[str, QCheckBox] = {}
        cols = 4
        for i, sys_code in enumerate(SYSTEM_CHOICES):
            cb = QCheckBox(sys_code)
            cb.setChecked(True)
            self._system_checks[sys_code] = cb
            self._systems_grid.addWidget(cb, i // cols, i % cols)
        scroll.setWidget(grid_widget)
        systems_outer.addWidget(scroll)
        layout.addRow(self._systems_filter_label, systems_widget)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        self._on_device_changed(self.device_combo.currentText())
        self._on_separate_save_changed()

    def _set_all_systems(self, checked: bool):
        for cb in self._system_checks.values():
            cb.setChecked(checked)

    def _on_separate_save_changed(self):
        visible = self.separate_save_check.isChecked()
        self._save_folder_label.setVisible(visible)
        self._save_folder_row_widget.setVisible(visible)

    def _on_device_changed(self, device_type: str):
        # System dropdown only needed for Generic/Everdrive (single-system flat folders)
        needs_system = device_type in ("Generic", "Everdrive")
        self.system_combo.setVisible(needs_system)
        if self._system_row_label:
            self._system_row_label.setVisible(needs_system)

    def _browse_path(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Game / ROM Folder")
        if folder:
            self.path_edit.setText(folder)

    def _browse_save_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Save Folder")
        if folder:
            self.save_folder_edit.setText(folder)

    def _load(self, profile: dict):
        self.name_edit.setText(profile.get("name", ""))
        idx = self.device_combo.findText(profile.get("device_type", "Generic"))
        if idx >= 0:
            self.device_combo.setCurrentIndex(idx)
        self.path_edit.setText(profile.get("path", ""))
        save_folder = profile.get("save_folder", "")
        if save_folder:
            self.separate_save_check.setChecked(True)
            self.save_folder_edit.setText(save_folder)
        idx = self.system_combo.findText(profile.get("system", "GBA"))
        if idx >= 0:
            self.system_combo.setCurrentIndex(idx)
        # Restore systems filter — empty list means "all"
        systems_filter = profile.get("systems_filter", [])
        if systems_filter:
            self._set_all_systems(False)
            for sys_code in systems_filter:
                if sys_code in self._system_checks:
                    self._system_checks[sys_code].setChecked(True)

    def _accept(self):
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "Validation", "Profile name is required")
            return
        if not self.path_edit.text().strip():
            QMessageBox.warning(self, "Validation", "Game folder path is required")
            return
        if self.separate_save_check.isChecked() and not self.save_folder_edit.text().strip():
            QMessageBox.warning(self, "Validation", "Save folder path is required when 'Saves are in a separate folder' is checked")
            return
        self.accept()

    def get_profile(self) -> dict:
        save_folder = self.save_folder_edit.text().strip() if self.separate_save_check.isChecked() else ""
        checked = [s for s, cb in self._system_checks.items() if cb.isChecked()]
        # Store empty list when all systems are selected (means "no filter")
        systems_filter = [] if len(checked) == len(self._system_checks) else checked
        return {
            "name": self.name_edit.text().strip(),
            "device_type": self.device_combo.currentText(),
            "path": self.path_edit.text().strip(),
            "save_folder": save_folder,
            "system": self.system_combo.currentText(),
            "systems_filter": systems_filter,
        }


# ---------------------------------------------------------------------------
# Background worker for sync scanning
# ---------------------------------------------------------------------------

class ScanWorker(QThread):
    result_ready = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, profiles: list[dict], base_url: str, headers: dict):
        super().__init__()
        self.profiles = profiles
        self.base_url = base_url
        self.headers = headers

    def run(self):
        try:
            from sync_engine import scan_profile, compare_with_server
            all_saves = []
            systems_filter: set[str] = set()
            for profile in self.profiles:
                all_saves.extend(scan_profile(profile))
                pf = set(s.upper() for s in (profile.get("systems_filter") or []))
                systems_filter |= pf
            statuses = compare_with_server(
                all_saves, self.base_url, self.headers,
                systems_filter=systems_filter or None,
            )
            self.result_ready.emit(statuses)
        except Exception as e:
            import traceback
            self.error.emit(traceback.format_exc())


# ---------------------------------------------------------------------------
# Tab 1 — Server Saves
# ---------------------------------------------------------------------------

class ServerSavesTab(QWidget):
    def __init__(self):
        super().__init__()
        self.saves = []
        self._init_ui()
        self.load_saves()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        toolbar = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.load_saves)
        toolbar.addWidget(self.refresh_btn)

        toolbar.addWidget(QLabel("Filter:"))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Search by ID or name...")
        self.filter_edit.textChanged.connect(self._apply_filter)
        toolbar.addWidget(self.filter_edit)

        self.console_filter = QComboBox()
        self.console_filter.addItems(ALL_CONSOLE_TYPES)
        self.console_filter.currentTextChanged.connect(self._apply_filter)
        toolbar.addWidget(QLabel("Console:"))
        toolbar.addWidget(self.console_filter)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(
            ["Console", "Game ID", "Name", "Last Saved", "Size", "Files"]
        )
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.doubleClicked.connect(self._on_double_click)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        layout.addWidget(self.table)

        self.status_label = QLabel("Ready")
        layout.addWidget(self.status_label)

    def load_saves(self):
        self.status_label.setText("Loading saves...")
        try:
            self.saves = fetch_all_saves()
            self._populate_table()
            self.status_label.setText(f"Loaded {len(self.saves)} saves")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load saves: {e}")
            self.status_label.setText("Error loading saves")

    def _populate_table(self):
        self.table.setRowCount(0)
        for save in self.saves:
            title_id = save.get("title_id", "")
            console_type = save.get("console_type", detect_console_type(title_id))
            name = save.get("game_name", title_id)
            last_sync = save.get("last_sync", "")
            try:
                dt = datetime.fromisoformat(last_sync.replace("Z", "+00:00"))
                last_saved = dt.strftime("%Y-%m-%d %H:%M")
            except Exception:
                last_saved = last_sync[:19] if len(last_sync) > 19 else last_sync

            size = save.get("save_size", 0)
            files = save.get("file_count", 0)

            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(console_type))
            self.table.setItem(row, 1, QTableWidgetItem(title_id))
            self.table.setItem(row, 2, QTableWidgetItem(name))
            self.table.setItem(row, 3, QTableWidgetItem(last_saved))
            self.table.setItem(row, 4, QTableWidgetItem(f"{size:,}"))
            self.table.setItem(row, 5, QTableWidgetItem(str(files)))
            self.table.item(row, 0).setData(
                Qt.ItemDataRole.UserRole,
                {"title_id": title_id, "console_id": save.get("console_id", "")},
            )

    def _apply_filter(self):
        filter_text = self.filter_edit.text().lower()
        console_filter = self.console_filter.currentText()
        for row in range(self.table.rowCount()):
            show = True
            if console_filter != "All":
                item = self.table.item(row, 0)
                if item and item.text() != console_filter:
                    show = False
            if filter_text:
                id_item = self.table.item(row, 1)
                name_item = self.table.item(row, 2)
                id_text = id_item.text().lower() if id_item else ""
                name_text = name_item.text().lower() if name_item else ""
                if filter_text not in id_text and filter_text not in name_text:
                    show = False
            self.table.setRowHidden(row, not show)

    def _context_menu(self, pos):
        menu = QMenu(self)
        rows = self._selected_rows()
        if rows:
            menu.addAction("Show History...", self._show_history)
            menu.addAction("Restore from History...", self._restore_history)
            menu.addAction("Download Save...", self._download_save)
            menu.addSeparator()
            menu.addAction(
                "Delete" if len(rows) == 1 else f"Delete {len(rows)} saves",
                self._delete_saves,
            )
        menu.addAction("Refresh", self.load_saves)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _selected_rows(self) -> list[int]:
        return sorted(set(idx.row() for idx in self.table.selectedIndexes()))

    def _row_data(self, row: int) -> dict:
        item = self.table.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else {}

    def _show_history(self):
        rows = self._selected_rows()
        if not rows:
            return
        data = self._row_data(rows[0])
        HistoryDialog(data.get("title_id", ""), data.get("console_id", ""), self).exec()

    def _restore_history(self):
        rows = self._selected_rows()
        if not rows:
            return
        data = self._row_data(rows[0])
        title_id = data.get("title_id", "")
        console_id = data.get("console_id", "")
        try:
            history = fetch_history(title_id, console_id)
            if not history:
                QMessageBox.information(self, "No History", "No history versions available")
                return

            dialog = QDialog(self)
            dialog.setWindowTitle("Restore from History")
            dlayout = QVBoxLayout(dialog)
            list_widget = QListWidget()
            for v in history:
                list_widget.addItem(
                    f"{v.get('display', 'Unknown')} - {v.get('size', 0):,} bytes, "
                    f"{v.get('file_count', 0)} files"
                )
                list_widget.item(list_widget.count() - 1).setData(Qt.ItemDataRole.UserRole, v)
            dlayout.addWidget(list_widget)
            btns = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
            )
            btns.accepted.connect(dialog.accept)
            btns.rejected.connect(dialog.reject)
            dlayout.addWidget(btns)

            if dialog.exec() == QDialog.DialogCode.Accepted:
                idx = list_widget.currentRow()
                if idx >= 0:
                    selected = list_widget.item(idx).data(Qt.ItemDataRole.UserRole)
                    timestamp = selected.get("timestamp")
                    reply = QMessageBox.question(
                        self, "Confirm Restore",
                        f"Restore version from {selected.get('display')}?",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    )
                    if reply == QMessageBox.StandardButton.Yes:
                        restore_history(title_id, timestamp, console_id)
                        QMessageBox.information(self, "Restored", "Save restored from history")
                        self.load_saves()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to restore: {e}")

    def _download_save(self):
        rows = self._selected_rows()
        if not rows:
            return
        data = self._row_data(rows[0])
        title_id = data.get("title_id", "")
        dest = QFileDialog.getSaveFileName(
            self, "Save File As", f"{title_id}.sav", "Save Files (*.sav *.srm);;All Files (*)"
        )[0]
        if not dest:
            return
        try:
            download_raw_save(title_id, Path(dest))
            QMessageBox.information(self, "Downloaded", f"Save written to:\n{dest}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Download failed: {e}")

    def _delete_saves(self):
        rows = self._selected_rows()
        if not rows:
            return
        count = len(rows)
        reply = QMessageBox.question(
            self, "Confirm Delete",
            f"Delete {count} save{'s' if count > 1 else ''}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        errors = []
        for row in rows:
            data = self._row_data(row)
            try:
                delete_save(data.get("title_id", ""), data.get("console_id", ""))
            except Exception as e:
                errors.append(str(e))
        if errors:
            QMessageBox.warning(self, "Partial Error", "\n".join(errors))
        self.load_saves()

    def _on_double_click(self, index):
        row = index.row()
        title_id = self.table.item(row, 1).text()
        name = self.table.item(row, 2).text()
        console = self.table.item(row, 0).text()
        size = self.table.item(row, 4).text()
        files = self.table.item(row, 5).text()
        last_saved = self.table.item(row, 3).text()
        QMessageBox.information(
            self, "Save Details",
            f"Game ID: {title_id}\nName: {name}\nConsole: {console}\n"
            f"Size: {size} bytes\nFiles: {files}\nLast Saved: {last_saved}",
        )

    def save_ui_state(self) -> dict:
        return {
            "console_filter": self.console_filter.currentText(),
            "search": self.filter_edit.text(),
        }

    def load_ui_state(self, state: dict):
        if "console_filter" in state:
            idx = self.console_filter.findText(state["console_filter"])
            if idx >= 0:
                self.console_filter.setCurrentIndex(idx)
        if "search" in state:
            self.filter_edit.setText(state["search"])


# ---------------------------------------------------------------------------
# Tab 2 — Sync Profiles
# ---------------------------------------------------------------------------

class ProfilesTab(QWidget):
    def __init__(self):
        super().__init__()
        self._init_ui()
        self._load_profiles()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("Add Profile")
        add_btn.clicked.connect(self._add_profile)
        edit_btn = QPushButton("Edit Profile")
        edit_btn.clicked.connect(self._edit_profile)
        del_btn = QPushButton("Delete Profile")
        del_btn.clicked.connect(self._delete_profile)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(edit_btn)
        btn_row.addWidget(del_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Name", "Device Type", "Game Folder", "Save Folder", "System"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)

    def _load_profiles(self):
        config = load_config()
        profiles = config.get("profiles", [])
        self.table.setRowCount(0)
        for p in profiles:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(p.get("name", "")))
            self.table.setItem(row, 1, QTableWidgetItem(p.get("device_type", "")))
            self.table.setItem(row, 2, QTableWidgetItem(p.get("path", "")))
            save_folder = p.get("save_folder", "")
            self.table.setItem(row, 3, QTableWidgetItem(save_folder or "(same as game folder)"))
            self.table.setItem(row, 4, QTableWidgetItem(p.get("system", "")))
            self.table.item(row, 0).setData(Qt.ItemDataRole.UserRole, p)

    def _save_profiles(self):
        profiles = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item:
                profiles.append(item.data(Qt.ItemDataRole.UserRole))
        config = load_config()
        config["profiles"] = profiles
        save_config(config)

    def get_profiles(self) -> list[dict]:
        profiles = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item:
                profiles.append(item.data(Qt.ItemDataRole.UserRole))
        return profiles

    def _add_profile(self):
        dialog = ProfileDialog(parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            profile = dialog.get_profile()
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(profile["name"]))
            self.table.setItem(row, 1, QTableWidgetItem(profile["device_type"]))
            self.table.setItem(row, 2, QTableWidgetItem(profile["path"]))
            sf = profile.get("save_folder", "")
            self.table.setItem(row, 3, QTableWidgetItem(sf or "(same as game folder)"))
            self.table.setItem(row, 4, QTableWidgetItem(profile.get("system", "")))
            self.table.item(row, 0).setData(Qt.ItemDataRole.UserRole, profile)
            self._save_profiles()

    def _edit_profile(self):
        row = self.table.currentRow()
        if row < 0:
            return
        profile = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        dialog = ProfileDialog(profile=profile, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            updated = dialog.get_profile()
            self.table.setItem(row, 0, QTableWidgetItem(updated["name"]))
            self.table.setItem(row, 1, QTableWidgetItem(updated["device_type"]))
            self.table.setItem(row, 2, QTableWidgetItem(updated["path"]))
            sf = updated.get("save_folder", "")
            self.table.setItem(row, 3, QTableWidgetItem(sf or "(same as game folder)"))
            self.table.setItem(row, 4, QTableWidgetItem(updated.get("system", "")))
            self.table.item(row, 0).setData(Qt.ItemDataRole.UserRole, updated)
            self._save_profiles()

    def _delete_profile(self):
        row = self.table.currentRow()
        if row < 0:
            return
        name = self.table.item(row, 0).text()
        reply = QMessageBox.question(
            self, "Confirm Delete", f"Delete profile '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.table.removeRow(row)
            self._save_profiles()


# ---------------------------------------------------------------------------
# Tab 3 — Sync
# ---------------------------------------------------------------------------

class SyncTab(QWidget):
    def __init__(self, profiles_tab: ProfilesTab):
        super().__init__()
        self.profiles_tab = profiles_tab
        self._statuses: list = []
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # Profile selector row
        profile_row = QHBoxLayout()
        profile_row.addWidget(QLabel("Profile:"))
        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(220)
        self.profile_combo.setToolTip("Select which profile to scan and sync")
        profile_row.addWidget(self.profile_combo)
        refresh_profiles_btn = QPushButton("↺")
        refresh_profiles_btn.setFixedWidth(30)
        refresh_profiles_btn.setToolTip("Refresh profile list")
        refresh_profiles_btn.clicked.connect(self._refresh_profile_list)
        profile_row.addWidget(refresh_profiles_btn)
        profile_row.addStretch()
        layout.addLayout(profile_row)

        # Action buttons row
        btn_row = QHBoxLayout()
        self.scan_btn = QPushButton("Scan Profile")
        self.scan_btn.clicked.connect(self._scan)
        self.sync_all_btn = QPushButton("Sync All")
        self.sync_all_btn.clicked.connect(self._sync_all)
        self.sync_all_btn.setEnabled(False)
        self.sync_sel_btn = QPushButton("Sync Selected")
        self.sync_sel_btn.clicked.connect(self._sync_selected)
        self.sync_sel_btn.setEnabled(False)
        btn_row.addWidget(self.scan_btn)
        btn_row.addWidget(self.sync_all_btn)
        btn_row.addWidget(self.sync_sel_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Filter row
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("System:"))
        self.system_filter_combo = QComboBox()
        self.system_filter_combo.addItem("All")
        self.system_filter_combo.setMinimumWidth(100)
        self.system_filter_combo.currentTextChanged.connect(self._apply_filter)
        filter_row.addWidget(self.system_filter_combo)
        filter_row.addWidget(QLabel("Status:"))
        self.status_filter_combo = QComboBox()
        self.status_filter_combo.addItems(["All", "Local newer", "Server newer",
                                           "Not on server", "Server only", "Conflict",
                                           "Up to date", "Error"])
        self.status_filter_combo.currentTextChanged.connect(self._apply_filter)
        filter_row.addWidget(self.status_filter_combo)
        filter_row.addWidget(QLabel("Search:"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Filter by game name or title ID…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self.search_edit, 1)
        layout.addLayout(filter_row)

        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(
            ["System", "Game", "Title ID", "Local File", "Server Status", "Action"]
        )
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)

        self.status_label = QLabel("Select a profile and click Scan to begin.")
        layout.addWidget(self.status_label)

        # Populate on first show
        self._refresh_profile_list()

    def _refresh_profile_list(self):
        """Reload profiles from ProfilesTab into the dropdown."""
        profiles = self.profiles_tab.get_profiles()
        current = self.profile_combo.currentText()
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        for p in profiles:
            self.profile_combo.addItem(p.get("name", ""), userData=p)
        # Restore previous selection if still present
        idx = self.profile_combo.findText(current)
        if idx >= 0:
            self.profile_combo.setCurrentIndex(idx)
        self.profile_combo.blockSignals(False)
        if not profiles:
            self.status_label.setText("No profiles configured — add profiles in the Sync Profiles tab.")

    def _scan(self):
        self._refresh_profile_list()
        if self.profile_combo.count() == 0:
            QMessageBox.information(
                self, "No Profiles",
                "Add sync profiles in the 'Sync Profiles' tab first."
            )
            return

        profile = self.profile_combo.currentData()
        if not profile:
            return

        self.scan_btn.setEnabled(False)
        self.sync_all_btn.setEnabled(False)
        self.sync_sel_btn.setEnabled(False)
        self.status_label.setText(f"Scanning profile '{profile.get('name', '')}'…")
        self.table.setRowCount(0)

        self._worker = ScanWorker([profile], get_base_url(), get_api_headers())
        self._worker.result_ready.connect(self._on_scan_done)
        self._worker.error.connect(self._on_scan_error)
        self._worker.start()

    def _on_scan_done(self, statuses: list):
        self._statuses = statuses
        self._populate_table(statuses)
        self.scan_btn.setEnabled(True)
        self.sync_all_btn.setEnabled(True)
        self.sync_sel_btn.setEnabled(True)
        local_count = sum(1 for s in statuses if s.status != "server_only")
        server_only_count = sum(1 for s in statuses if s.status == "server_only")
        if local_count == 0 and server_only_count > 0:
            profile = self.profile_combo.currentData() or {}
            folder = profile.get("save_folder") or profile.get("path", "")
            self.status_label.setText(
                f"⚠ No local saves found in profile folder — is the device connected? "
                f"({server_only_count} saves exist on server only)"
            )
        else:
            parts = [f"{local_count} local saves"]
            if server_only_count:
                parts.append(f"{server_only_count} server-only")
            self.status_label.setText("Found " + ", ".join(parts))

    def _on_scan_error(self, msg: str):
        self.scan_btn.setEnabled(True)
        QMessageBox.critical(self, "Scan Error", msg)
        self.status_label.setText("Scan failed")

    def _populate_table(self, statuses: list):
        # Sort by system first, then game name — keeps all SNES/GBA/etc. together
        sorted_statuses = sorted(
            enumerate(statuses),
            key=lambda x: (x[1].save.system.upper(), x[1].save.game_name.lower()),
        )

        # Rebuild system filter combo from actual results (preserve selection)
        prev_system = self.system_filter_combo.currentText()
        self.system_filter_combo.blockSignals(True)
        self.system_filter_combo.clear()
        self.system_filter_combo.addItem("All")
        seen_systems: list[str] = []
        for _, st in sorted_statuses:
            s = st.save.system
            if s and s not in seen_systems:
                seen_systems.append(s)
                self.system_filter_combo.addItem(s)
        idx = self.system_filter_combo.findText(prev_system)
        self.system_filter_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.system_filter_combo.blockSignals(False)

        self.table.setRowCount(0)
        for i, st in sorted_statuses:
            save = st.save
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(save.system))
            self.table.setItem(row, 1, QTableWidgetItem(save.game_name))
            self.table.setItem(row, 2, QTableWidgetItem(save.title_id))
            # Local file column
            save_exists = getattr(save, "save_exists", True)
            if save.path is None:
                local_name = "(server only)"
                local_tooltip = "This save exists on the server but has no local file in any profile."
                local_color = QColor(140, 140, 140)
            elif not save_exists:
                local_name = "(no local save)"
                local_tooltip = f"ROM present on device — save will be placed at:\n{save.path}"
                local_color = QColor(160, 120, 40)
            else:
                local_name = save.path.name
                local_tooltip = str(save.path)
                local_color = None
            local_item = QTableWidgetItem(local_name)
            if local_color:
                local_item.setForeground(local_color)
            if local_tooltip:
                local_item.setToolTip(local_tooltip)
            self.table.setItem(row, 3, local_item)

            status_label = STATUS_LABELS.get(st.status, st.status)
            status_item = QTableWidgetItem(status_label)
            color = STATUS_COLORS.get(st.status)
            if color:
                status_item.setForeground(color)
            self.table.setItem(row, 4, status_item)

            # Action buttons
            # Upload: only when local save actually exists
            can_upload = save_exists and save.path is not None
            # Download: server_only, conflict (keep server), or server_newer without a local save
            needs_download_btn = st.status in ("conflict", "server_only") or (
                st.status == "server_newer" and not save_exists
            )
            action_needed = (
                st.status in ("conflict", "server_only", "not_on_server")
                or needs_download_btn
            )
            if action_needed:
                action_widget = QWidget()
                action_layout = QHBoxLayout(action_widget)
                action_layout.setContentsMargins(2, 2, 2, 2)
                action_layout.setSpacing(4)

                if st.status in ("conflict", "not_on_server") and can_upload:
                    lbl = "Keep Local" if st.status == "conflict" else "Upload"
                    upload_btn = QPushButton(lbl)
                    upload_btn.setFixedHeight(22)
                    upload_btn.clicked.connect(lambda _, idx=i: self._keep_local(idx))
                    action_layout.addWidget(upload_btn)

                if needs_download_btn:
                    lbl = "Keep Server" if st.status == "conflict" else "Download"
                    download_btn = QPushButton(lbl)
                    download_btn.setFixedHeight(22)
                    download_btn.clicked.connect(lambda _, idx=i: self._keep_server(idx))
                    action_layout.addWidget(download_btn)

                self.table.setCellWidget(row, 5, action_widget)

            self.table.item(row, 0).setData(Qt.ItemDataRole.UserRole, i)

        self._apply_filter()

    def _apply_filter(self):
        system_filter = self.system_filter_combo.currentText()
        status_filter = self.status_filter_combo.currentText()
        search = self.search_edit.text().strip().lower()
        for row in range(self.table.rowCount()):
            system_item = self.table.item(row, 0)
            game_item   = self.table.item(row, 1)
            tid_item    = self.table.item(row, 2)
            status_item = self.table.item(row, 4)
            system = system_item.text() if system_item else ""
            game   = game_item.text()   if game_item   else ""
            tid    = tid_item.text()    if tid_item    else ""
            status = status_item.text() if status_item else ""
            match_system = system_filter == "All" or system == system_filter
            match_status = status_filter == "All" or status == status_filter
            match_search = not search or search in game.lower() or search in tid.lower()
            self.table.setRowHidden(row, not (match_system and match_status and match_search))

    def _selected_status_indices(self) -> list[int]:
        rows = sorted(set(idx.row() for idx in self.table.selectedIndexes()))
        result = []
        for row in rows:
            item = self.table.item(row, 0)
            if item:
                result.append(item.data(Qt.ItemDataRole.UserRole))
        return result

    def _sync_all(self):
        # Only sync rows currently visible (respects system/status/search filters)
        indices = []
        for row in range(self.table.rowCount()):
            if not self.table.isRowHidden(row):
                item = self.table.item(row, 0)
                if item:
                    indices.append(item.data(Qt.ItemDataRole.UserRole))
        self._do_sync(indices)

    def _sync_selected(self):
        self._do_sync(self._selected_status_indices())

    def _do_sync(self, indices):
        from sync_engine import upload_save, download_save
        base_url = get_base_url()
        headers = get_api_headers()
        errors = []
        synced = 0
        skipped = 0

        indices = list(indices)
        progress = QProgressDialog("Syncing saves...", "Cancel", 0, len(indices), self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.show()

        for n, idx in enumerate(indices):
            progress.setValue(n)
            if progress.wasCanceled():
                break
            st = self._statuses[idx]
            try:
                save_exists = getattr(st.save, "save_exists", True)
                if st.status in ("local_newer", "not_on_server") and st.save.path and save_exists:
                    upload_save(st.save.title_id, st.save.path, base_url, headers)
                    self._update_row_status(idx, "up_to_date")
                    synced += 1
                elif st.status == "server_newer" and st.save.path:
                    download_save(st.save.title_id, st.save.path, base_url, headers)
                    self._update_row_status(idx, "up_to_date", new_path=st.save.path)
                    synced += 1
                elif st.status == "server_only":
                    dest_path = self._resolve_download_path(st)
                    if dest_path:
                        download_save(st.save.title_id, dest_path, base_url, headers)
                        self._update_row_status(idx, "up_to_date", new_path=dest_path)
                        synced += 1
                    else:
                        skipped += 1  # can't resolve destination — use Download button
                elif st.status == "conflict":
                    skipped += 1  # conflicts need manual resolution
                # up_to_date / error: skip
            except Exception as e:
                errors.append(f"{st.save.title_id}: {e}")

        progress.setValue(len(indices))
        progress.close()

        msg = f"Synced {synced} saves."
        if skipped:
            msg += f"\n{skipped} item(s) skipped (conflicts / unresolvable server-only — use the action buttons)."
        if errors:
            msg += f"\n\nErrors:\n" + "\n".join(errors)
        QMessageBox.information(self, "Sync Complete", msg)

    def _update_row_status(self, status_idx: int, new_status: str, new_path: Path | None = None):
        """Update a single table row in-place after an upload or download.

        Mutates self._statuses[status_idx] and refreshes the corresponding
        table row without triggering a full re-scan.
        """
        st = self._statuses[status_idx]
        st.status = new_status
        if new_path is not None:
            st.save.path = new_path
            st.save.save_exists = True

        # Find the table row whose UserRole matches status_idx
        target_row = None
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == status_idx:
                target_row = row
                break
        if target_row is None:
            return

        # Update status column
        status_label = STATUS_LABELS.get(new_status, new_status)
        status_item = QTableWidgetItem(status_label)
        color = STATUS_COLORS.get(new_status)
        if color:
            status_item.setForeground(color)
        self.table.setItem(target_row, 4, status_item)

        # Update local file column
        save = st.save
        save_exists = getattr(save, "save_exists", True)
        if save.path is None:
            local_name = "(server only)"
            local_color = QColor(140, 140, 140)
        elif not save_exists:
            local_name = "(no local save)"
            local_color = QColor(160, 120, 40)
        else:
            local_name = save.path.name
            local_color = None
        local_item = QTableWidgetItem(local_name)
        if local_color:
            local_item.setForeground(local_color)
        self.table.setItem(target_row, 3, local_item)

        # Clear action buttons — row is now up_to_date (no actions needed)
        self.table.setCellWidget(target_row, 5, None)

    def _keep_local(self, status_idx: int):
        from sync_engine import upload_save
        st = self._statuses[status_idx]
        if not st.save.path:
            QMessageBox.warning(self, "No Local File", "No local file found for this save.")
            return
        try:
            upload_save(st.save.title_id, st.save.path, get_base_url(), get_api_headers(), force=True)
            self._update_row_status(status_idx, "up_to_date")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _find_rom_subdir(self, rom_folder: Path, game_name: str, system: str) -> Path | None:
        """Search rom_folder recursively for a ROM whose stem matches game_name.

        Returns the relative subdirectory of the matching ROM within rom_folder,
        or None if not found.  Used by Pocket profiles to mirror the Assets/
        folder structure in the Saves/ folder.
        """
        import rom_normalizer as rn
        target = rn.normalize_name(game_name)
        if not target:
            return None
        for f in rom_folder.rglob("*"):
            if f.is_file() and f.suffix.lower() in rn.ROM_EXTENSIONS:
                if rn.normalize_name(f.stem) == target:
                    try:
                        rel = f.parent.relative_to(rom_folder)
                        return rel
                    except ValueError:
                        pass
        return None

    def _resolve_download_path(self, st) -> Path | None:
        """Return the correct local path for downloading a server-only save into the active profile.

        Uses the profile's device type and folder structure to mirror where
        the device would store this save file natively.
        Falls back to None if no profile is selected or path can't be resolved.
        """
        from sync_engine import (
            MISTER_FOLDER_MAP, POCKET_FOLDER_MAP,
            POCKET_OPENFPGA_FOLDER_MAP, RETROARCH_CORE_MAP,
        )
        import re

        profile = self.profile_combo.currentData()
        if not profile:
            return None

        device_type = profile.get("device_type", "Generic")
        save_root_str = profile.get("save_folder") or profile.get("path", "")
        if not save_root_str:
            return None
        save_root = Path(save_root_str)

        system = (st.save.system or "").upper()

        # Build a clean filename from the server name or title_id slug
        raw_name = st.save.game_name or st.save.title_id
        filename_stem = re.sub(r'[<>:"/\\|?*]', "_", raw_name).strip()
        if not filename_stem:
            # Fall back to slug portion of title_id
            if "_" in st.save.title_id and not st.save.title_id[0].isdigit():
                filename_stem = st.save.title_id[len(system) + 1:]
            else:
                filename_stem = st.save.title_id
        filename = filename_stem + ".sav"

        if device_type in ("Generic", "Everdrive"):
            return save_root / filename

        elif device_type == "MiSTer":
            folder = next((k for k, v in MISTER_FOLDER_MAP.items() if v == system), system)
            return save_root / folder / filename

        elif device_type in ("Pocket", "Pocket (openFPGA)"):
            # Pocket saves mirror the ROM's subfolder inside the Assets tree.
            # E.g. ROM at  Assets/snes/common/all/A-F/game.sfc
            #      Save at Saves/snes/common/all/A-F/game.sav
            if device_type == "Pocket":
                sys_folder = next((k for k, v in POCKET_FOLDER_MAP.items() if v == system), system)
            else:
                sys_folder = next((k for k, v in POCKET_OPENFPGA_FOLDER_MAP.items() if v == system), system.lower())

            # Try to locate the matching ROM in the Assets folder to get the exact subdir
            rom_folder_str = profile.get("path", "")
            if rom_folder_str:
                rom_folder = Path(rom_folder_str) / sys_folder
                if rom_folder.exists():
                    rel_subdir = self._find_rom_subdir(rom_folder, raw_name, system)
                    if rel_subdir is not None:
                        return save_root / sys_folder / rel_subdir / filename

            # ROM not found locally — place save flat under system folder
            return save_root / sys_folder / filename

        elif device_type == "RetroArch":
            core = next((k for k, v in RETROARCH_CORE_MAP.items() if v == system), system)
            return save_root / core / filename

        else:
            return save_root / filename

    def _keep_server(self, status_idx: int):
        from sync_engine import download_save
        st = self._statuses[status_idx]
        dest_path = st.save.path

        if dest_path is None:
            # Truly server-only (no local ROM/path) — resolve destination from profile structure
            dest_path = self._resolve_download_path(st)
            if dest_path is None:
                # Fall back to file dialog if resolution failed
                suggested = f"{st.save.title_id}.sav"
                dest_str, _ = QFileDialog.getSaveFileName(
                    self, f"Download {st.save.title_id}", suggested,
                    "Save Files (*.sav *.srm *.bin);;All Files (*)"
                )
                if not dest_str:
                    return
                dest_path = Path(dest_str)
            else:
                # Show the resolved path and let user confirm
                reply = QMessageBox.question(
                    self, "Download Save",
                    f"Download to:\n{dest_path}\n\nProceed?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
        else:
            # dest_path is already computed from the ROM scan (correct name + location)
            save_exists = getattr(st.save, "save_exists", True)
            if not save_exists:
                # New download — confirm destination once
                reply = QMessageBox.question(
                    self, "Download Save",
                    f"Download save for {st.save.game_name} to:\n{dest_path}\n\nProceed?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return

        try:
            download_save(st.save.title_id, dest_path, get_base_url(), get_api_headers())
            self._update_row_status(status_idx, "up_to_date", new_path=dest_path)
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))


# ---------------------------------------------------------------------------
# ROM Normalizer tab
# ---------------------------------------------------------------------------

class NormalizeScanWorker(QThread):
    finished = pyqtSignal(list)   # list of dicts: old, new, source, subfolder
    progress = pyqtSignal(str)

    def __init__(self, folder: Path, no_intro: dict, system: str, save_folder: Path | None = None):
        super().__init__()
        self.folder = folder
        self.no_intro = no_intro
        self.system = system
        self.save_folder = save_folder

    def run(self):
        import rom_normalizer as rn

        # Build name-based index for header matching (patched ROMs)
        name_index = rn.build_name_index(self.no_intro) if self.no_intro else {}

        # Pre-index save files from the save folder (stem → list of Path).
        # Using rglob so nested structures (e.g. Pocket's snes/common/all/A-F/) are
        # found regardless of how the save folder root aligns with the ROM folder root.
        save_index: dict[str, list[Path]] = {}
        if self.save_folder and self.save_folder.exists():
            self.progress.emit("Indexing save files…")
            for f in self.save_folder.rglob("*"):
                if f.is_file() and f.suffix.lower() in rn.SAVE_EXTENSIONS:
                    save_index.setdefault(f.stem, []).append(f)

        roms = rn.find_roms(self.folder)
        results = []
        for i, rom in enumerate(roms):
            self.progress.emit(f"Scanning {i + 1}/{len(roms)}: {rom.name}")
            ext = rom.suffix.lower()
            source = "filename"
            new_stem = rn.normalize_name(rom.name)   # default fallback

            if self.no_intro:
                # Step 1: exact CRC32 match → use canonical No-Intro name with region
                crc = rn._crc32_file(rom)
                canonical = self.no_intro.get(crc)
                if canonical:
                    new_stem = canonical   # e.g. "Bahamut Lagoon (Japan)"
                    source = "No-Intro"
                else:
                    # Step 2: read ROM header, match via No-Intro index
                    # Handles translated ROMs ("Bahamut Lagoon Eng v31" → "bahamut_lagoon")
                    # and roman/arabic mismatches ("FINAL FANTASY 5" → "Final Fantasy V")
                    header_title = rn.read_rom_header_title(rom, self.system)
                    if header_title:
                        canonical = rn.lookup_header_in_index(header_title, name_index)
                        if canonical:
                            new_stem = canonical   # e.g. "Final Fantasy V (Japan)"
                            source = "Header"
                    # Step 3: fuzzy filename prefix search — finds games like
                    # "Chaos Seed.sfc" → "Chaos Seed - Fuusui Kairoki (Japan)"
                    # when the filename slug is a unique prefix of a No-Intro key.
                    if source == "filename":
                        canonical = rn.fuzzy_filename_search(rom.name, name_index)
                        if canonical:
                            new_stem = canonical
                            source = "Fuzzy"
                    # Step 4: filename normalization (already set as default)

                    # Step 5: parent folder name lookup — for MSU packs and other games
                    # where the ROM uses a shorthand filename (e.g. "ys5_msu.sfc") but
                    # lives in a properly named subfolder ("Ys V - Ushinawareta Suna…").
                    # Only tried when all other steps failed and ROM is in a subfolder.
                    if source == "filename" and rom.parent != self.folder:
                        canonical = rn.fuzzy_filename_search(rom.parent.name, name_index)
                        if canonical:
                            new_stem = canonical
                            source = "Folder"

                    # Region correction: if the filename (or folder name) has a region tag
                    # and the matched canonical has a different region, prefer the matching
                    # region's No-Intro entry (e.g. "Final Fight 2 (Europe)" → "(USA)").
                    if source in ("Header", "Fuzzy", "Folder"):
                        region_hint = (rn.extract_region_hint(rom.name)
                                       or rn.extract_region_hint(rom.parent.name))
                        if region_hint:
                            new_stem = rn.find_region_preferred(new_stem, self.no_intro, region_hint)

            new_rom = rom.parent / (new_stem + ext)
            subfolder = str(rom.parent.relative_to(self.folder)) if rom.parent != self.folder else ""

            # Companion files (MSU-1 tracks, CUE sheets): store as (old_path, suffix)
            # so apply-time can derive the new name from whatever the user typed.
            # suffix = everything after the rom stem in the companion filename,
            # e.g. ".msu", "-1.pcm", ".cue"
            companions: list[tuple[Path, str]] = []
            for comp_old, _ in rn.find_companion_files(rom, new_stem):
                suffix = comp_old.name[len(rom.stem):]   # e.g. "-1.pcm", ".msu"
                companions.append((comp_old, suffix))

            if new_rom != rom:
                results.append({
                    "old": rom, "new": new_rom, "source": source,
                    "subfolder": subfolder, "companions": companions,
                })

            # Matching save files — shown as separate visible rows so the user
            # can review, edit, or uncheck them independently.
            # Search order:
            #   1. ROM's own folder (co-located saves)
            #   2. pre-built save_index from save_folder (handles any depth/structure)
            seen_saves: set[Path] = set()
            candidate_saves: list[Path] = []
            for save_ext in rn.SAVE_EXTENSIONS:
                co_located = rom.parent / (rom.stem + save_ext)
                if co_located.exists():
                    candidate_saves.append(co_located)
            if save_index:
                for sp in save_index.get(rom.stem, []):
                    if sp not in {c for c in candidate_saves}:
                        candidate_saves.append(sp)

            for save_file in candidate_saves:
                if save_file in seen_saves:
                    continue
                seen_saves.add(save_file)
                new_save = save_file.parent / (new_stem + save_file.suffix)
                if new_save != save_file:
                    save_subfolder = ""
                    try:
                        root = self.save_folder or self.folder
                        save_subfolder = str(save_file.parent.relative_to(root))
                    except ValueError:
                        save_subfolder = str(save_file.parent)
                    results.append({
                        "old": save_file, "new": new_save, "source": "Save",
                        "subfolder": save_subfolder, "companions": [],
                    })

        self.finished.emit(results)


class RomNormalizerTab(QWidget):
    def __init__(self):
        super().__init__()
        self._renames: list[dict] = []
        self._no_intro: dict = {}
        self._worker = None
        self._loaded_dat_path: Path | None = None
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # Folder row
        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("ROM Folder:"))
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText("Path to ROM/save folder (searched recursively)...")
        folder_row.addWidget(self.folder_edit, 4)
        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(self._browse_folder)
        folder_row.addWidget(browse_btn)
        load_profile_btn = QPushButton("Load from Profile…")
        load_profile_btn.setToolTip("Populate folders and system from a saved sync profile")
        load_profile_btn.clicked.connect(self._load_from_profile)
        folder_row.addWidget(load_profile_btn)
        folder_row.addWidget(QLabel("System:"))
        self.system_combo = QComboBox()
        self.system_combo.addItems([""] + SYSTEM_CHOICES)
        self.system_combo.currentTextChanged.connect(self._on_system_changed)
        folder_row.addWidget(self.system_combo)
        layout.addLayout(folder_row)

        # Save folder row (optional — for devices like Everdrive where saves live elsewhere)
        save_folder_row = QHBoxLayout()
        save_folder_row.addWidget(QLabel("Save Folder:"))
        self.save_folder_edit = QLineEdit()
        self.save_folder_edit.setPlaceholderText("Optional — separate save folder (e.g. Everdrive SAVE/ dir)...")
        save_folder_row.addWidget(self.save_folder_edit, 4)
        browse_save_btn = QPushButton("Browse")
        browse_save_btn.clicked.connect(self._browse_save_folder)
        save_folder_row.addWidget(browse_save_btn)
        clear_save_btn = QPushButton("Clear")
        clear_save_btn.clicked.connect(self.save_folder_edit.clear)
        save_folder_row.addWidget(clear_save_btn)
        layout.addLayout(save_folder_row)

        # DAT row
        dat_row = QHBoxLayout()
        dat_row.addWidget(QLabel("DAT:"))
        self.dat_label = QLabel("No DAT loaded — select a system or browse manually")
        self.dat_label.setStyleSheet("color: gray;")
        dat_row.addWidget(self.dat_label, 4)
        browse_dat_btn = QPushButton("Browse DAT...")
        browse_dat_btn.clicked.connect(self._browse_dat)
        dat_row.addWidget(browse_dat_btn)
        layout.addLayout(dat_row)

        # Buttons
        btn_row = QHBoxLayout()
        scan_btn = QPushButton("Scan / Preview")
        scan_btn.clicked.connect(self._scan)
        check_all_btn = QPushButton("Check All")
        check_all_btn.clicked.connect(lambda: self._set_all_checked(True))
        uncheck_all_btn = QPushButton("Uncheck All")
        uncheck_all_btn.clicked.connect(lambda: self._set_all_checked(False))
        self.nointro_only_check = QCheckBox("No-Intro / Redump matches only")
        self.nointro_only_check.setToolTip(
            "When checked, only renames matched via DAT (CRC, header, fuzzy, or folder name) are applied.\n"
            "Filename-normalized renames (yellow) are skipped."
        )
        self.nointro_only_check.stateChanged.connect(self._update_row_highlighting)
        self.apply_btn = QPushButton("Apply Renames")
        self.apply_btn.clicked.connect(self._apply)
        self.apply_btn.setEnabled(False)
        btn_row.addWidget(scan_btn)
        btn_row.addWidget(check_all_btn)
        btn_row.addWidget(uncheck_all_btn)
        btn_row.addStretch()
        btn_row.addWidget(self.nointro_only_check)
        btn_row.addWidget(self.apply_btn)
        layout.addLayout(btn_row)

        # Filter row
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Filter:"))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Search name or subfolder…")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self.filter_edit, 4)
        filter_row.addWidget(QLabel("Source:"))
        self.source_filter_combo = QComboBox()
        self.source_filter_combo.addItems(["All", "No-Intro", "Header", "Fuzzy", "Folder", "filename", "Save"])
        self.source_filter_combo.currentTextChanged.connect(self._apply_filter)
        filter_row.addWidget(self.source_filter_combo)
        layout.addLayout(filter_row)

        # Results table — col 0 items have checkboxes for per-row opt-out
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Current Name", "New Name", "Subfolder", "Source"])
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        hdr.resizeSection(2, 160)
        hdr.resizeSection(3, 90)
        self.table.setEditTriggers(QTableWidget.EditTrigger.DoubleClicked)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        self.table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.table)

        self.status_label = QLabel("Select a folder and system, then click Scan.")
        layout.addWidget(self.status_label)

    def _browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select ROM Folder")
        if folder:
            self.folder_edit.setText(folder)

    def _load_from_profile(self):
        profiles = load_config().get("profiles", [])
        if not profiles:
            QMessageBox.information(self, "No Profiles", "No sync profiles configured yet.\nAdd profiles in the Sync Profiles tab first.")
            return

        menu = QMenu(self)
        for p in profiles:
            name = p.get("name", "")
            system = p.get("system", "")
            label = f"{name}  [{system}]" if system else name
            action = menu.addAction(label)
            action.setData(p)

        btn = self.sender()
        action = menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))
        if action is None:
            return

        profile = action.data()
        self.folder_edit.setText(profile.get("path", ""))
        save_folder = profile.get("save_folder", "")
        self.save_folder_edit.setText(save_folder)

        system = profile.get("system", "")
        if system:
            idx = self.system_combo.findText(system)
            if idx >= 0:
                self.system_combo.setCurrentIndex(idx)

    def _browse_save_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Save Folder")
        if folder:
            self.save_folder_edit.setText(folder)

    def _browse_dat(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select No-Intro DAT", "", "DAT Files (*.dat *.xml)")
        if path:
            self._load_dat(Path(path))

    def _on_system_changed(self, system: str):
        if not system:
            return
        import rom_normalizer as rn
        dat_path = rn.find_dat_for_system(system)
        if dat_path:
            self._load_dat(dat_path)
        else:
            self.dat_label.setText(f"No DAT found for {system} in dats/ folder — browse manually")
            self.dat_label.setStyleSheet("color: orange;")
            self._no_intro = {}

    def _load_dat(self, path: Path):
        import rom_normalizer as rn
        self._no_intro = rn.load_no_intro_dat(path)
        self._loaded_dat_path = path
        count = len(self._no_intro)
        self.dat_label.setText(f"{path.name}  ({count:,} entries)")
        self.dat_label.setStyleSheet("color: green;" if count > 0 else "color: red;")

    def _scan(self):
        folder = Path(self.folder_edit.text().strip())
        if not folder.exists():
            QMessageBox.warning(self, "Error", "ROM folder not found.")
            return
        self.apply_btn.setEnabled(False)
        self.table.setRowCount(0)
        self._renames = []
        self.filter_edit.clear()
        self.source_filter_combo.setCurrentIndex(0)
        self.status_label.setText("Scanning...")
        save_folder_text = self.save_folder_edit.text().strip()
        save_folder = Path(save_folder_text) if save_folder_text else None
        if save_folder and not save_folder.exists():
            QMessageBox.warning(self, "Error", "Save folder not found.")
            return
        self._worker = NormalizeScanWorker(folder, self._no_intro, self.system_combo.currentText(), save_folder)
        self._worker.finished.connect(self._on_scan_done)
        self._worker.progress.connect(self.status_label.setText)
        self._worker.start()

    def _on_scan_done(self, renames: list):
        self._renames = renames
        self.table.setRowCount(len(renames))

        save_exts = {".sav", ".srm", ".mcr", ".frz", ".fs", ".rtc"}
        roms_only = [r for r in renames if r["source"] != "Save"]
        saves_only = [r for r in renames if r["source"] == "Save"]
        nointro   = sum(1 for r in roms_only if r["source"] == "No-Intro")
        header    = sum(1 for r in roms_only if r["source"] == "Header")
        fuzzy     = sum(1 for r in roms_only if r["source"] == "Fuzzy")
        folder    = sum(1 for r in roms_only if r["source"] == "Folder")
        filename  = sum(1 for r in roms_only if r["source"] == "filename")
        companion = sum(len(r.get("companions", [])) for r in renames)

        SOURCE_COLORS = {
            "No-Intro":  QColor(0, 200, 0),
            "Header":    QColor(80, 160, 255),
            "Fuzzy":     QColor(200, 100, 255),
            "Folder":    QColor(255, 160, 50),
            "filename":  QColor(255, 200, 0),
            "Save":      QColor(255, 140, 0),
        }
        for row, r in enumerate(renames):
            ro = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
            name_item = QTableWidgetItem(r["old"].name)
            name_item.setFlags(ro | Qt.ItemFlag.ItemIsUserCheckable)
            name_item.setCheckState(Qt.CheckState.Unchecked)
            comps = r.get("companions", [])
            if comps:
                tip = "Companion files (renamed with ROM):\n" + "\n".join(
                    f"  {c.name}  →  {{new stem}}{s}" for c, s in comps
                )
                name_item.setToolTip(tip)
            self.table.setItem(row, 0, name_item)
            new_item = QTableWidgetItem(r["new"].name)
            if comps:
                new_item.setToolTip(f"+{len(comps)} companion file(s) will follow this name")
            self.table.setItem(row, 1, new_item)   # editable
            subfolder_item = QTableWidgetItem(r["subfolder"])
            subfolder_item.setFlags(ro)
            self.table.setItem(row, 2, subfolder_item)
            src_item = QTableWidgetItem(r["source"])
            src_item.setFlags(ro)
            src_item.setForeground(SOURCE_COLORS.get(r["source"], QColor(255, 255, 255)))
            self.table.setItem(row, 3, src_item)
        self._update_row_highlighting()
        if renames:
            self.apply_btn.setEnabled(True)
            parts = []
            if nointro:   parts.append(f"{nointro} No-Intro CRC")
            if header:    parts.append(f"{header} header match")
            if fuzzy:     parts.append(f"{fuzzy} fuzzy name match")
            if folder:    parts.append(f"{folder} folder name match")
            if filename:  parts.append(f"{filename} filename only")
            comp_note = f" (+{companion} companion files)" if companion else ""
            save_note = f" (+{len(saves_only)} save files)" if saves_only else ""
            self.status_label.setText(
                f"{len(roms_only)} ROM rename(s) needed — {', '.join(parts)}{comp_note}{save_note}. "
                f"Review above, then click Apply Renames."
            )
        else:
            self.status_label.setText("All files already normalized — no renames needed.")

    def _update_row_highlighting(self):
        """Grey out non-No-Intro rows when 'No-Intro only' is checked."""
        SOURCE_COLORS = {
            "No-Intro":  QColor(0, 200, 0),
            "Header":    QColor(80, 160, 255),
            "Fuzzy":     QColor(200, 100, 255),
            "Folder":    QColor(255, 160, 50),
            "filename":  QColor(255, 200, 0),
            "Save":      QColor(255, 140, 0),
        }
        nointro_only = self.nointro_only_check.isChecked()
        dim = QColor(100, 100, 100)
        for row in range(self.table.rowCount()):
            src_item = self.table.item(row, 3)
            if src_item is None:
                continue
            source = src_item.text()
            excluded = nointro_only and source not in ("No-Intro", "Header", "Fuzzy", "Folder", "Save")
            for col in range(self.table.columnCount()):
                cell = self.table.item(row, col)
                if cell:
                    if excluded:
                        cell.setForeground(dim)
                    elif col == 3:
                        cell.setForeground(SOURCE_COLORS.get(source, QColor(255, 255, 255)))
                    else:
                        cell.setForeground(QColor(255, 255, 255))

    def _apply_filter(self):
        text = self.filter_edit.text().strip().lower()
        source_filter = self.source_filter_combo.currentText()
        for row in range(self.table.rowCount()):
            name_item = self.table.item(row, 0)
            sub_item  = self.table.item(row, 2)
            src_item  = self.table.item(row, 3)
            name = (name_item.text() if name_item else "").lower()
            sub  = (sub_item.text()  if sub_item  else "").lower()
            src  = src_item.text()   if src_item  else ""
            text_match   = not text or text in name or text in sub
            source_match = source_filter == "All" or src == source_filter
            self.table.setRowHidden(row, not (text_match and source_match))

    def _on_item_changed(self, item: QTableWidgetItem):
        """When a checkbox in col 0 is toggled and its row is selected, apply to all selected rows."""
        if item.column() != 0:
            return
        selected_rows = {idx.row() for idx in self.table.selectedIndexes()}
        if item.row() not in selected_rows or len(selected_rows) < 2:
            return
        state = item.checkState()
        self.table.blockSignals(True)
        for row in selected_rows:
            if row == item.row():
                continue
            cell = self.table.item(row, 0)
            if cell:
                cell.setCheckState(state)
        self.table.blockSignals(False)

    def _on_cell_double_clicked(self, row: int, col: int):
        """Double-click on any column except New Name toggles all rows in the same subfolder."""
        if col == 1:
            return  # let the editor open normally for New Name
        subfolder_item = self.table.item(row, 2)
        if subfolder_item is None:
            return
        subfolder = subfolder_item.text()

        # Collect visible row indices that share this subfolder
        rows_in_folder = [
            r for r in range(self.table.rowCount())
            if not self.table.isRowHidden(r)
            and (self.table.item(r, 2) or QTableWidgetItem("")).text() == subfolder
        ]

        # If every visible row in the group is checked, uncheck all; otherwise check all
        all_checked = all(
            (self.table.item(r, 0) or QTableWidgetItem("")).checkState() == Qt.CheckState.Checked
            for r in rows_in_folder
        )
        new_state = Qt.CheckState.Unchecked if all_checked else Qt.CheckState.Checked
        for r in rows_in_folder:
            item = self.table.item(r, 0)
            if item:
                item.setCheckState(new_state)

    def _set_all_checked(self, checked: bool):
        """Check/uncheck all currently visible rows."""
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for row in range(self.table.rowCount()):
            if self.table.isRowHidden(row):
                continue
            item = self.table.item(row, 0)
            if item:
                item.setCheckState(state)

    def _apply(self):
        if not self._renames:
            return
        nointro_only = self.nointro_only_check.isChecked()
        to_apply = []
        for row, r in enumerate(self._renames):
            item = self.table.item(row, 0)
            if item and item.checkState() != Qt.CheckState.Checked:
                continue
            if nointro_only and r["source"] not in ("No-Intro", "Header", "Fuzzy", "Folder", "Save"):
                continue
            to_apply.append((row, r))
        if not to_apply:
            QMessageBox.information(self, "Nothing to apply",
                "No renames to apply — all rows are unchecked or filtered out.")
            return
        filter_note = " (No-Intro/Redump matches only)" if nointro_only else ""
        rom_count  = sum(1 for _, r in to_apply if r["source"] != "Save")
        save_count = sum(1 for _, r in to_apply if r["source"] == "Save")
        parts = []
        if rom_count:  parts.append(f"{rom_count} ROM(s)")
        if save_count: parts.append(f"{save_count} save(s)")
        reply = QMessageBox.question(
            self, "Apply Renames",
            f"Rename {' + '.join(parts)}{filter_note}? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        import rom_normalizer as rn
        from datetime import datetime
        done_rows = []   # table row indices that were successfully renamed
        skipped = 0
        log_entries: list[str] = []  # (old_path, new_path) pairs for the undo log

        for row, r in to_apply:
            old = r["old"]
            # Use the (possibly user-edited) name from the table cell
            new_name = (self.table.item(row, 1) or QTableWidgetItem("")).text().strip()
            if not new_name:
                skipped += 1
                continue
            new = old.parent / new_name
            if new.exists() and new != old:
                skipped += 1
                continue
            try:
                old.rename(new)
                log_entries.append(f"{new}\t{old}")
                # Rename companion files (MSU tracks, CUE sheets) using the new stem
                for comp_old, comp_suffix in r.get("companions", []):
                    comp_new = comp_old.parent / (new.stem + comp_suffix)
                    if comp_new != comp_old and not comp_new.exists() and comp_old.exists():
                        comp_old.rename(comp_new)
                        if comp_new.suffix.lower() == ".cue":
                            rn.patch_cue_references(comp_new, comp_old.stem, comp_new.stem)
                        log_entries.append(f"{comp_new}\t{comp_old}")
                # Save files are their own rows — handled separately in this loop
                done_rows.append(row)
            except Exception as e:
                QMessageBox.warning(self, "Rename Error", f"Could not rename {old.name}:\n{e}")
                break

        # Write undo log — tab-separated "new_path<TAB>old_path" per line so a script
        # can reverse the renames by reading each line and renaming new→old.
        if log_entries:
            logs_dir = Path(__file__).parent / "logs"
            logs_dir.mkdir(exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_path = logs_dir / f"renames_{ts}.txt"
            try:
                with open(log_path, "w", encoding="utf-8") as lf:
                    lf.write(f"# ROM Normalizer rename log — {datetime.now().isoformat()}\n")
                    lf.write("# Format: new_path<TAB>old_path  (rename new→old to undo)\n")
                    lf.write("# To undo: for each line, rename the first path back to the second.\n\n")
                    lf.write("\n".join(log_entries) + "\n")
            except Exception as e:
                QMessageBox.warning(self, "Log Error", f"Could not write undo log:\n{e}")

        # Remove successfully renamed rows from the table and _renames list.
        # Iterate in reverse so that removing a row doesn't shift subsequent indices.
        self.table.blockSignals(True)
        for row in sorted(done_rows, reverse=True):
            self.table.removeRow(row)
            self._renames.pop(row)
        self.table.blockSignals(False)

        remaining = self.table.rowCount()
        done = len(done_rows)
        log_note = f" — log saved to logs/renames_{ts}.txt" if log_entries else ""
        if remaining == 0:
            self.apply_btn.setEnabled(False)
            self.status_label.setText(f"Done: {done} renamed, {skipped} skipped — all renames applied.{log_note}")
        else:
            self.status_label.setText(
                f"Done: {done} renamed, {skipped} skipped — {remaining} item(s) still pending.{log_note}"
            )

    def save_ui_state(self) -> dict:
        return {
            "rom_folder":    self.folder_edit.text(),
            "save_folder":   self.save_folder_edit.text(),
            "system":        self.system_combo.currentText(),
            "dat_path":      str(self._loaded_dat_path) if self._loaded_dat_path else "",
            "nointro_only":  self.nointro_only_check.isChecked(),
        }

    def load_ui_state(self, state: dict):
        if "rom_folder" in state:
            self.folder_edit.setText(state["rom_folder"])
        if "save_folder" in state:
            self.save_folder_edit.setText(state["save_folder"])
        if "system" in state:
            idx = self.system_combo.findText(state["system"])
            if idx >= 0:
                self.system_combo.setCurrentIndex(idx)
        if state.get("nointro_only"):
            self.nointro_only_check.setChecked(True)
        dat_path_str = state.get("dat_path", "")
        if dat_path_str:
            dat_path = Path(dat_path_str)
            if dat_path.exists():
                self._load_dat(dat_path)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class SaveManagerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Save Manager")
        self.setMinimumSize(1000, 650)
        self._init_ui()
        self._restore_state()

    def _init_ui(self):
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.server_tab = ServerSavesTab()
        self.profiles_tab = ProfilesTab()
        self.sync_tab = SyncTab(self.profiles_tab)
        self.normalizer_tab = RomNormalizerTab()

        self.tabs.addTab(self.server_tab, "Server Saves")
        self.tabs.addTab(self.profiles_tab, "Sync Profiles")
        self.tabs.addTab(self.sync_tab, "Sync")
        self.tabs.addTab(self.normalizer_tab, "ROM Normalizer")

        # Refresh sync profile list whenever the Sync tab is shown
        self.tabs.currentChanged.connect(self._on_tab_changed)

        menubar = self.menuBar()
        file_menu = menubar.addMenu("File")
        file_menu.addAction("Refresh Server Saves", self.server_tab.load_saves)
        file_menu.addSeparator()
        file_menu.addAction("Exit", self.close)

        tools_menu = menubar.addMenu("Tools")
        tools_menu.addAction("Config...", self._show_config)

    def _restore_state(self):
        cfg = load_config()
        ui = cfg.get("ui_state", {})

        # Window geometry
        if "window" in ui:
            w = ui["window"]
            self.resize(w.get("width", 1100), w.get("height", 700))
            if "x" in w and "y" in w:
                self.move(w["x"], w["y"])
        else:
            self.resize(1100, 700)

        # Active tab
        self.tabs.setCurrentIndex(ui.get("active_tab", 0))

        # Per-tab state
        self.server_tab.load_ui_state(ui.get("server_saves", {}))
        self.normalizer_tab.load_ui_state(ui.get("rom_normalizer", {}))

    def _save_state(self):
        cfg = load_config()
        geo = self.geometry()
        cfg["ui_state"] = {
            "window": {
                "x": geo.x(), "y": geo.y(),
                "width": geo.width(), "height": geo.height(),
            },
            "active_tab": self.tabs.currentIndex(),
            "server_saves": self.server_tab.save_ui_state(),
            "rom_normalizer": self.normalizer_tab.save_ui_state(),
        }
        save_config(cfg)

    def closeEvent(self, event):
        self._save_state()
        super().closeEvent(event)

    def _on_tab_changed(self, index: int):
        if self.tabs.widget(index) is self.sync_tab:
            self.sync_tab._refresh_profile_list()

    def _show_config(self):
        dialog = ConfigDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.server_tab.load_saves()


def main():
    app = QApplication(sys.argv)
    window = SaveManagerWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
