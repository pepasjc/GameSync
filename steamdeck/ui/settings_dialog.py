"""Settings dialog — server config + emulation path."""

from pathlib import Path

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QFileDialog,
    QSpinBox,
    QFrame,
    QScrollArea,
    QWidget,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from config import (
    SATURN_SYNC_FORMATS,
    normalize_rom_dir_overrides,
    normalize_saturn_sync_format,
)
from saturn_format import SATURN_DOWNLOAD_FORMATS
from scanner.rom_target import SYSTEM_ROM_DIRS, prepare_rom_folders

from . import theme
from .confirm_dialog import ConfirmDialog, ResultDialog
from .gamepad_modal import GamepadModalMixin


class SettingsDialog(QDialog, GamepadModalMixin):
    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.setMinimumWidth(580)
        self.setStyleSheet(theme.STYLESHEET)
        self._init_gamepad_modal()

        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)

        # ── Title ─────────────────────────────────────────────────
        title = QLabel("Settings")
        f = QFont()
        f.setPointSize(16)
        f.setBold(True)
        title.setFont(f)
        layout.addWidget(title)

        layout.addWidget(_separator())

        # ── Server config ─────────────────────────────────────────
        layout.addWidget(_section_label("Server"))

        self._host = _field("Host / IP", config.get("host", ""))
        layout.addLayout(self._host["layout"])

        self._port_spin = QSpinBox()
        self._port_spin.setRange(1, 65535)
        self._port_spin.setValue(config.get("port", 8000))
        self._port_spin.setFixedWidth(100)
        self._port_spin.setStyleSheet(
            f"background:{theme.BG_CARD}; color:{theme.TEXT_PRIMARY}; "
            f"border:1px solid {theme.TEXT_DIM}; border-radius:4px; padding:4px;"
        )
        port_row = QHBoxLayout()
        port_row.addWidget(QLabel("Port"))
        port_row.addWidget(self._port_spin)
        port_row.addStretch()
        layout.addLayout(port_row)

        self._api_key = _field("API Key", config.get("api_key", ""))
        self._api_key["edit"].setEchoMode(QLineEdit.EchoMode.Password)
        layout.addLayout(self._api_key["layout"])

        layout.addWidget(_separator())

        # ── EmuDeck path ──────────────────────────────────────────
        layout.addWidget(_section_label("EmuDeck"))

        path_row = QHBoxLayout()
        self._emu_path = QLineEdit(config.get("emulation_path", ""))
        self._emu_path.setObjectName("searchBox")
        self._emu_path.setPlaceholderText("~/Emulation")
        browse_btn = QPushButton("Browse…")
        browse_btn.setFixedWidth(90)
        browse_btn.clicked.connect(self._browse_path)
        path_row.addWidget(QLabel("Emulation folder"))
        path_row.addWidget(self._emu_path)
        path_row.addWidget(browse_btn)
        layout.addLayout(path_row)

        rom_row = QHBoxLayout()
        self._rom_path = QLineEdit(config.get("rom_scan_dir", ""))
        self._rom_path.setObjectName("searchBox")
        self._rom_path.setPlaceholderText("(optional) Additional ROM directory")
        rom_browse_btn = QPushButton("Browse…")
        rom_browse_btn.setFixedWidth(90)
        rom_browse_btn.clicked.connect(self._browse_rom_path)
        rom_row.addWidget(QLabel("ROM scan dir"))
        rom_row.addWidget(self._rom_path)
        rom_row.addWidget(rom_browse_btn)
        layout.addLayout(rom_row)

        layout.addWidget(_separator())

        # ── Per-system ROM folder overrides ───────────────────────
        layout.addWidget(_section_label("Per-system ROM folders"))

        override_hint = QLabel(
            "Downloads default to the EmuDeck layout "
            "(<Emulation>/roms/<system>/).  Override individual systems "
            "below to pin them to a custom folder — e.g. a separate SD card."
        )
        override_hint.setWordWrap(True)
        override_hint.setStyleSheet(f"color:{theme.TEXT_SECONDARY};")
        layout.addWidget(override_hint)

        self._overrides: dict[str, str] = dict(
            normalize_rom_dir_overrides(config.get("rom_dir_overrides"))
        )
        self._overrides_container = QWidget()
        self._overrides_layout = QVBoxLayout(self._overrides_container)
        self._overrides_layout.setContentsMargins(0, 0, 0, 0)
        self._overrides_layout.setSpacing(4)

        overrides_scroll = QScrollArea()
        overrides_scroll.setWidgetResizable(True)
        overrides_scroll.setWidget(self._overrides_container)
        overrides_scroll.setMinimumHeight(140)
        overrides_scroll.setMaximumHeight(220)
        overrides_scroll.setStyleSheet(
            f"background:{theme.BG_CARD}; border:1px solid {theme.TEXT_DIM}; "
            "border-radius:4px;"
        )
        layout.addWidget(overrides_scroll)

        add_row = QHBoxLayout()
        self._add_system_combo = QComboBox()
        self._add_system_combo.setStyleSheet(
            f"background:{theme.BG_CARD}; color:{theme.TEXT_PRIMARY}; "
            f"border:1px solid {theme.TEXT_DIM}; border-radius:4px; padding:4px;"
        )
        add_btn = QPushButton("Add override…")
        add_btn.setFixedWidth(140)
        add_btn.clicked.connect(self._add_override)
        add_row.addWidget(QLabel("System"))
        add_row.addWidget(self._add_system_combo, 1)
        add_row.addWidget(add_btn)
        layout.addLayout(add_row)

        self._rebuild_override_rows()

        # "Prepare ROM folders" — fill in the canonical layout around
        # whatever the user already has, so catalog downloads stop
        # inventing stray folder names.  Respects per-system overrides.
        prepare_row = QHBoxLayout()
        prepare_btn = QPushButton("Prepare ROM folders")
        prepare_btn.setFixedWidth(200)
        prepare_btn.clicked.connect(self._on_prepare_folders)
        prepare_hint = QLabel(
            "Create the standard per-system folders so every future "
            "download lands in the right place.  Existing folders and "
            "files are never touched."
        )
        prepare_hint.setWordWrap(True)
        prepare_hint.setStyleSheet(f"color:{theme.TEXT_SECONDARY};")
        prepare_row.addWidget(prepare_btn)
        prepare_row.addWidget(prepare_hint, 1)
        layout.addLayout(prepare_row)

        layout.addWidget(_separator())

        # ── Saves ────────────────────────────────────────────────
        layout.addWidget(_section_label("Saves"))

        saturn_row = QHBoxLayout()
        saturn_lbl = QLabel("Saturn format")
        self._saturn_format = QComboBox()
        self._saturn_format.setStyleSheet(
            f"background:{theme.BG_CARD}; color:{theme.TEXT_PRIMARY}; "
            f"border:1px solid {theme.TEXT_DIM}; border-radius:4px; padding:4px;"
        )
        current_format = normalize_saturn_sync_format(config.get("saturn_sync_format"))
        for wire_value in SATURN_SYNC_FORMATS:
            label = SATURN_DOWNLOAD_FORMATS.get(wire_value, (wire_value, ""))[0]
            self._saturn_format.addItem(label, wire_value)
            if wire_value == current_format:
                self._saturn_format.setCurrentIndex(self._saturn_format.count() - 1)
        saturn_row.addWidget(saturn_lbl)
        saturn_row.addWidget(self._saturn_format, 1)
        layout.addLayout(saturn_row)

        saturn_hint = QLabel(
            "Server stays on Beetle/Mednafen canonical; this controls which "
            "emulator's Saturn saves get read and written locally."
        )
        saturn_hint.setWordWrap(True)
        saturn_hint.setStyleSheet(f"color:{theme.TEXT_SECONDARY};")
        layout.addWidget(saturn_hint)

        layout.addWidget(_separator())
        layout.addStretch()

        # ── Buttons ───────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel  [B]")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("Save  [A]")
        save_btn.clicked.connect(self.accept)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    def _browse_path(self):
        path = QFileDialog.getExistingDirectory(
            self,
            "Select Emulation Folder",
            self._emu_path.text() or str(Path.home()),
        )
        if path:
            self._emu_path.setText(path)

    def _browse_rom_path(self):
        path = QFileDialog.getExistingDirectory(
            self,
            "Select ROM Directory",
            self._rom_path.text()
            or self._emu_path.text()
            or str(Path.home()),
        )
        if path:
            self._rom_path.setText(path)

    def _current_roms_base(self) -> Path | None:
        """Resolve the current ROMs root from the live field values.

        Mirrors ``MainWindow._rom_roots_base`` so the prepared folders
        land in exactly the directory downloads would target — even when
        the user has edited paths but hasn't saved yet.  Returns ``None``
        if neither field is filled in.
        """
        rom_scan = self._rom_path.text().strip()
        emu = self._emu_path.text().strip()
        if rom_scan:
            return Path(rom_scan).expanduser()
        if emu:
            return Path(emu).expanduser() / "roms"
        return None

    def _on_prepare_folders(self) -> None:
        """Create the canonical per-system ROM folders under the current base."""
        base = self._current_roms_base()
        if base is None:
            ResultDialog(
                False,
                "Set an Emulation folder or ROM scan directory first, "
                "then try again.",
                parent=self,
            ).exec()
            return

        # Honour whatever the user is about to save — not what's on disk.
        # Reuse ``normalize_rom_dir_overrides`` so empty / malformed
        # entries behave exactly like they do in the download path.
        overrides = normalize_rom_dir_overrides(self._overrides)

        # Dry-run first: count what *would* be created so the confirm
        # dialog can show the real blast radius before touching disk.
        from scanner.rom_target import resolve_rom_target_dir

        systems = set(SYSTEM_ROM_DIRS.keys()) | set(overrides.keys())
        planned_create = []
        planned_skip = 0
        for system in sorted(systems):
            target = resolve_rom_target_dir(base, system, overrides)
            if target.is_dir():
                planned_skip += 1
            else:
                planned_create.append(target)

        if not planned_create:
            ResultDialog(
                True,
                f"All {planned_skip} system folders already exist under "
                f"{base}.\nNothing to do.",
                parent=self,
            ).exec()
            return

        preview = "\n".join(f"  • {p}" for p in planned_create[:10])
        if len(planned_create) > 10:
            preview += f"\n  … and {len(planned_create) - 10} more"
        msg = (
            f"Create {len(planned_create)} system folder(s) under\n"
            f"{base}?\n\n"
            f"{preview}\n\n"
            f"{planned_skip} folder(s) already exist and will be left alone.\n"
            "Existing files are never touched."
        )
        confirm = ConfirmDialog(
            title="Prepare ROM folders",
            message=msg,
            confirm_label="Create",
            confirm_color=theme.STATUS_DOWNLOAD,
            parent=self,
        )
        if confirm.exec() != confirm.DialogCode.Accepted:
            return

        report = prepare_rom_folders(base, overrides)
        if report.errors:
            err_txt = "\n".join(
                f"  • {sys}: {msg}" for sys, msg in report.errors[:5]
            )
            ResultDialog(
                report.created_count > 0,
                f"Created {report.created_count} folder(s); "
                f"{len(report.errors)} failed:\n{err_txt}",
                parent=self,
            ).exec()
        else:
            ResultDialog(
                True,
                f"Created {report.created_count} folder(s) under {base}.",
                parent=self,
            ).exec()

    def _override_picker_start(self, system: str) -> str:
        """Reasonable starting folder for a per-system browse dialog."""
        current = self._overrides.get(system, "").strip()
        if current:
            return current
        rom_base = self._rom_path.text().strip() or (
            f"{self._emu_path.text().strip()}/roms"
            if self._emu_path.text().strip()
            else ""
        )
        if rom_base:
            candidates = SYSTEM_ROM_DIRS.get(system, [system.lower()])
            existing = next(
                (c for c in candidates if (Path(rom_base) / c).is_dir()),
                None,
            )
            if existing:
                return str(Path(rom_base) / existing)
            return rom_base
        return str(Path.home())

    def _refresh_add_combo(self) -> None:
        self._add_system_combo.blockSignals(True)
        self._add_system_combo.clear()
        for system in sorted(SYSTEM_ROM_DIRS.keys()):
            if system in self._overrides:
                continue
            self._add_system_combo.addItem(system, system)
        self._add_system_combo.blockSignals(False)

    def _rebuild_override_rows(self) -> None:
        while self._overrides_layout.count():
            item = self._overrides_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        if not self._overrides:
            empty = QLabel("No overrides — all systems use the default EmuDeck layout.")
            empty.setStyleSheet(f"color:{theme.TEXT_DIM}; padding:8px;")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._overrides_layout.addWidget(empty)
        else:
            for system in sorted(self._overrides.keys()):
                self._overrides_layout.addWidget(self._build_override_row(system))
            self._overrides_layout.addStretch()

        self._refresh_add_combo()

    def _build_override_row(self, system: str) -> QWidget:
        row_widget = QWidget()
        row = QHBoxLayout(row_widget)
        row.setContentsMargins(4, 2, 4, 2)

        sys_lbl = QLabel(system)
        sys_lbl.setFixedWidth(80)
        sys_f = QFont()
        sys_f.setBold(True)
        sys_lbl.setFont(sys_f)
        sys_lbl.setStyleSheet(f"color:{theme.ACCENT};")

        path_edit = QLineEdit(self._overrides.get(system, ""))
        path_edit.setObjectName("searchBox")
        path_edit.textChanged.connect(
            lambda text, s=system: self._overrides.__setitem__(s, text.strip())
        )

        browse_btn = QPushButton("Browse…")
        browse_btn.setFixedWidth(90)
        browse_btn.clicked.connect(
            lambda _checked=False, s=system, e=path_edit: self._browse_override(s, e)
        )

        clear_btn = QPushButton("Remove")
        clear_btn.setFixedWidth(90)
        clear_btn.clicked.connect(lambda _checked=False, s=system: self._remove_override(s))

        row.addWidget(sys_lbl)
        row.addWidget(path_edit, 1)
        row.addWidget(browse_btn)
        row.addWidget(clear_btn)
        return row_widget

    def _browse_override(self, system: str, edit: QLineEdit) -> None:
        path = QFileDialog.getExistingDirectory(
            self,
            f"Select ROM folder for {system}",
            self._override_picker_start(system),
        )
        if path:
            edit.setText(path)
            self._overrides[system] = path

    def _add_override(self) -> None:
        system = self._add_system_combo.currentData()
        if not system or system in self._overrides:
            return
        path = QFileDialog.getExistingDirectory(
            self,
            f"Select ROM folder for {system}",
            self._override_picker_start(system),
        )
        if not path:
            return
        self._overrides[system] = path
        self._rebuild_override_rows()

    def _remove_override(self, system: str) -> None:
        self._overrides.pop(system, None)
        self._rebuild_override_rows()

    def get_config(self) -> dict:
        saturn_format = self._saturn_format.currentData()
        return {
            "host": self._host["edit"].text().strip(),
            "port": self._port_spin.value(),
            "api_key": self._api_key["edit"].text().strip(),
            "emulation_path": self._emu_path.text().strip(),
            "rom_scan_dir": self._rom_path.text().strip(),
            "rom_dir_overrides": normalize_rom_dir_overrides(self._overrides),
            "saturn_sync_format": normalize_saturn_sync_format(saturn_format),
        }

    def keyPressEvent(self, event):
        if self.handle_gamepad_key(event.key()):
            return
        super().keyPressEvent(event)

    def handle_gamepad_key(self, key: int) -> bool:
        # Let text inputs handle their own keys (Backspace, Enter, etc.)
        focused = self.focusWidget()
        is_editing = isinstance(focused, (QLineEdit, QSpinBox, QComboBox))

        if key in (Qt.Key.Key_Escape, Qt.Key.Key_B) and not is_editing:
            self.reject()
            return True
        if key == Qt.Key.Key_Backspace and not is_editing:
            self.reject()
            return True
        if key in (
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
            Qt.Key.Key_A,
        ) and not is_editing:
            self.accept()
            return True
        return False


def _field(label: str, value: str) -> dict:
    row = QHBoxLayout()
    lbl = QLabel(label)
    lbl.setFixedWidth(90)
    edit = QLineEdit(value)
    edit.setObjectName("searchBox")
    row.addWidget(lbl)
    row.addWidget(edit)
    return {"layout": row, "edit": edit}


def _section_label(text: str) -> QLabel:
    lbl = QLabel(text)
    f = QFont()
    f.setPointSize(12)
    f.setBold(True)
    lbl.setFont(f)
    lbl.setStyleSheet(f"color: {theme.ACCENT};")
    return lbl


def _separator() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet(f"color: {theme.TEXT_DIM};")
    return line
