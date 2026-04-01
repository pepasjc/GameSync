"""Gamepad-friendly confirmation dialog for critical sync operations."""

from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QKeyEvent

from . import theme


class ConfirmDialog(QDialog):
    """
    Modal confirmation dialog styled for gamepad use.

    Shows a title, message, and two buttons: Confirm (A) and Cancel (B).
    Returns QDialog.DialogCode.Accepted or Rejected.
    """

    def __init__(
        self,
        title: str,
        message: str,
        confirm_label: str = "Confirm",
        cancel_label: str = "Cancel",
        confirm_color: str = theme.ACCENT,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(480)
        self.setMaximumWidth(640)
        self.setStyleSheet(theme.STYLESHEET)

        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(28, 24, 28, 20)

        # Title
        title_lbl = QLabel(title)
        tf = QFont()
        tf.setPointSize(15)
        tf.setBold(True)
        title_lbl.setFont(tf)
        layout.addWidget(title_lbl)

        # Message
        msg_lbl = QLabel(message)
        msg_lbl.setWordWrap(True)
        msg_lbl.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 12pt;")
        layout.addWidget(msg_lbl)

        layout.addSpacing(8)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self._cancel_btn = QPushButton(f"{cancel_label}  [B]")
        self._cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._cancel_btn)

        btn_row.addSpacing(12)

        self._confirm_btn = QPushButton(f"{confirm_label}  [A]")
        self._confirm_btn.setStyleSheet(
            f"QPushButton {{ background: {confirm_color}; color: #fff; "
            f"border: none; font-weight: bold; }} "
            f"QPushButton:hover {{ opacity: 0.9; }} "
            f"QPushButton:pressed {{ opacity: 0.7; }}"
        )
        self._confirm_btn.clicked.connect(self.accept)
        self._confirm_btn.setDefault(True)
        btn_row.addWidget(self._confirm_btn)

        layout.addLayout(btn_row)

    def keyPressEvent(self, event: QKeyEvent):
        if self.handle_gamepad_key(event.key()):
            return
        super().keyPressEvent(event)

    def handle_gamepad_key(self, key: int) -> bool:
        if key in (Qt.Key.Key_Return, Qt.Key.Key_A):
            self.accept()
            return True
        if key in (Qt.Key.Key_Escape, Qt.Key.Key_B, Qt.Key.Key_Backspace):
            self.reject()
            return True
        return False


class ResultDialog(QDialog):
    """Brief result notification after an action completes."""

    def __init__(
        self,
        success: bool,
        message: str,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Result")
        self.setModal(True)
        self.setMinimumWidth(400)
        self.setStyleSheet(theme.STYLESHEET)

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(28, 24, 28, 20)

        color = theme.STATUS_SYNCED if success else theme.STATUS_CONFLICT
        icon = "OK" if success else "FAILED"

        header = QLabel(icon)
        header.setStyleSheet(f"color: {color}; font-size: 16pt; font-weight: bold;")
        layout.addWidget(header)

        msg_lbl = QLabel(message)
        msg_lbl.setWordWrap(True)
        msg_lbl.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 12pt;")
        layout.addWidget(msg_lbl)

        layout.addSpacing(4)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("OK  [A]")
        ok_btn.clicked.connect(self.accept)
        ok_btn.setDefault(True)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

    def keyPressEvent(self, event: QKeyEvent):
        if self.handle_gamepad_key(event.key()):
            return
        super().keyPressEvent(event)

    def handle_gamepad_key(self, key: int) -> bool:
        if key in (
            Qt.Key.Key_Return,
            Qt.Key.Key_A,
            Qt.Key.Key_B,
            Qt.Key.Key_Escape,
            Qt.Key.Key_Backspace,
        ):
            self.accept()
            return True
        return False
