"""Catalog search and the remembered catalogue cursor.

Search is a word filter over the Catalog tab only; Select clears it. The
system and row under the cursor are written out on exit and restored the
next time the catalogue is loaded, so a session starts where the last one
ended rather than at row one of "All".
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for candidate in (ROOT, ROOT / "mister"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from gamesync import input as gsinput  # noqa: E402
from gamesync.app import App, Row, _search_rows  # noqa: E402
from gamesync.input import Device, InputReader  # noqa: E402


CATALOG = [Row("PS1", "Final Fantasy IX", "1 GB", "not installed"),
           Row("PS1", "Final Fantasy Tactics", "400 MB", "installed"),
           Row("SNES", "Final Fight", "2 MB", "not installed"),
           Row("SNES", "Chrono Trigger", "4 MB", "not installed")]
INSTALLED = [Row("PS1", "Final Fantasy Tactics", "SD", "installed")]


class _Metrics:
    visible_rows = 2


def make_app(tab=1):
    app = App.__new__(App)
    app.tab = tab
    app.selected = 0
    app.scroll = 0
    app.system_filter = 0
    app.system_filter_name = "All"
    app.all_rows = {0: [], 1: CATALOG, 2: INSTALLED, 3: [], 4: []}
    app._data_version = 0
    app._rows_key = None
    app._rows_cache = []
    app.tab_systems = {
        index: ["All"] + sorted({row.system for row in rows if row.system})
        for index, rows in app.all_rows.items()
    }
    app.tab_positions = {}
    app.metrics = _Metrics()
    app.ui_state = {}
    app._catalog_last = ("All", "")
    app._catalog_restore_pending = True
    app._resync_system_filter()
    return app


def test_every_word_must_match_in_any_order():
    names = [r.name for r in _search_rows(CATALOG, "fantasy final")]
    assert names == ["Final Fantasy IX", "Final Fantasy Tactics"]
    assert [r.name for r in _search_rows(CATALOG, "TRIGGER")] == [
        "Chrono Trigger"]
    assert _search_rows(CATALOG, "zelda") == []
    assert _search_rows(CATALOG, "   ") == CATALOG


def test_search_narrows_the_catalog_and_stacks_with_the_system():
    app = make_app()
    app.search = "final"
    assert [r.name for r in app.rows()] == [
        "Final Fantasy IX", "Final Fantasy Tactics", "Final Fight"]
    app.system_filter = app.systems.index("SNES")
    assert [r.name for r in app.rows()] == ["Final Fight"]


def test_search_leaves_the_other_tabs_alone():
    app = make_app(tab=2)
    app.search = "zelda"
    assert [r.name for r in app.rows()] == ["Final Fantasy Tactics"]


def test_cursor_is_restored_by_system_and_name():
    app = make_app()
    app.ui_state = {"catalog_system": "SNES", "catalog_row": "Chrono Trigger"}

    app.restore_catalog_position()

    assert app.current_system == "SNES"
    assert app.rows()[app.selected].name == "Chrono Trigger"
    # Only once: a later catalogue refresh must not yank the cursor back.
    app.selected = 0
    app.restore_catalog_position()
    assert app.selected == 0


def test_restore_lands_in_tab_positions_when_not_on_the_catalog():
    app = make_app(tab=0)
    app.ui_state = {"catalog_system": "All",
                    "catalog_row": "Final Fantasy Tactics"}

    app.restore_catalog_position()

    assert app.tab == 0 and app.selected == 0
    assert app.tab_positions[1][0] == 1


def test_a_vanished_row_or_system_is_ignored():
    app = make_app()
    app.ui_state = {"catalog_system": "N64", "catalog_row": "Gone"}
    app.restore_catalog_position()
    assert app.current_system == "All" and app.selected == 0


def make_reader(*devices):
    reader = InputReader.__new__(InputReader)
    reader._devices = list(devices)
    reader._by_fd = {}
    reader._active_pad = None
    reader._capture = None
    reader._pending = []
    reader._last_action = {}
    reader.set_buttons(None, arcade=False)
    return reader


def test_keyboard_types_only_while_a_prompt_is_open():
    keyboard = Device("/dev/input/event0", "AT Keyboard", fd=-1, is_pad=False,
                      is_keyboard=True, axes={})
    reader = make_reader(keyboard)
    key_f, key_1 = 33, 2

    reader._on_key(keyboard, key_f, 1)
    assert reader._pending == []             # letters do nothing in the list

    reader.text_keys = True
    reader._on_key(keyboard, key_f, 1)
    reader._on_key(keyboard, key_1, 1)
    reader._on_key(keyboard, gsinput.KEY_SPACE, 1)
    reader._on_key(keyboard, gsinput.KEY_BACKSPACE, 1)
    reader._on_key(keyboard, gsinput.KEY_ENTER, 1)
    assert reader._pending == ["char:F", "char:1", "char: ",
                               gsinput.TEXT_DELETE, gsinput.TEXT_OK]


def test_search_matches_the_file_name_of_a_translation_patch():
    from shared.mister_install import group_discs

    rows = [{"system": "SNES", "title_id": "SNES_famicom_tantei_club_x",
             "name": "Famicom Tantei Club Part II (Japan) (NP)",
             "filename": "Famicom Detective Club Part II (Japan) [T-En].sfc",
             "size": 1}]
    group = group_discs(rows)[0]
    catalog = [Row("SNES", group.name, "4 MB", "not installed", ref=group)]

    assert _search_rows(catalog, "detective club") == catalog
    assert _search_rows(catalog, "tantei") == catalog
    assert _search_rows(catalog, "zelda") == []
