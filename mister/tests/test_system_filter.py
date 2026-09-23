"""The system filter must only offer systems that have rows in the current tab.

A MiSTer runs far more cores than the server has ROMs for, so cycling L1/R1
through systems with nothing behind them is pure noise. These exercise the
filter logic directly, without a framebuffer.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for candidate in (ROOT, ROOT / "mister"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from gamesync.app import App, Row  # noqa: E402


def make_app(all_rows, tab=0):
    """An App with just enough state for the filter, no hardware involved."""
    app = App.__new__(App)
    app.tab = tab
    app.selected = 0
    app.scroll = 0
    app.system_filter = 0
    app.system_filter_name = "All"
    app.all_rows = all_rows
    app._data_version = 0
    app._rows_key = None
    app._rows_cache = []
    app.tab_systems = {
        index: ["All"] + sorted({row.system for row in rows if row.system})
        for index, rows in all_rows.items()
    }
    app._resync_system_filter()
    return app


SAVES = [Row("SNES", "Mario", "2 KB", "synced"),
         Row("PS1", "Breath of Fire", "128 KB", "synced")]
CATALOG = [Row("GBA", "Zelda", "8 MB", "not installed"),
           Row("SNES", "Mario", "512 KB", "installed")]
INSTALLED = [Row("SNES", "Mario", "SD", "installed")]


def test_only_systems_present_in_the_tab_are_offered():
    app = make_app({0: SAVES, 1: CATALOG, 2: INSTALLED, 3: [], 4: []})

    assert app.systems == ["All", "PS1", "SNES"]      # Saves
    app.tab = 1
    assert app.systems == ["All", "GBA", "SNES"]      # Catalog
    app.tab = 2
    assert app.systems == ["All", "SNES"]             # Installed


def test_a_tab_with_no_rows_offers_only_all():
    app = make_app({0: SAVES, 1: [], 2: [], 3: [], 4: []})
    app.tab = 1
    assert app.systems == ["All"]


def test_cycling_stays_inside_the_current_tab():
    app = make_app({0: SAVES, 1: CATALOG, 2: INSTALLED, 3: [], 4: []}, tab=1)
    seen = []
    for _ in range(4):
        app.set_system = App.set_system.__get__(app)  # bind without __init__
        seen.append(app.systems[app.system_filter])
        app.system_filter = (app.system_filter + 1) % len(app.systems)
    assert seen == ["All", "GBA", "SNES", "All"]
    # PS1 exists in Saves but not in the catalogue, so it is never offered here.
    assert "PS1" not in app.systems


def test_selection_carries_across_tabs_that_share_the_system():
    app = make_app({0: SAVES, 1: CATALOG, 2: INSTALLED, 3: [], 4: []})
    app.system_filter = app.systems.index("SNES")
    app.system_filter_name = "SNES"

    app.tab = 1
    app._resync_system_filter()
    assert app.systems[app.system_filter] == "SNES"


def test_selection_falls_back_to_all_when_the_new_tab_lacks_it():
    """PS1 has a save but no catalogue row, so the Catalog tab shows all -
    but the choice is remembered, and comes back on a tab that has PS1."""
    app = make_app({0: SAVES, 1: CATALOG, 2: INSTALLED, 3: [], 4: []})
    app.system_filter = app.systems.index("PS1")
    app.system_filter_name = "PS1"

    app.tab = 1
    app._resync_system_filter()
    assert app.system_filter == 0
    assert app.systems[app.system_filter] == "All"
    assert app.system_filter_name == "PS1"

    app.tab = 0
    app._resync_system_filter()
    assert app.systems[app.system_filter] == "PS1"


def test_selection_survives_a_data_refresh_on_a_tab_without_it():
    """Queueing a download refreshes every tab's rows; the Downloads tab may
    hold only one system, and that must not reset the filter elsewhere."""
    app = make_app({0: SAVES, 1: CATALOG, 2: INSTALLED, 3: [], 4: []})
    app.system_filter_name = "SNES"
    app.tab = 3
    app._resync_system_filter()          # Downloads has nothing: shows All
    assert app.systems[app.system_filter] == "All"

    app.tab = 1
    app._resync_system_filter()
    assert app.systems[app.system_filter] == "SNES"


def test_filter_index_can_never_point_past_the_list():
    """A stale index from a longer tab must not raise IndexError."""
    app = make_app({0: SAVES, 1: [], 2: [], 3: [], 4: []})
    app.system_filter = 5
    app.system_filter_name = "SNES"
    app.tab = 1
    app._resync_system_filter()
    assert app.system_filter < len(app.systems)


def test_stale_index_reads_as_all_until_resolved():
    """Seen live: R2 into a tab with fewer systems raised IndexError in the
    header before the filter had been re-resolved. Reading through
    current_system must never raise, whatever the index says."""
    app = make_app({0: SAVES, 1: CATALOG, 2: INSTALLED, 3: [], 4: []})
    app.system_filter = 2          # valid for Saves (All, PS1, SNES)
    app.system_filter_name = "SNES"
    app.tab = 4                    # Settings: only "All"
    assert app.current_system == "All"
    assert app.rows() == []
    app.tab = 0
    assert app.current_system == "SNES"
    assert [row.name for row in app.rows()] == ["Mario"]
