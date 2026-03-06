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
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction


CONFIG_FILE = Path(__file__).parent / "config.json"


def load_config() -> dict:
    """Load config from file or environment variables."""
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "host": os.environ.get("SYNC_HOST", "localhost"),
        "port": int(os.environ.get("SYNC_PORT", "8000")),
        "api_key": os.environ.get("SYNC_API_KEY", "anything"),
    }


def save_config(config: dict) -> None:
    """Save config to file."""
    CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")


def get_api_headers() -> dict:
    """Get API headers from current config."""
    config = load_config()
    return {"X-API-Key": config.get("api_key", "anything")}


def get_base_url() -> str:
    """Get base URL from current config."""
    config = load_config()
    host = config.get("host", "localhost")
    port = config.get("port", "8000")
    return f"http://{host}:{port}"


_HEX_TITLE_RE = re.compile(r"^[0-9A-Fa-f]{16}$")
_PS_PREFIX_RE = re.compile(r"^[A-Z]{4}\d{5}")


def detect_console_type(title_id: str) -> str:
    """Detect console type from title ID format."""
    title_id = title_id.upper()
    if _HEX_TITLE_RE.match(title_id):
        return "3DS"
    if _PS_PREFIX_RE.match(title_id):
        base = title_id[:9]
        if base.startswith("PCS"):
            return "VITA"
        return "PSP"
    return "NDS"


def fetch_all_saves() -> list[dict]:
    """Fetch all saves from server."""
    resp = requests.get(
        f"{get_base_url()}/api/v1/titles", headers=get_api_headers(), timeout=30
    )
    resp.raise_for_status()
    return resp.json().get("titles", [])


def fetch_game_names(codes: list[str]) -> tuple[dict, dict]:
    """Fetch game names and types from server."""
    if not codes:
        return {}, {}
    try:
        resp = requests.post(
            f"{get_base_url()}/api/v1/titles/names",
            json={"codes": codes},
            headers=get_api_headers(),
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("names", {}), data.get("types", {})
    except Exception:
        return {}, {}


def fetch_history(title_id: str, console_id: str = "") -> list[dict]:
    """Fetch save history for a title."""
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
    """Delete a save via API."""
    params = {"console_id": console_id} if console_id else {}
    resp = requests.delete(
        f"{get_base_url()}/api/v1/saves/{title_id}",
        headers=get_api_headers(),
        params=params,
        timeout=30,
    )
    resp.raise_for_status()


def restore_history(title_id: str, timestamp: int, console_id: str = "") -> None:
    """Restore a save from history."""
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

        config = {
            "host": self.host_edit.text() or "localhost",
            "port": port,
            "api_key": self.api_key_edit.text() or "anything",
        }
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

    def get_selected_version(self) -> dict | None:
        return self.selected_timestamp


class SaveManagerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Save Manager")
        self.setMinimumSize(900, 600)
        self.saves = []
        self.game_names = {}
        self.game_types = {}
        self._init_ui()
        self._load_saves()

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        toolbar = QHBoxLayout()

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self._load_saves)
        toolbar.addWidget(self.refresh_btn)

        toolbar.addWidget(QLabel("Filter:"))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Search by ID or name...")
        self.filter_edit.textChanged.connect(self._apply_filter)
        toolbar.addWidget(self.filter_edit)

        self.console_filter = QComboBox()
        self.console_filter.addItems(["All", "3DS", "NDS", "PSP", "VITA", "PSX"])
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
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.doubleClicked.connect(self._on_double_click)
        layout.addWidget(self.table)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        menubar = self.menuBar()
        file_menu = menubar.addMenu("File")
        file_menu.addAction("Refresh", self._load_saves)
        file_menu.addSeparator()
        file_menu.addAction("Exit", self.close)

        tools_menu = menubar.addMenu("Tools")
        tools_menu.addAction("Config...", self._show_config)

    def _show_config(self):
        dialog = ConfigDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._load_saves()

    def contextMenuEvent(self, event):
        menu = QMenu(self)

        row = self.table.currentRow()
        if row >= 0:
            history_action = menu.addAction("Show History...")
            history_action.triggered.connect(self._show_history)

            restore_action = menu.addAction("Restore from History...")
            restore_action.triggered.connect(self._restore_history)

            menu.addSeparator()

            delete_action = menu.addAction("Delete")
            delete_action.triggered.connect(self._delete_save)

        menu.addAction("Refresh", self._load_saves)
        menu.exec(event.globalPos())

    def _load_saves(self):
        self.status_bar.showMessage("Loading saves...")
        try:
            self.saves = fetch_all_saves()
            self._populate_table()
            self.status_bar.showMessage(f"Loaded {len(self.saves)} saves")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load saves: {e}")
            self.status_bar.showMessage("Error loading saves")

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
            except:
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
                {
                    "title_id": title_id,
                    "console_id": save.get("console_id", ""),
                },
            )

    def _apply_filter(self):
        filter_text = self.filter_edit.text().lower()
        console_filter = self.console_filter.currentText()

        for row in range(self.table.rowCount()):
            show = True

            if console_filter != "All":
                console_item = self.table.item(row, 0)
                if console_item and console_item.text() != console_filter:
                    show = False

            if filter_text:
                id_item = self.table.item(row, 1)
                name_item = self.table.item(row, 2)
                id_text = id_item.text().lower() if id_item else ""
                name_text = name_item.text().lower() if name_item else ""
                if filter_text not in id_text and filter_text not in name_text:
                    show = False

            self.table.setRowHidden(row, not show)

    def _show_history(self):
        row = self.table.currentRow()
        if row < 0:
            return

        data = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        title_id = data.get("title_id", "")
        console_id = data.get("console_id", "")

        dialog = HistoryDialog(title_id, console_id, self)
        dialog.exec()

    def _restore_history(self):
        row = self.table.currentRow()
        if row < 0:
            return

        data = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        title_id = data.get("title_id", "")
        console_id = data.get("console_id", "")

        try:
            history = fetch_history(title_id, console_id)
            if not history:
                QMessageBox.information(
                    self, "No History", "No history versions available"
                )
                return

            dialog = QDialog(self)
            dialog.setWindowTitle("Restore from History")
            layout = QVBoxLayout(dialog)

            list_widget = QListWidget()
            for v in history:
                list_widget.addItem(
                    f"{v.get('display', 'Unknown')} - {v.get('size', 0):,} bytes, {v.get('file_count', 0)} files"
                )
                list_widget.item(list_widget.count() - 1).setData(
                    Qt.ItemDataRole.UserRole, v
                )
            layout.addWidget(list_widget)

            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok
                | QDialogButtonBox.StandardButton.Cancel
            )
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)

            if dialog.exec() == QDialog.DialogCode.Accepted:
                idx = list_widget.currentRow()
                if idx >= 0:
                    selected = list_widget.item(idx).data(Qt.ItemDataRole.UserRole)
                    timestamp = selected.get("timestamp")
                    reply = QMessageBox.question(
                        self,
                        "Confirm Restore",
                        f"Restore version from {selected.get('display')}?",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    )
                    if reply == QMessageBox.StandardButton.Yes:
                        restore_history(title_id, timestamp, console_id)
                        QMessageBox.information(
                            self, "Restored", "Save restored from history"
                        )
                        self._load_saves()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to restore: {e}")

    def _delete_save(self):
        row = self.table.currentRow()
        if row < 0:
            return

        data = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        title_id = data.get("title_id", "")
        console_id = data.get("console_id", "")

        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Delete save for '{title_id}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                delete_save(title_id, console_id)
                QMessageBox.information(self, "Deleted", "Save deleted")
                self._load_saves()
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to delete: {e}")

    def _on_double_click(self, index):
        row = index.row()
        title_id = self.table.item(row, 1).text()
        name = self.table.item(row, 2).text()
        console = self.table.item(row, 0).text()
        size = self.table.item(row, 4).text()
        files = self.table.item(row, 5).text()
        last_saved = self.table.item(row, 3).text()

        QMessageBox.information(
            self,
            "Save Details",
            f"Game ID: {title_id}\n"
            f"Name: {name}\n"
            f"Console: {console}\n"
            f"Size: {size} bytes\n"
            f"Files: {files}\n"
            f"Last Saved: {last_saved}",
        )


def main():
    app = QApplication(sys.argv)
    window = SaveManagerWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
