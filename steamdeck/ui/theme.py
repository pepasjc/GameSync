"""Visual theme for the Steam Deck GameSync UI.

The palette is the one every GameSync client draws with (3ds/source/gui.h,
xbox/source/ui.h, ...): deep slate background, lighter panels, teal accent,
green / amber / blue / red status colours and the coloured A/B/X/Y glyphs.
"""

# ── Shared GameSync palette ───────────────────────────────────────
BG        = "#0f1720"   # window
BG2       = "#1b2633"   # header / footer chrome
PANEL     = "#243244"   # cards, rows
PANEL_HI  = "#2c3d52"   # selected row, hovered button
LINE      = "#33465c"   # dividers, outlines
TEXT      = "#e6edf3"
DIM       = "#8da2b5"
MUTED     = "#5e7286"
ACCENT    = "#2ec4b6"   # teal: active tab, selection bar
ACCENT2   = "#3ddbd9"
OK        = "#3fb950"
WARN      = "#f0b429"
INFO      = "#58a6ff"
ERR       = "#f85149"
RA        = "#e5b143"
INK       = "#0f1720"   # dark text on accent fills

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
BTN_L = "#5e7286"   # slate  — shoulders, triggers, d-pad
BTN_S = "#5e7286"   # slate  — START / SELECT

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
    border-bottom: 1px solid {LINE};
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
