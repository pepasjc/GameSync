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
    # The RA-supported translation is on the card already: nothing to add.
    assert app.ra_variants(app.installed_entries[0]) == []
    assert app.ra_variants(app.installed_entries[1]) == []
    app = _app([jp, patched, usa], [_installed("Game (Japan)", system="SNES")])
    assert app.ra_variants(app.installed_entries[0]) == [usa]


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


def test_a_translation_is_offered_the_translation_ra_supports():
    """RA registers fan translations too, under its own tag style."""
    mine = _group("Super Aleste (Japan) [T-En by Asiwish v1.2] [FastROM hack]",
                  system="SNES")
    ras = _group("Super Aleste (Japan) [T-En v1.0 tettsui77]", system="SNES",
                 ra_achievements=30)
    original = _group("Super Aleste (Japan)", system="SNES", ra_achievements=30)
    app = _app([mine, ras, original],
               [_installed(mine.name, system="SNES")])
    assert app.ra_variants(app.installed_entries[0]) == [ras]


def test_a_translation_is_never_offered_its_untranslated_original():
    mine = _group("Psycho Dream (Japan) [T-En by Stardust Crusaders v1.0]",
                  system="SNES")
    original = _group("Psycho Dream (Japan)", system="SNES", ra_achievements=9)
    app = _app([mine, original], [_installed(mine.name, system="SNES")])
    assert app.ra_variants(app.installed_entries[0]) == []


def test_an_english_retranslation_may_go_back_to_the_english_release():
    mine = _group("Skyblazer (USA) [T-En by Krokodyl] [Relocalized]",
                  system="SNES")
    retail = _group("Skyblazer (USA)", system="SNES", ra_achievements=20)
    app = _app([mine, retail], [_installed(mine.name, system="SNES")])
    assert app.ra_variants(app.installed_entries[0]) == [retail]


def test_a_retail_game_is_offered_a_translation_only_when_nothing_else():
    """RA's Ganbare Goemon 3 set exists for the DDSTranslation patch alone."""
    mine = _group("Ganbare Goemon 3 (Japan)", system="SNES")
    patched = _group("Ganbare Goemon 3 (Japan) [T-En v4.0]", system="SNES",
                     ra_achievements=40)
    app = _app([mine, patched], [_installed(mine.name, system="SNES")])
    assert app.ra_variants(app.installed_entries[0]) == [patched]

    usa = _group("Ganbare Goemon 3 (USA)", system="SNES", ra_achievements=40)
    app = _app([mine, patched, usa], [_installed(mine.name, system="SNES")])
    assert app.ra_variants(app.installed_entries[0]) == [usa]


def test_a_plain_hack_is_never_swapped():
    mine = _group("Super Mario World (USA) [Hack by Someone]", system="SNES")
    retail = _group("Super Mario World (USA)", system="SNES", ra_achievements=90)
    app = _app([mine, retail], [_installed(mine.name, system="SNES")])
    assert app.ra_variants(app.installed_entries[0]) == []


def test_an_msu_pack_does_not_stand_in_for_the_plain_rom():
    """The name key folds "(MSU1)" away; the pack must not be mistaken for
    the installed ROM (it is unverified, the ROM is)."""
    pack = DiscGroup("SNES", "Final Fight 3 (USA) (MSU1)",
                     [_row("Final Fight 3 (USA) (MSU1)", "SNES",
                           is_bundle=True, bundle_kind="msu1")])
    rom = _group("Final Fight 3 (USA)", system="SNES")
    europe = _group("Final Fight 3 (Europe)", system="SNES", ra_achievements=30)
    app = _app([pack, rom, europe],
               [_installed("Final Fight 3 (USA)", system="SNES")])
    assert app.ra_variants(app.installed_entries[0]) == [europe]


# --- installs the catalogue does not know by name -----------------------------

from shared.ra_variants import VariantIndex  # noqa: E402


def _index(groups):
    from gamesync.app import _normalize, _group_pack_kind
    return VariantIndex(groups, _normalize, _group_pack_kind)


def test_a_renamed_patch_is_judged_by_its_hash():
    """"[Splash screen removed]" puts the install in no catalogue entry;
    its RA hash says RA does not know it, so the RA version is offered."""
    ras = _group("Front Mission (Japan) [T-En by Frank Hughes v1.0b] [Add by mziab v1.03]",
                 system="SNES", ra_achievements=50, ra_game_id=2895,
                 ra_hash="9a8e7a91")
    index = _index([ras])
    mine = "Front Mission (Japan) [T-En by Frank Hughes v1.0b] [Add by mziab v1.03] [Splash screen removed]"
    assert index.variants("SNES", mine, content_hash=lambda: "d79d9c07") == [ras]
    # The same file RA does know: nothing to offer.
    assert index.variants("SNES", mine, content_hash=lambda: "9A8E7A91") == []
    # No hash (a disc, or unreadable): a game the catalogue cannot see is
    # still never second-guessed.
    assert index.variants("SNES", mine) == []


def test_versions_are_linked_by_ra_game_when_names_differ():
    """The server links an unknown cartridge to its RA game by name; RA's
    title for it can share no spelling with the file."""
    mine = _group("Front Mission Series - Gun Hazard (Japan) [T-En by Aeon Genesis v1.01]",
                  system="SNES", ra_achievements=0, ra_match="title", ra_game_id=1408)
    ras = _group("Front Mission - Gun Hazard (Japan) [T-En Aeon Genesis 1.01]",
                 system="SNES", ra_achievements=112, ra_game_id=1408, ra_hash="fd473bc0")
    index = _index([mine, ras])
    assert index.variants("SNES", mine.name) == [ras]


def test_gokujou_parodius_punctuation_and_patch_tags_do_not_matter():
    ras = _group("Gokujou Parodius! (Japan) [T-En v1.0 blizzz]", system="SNES",
                 ra_achievements=40, ra_hash="aaaa")
    index = _index([ras])
    mine = "Gokujou Parodius (Japan) [T-En by Blizzz v1.1a] [no SRAM]"
    assert index.variants("SNES", mine, content_hash=lambda: "bbbb") == [ras]


def test_hash_is_only_read_when_there_is_something_to_offer():
    calls = []
    index = _index([_group("Other (USA)", system="SNES", ra_achievements=5)])
    index.variants("SNES", "Game (Japan)", content_hash=lambda: calls.append(1))
    assert calls == []


# --- the save follows the game within a region --------------------------------

from shared.ra_variants import same_region  # noqa: E402
from gamesync.app import _move_saves  # noqa: E402


def test_same_region_ignores_patch_tags_and_needs_a_shared_region():
    assert same_region("Front Mission (Japan) [T-En by A] [Splash screen removed]",
                       "Front Mission (Japan) [T-En by A] [Add by B]")
    assert same_region("Game (USA)", "Game (USA, Europe)")
    assert not same_region("Soulcalibur (USA)", "Soulcalibur (Europe) (En,Fr,De,Es)")
    assert not same_region("Homebrew", "Homebrew v2")


def test_save_is_renamed_to_the_new_stem(tmp_path):
    old = tmp_path / "Front Mission (Japan) [T-En] [Splash screen removed].sav"
    old.write_bytes(b"sram")
    problem = _move_saves([str(old)], "Front Mission (Japan) [T-En] [Splash screen removed]",
                          "Front Mission (Japan) [T-En]")
    assert problem == ""
    assert not old.exists()
    assert (tmp_path / "Front Mission (Japan) [T-En].sav").read_bytes() == b"sram"


def test_save_never_overwrites_one_the_new_version_has(tmp_path):
    old = tmp_path / "Old.sav"
    old.write_bytes(b"mine")
    new = tmp_path / "New.sav"
    new.write_bytes(b"theirs")
    assert "already exists" in _move_saves([str(old)], "Old", "New")
    assert old.read_bytes() == b"mine" and new.read_bytes() == b"theirs"


# --- replace runs in the background -------------------------------------------

from gamesync import app as gsapp  # noqa: E402
from gamesync import downloads as gsdl  # noqa: E402


class _Queued:
    def __init__(self, target, status):
        self.target, self.status = target, status


def _replacing_app(tmp_path, monkeypatch, job, queued):
    monkeypatch.setattr(gsapp, "REPLACEMENTS_PATH", str(tmp_path / "replacements.json"))
    app = App.__new__(App)
    app._replacements = [job]
    app.queue = type("Q", (), {"items": queued})()
    app.installed_entries = []
    app.toasts = []
    app.load_data = lambda rescan=True: None
    app.draw_all = lambda: None
    app.toast = lambda text, colour=None, hold=0.0: app.toasts.append(text)
    return app


def _job(tmp_path, old, new_target, saves=()):
    return {"targets": [str(new_target)], "old_path": str(old), "old_name": old.stem,
            "new_name": new_target.stem, "new_stem": new_target.stem,
            "saves": [str(s) for s in saves]}


def test_old_copy_waits_until_the_new_one_has_landed(tmp_path, monkeypatch):
    old = tmp_path / "Game (Japan) [T-En] [Splash].sfc"
    old.write_bytes(b"old")
    new = tmp_path / "Game (Japan) [T-En].sfc"
    job = _job(tmp_path, old, new)
    queued = [_Queued(str(new), gsdl.DOWNLOADING)]
    app = _replacing_app(tmp_path, monkeypatch, job, queued)

    app.finish_replacements()
    assert old.exists() and app._replacements == [job]

    new.write_bytes(b"new")
    queued[0].status = gsdl.DONE
    app.finish_replacements()
    assert not old.exists() and app._replacements == []
    assert app.toasts and app.toasts[0].startswith("Replaced")


def test_a_failed_download_keeps_the_old_copy(tmp_path, monkeypatch):
    old = tmp_path / "Game (USA).sfc"
    old.write_bytes(b"old")
    new = tmp_path / "Game (Europe).sfc"
    app = _replacing_app(tmp_path, monkeypatch, _job(tmp_path, old, new),
                         [_Queued(str(new), gsdl.FAILED)])
    app.finish_replacements()
    assert old.exists() and app._replacements == []
    assert "kept" in app.toasts[0]


def test_the_save_moves_with_the_game_once_it_lands(tmp_path, monkeypatch):
    old = tmp_path / "Game (Japan) [T-En] [Splash].sfc"
    old.write_bytes(b"old")
    save = tmp_path / "Game (Japan) [T-En] [Splash].sav"
    save.write_bytes(b"sram")
    new = tmp_path / "Game (Japan) [T-En].sfc"
    new.write_bytes(b"new")
    app = _replacing_app(tmp_path, monkeypatch, _job(tmp_path, old, new, [save]),
                         [_Queued(str(new), gsdl.DONE)])
    app.finish_replacements()
    assert (tmp_path / "Game (Japan) [T-En].sav").read_bytes() == b"sram"
    assert app.toasts[0].endswith("save moved")


def test_a_swap_that_landed_before_a_restart_still_finishes(tmp_path, monkeypatch):
    """Done items are dropped from the queue on load; the file is the proof."""
    old = tmp_path / "Game (USA).sfc"
    old.write_bytes(b"old")
    new = tmp_path / "Game (USA) (Rev 1).sfc"
    new.write_bytes(b"new")
    app = _replacing_app(tmp_path, monkeypatch, _job(tmp_path, old, new), [])
    app.finish_replacements()
    assert not old.exists()


def test_installed_hash_is_read_in_the_background_unless_asked_to_wait(tmp_path, monkeypatch):
    import hashlib
    monkeypatch.setattr(gsapp, "RA_HASH_CACHE_PATH", str(tmp_path / "cache.json"))
    rom = tmp_path / "Game (USA).sfc"
    rom.write_bytes(b"\x01" * 4096)
    want = hashlib.md5(rom.read_bytes()).hexdigest()
    app = App.__new__(App)
    item = InstalledGame("SNES", "Game (USA)", str(rom), "SD", "SNES")

    assert app.installed_ra_hash(item) is None          # queued, not read
    app._ra_hash_thread.join(5)
    assert app._ra_hash_landed
    assert app.installed_ra_hash(item) == want           # from the cache now

    other = tmp_path / "Other (USA).sfc"
    other.write_bytes(b"\x02" * 4096)
    item2 = InstalledGame("SNES", "Other (USA)", str(other), "SD", "SNES")
    assert app.installed_ra_hash(item2, wait=True) == hashlib.md5(other.read_bytes()).hexdigest()
