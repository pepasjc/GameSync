"""Detail dialog — shows metadata for a single game save and allows upload/download."""

import time
from pathlib import Path
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QMessageBox,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QColor

from scanner.models import GameEntry, SyncStatus, STATUS_LABEL, STATUS_COLOR, SYSTEM_COLOR, DEFAULT_SYSTEM_COLOR
from . import theme


class DetailDialog(QDialog):
    """
    Shows local + server metadata for a save and offers Upload / Download actions.
    """

    def __init__(self, entry: GameEntry, sync_client, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Save Details")
        self.setModal(True)
        self.setMinimumWidth(600)
        self.setStyleSheet(theme.STYLESHEET)
        self._entry = entry
        self._client = sync_client
        self._action = None  # "upload" | "download" | None

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(24, 24, 24, 24)

        # ── Header ────────────────────────────────────────────────
        sys_color = SYSTEM_COLOR.get(entry.system, DEFAULT_SYSTEM_COLOR)
        header = QHBoxLayout()
        sys_pill = QLabel(entry.system)
        sys_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sys_pill.setFixedSize(58, 26)
        sys_pill.setStyleSheet(
            f"background:{sys_color}; color:#fff; border-radius:4px; font-weight:bold;"
        )
        header.addWidget(sys_pill)

        name_lbl = QLabel(entry.display_name)
        nf = QFont()
        nf.setPointSize(15)
        nf.setBold(True)
        name_lbl.setFont(nf)
        name_lbl.setWordWrap(True)
        header.addWidget(name_lbl, 1)
        layout.addLayout(header)

        layout.addWidget(_separator())

        # ── Status ────────────────────────────────────────────────
        status_text = STATUS_LABEL.get(entry.status, "Unknown")
        status_color = STATUS_COLOR.get(entry.status, theme.STATUS_UNKNOWN)
        status_lbl = QLabel(f"Status:  {status_text}")
        status_lbl.setStyleSheet(f"color:{status_color}; font-weight:bold; font-size:13pt;")
        layout.addWidget(status_lbl)

        layout.addWidget(_separator())

        # ── Metadata grid ─────────────────────────────────────────
        grid = QVBoxLayout()
        grid.setSpacing(6)
        grid.addLayout(_row("Title ID", entry.title_id))
        grid.addLayout(_row("Emulator", entry.emulator))

        if entry.save_path:
            grid.addLayout(_row("Local save", str(entry.save_path)))
        else:
            grid.addLayout(_row("Local save", "— (not found)"))

        if entry.save_hash:
            grid.addLayout(_row("Local hash", entry.save_hash[:16] + "…"))
        if entry.save_mtime:
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(entry.save_mtime))
            grid.addLayout(_row("Local saved", ts))
        if entry.save_size:
            grid.addLayout(_row("Local size", f"{entry.save_size / 1024:.1f} KB"))

        layout.addLayout(grid)

        layout.addWidget(_separator())

        server_grid = QVBoxLayout()
        server_grid.setSpacing(6)
        if entry.server_hash:
            server_grid.addLayout(_row("Server hash", entry.server_hash[:16] + "…"))
        if entry.server_timestamp:
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(entry.server_timestamp))
            server_grid.addLayout(_row("Server saved", ts))
        if entry.server_size:
            server_grid.addLayout(_row("Server size", f"{entry.server_size / 1024:.1f} KB"))
        if not entry.server_hash:
            server_grid.addLayout(_row("Server", "No save on server"))

        layout.addLayout(server_grid)

        layout.addStretch()
        layout.addWidget(_separator())

        # ── Action buttons ────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        close_btn = QPushButton("Close  [B]")
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)

        if entry.save_path and entry.save_path.exists():
            upload_btn = QPushButton("Upload to Server  [A]")
            upload_btn.clicked.connect(self._do_upload)
            btn_row.addWidget(upload_btn)

        if entry.server_hash and entry.save_path:
            download_btn = QPushButton("Download from Server  [X]")
            download_btn.clicked.connect(self._do_download)
            btn_row.addWidget(download_btn)

        layout.addLayout(btn_row)

    # ------------------------------------------------------------------

    def _do_upload(self):
        if self._confirm("Upload", "Upload local save to server?"):
            ok = self._client.upload_save(self._entry, force=True)
            self._show_result(ok, "Uploaded successfully!", "Upload failed.")
            if ok:
                self.accept()

    def _do_download(self):
        if self._confirm("Download", "Download server save? Local save will be overwritten."):
            ok = self._client.download_save(self._entry, force=True)
            self._show_result(ok, "Downloaded successfully!", "Download failed.")
            if ok:
                self.accept()

    def _confirm(self, title: str, msg: str) -> bool:
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(msg)
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        box.setStyleSheet(theme.STYLESHEET)
        return box.exec() == QMessageBox.StandardButton.Yes

    def _show_result(self, ok: bool, ok_msg: str, fail_msg: str):
        box = QMessageBox(self)
        box.setWindowTitle("Result")
        box.setText(ok_msg if ok else fail_msg)
        box.setStyleSheet(theme.STYLESHEET)
        box.exec()

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key.Key_Escape, Qt.Key.Key_Backspace):
            self.reject()
        elif key == Qt.Key.Key_Return:
            if self._entry.save_path and self._entry.save_path.exists():
                self._do_upload()
        elif key == Qt.Key.Key_X:
            if self._entry.server_hash and self._entry.save_path:
                self._do_download()
        else:
            super().keyPressEvent(event)


def _row(label: str, value: str) -> QHBoxLayout:
    row = QHBoxLayout()
    lbl = QLabel(label + ":")
    lbl.setFixedWidth(110)
    lbl.setStyleSheet(f"color:{theme.TEXT_SECONDARY};")
    val = QLabel(value)
    val.setWordWrap(True)
    val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    row.addWidget(lbl)
    row.addWidget(val, 1)
    return row


def _separator() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet(f"color: {theme.TEXT_DIM};")
    return line
