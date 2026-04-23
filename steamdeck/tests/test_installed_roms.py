import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
STEAMDECK_ROOT = ROOT / "steamdeck"
if str(STEAMDECK_ROOT) not in sys.path:
    sys.path.insert(0, str(STEAMDECK_ROOT))

from scanner.installed_roms import (  # noqa: E402
    DeleteResult,
    delete_installed,
    scan_installed,
    would_remove_whole_folder,
)


def _mk_emu(tmp_path: Path) -> Path:
    emu = tmp_path / "Emulation"
    (emu / "roms").mkdir(parents=True)
    return emu


def test_scan_finds_cart_roms(tmp_path):
    emu = _mk_emu(tmp_path)
    (emu / "roms" / "gba").mkdir()
    (emu / "roms" / "gba" / "Pokemon Emerald (USA).gba").write_bytes(b"x" * 100)
    (emu / "roms" / "snes").mkdir()
    (emu / "roms" / "snes" / "Chrono Trigger.sfc").write_bytes(b"x" * 200)

    roms = scan_installed(str(emu))

    systems = sorted(r.system for r in roms)
    assert systems == ["GBA", "SNES"]
    gba = next(r for r in roms if r.system == "GBA")
    assert gba.display_name == "Pokemon Emerald (USA)"
    assert gba.size == 100
    assert gba.companion_files == []


def test_scan_recognizes_3ds_cci_and_cia_roms(tmp_path):
    emu = _mk_emu(tmp_path)
    (emu / "roms" / "3ds").mkdir()
    (emu / "roms" / "3ds" / "Super Mario 3D Land (USA).cci").write_bytes(b"x" * 100)
    (emu / "roms" / "3ds" / "Animal Crossing - New Leaf (USA).cia").write_bytes(b"y" * 120)

    roms = scan_installed(str(emu))

    assert [r.system for r in roms] == ["3DS", "3DS"]
    assert [r.display_name for r in roms] == [
        "Animal Crossing - New Leaf (USA)",
        "Super Mario 3D Land (USA)",
    ]


def test_scan_groups_cue_and_bin_pair(tmp_path):
    emu = _mk_emu(tmp_path)
    (emu / "roms" / "psx").mkdir()
    cue = emu / "roms" / "psx" / "Final Fantasy VII (USA).cue"
    cue.write_text('FILE "Final Fantasy VII (USA).bin" BINARY\n')
    bin_file = emu / "roms" / "psx" / "Final Fantasy VII (USA).bin"
    bin_file.write_bytes(b"x" * 5000)

    roms = scan_installed(str(emu))

    assert len(roms) == 1
    rom = roms[0]
    # .cue wins over .bin as the primary because it has a disc-format priority
    assert rom.path == cue
    assert rom.companion_files == [bin_file]
    assert rom.size == 5000 + len(cue.read_text())


def test_delete_installed_removes_primary_and_companions_from_system_root(tmp_path):
    """When cue + bin live directly in the system folder (not a
    dedicated subfolder) we delete the files individually and leave
    the system folder intact."""
    emu = _mk_emu(tmp_path)
    (emu / "roms" / "psx").mkdir()
    cue = emu / "roms" / "psx" / "Wild Arms.cue"
    cue.write_text('FILE "Wild Arms.bin" BINARY\n')
    bin_file = emu / "roms" / "psx" / "Wild Arms.bin"
    bin_file.write_bytes(b"x" * 2000)

    roms = scan_installed(str(emu))
    assert len(roms) == 1
    assert not would_remove_whole_folder(roms[0])

    result = delete_installed(roms[0])

    assert isinstance(result, DeleteResult)
    assert result.deleted_count == 2
    assert result.errors == []
    assert result.removed_dir is None
    assert not cue.exists()
    assert not bin_file.exists()
    # System folder stays — users expect `psx/` to persist even if
    # empty so the next download lands in a familiar place.
    assert (emu / "roms" / "psx").is_dir()


def test_delete_installed_removes_dedicated_subfolder(tmp_path):
    """Cue/bin set in its own per-game subfolder ⇒ rmtree the folder."""
    emu = _mk_emu(tmp_path)
    game_dir = emu / "roms" / "psx" / "Final Fantasy VII"
    game_dir.mkdir(parents=True)
    cue = game_dir / "FF7.cue"
    cue.write_text(
        'FILE "FF7 (Track 01).bin" BINARY\nFILE "FF7 (Track 02).bin" BINARY\n'
    )
    (game_dir / "FF7 (Track 01).bin").write_bytes(b"x" * 1000)
    (game_dir / "FF7 (Track 02).bin").write_bytes(b"x" * 500)

    roms = scan_installed(str(emu))
    assert len(roms) == 1
    # Grouping is by (parent, stem) so the cue-referenced track files
    # don't share a stem with the cue — only the primary is picked up.
    # The delete helper should still collapse the whole folder because
    # every file in it gets removed.
    assert would_remove_whole_folder(roms[0])

    result = delete_installed(roms[0])

    assert result.removed_dir == game_dir
    assert result.deleted_count == 3  # cue + 2 bin tracks
    assert result.errors == []
    assert not game_dir.exists()
    # Parent (psx/) is untouched
    assert (emu / "roms" / "psx").is_dir()


def test_delete_installed_preserves_folder_when_shared_with_another_game(tmp_path):
    """If a folder holds multiple games, never rmtree it."""
    emu = _mk_emu(tmp_path)
    shared = emu / "roms" / "psx" / "Discs"
    shared.mkdir(parents=True)
    a_cue = shared / "Game A.cue"
    a_cue.write_text('FILE "Game A.bin" BINARY\n')
    a_bin = shared / "Game A.bin"
    a_bin.write_bytes(b"x" * 100)
    b_cue = shared / "Game B.cue"
    b_cue.write_text('FILE "Game B.bin" BINARY\n')
    b_bin = shared / "Game B.bin"
    b_bin.write_bytes(b"x" * 100)

    roms = scan_installed(str(emu))
    assert len(roms) == 2
    rom_a = next(r for r in roms if r.display_name == "Game A")
    assert not would_remove_whole_folder(rom_a)

    result = delete_installed(rom_a)

    assert result.removed_dir is None
    assert result.deleted_count == 2  # Game A cue + bin
    assert not a_cue.exists()
    assert not a_bin.exists()
    # Game B files are untouched
    assert b_cue.exists()
    assert b_bin.exists()
    assert shared.is_dir()


def test_delete_installed_never_removes_system_root(tmp_path):
    """Even if the system folder holds only one game, don't rmtree it."""
    emu = _mk_emu(tmp_path)
    (emu / "roms" / "gba").mkdir()
    rom = emu / "roms" / "gba" / "Kirby.gba"
    rom.write_bytes(b"x" * 50)

    roms = scan_installed(str(emu))
    assert not would_remove_whole_folder(roms[0])

    result = delete_installed(roms[0])

    assert result.removed_dir is None
    assert not rom.exists()
    assert (emu / "roms" / "gba").is_dir()


def test_delete_whole_folder_cleans_non_rom_companions_too(tmp_path):
    """A dedicated per-game subfolder full of readmes / box art / save
    directories gets removed wholesale — the point of the subfolder
    delete is to leave *nothing* behind."""
    emu = _mk_emu(tmp_path)
    game_dir = emu / "roms" / "dreamcast" / "Shenmue"
    game_dir.mkdir(parents=True)
    (game_dir / "Shenmue.gdi").write_text("1\n1 0 4 2352 Shenmue.bin\n")
    (game_dir / "Shenmue.bin").write_bytes(b"x" * 500)
    (game_dir / "readme.txt").write_text("Don't forget to insert disc 2")
    # A nested metadata folder too
    meta = game_dir / ".thumbs"
    meta.mkdir()
    (meta / "cover.jpg").write_bytes(b"\xff\xd8\xff\xe0")

    roms = scan_installed(str(emu))
    assert len(roms) == 1
    assert would_remove_whole_folder(roms[0])

    result = delete_installed(roms[0])

    assert result.removed_dir == game_dir
    # All four files — gdi, bin, readme, cover — go in one rmtree call.
    assert result.deleted_count == 4
    assert not game_dir.exists()


def test_scan_honors_rom_scan_dir(tmp_path):
    scan_root = tmp_path / "External"
    (scan_root / "gba").mkdir(parents=True)
    (scan_root / "gba" / "Metroid Fusion.gba").write_bytes(b"x" * 50)

    roms = scan_installed(emulation_path=None, rom_scan_dir=str(scan_root))

    assert len(roms) == 1
    assert roms[0].system == "GBA"
    assert roms[0].display_name == "Metroid Fusion"


def test_scan_dedupes_when_rom_scan_dir_is_same_as_emulation_roms(tmp_path):
    emu = _mk_emu(tmp_path)
    (emu / "roms" / "gba").mkdir()
    (emu / "roms" / "gba" / "Kirby.gba").write_bytes(b"x" * 10)

    roms = scan_installed(
        emulation_path=str(emu),
        rom_scan_dir=str(emu / "roms"),
    )

    # Same directory via two different config keys — should yield one row
    assert len(roms) == 1


def test_scan_walks_nested_folders(tmp_path):
    emu = _mk_emu(tmp_path)
    nested = emu / "roms" / "psx" / "USA"
    nested.mkdir(parents=True)
    (nested / "Crash Bandicoot.chd").write_bytes(b"x" * 100)

    roms = scan_installed(str(emu))

    assert len(roms) == 1
    assert roms[0].system == "PS1"
    assert roms[0].filename == "Crash Bandicoot.chd"


def test_scan_ignores_non_rom_files(tmp_path):
    emu = _mk_emu(tmp_path)
    (emu / "roms" / "gba").mkdir()
    (emu / "roms" / "gba" / "Readme.txt").write_text("hi")
    (emu / "roms" / "gba" / "Boxart.jpg").write_bytes(b"x")
    (emu / "roms" / "gba" / "Real.gba").write_bytes(b"x" * 10)

    roms = scan_installed(str(emu))

    assert len(roms) == 1
    assert roms[0].filename == "Real.gba"


def test_scan_gdi_groups_tracks(tmp_path):
    emu = _mk_emu(tmp_path)
    (emu / "roms" / "dreamcast").mkdir()
    gdi = emu / "roms" / "dreamcast" / "Shenmue.gdi"
    gdi.write_text(
        '2\n1 0 4 2352 "Shenmue (Track 01).bin" 0\n'
        '2 600 4 2352 "Shenmue (Track 02).bin" 0\n'
    )
    t1 = emu / "roms" / "dreamcast" / "Shenmue (Track 01).bin"
    t1.write_bytes(b"x" * 1000)
    t2 = emu / "roms" / "dreamcast" / "Shenmue (Track 02).bin"
    t2.write_bytes(b"x" * 2000)

    roms = scan_installed(str(emu))

    assert len(roms) == 1
    assert roms[0].path == gdi  # .gdi owns the group via sheet parsing
    assert set(roms[0].companion_files) == {t1, t2}


def test_scan_returns_sorted_by_system_then_name(tmp_path):
    emu = _mk_emu(tmp_path)
    (emu / "roms" / "snes").mkdir()
    (emu / "roms" / "snes" / "Zelda.sfc").write_bytes(b"x")
    (emu / "roms" / "gba").mkdir()
    (emu / "roms" / "gba" / "Mario.gba").write_bytes(b"x")
    (emu / "roms" / "gba" / "Advance Wars.gba").write_bytes(b"x")

    roms = scan_installed(str(emu))
    names = [(r.system, r.display_name) for r in roms]

    assert names == [
        ("GBA", "Advance Wars"),
        ("GBA", "Mario"),
        ("SNES", "Zelda"),
    ]
