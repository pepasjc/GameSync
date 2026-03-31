"""Save info dialog — shows full metadata for local and server saves."""

import time
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
    QScrollArea,
    QWidget,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QKeyEvent

from scanner.models import (
    GameEntry,
    SyncStatus,
    STATUS_LABEL,
    STATUS_COLOR,
    SYSTEM_COLOR,
    DEFAULT_SYSTEM_COLOR,
)
from . import theme
from .confirm_dialog import ConfirmDialog, ResultDialog


class DetailDialog(QDialog):
    """
    Full save-info popup showing local + server metadata, file paths,
    hashes, timestamps, and sync status.  Gamepad-friendly.
    """

    def __init__(self, entry: GameEntry, sync_client, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Save Info")
        self.setModal(True)
        self.setMinimumWidth(680)
        self.setMaximumWidth(900)
        self.setMinimumHeight(400)
        self.setMaximumHeight(720)
        self.setStyleSheet(theme.STYLESHEET)
        self._entry = entry
        self._client = sync_client

        root = QVBoxLayout(self)
        root.setSpacing(0)
        root.setContentsMargins(0, 0, 0, 0)

        # ── Scrollable content area ───────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(
            f"QScrollArea {{ background: {theme.BG_DIALOG}; border: none; }}"
        )
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(10)
        layout.setContentsMargins(28, 24, 28, 12)

        # ── Header: system pill + game name ───────────────────────
        sys_color = SYSTEM_COLOR.get(entry.system, DEFAULT_SYSTEM_COLOR)
        header = QHBoxLayout()
        sys_pill = QLabel(entry.system)
        sys_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sys_pill.setFixedSize(58, 28)
        sys_pill.setStyleSheet(
            f"background:{sys_color}; color:#fff; border-radius:4px;"
            f" font-weight:bold; font-size:10pt;"
        )
        header.addWidget(sys_pill)

        name_lbl = QLabel(entry.display_name)
        nf = QFont()
        nf.setPointSize(16)
        nf.setBold(True)
        name_lbl.setFont(nf)
        name_lbl.setWordWrap(True)
        header.addWidget(name_lbl, 1)
        layout.addLayout(header)

        # ── Sync status badge ─────────────────────────────────────
        status_text = STATUS_LABEL.get(entry.status, "Unknown")
        status_color = STATUS_COLOR.get(entry.status, theme.STATUS_UNKNOWN)
        status_row = QHBoxLayout()
        status_pill = QLabel(f"  {status_text}  ")
        status_pill.setStyleSheet(
            f"background:{status_color}; color:#fff; border-radius:4px;"
            f" font-weight:bold; font-size:10pt; padding: 2px 8px;"
        )
        status_pill.setFixedHeight(26)
        status_row.addWidget(status_pill)
        status_row.addStretch()
        layout.addLayout(status_row)

        layout.addWidget(_separator())

        # ── Identity section ──────────────────────────────────────
        layout.addWidget(_section_header("Identity"))
        grid = QVBoxLayout()
        grid.setSpacing(4)
        grid.addLayout(_row("Title ID", entry.title_id))
        if entry.display_name != entry.title_id:
            grid.addLayout(_row("Display Name", entry.display_name))
        grid.addLayout(_row("Emulator", entry.emulator))
        grid.addLayout(_row("System", entry.system))
        layout.addLayout(grid)

        layout.addWidget(_separator())

        # ── Local save section ────────────────────────────────────
        layout.addWidget(_section_header("Local Save", theme.STATUS_UPLOAD))
        local_grid = QVBoxLayout()
        local_grid.setSpacing(4)

        if entry.save_path:
            save_p = entry.save_path
            if save_p.is_dir():
                local_grid.addLayout(_row("Save Dir", str(save_p)))
                # Count files inside
                try:
                    file_count = sum(1 for f in save_p.rglob("*") if f.is_file())
                    local_grid.addLayout(_row("Files", str(file_count)))
                except Exception:
                    pass
            else:
                local_grid.addLayout(_row("Save Path", str(save_p.parent)))
                local_grid.addLayout(_row("File Name", save_p.name))

            if entry.save_hash:
                local_grid.addLayout(_row("Hash", entry.save_hash, mono=True))
            if entry.save_size:
                local_grid.addLayout(_row("Size", _fmt_size(entry.save_size)))
            if entry.save_mtime:
                ts = time.strftime(
                    "%Y-%m-%d %H:%M:%S", time.localtime(entry.save_mtime)
                )
                local_grid.addLayout(_row("Modified", ts))
        else:
            no_lbl = QLabel("No local save found")
            no_lbl.setStyleSheet(f"color:{theme.TEXT_DIM}; font-style:italic;")
            local_grid.addWidget(no_lbl)

        layout.addLayout(local_grid)

        layout.addWidget(_separator())

        # ── Server save section ───────────────────────────────────
        layout.addWidget(_section_header("Server Save", theme.STATUS_DOWNLOAD))
        server_grid = QVBoxLayout()
        server_grid.setSpacing(4)

        if entry.server_hash:
            server_grid.addLayout(_row("Hash", entry.server_hash, mono=True))
            if entry.server_size:
                server_grid.addLayout(_row("Size", _fmt_size(entry.server_size)))
            if entry.server_timestamp:
                ts = time.strftime(
                    "%Y-%m-%d %H:%M:%S", time.localtime(entry.server_timestamp)
                )
                server_grid.addLayout(_row("Uploaded", ts))

            # ── Hash comparison indicator ─────────────────────────
            if entry.save_hash and entry.server_hash:
                if entry.save_hash == entry.server_hash:
                    match_lbl = QLabel("Hashes match")
                    match_lbl.setStyleSheet(
                        f"color:{theme.STATUS_SYNCED}; font-weight:bold;"
                    )
                else:
                    match_lbl = QLabel("Hashes differ")
                    match_lbl.setStyleSheet(
                        f"color:{theme.STATUS_CONFLICT}; font-weight:bold;"
                    )
                server_grid.addWidget(match_lbl)
        else:
            no_lbl = QLabel("No save on server")
            no_lbl.setStyleSheet(f"color:{theme.TEXT_DIM}; font-style:italic;")
            server_grid.addWidget(no_lbl)

        layout.addLayout(server_grid)

        layout.addStretch()

        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        # ── Action buttons (fixed at bottom) ──────────────────────
        btn_bar = QWidget()
        btn_bar.setStyleSheet(f"background:{theme.BG_TOPBAR};")
        btn_layout = QHBoxLayout(btn_bar)
        btn_layout.setContentsMargins(24, 10, 24, 10)
        btn_layout.setSpacing(12)
        btn_layout.addStretch()

        close_btn = QPushButton("Close  [B]")
        close_btn.clicked.connect(self.reject)
        btn_layout.addWidget(close_btn)

        self._upload_btn = None
        if entry.save_path and entry.save_path.exists():
            self._upload_btn = QPushButton("Upload  [A]")
            self._upload_btn.setStyleSheet(
                f"QPushButton {{ background:{theme.STATUS_UPLOAD}; color:#fff;"
                f" border:none; font-weight:bold; }}"
                f" QPushButton:hover {{ opacity:0.9; }}"
            )
            self._upload_btn.clicked.connect(self._do_upload)
            btn_layout.addWidget(self._upload_btn)

        self._download_btn = None
        if entry.server_hash and entry.save_path:
            self._download_btn = QPushButton("Download  [X]")
            self._download_btn.setStyleSheet(
                f"QPushButton {{ background:{theme.STATUS_DOWNLOAD}; color:#fff;"
                f" border:none; font-weight:bold; }}"
                f" QPushButton:hover {{ opacity:0.9; }}"
            )
            self._download_btn.clicked.connect(self._do_download)
            btn_layout.addWidget(self._download_btn)

        root.addWidget(btn_bar)

    # ──────────────────────────────────────────────────────────────
    # Actions
    # ──────────────────────────────────────────────────────────────

    def _do_upload(self):
        entry = self._entry
        msg = (
            f"Upload local save for '{entry.display_name}' to the server?\n"
            f"Title ID: {entry.title_id}"
        )
        if entry.server_hash:
            msg += "\n\nThis will overwrite the existing server save."

        dlg = ConfirmDialog(
            title="Upload Save",
            message=msg,
            confirm_label="Upload",
            confirm_color=theme.STATUS_UPLOAD,
            parent=self,
        )
        if dlg.exec() != dlg.DialogCode.Accepted:
            return

        ok = self._client.upload_save(entry, force=True)
        ResultDialog(
            ok,
            f"'{entry.display_name}' uploaded successfully."
            if ok
            else f"Upload failed for '{entry.display_name}'.",
            parent=self,
        ).exec()
        if ok:
            entry.status = SyncStatus.SYNCED
            self.accept()

    def _do_download(self):
        entry = self._entry
        msg = (
            f"Download server save for '{entry.display_name}'?\n"
            f"Title ID: {entry.title_id}"
        )
        if entry.save_path and entry.save_path.exists():
            msg += "\n\nThis will overwrite your local save file."

        dlg = ConfirmDialog(
            title="Download Save",
            message=msg,
            confirm_label="Download",
            confirm_color=theme.STATUS_DOWNLOAD,
            parent=self,
        )
        if dlg.exec() != dlg.DialogCode.Accepted:
            return

        ok = self._client.download_save(entry, force=True)
        ResultDialog(
            ok,
            f"'{entry.display_name}' downloaded successfully."
            if ok
            else f"Download failed for '{entry.display_name}'.",
            parent=self,
        ).exec()
        if ok:
            entry.status = SyncStatus.SYNCED
            self.accept()

    # ──────────────────────────────────────────────────────────────
    # Gamepad / keyboard
    # ──────────────────────────────────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()
        if key in (Qt.Key.Key_Escape, Qt.Key.Key_B, Qt.Key.Key_Backspace):
            self.reject()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_A):
            if self._upload_btn:
                self._do_upload()
        elif key == Qt.Key.Key_X:
            if self._download_btn:
                self._do_download()
        else:
            super().keyPressEvent(event)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _section_header(text: str, color: str = theme.TEXT_PRIMARY) -> QLabel:
    """Colored section header label."""
    lbl = QLabel(text)
    f = QFont()
    f.setPointSize(13)
    f.setBold(True)
    lbl.setFont(f)
    lbl.setStyleSheet(f"color:{color}; margin-top:4px;")
    return lbl


def _row(label: str, value: str, mono: bool = False) -> QHBoxLayout:
    """Key-value row. If mono=True, value uses monospace font."""
    row = QHBoxLayout()
    row.setSpacing(8)
    lbl = QLabel(label + ":")
    lbl.setFixedWidth(120)
    lbl.setStyleSheet(f"color:{theme.TEXT_SECONDARY};")
    lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)

    val = QLabel(value)
    val.setWordWrap(True)
    val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    if mono:
        mf = QFont("Consolas, Courier New, monospace")
        mf.setPointSize(9)
        val.setFont(mf)
        val.setStyleSheet(f"color:{theme.ACCENT};")
    row.addWidget(lbl)
    row.addWidget(val, 1)
    return row


def _separator() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFixedHeight(1)
    line.setStyleSheet(f"background:{theme.TEXT_DIM}; border:none;")
    return line


def _fmt_size(size_bytes: int) -> str:
    """Format byte count as human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
