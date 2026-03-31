"""Settings dialog — server config + emulation path."""

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QFileDialog,
    QSpinBox,
    QFrame,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from . import theme


class SettingsDialog(QDialog):
    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.setMinimumWidth(580)
        self.setStyleSheet(theme.STYLESHEET)

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
            self._emu_path.text() or str(__import__("pathlib").Path.home()),
        )
        if path:
            self._emu_path.setText(path)

    def _browse_rom_path(self):
        path = QFileDialog.getExistingDirectory(
            self,
            "Select ROM Directory",
            self._rom_path.text()
            or self._emu_path.text()
            or str(__import__("pathlib").Path.home()),
        )
        if path:
            self._rom_path.setText(path)

    def get_config(self) -> dict:
        return {
            "host": self._host["edit"].text().strip(),
            "port": self._port_spin.value(),
            "api_key": self._api_key["edit"].text().strip(),
            "emulation_path": self._emu_path.text().strip(),
            "rom_scan_dir": self._rom_path.text().strip(),
        }

    def keyPressEvent(self, event):
        # Let text inputs handle their own keys (Backspace, Enter, etc.)
        focused = self.focusWidget()
        is_editing = isinstance(focused, (QLineEdit, QSpinBox))

        if event.key() == Qt.Key.Key_Escape:
            self.reject()
        elif event.key() == Qt.Key.Key_Backspace and not is_editing:
            self.reject()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not is_editing:
            self.accept()
        else:
            super().keyPressEvent(event)


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
