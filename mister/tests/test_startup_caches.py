"""What keeps startup fast as saves and the catalogue grow.

Profiled on a MiSTer with 277 saves and 15,763 catalogue games: matching a
save to its game walked the whole catalogue per save, every refresh
re-derived each game's facts, and every start re-normalised every name.
These pin the replacements: a (system, name) index, facts computed once per
catalogue, and a name cache that a new build throws away.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for candidate in (ROOT, ROOT / "mister"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from gamesync import app as gsapp  # noqa: E402
from shared.mister_install import DiscGroup  # noqa: E402


class Entry:
    def __init__(self, system, name, title_id=""):
        self.system = system
        self.name = name
        self.title_id = title_id


def _bare_app(groups):
    app = object.__new__(gsapp.App)
    app.catalog_groups = groups
    app.group_by_title = {}
    app.installed_ids = set()
    return app


def test_save_matches_its_game_by_name_through_the_index():
    zelda = DiscGroup("SNES", "Legend of Zelda, The (USA)", [{"size": 1}])
    mario = DiscGroup("SNES", "Super Mario World (USA)", [{"size": 1}])
    app = _bare_app([zelda, mario])
    assert app.game_for_save(Entry("SNES", "Super Mario World (USA)")) is mario
    assert app.game_for_save(Entry("GBA", "Super Mario World (USA)")) is None


def test_first_game_of_a_name_wins_as_the_linear_scan_did():
    first = DiscGroup("SNES", "Game (USA)", [{"size": 1}])
    second = DiscGroup("SNES", "Game (USA)", [{"size": 2}])
    app = _bare_app([first, second])
    assert app.game_for_save(Entry("SNES", "Game (USA)")) is first


def test_a_replaced_catalogue_is_reindexed():
    old = DiscGroup("SNES", "Old Game (USA)", [{"size": 1}])
    new = DiscGroup("SNES", "New Game (USA)", [{"size": 1}])
    app = _bare_app([old])
    assert app.game_for_save(Entry("SNES", "Old Game (USA)")) is old
    app.catalog_groups = [new]
    assert app.game_for_save(Entry("SNES", "Old Game (USA)")) is None
    assert app.game_for_save(Entry("SNES", "New Game (USA)")) is new


def test_installed_check_uses_the_precomputed_key():
    game = DiscGroup("SNES", "Game (USA)", [{"size": 1}])
    app = _bare_app([game])
    app._index_catalog()
    assert not app.game_installed(game)
    app.installed_ids = {gsapp._installed_key(game)}
    assert app.game_installed(game)


def test_name_cache_round_trips_for_the_same_build(tmp_path, monkeypatch):
    path = str(tmp_path / "name_cache.json")
    monkeypatch.setattr(gsapp, "_NORMALIZE_CACHE", {})
    monkeypatch.setattr(gsapp, "_name_cache_loaded", 0)
    gsapp._NORMALIZE_CACHE["Some Game (USA)"] = "some_game_usa"
    gsapp.save_name_cache(path, stamp="build-1")
    assert json.loads(Path(path).read_text())["build"] == "build-1"

    gsapp._NORMALIZE_CACHE.clear()
    gsapp.load_name_cache(path, stamp="build-1")
    assert gsapp._NORMALIZE_CACHE == {"Some Game (USA)": "some_game_usa"}


def test_a_new_build_discards_the_name_cache(tmp_path, monkeypatch):
    """The normaliser may have changed: stale keys would mismatch saves."""
    path = str(tmp_path / "name_cache.json")
    monkeypatch.setattr(gsapp, "_NORMALIZE_CACHE", {"a": "old"})
    monkeypatch.setattr(gsapp, "_name_cache_loaded", 0)
    gsapp.save_name_cache(path, stamp="build-1")
    gsapp._NORMALIZE_CACHE.clear()
    gsapp.load_name_cache(path, stamp="build-2")
    assert gsapp._NORMALIZE_CACHE == {}


def test_unchanged_cache_is_not_rewritten(tmp_path, monkeypatch):
    path = tmp_path / "name_cache.json"
    monkeypatch.setattr(gsapp, "_NORMALIZE_CACHE", {"a": "b"})
    monkeypatch.setattr(gsapp, "_name_cache_loaded", 1)
    gsapp.save_name_cache(str(path), stamp="build-1")
    assert not path.exists()


def test_nothing_is_persisted_outside_a_zipapp(tmp_path, monkeypatch):
    path = tmp_path / "name_cache.json"
    monkeypatch.setattr(gsapp, "_NORMALIZE_CACHE", {"a": "b"})
    monkeypatch.setattr(gsapp, "_name_cache_loaded", 0)
    gsapp.save_name_cache(str(path), stamp="")
    assert not path.exists()
