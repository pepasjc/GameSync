from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import Qt

from config import DEVICE_TYPES, SYSTEM_CHOICES


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

        # Save file extension
        self.save_ext_combo = QComboBox()
        self.save_ext_combo.setEditable(True)
        self.save_ext_combo.addItems([".sav", ".srm", ".mcr", ".frz", ".fs", ".mcd", ".dsv"])
        self.save_ext_combo.setCurrentText(".sav")
        self.save_ext_combo.setToolTip(
            "Extension used for save files on this device.\n"
            "e.g. Everdrive FXPAK uses .srm, Pocket openFPGA uses .sav"
        )
        layout.addRow("Save Extension:", self.save_ext_combo)

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
        self.save_ext_combo.setCurrentText(profile.get("save_ext", ".sav"))
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
        ext = self.save_ext_combo.currentText().strip()
        if not ext.startswith("."):
            ext = "." + ext
        return {
            "name": self.name_edit.text().strip(),
            "device_type": self.device_combo.currentText(),
            "path": self.path_edit.text().strip(),
            "save_folder": save_folder,
            "system": self.system_combo.currentText(),
            "save_ext": ext,
            "systems_filter": systems_filter,
        }
