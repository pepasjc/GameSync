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

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(theme.CONTROLS_H)
        self.setObjectName("controlsBar")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(16)

        self._hints = [
            ButtonHint("A", theme.BTN_A, "Info"),
            ButtonHint("X", theme.BTN_X, "Sync"),
            ButtonHint("Y", theme.BTN_Y, "Refresh"),
            ButtonHint("L1", theme.BTN_L, "System"),
            ButtonHint("R1", theme.BTN_L, "Status"),
            ButtonHint("☰", theme.BTN_S, "Settings"),
        ]

        for h in self._hints:
            layout.addWidget(h)
        layout.addStretch()
