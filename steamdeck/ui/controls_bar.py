"""Bottom controls bar showing button legend."""

from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QColor, QPainter, QBrush, QFontMetrics

from . import theme


class ButtonHint(QLabel):
    """A small pill showing a gamepad button + action label."""

    def __init__(self, button_char: str, button_color: str, label: str, parent=None):
        super().__init__(parent)
        self._button = button_char
        self._color = QColor(button_color)
        self._label = label

        font = QFont()
        font.setPointSize(theme.FONT_CONTROLS)
        self.setFont(font)

        fm = QFontMetrics(font)
        pill_w = fm.horizontalAdvance(button_char) + 10
        text_w = fm.horizontalAdvance(f" {label}") + 4
        self.setFixedSize(pill_w + text_w + 6, 28)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        font = self.font()
        fm = QFontMetrics(font)
        pill_w = fm.horizontalAdvance(self._button) + 10
        h = self.height()

        # Button pill
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(self._color))
        painter.drawRoundedRect(0, (h - 22) // 2, pill_w, 22, 11, 11)

        painter.setFont(font)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(
            0, (h - 22) // 2, pill_w, 22, Qt.AlignmentFlag.AlignCenter, self._button
        )

        # Label
        painter.setPen(QColor(theme.TEXT_SECONDARY))
        painter.drawText(
            pill_w + 4,
            0,
            self.width() - pill_w - 4,
            h,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self._label,
        )
        painter.end()


class ControlsBar(QWidget):
    """Bottom bar with button hints. Updates based on context."""

    MODE_SAVES = "saves"
    MODE_CATALOG = "catalog"
    MODE_INSTALLED = "installed"

    _HINTS_SAVES: list[tuple[str, str, str]] = [
        ("A", "BTN_A", "Info"),
        ("B", "BTN_B", "Exit"),
        ("X", "BTN_X", "Sync"),
        ("Y", "BTN_Y", "Refresh"),
        ("L1/R1", "BTN_L", "Tab"),
        ("L2/R2", "BTN_L", "Page"),
        ("☰", "BTN_S", "Settings"),
    ]

    _HINTS_CATALOG: list[tuple[str, str, str]] = [
        ("A", "BTN_A", "Download"),
        ("B", "BTN_B", "Exit"),
        ("Y", "BTN_Y", "Search"),
        ("L1/R1", "BTN_L", "Tab"),
        ("L2/R2", "BTN_L", "Page"),
        ("☰", "BTN_S", "Settings"),
    ]

    _HINTS_INSTALLED: list[tuple[str, str, str]] = [
        ("A", "BTN_B", "Delete"),
        ("B", "BTN_B", "Exit"),
        ("Y", "BTN_Y", "Search"),
        ("L1/R1", "BTN_L", "Tab"),
        ("L2/R2", "BTN_L", "Page"),
        ("☰", "BTN_S", "Settings"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(theme.CONTROLS_H)
        self.setObjectName("controlsBar")

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(12, 0, 12, 0)
        self._layout.setSpacing(16)
        self._hint_widgets: list[ButtonHint] = []
        self._stretch_added = False

        self.set_mode(self.MODE_SAVES)

    def set_mode(self, mode: str) -> None:
        """Swap the visible hints for *mode* (saves / catalog / installed)."""
        if mode == self.MODE_CATALOG:
            hints = self._HINTS_CATALOG
        elif mode == self.MODE_INSTALLED:
            hints = self._HINTS_INSTALLED
        else:
            hints = self._HINTS_SAVES
        # Tear down the previous pills and stretch so the new set lays out
        # left-aligned with a trailing stretch (matches the original look).
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._hint_widgets = []

        for button, color_attr, label in hints:
            color = getattr(theme, color_attr, theme.TEXT_DIM)
            pill = ButtonHint(button, color, label)
            self._hint_widgets.append(pill)
            self._layout.addWidget(pill)
        self._layout.addStretch()
