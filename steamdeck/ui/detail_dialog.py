"""Save info dialog — shows full metadata for local and server saves."""

import time
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
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
from scanner.rom_target import resolve_rom_target_dir
from . import theme
from .confirm_dialog import ConfirmDialog, ResultDialog
from .download_dialog import DownloadProgressDialog
from .gamepad_modal import GamepadModalMixin

# Every system the server would otherwise extract (CHD→CUE/GDI/ISO or
# RVZ→ISO) has an emulator on the Deck that reads the compressed format
# directly: DuckStation / PCSX2 / BeetleSaturn / Genesis Plus GX /
# Flycast / PPSSPP all consume CHD, and Dolphin consumes RVZ.  Skipping
# the ``extract`` query parameter across the board lets every ROM stream
# as-is with a proper Content-Length so the progress bar starts moving
# immediately instead of stalling on a minutes-long chdman / DolphinTool
# subprocess.
_NATIVE_COMPRESSED_FORMAT_SYSTEMS = frozenset(
    {
        # CHD (CD-ROM) systems
        "PS1", "PSX",
        "PS2",
        "SAT",
        "SCD", "MEGACD",
        "PCECD", "PCENGINECD", "TG16CD",
        "3DO",
        "PCFX",
        "NGCD",
        "AMIGACD32",
        "JAGCD",
        # CHD (GD-ROM) Dreamcast
        "DC", "DREAMCAST",
        # CHD PSP (PPSSPP reads CHD directly)
        "PSP",
        # RVZ GC / Wii (Dolphin reads RVZ natively)
        "GC", "WII",
    }
)


class DetailDialog(QDialog, GamepadModalMixin):
    """
    Full save-info popup showing local + server metadata, file paths,
    hashes, timestamps, and sync status.  Gamepad-friendly.
    """

    def __init__(
        self,
        entry: GameEntry,
        sync_client,
        parent=None,
        emulation_path: Optional[Path] = None,
        rom_scan_dir: Optional[str] = None,
        rom_dir_overrides: Optional[dict] = None,
    ):
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
        self._emulation_path = Path(emulation_path) if emulation_path else None
        self._rom_scan_dir = rom_scan_dir or ""
        self._rom_dir_overrides = dict(rom_dir_overrides or {})
        # Caller checks this after exec() to decide whether to trigger a full
        # rescan — a downloaded ROM changes the SERVER_ONLY → SYNCED status
        # for its save entry, but only after the scanner re-runs.
        self.rom_downloaded = False
        self._init_gamepad_modal()
        self.setObjectName("detailDialog")
        self.setStyleSheet(
            theme.STYLESHEET
            + f"""
QDialog#detailDialog {{
    background: {theme.BG_DIALOG};
}}
QWidget#detailContent, QScrollArea#detailScroll, QScrollArea#detailScroll QWidget {{
    background: {theme.BG_DIALOG};
}}
QFrame#detailCard {{
    background: {theme.BG_TOPBAR};
    border: 1px solid {theme.TEXT_DIM};
    border-radius: 8px;
}}
QLabel#detailValue {{
    color: {theme.TEXT_PRIMARY};
    background: transparent;
}}
QLabel#detailValueMono {{
    color: {theme.ACCENT};
    background: transparent;
}}
QLabel#detailLabel {{
    color: {theme.TEXT_SECONDARY};
    background: transparent;
}}
"""
        )

        root = QVBoxLayout(self)
        root.setSpacing(0)
        root.setContentsMargins(0, 0, 0, 0)

        # ── Scrollable content area ───────────────────────────────
        scroll = QScrollArea()
        scroll.setObjectName("detailScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(
            f"QScrollArea {{ background: {theme.BG_DIALOG}; border: none; }}"
        )
        scroll.viewport().setStyleSheet(f"background: {theme.BG_DIALOG};")
        content = QWidget()
        content.setObjectName("detailContent")
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
        grid = _card_layout()
        grid.addLayout(_row("Title ID", entry.title_id or "Unknown"))
        if entry.display_name != entry.title_id:
            grid.addLayout(_row("Display Name", entry.display_name))
        grid.addLayout(_row("Emulator", entry.emulator or "Unknown"))
        grid.addLayout(_row("System", entry.system or "Unknown"))
        layout.addWidget(_wrap_card(grid))

        layout.addWidget(_separator())

        # ── Local save section ────────────────────────────────────
        layout.addWidget(_section_header("Local Save", theme.STATUS_UPLOAD))
        local_grid = _card_layout()

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

        layout.addWidget(_wrap_card(local_grid))

        layout.addWidget(_separator())

        # ── Server save section ───────────────────────────────────
        layout.addWidget(_section_header("Server Save", theme.STATUS_DOWNLOAD))
        server_grid = _card_layout()

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

        layout.addWidget(_wrap_card(server_grid))

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
        if entry.server_hash:
            self._download_btn = QPushButton("Download  [X]")
            self._download_btn.setStyleSheet(
                f"QPushButton {{ background:{theme.STATUS_DOWNLOAD}; color:#fff;"
                f" border:none; font-weight:bold; }}"
                f" QPushButton:hover {{ opacity:0.9; }}"
            )
            self._download_btn.clicked.connect(self._do_download)
            btn_layout.addWidget(self._download_btn)

        # Download-ROM is offered only when both:
        #   - the user is missing the ROM locally, AND
        #   - the server's catalog actually has a ROM for this title
        #     (``entry.available_roms`` is populated up-front by
        #     ``ServerWorker``).
        # Hiding the button when the server has nothing avoids the
        # confusing "Download ROM → No server ROM available" round trip
        # the old code triggered on every click.
        needs_rom = entry.rom_path is None or not entry.rom_path.exists()
        has_server_rom = bool(entry.available_roms)
        self._rom_download_btn = None
        if needs_rom and has_server_rom:
            self._rom_download_btn = QPushButton("Download ROM  [Y]")
            self._rom_download_btn.setStyleSheet(
                f"QPushButton {{ background:{theme.STATUS_DOWNLOAD}; color:#fff;"
                f" border:none; font-weight:bold; }}"
                f" QPushButton:hover {{ opacity:0.9; }}"
            )
            self._rom_download_btn.clicked.connect(self._do_download_rom)
            btn_layout.addWidget(self._rom_download_btn)

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
        if entry.save_path is None:
            ResultDialog(
                False,
                f"No local save destination for '{entry.display_name}' on"
                f" {entry.system}.  Install the ROM and rescan, or configure"
                " the emulation path in Settings.",
                parent=self,
            ).exec()
            return

        msg = (
            f"Download server save for '{entry.display_name}'?\n"
            f"Title ID: {entry.title_id}"
        )
        if entry.save_path.exists():
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

    def _do_download_rom(self):
        """
        Ask the server if it has a ROM for this title, confirm with the user,
        stream it to the standard per-system folder, and flag the parent
        window to rescan so the save status flips to SYNCED.
        """
        entry = self._entry
        if self._emulation_path is None and not self._rom_scan_dir:
            ResultDialog(
                False,
                "No ROM destination is configured.  Set the emulation path or"
                " ROM scan directory in Settings and try again.",
                parent=self,
            ).exec()
            return

        # ``available_roms`` is pre-populated by ``ServerWorker`` (using
        # title_id + filename + normalised-name lookups against the
        # server catalog) so single-click download stays snappy.  Re-fetch
        # only as a fallback for entries created outside the worker flow.
        roms = list(entry.available_roms) or self._client.find_roms_for_title(
            entry.title_id, entry.system
        )
        if not roms:
            ResultDialog(
                False,
                f"No server ROM available for '{entry.display_name}'.",
                parent=self,
            ).exec()
            return

        # Multi-disc games show up as multiple catalog rows sharing a
        # title_id; pick the first so the common single-disc case stays
        # one click.  A future change could present a chooser for multi-
        # row cases.
        rom = roms[0]
        filename = rom.get("filename") or f"{rom.get('rom_id', entry.title_id)}.rom"
        size = int(rom.get("size") or 0)
        size_txt = f" ({_fmt_size(size)})" if size else ""

        roms_base = self._rom_roots_base()
        target_dir = resolve_rom_target_dir(
            roms_base, entry.system, self._rom_dir_overrides
        )
        target_filename, extract_format = self._client.plan_rom_download(rom, entry.system)
        if entry.system in _NATIVE_COMPRESSED_FORMAT_SYSTEMS:
            extract_format = None
            target_filename = filename
        target_path = target_dir / target_filename

        msg = (
            f"Download ROM for '{entry.display_name}'?\n"
            f"File: {target_filename}{size_txt}\n"
            f"Destination: {target_dir}"
        )
        if target_path.exists():
            msg += "\n\nA file with this name already exists and will be overwritten."

        dlg = ConfirmDialog(
            title="Download ROM",
            message=msg,
            confirm_label="Download",
            confirm_color=theme.STATUS_DOWNLOAD,
            parent=self,
        )
        if dlg.exec() != dlg.DialogCode.Accepted:
            return

        progress_dlg = DownloadProgressDialog(
            client=self._client,
            rom_id=str(rom.get("rom_id") or entry.title_id),
            target_path=target_path,
            extract_format=extract_format,
            display_name=entry.display_name,
            parent=self,
        )
        progress_dlg.exec()
        ok = progress_dlg.success

        if ok:
            result_msg = f"ROM for '{entry.display_name}' downloaded to {target_path}."
        else:
            # Surface whatever the worker captured (cancel message, HTTP
            # status + server error body, timeout explanation, exception
            # message) so the user isn't left guessing why a bare
            # "Download failed" appeared.
            detail = progress_dlg.error_detail or getattr(
                self._client, "last_download_error", ""
            ) or ""
            result_msg = f"Download failed for '{entry.display_name}'."
            if detail:
                result_msg += f"\n\n{detail}"

        ResultDialog(ok, result_msg, parent=self).exec()
        if ok:
            self.rom_downloaded = True
            self.accept()

    def _rom_roots_base(self) -> Path:
        """
        Pick the directory whose subfolders hold per-system ROM folders.
        Prefers the user's ``rom_scan_dir`` when set (matches the scanner's
        search order), else falls back to ``<emulation_path>/roms``.
        """
        if self._rom_scan_dir:
            scan_root = Path(self._rom_scan_dir)
            if scan_root.is_dir():
                return scan_root
        return (self._emulation_path or Path.home()) / "roms"

    # ──────────────────────────────────────────────────────────────
    # Gamepad / keyboard
    # ──────────────────────────────────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent):
        if self.handle_gamepad_key(event.key()):
            return
        super().keyPressEvent(event)

    def handle_gamepad_key(self, key: int) -> bool:
        if key in (Qt.Key.Key_Escape, Qt.Key.Key_B, Qt.Key.Key_Backspace):
            self.reject()
            return True
        if key in (Qt.Key.Key_Return, Qt.Key.Key_A):
            if self._upload_btn:
                self._do_upload()
                return True
            return False
        if key == Qt.Key.Key_X:
            if self._download_btn:
                self._do_download()
                return True
            return False
        if key == Qt.Key.Key_Y:
            if self._rom_download_btn:
                self._do_download_rom()
                return True
            return False
        return False


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
    lbl.setObjectName("detailLabel")
    lbl.setFixedWidth(120)
    lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)

    val = QLabel(str(value))
    val.setObjectName("detailValueMono" if mono else "detailValue")
    val.setWordWrap(True)
    val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    if mono:
        mf = QFont("Consolas, Courier New, monospace")
        mf.setPointSize(9)
        val.setFont(mf)
    row.addWidget(lbl)
    row.addWidget(val, 1)
    return row


def _card_layout() -> QVBoxLayout:
    layout = QVBoxLayout()
    layout.setSpacing(8)
    layout.setContentsMargins(14, 12, 14, 12)
    return layout


def _wrap_card(inner_layout: QVBoxLayout) -> QFrame:
    frame = QFrame()
    frame.setObjectName("detailCard")
    frame.setLayout(inner_layout)
    return frame


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
