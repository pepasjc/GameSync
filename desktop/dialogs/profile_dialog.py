from pathlib import Path

from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import Qt

from config import DEVICE_TYPES, SYSTEM_CHOICES


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SINGLE_SYSTEM_DEVICES = {
    "Generic",
    "Everdrive",
    "MEGA EverDrive",
    "SAROO",
    "MemCard Pro",
    "CD Folder",
}
MEMCARD_PRO_SYSTEMS = ["PS1", "PS2", "GC", "DC"]

# Relevant systems per multi-system device type (ordered by popularity)
DEVICE_SYSTEMS: dict[str, list[str]] = {
    "MiSTer": [
        "GBA",
        "SNES",
        "NES",
        "MD",
        "N64",
        "GB",
        "GBC",
        "GG",
        "SMS",
        "PCE",
        "PCSG",
        "A2600",
        "A7800",
        "LYNX",
        "NEOGEO",
        "32X",
        "SEGACD",
        "PS1",
    ],
    "RetroArch": [system for system in SYSTEM_CHOICES if system != "PS3"],
    "Analogue Pocket": [
        "GB",
        "GBA",
        "GBC",
        "GG",
        "SMS",
        "NES",
        "SNES",
        "MD",
        "NGP",
        "NGPC",
        "PCE",
        "PCSG",
        "LYNX",
        "WSWAN",
        "WSWANC",
    ],
    "Pocket (openFPGA)": [
        "GB",
        "GBA",
        "GBC",
        "GG",
        "SMS",
        "NES",
        "SNES",
        "MD",
        "N64",
        "NGP",
        "NGPC",
        "PCE",
        "PCSG",
        "LYNX",
        "WSWAN",
        "WSWANC",
        "PS1",
        "32X",
        "SEGACD",
        "SAT",
    ],
    "EmuDeck": list(SYSTEM_CHOICES),
}

# Default save extension per multi-system device type
DEVICE_DEFAULT_EXT: dict[str, str] = {
    "MiSTer": ".sav",
    "RetroArch": ".srm",
    "Analogue Pocket": ".sav",
    "Pocket (openFPGA)": ".sav",
    "EmuDeck": ".srm",
}

SAVE_EXT_OPTIONS = [
    ".sav",
    ".srm",
    ".mcr",
    ".frz",
    ".fs",
    ".mcd",
    ".mc2",
    ".dsv",
    ".raw",
    ".bkr",
]

SYSTEM_DEFAULT_EXT: dict[str, str] = {
    "SAT": ".bkr",
}

RETROARCH_AUTO_CORE_LABEL = "Auto"
RETROARCH_SATURN_CORE_LABELS = [
    "Beetle Saturn",
    "Yabause",
    "YabaSanshiro",
]


def _retroarch_core_options(system: str) -> list[str]:
    from sync_engine import RETROARCH_SYSTEM_CORES

    system_code = (system or "").upper()
    if system_code == "SAT":
        return RETROARCH_SATURN_CORE_LABELS[:]

    options = RETROARCH_SYSTEM_CORES.get(system_code, [])
    if not options:
        return []
    return [RETROARCH_AUTO_CORE_LABEL, *options]


def _is_retroarch_core_row(device_type: str, system: str) -> bool:
    return device_type == "RetroArch" and bool(_retroarch_core_options(system))


def _is_retroarch_saturn_row(device_type: str, system: str) -> bool:
    return _is_retroarch_core_row(device_type, system) and system.upper() == "SAT"


def _retroarch_saturn_display_value(save_ext: str, save_folder: str = "") -> str:
    ext = (save_ext or "").strip().lower()
    folder_name = ""
    if save_folder:
        try:
            folder_name = Path(save_folder).name.strip().lower()
        except Exception:
            folder_name = ""
    if ext == ".bin" or folder_name == "yabasanshiro":
        return "YabaSanshiro"
    if ext == ".srm":
        return "Yabause"
    return "Beetle Saturn"


def _retroarch_saturn_storage_values(
    display_value: str,
    save_folder: str = "",
) -> tuple[str, str]:
    label = (display_value or "").strip()
    folder = (save_folder or "").strip()
    folder_name = ""
    if folder:
        try:
            folder_name = Path(folder).name.strip().lower()
        except Exception:
            folder_name = ""

    if label == "YabaSanshiro":
        return ".bin", folder
    if label == "Yabause":
        return ".srm", "" if folder_name == "yabasanshiro" else folder
    return ".bkr", "" if folder_name == "yabasanshiro" else folder


def _normalize_ext(value: str, fallback: str) -> str:
    ext = (value or fallback).strip() or fallback
    if not ext.startswith("."):
        ext = "." + ext
    return ext


def _retroarch_display_value(
    system: str,
    save_ext: str,
    save_folder: str = "",
    core: str = "",
) -> str:
    system_code = (system or "").upper()
    if system_code == "SAT":
        return _retroarch_saturn_display_value(save_ext, save_folder)

    options = _retroarch_core_options(system_code)
    if not options:
        return save_ext

    core_label = (core or "").strip()
    if core_label and core_label in options:
        return core_label
    return RETROARCH_AUTO_CORE_LABEL


def _retroarch_storage_values(
    system: str,
    display_value: str,
    save_folder: str = "",
    current_ext: str = "",
    current_core: str = "",
) -> tuple[str, str, str]:
    system_code = (system or "").upper()
    if system_code == "SAT":
        ext, folder = _retroarch_saturn_storage_values(display_value, save_folder)
        return ext, folder, display_value.strip() or "Beetle Saturn"

    options = _retroarch_core_options(system_code)
    if not options:
        return _normalize_ext(display_value, ".sav"), save_folder, ""

    label = (display_value or "").strip() or RETROARCH_AUTO_CORE_LABEL
    fallback_ext = DEVICE_DEFAULT_EXT.get("RetroArch", ".srm")
    normalized_current_ext = _normalize_ext(current_ext or fallback_ext, fallback_ext)
    if label == RETROARCH_AUTO_CORE_LABEL:
        return normalized_current_ext, save_folder, ""
    chosen_ext = (
        fallback_ext
        if normalized_current_ext.lower() in {"", ".sav"}
        else normalized_current_ext
    )
    return _normalize_ext(chosen_ext, fallback_ext), save_folder, label


# ---------------------------------------------------------------------------
# Delegate: drop-down combo for the Save Ext column
# ---------------------------------------------------------------------------


class SaveExtDelegate(QStyledItemDelegate):
    def __init__(self, dialog, parent=None):
        super().__init__(parent)
        self._dialog = dialog

    def createEditor(self, parent, option, index):
        combo = QComboBox(parent)
        system_item = self._dialog.systems_table.item(index.row(), 1)
        system = system_item.text() if system_item else ""
        if _is_retroarch_core_row(self._dialog.device_combo.currentText(), system):
            combo.setEditable(False)
            combo.addItems(_retroarch_core_options(system))
        else:
            combo.setEditable(True)
            combo.addItems(SAVE_EXT_OPTIONS)
        return combo

    def setEditorData(self, editor, index):
        editor.setCurrentText(index.data() or ".sav")

    def setModelData(self, editor, model, index):
        system_item = self._dialog.systems_table.item(index.row(), 1)
        system = system_item.text() if system_item else ""
        val = editor.currentText().strip()
        if _is_retroarch_core_row(self._dialog.device_combo.currentText(), system):
            fallback = (
                "Beetle Saturn"
                if system.upper() == "SAT"
                else RETROARCH_AUTO_CORE_LABEL
            )
            model.setData(index, val or fallback)
            return
        if val and not val.startswith("."):
            val = "." + val
        model.setData(index, val or ".sav")

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)


# ---------------------------------------------------------------------------
# Main dialog
# ---------------------------------------------------------------------------


class ProfileDialog(QDialog):
    """Add / edit a sync profile.

    Single-system devices (Generic, Everdrive): show system + save ext + optional save folder.
    Multi-system devices (MiSTer, RetroArch, …): show global save folder + per-system table.
    """

    def __init__(self, profile: dict | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Sync Profile" if profile else "Add Sync Profile")
        self._loading = False
        self._last_save_override_folder: Path | None = None
        self._last_rom_override_folder: Path | None = None
        self._last_game_folder: Path | None = None
        self._last_single_save_folder: Path | None = None
        self._last_multi_save_folder: Path | None = None
        self._last_dat_folder: Path | None = None
        self._init_ui()
        if profile:
            self._load(profile)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _init_ui(self):
        outer = QVBoxLayout(self)
        outer.setSpacing(8)

        # ── Always-visible fields ──────────────────────────────────────
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
        outer.addLayout(form)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("e.g. MiSTer, RetroArch GBA, Everdrive SNES")
        form.addRow("Profile Name:", self.name_edit)

        self.device_combo = QComboBox()
        self.device_combo.addItems(DEVICE_TYPES)
        self.device_combo.currentTextChanged.connect(self._on_device_changed)
        form.addRow("Device Type:", self.device_combo)

        game_row = QWidget()
        game_layout = QHBoxLayout(game_row)
        game_layout.setContentsMargins(0, 0, 0, 0)
        self.game_folder_edit = QLineEdit()
        self.game_folder_edit.setPlaceholderText("Root game / ROM folder...")
        browse_game_btn = QPushButton("Browse...")
        browse_game_btn.clicked.connect(self._browse_game_folder)
        game_layout.addWidget(self.game_folder_edit)
        game_layout.addWidget(browse_game_btn)
        self._game_folder_label = QLabel("Game Folder:")
        form.addRow(self._game_folder_label, game_row)

        # ── Single-system section (Generic / Everdrive) ────────────────
        self._single_widget = QWidget()
        self._build_single_section()
        outer.addWidget(self._single_widget)

        # ── Multi-system section ───────────────────────────────────────
        self._multi_widget = QWidget()
        self._build_multi_section()
        outer.addWidget(self._multi_widget)

        # ── Buttons ────────────────────────────────────────────────────
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        # Trigger initial visibility
        self._on_device_changed(self.device_combo.currentText())

    def _build_single_section(self):
        layout = QFormLayout(self._single_widget)
        layout.setContentsMargins(0, 0, 0, 0)

        self.system_combo = QComboBox()
        self.system_combo.addItems(SYSTEM_CHOICES)
        layout.addRow("System:", self.system_combo)

        self.save_ext_combo = QComboBox()
        self.save_ext_combo.setEditable(True)
        self.save_ext_combo.addItems(SAVE_EXT_OPTIONS)
        self.save_ext_combo.setCurrentText(".sav")
        layout.addRow("Save Extension:", self.save_ext_combo)

        # Separate save folder (optional)
        sep_row = QWidget()
        sep_layout = QHBoxLayout(sep_row)
        sep_layout.setContentsMargins(0, 0, 0, 0)
        self.single_save_folder_edit = QLineEdit()
        self.single_save_folder_edit.setPlaceholderText(
            "Leave empty — saves are in same folder as games"
        )
        browse_save_btn = QPushButton("Browse...")
        browse_save_btn.clicked.connect(self._browse_single_save_folder)
        clear_save_btn = QPushButton("Clear")
        clear_save_btn.clicked.connect(self.single_save_folder_edit.clear)
        sep_layout.addWidget(self.single_save_folder_edit)
        sep_layout.addWidget(browse_save_btn)
        sep_layout.addWidget(clear_save_btn)
        layout.addRow("Save Folder:", sep_row)

        # Redump DAT file (optional, only shown for CD Folder profiles)
        dat_row = QWidget()
        dat_layout = QHBoxLayout(dat_row)
        dat_layout.setContentsMargins(0, 0, 0, 0)
        self.dat_file_edit = QLineEdit()
        self.dat_file_edit.setPlaceholderText(
            "Optional Redump DAT for canonical game names…"
        )
        browse_dat_btn = QPushButton("Browse…")
        browse_dat_btn.clicked.connect(self._browse_dat_file)
        clear_dat_btn = QPushButton("Clear")
        clear_dat_btn.clicked.connect(self.dat_file_edit.clear)
        dat_layout.addWidget(self.dat_file_edit)
        dat_layout.addWidget(browse_dat_btn)
        dat_layout.addWidget(clear_dat_btn)
        self._dat_row_label = QLabel("Redump DAT:")
        layout.addRow(self._dat_row_label, dat_row)
        self._dat_row_widget = dat_row

    def _build_multi_section(self):
        layout = QVBoxLayout(self._multi_widget)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(6)

        # Global save folder
        save_root_row = QHBoxLayout()
        save_root_row.addWidget(QLabel("Save Folder:"))
        self.multi_save_folder_edit = QLineEdit()
        self.multi_save_folder_edit.setPlaceholderText(
            "Leave empty — saves co-located with game folder"
        )
        save_root_row.addWidget(self.multi_save_folder_edit, 1)
        browse_save_root_btn = QPushButton("Browse...")
        browse_save_root_btn.clicked.connect(self._browse_multi_save_folder)
        clear_save_root_btn = QPushButton("Clear")
        clear_save_root_btn.clicked.connect(self.multi_save_folder_edit.clear)
        save_root_row.addWidget(browse_save_root_btn)
        save_root_row.addWidget(clear_save_root_btn)
        layout.addLayout(save_root_row)

        # Systems table header row
        table_hdr_row = QHBoxLayout()
        table_hdr_row.addWidget(QLabel("Systems:"))
        sel_all_btn = QPushButton("All")
        sel_all_btn.setFixedWidth(40)
        sel_all_btn.clicked.connect(lambda: self._set_all_enabled(True))
        sel_none_btn = QPushButton("None")
        sel_none_btn.setFixedWidth(46)
        sel_none_btn.clicked.connect(lambda: self._set_all_enabled(False))
        table_hdr_row.addWidget(sel_all_btn)
        table_hdr_row.addWidget(sel_none_btn)
        table_hdr_row.addStretch()
        layout.addLayout(table_hdr_row)

        # Systems table
        self.systems_table = QTableWidget(0, 5)
        self.systems_table.setHorizontalHeaderLabels(
            ["", "System", "Save Format", "Save Folder Override", "ROM Folder Override"]
        )
        hdr = self.systems_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.systems_table.setColumnWidth(0, 28)
        self.systems_table.setColumnWidth(1, 90)
        self.systems_table.setColumnWidth(2, 170)
        self.systems_table.setItemDelegateForColumn(2, SaveExtDelegate(self, self))
        self.systems_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.systems_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.systems_table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.SelectedClicked
        )
        self.systems_table.verticalHeader().setVisible(False)
        layout.addWidget(self.systems_table)

        # Browse override button
        override_row = QHBoxLayout()
        self.browse_save_override_btn = QPushButton(
            "Browse Save Override for Selected System…"
        )
        self.browse_save_override_btn.setEnabled(False)
        self.browse_save_override_btn.clicked.connect(self._browse_save_override_folder)
        override_row.addWidget(self.browse_save_override_btn)
        clear_save_override_btn = QPushButton("Clear Save Override")
        clear_save_override_btn.clicked.connect(self._clear_save_override_folder)
        override_row.addWidget(clear_save_override_btn)
        self.browse_rom_override_btn = QPushButton(
            "Browse ROM Override for Selected System…"
        )
        self.browse_rom_override_btn.setEnabled(False)
        self.browse_rom_override_btn.clicked.connect(self._browse_rom_override_folder)
        override_row.addWidget(self.browse_rom_override_btn)
        clear_rom_override_btn = QPushButton("Clear ROM Override")
        clear_rom_override_btn.clicked.connect(self._clear_rom_override_folder)
        override_row.addWidget(clear_rom_override_btn)
        override_row.addStretch()
        layout.addLayout(override_row)

        self.systems_table.selectionModel().selectionChanged.connect(
            self._on_system_selection_changed
        )

    # ------------------------------------------------------------------
    # Signal handlers
    # ------------------------------------------------------------------

    def _on_device_changed(self, device_type: str):
        is_single = device_type in SINGLE_SYSTEM_DEVICES
        self._single_widget.setVisible(is_single)
        self._multi_widget.setVisible(not is_single)

        is_memcard = device_type == "MemCard Pro"
        is_saroo = device_type == "SAROO"
        self._set_single_system_choices(device_type)
        # Show Redump DAT field only for CD Folder profiles
        is_cd = device_type == "CD Folder"
        self._dat_row_label.setVisible(is_cd)
        self._dat_row_widget.setVisible(is_cd)
        self._game_folder_label.setText(
            "Root Folder:" if is_memcard else "Game Folder:"
        )
        if is_memcard:
            self.game_folder_edit.setPlaceholderText(
                "MemCard Pro root folder or MemoryCards folder..."
            )
            self.single_save_folder_edit.setPlaceholderText(
                "Leave empty — not used for MemCard Pro"
            )
        elif is_saroo:
            self.game_folder_edit.setPlaceholderText(
                "Saroo SD card root folder (contains SS_SAVE.BIN)…"
            )
            self.single_save_folder_edit.setPlaceholderText(
                "Mednafen save folder (optional — for emulator sync)"
            )
        else:
            self.game_folder_edit.setPlaceholderText("Root game / ROM folder...")
            self.single_save_folder_edit.setPlaceholderText(
                "Leave empty — saves are in same folder as games"
            )

        # Card-manager and CD-folder profiles default to PS1 + .mcd.
        if (is_cd or is_memcard) and not self._loading:
            self._apply_single_system_defaults("PS1")

        # SAROO is always SAT; lock system and hide save-ext picker
        if is_saroo and not self._loading:
            self._apply_single_system_defaults("SAT")

        if is_single:
            self.setMinimumSize(480, 0)
        else:
            self.setMinimumSize(580, 520)
            self.systems_table.setHorizontalHeaderLabels(
                [
                    "",
                    "System",
                    "Core" if device_type == "RetroArch" else "Save Format",
                    "Save Folder Override",
                    "ROM Folder Override",
                ]
            )
            self._populate_systems_table(device_type)
        self.adjustSize()

    def _set_single_system_choices(self, device_type: str) -> None:
        """Restrict the system picker for device-specific single-system profiles."""
        current = self.system_combo.currentText()
        choices = (
            MEMCARD_PRO_SYSTEMS if device_type == "MemCard Pro" else SYSTEM_CHOICES
        )
        self.system_combo.blockSignals(True)
        self.system_combo.clear()
        self.system_combo.addItems(choices)
        if current in choices:
            self.system_combo.setCurrentText(current)
        elif choices:
            self.system_combo.setCurrentIndex(0)
        self.system_combo.blockSignals(False)

    def _apply_single_system_defaults(self, system: str) -> None:
        """Apply sensible defaults when a profile implies a specific system."""
        idx = self.system_combo.findText(system)
        if idx >= 0:
            self.system_combo.setCurrentIndex(idx)
        default_ext = {
            "PS1": ".mcd",
            "PS2": ".mc2",
            "GC": ".raw",
            "DC": ".bin",
            "SAT": ".bkr",
        }.get(system)
        if default_ext:
            ext_idx = self.save_ext_combo.findText(default_ext)
            if ext_idx >= 0:
                self.save_ext_combo.setCurrentIndex(ext_idx)
            else:
                self.save_ext_combo.setCurrentText(default_ext)

    def _on_system_selection_changed(self):
        has_selection = bool(self.systems_table.selectedItems())
        self.browse_save_override_btn.setEnabled(has_selection)
        self.browse_rom_override_btn.setEnabled(has_selection)

    def _on_separate_save_changed(self):
        pass  # kept for compat — single section always shows save folder row

    # ------------------------------------------------------------------
    # Systems table helpers
    # ------------------------------------------------------------------

    def _populate_systems_table(
        self, device_type: str, existing: dict[str, dict] | None = None
    ):
        """Fill the systems table for *device_type*.

        existing: {system_code: {enabled, save_ext, save_folder, rom_folder}} for pre-loading saved values.
        """
        systems = DEVICE_SYSTEMS.get(device_type, list(SYSTEM_CHOICES))
        default_ext = DEVICE_DEFAULT_EXT.get(device_type, ".sav")

        self.systems_table.setRowCount(0)
        ro_flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

        for system in systems:
            row = self.systems_table.rowCount()
            self.systems_table.insertRow(row)
            info = (existing or {}).get(system, {})

            # Col 0 — enabled checkbox (no text)
            cb_item = QTableWidgetItem()
            cb_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
            )
            enabled = info.get("enabled", True)
            cb_item.setCheckState(
                Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked
            )
            self.systems_table.setItem(row, 0, cb_item)

            # Col 1 — system name (read-only)
            sys_item = QTableWidgetItem(system)
            sys_item.setFlags(ro_flags)
            self.systems_table.setItem(row, 1, sys_item)

            # Col 2 — save extension (editable via delegate)
            ext = info.get("save_ext", SYSTEM_DEFAULT_EXT.get(system, default_ext))
            display_ext = (
                _retroarch_display_value(
                    system,
                    ext,
                    info.get("save_folder", ""),
                    info.get("core", ""),
                )
                if device_type == "RetroArch"
                else ext
            )
            ext_item = QTableWidgetItem(display_ext)
            ext_item.setFlags(ro_flags | Qt.ItemFlag.ItemIsEditable)
            ext_item.setData(
                Qt.ItemDataRole.UserRole,
                {
                    "save_ext": ext,
                    "core": info.get("core", ""),
                },
            )
            if _is_retroarch_core_row(device_type, system):
                ext_item.setToolTip(
                    "Choose the RetroArch core/save target for this system."
                )
            self.systems_table.setItem(row, 2, ext_item)

            # Col 3 — save folder override (editable text, empty = use global)
            folder_item = QTableWidgetItem(info.get("save_folder", ""))
            folder_item.setFlags(ro_flags | Qt.ItemFlag.ItemIsEditable)
            folder_item.setToolTip(
                "Override save folder for this specific system.\n"
                "Leave empty to auto-compute from the save root above."
            )
            self.systems_table.setItem(row, 3, folder_item)

            rom_folder_item = QTableWidgetItem(info.get("rom_folder", ""))
            rom_folder_item.setFlags(ro_flags | Qt.ItemFlag.ItemIsEditable)
            rom_folder_item.setToolTip(
                "Optional ROM folder override for this specific system.\n"
                "Leave empty to use the profile's global Game Folder."
            )
            self.systems_table.setItem(row, 4, rom_folder_item)

    def _set_all_enabled(self, checked: bool):
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for row in range(self.systems_table.rowCount()):
            item = self.systems_table.item(row, 0)
            if item:
                item.setCheckState(state)

    def _selected_row(self) -> int:
        rows = self.systems_table.selectionModel().selectedRows()
        return rows[0].row() if rows else -1

    def _browse_save_override_folder(self):
        row = self._selected_row()
        if row < 0:
            return
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Save Folder Override",
            str(self._last_save_override_folder or ""),
        )
        if folder:
            self._last_save_override_folder = Path(folder)
            item = self.systems_table.item(row, 3)
            if item:
                item.setText(folder)

    def _clear_save_override_folder(self):
        row = self._selected_row()
        if row < 0:
            return
        item = self.systems_table.item(row, 3)
        if item:
            item.setText("")

    def _browse_rom_override_folder(self):
        row = self._selected_row()
        if row < 0:
            return
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select ROM Folder Override",
            str(self._last_rom_override_folder or ""),
        )
        if folder:
            self._last_rom_override_folder = Path(folder)
            item = self.systems_table.item(row, 4)
            if item:
                item.setText(folder)

    def _clear_rom_override_folder(self):
        row = self._selected_row()
        if row < 0:
            return
        item = self.systems_table.item(row, 4)
        if item:
            item.setText("")

    # ------------------------------------------------------------------
    # Browse helpers
    # ------------------------------------------------------------------

    def _browse_game_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Game / ROM Folder",
            str(self._last_game_folder or ""),
        )
        if folder:
            self._last_game_folder = Path(folder)
            self.game_folder_edit.setText(folder)

    def _browse_single_save_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Save Folder",
            str(self._last_single_save_folder or ""),
        )
        if folder:
            self._last_single_save_folder = Path(folder)
            self.single_save_folder_edit.setText(folder)

    def _browse_multi_save_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Save Root Folder",
            str(self._last_multi_save_folder or ""),
        )
        if folder:
            self._last_multi_save_folder = Path(folder)
            self.multi_save_folder_edit.setText(folder)

    def _browse_dat_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Redump DAT File",
            str(self._last_dat_folder or ""),
            "DAT Files (*.dat);;All Files (*)",
        )
        if path:
            self._last_dat_folder = Path(path).parent
            self.dat_file_edit.setText(path)

    # ------------------------------------------------------------------
    # Load / save profile data
    # ------------------------------------------------------------------

    def _load(self, profile: dict):
        self._loading = True
        try:
            self.name_edit.setText(profile.get("name", ""))

            idx = self.device_combo.findText(profile.get("device_type", "Generic"))
            if idx >= 0:
                self.device_combo.setCurrentIndex(idx)

            self.game_folder_edit.setText(profile.get("path", ""))

            device_type = profile.get("device_type", "Generic")

            if device_type in SINGLE_SYSTEM_DEVICES:
                idx = self.system_combo.findText(profile.get("system", "GBA"))
                if idx >= 0:
                    self.system_combo.setCurrentIndex(idx)
                self.save_ext_combo.setCurrentText(profile.get("save_ext", ".sav"))
                self.single_save_folder_edit.setText(profile.get("save_folder", ""))
                if device_type == "CD Folder":
                    self.dat_file_edit.setText(profile.get("dat_path", ""))
            else:
                self.multi_save_folder_edit.setText(profile.get("save_folder", ""))

                # Build existing dict from whichever format the profile uses
                existing: dict[str, dict] = {}
                if "systems" in profile:
                    # New format
                    for s in profile["systems"]:
                        existing[s["system"]] = s
                elif "systems_filter" in profile:
                    # Old format: systems_filter is a list of enabled system codes
                    sf = set(profile.get("systems_filter") or [])
                    all_systems = DEVICE_SYSTEMS.get(device_type, list(SYSTEM_CHOICES))
                    if sf:
                        for s in all_systems:
                            existing[s] = {
                                "enabled": s in sf,
                                "save_ext": profile.get("save_ext", ".sav"),
                            }
                    # If sf is empty, all systems enabled — leave existing empty so defaults apply

                self._populate_systems_table(device_type, existing or None)
        finally:
            self._loading = False

    def _accept(self):
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "Validation", "Profile name is required.")
            return
        if not self.game_folder_edit.text().strip():
            device_type = self.device_combo.currentText()
            message = (
                "Root folder path is required."
                if device_type == "MemCard Pro"
                else "Game folder path is required."
            )
            QMessageBox.warning(self, "Validation", message)
            return
        self.accept()

    def get_profile(self) -> dict:
        device_type = self.device_combo.currentText()

        base = {
            "name": self.name_edit.text().strip(),
            "device_type": device_type,
            "path": self.game_folder_edit.text().strip(),
        }

        if device_type in SINGLE_SYSTEM_DEVICES:
            ext = self.save_ext_combo.currentText().strip()
            if not ext.startswith("."):
                ext = "." + ext
            result = {
                **base,
                "save_folder": self.single_save_folder_edit.text().strip(),
                "system": self.system_combo.currentText(),
                "save_ext": ext or ".sav",
            }
            if device_type == "CD Folder":
                dat_path = self.dat_file_edit.text().strip()
                if dat_path:
                    result["dat_path"] = dat_path
            return result
        else:
            systems = []
            for row in range(self.systems_table.rowCount()):
                cb = self.systems_table.item(row, 0)
                sys = self.systems_table.item(row, 1)
                ext = self.systems_table.item(row, 2)
                fld = self.systems_table.item(row, 3)
                rom_fld = self.systems_table.item(row, 4)
                if not sys:
                    continue
                ext_text = (ext.text().strip() if ext else ".sav") or ".sav"
                ext_meta = (
                    ext.data(Qt.ItemDataRole.UserRole)
                    if ext and ext.data(Qt.ItemDataRole.UserRole) is not None
                    else {}
                )
                current_ext = str(ext_meta.get("save_ext", "")) if isinstance(ext_meta, dict) else ""
                current_core = str(ext_meta.get("core", "")) if isinstance(ext_meta, dict) else ""
                folder_val = fld.text().strip() if fld else ""
                rom_folder_val = rom_fld.text().strip() if rom_fld else ""
                core_val = ""
                if device_type == "RetroArch":
                    ext_val, folder_val, core_val = _retroarch_storage_values(
                        sys.text(),
                        ext_text,
                        folder_val,
                        current_ext=current_ext,
                        current_core=current_core,
                    )
                else:
                    ext_val = ext_text
                    if not ext_val.startswith("."):
                        ext_val = "." + ext_val
                systems.append(
                    {
                        "system": sys.text(),
                        "enabled": cb.checkState() == Qt.CheckState.Checked
                        if cb
                        else True,
                        "save_ext": ext_val,
                        "save_folder": folder_val,
                        "rom_folder": rom_folder_val,
                        "core": core_val,
                    }
                )
            return {
                **base,
                "save_folder": self.multi_save_folder_edit.text().strip(),
                "systems": systems,
            }
