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
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QAction, QColor


CONFIG_FILE = Path(__file__).parent / "config.json"

ALL_CONSOLE_TYPES = [
    "All", "3DS", "NDS", "PSP", "VITA", "PSX",
    "GBA", "SNES", "NES", "MD", "N64", "GB", "GBC",
    "GG", "SMS", "PCE", "PS1", "NGP", "ATARI2600",
    "ATARI7800", "LYNX", "NEOGEO", "32X", "SEGACD",
    "WSWAN", "WSWANC", "ARCADE", "MAME",
]

DEVICE_TYPES = ["Generic", "RetroArch", "MiSTer", "Analogue Pocket", "Everdrive"]

SYSTEM_CHOICES = [
    "GBA", "SNES", "NES", "MD", "N64", "GB", "GBC", "GG", "NGP",
    "PCE", "PS1", "SMS", "ATARI2600", "ATARI7800", "LYNX", "NEOGEO",
    "32X", "SEGACD", "TG16", "WSWAN", "WSWANC", "ARCADE", "MAME",
]

STATUS_COLORS = {
    "up_to_date":   QColor(0, 200, 0),
    "local_newer":  QColor(0, 160, 255),
    "server_newer": QColor(255, 200, 0),
    "not_on_server": QColor(180, 180, 180),
    "conflict":     QColor(220, 60, 60),
    "error":        QColor(200, 0, 200),
    "unknown":      QColor(180, 180, 180),
}

STATUS_LABELS = {
    "up_to_date":    "Up to date",
    "local_newer":   "Local newer",
    "server_newer":  "Server newer",
    "not_on_server": "Not on server",
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
        self.setWindowTitle("Sync Profile" if profile else "Add Sync Profile")
        self.setMinimumSize(480, 300)
        self._init_ui()
        if profile:
            self._load(profile)

    def _init_ui(self):
        layout = QFormLayout(self)

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
        self.path_edit.setPlaceholderText("Folder path...")
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse)
        path_layout.addWidget(self.path_edit)
        path_layout.addWidget(browse_btn)
        layout.addRow("Save Folder:", path_row)

        self.system_combo = QComboBox()
        self.system_combo.addItems(SYSTEM_CHOICES)
        layout.addRow("System:", self.system_combo)
        self._system_row_label = layout.labelForField(self.system_combo)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

        self._on_device_changed(self.device_combo.currentText())

    def _on_device_changed(self, device_type: str):
        # System dropdown only needed for Generic/Everdrive (flat folders)
        needs_system = device_type in ("Generic", "Everdrive")
        self.system_combo.setVisible(needs_system)
        if self._system_row_label:
            self._system_row_label.setVisible(needs_system)

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Save Folder")
        if folder:
            self.path_edit.setText(folder)

    def _load(self, profile: dict):
        self.name_edit.setText(profile.get("name", ""))
        idx = self.device_combo.findText(profile.get("device_type", "Generic"))
        if idx >= 0:
            self.device_combo.setCurrentIndex(idx)
        self.path_edit.setText(profile.get("path", ""))
        idx = self.system_combo.findText(profile.get("system", "GBA"))
        if idx >= 0:
            self.system_combo.setCurrentIndex(idx)

    def _accept(self):
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "Validation", "Profile name is required")
            return
        if not self.path_edit.text().strip():
            QMessageBox.warning(self, "Validation", "Save folder path is required")
            return
        self.accept()

    def get_profile(self) -> dict:
        return {
            "name": self.name_edit.text().strip(),
            "device_type": self.device_combo.currentText(),
            "path": self.path_edit.text().strip(),
            "system": self.system_combo.currentText(),
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
            for profile in self.profiles:
                all_saves.extend(scan_profile(profile))
            statuses = compare_with_server(all_saves, self.base_url, self.headers)
            self.result_ready.emit(statuses)
        except Exception as e:
            self.error.emit(str(e))


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
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Name", "Device Type", "Path", "System"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
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
            self.table.setItem(row, 3, QTableWidgetItem(p.get("system", "")))
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
            self.table.setItem(row, 3, QTableWidgetItem(profile.get("system", "")))
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
            self.table.setItem(row, 3, QTableWidgetItem(updated.get("system", "")))
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

        btn_row = QHBoxLayout()
        self.scan_btn = QPushButton("Scan Profiles")
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

        self.status_label = QLabel("Configure profiles and click Scan to begin.")
        layout.addWidget(self.status_label)

    def _scan(self):
        profiles = self.profiles_tab.get_profiles()
        if not profiles:
            QMessageBox.information(
                self, "No Profiles",
                "Add sync profiles in the 'Sync Profiles' tab first."
            )
            return

        self.scan_btn.setEnabled(False)
        self.status_label.setText("Scanning...")
        self.table.setRowCount(0)

        self._worker = ScanWorker(profiles, get_base_url(), get_api_headers())
        self._worker.result_ready.connect(self._on_scan_done)
        self._worker.error.connect(self._on_scan_error)
        self._worker.start()

    def _on_scan_done(self, statuses: list):
        self._statuses = statuses
        self._populate_table(statuses)
        self.scan_btn.setEnabled(True)
        self.sync_all_btn.setEnabled(True)
        self.sync_sel_btn.setEnabled(True)
        self.status_label.setText(f"Found {len(statuses)} local saves")

    def _on_scan_error(self, msg: str):
        self.scan_btn.setEnabled(True)
        QMessageBox.critical(self, "Scan Error", msg)
        self.status_label.setText("Scan failed")

    def _populate_table(self, statuses: list):
        self.table.setRowCount(0)
        for i, st in enumerate(statuses):
            save = st.save
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(save.system))
            self.table.setItem(row, 1, QTableWidgetItem(save.game_name))
            self.table.setItem(row, 2, QTableWidgetItem(save.title_id))
            self.table.setItem(row, 3, QTableWidgetItem(str(save.path.name)))
            status_label = STATUS_LABELS.get(st.status, st.status)
            status_item = QTableWidgetItem(status_label)
            color = STATUS_COLORS.get(st.status)
            if color:
                status_item.setForeground(color)
            self.table.setItem(row, 4, status_item)

            # Action buttons for conflicts
            if st.status == "conflict":
                action_widget = QWidget()
                action_layout = QHBoxLayout(action_widget)
                action_layout.setContentsMargins(2, 2, 2, 2)
                keep_local_btn = QPushButton("Keep Local")
                keep_server_btn = QPushButton("Keep Server")
                keep_local_btn.setFixedHeight(22)
                keep_server_btn.setFixedHeight(22)
                keep_local_btn.clicked.connect(lambda _, idx=i: self._keep_local(idx))
                keep_server_btn.clicked.connect(lambda _, idx=i: self._keep_server(idx))
                action_layout.addWidget(keep_local_btn)
                action_layout.addWidget(keep_server_btn)
                self.table.setCellWidget(row, 5, action_widget)

            self.table.item(row, 0).setData(Qt.ItemDataRole.UserRole, i)

    def _selected_status_indices(self) -> list[int]:
        rows = sorted(set(idx.row() for idx in self.table.selectedIndexes()))
        result = []
        for row in rows:
            item = self.table.item(row, 0)
            if item:
                result.append(item.data(Qt.ItemDataRole.UserRole))
        return result

    def _sync_all(self):
        self._do_sync(range(len(self._statuses)))

    def _sync_selected(self):
        self._do_sync(self._selected_status_indices())

    def _do_sync(self, indices):
        from sync_engine import upload_save, download_save
        base_url = get_base_url()
        headers = get_api_headers()
        errors = []
        synced = 0
        skipped = 0

        progress = QProgressDialog("Syncing saves...", "Cancel", 0, len(list(indices)), self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.show()

        for n, idx in enumerate(indices):
            progress.setValue(n)
            if progress.wasCanceled():
                break
            st = self._statuses[idx]
            try:
                if st.status == "local_newer" or st.status == "not_on_server":
                    upload_save(st.save.title_id, st.save.path, base_url, headers)
                    synced += 1
                elif st.status == "server_newer":
                    download_save(st.save.title_id, st.save.path, base_url, headers)
                    synced += 1
                elif st.status == "conflict":
                    skipped += 1  # conflicts need manual resolution
                # up_to_date / error: skip
            except Exception as e:
                errors.append(f"{st.save.title_id}: {e}")

        progress.setValue(len(list(indices)) if not progress.wasCanceled() else n)
        progress.close()

        msg = f"Synced {synced} saves."
        if skipped:
            msg += f"\n{skipped} conflicts skipped (use Keep Local / Keep Server buttons)."
        if errors:
            msg += f"\n\nErrors:\n" + "\n".join(errors)
        QMessageBox.information(self, "Sync Complete", msg)
        self._scan()

    def _keep_local(self, status_idx: int):
        from sync_engine import upload_save
        st = self._statuses[status_idx]
        try:
            upload_save(st.save.title_id, st.save.path, get_base_url(), get_api_headers(), force=True)
            QMessageBox.information(self, "Done", f"Uploaded local save for {st.save.title_id}")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
        self._scan()

    def _keep_server(self, status_idx: int):
        from sync_engine import download_save
        st = self._statuses[status_idx]
        try:
            download_save(st.save.title_id, st.save.path, get_base_url(), get_api_headers())
            QMessageBox.information(self, "Done", f"Downloaded server save for {st.save.title_id}")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
        self._scan()


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class SaveManagerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Save Manager")
        self.setMinimumSize(1000, 650)
        self._init_ui()

    def _init_ui(self):
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.server_tab = ServerSavesTab()
        self.profiles_tab = ProfilesTab()
        self.sync_tab = SyncTab(self.profiles_tab)

        self.tabs.addTab(self.server_tab, "Server Saves")
        self.tabs.addTab(self.profiles_tab, "Sync Profiles")
        self.tabs.addTab(self.sync_tab, "Sync")

        menubar = self.menuBar()
        file_menu = menubar.addMenu("File")
        file_menu.addAction("Refresh Server Saves", self.server_tab.load_saves)
        file_menu.addSeparator()
        file_menu.addAction("Exit", self.close)

        tools_menu = menubar.addMenu("Tools")
        tools_menu.addAction("Config...", self._show_config)

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
