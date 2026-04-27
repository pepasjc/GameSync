"""Downloads tab — non-modal queue UI for the Steam Deck client.

Companion to ``download_manager.DownloadManager``.  Renders one row per
download with a progress bar, status, and the right action buttons for
the row's current state (pause/resume/cancel/retry/remove).  Subscribes
to manager signals so progress updates don't require polling.

Layout choices
--------------

* Single ``QScrollArea`` of stacked rows instead of a model/view —
  each row needs heterogeneous controls (different button sets per
  status) and per-row progress bars, both of which fight ``QListView``
  delegates more than they help.
* The whole list rebuilds on ``list_changed`` (status flips, inserts,
  deletes); per-row progress goes through a much cheaper signal that
  only updates the bar + label of the affected row.  Avoids tearing
  the layout down on every byte of a 4 GB transfer.
* Action buttons are sized for the Steam Deck touch surface (36 px
  square) — same minimums as the catalog view.
"""

from __future__ import annotations

import time
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QProgressBar,
    QScrollArea,
    QFrame,
    QSizePolicy,
)

from . import theme
from download_manager import (
    DownloadManager,
    DownloadEntity,
    STATUS_QUEUED,
    STATUS_DOWNLOADING,
    STATUS_PAUSED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_CANCELLED,
)


# Status → human-readable label + colour.  Mirrors the badge palette
# used elsewhere in the app so the user picks up the meaning by
# colour without reading the text on small screens.
_STATUS_LABELS = {
    STATUS_QUEUED: "Queued",
    STATUS_DOWNLOADING: "Downloading",
    STATUS_PAUSED: "Paused",
    STATUS_COMPLETED: "Done",
    STATUS_FAILED: "Failed",
    STATUS_CANCELLED: "Cancelled",
}

_STATUS_COLORS = {
    STATUS_QUEUED: theme.TEXT_SECONDARY,
    STATUS_DOWNLOADING: theme.STATUS_DOWNLOAD,
    STATUS_PAUSED: theme.TEXT_SECONDARY,
    STATUS_COMPLETED: getattr(theme, "STATUS_SYNCED", "#5fcc7d"),
    STATUS_FAILED: getattr(theme, "STATUS_ERROR", "#e57373"),
    STATUS_CANCELLED: theme.TEXT_DIM,
}


# ── Helpers ──────────────────────────────────────────────────────────────────


def _fmt_size(b: int) -> str:
    if b < 1024:
        return f"{b} B"
    if b < 1024 * 1024:
        return f"{b / 1024:.1f} KB"
    if b < 1024 * 1024 * 1024:
        return f"{b / (1024 * 1024):.2f} MB"
    return f"{b / (1024 * 1024 * 1024):.2f} GB"


def _fmt_eta(seconds: float) -> str:
    s = int(max(0, seconds))
    if s < 60:
        return f"{s}s"
    m, sec = divmod(s, 60)
    if m < 60:
        return f"{m}m {sec:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


# ── Row widget ───────────────────────────────────────────────────────────────


class _DownloadRow(QFrame):
    """One card in the downloads list.

    Owns its own progress bar, status label, and a button row whose
    contents change with the entity's status.  ``update_progress`` is
    the hot path — called many times a second during a download — so
    it touches only the widgets that change.
    """

    pause_clicked = pyqtSignal(str)
    resume_clicked = pyqtSignal(str)
    cancel_clicked = pyqtSignal(str)
    retry_clicked = pyqtSignal(str)
    remove_clicked = pyqtSignal(str)

    def __init__(self, ent: DownloadEntity, parent: QWidget | None = None):
        super().__init__(parent)
        self._id = ent.id
        self._status = ent.status
        # For computing the byte-rate label without fighting the
        # manager's persistence cadence.
        self._last_sample_time = time.monotonic()
        self._last_sample_bytes = ent.downloaded_bytes
        self._speed_bps = 0.0

        self.setObjectName("downloadRow")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet(
            f"QFrame#downloadRow {{ background: {theme.BG_CARD}; "
            f"border: 1px solid {theme.TEXT_DIM}; "
            f"border-radius: 8px; padding: 10px 12px; }}"
        )
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        outer = QVBoxLayout(self)
        outer.setSpacing(6)
        outer.setContentsMargins(12, 10, 12, 10)

        # ── Header row: title + buttons ────────────────────────────
        header = QHBoxLayout()
        header.setSpacing(8)

        title_box = QVBoxLayout()
        title_box.setSpacing(2)

        self._title_lbl = QLabel(ent.display_name or ent.rom_id)
        self._title_lbl.setStyleSheet(
            f"color:{theme.TEXT_PRIMARY}; font-size:13pt; font-weight:bold;"
        )
        self._title_lbl.setWordWrap(False)
        self._title_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        title_box.addWidget(self._title_lbl)

        sub = ent.system or "?"
        if ent.filename:
            sub += f"  ·  {ent.filename}"
        self._sub_lbl = QLabel(sub)
        self._sub_lbl.setStyleSheet(
            f"color:{theme.TEXT_SECONDARY}; font-size:10pt;"
        )
        title_box.addWidget(self._sub_lbl)

        header.addLayout(title_box, 1)

        # Button row replaced wholesale on status change (see _rebuild_buttons).
        self._btn_row = QHBoxLayout()
        self._btn_row.setSpacing(6)
        header.addLayout(self._btn_row)

        outer.addLayout(header)

        # ── Progress bar ───────────────────────────────────────────
        self._bar = QProgressBar()
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(8)
        self._bar.setStyleSheet(
            f"QProgressBar {{ border: none; "
            f"border-radius: 4px; background: {theme.BG_TOPBAR}; }}"
            f"QProgressBar::chunk {{ background: {theme.STATUS_DOWNLOAD}; "
            f"border-radius: 4px; }}"
        )
        outer.addWidget(self._bar)

        # ── Status / metric row ────────────────────────────────────
        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet(
            f"color:{theme.TEXT_SECONDARY}; font-size:10pt;"
        )
        self._status_lbl.setWordWrap(True)
        outer.addWidget(self._status_lbl)

        self.refresh(ent)

    # ── Public ─────────────────────────────────────────────────────

    def entity_id(self) -> str:
        return self._id

    def refresh(self, ent: DownloadEntity) -> None:
        """Re-render everything from a fresh entity snapshot."""
        # Title might have changed if e.g. user re-enqueued.
        self._title_lbl.setText(ent.display_name or ent.rom_id)
        sub = ent.system or "?"
        if ent.filename:
            sub += f"  ·  {ent.filename}"
        self._sub_lbl.setText(sub)

        # Progress bar mode: indeterminate while queued or
        # connecting (no total + no bytes yet); determinate otherwise.
        if ent.status == STATUS_DOWNLOADING and ent.total_bytes <= 0 and ent.downloaded_bytes == 0:
            self._bar.setRange(0, 0)
        elif ent.total_bytes > 0:
            self._bar.setRange(0, ent.total_bytes)
            self._bar.setValue(min(ent.downloaded_bytes, ent.total_bytes))
        else:
            # Bytes flowing but no Content-Length — show a partial bar
            # at 50% so the user sees motion without a misleading %.
            self._bar.setRange(0, 100)
            self._bar.setValue(50 if ent.status == STATUS_DOWNLOADING else 0)

        if ent.status != self._status:
            # Reset speed sampler on status flip so a "Resume" doesn't
            # report the old sample as instantaneous throughput.
            self._last_sample_time = time.monotonic()
            self._last_sample_bytes = ent.downloaded_bytes
            self._speed_bps = 0.0

        self._status = ent.status
        self._rebuild_buttons(ent.status)
        self._update_status_label(ent)

    def update_progress(self, downloaded: int, total: int) -> None:
        """Hot path — called per-progress-tick.  Only touches the bar + label."""
        # Maintain a rolling speed sample so the user sees a stable
        # MB/s instead of jittery per-chunk numbers.
        now = time.monotonic()
        dt = now - self._last_sample_time
        if dt >= 0.5:
            delta = downloaded - self._last_sample_bytes
            if dt > 0:
                self._speed_bps = max(0.0, delta / dt)
            self._last_sample_time = now
            self._last_sample_bytes = downloaded

        if total > 0:
            if self._bar.maximum() != total:
                self._bar.setRange(0, total)
            self._bar.setValue(min(downloaded, total))
        # Build the label inline rather than pulling a fresh entity
        # every tick (the manager has already persisted; the label is
        # a pure function of the args we have).
        parts = [_fmt_size(downloaded)]
        if total > 0:
            pct = int(downloaded * 100 / total)
            parts.append(f"/ {_fmt_size(total)}  ({pct}%)")
        if self._speed_bps > 0:
            parts.append(f"·  {_fmt_size(int(self._speed_bps))}/s")
            if total > 0 and downloaded < total:
                eta = (total - downloaded) / self._speed_bps
                parts.append(f"·  ETA {_fmt_eta(eta)}")
        self._status_lbl.setText("  ".join(parts))

    # ── Internals ──────────────────────────────────────────────────

    def _update_status_label(self, ent: DownloadEntity) -> None:
        """Compute the subtitle string for non-hot-path status states."""
        label = _STATUS_LABELS.get(ent.status, ent.status)
        color = _STATUS_COLORS.get(ent.status, theme.TEXT_SECONDARY)

        if ent.status == STATUS_DOWNLOADING:
            # The hot path will overwrite this momentarily; show
            # something useful in the meantime.
            if ent.total_bytes > 0:
                pct = int(ent.downloaded_bytes * 100 / ent.total_bytes)
                text = (
                    f"{_fmt_size(ent.downloaded_bytes)} / "
                    f"{_fmt_size(ent.total_bytes)}  ({pct}%)"
                )
            elif ent.downloaded_bytes > 0:
                text = f"Downloading  ·  {_fmt_size(ent.downloaded_bytes)}"
            else:
                text = "Connecting to server…"
        elif ent.status == STATUS_QUEUED:
            text = "Queued"
        elif ent.status == STATUS_PAUSED:
            if ent.total_bytes > 0:
                pct = int(ent.downloaded_bytes * 100 / ent.total_bytes)
                text = (
                    f"Paused  ·  {_fmt_size(ent.downloaded_bytes)} / "
                    f"{_fmt_size(ent.total_bytes)}  ({pct}%)"
                )
            else:
                text = f"Paused  ·  {_fmt_size(ent.downloaded_bytes)} downloaded"
        elif ent.status == STATUS_COMPLETED:
            total = ent.total_bytes or ent.downloaded_bytes
            text = f"Done  ·  {_fmt_size(total)}"
        elif ent.status == STATUS_FAILED:
            text = f"Failed  ·  {_fmt_size(ent.downloaded_bytes)} downloaded"
            if ent.error_message:
                text += f"\n{ent.error_message}"
        elif ent.status == STATUS_CANCELLED:
            text = "Cancelled"
        else:
            text = label

        self._status_lbl.setText(text)
        # Top-bar of the row gets a coloured status hint via title
        # subtitle (system · filename) — keep title plain; we use the
        # colour on the status label only.
        self._status_lbl.setStyleSheet(f"color:{color}; font-size:10pt;")

    def _rebuild_buttons(self, status: str) -> None:
        """Replace the action button row to match the new status."""
        # Remove existing widgets.  ``_btn_row`` is a QHBoxLayout, so
        # we have to clear children manually.
        while self._btn_row.count():
            item = self._btn_row.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        if status in (STATUS_DOWNLOADING, STATUS_QUEUED):
            self._btn_row.addWidget(
                self._make_btn("⏸  Pause", lambda: self.pause_clicked.emit(self._id))
            )
            self._btn_row.addWidget(
                self._make_btn("✕  Cancel", lambda: self.cancel_clicked.emit(self._id))
            )
        elif status == STATUS_PAUSED:
            self._btn_row.addWidget(
                self._make_btn(
                    "▶  Resume",
                    lambda: self.resume_clicked.emit(self._id),
                    primary=True,
                )
            )
            self._btn_row.addWidget(
                self._make_btn("✕  Cancel", lambda: self.cancel_clicked.emit(self._id))
            )
        elif status in (STATUS_FAILED, STATUS_CANCELLED):
            self._btn_row.addWidget(
                self._make_btn(
                    "⟳  Retry",
                    lambda: self.retry_clicked.emit(self._id),
                    primary=True,
                )
            )
            self._btn_row.addWidget(
                self._make_btn("🗑  Remove", lambda: self.remove_clicked.emit(self._id))
            )
        elif status == STATUS_COMPLETED:
            self._btn_row.addWidget(
                self._make_btn("🗑  Remove", lambda: self.remove_clicked.emit(self._id))
            )

    def _make_btn(self, text: str, slot, primary: bool = False) -> QPushButton:
        btn = QPushButton(text)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setMinimumHeight(32)
        if primary:
            btn.setStyleSheet(
                f"QPushButton {{ background: {theme.STATUS_DOWNLOAD}; "
                f"color: {theme.BG_WINDOW}; border: none; "
                f"border-radius: 6px; padding: 4px 12px; font-weight: bold; }}"
                f"QPushButton:hover {{ background: {theme.ACCENT}; }}"
            )
        else:
            btn.setStyleSheet(
                f"QPushButton {{ background: transparent; "
                f"color: {theme.TEXT_PRIMARY}; "
                f"border: 1px solid {theme.TEXT_DIM}; "
                f"border-radius: 6px; padding: 4px 12px; }}"
                f"QPushButton:hover {{ border-color: {theme.ACCENT}; "
                f"color: {theme.ACCENT}; }}"
            )
        btn.clicked.connect(slot)
        return btn


# ── Tab widget ───────────────────────────────────────────────────────────────


class DownloadsView(QWidget):
    """The full Downloads tab.  Stack into MainWindow's QStackedWidget."""

    # Bubbled up so the main window can rescan the Installed tab when
    # a download lands.
    download_completed = pyqtSignal(str)

    def __init__(self, manager: DownloadManager, parent: QWidget | None = None):
        super().__init__(parent)
        self._manager = manager
        # id → row widget — used by the hot ``progress`` path to skip
        # a full list rebuild when only the bar should move.
        self._rows: dict[str, _DownloadRow] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(10)

        # ── Toolbar ────────────────────────────────────────────────
        bar = QHBoxLayout()
        bar.setSpacing(10)
        self._summary_lbl = QLabel("")
        self._summary_lbl.setStyleSheet(
            f"color:{theme.TEXT_SECONDARY}; font-size:11pt;"
        )
        bar.addWidget(self._summary_lbl)
        bar.addStretch()
        clear_btn = QPushButton("Clear finished")
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        clear_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; "
            f"color: {theme.TEXT_SECONDARY}; "
            f"border: 1px solid {theme.TEXT_DIM}; "
            f"border-radius: 6px; padding: 6px 14px; }}"
            f"QPushButton:hover {{ color: {theme.ACCENT}; "
            f"border-color: {theme.ACCENT}; }}"
        )
        clear_btn.clicked.connect(self._on_clear_finished)
        bar.addWidget(clear_btn)
        outer.addLayout(bar)

        # ── Scroll list ────────────────────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet("QScrollArea { border: none; }")
        self._list_host = QWidget()
        self._list_layout = QVBoxLayout(self._list_host)
        self._list_layout.setSpacing(8)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.addStretch(1)
        self._scroll.setWidget(self._list_host)
        outer.addWidget(self._scroll, 1)

        # Empty-state placeholder shown when there are zero rows.
        self._empty_lbl = QLabel(
            "No downloads yet.  Pick a ROM from the Catalog tab and press "
            "Download — it will queue here so you can keep browsing."
        )
        self._empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_lbl.setWordWrap(True)
        self._empty_lbl.setStyleSheet(
            f"color:{theme.TEXT_SECONDARY}; font-size:12pt; padding: 40px;"
        )
        outer.addWidget(self._empty_lbl)

        # Wire signals.
        manager.list_changed.connect(self._rebuild)
        manager.progress.connect(self._on_progress)
        manager.completed.connect(self.download_completed.emit)

        self._rebuild()

    # ── Manager signal handlers ────────────────────────────────────

    def _rebuild(self) -> None:
        """Re-render the entire list.  Coarse but rare."""
        entities = self._manager.list_all()

        # Remove rows whose entities are gone.
        existing_ids = {e.id for e in entities}
        for eid in list(self._rows.keys()):
            if eid not in existing_ids:
                row = self._rows.pop(eid)
                self._list_layout.removeWidget(row)
                row.deleteLater()

        # Insert / update remaining.
        # We rebuild order from scratch by removing all rows from the
        # layout (without deleting them) and re-adding in the new order.
        # The terminal stretch item lives at index 0 originally — we
        # take it out and put it back at the end.
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)

        for ent in entities:
            row = self._rows.get(ent.id)
            if row is None:
                row = _DownloadRow(ent)
                row.pause_clicked.connect(self._manager.pause)
                row.resume_clicked.connect(self._manager.resume)
                row.cancel_clicked.connect(self._manager.cancel)
                row.retry_clicked.connect(self._manager.resume)  # retry == resume
                row.remove_clicked.connect(self._manager.remove)
                self._rows[ent.id] = row
            else:
                row.refresh(ent)
            self._list_layout.addWidget(row)
        self._list_layout.addStretch(1)

        self._update_summary(entities)
        self._empty_lbl.setVisible(not entities)
        self._scroll.setVisible(bool(entities))

    def _on_progress(self, eid: str, downloaded: int, total: int) -> None:
        row = self._rows.get(eid)
        if row is not None:
            row.update_progress(downloaded, total)

    def _on_clear_finished(self) -> None:
        self._manager.clear_finished()

    def _update_summary(self, entities: list[DownloadEntity]) -> None:
        active = sum(
            1 for e in entities
            if e.status in (STATUS_QUEUED, STATUS_DOWNLOADING)
        )
        paused = sum(1 for e in entities if e.status == STATUS_PAUSED)
        done = sum(1 for e in entities if e.is_terminal)
        parts = []
        if active:
            parts.append(f"{active} active")
        if paused:
            parts.append(f"{paused} paused")
        if done:
            parts.append(f"{done} finished")
        self._summary_lbl.setText("  ·  ".join(parts) if parts else "")
