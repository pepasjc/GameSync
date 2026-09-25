"""Square on the Catalog tab: only games with a RetroAchievements set, or all.

"RA?" rows (a set exists for a game of that name, but the disc could not be
hashed) count as RA games - hiding them would hide nearly every disc game.
The L1/R1 system stops follow the filter, so no system comes up empty.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for candidate in (ROOT, ROOT / "mister"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from gamesync import input as gsinput  # noqa: E402
from gamesync.app import App, Row, catalog_systems  # noqa: E402


def _rows():
    return [
        Row("SNES", "Super Mario World (USA)", "512 KB", "installed", ra="hash"),
        Row("SNES", "Obscure Hack (USA)", "1 MB", "not installed"),
        Row("PS1", "Final Fantasy IX (USA)", "4 discs", "not installed",
            ra="title"),
        Row("GBA", "No Set Here (USA)", "8 MB", "not installed"),
    ]


class FakeInput:
    def label(self, action):
        return {gsinput.SYNC: "Square"}.get(action, action)


def make_app(rows):
    app = App.__new__(App)
    app.tab = 1
    app.selected = 3
    app.scroll = 0
    app.system_filter = 0
    app.system_filter_name = "All"
    app.all_rows = {1: rows, 0: []}
    app._data_version = 0
    app._rows_key = None
    app._rows_cache = []
    app.tab_systems = {1: catalog_systems(rows)}
    app._resync_system_filter()
    app.input = FakeInput()
    app.client = object()
    app.catalog = rows
    app.toasts = []
    app.draw_all = lambda: None
    app.toast = lambda text, *a, **k: app.toasts.append(text)
    return app


def test_toggle_shows_only_ra_games_then_everything_again():
    app = make_app(_rows())
    assert len(app.rows()) == 4

    app.handle(gsinput.SYNC)
    assert app.ra_only
    assert [row.name for row in app.rows()] == [
        "Super Mario World (USA)", "Final Fantasy IX (USA)"]
    assert app.toasts[-1] == "RetroAchievements games only: 2"
    assert app.selected == 0  # the old cursor may point past the new list

    app.handle(gsinput.SYNC)
    assert not app.ra_only
    assert len(app.rows()) == 4
    assert app.toasts[-1] == "Showing all games"


def test_system_stops_skip_systems_without_ra_games():
    app = make_app(_rows())
    app.toggle_ra_only()
    assert app.tab_systems[1] == ["All", "PS1", "SNES"]
    app.toggle_ra_only()
    assert app.tab_systems[1] == ["All", "GBA", "PS1", "SNES"]


def test_chosen_system_without_ra_games_falls_back_to_all():
    app = make_app(_rows())
    app.system_filter_name = "GBA"
    app._resync_system_filter()
    assert app.current_system == "GBA"
    app.toggle_ra_only()
    assert app.current_system == "All"
    # The choice is kept by name and returns when the filter is lifted.
    app.toggle_ra_only()
    assert app.current_system == "GBA"


def test_filter_only_applies_to_the_catalog_tab():
    app = make_app(_rows())
    app.toggle_ra_only()
    app.all_rows[0] = [Row("SNES", "Some save", "8 KB", "synced")]
    app.tab = 0
    app._rows_key = None
    assert len(app.rows()) == 1


def test_empty_list_says_why_and_how_to_undo():
    app = make_app([Row("GBA", "No Set Here (USA)", "8 MB", "not installed")])
    app.search = ""
    app.toggle_ra_only()
    assert app.rows() == []
    assert app._empty_message() == "No RetroAchievements games - Square shows all"
