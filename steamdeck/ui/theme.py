"""Visual theme for the Steam Deck GameSync UI.

The palette is the one every GameSync client draws with (3ds/source/gui.h,
xbox/source/ui.h, ...): deep navy background, lighter panels, cyan accent,
green / amber / blue / red status colours and the coloured A/B/X/Y glyphs.
"""

# ── Shared GameSync palette ───────────────────────────────────────
BG        = "#0b1020"   # window
BG2       = "#141b33"   # header / footer chrome
PANEL     = "#1D2746"   # cards, rows
PANEL_HI  = "#2a3760"   # selected row, hovered button
LINE      = "#36446e"   # dividers, outlines
TEXT      = "#eef2fa"
DIM       = "#9ba9cd"
MUTED     = "#6b789f"
ACCENT    = "#22c3f6"   # cyan: active tab, selection bar
ACCENT2   = "#7bddff"
OK        = "#3dd68c"
WARN      = "#ffb938"
INFO      = "#6c93ff"
ERR       = "#ff5d6c"
RA        = "#e5b143"
INK       = "#0b1020"   # dark text on accent fills

# The logo's gradient, left to right: the rule under the top bar.
BRAND_A   = "#00d6ff"
BRAND_B   = "#3a78ff"
BRAND_C   = "#a45cff"

# ── Aliases used throughout the widgets ───────────────────────────
BG_WINDOW    = BG
BG_TOPBAR    = BG2
BG_FILTERBAR = BG2
BG_CARD      = PANEL
BG_CARD_SEL  = PANEL_HI
BG_DIALOG    = BG2

ACCENT_HOVER = ACCENT2

TEXT_PRIMARY   = TEXT
TEXT_SECONDARY = DIM
TEXT_DIM       = MUTED

# ── Status colours ────────────────────────────────────────────────
STATUS_SYNCED       = OK
STATUS_UPLOAD       = INFO
STATUS_DOWNLOAD     = INFO
STATUS_CONFLICT     = ERR
STATUS_LOCAL_ONLY   = INFO
STATUS_SERVER_ONLY  = WARN
STATUS_NO_SAVE      = MUTED
STATUS_UNKNOWN      = MUTED

# ── Button glyphs (shared with the console clients) ───────────────
BTN_A = "#5fbf3f"   # green  — confirm / main action
BTN_B = "#e5483f"   # red    — cancel / back
BTN_X = "#3d8fe0"   # blue   — secondary action
BTN_Y = "#f2c230"   # yellow — details / search
BTN_L = "#6b789f"   # slate  — shoulders, triggers, d-pad
BTN_S = "#6b789f"   # slate  — START / SELECT

# ── Dimensions ───────────────────────────────────────────────────
HEADER_H      = 60
TOPBAR_H      = HEADER_H
SUBBAR_H      = 44
FILTERBAR_H   = SUBBAR_H
CONTROLS_H    = 48
BANNER_H      = 34
DETAIL_W      = 400
CARD_H        = 68
CARD_RADIUS   = 8
BADGE_RADIUS  = 10   # pills

# RetroAchievements badge. Gold, so it reads as a reward rather than as
# another sync status.
RA_BADGE      = RA
# Title-only match: a set exists for a game of this name, but this dump
# was never verified against it.
RA_BADGE_WEAK = "#8a712e"
RA_BADGE_TEXT = "#161206"
FONT_TITLE    = 14   # pt
FONT_SUBTITLE = 10   # pt
FONT_BADGE    = 9    # pt
FONT_CONTROLS = 11   # pt

STYLESHEET = f"""
QMainWindow, QWidget#centralWidget {{
    background: {BG};
}}
QWidget#topBar, QWidget#filterBar, QWidget#controlsBar {{
    background: {BG2};
    border: none;
}}
QWidget#topBar {{
    border-bottom: 2px solid qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {BRAND_A}, stop:0.5 {BRAND_B}, stop:1 {BRAND_C});
}}
QWidget#controlsBar {{
    border-top: 1px solid {LINE};
}}
QLabel {{
    color: {TEXT};
    background: transparent;
}}
QLabel#subText {{
    color: {DIM};
}}
QListView {{
    background: {BG};
    border: none;
    outline: 0;
}}
QListView::item {{
    background: transparent;
    border: none;
}}
QScrollBar:vertical {{
    background: {BG};
    width: 6px;
    border-radius: 3px;
}}
QScrollBar::handle:vertical {{
    background: {LINE};
    border-radius: 3px;
    min-height: 30px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QLineEdit#searchBox {{
    background: {PANEL};
    color: {TEXT};
    border: 1px solid {LINE};
    border-radius: 6px;
    padding: 4px 10px;
    font-size: 13pt;
}}
QLineEdit#searchBox:focus {{
    border-color: {ACCENT};
}}
QPushButton {{
    background: {PANEL};
    color: {TEXT};
    border: 1px solid {LINE};
    border-radius: 6px;
    padding: 6px 14px;
    font-size: 11pt;
}}
QPushButton:hover {{
    background: {PANEL_HI};
    border-color: {ACCENT};
}}
QPushButton:pressed {{
    background: {ACCENT};
    color: {INK};
}}
QDialog {{
    background: {BG2};
}}
"""
