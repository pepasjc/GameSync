"""The selected row scrolls its own name when it does not fit.

On a CRT a long filename runs off the end of the name column, so the row
under the cursor crawls left to the end, holds, and snaps back to the first
character. Everything else keeps ellipsizing - scrolling every row at once
would be unreadable.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for candidate in (ROOT, ROOT / "mister"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from gamesync.app import (  # noqa: E402
    MARQUEE_END_HOLD,
    MARQUEE_INTERVAL,
    MARQUEE_START_HOLD,
    MARQUEE_STEP,
    App,
    Row,
)
from gamesync.fb import Framebuffer  # noqa: E402


class Font:
    """One pixel of advance per character, so widths are string lengths."""

    line_height = 8

    def measure(self, text):
        return len(text)

    def ellipsize(self, text, max_width):
        return text if len(text) <= max_width else text[: max(0, max_width - 1)] + "…"


class Renderer:
    def __init__(self):
        self.rendered = []

    def render(self, font, message, fg, bg):
        self.rendered.append(message)
        width = font.measure(message)
        return width, font.line_height, bytearray(width * font.line_height * 4)


class FB:
    def __init__(self):
        self.blits = []

    def blit(self, x, y, w, h, pixels, crop_x=0, crop_w=None):
        self.blits.append({"x": x, "crop_x": crop_x, "crop_w": crop_w, "w": w})

    def fill_rect(self, *a, **kw):
        pass


def make_app(name, tab=0, selected=0):
    app = App.__new__(App)
    app.tab = tab
    app.selected = selected
    app.scroll = 0
    app.font_row = Font()
    app.renderer = Renderer()
    app.fb = FB()
    app._marquee_offset = 0
    app._marquee_span = 0
    app._marquee_next = 0.0
    app._marquee_key = None
    app.rows = lambda: [Row("SNES", name, "", "")]
    # tick_marquee asks for a repaint; drawing a whole row needs the real
    # metrics/theme stack, which is not what these tests are about.
    app.repaints = []
    app.draw_row = lambda index, offset: app.repaints.append((index, offset))
    return app


LONG = "Fire Emblem - Genealogy of the Holy War (Japan) [T-En v1.0].sfc"


# --- deciding whether to scroll at all ---------------------------------------


def test_name_that_fits_is_drawn_whole_and_does_not_scroll():
    app = make_app("Short.sfc")
    app._draw_name_marquee(10, 0, "Short.sfc", 100, (0, 0, 0))
    assert app._marquee_span == 0
    assert app.renderer.rendered == ["Short.sfc"]
    # Plain text path: drawn in one piece, with no source crop.
    assert app.fb.blits[-1]["crop_w"] is None


def test_long_name_sets_up_a_scroll_clipped_to_its_column():
    app = make_app(LONG)
    app._draw_name_marquee(10, 0, LONG, 20, (0, 0, 0))
    assert app._marquee_span == len(LONG) - 20
    blit = app.fb.blits[-1]
    # Cropped to the column, so it cannot run over the system chip to its left.
    assert blit["x"] == 10
    assert blit["crop_w"] == 20
    assert blit["crop_x"] == 0         # starts at the first character


def test_zero_width_column_does_not_scroll():
    app = make_app(LONG)
    app._draw_name_marquee(10, 0, LONG, 0, (0, 0, 0))
    assert app._marquee_span == 0


# --- the crawl ---------------------------------------------------------------


def test_holds_at_the_start_before_crawling():
    app = make_app(LONG)
    app._draw_name_marquee(10, 0, LONG, 20, (0, 0, 0))
    assert app.tick_marquee() is False          # still inside the opening hold
    assert app._marquee_offset == 0


def test_crawls_a_step_at_a_time():
    app = make_app(LONG)
    app._draw_name_marquee(10, 0, LONG, 20, (0, 0, 0))
    app._marquee_next = 0.0                      # opening hold elapsed
    assert app.tick_marquee() is True
    assert app.repaints == [(0, 0)]          # repainted just the selected row
    assert app._marquee_offset == MARQUEE_STEP
    app._marquee_next = 0.0
    app.tick_marquee()
    assert app._marquee_offset == 2 * MARQUEE_STEP


def test_reaching_the_end_holds_then_returns_to_the_first_character():
    app = make_app(LONG)
    app._draw_name_marquee(10, 0, LONG, 20, (0, 0, 0))
    span = app._marquee_span

    while app._marquee_offset < span:
        app._marquee_next = 0.0
        app.tick_marquee()
    assert app._marquee_offset == span           # parked on the last character

    # Parked there for the end hold, not wrapping on the very next frame.
    app.tick_marquee()
    assert app._marquee_offset == span

    app._marquee_next = 0.0
    app.tick_marquee()
    assert app._marquee_offset == 0              # wrapped back to the start


def test_offset_never_overshoots_the_end():
    app = make_app(LONG)
    app._draw_name_marquee(10, 0, LONG, 20, (0, 0, 0))
    for _ in range(500):
        app._marquee_next = 0.0
        app.tick_marquee()
        assert 0 <= app._marquee_offset <= app._marquee_span


def test_scrolled_frame_is_cropped_from_further_into_the_name():
    app = make_app(LONG)
    app._draw_name_marquee(10, 0, LONG, 20, (0, 0, 0))
    app._marquee_next = 0.0
    app.tick_marquee()
    app._draw_name_marquee(10, 0, LONG, 20, (0, 0, 0))
    assert app.fb.blits[-1]["crop_x"] == MARQUEE_STEP


def test_a_row_that_fits_stops_the_tick():
    app = make_app("Short.sfc")
    app._draw_name_marquee(10, 0, "Short.sfc", 100, (0, 0, 0))
    assert app.tick_marquee() is False


# --- restarting ---------------------------------------------------------------


def test_moving_the_cursor_restarts_from_the_first_character():
    app = make_app(LONG)
    app._draw_name_marquee(10, 0, LONG, 20, (0, 0, 0))
    app._marquee_next = 0.0
    app.tick_marquee()
    assert app._marquee_offset > 0

    app.selected = 1                              # cursor moved
    app._draw_name_marquee(10, 0, LONG, 20, (0, 0, 0))
    assert app._marquee_offset == 0


def test_same_index_on_another_tab_restarts():
    app = make_app(LONG)
    app._draw_name_marquee(10, 0, LONG, 20, (0, 0, 0))
    app._marquee_next = 0.0
    app.tick_marquee()
    app.tab = 2                                   # different list, same row index
    app._draw_name_marquee(10, 0, LONG, 20, (0, 0, 0))
    assert app._marquee_offset == 0


def test_redrawing_the_same_row_keeps_its_place():
    app = make_app(LONG)
    app._draw_name_marquee(10, 0, LONG, 20, (0, 0, 0))
    app._marquee_next = 0.0
    app.tick_marquee()
    offset = app._marquee_offset
    app._draw_name_marquee(10, 0, LONG, 20, (0, 0, 0))
    assert app._marquee_offset == offset


def test_pacing_constants_are_sane():
    assert MARQUEE_STEP >= 1
    assert 0 < MARQUEE_INTERVAL < 1
    assert MARQUEE_START_HOLD > MARQUEE_INTERVAL
    assert MARQUEE_END_HOLD > MARQUEE_INTERVAL


# --- the framebuffer crop this all rests on -----------------------------------


def _solid(width, height, value):
    return bytearray([value] * (width * height * 4))


def make_fb(width, height):
    fb = Framebuffer.__new__(Framebuffer)
    fb.width = width
    fb.height = height
    fb.bytes_per_pixel = 4
    fb.stride = width * 4
    fb._origin_x = 0
    fb._origin_y = 0
    fb._map = bytearray(width * height * 4)
    return fb


def test_blit_without_crop_is_unchanged():
    fb = make_fb(8, 1)
    fb.blit(0, 0, 4, 1, _solid(4, 1, 0xFF))
    assert fb._map[:16] == bytearray([0xFF] * 16)
    assert fb._map[16:] == bytearray(16)


def test_blit_crop_skips_the_left_of_the_source():
    fb = make_fb(8, 1)
    # Source: 4 px, first two 0x11, last two 0x22.
    src = bytearray([0x11] * 8 + [0x22] * 8)
    fb.blit(0, 0, 4, 1, src, crop_x=2)
    assert fb._map[:8] == bytearray([0x22] * 8)


def test_blit_crop_width_bounds_the_column():
    fb = make_fb(8, 1)
    src = bytearray([0x33] * 16)
    fb.blit(0, 0, 4, 1, src, crop_x=0, crop_w=2)
    assert fb._map[:8] == bytearray([0x33] * 8)
    assert fb._map[8:] == bytearray(24)      # nothing past the column


def test_blit_crop_past_the_end_of_the_source_draws_nothing():
    fb = make_fb(8, 1)
    fb.blit(0, 0, 4, 1, _solid(4, 1, 0xFF), crop_x=4)
    assert fb._map == bytearray(32)


def test_blit_crop_survives_multiple_rows():
    fb = make_fb(4, 2)
    # 2x2 source; column 0 is 0x11, column 1 is 0x99, on both rows.
    row = bytearray([0x11] * 4 + [0x99] * 4)
    fb.blit(0, 0, 2, 2, row + row, crop_x=1)
    assert fb._map[0:4] == bytearray([0x99] * 4)         # row 0
    assert fb._map[16:20] == bytearray([0x99] * 4)       # row 1
