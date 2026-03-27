from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QListWidget,
    QVBoxLayout,
)
from PyQt6.QtCore import Qt

from config import fetch_history


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
