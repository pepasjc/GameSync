"""Each tab keeps its own cursor across tab switches.

Glancing at Downloads and coming back to Catalog should land on the game you
were looking at, not on row one. The cursor is clamped when the list shrank
in the meantime.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for candidate in (ROOT, ROOT / "mister"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from gamesync.app import App, Row  # noqa: E402


class Metrics:
    visible_rows = 5


def make_app(all_rows):
    app = App.__new__(App)
    app.tab = 0
    app.selected = 0
    app.scroll = 0
    app.tab_positions = {}
    app.system_filter = 0
    app.system_filter_name = "All"
    app.all_rows = all_rows
    app._data_version = 0
    app._rows_key = None
    app._rows_cache = []
    app.metrics = Metrics()
    app.catalog = ["something"]   # so set_tab never tries to load it
    app.client = None
    app.tab_systems = {
        index: ["All"] + sorted({row.system for row in rows if row.system})
        for index, rows in all_rows.items()
    }
    app.draw_all = lambda: None
    app._resync_system_filter()
    return app


def rows(count, system="SNES"):
    return [Row(system, "Game %02d" % i, "", "") for i in range(count)]


def test_cursor_is_remembered_per_tab():
    app = make_app({0: rows(20), 1: rows(30), 2: rows(3), 3: [], 4: []})
    app.selected, app.scroll = 12, 8

    app.set_tab(1)
    assert (app.selected, app.scroll) == (0, 0)
    app.selected, app.scroll = 25, 21

    app.set_tab(-1)
    assert app.tab == 0
    assert (app.selected, app.scroll) == (12, 8)

    app.set_tab(1)
    assert (app.selected, app.scroll) == (25, 21)


def test_remembered_cursor_is_clamped_when_the_list_shrank():
    app = make_app({0: rows(20), 1: rows(30), 2: rows(3), 3: [], 4: []})
    app.selected, app.scroll = 19, 15
    app.set_tab(1)
    app.all_rows[0] = rows(4)
    app._data_version += 1

    app.set_tab(-1)
    assert app.selected == 3
    assert app.scroll == 0

    app.set_tab(3)           # jump to the empty Downloads tab
    app.set_tab(-3)
    assert app.selected == 3
    app.all_rows[0] = []
    app._data_version += 1
    app.set_tab(1)
    app.set_tab(-1)
    assert (app.selected, app.scroll) == (0, 0)
