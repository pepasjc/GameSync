"""Holding left/right long enough sweeps the list by initial letter.

Mirrors the Steam Deck client: a tap pages, a long hold jumps A -> B -> C.
Exercised without a framebuffer by stubbing the three draw calls move() makes.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for candidate in (ROOT, ROOT / "mister"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from gamesync.app import App, Row, _first_letter  # noqa: E402


class _Metrics:
    visible_rows = 4


def make_app(names):
    app = App.__new__(App)
    app.tab = 1
    app.selected = 0
    app.scroll = 0
    app.system_filter = 0
    app.system_filter_name = "All"
    app.all_rows = {1: [Row("SNES", name, "", "") for name in names]}
    app.tab_systems = {1: ["All", "SNES"]}
    app._data_version = 0
    app._rows_key = None
    app._rows_cache = []
    app.metrics = _Metrics()
    app.last_frame_ms = 0.0
    app.draw_list = app.draw_row = app.draw_footer = lambda *a, **k: None
    return app


NAMES = ["Actraiser", "Aladdin", "Axelay", "Batman Returns", "Breath of Fire",
         "Chrono Trigger", "Contra III", "Donkey Kong Country", "EarthBound",
         "F-Zero", "Final Fantasy VI"]


def test_forward_jumps_land_on_the_first_row_of_each_letter():
    app = make_app(NAMES)
    seen = []
    for _ in range(6):
        app.jump_letter(1)
        seen.append(NAMES[app.selected])
    assert seen == ["Batman Returns", "Chrono Trigger", "Donkey Kong Country",
                    "EarthBound", "F-Zero", "Final Fantasy VI"]
    # Off the end: stays on the last row rather than wrapping.
    app.jump_letter(1)
    assert NAMES[app.selected] == "Final Fantasy VI"


def test_backward_jumps_land_on_the_first_row_of_the_previous_letter():
    app = make_app(NAMES)
    app.selected = NAMES.index("EarthBound")
    app.jump_letter(-1)
    assert NAMES[app.selected] == "Donkey Kong Country"
    app.jump_letter(-1)
    assert NAMES[app.selected] == "Chrono Trigger"      # not "Contra III"
    app.jump_letter(-1)
    assert NAMES[app.selected] == "Batman Returns"
    app.jump_letter(-1)
    assert NAMES[app.selected] == "Actraiser"
    app.jump_letter(-1)
    assert app.selected == 0


def test_jump_keeps_the_selection_visible():
    app = make_app(NAMES)
    app.jump_letter(1)
    app.jump_letter(1)
    app.jump_letter(1)      # row 7 with 4 visible rows: must scroll
    assert app.scroll <= app.selected < app.scroll + app.metrics.visible_rows


def test_first_letter_skips_punctuation_and_digits():
    assert _first_letter("'98 Koshien") == "K"
    assert _first_letter("3D Baseball") == "D"
    assert _first_letter("1943") == ""
    assert _first_letter("") == ""


def test_empty_list_is_a_no_op():
    app = make_app([])
    app.jump_letter(1)
    assert app.selected == 0
