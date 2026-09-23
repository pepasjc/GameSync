import re
from pathlib import Path

from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
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
    QSpinBox,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import Qt

from config import DEVICE_TYPES, SYSTEM_CHOICES
from rom_installer import ROM_FORMAT_LABELS, ROM_FORMAT_OPTIONS
from systems import SAVE_EXT_CHOICES, SYSTEM_DEFAULT_SAVE_EXT


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SINGLE_SYSTEM_DEVICES = {
    "Generic",
    "Everdrive",
    "MEGA EverDrive",
    "SAROO",
    "MemCard Pro",
    "MemCard Pro FTP",
    "MemCard Pro DC",
    "PSIO",
    "CD Folder",
    "OPL",
    "GDEMU",
    "openMenu",
}
MEMCARD_PRO_SYSTEMS = ["PS1", "PS2", "GC", "DC"]
MEMCARD_PRO_FTP_SYSTEMS = ["PS1", "PS2", "GC"]
# Dreamcast-only profiles: the two ODE menus (GDEMU / openMenu) and the
# Dreamcast card manager.  All three are keyed by the disc's Game ID.
DREAMCAST_DEVICES = {"GDEMU", "openMenu", "MemCard Pro DC"}

# MiSTer ROM install destination.  "local" is the classic mounted-card mode
# (Game Folder path on this PC); "sd"/"usb" install over the network via the
# MiSTer tab's SSH connection into the MiSTer's own filesystem.
MISTER_TARGET_OPTIONS: list[tuple[str, str]] = [
    ("local", "Local folder (mounted SD card)"),
    ("sd", "MiSTer over network — SD card (/media/fat/games)"),
    ("usb", "MiSTer over network — USB drive (/media/usb0/games)"),
]

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
        "PCECD",
        "A2600",
        "A7800",
        "LYNX",
        "NEOGEO",
        "NEOCD",
        "NGP",
        "NGPC",
        "WSWAN",
        "WSWANC",
        "32X",
        "SEGACD",
        "SAT",
        "PS1",
        "3DO",
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
    # Super SD System 3 (TerraOnion PC Engine ODE): HuCard/ holds cartridge
    # dumps, Cd/<Game>/ holds CUE/BIN discs, bup/ holds every save.
    "Super SD System 3": ["PCECD", "PCE", "PCSG"],
}

# Default save extension per multi-system device type
DEVICE_DEFAULT_EXT: dict[str, str] = {
    "MiSTer": ".sav",
    "RetroArch": ".srm",
    "Analogue Pocket": ".sav",
    "Pocket (openFPGA)": ".sav",
    "EmuDeck": ".srm",
    "Super SD System 3": ".bup",
}

SAVE_EXT_OPTIONS = SAVE_EXT_CHOICES   # UI save-extension dropdown list
SYSTEM_DEFAULT_EXT = SYSTEM_DEFAULT_SAVE_EXT  # per-system save extension override

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


def _path_basename(path: str) -> str:
    """Return the last component of a path, handling both / and \\ separators."""
    return re.split(r"[/\\]", (path or "").rstrip("/\\"))[-1].strip().lower()


def _retroarch_saturn_display_value(save_ext: str, save_folder: str = "") -> str:
    ext = (save_ext or "").strip().lower()
    folder_name = _path_basename(save_folder)
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
    folder_name = _path_basename(folder)

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


class RomFormatDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        combo = QComboBox(parent)
        combo.addItems([label for _value, label in ROM_FORMAT_OPTIONS])
        return combo

    def setEditorData(self, editor, index):
        editor.setCurrentText(index.data() or "Auto")

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText().strip() or "Auto")

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

        self.mister_target_combo = QComboBox()
        for value, label in MISTER_TARGET_OPTIONS:
            self.mister_target_combo.addItem(label, value)
        self.mister_target_combo.setToolTip(
            "Where ROMs from the server catalog are installed.\n"
            "Local folder: writes into the Game Folder path below (card in a reader).\n"
            "Network targets: uploads over SSH using the connection saved in the\n"
            "MiSTer tab; per-system folders (PSX, Saturn, MegaCD, ...) are created\n"
            "automatically under the chosen games root."
        )
        self.mister_target_combo.currentIndexChanged.connect(
            lambda _idx: self._on_mister_target_changed()
        )
        self._mister_target_label = QLabel("Install To:")
        form.addRow(self._mister_target_label, self.mister_target_combo)

        self.ftp_host_edit = QLineEdit()
        self.ftp_host_edit.setPlaceholderText("MemCard PRO IP address or hostname")
        self._ftp_host_label = QLabel("FTP Host:")
        form.addRow(self._ftp_host_label, self.ftp_host_edit)

        self.ftp_port_spin = QSpinBox()
        self.ftp_port_spin.setRange(1, 65535)
        self.ftp_port_spin.setValue(21)
        self._ftp_port_label = QLabel("FTP Port:")
        form.addRow(self._ftp_port_label, self.ftp_port_spin)

        self.ftp_username_edit = QLineEdit()
        self.ftp_username_edit.setPlaceholderText("Required — set in MemCard PRO WebUI")
        self._ftp_username_label = QLabel("FTP Username:")
        form.addRow(self._ftp_username_label, self.ftp_username_edit)

        self.ftp_password_edit = QLineEdit()
        self.ftp_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.ftp_password_edit.setPlaceholderText("Required — set in MemCard PRO WebUI")
        self._ftp_password_label = QLabel("FTP Password:")
        form.addRow(self._ftp_password_label, self.ftp_password_edit)

        # ── MiSTer SSH connection (per-profile) ────────────────────────
        self.ssh_host_edit = QLineEdit()
        self.ssh_host_edit.setPlaceholderText("MiSTer IP or hostname (e.g. 192.168.1.41)")
        self._ssh_host_label = QLabel("SSH Host:")
        form.addRow(self._ssh_host_label, self.ssh_host_edit)

        self.ssh_port_spin = QSpinBox()
        self.ssh_port_spin.setRange(1, 65535)
        self.ssh_port_spin.setValue(22)
        self._ssh_port_label = QLabel("SSH Port:")
        form.addRow(self._ssh_port_label, self.ssh_port_spin)

        self.ssh_username_edit = QLineEdit()
        self.ssh_username_edit.setPlaceholderText("root (MiSTer default)")
        self._ssh_username_label = QLabel("SSH User:")
        form.addRow(self._ssh_username_label, self.ssh_username_edit)

        self.ssh_password_edit = QLineEdit()
        self.ssh_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.ssh_password_edit.setPlaceholderText("1 (MiSTer default)")
        self._ssh_password_label = QLabel("SSH Password:")
        form.addRow(self._ssh_password_label, self.ssh_password_edit)

        self.ssh_key_edit = QLineEdit()
        self.ssh_key_edit.setPlaceholderText("Optional — path to a private key file")
        self._ssh_key_label = QLabel("SSH Key:")
        form.addRow(self._ssh_key_label, self.ssh_key_edit)

        game_row = QWidget()
        game_layout = QHBoxLayout(game_row)
        game_layout.setContentsMargins(0, 0, 0, 0)
        self.game_folder_edit = QLineEdit()
        self.game_folder_edit.setPlaceholderText("Root game / ROM folder...")
        self.browse_game_btn = QPushButton("Browse...")
        self.browse_game_btn.clicked.connect(self._browse_game_folder)
        game_layout.addWidget(self.game_folder_edit)
        game_layout.addWidget(self.browse_game_btn)
        self._game_folder_label = QLabel("Game Folder:")
        form.addRow(self._game_folder_label, game_row)

        self.sd_card_check = QCheckBox("This is an SD card (remap drive letter)")
        self.sd_card_check.setToolTip(
            "When checked, the drive letter of every path in this profile is\n"
            "rewritten to the \"Current SD Card Drive\" set in Server Configuration\n"
            "at sync/install time. Use this when your card reader gets a different\n"
            "drive letter on each reconnect."
        )
        self._sd_card_label = QLabel("Removable:")
        form.addRow(self._sd_card_label, self.sd_card_check)

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

        self.rom_format_combo = QComboBox()
        self.rom_format_combo.addItems([label for _value, label in ROM_FORMAT_OPTIONS])
        self.rom_format_combo.setCurrentText("Auto")
        layout.addRow("ROM Install Format:", self.rom_format_combo)

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
        self._single_save_row_label = QLabel("Save Folder:")
        self._single_save_row_widget = sep_row
        layout.addRow(self._single_save_row_label, self._single_save_row_widget)

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
        self.systems_table = QTableWidget(0, 6)
        self.systems_table.setHorizontalHeaderLabels(
            [
                "",
                "System",
                "Save Format",
                "ROM Format",
                "Save Folder Override",
                "ROM Folder Override",
            ]
        )
        hdr = self.systems_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.systems_table.setColumnWidth(0, 28)
        self.systems_table.setColumnWidth(1, 90)
        self.systems_table.setColumnWidth(2, 170)
        self.systems_table.setColumnWidth(3, 150)
        self.systems_table.setItemDelegateForColumn(2, SaveExtDelegate(self, self))
        self.systems_table.setItemDelegateForColumn(3, RomFormatDelegate(self))
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
        is_memcard_ftp = device_type == "MemCard Pro FTP"
        is_psio = device_type == "PSIO"
        is_saroo = device_type == "SAROO"
        is_opl = device_type == "OPL"
        is_supersd3 = device_type == "Super SD System 3"
        is_mister = device_type == "MiSTer"
        is_gdemu = device_type == "GDEMU"
        is_openmenu = device_type == "openMenu"
        is_memcard_dc = device_type == "MemCard Pro DC"
        self._mister_target_label.setVisible(is_mister)
        self.mister_target_combo.setVisible(is_mister)
        for widget in (
            self._ssh_host_label,
            self.ssh_host_edit,
            self._ssh_port_label,
            self.ssh_port_spin,
            self._ssh_username_label,
            self.ssh_username_edit,
            self._ssh_password_label,
            self.ssh_password_edit,
            self._ssh_key_label,
            self.ssh_key_edit,
        ):
            widget.setVisible(is_mister)
        if is_mister and not self.ssh_host_edit.text().strip():
            self._prefill_mister_ssh_defaults()
        self._set_single_system_choices(device_type)
        for widget in (
            self._ftp_host_label,
            self.ftp_host_edit,
            self._ftp_port_label,
            self.ftp_port_spin,
            self._ftp_username_label,
            self.ftp_username_edit,
            self._ftp_password_label,
            self.ftp_password_edit,
        ):
            widget.setVisible(is_memcard_ftp)
        self.browse_game_btn.setVisible(not is_memcard_ftp)
        self._sd_card_label.setVisible(not is_memcard_ftp)
        self.sd_card_check.setVisible(not is_memcard_ftp)
        # Show Redump DAT field only for CD Folder profiles
        is_cd = device_type == "CD Folder"
        self._dat_row_label.setVisible(is_cd)
        self._dat_row_widget.setVisible(is_cd)
        folder_label = "Game Folder:"
        if is_memcard_ftp:
            folder_label = "Remote Root:"
        elif is_memcard:
            folder_label = "Root Folder:"
        elif is_psio or is_supersd3:
            folder_label = "SD Card Root:"
        elif is_opl:
            folder_label = "USB Root:"
        elif is_gdemu or is_openmenu:
            folder_label = "GDEMU SD Root:"
        elif is_memcard_dc:
            folder_label = "Root Folder:"
        self._game_folder_label.setText(folder_label)
        self.multi_save_folder_edit.setPlaceholderText(
            "Leave empty — saves always live in <root>/bup"
            if is_supersd3
            else "Leave empty — saves co-located with game folder"
        )
        self._single_save_row_label.setVisible(not is_memcard_ftp)
        self._single_save_row_widget.setVisible(not is_memcard_ftp)
        if is_memcard_ftp:
            self.game_folder_edit.setPlaceholderText(
                "/, /MemoryCards, /PS2, or a system-specific remote folder"
            )
        elif is_memcard:
            self.game_folder_edit.setPlaceholderText(
                "MemCard Pro root folder or MemoryCards folder..."
            )
            self.single_save_folder_edit.setPlaceholderText(
                "Leave empty — not used for MemCard Pro"
            )
        elif is_psio:
            self.game_folder_edit.setPlaceholderText("PSIO SD card root folder...")
            self.single_save_folder_edit.setPlaceholderText(
                "Memory card folder (optional)"
            )
        elif is_saroo:
            self.game_folder_edit.setPlaceholderText(
                "Saroo SD card root folder (contains SS_SAVE.BIN)…"
            )
            self.single_save_folder_edit.setPlaceholderText(
                "Mednafen save folder (optional — for emulator sync)"
            )
        elif is_supersd3:
            self.game_folder_edit.setPlaceholderText(
                "Super SD System 3 SD card root (contains HuCard/, Cd/, bup/)…"
            )
        elif is_opl:
            self.game_folder_edit.setPlaceholderText(
                "OPL USB root (DVD/, CD/ and POPS/ created here)…"
            )
            self.single_save_folder_edit.setPlaceholderText(
                "Leave empty — OPL VMCs managed separately"
            )
        elif is_gdemu:
            self.game_folder_edit.setPlaceholderText(
                "GDEMU SD card root (01/ holds the menu, games go in 02/, 03/, …)"
            )
            self.single_save_folder_edit.setPlaceholderText(
                "Leave empty — GDEMU stores no saves; sync them with an "
                "openMenu or MemCard PRO DC profile"
            )
        elif is_openmenu:
            self.game_folder_edit.setPlaceholderText(
                "GDEMU SD card root (01/ holds the openMenu disc)…"
            )
            self.single_save_folder_edit.setPlaceholderText(
                "Serial SD adapter root (holds OPENMENU/SAVES/) — required to "
                "sync Serial VMUs"
            )
        elif is_memcard_dc:
            self.game_folder_edit.setPlaceholderText(
                "MemCard PRO DC microSD root (holds Dreamcast/)…"
            )
            self.single_save_folder_edit.setPlaceholderText(
                "Leave empty — not used for MemCard PRO DC"
            )
        else:
            self.game_folder_edit.setPlaceholderText("Root game / ROM folder...")
            self.single_save_folder_edit.setPlaceholderText(
                "Leave empty — saves are in same folder as games"
            )

        # Card-manager and CD-folder profiles default to PS1 + .mcd.
        if (is_cd or is_memcard or is_memcard_ftp or is_psio) and not self._loading:
            self._apply_single_system_defaults("PS1")
            if is_psio:
                fmt_label = ROM_FORMAT_LABELS.get("psio", "PSIO BIN/CU2")
                idx = self.rom_format_combo.findText(fmt_label)
                if idx >= 0:
                    self.rom_format_combo.setCurrentIndex(idx)

        # OPL defaults to PS2; Auto resolves to ISO (PS2) / VCD (PS1).
        if is_opl and not self._loading:
            self._apply_single_system_defaults("PS2")
            idx = self.rom_format_combo.findText(
                ROM_FORMAT_LABELS.get("auto", "Auto")
            )
            if idx >= 0:
                self.rom_format_combo.setCurrentIndex(idx)

        # SAROO is always SAT; lock system and hide save-ext picker
        if is_saroo and not self._loading:
            self._apply_single_system_defaults("SAT")

        # GDEMU / openMenu / MemCard PRO DC are Dreamcast-only.  Auto resolves
        # to GDI for the ODEs (GDEMU cannot read CHD).
        if device_type in DREAMCAST_DEVICES and not self._loading:
            self._apply_single_system_defaults("DC")
            idx = self.rom_format_combo.findText(
                ROM_FORMAT_LABELS.get("auto", "Auto")
            )
            if idx >= 0:
                self.rom_format_combo.setCurrentIndex(idx)

        self._on_mister_target_changed()

        if is_single:
            self.setMinimumSize(480, 0)
        else:
            self.setMinimumSize(580, 520)
            self.systems_table.setHorizontalHeaderLabels(
                [
                    "",
                    "System",
                    "Core" if device_type == "RetroArch" else "Save Format",
                    "ROM Format",
                    "Save Folder Override",
                    "ROM Folder Override",
                ]
            )
            self._populate_systems_table(device_type)
        self.adjustSize()

    def _set_single_system_choices(self, device_type: str) -> None:
        """Restrict the system picker for device-specific single-system profiles."""
        current = self.system_combo.currentText()
        if device_type == "MemCard Pro FTP":
            choices = MEMCARD_PRO_FTP_SYSTEMS
        elif device_type == "MemCard Pro":
            choices = MEMCARD_PRO_SYSTEMS
        elif device_type == "PSIO":
            choices = ["PS1"]
        elif device_type == "OPL":
            choices = ["PS2", "PS1"]
        elif device_type in DREAMCAST_DEVICES:
            choices = ["DC"]
        else:
            choices = SYSTEM_CHOICES
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
            # MemCard PRO DC and openMenu both store bare 128 KB VMU images.
            "DC": ".vmu",
            "SAT": ".bkr",
        }.get(system)
        if default_ext:
            ext_idx = self.save_ext_combo.findText(default_ext)
            if ext_idx >= 0:
                self.save_ext_combo.setCurrentIndex(ext_idx)
            else:
                self.save_ext_combo.setCurrentText(default_ext)

    def _prefill_mister_ssh_defaults(self) -> None:
        """Seed empty SSH fields from the legacy global ``mister_ssh`` config
        (the retired MiSTer SSH tab stored the connection there)."""
        from config import load_config

        cfg = load_config().get("mister_ssh") or {}
        if cfg.get("host"):
            self.ssh_host_edit.setText(str(cfg.get("host", "")))
            self.ssh_port_spin.setValue(int(cfg.get("port", 22) or 22))
            self.ssh_username_edit.setText(str(cfg.get("username", "root") or "root"))
            self.ssh_password_edit.setText(str(cfg.get("password", "") or ""))
            self.ssh_key_edit.setText(str(cfg.get("key_path", "") or ""))

    def _mister_target(self) -> str:
        return str(self.mister_target_combo.currentData() or "local")

    def _mister_network_install(self) -> bool:
        return (
            self.device_combo.currentText() == "MiSTer"
            and self._mister_target() in {"sd", "usb"}
        )

    def _on_mister_target_changed(self):
        if self.device_combo.currentText() != "MiSTer":
            return
        if self._mister_network_install():
            self.game_folder_edit.setPlaceholderText(
                "Optional — ROM installs go over the network; set this only if\n"
                "you also sync saves/ROMs from a locally mounted card"
            )
        else:
            self.game_folder_edit.setPlaceholderText("Root game / ROM folder...")

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

            # Col 3 — ROM install format
            rom_format = str(info.get("rom_format", "auto") or "auto").lower()
            fmt_item = QTableWidgetItem(ROM_FORMAT_LABELS.get(rom_format, "Auto"))
            fmt_item.setFlags(ro_flags | Qt.ItemFlag.ItemIsEditable)
            fmt_item.setToolTip(
                "Format requested when installing ROMs from the server catalog.\n"
                "Auto uses the built-in default for this profile and system."
            )
            self.systems_table.setItem(row, 3, fmt_item)

            # Col 4 — save folder override (editable text, empty = use global)
            folder_item = QTableWidgetItem(info.get("save_folder", ""))
            folder_item.setFlags(ro_flags | Qt.ItemFlag.ItemIsEditable)
            folder_item.setToolTip(
                "Override save folder for this specific system.\n"
                "Leave empty to auto-compute from the save root above."
            )
            self.systems_table.setItem(row, 4, folder_item)

            rom_folder_item = QTableWidgetItem(info.get("rom_folder", ""))
            rom_folder_item.setFlags(ro_flags | Qt.ItemFlag.ItemIsEditable)
            rom_folder_item.setToolTip(
                "Optional ROM folder override for this specific system.\n"
                "Leave empty to use the profile's global Game Folder."
            )
            self.systems_table.setItem(row, 5, rom_folder_item)

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
            item = self.systems_table.item(row, 4)
            if item:
                item.setText(folder)

    def _clear_save_override_folder(self):
        row = self._selected_row()
        if row < 0:
            return
        item = self.systems_table.item(row, 4)
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
            item = self.systems_table.item(row, 5)
            if item:
                item.setText(folder)

    def _clear_rom_override_folder(self):
        row = self._selected_row()
        if row < 0:
            return
        item = self.systems_table.item(row, 5)
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
            self.sd_card_check.setChecked(bool(profile.get("is_sd_card", False)))
            target_idx = self.mister_target_combo.findData(
                str(profile.get("mister_target", "local") or "local")
            )
            if target_idx >= 0:
                self.mister_target_combo.setCurrentIndex(target_idx)
            if profile.get("ssh_host"):
                self.ssh_host_edit.setText(str(profile.get("ssh_host", "")))
                self.ssh_port_spin.setValue(int(profile.get("ssh_port", 22) or 22))
                self.ssh_username_edit.setText(
                    str(profile.get("ssh_username", "root") or "root")
                )
                self.ssh_password_edit.setText(str(profile.get("ssh_password", "")))
                self.ssh_key_edit.setText(str(profile.get("ssh_key_path", "")))
            elif profile.get("device_type") == "MiSTer":
                self._prefill_mister_ssh_defaults()
            self.ftp_host_edit.setText(profile.get("ftp_host", ""))
            self.ftp_port_spin.setValue(int(profile.get("ftp_port", 21) or 21))
            self.ftp_username_edit.setText(profile.get("ftp_username", ""))
            self.ftp_password_edit.setText(profile.get("ftp_password", ""))

            device_type = profile.get("device_type", "Generic")

            if device_type in SINGLE_SYSTEM_DEVICES:
                idx = self.system_combo.findText(profile.get("system", "GBA"))
                if idx >= 0:
                    self.system_combo.setCurrentIndex(idx)
                self.save_ext_combo.setCurrentText(profile.get("save_ext", ".sav"))
                rom_format = str(profile.get("rom_format", "auto") or "auto").lower()
                self.rom_format_combo.setCurrentText(
                    ROM_FORMAT_LABELS.get(rom_format, "Auto")
                )
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
        device_type = self.device_combo.currentText()
        if device_type == "MemCard Pro FTP":
            if not self.ftp_host_edit.text().strip():
                QMessageBox.warning(self, "Validation", "FTP host is required.")
                return
            if not self.ftp_username_edit.text().strip():
                QMessageBox.warning(self, "Validation", "FTP username is required.")
                return
            if not self.ftp_password_edit.text():
                QMessageBox.warning(self, "Validation", "FTP password is required.")
                return
            if not self.game_folder_edit.text().strip():
                self.game_folder_edit.setText("/")
            self.accept()
            return
        if device_type == "MiSTer" and self._mister_network_install():
            if not self.ssh_host_edit.text().strip():
                QMessageBox.warning(
                    self,
                    "Validation",
                    "SSH host is required for a network install target.",
                )
                return
        if not self.game_folder_edit.text().strip():
            # MiSTer with SSH configured needs no local path — saves sync and
            # ROM installs both go over the network.
            if device_type == "MiSTer" and self.ssh_host_edit.text().strip():
                self.accept()
                return
            if device_type in {"MemCard Pro", "MemCard Pro DC"}:
                message = "Root folder path is required."
            elif device_type in {"GDEMU", "openMenu"}:
                message = "GDEMU SD card root is required."
            else:
                message = "Game folder path is required."
            QMessageBox.warning(self, "Validation", message)
            return
        self.accept()

    def _rom_format_value(self, label_or_value: str) -> str:
        text = (label_or_value or "Auto").strip()
        for value, label in ROM_FORMAT_OPTIONS:
            if text == label or text.lower() == value:
                return value
        return "auto"

    def get_profile(self) -> dict:
        device_type = self.device_combo.currentText()

        base = {
            "name": self.name_edit.text().strip(),
            "device_type": device_type,
            "path": self.game_folder_edit.text().strip()
            or ("/" if device_type == "MemCard Pro FTP" else ""),
        }
        if device_type != "MemCard Pro FTP" and self.sd_card_check.isChecked():
            base["is_sd_card"] = True
        if device_type == "MiSTer":
            base["mister_target"] = self._mister_target()
            base["ssh_host"] = self.ssh_host_edit.text().strip()
            base["ssh_port"] = self.ssh_port_spin.value()
            base["ssh_username"] = self.ssh_username_edit.text().strip() or "root"
            base["ssh_password"] = self.ssh_password_edit.text()
            base["ssh_key_path"] = self.ssh_key_edit.text().strip()
        if device_type == "MemCard Pro FTP":
            base.update(
                {
                    "ftp_host": self.ftp_host_edit.text().strip(),
                    "ftp_port": self.ftp_port_spin.value(),
                    "ftp_username": self.ftp_username_edit.text().strip(),
                    "ftp_password": self.ftp_password_edit.text(),
                    "ftp_passive": True,
                }
            )

        if device_type in SINGLE_SYSTEM_DEVICES:
            ext = self.save_ext_combo.currentText().strip()
            if not ext.startswith("."):
                ext = "." + ext
            result = {
                **base,
                "save_folder": self.single_save_folder_edit.text().strip(),
                "system": self.system_combo.currentText(),
                "save_ext": ext or ".sav",
                "rom_format": self._rom_format_value(
                    self.rom_format_combo.currentText()
                ),
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
                rom_fmt = self.systems_table.item(row, 3)
                fld = self.systems_table.item(row, 4)
                rom_fld = self.systems_table.item(row, 5)
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
                        "rom_format": self._rom_format_value(
                            rom_fmt.text().strip() if rom_fmt else "Auto"
                        ),
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
