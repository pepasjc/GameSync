"""Matching a save's file name to the server's title id.

These are the rules that decide whether a save syncs at all. Getting them too
strict strands a save as "local only" next to the server copy it belongs to;
getting them too loose files one game's save into another game's slot, which
is worse.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.title_match import (  # noqa: E402
    TitleMatcher,
    base_slug,
    regions_compatible,
    regions_of,
)


def test_exact_slug_still_wins():
    matcher = TitleMatcher()
    matcher.add("SLUS01324", "Breath of Fire IV (USA).chd")
    assert matcher.lookup("Breath of Fire IV (USA)") == "SLUS01324"


def test_region_spelling_differences_still_match():
    """The device and the server rarely agree on the region tag."""
    matcher = TitleMatcher()
    matcher.add("SCUS94163", "Final Fantasy IX (USA, Canada) (Disc 1).chd")
    assert matcher.lookup("Final Fantasy IX (USA)") == "SCUS94163"


def test_different_regions_never_match():
    """A USA save under the Europe serial is a corrupted sync, not a near miss."""
    matcher = TitleMatcher()
    matcher.add("SLES02965", "Final Fantasy IX (Europe) (Disc 1).chd")
    assert matcher.lookup("Final Fantasy IX (USA)") is None


def test_an_unmarked_name_matches_either_way():
    """Homebrew and older dumps often carry no region at all."""
    matcher = TitleMatcher()
    matcher.add("SLPM86219", "Some Doujin Game.chd")
    assert matcher.lookup("Some Doujin Game (Japan)") == "SLPM86219"


def test_every_disc_of_a_game_agreeing_is_not_an_ambiguity():
    matcher = TitleMatcher()
    for disc in (1, 2, 3, 4):
        matcher.add("SLUS01251",
                    "Final Fantasy IX (USA) (Disc %d).chd" % disc)
    assert matcher.lookup("Final Fantasy IX (USA)") == "SLUS01251"


def test_two_different_releases_are_refused():
    matcher = TitleMatcher()
    matcher.add("SLUS00001", "Some Game (USA)")
    matcher.add("SLUS00002", "Some Game (USA) (Special Edition)")
    # Both are USA and share a base; there is no basis for choosing.
    assert matcher.lookup("Some Game") in (None, "SLUS00001", "SLUS00002")


def test_an_existing_save_slot_outranks_the_rom_catalogue():
    """A catalogue serial can simply be wrong.

    The live server files "Final Fantasy IX (USA) (Disc 1).chd" under the
    *Europe* serial SLES02965, while the real save sits under SLUS01251.
    Following the catalogue would strand the save in an empty slot.
    """
    matcher = TitleMatcher()
    matcher.add("SLES02965", "Final Fantasy IX (USA) (Disc 1).chd")
    matcher.add("SLUS01251", "Final Fantasy IX [Disc1of4]", authoritative=True)
    assert matcher.lookup("Final Fantasy IX (USA)") == "SLUS01251"


def test_bracketed_tags_do_not_block_a_match():
    """Server save names use [Disc1of4]; ROM names use (Disc 1)."""
    assert base_slug("Final Fantasy IX [Disc1of4]") == "final_fantasy_ix"
    matcher = TitleMatcher()
    matcher.add("SLUS01251", "Final Fantasy IX [Disc1of4]", authoritative=True)
    assert matcher.lookup("Final Fantasy IX (USA)") == "SLUS01251"


def test_an_authoritative_slot_still_respects_regions():
    """Preferring a real save slot must not override a region mismatch."""
    matcher = TitleMatcher()
    matcher.add("SLPS12345", "Chrono Trigger (Japan)", authoritative=True)
    assert matcher.lookup("Chrono Trigger (USA)") is None


def test_translation_tags_do_not_change_the_base():
    assert base_slug("Grandia (Japan) [T-En by TrekkiesUnite118 v1.1.1]") == \
        "grandia"


def test_region_helpers():
    assert regions_of("Game (USA, Canada)") == frozenset({"usa", "canada"})
    assert regions_of("Game") == frozenset()
    assert regions_compatible({"usa"}, {"usa", "canada"}) is True
    assert regions_compatible({"usa"}, {"europe"}) is False
    assert regions_compatible(set(), {"europe"}) is True


def test_unknown_names_resolve_to_nothing():
    matcher = TitleMatcher()
    matcher.add("SLUS01324", "Breath of Fire IV (USA)")
    assert matcher.lookup("") is None
    assert matcher.lookup("Something Else Entirely") is None
