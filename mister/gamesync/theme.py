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


BACKGROUND = (0x14, 0x16, 0x1C)
HEADER = (0x1B, 0x1E, 0x27)
TAB_BAR = (0x17, 0x1A, 0x22)
ROW = (0x1A, 0x1D, 0x26)
ROW_ALT = (0x17, 0x19, 0x21)
ROW_SELECTED = (0x27, 0x3A, 0x5B)
FOOTER = (0x11, 0x13, 0x18)
DIVIDER = (0x25, 0x29, 0x34)

TEXT = (0xE8, 0xEA, 0xF0)
TEXT_DIM = (0x8A, 0x92, 0xA6)
TEXT_STRONG = (0xFF, 0xFF, 0xFF)

ACCENT = (0x4D, 0xA3, 0xFF)
OK = (0x56, 0xC4, 0x86)
WARN = (0xF7, 0xB5, 0x38)
DANGER = (0xE8, 0x5D, 0x75)
CHIP_TEXT = (0xFF, 0xFF, 0xFF)
#: RetroAchievements badge. Gold, so it reads as a reward rather than
#: as another status, and stays legible on a CRT's smeared colour.
RA_BADGE = (0xD8, 0xA7, 0x2B)
RA_BADGE_TEXT = (0x16, 0x12, 0x06)

SCROLL_TRACK = (0x1F, 0x22, 0x2C)
SCROLL_THUMB = (0x3C, 0x44, 0x58)

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


def apply_crt_palette() -> None:
    """Re-tune the palette for a 15 kHz tube.

    Two problems with the desktop palette on a CRT. The alternating row shades
    are three levels apart, which survives an LCD and vanishes into a tube's
    gamma curve. And dim text at 0x8A is legible on a panel but muddy at 240p,
    where each glyph has a third of the scanlines to work with.

    Mutates the module globals because every draw site reads them directly.
    Safe to call once at startup; there is no way back to the LCD palette
    without restarting, and nothing needs one.
    """
    global ROW_ALT, ROW_SELECTED, TEXT_DIM, DIVIDER, BACKGROUND

    BACKGROUND = (0x0A, 0x0B, 0x10)
    ROW_ALT = (0x0E, 0x10, 0x16)
    ROW_SELECTED = (0x2E, 0x4C, 0x7C)
    TEXT_DIM = (0xA8, 0xB0, 0xC2)
    DIVIDER = (0x39, 0x40, 0x52)


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
    """

    def __init__(self, width: int, height: int, pixel_aspect: float = 1.0):
        self.width = width
        self.height = height
        self.pixel_aspect = pixel_aspect if pixel_aspect > 0 else 1.0
        #: Horizontal pixels per unit of visual width.
        xs = 1.0 / self.pixel_aspect
        self.lowres = height <= LOWRES_MAX_HEIGHT
        #: Drop the system chip and the secondary detail column.
        self.compact = self.lowres

        if self.lowres:
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
