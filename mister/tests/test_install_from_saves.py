"""A save whose game is not on the MiSTer can be installed from the Saves tab.

Matching save -> catalogue game is by title id (a PS1 save and its disc share
the serial; a slug system's save and ROM share the slug), with a name fallback
for catalogue rows that carry no id. No hardware involved.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for candidate in (ROOT, ROOT / "mister"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from gamesync.app import App, _normalize  # noqa: E402
from gamesync.sync import SaveEntry  # noqa: E402
from shared.mister_install import group_discs  # noqa: E402


def make_app(rows, installed):
    app = App.__new__(App)
    app.catalog = rows
    app.catalog_groups = group_discs(rows)
    app.group_by_title = {}
    for group in app.catalog_groups:
        for rom in group.rows:
            title_id = str(rom.get("title_id") or "").upper()
            if title_id:
                app.group_by_title.setdefault(title_id, group)
    app.installed_ids = {(system, _normalize(name), False)
                         for system, name in installed}
    return app


CATALOG = [
    {"system": "PS1", "name": "Dino Crisis 2 (USA)", "title_id": "SLUS01279",
     "size": 500, "disc_index": 1},
    {"system": "PS1", "name": "Final Fantasy IX (USA) (Disc 1)",
     "title_id": "SLUS01251", "size": 700, "disc_index": 1},
    {"system": "PS1", "name": "Final Fantasy IX (USA) (Disc 2)",
     "title_id": "SLUS01251", "size": 700, "disc_index": 2},
    {"system": "SNES", "name": "Chrono Trigger (USA)",
     "title_id": "SNES_chrono_trigger_usa", "size": 4},
    {"system": "SNES", "name": "EarthBound (USA)", "title_id": "", "size": 3},
]


def test_save_finds_its_game_by_title_id():
    app = make_app(CATALOG, installed=[])
    entry = SaveEntry("SLUS01251", "PS1", "FF9 card", path="/x")
    group = app.game_for_save(entry)
    assert group is not None and group.disc_count == 2
    assert not app.game_installed(group)


def test_installed_game_is_recognised_whatever_the_save_is_called():
    app = make_app(CATALOG, installed=[("PS1", "Dino Crisis 2 (USA)")])
    entry = SaveEntry("SLUS01279", "PS1", "SLUS_012.79", path="/x", is_cd=True)
    assert app.game_installed(app.game_for_save(entry))


def test_name_fallback_when_the_catalogue_row_has_no_title_id():
    app = make_app(CATALOG, installed=[])
    entry = SaveEntry("SNES_earthbound_usa", "SNES", "EarthBound (USA)")
    group = app.game_for_save(entry)
    assert group is not None and group.name == "EarthBound (USA)"


def test_save_for_a_game_the_server_does_not_have():
    app = make_app(CATALOG, installed=[])
    entry = SaveEntry("SLUS00594", "PS1", "Parasite Eve II (USA)", path="/x")
    assert app.game_for_save(entry) is None
