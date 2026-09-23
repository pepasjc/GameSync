"""Regression tests for the MiSTer client's catalogue paging.

The ROM catalogue is ordered by ``title_id``, which begins with the system
code, so it is effectively alphabetical by system. A client that asks for one
page and ignores ``has_more`` does not lose a random sample - it loses every
system after the cut-off. That looked exactly like "SNES, PS1 and PCECD have no
games" while A2600 and GBA were fine.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for candidate in (ROOT, ROOT / "mister"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from gamesync.api import Client  # noqa: E402


class FakeServer(Client):
    """A Client whose transport is a canned, paginated catalogue."""

    def __init__(self, rows, page_cap=20000):
        Client.__init__(self, "http://example/api/v1", "key")
        self.rows = rows
        self.page_cap = page_cap
        self.requests = []

    def _get_json(self, path, query=None, timeout=None):
        query = query or {}
        offset = int(query.get("offset", 0))
        limit = min(int(query.get("limit", len(self.rows))), self.page_cap)
        system = query.get("system")
        rows = ([r for r in self.rows if r["system"] == system]
                if system else self.rows)
        page = rows[offset:offset + limit]
        self.requests.append((offset, limit))
        return {
            "roms": page,
            "total": len(rows),
            "offset": offset,
            "limit": limit,
            "has_more": offset + len(page) < len(rows),
        }


def _catalogue():
    """23000 rows ordered by system, like the real catalogue."""
    rows = []
    for system, count in (("A2600", 6000), ("GBA", 9000),
                          ("PS1", 2000), ("SNES", 6000)):
        for index in range(count):
            rows.append({
                "rom_id": "%s_game_%05d" % (system.lower(), index),
                "system": system,
                "name": "Game %05d" % index,
                "filename": "Game %05d.rom" % index,
                "size": 16,
            })
    return rows


def test_every_system_survives_a_catalogue_larger_than_one_page():
    server = FakeServer(_catalogue())
    roms = server.list_roms()

    counts = {}
    for rom in roms:
        counts[rom["system"]] = counts.get(rom["system"], 0) + 1

    assert len(roms) == 23000
    # The bug: SNES sorts last, so a single page left it with 3000 of 6000.
    assert counts == {"A2600": 6000, "GBA": 9000, "PS1": 2000, "SNES": 6000}


def test_paging_walks_forward_and_stops():
    server = FakeServer(_catalogue())
    server.list_roms()

    offsets = [offset for offset, _limit in server.requests]
    assert offsets == sorted(offsets), "offsets must advance"
    assert len(set(offsets)) == len(offsets), "no page fetched twice"
    assert offsets[0] == 0


def test_a_short_catalogue_takes_a_single_request():
    server = FakeServer(_catalogue()[:10])
    assert len(server.list_roms()) == 10
    assert len(server.requests) == 1


def test_an_empty_catalogue_is_not_an_error():
    server = FakeServer([])
    assert server.list_roms() == []


def test_fields_filter_keeps_only_what_the_ui_needs():
    """A full catalogue is tens of thousands of rows on a 492 MB device."""
    server = FakeServer(_catalogue()[:5])
    roms = server.list_roms(fields=("rom_id", "system"))
    assert all(set(rom) == {"rom_id", "system"} for rom in roms)


def test_filtering_by_system_still_pages():
    server = FakeServer(_catalogue())
    roms = server.list_roms(system="SNES")
    assert len(roms) == 6000
    assert all(rom["system"] == "SNES" for rom in roms)


def test_a_server_that_never_says_stop_cannot_loop_forever():
    class Runaway(FakeServer):
        def _get_json(self, path, query=None, timeout=None):
            self.requests.append(0)
            return {"roms": [{"rom_id": "x", "system": "GBA"}],
                    "total": 1, "has_more": True}

    server = Runaway([])
    server.list_roms()
    assert len(server.requests) == Client.MAX_ROM_PAGES
