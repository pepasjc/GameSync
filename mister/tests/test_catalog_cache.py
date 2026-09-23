"""The catalogue is kept on disk and refreshed by difference.

The server publishes one fingerprint per system; only systems whose
fingerprint moved are fetched again, and a system the server dropped is
dropped here too.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for candidate in (ROOT, ROOT / "mister"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from gamesync.catalogcache import CatalogCache  # noqa: E402


def make(tmp_path):
    return CatalogCache(path=str(tmp_path / "catalog_cache.json"))


def test_only_moved_fingerprints_are_stale(tmp_path):
    cache = make(tmp_path)
    cache.put("SNES", "aaa", [{"rom_id": "s1"}])
    cache.put("PS1", "bbb", [{"rom_id": "p1"}])
    cache.put("GBA", "ccc", [{"rom_id": "g1"}])

    server = {"SNES": {"fingerprint": "aaa"},      # unchanged
              "PS1": {"fingerprint": "bbb2"},      # library changed
              "MD": {"fingerprint": "ddd"}}        # new on the server; GBA gone
    fresh, stale = cache.plan(server, ["SNES", "PS1", "MD", "GBA"])
    assert fresh == ["SNES"]
    assert stale == ["PS1", "MD"]
    assert cache.systems() == ["PS1", "SNES"]      # GBA dropped
    # The stale PS1 rows are still served until they are replaced.
    assert cache.rows("PS1") == [{"rom_id": "p1"}]


def test_round_trips_through_disk(tmp_path):
    cache = make(tmp_path)
    cache.put("SNES", "aaa", [{"rom_id": "s1", "name": "Mario"}])
    cache.save()

    again = make(tmp_path)
    assert again.fingerprint("SNES") == "aaa"
    assert again.all_rows() == [{"rom_id": "s1", "name": "Mario"}]
    assert len(again) == 1


def test_wanted_system_the_server_lacks_is_neither_fresh_nor_stale(tmp_path):
    cache = make(tmp_path)
    fresh, stale = cache.plan({"SNES": {"fingerprint": "x"}}, ["SNES", "PS1"])
    assert (fresh, stale) == ([], ["SNES"])


def test_old_cache_version_is_ignored(tmp_path):
    path = tmp_path / "catalog_cache.json"
    path.write_text('{"version": 0, "systems": {"SNES": {"fingerprint": "a", '
                    '"rows": [{"rom_id": "s1"}]}}}')
    assert len(CatalogCache(path=str(path))) == 0
