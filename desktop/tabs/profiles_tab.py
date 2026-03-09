from PyQt6.QtWidgets import (
    QDialog,
    QHeaderView,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import Qt

from config import load_config, save_config
from dialogs.profile_dialog import ProfileDialog


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
