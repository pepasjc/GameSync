"""Installed tab: "replace with the RA version".

RA's Dreamcast Soulcalibur set was made against the Europe disc only. Both
regions title-match the set, so both badge in the catalogue - but only the
Europe one carries ``ra_dump`` (its file name is one RA registered), and a
USA copy on the card should be offered the swap.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for candidate in (ROOT, ROOT / "mister"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from gamesync.app import (  # noqa: E402
    App,
    InstalledGame,
    _group_ra_verified,
    _region_rank,
)
from shared.mister_install import DiscGroup  # noqa: E402


def _row(name, system="DC", **ra):
    row = {"name": name, "system": system, "filename": name + ".chd",
           "size": 1000}
    row.update(ra)
    return row


def _group(name, system="DC", **ra):
    return DiscGroup(system, name, [_row(name, system, **ra)])


EUROPE = dict(ra_achievements=88, ra_match="title", ra_game_id=3395,
              ra_dump=True)
USA = dict(ra_achievements=88, ra_match="title", ra_game_id=3395)


def _app(groups, installed):
    app = App.__new__(App)
    app.catalog_groups = groups
    app.installed_entries = installed
    app.installed_ids = set()
    app._catalog_rows = []
    app._index_catalog()
    from gamesync.app import _normalize
    app.installed_ids = {(i.system, _normalize(i.name), bool(i.is_pack))
                         for i in installed}
    return app


def _installed(name, system="DC"):
    return InstalledGame(system, name, "/media/fat/games/Dreamcast/" + name,
                         "SD", "Dreamcast")


def test_verified_means_hash_or_registered_dump():
    assert _group_ra_verified(_group("A", **EUROPE))
    assert not _group_ra_verified(_group("A", **USA))
    assert _group_ra_verified(_group("A", system="SNES", ra_achievements=5))
    assert not _group_ra_verified(_group("A", ra_achievements=0, ra_dump=True))


def test_usa_copy_is_offered_the_europe_version():
    usa = _group("Soulcalibur (USA)", **USA)
    europe = _group("Soulcalibur (Europe) (En,Fr,De,Es)", **EUROPE)
    app = _app([usa, europe], [_installed("Soulcalibur (USA)")])
    assert app.ra_variants(app.installed_entries[0]) == [europe]


def test_spelling_differences_still_find_the_variant():
    """No shared RA id needed: the tag-free names agree."""
    usa = _group("Soul Calibur (USA)")
    europe = _group("Soulcalibur (Europe) (En,Fr,De,Es)", **EUROPE)
    app = _app([usa, europe], [_installed("Soul Calibur (USA)")])
    assert app.ra_variants(app.installed_entries[0]) == [europe]


def test_nothing_offered_when_the_installed_copy_is_verified():
    europe = _group("Soulcalibur (Europe) (En,Fr,De,Es)", **EUROPE)
    other = _group("Soulcalibur (Japan)", **dict(EUROPE))
    app = _app([europe, other],
               [_installed("Soulcalibur (Europe) (En,Fr,De,Es)")])
    assert app.ra_variants(app.installed_entries[0]) == []


def test_nothing_offered_when_the_ra_version_is_already_installed():
    usa = _group("Soulcalibur (USA)", **USA)
    europe = _group("Soulcalibur (Europe) (En,Fr,De,Es)", **EUROPE)
    app = _app([usa, europe], [_installed("Soulcalibur (USA)"),
                               _installed("Soulcalibur (Europe) (En,Fr,De,Es)")])
    assert app.ra_variants(app.installed_entries[0]) == []


def test_games_the_catalogue_does_not_know_are_left_alone():
    europe = _group("Soulcalibur (Europe) (En,Fr,De,Es)", **EUROPE)
    app = _app([europe], [_installed("Soulcalibur (USA)")])
    assert app.ra_variants(app.installed_entries[0]) == []


def test_translations_and_hacks_are_never_swapped_or_offered():
    jp = _group("Game (Japan)", system="SNES")
    patched = _group("Game (Japan) [T-En by Someone]", system="SNES",
                     ra_achievements=10)
    usa = _group("Game (USA)", system="SNES", ra_achievements=10)
    app = _app([jp, patched, usa],
               [_installed("Game (Japan)", system="SNES"),
                _installed("Game (Japan) [T-En by Someone]", system="SNES")])
    assert app.ra_variants(app.installed_entries[0]) == [usa]
    assert app.ra_variants(app.installed_entries[1]) == []


def test_other_systems_do_not_count():
    usa = _group("Soulcalibur (USA)", **USA)
    elsewhere = _group("Soulcalibur (Europe)", system="PS1", ra_achievements=5)
    app = _app([usa, elsewhere], [_installed("Soulcalibur (USA)")])
    assert app.ra_variants(app.installed_entries[0]) == []


def test_usa_and_world_are_offered_before_other_regions():
    assert _region_rank("Game (World)") < _region_rank("Game (USA)")
    assert _region_rank("Game (USA)") < _region_rank("Game (Europe) (En,Fr)")
    assert _region_rank("Game (Europe)") < _region_rank("Game (Japan)")
    assert _region_rank("Game (Japan, USA)") == _region_rank("Game (USA)")


def test_bonus_discs_are_not_versions_of_the_game():
    bonus = _group("Persona 2 (USA) (Bonus Disc)", system="PS1")
    game = _group("Persona 2 (USA)", system="PS1", ra_achievements=40)
    app = _app([bonus, game], [_installed("Persona 2 (USA) (Bonus Disc)",
                                          system="PS1")])
    assert app.ra_variants(app.installed_entries[0]) == []


def test_a_same_named_entry_ra_knows_means_no_question():
    """Two catalogue entries share the installed name; one is verified.
    Which one is on the card is unknowable, so nothing is offered."""
    first = _group("Game (USA)", system="PS1")
    second = DiscGroup("PS1", "Game (USA)",
                       [_row("Game (USA)", "PS1", ra_achievements=10)])
    europe = _group("Game (Europe)", system="PS1", ra_achievements=10)
    app = _app([first, second, europe], [_installed("Game (USA)", "PS1")])
    assert app.ra_variants(app.installed_entries[0]) == []


def test_different_ra_games_are_not_versions_of_each_other():
    usa = _group("Game (USA)", system="PS1", ra_achievements=5,
                 ra_match="title", ra_game_id=1)
    europe = _group("Game (Europe)", system="PS1", ra_achievements=5,
                    ra_game_id=2)
    app = _app([usa, europe], [_installed("Game (USA)", "PS1")])
    assert app.ra_variants(app.installed_entries[0]) == []
