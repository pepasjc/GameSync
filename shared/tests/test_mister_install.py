"""Tests for the shared MiSTer ROM install rules."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.mister_install import (  # noqa: E402
    group_discs,
    bios_seed_sources,
    games_root,
    install_target,
    safe_file_name,
    safe_folder_name,
    strip_disc_tag,
    system_games_dir,
)


class FakeProvider:
    def __init__(self, tree):
        self.tree = tree

    def listdir(self, path):
        entry = self.tree.get(path.rstrip("/"))
        return list(entry) if isinstance(entry, list) else []

    def is_dir(self, path):
        return isinstance(self.tree.get(path.rstrip("/")), list)


def test_games_root_follows_the_rom_target():
    assert games_root("sd") == "/media/fat/games"
    assert games_root("usb") == "/media/usb0/games"
    assert games_root("") == "/media/fat/games"


def test_disc_tags_are_stripped_so_discs_share_a_folder():
    assert strip_disc_tag("Final Fantasy IX (USA) (Disc 1)") == \
        "Final Fantasy IX (USA)"
    assert strip_disc_tag("Game [Disk 2 of 3]") == "Game"
    assert strip_disc_tag("Plain Game (USA)") == "Plain Game (USA)"


def test_folder_name_keeps_a_version_tail_but_drops_a_rom_extension():
    assert safe_folder_name("Breath of Fire IV (USA).chd") == \
        "Breath of Fire IV (USA)"
    # A trailing version must survive; only known ROM extensions are dropped.
    assert safe_folder_name("Castlevania v1.021+hotfix") == \
        "Castlevania v1.021+hotfix"


def test_file_name_cannot_escape_the_target_folder():
    assert safe_file_name("../../etc/passwd") == "passwd"
    assert safe_file_name("sub/dir/game.chd") == "game.chd"
    assert safe_file_name("") == "download.rom"


def test_existing_legacy_folder_is_reused():
    provider = FakeProvider({
        "/media/fat/games": ["Genesis"],
        "/media/fat/games/Genesis": [],
    })
    assert system_games_dir(provider, "MD") == "/media/fat/games/Genesis"

    fresh = FakeProvider({"/media/fat/games": []})
    assert system_games_dir(fresh, "MD") == "/media/fat/games/MegaDrive"


def test_unknown_system_is_refused_rather_than_dumped_in_the_root():
    provider = FakeProvider({"/media/fat/games": []})
    assert system_games_dir(provider, "DREAMCAST") == ""
    assert install_target(provider, "DREAMCAST", "game.gdi") == ("", "")


def test_pcfx_has_no_mister_core_so_it_is_hidden_and_refused():
    # There is no PC-FX core for MiSTer. The on-device client only offers
    # systems in MISTER_SYSTEM_FOLDER_CANDIDATES, so PC-FX must stay out of
    # it, and an install is refused even if someone made a games/PCFX folder.
    from shared.mister import MISTER_SYSTEM_FOLDER_CANDIDATES

    assert "PCFX" not in MISTER_SYSTEM_FOLDER_CANDIDATES
    provider = FakeProvider({"/media/fat/games": ["PCFX"],
                             "/media/fat/games/PCFX": []})
    assert system_games_dir(provider, "PCFX") == ""
    assert install_target(provider, "PCFX", "Zenki FX (Japan).chd") == ("", "")


def test_cartridge_rom_installs_straight_into_the_system_folder():
    provider = FakeProvider({"/media/fat/games": ["SNES"],
                             "/media/fat/games/SNES": []})
    directory, name = install_target(provider, "SNES",
                                     "Super Mario World (USA).sfc")
    assert directory == "/media/fat/games/SNES"
    assert name == "Super Mario World (USA).sfc"


def test_cd_game_gets_its_own_folder_and_all_discs_share_it():
    """The core names its memory card after the folder, so both discs must
    land in the same one or each disc would get a separate save."""
    provider = FakeProvider({"/media/fat/games": ["PSX"],
                             "/media/fat/games/PSX": []})
    first = install_target(provider, "PS1", "FFIX (Disc 1).chd",
                           "Final Fantasy IX (USA) (Disc 1)")
    second = install_target(provider, "PS1", "FFIX (Disc 2).chd",
                            "Final Fantasy IX (USA) (Disc 2)")

    assert first[0] == "/media/fat/games/PSX/Final Fantasy IX (USA)"
    assert first[0] == second[0]
    assert first[1] != second[1]


def test_usb_target_uses_the_usb_root():
    provider = FakeProvider({"/media/usb0/games": ["SNES"],
                             "/media/usb0/games/SNES": []})
    directory, _ = install_target(provider, "SNES", "game.sfc",
                                  rom_target="usb")
    assert directory == "/media/usb0/games/SNES"


def test_bios_is_seeded_only_for_usb_installs():
    """Creating a USB core folder makes the core ignore the SD card, so a CD
    core would lose its boot.rom."""
    provider = FakeProvider({
        "/media/fat/games": ["PSX"],
        "/media/fat/games/PSX": ["boot.rom", "boot1.rom", "Game.chd"],
    })
    sources = bios_seed_sources(provider, "PS1", "usb")
    assert sources == ["/media/fat/games/PSX/boot.rom",
                       "/media/fat/games/PSX/boot1.rom"]

    # Installing to the SD card cannot shadow anything, so nothing to seed.
    assert bios_seed_sources(provider, "PS1", "sd") == []


def test_bios_seeding_is_quiet_when_there_is_nothing_to_copy():
    provider = FakeProvider({"/media/fat/games": ["SNES"],
                             "/media/fat/games/SNES": ["game.sfc"]})
    assert bios_seed_sources(provider, "SNES", "usb") == []


def test_multi_disc_games_fold_into_one_entry():
    """Every disc installs into the same folder, so they are one thing to
    install; offering them separately invites installing half a game."""
    rows = [
        {"rom_id": "ff9_d%d" % n, "system": "PS1", "size": 100,
         "name": "Final Fantasy IX (USA) (Disc %d)" % n,
         "filename": "Final Fantasy IX (USA) (Disc %d).chd" % n,
         "disc_index": n, "primary_rom_id": "ff9_d1"}
        for n in (2, 1, 4, 3)
    ]
    groups = group_discs(rows)

    assert len(groups) == 1
    group = groups[0]
    assert group.name == "Final Fantasy IX (USA)"
    assert group.disc_count == 4
    assert group.size == 400
    # Disc 1 first, so it is the one the core sees initially.
    assert [row["disc_index"] for row in group.rows] == [1, 2, 3, 4]


def test_discs_group_without_a_primary_rom_id():
    """Only some systems get primary_rom_id from the server."""
    rows = [
        {"rom_id": "a", "system": "SAT", "size": 1,
         "name": "Panzer Dragoon Saga (USA) (Disc 1)"},
        {"rom_id": "b", "system": "SAT", "size": 1,
         "name": "Panzer Dragoon Saga (USA) (Disc 2)"},
    ]
    groups = group_discs(rows)
    assert len(groups) == 1
    assert groups[0].name == "Panzer Dragoon Saga (USA)"


def test_different_games_do_not_merge():
    rows = [
        {"rom_id": "a", "system": "PS1", "name": "Game A (USA) (Disc 1)"},
        {"rom_id": "b", "system": "PS1", "name": "Game B (USA) (Disc 1)"},
    ]
    assert len(group_discs(rows)) == 2


def test_cartridge_systems_are_never_grouped():
    """Two SNES games with similar names must stay separate."""
    rows = [
        {"rom_id": "a", "system": "SNES", "name": "Super Mario World (USA)"},
        {"rom_id": "b", "system": "SNES", "name": "Super Mario World 2 (USA)"},
    ]
    assert len(group_discs(rows)) == 2


def test_a_single_disc_cd_game_is_still_one_group():
    rows = [{"rom_id": "a", "system": "PS1", "size": 5,
             "name": "Dino Crisis 2 (USA)"}]
    groups = group_discs(rows)
    assert len(groups) == 1
    assert groups[0].disc_count == 1
    assert groups[0].name == "Dino Crisis 2 (USA)"


def test_one_group_installs_into_exactly_one_folder():
    """Seen live: the server gave a vanilla release, a fan translation of it
    and a bonus disc the same primary_rom_id, but they install into three
    different folders. Trusting that id would have mixed them into one entry."""
    rows = [
        {"rom_id": "a", "system": "PS1", "primary_rom_id": "same",
         "name": "Lunar 2 (USA) (Disc 1)"},
        {"rom_id": "b", "system": "PS1", "primary_rom_id": "same",
         "name": "Lunar 2 (USA) (Disc 1) [Un-Worked Design]"},
        {"rom_id": "c", "system": "PS1", "primary_rom_id": "same",
         "name": "Lunar 2 (USA) (The Making of)"},
    ]
    groups = group_discs(rows)

    assert len(groups) == 3
    folders = {safe_folder_name(strip_disc_tag(row["name"]))
               for group in groups for row in group.rows}
    assert len(folders) == 3


def test_msu_pack_layout_routes_msu_md_to_the_megacd_core():
    from shared.mister_install import msu_pack_layout, msu_pack_target

    assert msu_pack_layout("msu1", "SNES") == ("SNES", None)
    assert msu_pack_layout("mdplus", "MD") == ("MD", None)
    assert msu_pack_layout("msu-md", "MD") == ("SEGACD", "cart.rom")
    assert msu_pack_layout("weird", "MD") is None

    provider = FakeProvider({
        "/media/fat/games": ["SNES", "MegaCD", "Genesis"],
        "/media/fat/games/SNES": [],
        "/media/fat/games/MegaCD": [],
        "/media/fat/games/Genesis": [],
    })
    assert msu_pack_target(provider, "SNES", "msu1", "ActRaiser (USA) (MSU1)") == (
        "/media/fat/games/SNES/ActRaiser (USA) (MSU1)", None)
    assert msu_pack_target(provider, "MD", "msu-md", "Sonic 2 (MSU-MD)") == (
        "/media/fat/games/MegaCD/Sonic 2 (MSU-MD)", "cart.rom")
    assert msu_pack_target(provider, "MD", "mdplus", "Sonic 2 (MD+)") == (
        "/media/fat/games/Genesis/Sonic 2 (MD+)", None)
    assert msu_pack_target(provider, "MD", "nope", "x") == ("", None)
