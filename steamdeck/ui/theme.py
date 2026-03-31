"""Visual theme constants for the Steam Deck SaveSync UI."""

# ── Background layers ─────────────────────────────────────────────
BG_WINDOW    = "#0d1117"
BG_TOPBAR    = "#161b22"
BG_FILTERBAR = "#161b22"
BG_CARD      = "#1c2128"
BG_CARD_SEL  = "#2d333b"
BG_DIALOG    = "#1c2128"

# ── Accent / interactive ──────────────────────────────────────────
ACCENT       = "#58a6ff"   # Steam-style blue
ACCENT_HOVER = "#79b8ff"

# ── Text ─────────────────────────────────────────────────────────
TEXT_PRIMARY   = "#e6edf3"
TEXT_SECONDARY = "#8b949e"
TEXT_DIM       = "#484f58"

# ── Status badge colors ──────────────────────────────────────────
STATUS_SYNCED       = "#3fb950"
STATUS_UPLOAD       = "#d29922"
STATUS_DOWNLOAD     = "#58a6ff"
STATUS_CONFLICT     = "#f85149"
STATUS_LOCAL_ONLY   = "#d29922"
STATUS_SERVER_ONLY  = "#58a6ff"
STATUS_NO_SAVE      = "#484f58"
STATUS_UNKNOWN      = "#6e7681"

# ── Button hint pills ────────────────────────────────────────────
BTN_A = "#3fb950"   # green  — confirm / upload
BTN_B = "#f85149"   # red    — back / download
BTN_X = "#58a6ff"   # blue   — sync
BTN_Y = "#d29922"   # yellow — refresh / search
BTN_L = "#8b949e"   # gray   — shoulder buttons
BTN_S = "#484f58"   # dark   — start/select

# ── Dimensions ───────────────────────────────────────────────────
TOPBAR_H      = 56
FILTERBAR_H   = 38
CONTROLS_H    = 48
CARD_H        = 72
CARD_RADIUS   = 8
BADGE_RADIUS  = 4
FONT_TITLE    = 15   # pt
FONT_SUBTITLE = 11   # pt
FONT_BADGE    = 10   # pt
FONT_CONTROLS = 11   # pt

STYLESHEET = f"""
QMainWindow, QWidget#centralWidget {{
    background: {BG_WINDOW};
}}
QWidget#topBar, QWidget#filterBar, QWidget#controlsBar {{
    background: {BG_TOPBAR};
    border: none;
}}
QLabel {{
    color: {TEXT_PRIMARY};
    background: transparent;
}}
QLabel#subText {{
    color: {TEXT_SECONDARY};
}}
QListView {{
    background: {BG_WINDOW};
    border: none;
    outline: 0;
}}
QListView::item {{
    background: transparent;
    border: none;
}}
QScrollBar:vertical {{
    background: {BG_WINDOW};
    width: 6px;
    border-radius: 3px;
}}
QScrollBar::handle:vertical {{
    background: {TEXT_DIM};
    border-radius: 3px;
    min-height: 30px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QLineEdit#searchBox {{
    background: {BG_CARD};
    color: {TEXT_PRIMARY};
    border: 1px solid {TEXT_DIM};
    border-radius: 4px;
    padding: 4px 10px;
    font-size: 13pt;
}}
QLineEdit#searchBox:focus {{
    border-color: {ACCENT};
}}
QPushButton {{
    background: {BG_CARD};
    color: {TEXT_PRIMARY};
    border: 1px solid {TEXT_DIM};
    border-radius: 4px;
    padding: 6px 14px;
    font-size: 11pt;
}}
QPushButton:hover {{
    background: {BG_CARD_SEL};
    border-color: {ACCENT};
}}
QPushButton:pressed {{
    background: {ACCENT};
    color: {BG_WINDOW};
}}
QDialog {{
    background: {BG_DIALOG};
}}
"""
