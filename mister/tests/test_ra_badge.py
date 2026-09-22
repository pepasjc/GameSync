"""RetroAchievements badge on rows.

Only a game with a *published* set earns the badge. A hash RA merely knows
(0 achievements) promises nothing, and -1 means the server had no API key
and could not read the count at all - neither gets one.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for candidate in (ROOT, ROOT / "mister"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from gamesync.app import Row, _group_has_achievements  # noqa: E402


class Group:
    def __init__(self, rows, system="SNES", name="Game"):
        self.rows = rows
        self.system = system
        self.name = name


def test_group_with_a_published_set_badges():
    assert _group_has_achievements(Group([{"ra_achievements": 63}]))


def test_registered_but_empty_set_does_not_badge():
    assert not _group_has_achievements(Group([{"ra_achievements": 0}]))


def test_unknown_count_does_not_badge():
    """-1 is 'server had no API key', not a promise of achievements."""
    assert not _group_has_achievements(Group([{"ra_achievements": -1}]))


def test_missing_field_does_not_badge():
    assert not _group_has_achievements(Group([{"name": "Game"}]))


def test_multi_disc_group_badges_when_any_disc_has_a_set():
    group = Group([{"ra_achievements": 0}, {"ra_achievements": 12}])
    assert _group_has_achievements(group)


def test_garbage_count_is_ignored_not_raised():
    assert not _group_has_achievements(Group([{"ra_achievements": "lots"}]))
    assert not _group_has_achievements(Group([{"ra_achievements": None}]))


def test_group_without_rows_does_not_badge():
    assert not _group_has_achievements(Group([]))
    assert not _group_has_achievements(Group(None))


def test_row_defaults_to_no_badge():
    assert Row("SNES", "Game", "1 MB", "installed").ra is False
    assert Row("SNES", "Game", "1 MB", "installed", ra=True).ra is True
