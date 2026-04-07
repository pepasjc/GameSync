from datetime import datetime
from pathlib import Path

from PyQt6.QtWidgets import (
    QInputDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import Qt

from config import (
    ALL_CONSOLE_TYPES,
    detect_console_type,
    delete_save,
    download_ps1_cards,
    download_ps2_card,
    download_ps3_save,
    download_raw_save,
    fetch_all_saves,
    fetch_history,
    format_display_game_name,
    restore_history,
)
from dialogs.history_dialog import HistoryDialog


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
            name = format_display_game_name(
                save.get("game_name", title_id),
                console_type,
            )
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
                {
                    "title_id": title_id,
                    "console_id": save.get("console_id", ""),
                    "console_type": console_type,
                    "game_name": name,
                },
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
        console_type = data.get("console_type", detect_console_type(title_id))
        game_name = data.get("game_name", title_id)

        if console_type == "PS1":
            default_name = f"{title_id}.mcd"
            file_filter = "PS1 Memory Cards (*.mcd *.mcr);;All Files (*)"
        elif console_type == "PS2":
            selected_format, ok = QInputDialog.getItem(
                self,
                "PS2 Download Format",
                "Download card as:",
                ["mc2 (MemCard Pro)", "ps2 (PCSX2 / AetherSX2)"],
                0,
                False,
            )
            if not ok:
                return
            card_format = "ps2" if selected_format.startswith("ps2") else "mc2"
            suffix = ".ps2" if card_format == "ps2" else ".mc2"
            default_name = f"{game_name}{suffix}"
            file_filter = (
                "PS2 Cards (*.ps2 *.mc2);;All Files (*)"
                if card_format == "ps2"
                else "PS2 Cards (*.mc2 *.ps2);;All Files (*)"
            )
        elif console_type == "PS3":
            dest = QFileDialog.getExistingDirectory(
                self,
                "Select Destination Folder",
            )
            if not dest:
                return
            dest_path = Path(dest)
            if dest_path.name.upper() != title_id.upper():
                dest_path = dest_path / title_id
        else:
            default_name = f"{title_id}.sav"
            file_filter = "Save Files (*.sav *.srm);;All Files (*)"
            dest = QFileDialog.getSaveFileName(
                self, "Save File As", default_name, file_filter
            )[0]
            if not dest:
                return
            dest_path = Path(dest)
        try:
            if console_type == "PS1":
                written = download_ps1_cards(title_id, dest_path)
                QMessageBox.information(
                    self,
                    "Downloaded",
                    "PS1 card(s) written to:\n" + "\n".join(str(p) for p in written),
                )
            elif console_type == "PS2":
                download_ps2_card(title_id, dest_path, card_format=card_format)
                QMessageBox.information(self, "Downloaded", f"PS2 card written to:\n{dest}")
            elif console_type == "PS3":
                download_ps3_save(title_id, dest_path)
                QMessageBox.information(
                    self,
                    "Downloaded",
                    f"PS3 save folder written to:\n{dest_path}",
                )
            else:
                download_raw_save(title_id, dest_path)
                QMessageBox.information(
                    self,
                    "Downloaded",
                    f"Save written to:\n{dest_path}",
                )
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
