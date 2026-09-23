"""The short-lived cache for server lookups.

This one is riskier than the hash cache: it caches data used to decide which
server slot a save belongs to. It must expire, and it must never be consulted
for the sync plan itself.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for candidate in (ROOT, ROOT / "mister"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from gamesync.netcache import VERSION, NetCache  # noqa: E402
from shared.title_match import TitleMatcher  # noqa: E402


def make_cache(tmp_path, ttl=600.0):
    return NetCache(str(tmp_path / "server_cache.json"), ttl=ttl)


def test_a_fresh_value_is_returned(tmp_path):
    cache = make_cache(tmp_path)
    cache.put("titles", {"A": 1}, now=1000.0)
    assert cache.get("titles", now=1100.0) == {"A": 1}


def test_a_stale_value_is_not(tmp_path):
    cache = make_cache(tmp_path, ttl=600.0)
    cache.put("titles", {"A": 1}, now=1000.0)
    assert cache.get("titles", now=1700.0) is None


def test_a_clock_that_went_backwards_is_treated_as_stale(tmp_path):
    """A MiSTer has no battery-backed clock until the network sets it."""
    cache = make_cache(tmp_path)
    cache.put("titles", {"A": 1}, now=5000.0)
    assert cache.get("titles", now=1000.0) is None


def test_values_persist_across_instances(tmp_path):
    cache = make_cache(tmp_path)
    cache.put("roms:PS1", [{"title_id": "SLUS01324"}], now=1000.0)
    cache.save()
    assert make_cache(tmp_path).get("roms:PS1", now=1100.0) is not None


def test_invalidate_clears_everything(tmp_path):
    cache = make_cache(tmp_path)
    cache.put("titles", 1, now=1000.0)
    cache.put("roms:PS1", 2, now=1000.0)
    cache.invalidate()
    assert cache.get("titles", now=1000.0) is None
    assert cache.get("roms:PS1", now=1000.0) is None


def test_invalidate_can_target_a_prefix(tmp_path):
    cache = make_cache(tmp_path)
    cache.put("titles", 1, now=1000.0)
    cache.put("roms:PS1", 2, now=1000.0)
    cache.invalidate("roms:")
    assert cache.get("titles", now=1000.0) == 1
    assert cache.get("roms:PS1", now=1000.0) is None


def test_a_cache_from_another_format_is_ignored(tmp_path):
    path = tmp_path / "server_cache.json"
    path.write_text('{"version": %d, "entries": {"titles": {}}}' % (VERSION + 1))
    assert len(NetCache(str(path))) == 0


def test_corrupt_json_is_ignored(tmp_path):
    path = tmp_path / "server_cache.json"
    path.write_text("not json at all")
    assert len(NetCache(str(path))) == 0


def test_a_matcher_survives_being_cached():
    """The built index is what gets cached, so it must round-trip exactly."""
    original = TitleMatcher()
    original.add("SLES02965", "Final Fantasy IX (USA) (Disc 1).chd")
    original.add("SLUS01251", "Final Fantasy IX [Disc1of4]", authoritative=True)
    original.add("SLUS01324", "Breath of Fire IV (USA).chd")

    restored = TitleMatcher.from_dict(original.to_dict())

    for name in ("Final Fantasy IX (USA)", "Breath of Fire IV (USA)",
                 "Chrono Trigger (USA)"):
        assert restored.lookup(name) == original.lookup(name)
    # Specifically: the authoritative slot must still win after a round trip.
    assert restored.lookup("Final Fantasy IX (USA)") == "SLUS01251"


def test_a_matcher_from_junk_is_empty_not_broken():
    assert len(TitleMatcher.from_dict({})) == 0
    assert len(TitleMatcher.from_dict(None)) == 0
    assert TitleMatcher.from_dict({"exact": None}).lookup("Anything") is None
