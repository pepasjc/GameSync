"""Screen chrome in the shared GameSync design.

The console clients all draw the same frame around their lists: a header
with the app name, the top-level tabs between the L / R shoulder glyphs and
a server status dot; a row of sub-tab chips; a detail panel beside the list;
a status banner; and a footer of controller hints (``controls_bar.py``).
These are the PyQt versions of those pieces.
"""

from __future__ import annotations

from typing import Optional, Sequence

from PyQt6.QtCore import QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import theme


def _font(size: int, bold: bool = False) -> QFont:
    font = QFont()
    font.setPointSize(size)
    font.setBold(bold)
    return font


def draw_pill(
    painter: QPainter,
    x: float,
    y: float,
    h: float,
    text: str,
    fill: str,
    color: str = "#ffffff",
    font: Optional[QFont] = None,
    outline: Optional[str] = None,
    pad: int = 10,
) -> float:
    """Draw a rounded pill at (x, y) and return its width."""
    if font is not None:
        painter.setFont(font)
    fm = QFontMetrics(painter.font())
    w = fm.horizontalAdvance(text) + pad * 2
    rect = QRectF(x, y, w, h)
    painter.setPen(QPen(QColor(outline), 1) if outline else Qt.PenStyle.NoPen)
    painter.setBrush(QColor(fill))
    painter.drawRoundedRect(rect, h / 2, h / 2)
    painter.setPen(QColor(color))
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
    return w


class _TabStrip(QWidget):
    """``[L1]  Saves  Catalog  …  [R1]``, the active tab a teal pill."""

    tab_clicked = pyqtSignal(int)

    PILL_H = 30
    GAP = 6

    def __init__(self, labels: Sequence[str], parent=None):
        super().__init__(parent)
        self._labels = list(labels)
        self._badges = [""] * len(self._labels)
        self._active = 0
        self._font = _font(12, bold=True)
        self._glyph_font = _font(9, bold=True)
        self._hit: list[QRectF] = []
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(self.PILL_H + 8)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_active(self, index: int) -> None:
        self._active = index
        self.update()

    def set_badge(self, index: int, text: str) -> None:
        """Short suffix on a tab label (``Downloads 43%``)."""
        if 0 <= index < len(self._badges) and self._badges[index] != text:
            self._badges[index] = text
            self.updateGeometry()
            self.update()

    def _label(self, index: int) -> str:
        badge = self._badges[index]
        return f"{self._labels[index]} {badge}" if badge else self._labels[index]

    def sizeHint(self) -> QSize:
        fm = QFontMetrics(self._font)
        gm = QFontMetrics(self._glyph_font)
        width = sum(fm.horizontalAdvance(self._label(i)) + 32
                    for i in range(len(self._labels)))
        width += self.GAP * (len(self._labels) + 1)
        width += 2 * (gm.horizontalAdvance("R1") + 16 + 10)
        return QSize(width, self.PILL_H + 8)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        y = (self.height() - self.PILL_H) / 2
        glyph_h = 22
        gy = (self.height() - glyph_h) / 2
        x = 0.0
        x += draw_pill(painter, x, gy, glyph_h, "L1", theme.LINE, theme.TEXT,
                       self._glyph_font, pad=8) + 10
        self._hit = []
        painter.setFont(self._font)
        fm = QFontMetrics(self._font)
        for index in range(len(self._labels)):
            text = self._label(index)
            w = fm.horizontalAdvance(text) + 32
            rect = QRectF(x, y, w, self.PILL_H)
            self._hit.append(rect)
            if index == self._active:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(theme.ACCENT))
                painter.drawRoundedRect(rect, self.PILL_H / 2, self.PILL_H / 2)
                painter.setPen(QColor(theme.INK))
            else:
                painter.setPen(QColor(theme.DIM))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
            x += w + self.GAP
        x += 4
        draw_pill(painter, x, gy, glyph_h, "R1", theme.LINE, theme.TEXT,
                  self._glyph_font, pad=8)
        painter.end()

    def mousePressEvent(self, event) -> None:
        pos = event.position()
        for index, rect in enumerate(self._hit):
            if rect.contains(pos):
                self.tab_clicked.emit(index)
                return
        super().mousePressEvent(event)


class HeaderBar(QWidget):
    """App name, the tab strip and the server status."""

    tab_clicked = pyqtSignal(int)

    def __init__(self, labels: Sequence[str], version: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("topBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(theme.HEADER_H)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 0, 18, 0)
        layout.setSpacing(14)

        logo = QLabel(
            f"<span style='color:{theme.ACCENT}'>Game</span>"
            f"<span style='color:{theme.TEXT}'>Sync</span>"
        )
        logo.setFont(_font(17, bold=True))
        layout.addWidget(logo)
        if version:
            ver = QLabel(f"v{version}")
            ver.setStyleSheet(f"color:{theme.MUTED}; font-size:9pt;")
            layout.addWidget(ver)

        layout.addStretch(1)
        self._tabs = _TabStrip(labels)
        self._tabs.tab_clicked.connect(self.tab_clicked.emit)
        layout.addWidget(self._tabs)
        layout.addStretch(1)

        self._scan_bar = QProgressBar()
        self._scan_bar.setFixedSize(110, 6)
        self._scan_bar.setRange(0, 0)
        self._scan_bar.setTextVisible(False)
        self._scan_bar.setStyleSheet(
            f"QProgressBar {{ background:{theme.LINE}; border:none; border-radius:3px; }}"
            f"QProgressBar::chunk {{ background:{theme.ACCENT}; border-radius:3px; }}"
        )
        self._scan_bar.hide()
        layout.addWidget(self._scan_bar)

        self._status_dot = QLabel("●")
        self._status_dot.setStyleSheet(f"color:{theme.MUTED}; font-size:13pt;")
        layout.addWidget(self._status_dot)
        self._status_label = QLabel("Checking…")
        self._status_label.setMaximumWidth(220)
        self._status_label.setStyleSheet(f"color:{theme.DIM}; font-size:10pt;")
        layout.addWidget(self._status_label)

    def set_active_tab(self, index: int) -> None:
        self._tabs.set_active(index)

    def set_tab_badge(self, index: int, text: str) -> None:
        self._tabs.set_badge(index, text)

    def set_server(self, online: bool, text: str) -> None:
        color = theme.OK if online else theme.ERR
        self._status_dot.setStyleSheet(f"color:{color}; font-size:13pt;")
        self._status_label.setText(text)

    def set_busy(self, busy: bool) -> None:
        self._scan_bar.setVisible(busy)


class ChipBar(QWidget):
    """Sub-tab chips (system filter) with the SELECT glyph, plus a free
    text slot on the left (e.g. the Saves status filter) and a count on the
    right.  Chips that don't fit are scrolled so the active one shows."""

    chip_clicked = pyqtSignal(str)

    CHIP_H = 26

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("filterBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(theme.SUBBAR_H)
        self._chips: list[str] = []
        self._active = ""
        self._hint = "SELECT"
        self._left = ""
        self._right = ""
        self._font = _font(10, bold=True)
        self._text_font = _font(10)
        self._hit: list[tuple[QRectF, str]] = []

    def set_state(
        self,
        chips: Sequence[str],
        active: str,
        left: str = "",
        right: str = "",
        hint: str = "SELECT",
    ) -> None:
        self._chips = list(chips)
        self._active = active
        self._left = left
        self._right = right
        self._hint = hint
        self.update()

    def set_right(self, text: str) -> None:
        if text != self._right:
            self._right = text
            self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(theme.BG2))
        painter.setPen(QColor(theme.LINE))
        painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)

        y = (self.height() - self.CHIP_H) / 2
        x = 18.0
        right_edge = self.width() - 18.0

        painter.setFont(self._text_font)
        fm_text = QFontMetrics(self._text_font)
        if self._right:
            w = fm_text.horizontalAdvance(self._right)
            painter.setPen(QColor(theme.DIM))
            painter.drawText(QRectF(right_edge - w, 0, w, self.height()),
                             Qt.AlignmentFlag.AlignVCenter, self._right)
            right_edge -= w + 24
        if self._left:
            w = fm_text.horizontalAdvance(self._left)
            painter.setPen(QColor(theme.DIM))
            painter.drawText(QRectF(x, 0, w, self.height()),
                             Qt.AlignmentFlag.AlignVCenter, self._left)
            x += w + 24

        self._hit = []
        if not self._chips:
            painter.end()
            return

        if self._hint:
            x += draw_pill(painter, x, y + 2, self.CHIP_H - 4, self._hint,
                           theme.LINE, theme.TEXT, _font(8, bold=True), pad=7) + 10

        painter.setFont(self._font)
        fm = QFontMetrics(self._font)
        widths = [fm.horizontalAdvance(_chip_label(c)) + 24 for c in self._chips]
        gap = 6
        try:
            active_index = self._chips.index(self._active)
        except ValueError:
            active_index = 0
        # Start late enough that the active chip fits in the strip.
        start = 0
        avail = right_edge - x
        while start < active_index:
            span = sum(widths[start:active_index + 1]) + gap * (active_index - start)
            if span <= avail:
                break
            start += 1
        if start > 0:
            painter.setPen(QColor(theme.MUTED))
            painter.drawText(QRectF(x, 0, 14, self.height()),
                             Qt.AlignmentFlag.AlignVCenter, "‹")
            x += 16
        for index in range(start, len(self._chips)):
            w = widths[index]
            if x + w > right_edge:
                painter.setPen(QColor(theme.MUTED))
                painter.drawText(QRectF(x, 0, 14, self.height()),
                                 Qt.AlignmentFlag.AlignVCenter, "›")
                break
            chip = self._chips[index]
            rect = QRectF(x, y, w, self.CHIP_H)
            self._hit.append((rect, chip))
            if chip == self._active:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(theme.ACCENT))
                painter.drawRoundedRect(rect, self.CHIP_H / 2, self.CHIP_H / 2)
                painter.setPen(QColor(theme.INK))
            else:
                painter.setPen(QPen(QColor(theme.LINE), 1))
                painter.setBrush(QColor(theme.PANEL))
                painter.drawRoundedRect(rect, self.CHIP_H / 2, self.CHIP_H / 2)
                painter.setPen(QColor(theme.DIM))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, _chip_label(chip))
            x += w + gap
        painter.end()

    def mousePressEvent(self, event) -> None:
        pos = event.position()
        for rect, chip in self._hit:
            if rect.contains(pos):
                self.chip_clicked.emit(chip)
                return
        super().mousePressEvent(event)


class DetailPanel(QFrame):
    """The panel beside the list describing the highlighted row."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("detailPanel")
        self.setFixedWidth(theme.DETAIL_W)
        self.setStyleSheet(
            f"QFrame#detailPanel {{ background:{theme.PANEL}; "
            f"border:1px solid {theme.LINE}; border-radius:10px; }}"
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 16)
        outer.setSpacing(8)

        self._kicker = QLabel("")
        self._kicker.setStyleSheet(
            f"color:{theme.ACCENT}; font-size:9pt; font-weight:bold;"
            "letter-spacing:1px;"
        )
        outer.addWidget(self._kicker)

        self._title = QLabel("")
        self._title.setWordWrap(True)
        self._title.setStyleSheet(
            f"color:{theme.TEXT}; font-size:15pt; font-weight:bold;"
        )
        outer.addWidget(self._title)

        self._pills = QLabel("")
        self._pills.setTextFormat(Qt.TextFormat.RichText)
        outer.addWidget(self._pills)

        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background:{theme.LINE}; border:none;")
        outer.addWidget(line)

        self._rows = QLabel("")
        self._rows.setTextFormat(Qt.TextFormat.RichText)
        self._rows.setWordWrap(True)
        self._rows.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        outer.addWidget(self._rows)

        self._note = QLabel("")
        self._note.setWordWrap(True)
        self._note.setStyleSheet(f"color:{theme.DIM}; font-size:10pt;")
        outer.addWidget(self._note)
        outer.addStretch(1)

    def show_info(
        self,
        kicker: str,
        title: str,
        pills: Sequence[tuple[str, str]] = (),
        rows: Sequence[tuple[str, str]] = (),
        note: str = "",
    ) -> None:
        """``pills`` are ``(text, colour)``; ``rows`` are ``(label, value)``."""
        self._kicker.setText(kicker.upper())
        self._title.setText(title)
        self._pills.setText("&nbsp;".join(
            f"<span style='background:{color}; color:#ffffff; font-weight:bold;"
            f" font-size:9pt;'>&nbsp;{_esc(text)}&nbsp;</span>"
            for text, color in pills
        ))
        self._pills.setVisible(bool(pills))
        self._rows.setText(
            "<table cellspacing='0' cellpadding='3'>" + "".join(
                f"<tr><td style='color:{theme.MUTED}; padding-right:12px;'>"
                f"{_esc(label)}</td><td style='color:{theme.TEXT};'>"
                f"{_esc(value)}</td></tr>"
                for label, value in rows
            ) + "</table>"
        )
        self._note.setText(note)
        self._note.setVisible(bool(note))

    def clear(self, message: str = "") -> None:
        self.show_info("", message)


class StatusBanner(QLabel):
    """One line of feedback above the footer; fades back to empty."""

    _COLORS = {"info": theme.ACCENT, "ok": theme.OK, "warn": theme.WARN,
               "error": theme.ERR}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(theme.BANNER_H)
        self.setContentsMargins(18, 0, 18, 0)
        # A long message must never widen the window.
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(lambda: self.show_message(""))
        self.show_message("")

    def show_message(self, text: str, kind: str = "info", hold_ms: int = 5000) -> None:
        color = self._COLORS.get(kind, theme.ACCENT)
        self.setText(f"●  {text}" if text else "")
        self.setStyleSheet(
            f"background:{theme.BG}; color:{color if text else theme.DIM};"
            "font-size:10pt;"
        )
        self._timer.stop()
        if text and hold_ms > 0:
            self._timer.start(hold_ms)


def _chip_label(chip: str) -> str:
    # The views keep "All Systems" as their filter value; a chip says "All".
    return "All" if chip.lower().startswith("all") else chip


def _esc(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))
