from PyQt6.QtWidgets import (
    QDialog,
    QHeaderView,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import Qt

from config import load_config, resolve_profile_for_sd, save_config
from dialogs.profile_dialog import ProfileDialog
from rom_installer import ROM_FORMAT_LABELS
from table_sorting import SortableItem, make_sortable, sorting_suspended

# Position in the saved profile list, so sorting the view never reorders config.
_ORDER_ROLE = Qt.ItemDataRole.UserRole + 1


def _profile_path_display(profile: dict) -> str:
    if profile.get("device_type") == "MemCard Pro FTP":
        host = profile.get("ftp_host", "")
        port = profile.get("ftp_port", 21)
        path = profile.get("path", "/") or "/"
        try:
            port_num = int(port or 21)
        except (TypeError, ValueError):
            port_num = 21
        port_text = "" if port_num == 21 else f":{port_num}"
        return f"ftp://{host}{port_text}{path}"
    return profile.get("path", "")


def _profile_rom_format_display(profile: dict) -> str:
    if "systems" in profile:
        values = {
            str(s.get("rom_format", "auto") or "auto").lower()
            for s in profile.get("systems", [])
            if s.get("enabled", True)
        }
        if len(values) == 1:
            return ROM_FORMAT_LABELS.get(next(iter(values)), "Auto")
        return "Mixed" if values else "Auto"
    value = str(profile.get("rom_format", "auto") or "auto").lower()
    return ROM_FORMAT_LABELS.get(value, "Auto")


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
        self.table.setHorizontalHeaderLabels(["Name", "Device Type", "Game Folder", "Save Folder", "ROM Format"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        make_sortable(self.table)
        layout.addWidget(self.table)

    def _set_row(self, row: int, profile: dict, order: int):
        with sorting_suspended(self.table):
            self.table.setItem(row, 0, SortableItem(profile.get("name", "")))
            self.table.setItem(row, 1, SortableItem(profile.get("device_type", "")))
            self.table.setItem(row, 2, SortableItem(_profile_path_display(profile)))
            sf = profile.get("save_folder", "")
            self.table.setItem(row, 3, SortableItem(sf or "(same as game folder)"))
            self.table.setItem(row, 4, SortableItem(_profile_rom_format_display(profile)))
            self.table.item(row, 0).setData(Qt.ItemDataRole.UserRole, profile)
            self.table.item(row, 0).setData(_ORDER_ROLE, order)

    def _append_row(self, profile: dict):
        orders = [item.data(_ORDER_ROLE) for item in self._profile_items()]
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._set_row(row, profile, max(orders, default=-1) + 1)

    def _profile_items(self) -> list:
        """Column-0 items in saved-profile order, whatever the view's sort."""
        items = [self.table.item(row, 0) for row in range(self.table.rowCount())]
        return sorted((i for i in items if i), key=lambda i: i.data(_ORDER_ROLE))

    def _load_profiles(self):
        config = load_config()
        profiles = config.get("profiles", [])
        self.table.setRowCount(0)
        for p in profiles:
            self._append_row(p)

    def _save_profiles(self):
        profiles = [item.data(Qt.ItemDataRole.UserRole) for item in self._profile_items()]
        config = load_config()
        config["profiles"] = profiles
        save_config(config)

    def get_profiles(self) -> list[dict]:
        # SD-card profiles get their drive letter remapped to the currently
        # mounted reader; the stored copy in the table keeps its fixed letter.
        return [
            resolve_profile_for_sd(item.data(Qt.ItemDataRole.UserRole))
            for item in self._profile_items()
        ]

    def _add_profile(self):
        dialog = ProfileDialog(parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._append_row(dialog.get_profile())
            self._save_profiles()

    def _edit_profile(self):
        row = self.table.currentRow()
        if row < 0:
            return
        item = self.table.item(row, 0)
        profile = item.data(Qt.ItemDataRole.UserRole)
        order = item.data(_ORDER_ROLE)
        dialog = ProfileDialog(profile=profile, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._set_row(row, dialog.get_profile(), order)
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
