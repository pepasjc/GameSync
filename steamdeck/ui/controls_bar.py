"""Footer of controller hints, in the shared GameSync design.

Face buttons are round glyphs in the colours every GameSync client uses
(A green, B red, X blue, Y yellow); shoulders, SELECT, START and the d-pad
are slate pills.  The tab's own hints sit on the left, the global ones
(tabs, exit) on the right.
"""

from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QColor, QPainter, QBrush, QFontMetrics

from . import theme

_FACE = {"A", "B", "X", "Y"}


class ButtonHint(QLabel):
    """A gamepad glyph + action label."""

    def __init__(self, button_char: str, button_color: str, label: str, parent=None):
        super().__init__(parent)
        self._button = button_char
        self._color = QColor(button_color)
        self._label = label

        font = QFont()
        font.setPointSize(theme.FONT_CONTROLS)
        self.setFont(font)
        self._glyph_font = QFont(font)
        self._glyph_font.setBold(True)
        self._glyph_font.setPointSize(theme.FONT_CONTROLS - 1)

        self.setFixedSize(self._glyph_w() + 6 + QFontMetrics(font).horizontalAdvance(label) + 4, 28)

    def _glyph_w(self) -> int:
        if self._button in _FACE:
            return 24
        return QFontMetrics(self._glyph_font).horizontalAdvance(self._button) + 14

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        h = self.height()
        gw = self._glyph_w()
        gh = 24 if self._button in _FACE else 22
        gy = (h - gh) // 2

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(self._color))
        painter.drawRoundedRect(0, gy, gw, gh, gh / 2, gh / 2)
        painter.setFont(self._glyph_font)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(0, gy, gw, gh, Qt.AlignmentFlag.AlignCenter, self._button)

        painter.setFont(self.font())
        painter.setPen(QColor(theme.DIM))
        painter.drawText(
            gw + 6, 0, self.width() - gw - 6, h,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self._label,
        )
        painter.end()


class ControlsBar(QWidget):
    """Bottom bar with button hints. Updates with the active tab."""

    MODE_SAVES = "saves"
    MODE_CATALOG = "catalog"
    MODE_INSTALLED = "installed"
    MODE_DOWNLOADS = "downloads"
    MODE_SETTINGS = "settings"

    _HINTS: dict[str, list[tuple[str, str, str]]] = {
        MODE_SAVES: [
            ("A", "BTN_A", "Sync"),
            ("X", "BTN_X", "Sync all"),
            ("Y", "BTN_Y", "Details"),
            ("B", "BTN_B", "Clear search"),
            ("SELECT", "BTN_S", "System"),
            ("L2/R2", "BTN_L", "Status"),
            ("◀ ▶", "BTN_L", "Page"),
        ],
        MODE_CATALOG: [
            ("A", "BTN_A", "Install"),
            ("X", "BTN_X", "RA only"),
            ("Y", "BTN_Y", "Search"),
            ("B", "BTN_B", "Clear search"),
            ("SELECT", "BTN_S", "System"),
            ("◀ ▶", "BTN_L", "Page"),
        ],
        MODE_INSTALLED: [
            ("A", "BTN_A", "Delete"),
            ("X", "BTN_X", "Rescan"),
            ("Y", "BTN_Y", "Search"),
            ("B", "BTN_B", "Clear search"),
            ("SELECT", "BTN_S", "System"),
            ("◀ ▶", "BTN_L", "Page"),
        ],
        MODE_DOWNLOADS: [
            ("A", "BTN_A", "Pause / resume"),
            ("X", "BTN_X", "Clear finished"),
            ("Y", "BTN_Y", "Remove"),
            ("B", "BTN_B", "Pause running"),
        ],
        MODE_SETTINGS: [
            ("A", "BTN_A", "Select"),
        ],
    }

    _GLOBAL: list[tuple[str, str, str]] = [
        ("L1/R1", "BTN_L", "Tab"),
        ("START", "BTN_S", "Exit"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(theme.CONTROLS_H)
        self.setObjectName("controlsBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(18, 0, 18, 0)
        self._layout.setSpacing(18)
        self._hint_widgets: list[ButtonHint] = []
        self._mode = ""

        self.set_mode(self.MODE_SAVES)

    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        """Swap the visible hints for *mode*."""
        if mode not in self._HINTS:
            mode = self.MODE_SAVES
        self._mode = mode
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._hint_widgets = []

        def add(hints):
            for button, color_attr, label in hints:
                pill = ButtonHint(button, getattr(theme, color_attr, theme.MUTED), label)
                self._hint_widgets.append(pill)
                self._layout.addWidget(pill)

        add(self._HINTS[mode])
        self._layout.addStretch()
        add(self._GLOBAL)
