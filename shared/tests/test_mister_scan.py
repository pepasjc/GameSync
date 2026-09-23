"""Tests for the shared MiSTer directory walking rules.

A fake provider stands in for the filesystem, so these cover the desktop's
SFTP path and the on-device local path at once.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.mister_scan import (  # noqa: E402
    build_save_path,
    find_installed_rom_stem,
    installed_game_save_paths,
    save_folder_for_system,
    scan_saves,
)


class FakeProvider:
    """An in-memory tree: ``{path: [children]}`` for dirs, bytes for files."""

    def __init__(self, tree):
        self.tree = tree

    def listdir(self, path):
        entry = self.tree.get(path.rstrip("/"))
        return list(entry) if isinstance(entry, list) else []

    def is_dir(self, path):
        return isinstance(self.tree.get(path.rstrip("/")), list)

    def stat(self, path):
        data = self.tree.get(path)
        return (len(data) if isinstance(data, bytes) else 0), 1000.0

    def read(self, path):
        data = self.tree.get(path)
        if not isinstance(data, bytes):
            raise OSError("not a file: %s" % path)
        return data


def test_scan_finds_saves_and_skips_unknown_folders():
    provider = FakeProvider({
        "/media/fat/saves": ["SNES", "Paprium", "GBA"],
        "/media/fat/saves/SNES": ["Super Mario World (USA).sav", "notes.txt"],
        "/media/fat/saves/SNES/Super Mario World (USA).sav": b"\x01" * 2048,
        "/media/fat/saves/SNES/notes.txt": b"hello",
        # Paprium is a real folder on a real device that maps to no system.
        "/media/fat/saves/Paprium": ["Paprium.sav"],
        "/media/fat/saves/Paprium/Paprium.sav": b"\x00" * 64,
        "/media/fat/saves/GBA": ["Zelda - Minish Cap (USA).sav"],
        "/media/fat/saves/GBA/Zelda - Minish Cap (USA).sav": b"\x02" * 32768,
    })

    found = scan_saves(provider)
    by_name = {item.filename: item for item in found}

    assert set(by_name) == {"Super Mario World (USA).sav",
                            "Zelda - Minish Cap (USA).sav"}
    assert by_name["Zelda - Minish Cap (USA).sav"].title_id == \
        "GBA_zelda_minish_cap_usa"
    assert by_name["Super Mario World (USA).sav"].system == "SNES"
    assert by_name["Super Mario World (USA).sav"].size == 2048


def test_scan_honours_every_save_extension():
    """The legacy shell script only looked at .sav/.srm/.fs of the twelve."""
    from shared.systems import SAVE_EXTENSIONS

    files = ["game%d%s" % (index, ext)
             for index, ext in enumerate(sorted(SAVE_EXTENSIONS))]
    tree = {"/media/fat/saves": ["SNES"], "/media/fat/saves/SNES": files}
    for name in files:
        tree["/media/fat/saves/SNES/" + name] = b"x"

    assert len(scan_saves(FakeProvider(tree))) == len(SAVE_EXTENSIONS)


def test_existing_legacy_folder_wins_over_the_modern_name():
    """Cores were renamed; an existing folder must not be orphaned."""
    legacy = FakeProvider({"/media/fat/saves": ["Genesis"],
                           "/media/fat/saves/Genesis": []})
    assert save_folder_for_system(legacy, "MD") == "Genesis"

    fresh = FakeProvider({"/media/fat/saves": []})
    assert save_folder_for_system(fresh, "MD") == "MegaDrive"


def _games_provider():
    return FakeProvider({
        "/media/usb0/games": ["PSX", "SNES"],
        # A CD core names its card after the *folder* holding the discs.
        "/media/usb0/games/PSX": ["Breath of Fire IV (USA)"],
        "/media/usb0/games/PSX/Breath of Fire IV (USA)": ["disc1.chd"],
        "/media/usb0/games/PSX/Breath of Fire IV (USA)/disc1.chd": b"x",
        "/media/usb0/games/SNES": ["Super Mario World (USA).sfc"],
        "/media/usb0/games/SNES/Super Mario World (USA).sfc": b"x",
        "/media/fat/games": ["SNES"],
        "/media/fat/games/SNES": ["Super Mario World (USA).sfc"],
        "/media/fat/games/SNES/Super Mario World (USA).sfc": b"x",
        "/media/fat/saves": ["SNES"],
        "/media/fat/saves/SNES": [],
    })


def test_installed_rom_stem_matches_a_file():
    provider = _games_provider()
    stem = find_installed_rom_stem(provider, "SNES",
                                   "SNES_super_mario_world_usa")
    assert stem == "Super Mario World (USA)"


def test_installed_rom_stem_returns_a_cd_folder_name_whole():
    """CD cores name the card after the folder, and game folders routinely
    contain dots ("... v1.021+hotfix"), so the folder name is returned intact
    rather than being split into stem and extension."""
    from shared.rom_id import make_title_id

    folder = "Final Fantasy IX (USA) v1.1"
    provider = FakeProvider({
        "/media/usb0/games": ["PSX"],
        "/media/usb0/games/PSX": [folder],
        "/media/usb0/games/PSX/" + folder: ["d.chd"],
        "/media/usb0/games/PSX/%s/d.chd" % folder: b"x",
        "/media/fat/games": [],
    })
    stem = find_installed_rom_stem(provider, "PS1",
                                   make_title_id("PS1", folder))
    assert stem == folder


def test_usb_is_searched_before_the_sd_card():
    """Cores read /media/usb0 first, so a game there shadows the SD copy."""
    provider = FakeProvider({
        "/media/usb0/games": ["SNES"],
        "/media/usb0/games/SNES": ["Super Mario World (USA).sfc"],
        "/media/usb0/games/SNES/Super Mario World (USA).sfc": b"x",
        "/media/fat/games": ["SNES"],
        "/media/fat/games/SNES": ["Super Mario World (USA).sfc"],
        "/media/fat/games/SNES/Super Mario World (USA).sfc": b"x",
    })
    calls = []
    original = provider.listdir

    def spy(path):
        calls.append(path)
        return original(path)

    provider.listdir = spy
    find_installed_rom_stem(provider, "SNES", "SNES_super_mario_world_usa")
    assert calls[0].startswith("/media/usb0")


def test_download_path_uses_the_installed_name_and_always_dot_sav():
    provider = _games_provider()
    path = build_save_path(provider, "SNES", "SNES_super_mario_world_usa",
                           game_name="Super Mario World")
    # The core looks for a file named after the game it launched.
    assert path == "/media/fat/saves/SNES/Super Mario World (USA).sav"


def test_download_path_falls_back_to_the_server_name():
    provider = FakeProvider({"/media/fat/saves": [], "/media/usb0/games": [],
                             "/media/fat/games": []})
    path = build_save_path(provider, "SNES", "SNES_some_game",
                           game_name="Some Game")
    assert path == "/media/fat/saves/SNES/Some Game.sav"


def test_download_path_sanitises_a_hostile_server_name():
    provider = FakeProvider({"/media/fat/saves": [], "/media/usb0/games": [],
                             "/media/fat/games": []})
    path = build_save_path(provider, "SNES", "SNES_x",
                           game_name="../../etc/passwd")
    assert ".." not in path.replace("/media/fat/saves/SNES/", "")
    assert path.startswith("/media/fat/saves/SNES/")


# ── One save folder serving two systems ─────────────────────────────────────

def _pce_provider(cd_installed=True, hucard_installed=False):
    tree = {
        "/media/fat/saves": ["TGFX16"],
        "/media/fat/saves/TGFX16": ["Akumajou Dracula X (Japan).sav"],
        "/media/fat/saves/TGFX16/Akumajou Dracula X (Japan).sav": b"\x01" * 2048,
        "/media/usb0/games": [],
        "/media/fat/games": [],
    }
    if cd_installed:
        tree["/media/usb0/games"] = ["TGFX16-CD"]
        tree["/media/usb0/games/TGFX16-CD"] = ["Akumajou Dracula X (Japan)"]
        tree["/media/usb0/games/TGFX16-CD/Akumajou Dracula X (Japan)"] = ["d.chd"]
        tree["/media/usb0/games/TGFX16-CD/Akumajou Dracula X (Japan)/d.chd"] = b"x"
    if hucard_installed:
        tree.setdefault("/media/fat/games", [])
        tree["/media/fat/games"] = ["TGFX16"]
        tree["/media/fat/games/TGFX16"] = ["Akumajou Dracula X (Japan).pce"]
        tree["/media/fat/games/TGFX16/Akumajou Dracula X (Japan).pce"] = b"x"
    return FakeProvider(tree)


def test_a_cd_save_in_the_shared_folder_is_recognised_as_pcecd():
    """The core writes HuCard and CD saves both into saves/TGFX16, so the
    installed game is what says which system the save belongs to."""
    found = scan_saves(_pce_provider(cd_installed=True))
    assert len(found) == 1
    assert found[0].system == "PCECD"
    assert found[0].title_id.startswith("PCECD_")


def test_a_hucard_save_in_the_shared_folder_stays_pce():
    found = scan_saves(_pce_provider(cd_installed=False, hucard_installed=True))
    assert len(found) == 1
    assert found[0].system == "PCE"
    assert found[0].title_id.startswith("PCE_")


def test_with_nothing_installed_the_folder_decides_as_before():
    found = scan_saves(_pce_provider(cd_installed=False))
    assert len(found) == 1
    assert found[0].system == "PCE"


def test_a_pcecd_save_is_written_where_the_core_reads_it():
    """games/TGFX16-CD, but saves/TGFX16 - writing it to a folder named after
    the games folder puts it somewhere the core never looks."""
    provider = _pce_provider()
    assert save_folder_for_system(provider, "PCECD") == "TGFX16"

    path = build_save_path(provider, "PCECD", "PCECD_akumajou_dracula_x_japan",
                           game_name="Akumajou Dracula X (Japan)")
    assert path.startswith("/media/fat/saves/TGFX16/")
    assert "/TGFX16-CD/" not in path


def test_other_systems_are_unaffected_by_the_override():
    provider = FakeProvider({"/media/fat/saves": [], "/media/usb0/games": [],
                             "/media/fat/games": []})
    assert save_folder_for_system(provider, "SAT") == "Saturn"
    assert save_folder_for_system(provider, "PS1") == "PSX"
    assert save_folder_for_system(provider, "MD") == "MegaDrive"


def test_a_save_in_a_stale_folder_does_not_duplicate_the_real_one():
    """saves/TGFX16-CD was a plausible guess before the core turned out to
    write everything to saves/TGFX16. Both files exist; only the one the core
    reads should be listed."""
    name = "Akumajou Dracula X (Japan).sav"
    provider = FakeProvider({
        "/media/fat/saves": ["TGFX16", "TGFX16-CD"],
        "/media/fat/saves/TGFX16": [name],
        "/media/fat/saves/TGFX16/" + name: b"\x01" * 2048,
        "/media/fat/saves/TGFX16-CD": [name],
        "/media/fat/saves/TGFX16-CD/" + name: b"\x02" * 2048,
        "/media/usb0/games": ["TGFX16-CD"],
        "/media/usb0/games/TGFX16-CD": ["Akumajou Dracula X (Japan)"],
        "/media/usb0/games/TGFX16-CD/Akumajou Dracula X (Japan)": ["d.chd"],
        "/media/usb0/games/TGFX16-CD/Akumajou Dracula X (Japan)/d.chd": b"x",
        "/media/fat/games": [],
    })

    found = scan_saves(provider)
    assert len(found) == 1
    assert found[0].system == "PCECD"
    assert found[0].folder == "TGFX16"


def test_two_different_games_are_never_deduplicated():
    provider = FakeProvider({
        "/media/fat/saves": ["SNES"],
        "/media/fat/saves/SNES": ["A (USA).sav", "B (USA).sav"],
        "/media/fat/saves/SNES/A (USA).sav": b"a",
        "/media/fat/saves/SNES/B (USA).sav": b"b",
    })
    assert len(scan_saves(provider)) == 2


def test_installed_game_save_paths_finds_the_core_named_card():
    """Uninstalling a variant should be able to take its card along: the
    core names the save after the game file stem or folder, in whichever
    save folder the system has used (legacy names included)."""
    provider = FakeProvider({
        "/media/fat/saves": ["PSX", "Genesis", "MegaDrive"],
        "/media/fat/saves/PSX": ["Snatcher (Japan) [T-En by pepa v0.5].sav",
                                 "Snatcher (Japan).sav"],
        "/media/fat/saves/Genesis": ["Sonic (USA).sav"],
        "/media/fat/saves/MegaDrive": ["Sonic (USA).sav"],
    })

    assert installed_game_save_paths(
        provider, "PS1", "Snatcher (Japan) [T-En by pepa v0.5]") == [
        "/media/fat/saves/PSX/Snatcher (Japan) [T-En by pepa v0.5].sav"]
    # The other variant's card is left alone.
    assert installed_game_save_paths(provider, "PS1", "Snatcher (Japan)") == [
        "/media/fat/saves/PSX/Snatcher (Japan).sav"]
    assert installed_game_save_paths(provider, "PS1", "Not Installed") == []
    # Both the modern and the legacy folder can hold one.
    assert set(installed_game_save_paths(provider, "MD", "Sonic (USA)")) == {
        "/media/fat/saves/Genesis/Sonic (USA).sav",
        "/media/fat/saves/MegaDrive/Sonic (USA).sav"}
