"""The Installed tab lists games, not the BIOS files sitting beside them.

Every CD core keeps its BIOS in the games folder, so before filtering a system
holding nothing but ``boot.rom`` looked like a system with a game in it - and
there are enough of those to bury the systems that really are populated.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for candidate in (ROOT, ROOT / "mister"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from shared.mister_scan import (  # noqa: E402
    filter_game_entries,
    is_game_entry,
    list_installed_games,
)


def test_bios_files_are_not_games():
    for name in ("boot.rom", "boot0.rom", "boot3.rom", "cd_bios.rom",
                 "CD_BIOS.ROM", "syscard3.pce", "neocd.bin", "tos.img",
                 "uni-bios_4_0.rom", "000-lo.lo", "sfix.sfix", "neogeo.zip",
                 "x68000_iplrom.dat", "Saturn BIOS.bin", "kick34005.a500.rom"):
        assert not is_game_entry(name), name


def test_real_games_survive():
    for name in ("Sonic the Hedgehog (USA).md", "Kirby's Adventure.nes",
                 "Final Fantasy VII (Disc 1).chd", "Kick Off 2.adf",
                 "Bios Fighter.md"):
        assert is_game_entry(name), name


def test_support_folders_are_not_games():
    assert not is_game_entry("Palettes", is_dir=True)
    assert not is_game_entry("config", is_dir=True)
    assert is_game_entry("Final Fantasy IX (USA)", is_dir=True)


def test_a_cue_and_its_bin_are_one_game():
    listing = [("Game.cue", False), ("Game.bin", False),
               ("Game (Track 02).bin", False), ("boot.rom", False),
               ("Other.chd", False), ("Palettes", True)]

    assert filter_game_entries(listing) == [("Game.cue", False),
                                            ("Other.chd", False)]


def test_a_lone_bin_is_still_a_game():
    """No sheet beside it means the .bin is the disc image itself."""
    assert filter_game_entries([("Game.bin", False)]) == [("Game.bin", False)]


# --------------------------------------------------------------- folder scan

class FakeProvider:
    """Reads a dict tree: {path: [names]} plus a set of directory paths."""

    def __init__(self, tree, dirs):
        self.tree = tree
        self.dirs = set(dirs)

    def listdir(self, path):
        return list(self.tree.get(path, []))

    def is_dir(self, path):
        return path in self.dirs


def build(entries):
    """``entries`` maps a path to its children; a child ending in / is a dir."""
    tree, dirs = {}, set()
    for path, children in entries.items():
        dirs.add(path)
        tree[path] = [c.rstrip("/") for c in children]
        for child in children:
            if child.endswith("/"):
                dirs.add(path + "/" + child.rstrip("/"))
    return FakeProvider(tree, dirs)


def names(roms):
    return sorted(rom.name for rom in roms)


def test_only_the_cores_own_extensions_count():
    """``boot.rom`` in the GBA folder is a BIOS, not a game."""
    provider = build({"/g/GBA": ["boot.rom", "Metroid Fusion (USA).gba"]})

    roms = list_installed_games(provider, "/g/GBA", "GBA")

    assert names(roms) == ["Metroid Fusion (USA)"]


def test_a_bios_only_folder_lists_nothing():
    """The whole point: that system then disappears from the tab."""
    provider = build({"/g/TGFX16-CD": ["cd_bios.rom"]})

    assert list_installed_games(provider, "/g/TGFX16-CD", "TGFX16-CD") == []


def test_a_cd_folder_is_a_game_only_when_it_holds_a_disc():
    provider = build({
        "/g/MegaCD": ["boot.rom", "Snatcher (USA)/", "USA/"],
        "/g/MegaCD/Snatcher (USA)": ["Snatcher (USA).chd"],
        "/g/MegaCD/USA": ["cd_bios.rom"],  # regional BIOS, not a game
    })

    roms = list_installed_games(provider, "/g/MegaCD", "MegaCD")

    assert names(roms) == ["Snatcher (USA)"]
    assert roms[0].is_dir


def test_a_disc_nested_one_level_deeper_still_counts():
    """Redump sets often unpack into their own subfolder."""
    provider = build({
        "/g/Dreamcast": ["dc_boot.bin", "Capcom vs. SNK 2 (Japan)/"],
        "/g/Dreamcast/Capcom vs. SNK 2 (Japan)": ["capcom vs snk 2/"],
        "/g/Dreamcast/Capcom vs. SNK 2 (Japan)/capcom vs snk 2": [
            "Capcom vs. SNK 2 (Japan).gdi", "track01.bin",
        ],
    })

    assert names(list_installed_games(provider, "/g/Dreamcast", "Dreamcast")) \
        == ["Capcom vs. SNK 2 (Japan)"]


def test_a_cartridge_cores_subfolder_is_a_shelf_not_a_game():
    """NeoGeo romsets get filed by genre; the genre is not a game."""
    provider = build({
        "/g/NEOGEO": ["000-lo.lo", "Fighting/", "Puzzle/"],
        "/g/NEOGEO/Fighting": ["Art of Fighting.neo", "Breakers.neo"],
        "/g/NEOGEO/Puzzle": ["Puzzle Bobble.neo"],
    })

    roms = list_installed_games(provider, "/g/NEOGEO", "NEOGEO")

    assert names(roms) == ["Art of Fighting", "Breakers", "Puzzle Bobble"]
    assert not any(rom.is_dir for rom in roms)


def test_an_unknown_core_falls_back_to_the_global_rom_set():
    """A folder we have no extension list for must not hide its games."""
    provider = build({"/g/Apogee": ["Game.rkr", "Sonic.md", "boot.rom"]})

    assert names(list_installed_games(provider, "/g/Apogee", "Apogee")) \
        == ["Sonic"]
