"""Modal dialog that streams a ROM download with live progress feedback.

The download runs on a worker thread so the UI stays responsive, and the
progress callback from ``SyncClient.download_rom`` drives a Qt progress bar
via a queued signal.  The user can cancel an in-flight download; the
``progress_cb`` raises on the next chunk, which trips ``download_rom``'s
existing .part-file cleanup path.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QProgressBar,
)
from PyQt6.QtCore import Qt, QThread, QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QKeyEvent

from . import theme
from .gamepad_modal import GamepadModalMixin


class _DownloadCancelled(Exception):
    """Raised inside the progress callback to abort the HTTP stream."""


class RomDownloadWorker(QObject):
    """Runs ``SyncClient.download_rom`` on a worker thread with progress signals."""

    progress = pyqtSignal(int, int)       # downloaded, total (total=0 if unknown)
    finished = pyqtSignal(bool, str)      # success, error_detail

    def __init__(
        self,
        client,
        rom_id: str,
        target_path: Path,
        extract_format: Optional[str],
    ):
        super().__init__()
        self._client = client
        self._rom_id = rom_id
        self._target = target_path
        self._extract = extract_format
        self._cancelled = False

    def cancel(self) -> None:
        """Flag the stream for cancellation; cb raises on the next chunk."""
        self._cancelled = True

    def run(self) -> None:
        def cb(downloaded: int, total: int) -> None:
            if self._cancelled:
                raise _DownloadCancelled
            self.progress.emit(downloaded, total)

        ok = self._client.download_rom(
            rom_id=self._rom_id,
            target_path=self._target,
            extract_format=self._extract,
            progress_cb=cb,
        )

        if self._cancelled:
            # ``download_rom`` catches every exception into last_download_error,
            # which would surface "_DownloadCancelled" as the failure reason.
            # Replace it with a user-meaningful string.
            self.finished.emit(False, "Download cancelled.")
            return

        detail = "" if ok else (getattr(self._client, "last_download_error", "") or "")
        self.finished.emit(ok, detail)


class DownloadProgressDialog(QDialog, GamepadModalMixin):
    """Live progress dialog for a single ROM download.

    Shows a QProgressBar, byte counter, transfer speed, and ETA while the
    worker thread streams the ROM.  ``exec()`` returns Accepted on success
    and Rejected on failure or cancel; the caller reads ``success`` and
    ``error_detail`` to decide what to do next.
    """

    def __init__(
        self,
        client,
        rom_id: str,
        target_path: Path,
        extract_format: Optional[str] = None,
        display_name: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Downloading ROM")
        self.setModal(True)
        self.setMinimumWidth(520)
        self.setStyleSheet(theme.STYLESHEET)
        self._init_gamepad_modal()

        self._target_path = target_path
        self._success = False
        self._error_detail = ""
        self._finished = False
        self._start_time = time.monotonic()
        self._last_update = self._start_time
        self._last_downloaded = 0
        self._speed_bps = 0.0
        self._any_progress = False
        # Before the first Content-Length-bearing progress tick, the bar is
        # indeterminate (0,0) so the user sees motion even when the server
        # is still extracting a CHD/RVZ before any bytes flow.
        self._known_total = False

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(28, 24, 28, 20)

        title_text = (
            f"Downloading '{display_name}'" if display_name else "Downloading ROM"
        )
        title_lbl = QLabel(title_text)
        tf = QFont()
        tf.setPointSize(15)
        tf.setBold(True)
        title_lbl.setFont(tf)
        title_lbl.setWordWrap(True)
        layout.addWidget(title_lbl)

        self._file_lbl = QLabel(target_path.name)
        self._file_lbl.setStyleSheet(
            f"color:{theme.TEXT_SECONDARY}; font-size:11pt;"
        )
        self._file_lbl.setWordWrap(True)
        layout.addWidget(self._file_lbl)

        self._bar = QProgressBar()
        self._bar.setRange(0, 0)
        self._bar.setTextVisible(True)
        self._bar.setFormat("Waiting for server…")
        self._bar.setStyleSheet(
            f"QProgressBar {{ border: 1px solid {theme.TEXT_DIM};"
            f" border-radius: 4px; text-align: center; height: 22px;"
            f" color: #fff; background: {theme.BG_CARD}; }}"
            f"QProgressBar::chunk {{ background: {theme.STATUS_DOWNLOAD};"
            f" border-radius: 3px; }}"
        )
        layout.addWidget(self._bar)

        self._status_lbl = QLabel(
            "Waiting for server to prepare file — "
            "CHD / RVZ extractions can take a few minutes…"
        )
        self._status_lbl.setStyleSheet(
            f"color:{theme.TEXT_SECONDARY}; font-size:11pt;"
        )
        self._status_lbl.setWordWrap(True)
        layout.addWidget(self._status_lbl)

        # Tick an elapsed-time counter while we're still waiting for the
        # first byte.  Without this the status label froze at "Starting…"
        # for minutes during CHD extraction and users thought the app had
        # hung.  Stops on the first real progress callback.
        self._waiting_timer = QTimer(self)
        self._waiting_timer.setInterval(1000)
        self._waiting_timer.timeout.connect(self._tick_waiting)
        self._waiting_timer.start()

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._cancel_btn = QPushButton("Cancel  [B]")
        self._cancel_btn.clicked.connect(self._request_cancel)
        btn_row.addWidget(self._cancel_btn)
        layout.addLayout(btn_row)

        self._thread = QThread()
        self._worker = RomDownloadWorker(client, rom_id, target_path, extract_format)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    # ──────────────────────────────────────────────────────────────
    # Worker signal handlers (main thread)
    # ──────────────────────────────────────────────────────────────

    def _tick_waiting(self) -> None:
        """Update the 'waiting for server' label every second with elapsed time.

        Stops firing once the first progress callback arrives — after that the
        regular status formatter owns the label.
        """
        if self._any_progress or self._finished:
            self._waiting_timer.stop()
            return
        elapsed = int(time.monotonic() - self._start_time)
        self._status_lbl.setText(
            f"Waiting for server to prepare file ({_fmt_eta(elapsed)} elapsed) — "
            f"CHD / RVZ extractions can take a few minutes…"
        )

    def _on_progress(self, downloaded: int, total: int) -> None:
        if not self._any_progress:
            self._any_progress = True
            self._waiting_timer.stop()
            # Reset the speed baseline so the first post-wait sample measures
            # actual stream throughput, not "X MB since dialog opened".
            self._last_update = time.monotonic()
            self._last_downloaded = downloaded
        now = time.monotonic()
        dt = now - self._last_update
        # Recompute speed at most 4× per second so the label doesn't jitter.
        if dt >= 0.25:
            delta = downloaded - self._last_downloaded
            self._speed_bps = delta / dt if dt > 0 else 0.0
            self._last_update = now
            self._last_downloaded = downloaded

        if total > 0:
            if not self._known_total:
                self._bar.setRange(0, total)
                self._bar.setFormat("%p%")
                self._known_total = True
            elif self._bar.maximum() != total:
                self._bar.setRange(0, total)
            self._bar.setValue(min(downloaded, total))

        parts = [_fmt_size(downloaded)]
        if total > 0:
            parts.append(f"/ {_fmt_size(total)}")
        if self._speed_bps > 0:
            extra = f"({_fmt_size(int(self._speed_bps))}/s"
            if total > 0 and downloaded < total:
                remaining = max(0, total - downloaded)
                eta = remaining / self._speed_bps
                extra += f", {_fmt_eta(eta)} left"
            extra += ")"
            parts.append(extra)
        self._status_lbl.setText("  ".join(parts))

    def _on_finished(self, success: bool, detail: str) -> None:
        self._success = success
        self._error_detail = detail
        self._finished = True
        self._waiting_timer.stop()
        if success:
            if self._known_total:
                self._bar.setValue(self._bar.maximum())
            self.accept()
        else:
            self.reject()

    # ──────────────────────────────────────────────────────────────
    # Cancellation / close
    # ──────────────────────────────────────────────────────────────

    def _request_cancel(self) -> None:
        if self._finished:
            self.reject()
            return
        self._cancel_btn.setEnabled(False)
        self._status_lbl.setText("Cancelling…")
        self._worker.cancel()

    def closeEvent(self, event):
        # Keep the dialog open until the worker either finishes or notices the
        # cancel flag — closing mid-stream would leak the HTTP connection and
        # skip the .part-file cleanup in ``download_rom``.
        if not self._finished:
            self._request_cancel()
            event.ignore()
            return
        if self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(2000)
        super().closeEvent(event)

    # ──────────────────────────────────────────────────────────────
    # Gamepad / keyboard
    # ──────────────────────────────────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent):
        if self.handle_gamepad_key(event.key()):
            return
        super().keyPressEvent(event)

    def handle_gamepad_key(self, key: int) -> bool:
        if key in (Qt.Key.Key_Escape, Qt.Key.Key_B, Qt.Key.Key_Backspace):
            self._request_cancel()
            return True
        return False

    # ──────────────────────────────────────────────────────────────
    # Public result
    # ──────────────────────────────────────────────────────────────

    @property
    def success(self) -> bool:
        return self._success

    @property
    def error_detail(self) -> str:
        return self._error_detail


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _fmt_size(b: int) -> str:
    if b < 1024:
        return f"{b} B"
    if b < 1024 * 1024:
        return f"{b / 1024:.1f} KB"
    if b < 1024 * 1024 * 1024:
        return f"{b / (1024 * 1024):.2f} MB"
    return f"{b / (1024 * 1024 * 1024):.2f} GB"


def _fmt_eta(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m, sec = divmod(s, 60)
    if m < 60:
        return f"{m}m {sec:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"
