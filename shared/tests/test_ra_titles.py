"""Title matching for disc systems.

A title match is a weaker claim than a hash match, so the bar is strict
equality on a normalised name.  Badging the wrong game is worse than
badging nothing.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shared.ra_titles import (  # noqa: E402
    TitleIndex,
    build_index,
    normalize,
    title_variants,
)
from shared.ra_api import RaLibrary  # noqa: E402


# --- normalisation ------------------------------------------------------------


def test_region_disc_and_translation_tags_are_dropped():
    assert normalize("Final Fantasy VII (USA) (Disc 1).chd") == normalize("Final Fantasy 7")
    assert normalize("Grandia (Japan) [T-En by X v1.0].chd") == normalize("Grandia")


def test_punctuation_between_title_and_subtitle_does_not_matter():
    a = normalize("Castlevania - Symphony of the Night (USA).chd")
    b = normalize("Castlevania: Symphony of the Night")
    assert a == b == "castlevaniasymphonyofnight"


def test_article_position_does_not_matter():
    """No-Intro writes 'Legend of Zelda, The'; RA writes 'The Legend of Zelda'."""
    assert normalize("Legend of Zelda, The - Ocarina of Time (USA).z64") == \
           normalize("The Legend of Zelda: Ocarina of Time")


def test_roman_numerals_fold_to_digits():
    assert normalize("Final Fantasy VII") == normalize("Final Fantasy 7")
    assert normalize("Street Fighter II") == normalize("Street Fighter 2")


def test_extension_is_stripped_but_a_dotted_title_survives():
    assert normalize("Tekken 3.chd") == normalize("Tekken 3")
    # "Vol. 2" must not lose the 2 to extension stripping.
    assert "2" in normalize("Puzzle Collection Vol. 2")


def test_case_and_spacing_are_irrelevant():
    assert normalize("  METAL   GEAR  SOLID ") == normalize("Metal Gear Solid")


def test_empty_name_normalises_empty():
    assert normalize("") == ""
    assert normalize("(USA).chd") == ""


# --- RA title quirks ----------------------------------------------------------


def test_ra_hack_and_demo_prefixes_are_stripped():
    assert title_variants("~Hack~ Kaizo Mario World") == [normalize("Kaizo Mario World")]
    assert title_variants("~Demo~ Some Game") == [normalize("Some Game")]


def test_piped_alternates_are_both_indexed():
    variants = title_variants("Jerry Boy | Smart Ball")
    assert normalize("Jerry Boy") in variants
    assert normalize("Smart Ball") in variants


# --- the index ----------------------------------------------------------------


def _index(*games):
    return TitleIndex(games)


def test_lookup_finds_a_game_by_filename():
    index = _index((100, "Symphony of the Night", 50))
    assert index.lookup("Symphony of the Night (USA).chd") == (100, 50)


def test_lookup_misses_return_none():
    index = _index((100, "Symphony of the Night", 50))
    assert index.lookup("Some Other Game (USA).chd") is None
    assert index.lookup("") is None


def test_ambiguous_titles_are_dropped_rather_than_guessed():
    """Two different games normalising alike must badge neither."""
    index = _index((1, "Twin Cobra", 10), (2, "Twin Cobra", 20))
    assert index.lookup("Twin Cobra (USA).chd") is None


def test_the_same_game_listed_twice_is_not_treated_as_a_clash():
    index = _index((1, "Twin Cobra", 10), (1, "Twin Cobra | Kyukyoku Tiger", 10))
    assert index.lookup("Twin Cobra (USA).chd") == (1, 10)
    assert index.lookup("Kyukyoku Tiger (Japan).chd") == (1, 10)


def test_a_near_miss_does_not_match():
    """Strict equality: no fuzzy fallback."""
    index = _index((1, "Final Fantasy VII", 90))
    assert index.lookup("Final Fantasy VIII (USA).chd") is None
    assert index.lookup("Final Fantasy VII Remake (USA).chd") is None


# --- building from a library ---------------------------------------------------


def test_build_index_carries_achievement_counts():
    lib = RaLibrary(12, {}, {5: "Tekken 3", 6: "Ridge Racer"}, achievements={5: 42, 6: 0})
    index = build_index(lib)
    assert index.lookup("Tekken 3 (USA).chd") == (5, 42)
    assert index.lookup("Ridge Racer (USA).chd") == (6, 0)


def test_build_index_marks_unknown_counts_as_minus_one():
    """Public endpoints report no counts; that must not read as zero."""
    lib = RaLibrary(12, {}, {5: "Tekken 3"})          # achievements=None
    assert build_index(lib).lookup("Tekken 3 (USA).chd") == (5, -1)


def test_build_index_over_an_empty_library():
    assert len(build_index(RaLibrary(12))) == 0


def test_subsets_do_not_erase_the_base_game():
    index = TitleIndex([
        (6049, "Super Mario Sunshine", 148),
        (28562, "Super Mario Sunshine [Subset - Bonus]", 75),
        (28560, "Super Mario Sunshine [Subset - Hoverless]", 53),
    ])
    assert index.lookup("Super Mario Sunshine (USA)") == (6049, 148)


def test_retail_entry_outranks_a_tagged_one_of_the_same_name():
    index = TitleIndex([(2, "~Hack~ Star Fox", 10), (1, "Star Fox", 40)])
    assert index.lookup("Star Fox (USA)") == (1, 40)
    index = TitleIndex([(1, "Star Fox", 40), (2, "~Hack~ Star Fox", 10)])
    assert index.lookup("Star Fox (USA)") == (1, 40)


def test_two_retail_games_with_one_name_stay_ambiguous():
    index = TitleIndex([(1, "Tetris", 10), (2, "Tetris", 20)])
    assert index.lookup("Tetris") is None
