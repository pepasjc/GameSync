"""RetroAchievements badge on rows.

Only a game with a *published* set earns a badge. A hash RA merely knows
(0 achievements) promises nothing, and -1 means the server had no API key
and could not read the count at all - neither gets one.

The badge also says how the game was identified: "hash" is exact, "title"
only means RA has a set for a game of this name, which is all a disc
system can offer until something can read a CHD.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for candidate in (ROOT, ROOT / "mister"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from gamesync.app import Row, _group_ra_kind  # noqa: E402


class Group:
    def __init__(self, rows, system="SNES", name="Game"):
        self.rows = rows
        self.system = system
        self.name = name


# --- does it badge at all? ----------------------------------------------------


def test_group_with_a_published_set_badges():
    assert _group_ra_kind(Group([{"ra_achievements": 63}])) == "hash"


def test_registered_but_empty_set_does_not_badge():
    assert _group_ra_kind(Group([{"ra_achievements": 0}])) == ""


def test_unknown_count_does_not_badge():
    """-1 is 'server had no API key', not a promise of achievements."""
    assert _group_ra_kind(Group([{"ra_achievements": -1}])) == ""


def test_missing_field_does_not_badge():
    assert _group_ra_kind(Group([{"name": "Game"}])) == ""


def test_multi_disc_group_badges_when_any_disc_has_a_set():
    assert _group_ra_kind(Group([{"ra_achievements": 0}, {"ra_achievements": 12}])) == "hash"


def test_garbage_count_is_ignored_not_raised():
    assert _group_ra_kind(Group([{"ra_achievements": "lots"}])) == ""
    assert _group_ra_kind(Group([{"ra_achievements": None}])) == ""


def test_group_without_rows_does_not_badge():
    assert _group_ra_kind(Group([])) == ""
    assert _group_ra_kind(Group(None)) == ""


# --- which kind of match? -----------------------------------------------------


def test_absent_match_field_is_treated_as_an_exact_hash():
    """Older servers send no ra_match; everything they indexed was a hash."""
    assert _group_ra_kind(Group([{"ra_achievements": 40}])) == "hash"


def test_title_match_is_reported_as_title():
    group = Group([{"ra_achievements": 94, "ra_match": "title"}], system="PS1")
    assert _group_ra_kind(group) == "title"


def test_exact_match_wins_over_a_title_match_in_the_same_group():
    """The stronger claim should be the one shown."""
    group = Group([
        {"ra_achievements": 30, "ra_match": "title"},
        {"ra_achievements": 30, "ra_match": "hash"},
    ])
    assert _group_ra_kind(group) == "hash"


def test_a_title_match_with_no_set_still_does_not_badge():
    group = Group([{"ra_achievements": 0, "ra_match": "title"}])
    assert _group_ra_kind(group) == ""


def test_match_kind_is_case_insensitive():
    assert _group_ra_kind(Group([{"ra_achievements": 5, "ra_match": "TITLE"}])) == "title"


# --- the Row itself -----------------------------------------------------------


def test_row_defaults_to_no_badge():
    assert Row("SNES", "Game", "1 MB", "installed").ra == ""


def test_row_accepts_a_match_kind():
    assert Row("PS1", "Game", "1 MB", "installed", ra="title").ra == "title"
    assert Row("SNES", "Game", "1 MB", "installed", ra="hash").ra == "hash"


def test_row_still_accepts_a_plain_flag():
    """Older callers passed a bool; that means an exact match."""
    assert Row("SNES", "Game", "1 MB", "installed", ra=True).ra == "hash"
    assert Row("SNES", "Game", "1 MB", "installed", ra=False).ra == ""
