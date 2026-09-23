"""The scan's hash cache.

A wrong cache is worse than no cache: it would report a save as unchanged
after it had been played, and the new progress would never be uploaded. These
cover the invalidation cases specifically.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for candidate in (ROOT, ROOT / "mister"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from gamesync.hashcache import VERSION, HashCache  # noqa: E402


def make_cache(tmp_path):
    return HashCache(str(tmp_path / "hash_cache.json"))


def test_a_miss_before_anything_is_stored(tmp_path):
    cache = make_cache(tmp_path)
    assert cache.get("/media/fat/saves/SNES/Game.sav", 2048, 1000.0) is None
    assert cache.misses == 1


def test_an_unchanged_file_hits(tmp_path):
    cache = make_cache(tmp_path)
    cache.put("/saves/Game.sav", 2048, 1000.0, "abc123")
    entry = cache.get("/saves/Game.sav", 2048, 1000.0)
    assert entry["hash"] == "abc123"
    assert cache.hits == 1


def test_a_changed_size_misses(tmp_path):
    cache = make_cache(tmp_path)
    cache.put("/saves/Game.sav", 2048, 1000.0, "abc123")
    assert cache.get("/saves/Game.sav", 4096, 1000.0) is None


def test_a_changed_mtime_misses(tmp_path):
    """The whole point: a save played since the last scan must be re-read."""
    cache = make_cache(tmp_path)
    cache.put("/saves/Game.sav", 2048, 1000.0, "abc123")
    assert cache.get("/saves/Game.sav", 2048, 2000.0) is None


def test_exfat_two_second_granularity_is_tolerated(tmp_path):
    """exfat stores mtime to the nearest two seconds."""
    cache = make_cache(tmp_path)
    cache.put("/saves/Game.sav", 2048, 1000.0, "abc123")
    assert cache.get("/saves/Game.sav", 2048, 1001.0) is not None
    assert cache.get("/saves/Game.sav", 2048, 1005.0) is None


def test_card_facts_survive_a_round_trip(tmp_path):
    """The serial and blank flag are why a PS1 card need not be re-read."""
    cache = make_cache(tmp_path)
    cache.put("/saves/PSX/Game.sav", 131072, 10.0, "hash",
              serial="SLUS01324", blank=False)
    cache.save()

    reopened = HashCache(str(tmp_path / "hash_cache.json"))
    entry = reopened.get("/saves/PSX/Game.sav", 131072, 10.0)
    assert entry["serial"] == "SLUS01324"
    assert entry["blank"] is False


def test_persists_across_instances(tmp_path):
    cache = make_cache(tmp_path)
    cache.put("/saves/Game.sav", 2048, 1000.0, "abc123")
    cache.save()
    assert make_cache(tmp_path).get("/saves/Game.sav", 2048, 1000.0) is not None


def test_deleted_saves_are_pruned(tmp_path):
    cache = make_cache(tmp_path)
    cache.put("/saves/Gone.sav", 1, 1.0, "a")
    cache.put("/saves/Here.sav", 1, 1.0, "b")
    cache.prune(["/saves/Here.sav"])
    assert len(cache) == 1
    assert cache.get("/saves/Gone.sav", 1, 1.0) is None


def test_a_cache_from_a_future_format_is_ignored(tmp_path):
    path = tmp_path / "hash_cache.json"
    path.write_text('{"version": %d, "entries": {"/x": {"size": 1}}}'
                    % (VERSION + 1))
    assert len(HashCache(str(path))) == 0


def test_a_corrupt_cache_is_ignored_not_fatal(tmp_path):
    path = tmp_path / "hash_cache.json"
    path.write_text("{ this is not json")
    cache = HashCache(str(path))
    assert len(cache) == 0
    assert cache.get("/saves/Game.sav", 1, 1.0) is None


def test_an_unwritable_cache_is_survivable(tmp_path):
    """A cache that cannot be written is a slow scan, not a crash."""
    cache = HashCache(str(tmp_path / "nope" / "sub" / "hash_cache.json"))
    cache.put("/saves/Game.sav", 1, 1.0, "a")
    cache.path = "\0invalid"          # guaranteed to fail on any platform
    cache.save()                       # must not raise
