"""Colour palette and layout metrics for the MiSTer GameSync UI.

System accent colours are taken from shared/systems.py so the MiSTer client,
the web UI and the desktop client all tint a system the same way.
"""

from __future__ import annotations

try:
    from shared.systems import SYSTEM_COLOR, DEFAULT_SYSTEM_COLOR
except Exception:  # pragma: no cover - the client always vendors shared/
    SYSTEM_COLOR = {}
    DEFAULT_SYSTEM_COLOR = "#607d8b"


def hex_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def mix(a: tuple[int, int, int], b: tuple[int, int, int], amount: float):
    """Blend two colours ahead of time so nothing blends per pixel later."""
    return (
        int(a[0] + (b[0] - a[0]) * amount),
        int(a[1] + (b[1] - a[1]) * amount),
        int(a[2] + (b[2] - a[2]) * amount),
    )


# The palette every GameSync client draws with (3ds/source/gui.h,
# desktop/theme.py, ...): deep navy, cyan accent, the logo's gradient. Keep
# the values in step with those files.
BACKGROUND = (0x0B, 0x10, 0x20)
HEADER = (0x14, 0x1B, 0x33)
TAB_BAR = (0x10, 0x16, 0x2B)
ROW = (0x11, 0x18, 0x31)
ROW_ALT = (0x0D, 0x13, 0x26)
ROW_SELECTED = (0x28, 0x40, 0x79)
FOOTER = (0x08, 0x0C, 0x1A)
DIVIDER = (0x28, 0x33, 0x5A)

TEXT = (0xEE, 0xF2, 0xFA)
TEXT_DIM = (0x9B, 0xA9, 0xCD)
TEXT_STRONG = (0xFF, 0xFF, 0xFF)

ACCENT = (0x22, 0xC3, 0xF6)
OK = (0x3D, 0xD6, 0x8C)
WARN = (0xFF, 0xB9, 0x38)
DANGER = (0xFF, 0x5D, 0x6C)

#: The logo's gradient, left to right: the rule under the header.
BRAND = ((0x00, 0xD6, 0xFF), (0x3A, 0x78, 0xFF), (0xA4, 0x5C, 0xFF))


def brand_steps(count: int = 32):
    """The brand gradient as ``count`` flat colours, so the rule is a handful
    of rectangle fills rather than a per-pixel blend."""
    half = max(1, count // 2)
    steps = []
    for index in range(count):
        if index < half:
            steps.append(mix(BRAND[0], BRAND[1], index / half))
        else:
            steps.append(mix(BRAND[1], BRAND[2],
                             (index - half) / max(1, count - half - 1)))
    return steps

CHIP_TEXT = (0xFF, 0xFF, 0xFF)
#: RetroAchievements badge. Gold, so it reads as a reward rather than
#: as another status, and stays legible on a CRT's smeared colour.
RA_BADGE = (0xD8, 0xA7, 0x2B)
#: Title-only match: RA has a set for a game of this name, but nothing has
#: verified this particular dump. Muted so it cannot be mistaken for the
#: exact badge at a glance.
RA_BADGE_WEAK = (0x8A, 0x71, 0x2E)
RA_BADGE_TEXT = (0x16, 0x12, 0x06)

SCROLL_TRACK = (0x18, 0x20, 0x39)
SCROLL_THUMB = (0x41, 0x4E, 0x7A)

STATUS_COLORS = {
    "synced": OK,
    "local": TEXT_DIM,
    "upload": ACCENT,
    "download": ACCENT,
    "server only": WARN,
    "conflict": DANGER,
    "error": DANGER,
    "empty": TEXT_DIM,
    "not installed": TEXT_DIM,
    "installed": OK,
}


#: The desktop palette, captured before anything re-tunes it, so a style
#: change at runtime can always get back to it.
_PALETTE_NAMES = ("BACKGROUND", "HEADER", "TAB_BAR", "ROW", "ROW_ALT",
                  "ROW_SELECTED", "FOOTER", "DIVIDER", "TEXT", "TEXT_DIM",
                  "TEXT_STRONG", "ACCENT", "TITLE", "SCROLL_TRACK",
                  "SCROLL_THUMB")
TITLE = TEXT_STRONG
_BASE = None

#: 15 kHz tweaks over the desktop palette (see apply_crt_palette).
CRT_PALETTE = {
    "BACKGROUND": (0x06, 0x09, 0x14),
    "ROW_ALT": (0x0A, 0x0E, 0x1E),
    "ROW_SELECTED": (0x2E, 0x4C, 0x8C),
    "TEXT_DIM": (0xB4, 0xBF, 0xDD),
    "DIVIDER": (0x41, 0x4E, 0x7A),
}

#: The dense layout's look, after DreamSTer and the RetroAchievements overlay
#: it shares a UI with: a flat navy page, gold title, thin rules and one
#: full-width bar for the selection. No striping - at 10 lines a row, two
#: shades of navy read as flicker rather than as rows.
DENSE_PALETTE = {
    "BACKGROUND": (16, 24, 56),
    "HEADER": (16, 24, 56),
    "TAB_BAR": (16, 24, 56),
    "ROW": (16, 24, 56),
    "ROW_ALT": (16, 24, 56),
    "ROW_SELECTED": (48, 72, 150),
    "FOOTER": (16, 24, 56),
    "DIVIDER": (80, 96, 140),
    "TEXT": (240, 240, 240),
    "TEXT_DIM": (150, 158, 180),
    "TEXT_STRONG": (255, 255, 255),
    "ACCENT": (120, 184, 255),
    "TITLE": (255, 208, 64),
    "SCROLL_TRACK": (28, 38, 80),
    "SCROLL_THUMB": (80, 96, 140),
}


def use_palette(name: str = "desktop") -> None:
    """Switch every palette global: "desktop", "crt" or "dense".

    Mutates the module globals because every draw site reads them directly.
    The desktop values are snapshotted on first use, so switching back and
    forth (the UI style setting does) never compounds one tweak on another.
    """
    global _BASE
    module = globals()
    if _BASE is None:
        _BASE = {key: module[key] for key in _PALETTE_NAMES}
    module.update(_BASE)
    if name == "crt":
        module.update(CRT_PALETTE)
    elif name == "dense":
        module.update(DENSE_PALETTE)
    STATUS_COLORS.update({
        "synced": OK, "local": module["TEXT_DIM"],
        "upload": module["ACCENT"], "download": module["ACCENT"],
        "empty": module["TEXT_DIM"], "not installed": module["TEXT_DIM"],
    })


def apply_crt_palette() -> None:
    """Re-tune the palette for a 15 kHz tube.

    Two problems with the desktop palette on a CRT. The alternating row shades
    are three levels apart, which survives an LCD and vanishes into a tube's
    gamma curve. And dim text at 0x8A is legible on a panel but muddy at 240p,
    where each glyph has a third of the scanlines to work with.
    """
    use_palette("crt")


def system_color(system: str) -> tuple[int, int, int]:
    value = SYSTEM_COLOR.get((system or "").upper(), DEFAULT_SYSTEM_COLOR)
    try:
        return hex_rgb(value)
    except Exception:
        return hex_rgb(DEFAULT_SYSTEM_COLOR)


#: Below this many scanlines the display is a 240p-class CRT rather than a
#: monitor. The framebuffer driver on MiSTer reports pixclock 0 and vmode 0,
#: so there is no timing to derive the horizontal rate from - the geometry is
#: the only signal available.
LOWRES_MAX_HEIGHT = 288


class Metrics:
    """Layout derived from the framebuffer geometry.

    MiSTer changes video mode at runtime, so nothing here may be a constant.

    Two profiles, not one curve. Scaling the desktop layout linearly collapses
    at 240p: a 0.33 factor gives 12-scanline rows holding a 13px font and a
    20x7 chip holding a 10px one. The low-resolution profile is sized from
    what a CRT can actually resolve instead.

    ``pixel_aspect`` is pixel width over pixel height. Horizontal metrics are
    divided by it so a 640x240 mode, where pixels are twice as tall as wide,
    gets twice as many pixels of padding for the same visual gap.

    ``dense`` is a third profile, a text-mode grid of 8x8 cells in 10-line
    rows (see bitfont.py), at ``scale`` framebuffer pixels per dot. It fits
    twice the rows of the low-resolution profile, and has the width for the
    chip and the detail column too: 640 pixels is 80 cells.
    """

    def __init__(self, width: int, height: int, pixel_aspect: float = 1.0,
                 dense: bool = False, scale: tuple = (1, 1)):
        self.width = width
        self.height = height
        self.pixel_aspect = pixel_aspect if pixel_aspect > 0 else 1.0
        #: Horizontal pixels per unit of visual width.
        xs = 1.0 / self.pixel_aspect
        self.lowres = height <= LOWRES_MAX_HEIGHT
        #: Drop the system chip and the secondary detail column.
        self.compact = self.lowres and not dense
        self.dense = dense
        self.scale = (max(1, scale[0]), max(1, scale[1])) if dense else (1, 1)

        if dense:
            sx, sy = self.scale
            #: One character cell, and one canvas line.
            self.cell_w = 8 * sx
            self.line = sy
            self.pad = self.cell_w
            # Two text rows (title + tabs, then the scope line) and a rule.
            self.header_h = 23 * sy
            self.tab_h = 0
            self.footer_h = 12 * sy
            self.row_h = 10 * sy
            # Six cells of label: SEGACD, NEOGEO, ARCADE fit whole.
            self.chip_w = 7 * self.cell_w
            self.chip_h = 9 * sy
            self.scroll_w = 2 * sx
            # Fonts are BitmapFonts at self.scale; the sizes only tell
            # _relayout whether they need rebuilding.
            self.font_title = self.font_row = ("bitmap",) + self.scale
            self.font_small = self.font_chip = ("bitmap",) + self.scale
        elif self.lowres:
            self.pad = int(7 * xs)
            self.header_h = 22
            self.tab_h = 18
            self.footer_h = 15
            self.row_h = 20
            # A chip that can only ever show "G..." is noise, not information;
            # the system is in the name and in the filter line already.
            self.chip_w = 0
            self.chip_h = 0
            self.scroll_w = max(2, int(3 * xs))

            self.font_title = 15
            self.font_row = 14
            self.font_small = 11
            self.font_chip = 10
        else:
            scale = height / 720.0
            self.pad = int(24 * scale * xs)
            self.header_h = int(62 * scale)
            self.tab_h = int(46 * scale)
            self.footer_h = int(40 * scale)
            self.row_h = int(38 * scale)
            self.chip_w = int(62 * scale * xs)
            self.chip_h = int(22 * scale)
            self.scroll_w = max(1, int(6 * scale * xs))

            self.font_title = max(18, int(27 * scale))
            self.font_row = max(13, int(19 * scale))
            self.font_small = max(11, int(15 * scale))
            self.font_chip = max(10, int(13 * scale))

        self.list_top = self.header_h + self.tab_h
        self.list_bottom = height - self.footer_h
        self.visible_rows = max(1, (self.list_bottom - self.list_top)
                                // self.row_h)


def wants_dense(style: str, phys_height: int) -> bool:
    """``UI_STYLE`` resolved: "auto" is dense on a 240p-class display, where
    the room it wins back matters most, and FreeType on a monitor."""
    if style == "dense":
        return True
    if style == "classic":
        return False
    return phys_height <= LOWRES_MAX_HEIGHT


def dense_scale(phys_width: int, phys_height: int) -> tuple:
    """Whole-pixel scale for the 8x8 grid on this framebuffer.

    From the physical mode, not the overscan viewport, so calibrating the
    margins never changes the glyph size. 640x240 is 1:1, 640x480 doubles
    the lines (the 480i look of the same grid), 1280x720 is 2x2 and
    1920x1080 3x3 - DreamSTer's choices, so both read alike on one screen.
    """
    sx = max(1, phys_width // 640)
    if phys_height < 600:
        sy = max(1, phys_height // 240)
    else:
        sy = max(1, phys_height // 360)
    return sx, sy
