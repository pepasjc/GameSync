"""The dense UI style: an 8x8 text-mode grid that doubles the rows at 240p.

A CRT gets it by default (UI_STYLE=auto), a monitor keeps the FreeType look
unless the config asks for dense, and the glyphs scale by whole pixels so
they stay sharp in every mode.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for candidate in (ROOT, ROOT / "mister"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from gamesync import config as gsconfig  # noqa: E402
from gamesync import theme  # noqa: E402
from gamesync.bitfont import BitmapFont  # noqa: E402


def test_auto_is_dense_on_a_crt_only():
    assert theme.wants_dense("auto", 240)
    assert not theme.wants_dense("auto", 720)
    assert theme.wants_dense("dense", 1080)
    assert not theme.wants_dense("classic", 240)


def test_scale_follows_the_physical_mode():
    assert theme.dense_scale(640, 240) == (1, 1)
    assert theme.dense_scale(640, 480) == (1, 2)
    assert theme.dense_scale(1280, 720) == (2, 2)
    assert theme.dense_scale(1920, 1080) == (3, 3)
    assert theme.dense_scale(320, 240) == (1, 1)


def test_dense_fits_twice_the_rows_at_240p():
    # 640x240 inside the CRT default overscan (5% / 8%).
    classic = theme.Metrics(608, 202, 0.5)
    dense = theme.Metrics(608, 202, 0.5, dense=True, scale=(1, 1))
    assert dense.visible_rows >= 2 * classic.visible_rows
    assert dense.row_h == 10
    # 80 columns has room for the chip and the detail column.
    assert not dense.compact and dense.chip_w


def test_bitmap_font_scales_by_whole_pixels():
    font = BitmapFont(2, 3)
    assert font.line_height == 24
    assert font.measure("abc") == 48
    width, height, mask = font.coverage_run("H")
    assert (width, height) == (16, 24)
    assert set(mask) <= {0, 255}


def test_bitmap_font_folds_what_it_cannot_draw():
    font = BitmapFont()
    assert font.glyph("é") is font.glyph("e")
    assert font.glyph("’") is font.glyph("'")
    assert font.glyph("一") is font.glyph("?")
    assert font.ellipsize("abcdefghij", 8 * 6) == "abc..."


def test_ui_style_round_trips_and_rejects_junk():
    config = gsconfig.Config(ui_style="Dense")
    assert config.ui_style == "dense"
    values = gsconfig._parse(config.to_text())
    assert values["UI_STYLE"] == "dense"
    assert gsconfig.Config(ui_style="huge").ui_style == "auto"
    assert gsconfig.Config().ui_style == "auto"


def test_palette_switch_is_reversible():
    desktop = theme.BACKGROUND
    theme.use_palette("dense")
    assert theme.BACKGROUND == theme.DENSE_PALETTE["BACKGROUND"]
    theme.use_palette("crt")
    assert theme.TITLE == theme.TEXT_STRONG
    theme.use_palette("desktop")
    assert theme.BACKGROUND == desktop
