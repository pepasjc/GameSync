"""The save scan must not wait on the server it can do without.

On hardware, a server that accepted connections but never answered turned a
2 s scan into six minutes: every name lookup for a serial-keyed system
(PS1, Saturn) fetched a ROM list or the title index and waited out a 60 s
timeout. And with a healthy server the same lookups were refetched, and the
matcher rebuilt, every ten minutes.
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for candidate in (ROOT, ROOT / "mister"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from gamesync import sync as gssync  # noqa: E402
from gamesync.netcache import NetCache  # noqa: E402


class ExplodingClient:
    """Any request is a test failure: the scan was meant to stay local."""

    def __getattr__(self, name):
        def call(*_a, **_k):
            raise AssertionError("scan asked the server: %s" % name)
        return call


class CountingClient:
    def __init__(self, titles=None, roms=None):
        self.calls = []
        self._titles = titles or {}
        self._roms = roms or []

    def list_titles(self):
        self.calls.append("titles")
        return self._titles

    def list_roms(self, system, fields=None):
        self.calls.append("roms:%s" % system)
        return self._roms


def _engine(tmp_path, client, catalog=None):
    engine = object.__new__(gssync.SyncEngine)
    engine.client = client
    engine.net = NetCache(path=str(tmp_path / "server_cache.json"))
    engine.offline = False
    engine.entries = []
    if catalog is not None:
        engine.catalog_rows = lambda system: catalog.get(system, [])
        engine.catalog_known = lambda: bool(catalog)
        engine.catalog_fingerprint = lambda system: "fp-%s" % system
    return engine


PS1_ROWS = [{"title_id": "SLUS01251", "filename": "Final Fantasy IX (USA) (Disc 1).chd",
             "name": "Final Fantasy IX (USA) (Disc 1)"}]


def test_rom_lists_come_from_the_catalogue_cache(tmp_path):
    engine = _engine(tmp_path, ExplodingClient(), catalog={"PS1": PS1_ROWS})
    assert engine._roms_for("PS1") == PS1_ROWS


def test_a_system_absent_from_a_known_catalogue_has_no_roms(tmp_path):
    engine = _engine(tmp_path, ExplodingClient(), catalog={"PS1": PS1_ROWS})
    assert engine._roms_for("SATURN") == []


def test_offline_uses_stale_titles_however_old(tmp_path):
    engine = _engine(tmp_path, ExplodingClient(), catalog={"PS1": PS1_ROWS})
    engine.net.put("titles", {"SLUS01251": {"name": "FF9"}},
                   now=time.time() - 30 * 24 * 3600)
    engine.offline = True
    assert engine._server_titles() == {"SLUS01251": {"name": "FF9"}}


def test_offline_without_any_titles_raises_instead_of_fetching(tmp_path):
    engine = _engine(tmp_path, ExplodingClient(), catalog={"PS1": PS1_ROWS})
    engine.offline = True
    try:
        engine._server_titles()
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected RuntimeError")


def test_titles_are_trusted_for_a_day_not_ten_minutes(tmp_path):
    client = CountingClient(titles={"SLUS01251": {"name": "FF9"}})
    engine = _engine(tmp_path, client, catalog={"PS1": PS1_ROWS})
    engine.net.put("titles", {"X": {}}, now=time.time() - 3600)
    engine._server_titles()
    assert client.calls == []


def test_matcher_is_reused_while_its_inputs_are_unchanged(tmp_path, monkeypatch):
    client = CountingClient(titles={})
    engine = _engine(tmp_path, client, catalog={"PS1": PS1_ROWS})
    engine._server_titles()
    engine._matcher_for("PS1")
    engine.net.save()

    built = []
    from shared import title_match

    original = title_match.TitleMatcher.add

    def spy(self, *a, **k):
        built.append(a)
        return original(self, *a, **k)

    monkeypatch.setattr(title_match.TitleMatcher, "add", spy)
    again = _engine(tmp_path, ExplodingClient(), catalog={"PS1": PS1_ROWS})
    again.net = NetCache(path=str(tmp_path / "server_cache.json"))
    again._titles_cache = {}
    matcher = again._matcher_for("PS1")
    assert built == [], "matcher was rebuilt although nothing changed"
    assert matcher.lookup("Final Fantasy IX (USA) (Disc 1)") == "SLUS01251"


def test_matcher_is_rebuilt_when_the_catalogue_moves(tmp_path):
    engine = _engine(tmp_path, CountingClient(), catalog={"PS1": PS1_ROWS})
    engine._titles_cache = {}
    engine._matcher_for("PS1")
    engine._matcher_cache = None
    engine.catalog_fingerprint = lambda system: "fp-changed"
    engine.catalog_rows = lambda system: PS1_ROWS + [
        {"title_id": "SLUS00892", "filename": "Vagrant Story (USA).chd",
         "name": "Vagrant Story (USA)"}]
    assert engine._matcher_for("PS1").lookup("Vagrant Story (USA)") == "SLUS00892"
