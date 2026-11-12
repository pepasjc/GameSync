"""Visual theme for the GameSync desktop client.

The palette is the one every GameSync client draws with (3ds/source/gui.h,
steamdeck/ui/theme.py, android/.../ui/theme/Theme.kt, ...): deep navy
background, lighter panels, cyan accent, green / amber / blue / red status
colours, and the logo's cyan -> blue -> violet gradient as the one flourish.
Keep the hex values in step with those files.

Fusion draws the widgets (so the look is the same on Windows, Linux and
macOS), a QPalette colours everything Fusion paints itself (check boxes,
combo arrows, spin buttons), and the stylesheet restyles the widgets that
carry the brand: tabs, buttons, tables, inputs, scroll bars.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QLinearGradient,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import QApplication, QWidget

# ── Shared GameSync palette ───────────────────────────────────────
BG = "#0b1020"        # window
BG2 = "#141b33"       # header / footer chrome
PANEL = "#1d2746"     # cards, rows, inputs
PANEL_HI = "#2a3760"  # selected row, hovered button
LINE = "#36446e"      # dividers, outlines
TEXT = "#eef2fa"
DIM = "#9ba9cd"
MUTED = "#6b789f"
ACCENT = "#22c3f6"    # cyan: active tab, selection, primary action
ACCENT2 = "#7bddff"
OK = "#3dd68c"
WARN = "#ffb938"
INFO = "#6c93ff"
ERR = "#ff5d6c"
RA = "#e5b143"
INK = "#0b1020"       # dark text on accent fills

# The logo's gradient, left to right.
BRAND_A = "#00d6ff"
BRAND_B = "#3a78ff"
BRAND_C = "#a45cff"
VIOLET = BRAND_C

_ASSETS = Path(__file__).parent / "assets"
_VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"


def tint(color: str, amount: float = 0.22, base: str = PANEL) -> QColor:
    """``color`` mixed into ``base``: a cell background that carries a status
    without drowning the light text drawn on top of it."""
    a, b = QColor(base), QColor(color)
    return QColor(
        round(a.red() + (b.red() - a.red()) * amount),
        round(a.green() + (b.green() - a.green()) * amount),
        round(a.blue() + (b.blue() - a.blue()) * amount),
    )


def brand_gradient(x0: float, x1: float) -> QLinearGradient:
    gradient = QLinearGradient(x0, 0, x1, 0)
    gradient.setColorAt(0.0, QColor(BRAND_A))
    gradient.setColorAt(0.5, QColor(BRAND_B))
    gradient.setColorAt(1.0, QColor(BRAND_C))
    return gradient


def app_version() -> str:
    try:
        return _VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _write_icons() -> tuple[str, str]:
    """The check mark and the combo arrow, as SVG files the stylesheet can
    point at (Qt style sheets take images by URL only)."""
    import tempfile

    folder = Path(tempfile.gettempdir()) / "gamesync-desktop-theme"
    icons = {
        "check.svg": (
            '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 14 14">'
            f'<path d="M3 7.4l2.7 2.7L11 4.6" fill="none" stroke="{INK}" stroke-width="2"'
            ' stroke-linecap="round" stroke-linejoin="round"/></svg>'
        ),
        "arrow.svg": (
            '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 10 10">'
            f'<path d="M1.5 3.5L5 7l3.5-3.5" fill="none" stroke="{DIM}" stroke-width="1.6"'
            ' stroke-linecap="round" stroke-linejoin="round"/></svg>'
        ),
    }
    try:
        folder.mkdir(parents=True, exist_ok=True)
        for name, body in icons.items():
            target = folder / name
            if not target.exists() or target.read_text(encoding="utf-8") != body:
                target.write_text(body, encoding="utf-8")
    except OSError:
        return "", ""
    return (folder / "check.svg").as_posix(), (folder / "arrow.svg").as_posix()


CHECK_ICON, ARROW_ICON = _write_icons()

STYLESHEET = f"""
QWidget {{
    color: {TEXT};
    font-size: 10pt;
}}
QMainWindow, QDialog {{
    background: {BG};
}}
QToolTip {{
    background: {BG2};
    color: {TEXT};
    border: 1px solid {LINE};
    padding: 4px 6px;
}}

/* ── Menus ── */
QMenuBar {{
    background: {BG2};
    color: {DIM};
    border: none;
    padding: 2px 6px;
}}
QMenuBar::item {{
    background: transparent;
    padding: 5px 10px;
    border-radius: 5px;
}}
QMenuBar::item:selected {{
    background: {PANEL};
    color: {TEXT};
}}
QMenu {{
    background: {BG2};
    border: 1px solid {LINE};
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 26px 6px 14px;
    border-radius: 5px;
}}
QMenu::item:selected {{
    background: {PANEL_HI};
}}
QMenu::item:disabled {{
    color: {MUTED};
}}
QMenu::separator {{
    height: 1px;
    background: {LINE};
    margin: 4px 8px;
}}

/* ── Tabs ── */
QTabWidget::pane {{
    border: none;
    border-top: 1px solid {LINE};
    background: {BG};
}}
QTabBar {{
    background: transparent;
}}
QTabBar::tab {{
    background: transparent;
    color: {DIM};
    padding: 7px 16px;
    margin: 6px 2px 6px 2px;
    border: none;
    border-radius: 14px;
}}
QTabBar::tab:hover {{
    background: {PANEL};
    color: {TEXT};
}}
QTabBar::tab:selected {{
    background: {ACCENT};
    color: {INK};
    font-weight: 600;
}}
QTabBar::tab:disabled {{
    color: {MUTED};
}}

/* ── Buttons ── */
QPushButton {{
    background: {PANEL};
    color: {TEXT};
    border: 1px solid {LINE};
    border-radius: 6px;
    padding: 6px 14px;
    min-height: 16px;
}}
QPushButton:hover {{
    background: {PANEL_HI};
    border-color: {ACCENT};
}}
QPushButton:pressed {{
    background: {ACCENT};
    color: {INK};
}}
QPushButton:default {{
    border-color: {ACCENT};
}}
QPushButton:focus {{
    border-color: {ACCENT};
}}
QPushButton:disabled {{
    background: {BG2};
    color: {MUTED};
    border-color: {BG2};
}}
QPushButton:checked {{
    background: {ACCENT};
    color: {INK};
    border-color: {ACCENT};
}}
QToolButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 4px 8px;
}}
QToolButton:hover {{
    background: {PANEL};
    border-color: {LINE};
}}

/* ── Inputs ── */
QLineEdit, QTextEdit, QPlainTextEdit {{
    background: {BG2};
    color: {TEXT};
    border: 1px solid {LINE};
    border-radius: 6px;
    padding: 5px 8px;
    selection-background-color: {ACCENT};
    selection-color: {INK};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border-color: {ACCENT};
}}
QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled {{
    color: {MUTED};
    border-color: {BG2};
}}
QLineEdit:read-only {{
    color: {DIM};
}}

/* ── Check boxes and radio buttons ── */
QCheckBox, QRadioButton {{
    spacing: 7px;
}}
QCheckBox::indicator, QRadioButton::indicator,
QTableView::indicator, QTreeView::indicator, QListView::indicator {{
    width: 14px;
    height: 14px;
    background: {BG2};
    border: 1px solid {MUTED};
    border-radius: 4px;
}}
QRadioButton::indicator {{
    border-radius: 8px;
}}
QCheckBox::indicator:hover, QRadioButton::indicator:hover,
QTableView::indicator:hover, QTreeView::indicator:hover, QListView::indicator:hover {{
    border-color: {ACCENT};
}}
QCheckBox::indicator:checked, QRadioButton::indicator:checked,
QTableView::indicator:checked, QTreeView::indicator:checked, QListView::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
    image: url("{CHECK_ICON}");
}}
QRadioButton::indicator:checked {{
    image: none;
    border: 4px solid {BG2};
    background: {ACCENT};
}}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
    border-color: {LINE};
    background: {BG};
}}
QCheckBox:disabled, QRadioButton:disabled {{
    color: {MUTED};
}}

/* ── Combo and spin boxes ── */
QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QTimeEdit {{
    background: {PANEL};
    color: {TEXT};
    border: 1px solid {LINE};
    border-radius: 6px;
    padding: 4px 8px;
    min-height: 18px;
    selection-background-color: {ACCENT};
    selection-color: {INK};
}}
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {{
    border-color: {MUTED};
}}
QComboBox:focus, QComboBox:on, QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {ACCENT};
}}
QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    background: {BG2};
    color: {MUTED};
    border-color: {BG2};
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 24px;
    border: none;
}}
QComboBox::down-arrow {{
    image: url("{ARROW_ICON}");
    width: 10px;
    height: 10px;
}}
QComboBox QAbstractItemView {{
    background: {BG2};
    border: 1px solid {LINE};
    border-radius: 0;
    selection-background-color: {PANEL_HI};
    selection-color: {TEXT};
    outline: 0;
}}

/* ── Group boxes ── */
QGroupBox {{
    background: {BG2};
    border: 1px solid {LINE};
    border-radius: 8px;
    margin-top: 12px;
    padding-top: 10px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 6px;
    color: {ACCENT2};
    font-weight: 600;
}}

/* ── Tables, trees, lists ── */
QTableView, QTreeView, QListView {{
    background: {BG2};
    alternate-background-color: #111730;
    color: {TEXT};
    border: 1px solid {LINE};
    border-radius: 8px;
    gridline-color: #232d52;
    selection-background-color: {PANEL_HI};
    selection-color: {TEXT};
    outline: 0;
}}
QTableView::item, QTreeView::item, QListView::item {{
    padding: 3px 6px;
}}
QTableView::item:selected, QTreeView::item:selected, QListView::item:selected {{
    background: {PANEL_HI};
    color: {TEXT};
}}
QTableView::item:hover, QTreeView::item:hover, QListView::item:hover {{
    background: {PANEL};
}}
QHeaderView {{
    background: transparent;
}}
QHeaderView::section {{
    background: {PANEL};
    color: {DIM};
    border: none;
    border-right: 1px solid {BG2};
    border-bottom: 1px solid {LINE};
    padding: 6px 8px;
    font-weight: 600;
}}
QHeaderView::section:hover {{
    color: {TEXT};
}}
QTableCornerButton::section {{
    background: {PANEL};
    border: none;
    border-bottom: 1px solid {LINE};
}}

/* ── Scroll bars ── */
QScrollBar:vertical {{
    background: transparent;
    width: 12px;
    margin: 2px;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 12px;
    margin: 2px;
}}
QScrollBar::handle {{
    background: {LINE};
    border-radius: 4px;
}}
QScrollBar::handle:vertical {{
    min-height: 32px;
}}
QScrollBar::handle:horizontal {{
    min-width: 32px;
}}
QScrollBar::handle:hover {{
    background: {MUTED};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0;
    height: 0;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}

/* ── Progress ── */
QProgressBar {{
    background: {BG2};
    border: 1px solid {LINE};
    border-radius: 6px;
    text-align: center;
    color: {TEXT};
    min-height: 14px;
}}
QProgressBar::chunk {{
    border-radius: 5px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {BRAND_A}, stop:0.5 {BRAND_B}, stop:1 {BRAND_C});
}}

/* ── Odds and ends ── */
QSplitter::handle {{
    background: {LINE};
}}
QSplitter::handle:horizontal {{
    width: 1px;
}}
QSplitter::handle:vertical {{
    height: 1px;
}}
QStatusBar {{
    background: {BG2};
    color: {DIM};
    border-top: 1px solid {LINE};
}}
QLabel#subText {{
    color: {DIM};
}}
QLabel[status="ok"] {{ color: {OK}; }}
QLabel[status="warn"] {{ color: {WARN}; }}
QLabel[status="error"] {{ color: {ERR}; }}
QLabel[status="muted"] {{ color: {MUTED}; }}
"""


def _palette() -> QPalette:
    palette = QPalette()
    role = QPalette.ColorRole
    for key, value in (
        (role.Window, BG),
        (role.WindowText, TEXT),
        (role.Base, BG2),
        (role.AlternateBase, "#111730"),
        (role.ToolTipBase, BG2),
        (role.ToolTipText, TEXT),
        (role.Text, TEXT),
        (role.Button, PANEL),
        (role.ButtonText, TEXT),
        (role.BrightText, TEXT),
        (role.Link, ACCENT),
        (role.LinkVisited, VIOLET),
        (role.Highlight, ACCENT),
        (role.HighlightedText, INK),
        (role.PlaceholderText, MUTED),
        (role.Light, PANEL_HI),
        (role.Midlight, PANEL),
        (role.Mid, LINE),
        (role.Dark, BG),
        (role.Shadow, "#000000"),
    ):
        palette.setColor(key, QColor(value))
    disabled = QPalette.ColorGroup.Disabled
    for key in (role.WindowText, role.Text, role.ButtonText):
        palette.setColor(disabled, key, QColor(MUTED))
    palette.setColor(disabled, role.Highlight, QColor(PANEL_HI))
    return palette


def apply(app: QApplication) -> None:
    """Dress the whole application in the GameSync look."""
    app.setStyle("Fusion")
    app.setPalette(_palette())
    app.setStyleSheet(STYLESHEET)


def set_status(label, status: str) -> None:
    """Colour a QLabel as ``ok`` / ``warn`` / ``error`` / ``muted``."""
    label.setProperty("status", status)
    label.style().unpolish(label)
    label.style().polish(label)


class BrandHeader(QWidget):
    """The strip every GameSync client opens with: logo mark, the two-tone
    wordmark, the version, and the gradient rule underneath."""

    HEIGHT = 52
    RULE = 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.HEIGHT)
        self._logo = QPixmap(str(_ASSETS / "gamesync.png"))
        self._version = app_version()

    def paintEvent(self, event):  # noqa: N802 (Qt override)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        width, height = self.width(), self.height()
        painter.fillRect(0, 0, width, height, QColor(BG2))

        body = height - self.RULE
        x = 16.0
        if not self._logo.isNull():
            size = 32
            painter.drawPixmap(
                QRectF(x, (body - size) / 2, size, size),
                self._logo,
                QRectF(self._logo.rect()),
            )
            x += size + 10

        font = QFont(self.font())
        font.setPointSize(15)
        font.setBold(True)
        painter.setFont(font)
        metrics = QFontMetricsF(font)
        baseline = (body + metrics.ascent() - metrics.descent()) / 2
        painter.setPen(QColor(TEXT))
        painter.drawText(int(x), int(baseline), "Game")
        x += metrics.horizontalAdvance("Game")
        sync_w = metrics.horizontalAdvance("Sync")
        painter.setPen(QPen(QBrush(brand_gradient(x, x + sync_w)), 1))
        painter.drawText(int(x), int(baseline), "Sync")
        x += sync_w

        small = QFont(self.font())
        small.setPointSize(9)
        painter.setFont(small)
        painter.setPen(QColor(MUTED))
        painter.drawText(
            QRectF(x + 12, 0, 300, body),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            "SAVE SYNC FOR EVERY CONSOLE",
        )
        if self._version:
            painter.setPen(QColor(DIM))
            painter.drawText(
                QRectF(0, 0, width - 16, body),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                f"v{self._version}",
            )

        painter.fillRect(
            QRectF(0, body, width, self.RULE), QBrush(brand_gradient(0, width))
        )
        painter.end()
