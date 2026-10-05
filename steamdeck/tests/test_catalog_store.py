"""The catalog is cached per system and refreshed by fingerprint."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STEAMDECK_ROOT = ROOT / "steamdeck"
if str(STEAMDECK_ROOT) not in sys.path:
    sys.path.insert(0, str(STEAMDECK_ROOT))

from catalog_store import clear_catalog_cache, load_catalog  # noqa: E402


class FakeClient:
    def __init__(self, catalog, fingerprints):
        self.catalog = catalog              # system -> rows
        self.fingerprints = fingerprints    # dict, None (old server) or Exception
        self.fetched = []
        self.rescans = 0
        self.fail_lists = False

    def rom_fingerprints(self):
        if isinstance(self.fingerprints, Exception):
            raise self.fingerprints
        return self.fingerprints

    def list_roms(self, system=None, strict=False):
        if self.fail_lists:
            raise RuntimeError("boom")
        self.fetched.append(system)
        if system is None:
            return [row for rows in self.catalog.values() for row in rows]
        return list(self.catalog.get(system, []))

    def rescan_roms(self):
        self.rescans += 1
        return 3


def _rows(system, *ids):
    return [{"rom_id": i, "system": system, "name": i} for i in ids]


def _client():
    return FakeClient(
        {"SNES": _rows("SNES", "s1", "s2"), "PS1": _rows("PS1", "p1")},
        {"SNES": {"fingerprint": "a"}, "PS1": {"fingerprint": "b"}},
    )


def test_first_load_fetches_every_system_then_none(tmp_path):
    path = tmp_path / "cat.json"
    client = _client()
    first = load_catalog(client, path)
    assert sorted(client.fetched) == ["PS1", "SNES"]
    assert first.refreshed == 2 and len(first.rows) == 3

    client.fetched.clear()
    second = load_catalog(client, path)
    assert client.fetched == []
    assert second.unchanged == 2 and len(second.rows) == 3


def test_only_the_changed_system_is_refetched(tmp_path):
    path = tmp_path / "cat.json"
    client = _client()
    load_catalog(client, path)
    client.fetched.clear()
    client.catalog["PS1"] = _rows("PS1", "p1", "p2")
    client.fingerprints["PS1"] = {"fingerprint": "b2"}
    result = load_catalog(client, path)
    assert client.fetched == ["PS1"]
    assert result.refreshed == 1 and result.unchanged == 1
    assert len(result.rows) == 4


def test_system_dropped_by_server_is_dropped(tmp_path):
    path = tmp_path / "cat.json"
    client = _client()
    load_catalog(client, path)
    del client.fingerprints["PS1"]
    result = load_catalog(client, path)
    assert {row["system"] for row in result.rows} == {"SNES"}


def test_alias_rows_are_not_cached_twice(tmp_path):
    client = _client()
    client.catalog["PS1"].append({"rom_id": "x", "system": "PSX"})
    result = load_catalog(client, tmp_path / "cat.json")
    assert [row["rom_id"] for row in result.rows if row["system"] != "SNES"] == ["p1"]


def test_unreachable_server_uses_cached_copy(tmp_path):
    path = tmp_path / "cat.json"
    load_catalog(_client(), path)
    offline = FakeClient({}, ConnectionError("down"))
    result = load_catalog(offline, path)
    assert result.offline and len(result.rows) == 3


def test_unreachable_server_without_cache_is_an_error(tmp_path):
    result = load_catalog(FakeClient({}, ConnectionError("down")), tmp_path / "c.json")
    assert result.error and not result.rows


def test_old_server_is_fetched_whole_and_not_cached(tmp_path):
    path = tmp_path / "cat.json"
    client = _client()
    client.fingerprints = None
    result = load_catalog(client, path)
    assert client.fetched == [None] and not result.cached
    assert len(result.rows) == 3
    assert not path.exists()


def test_failed_fetch_never_caches_an_empty_system(tmp_path):
    path = tmp_path / "cat.json"
    client = _client()
    client.fail_lists = True
    result = load_catalog(client, path)
    assert result.error
    client.fail_lists = False
    result = load_catalog(client, path)
    assert result.refreshed == 2 and len(result.rows) == 3


def test_force_rescans_and_refetches_everything(tmp_path):
    path = tmp_path / "cat.json"
    client = _client()
    load_catalog(client, path)
    client.fetched.clear()
    result = load_catalog(client, path, force=True)
    assert client.rescans == 1
    assert sorted(client.fetched) == ["PS1", "SNES"]
    assert result.refreshed == 2


def test_clear_catalog_cache(tmp_path):
    path = tmp_path / "cat.json"
    client = _client()
    load_catalog(client, path)
    clear_catalog_cache(path)
    client.fetched.clear()
    load_catalog(client, path)
    assert sorted(client.fetched) == ["PS1", "SNES"]
