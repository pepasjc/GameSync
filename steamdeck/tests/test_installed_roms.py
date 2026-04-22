import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
STEAMDECK_ROOT = ROOT / "steamdeck"
if str(STEAMDECK_ROOT) not in sys.path:
    sys.path.insert(0, str(STEAMDECK_ROOT))

from scanner.installed_roms import (  # noqa: E402
    delete_installed,
    scan_installed,
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


def test_delete_installed_removes_primary_and_companions(tmp_path):
    emu = _mk_emu(tmp_path)
    (emu / "roms" / "psx").mkdir()
    cue = emu / "roms" / "psx" / "Wild Arms.cue"
    cue.write_text('FILE "Wild Arms.bin" BINARY\n')
    bin_file = emu / "roms" / "psx" / "Wild Arms.bin"
    bin_file.write_bytes(b"x" * 2000)

    roms = scan_installed(str(emu))
    assert len(roms) == 1
    deleted, errors = delete_installed(roms[0])

    assert deleted == 2
    assert errors == []
    assert not cue.exists()
    assert not bin_file.exists()


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
    gdi.write_text("3\n1 0 4 2352 track01.bin\n2 600 0 2352 track02.bin\n")
    t1 = emu / "roms" / "dreamcast" / "Shenmue.bin"
    t1.write_bytes(b"x" * 1000)

    roms = scan_installed(str(emu))

    assert len(roms) == 1
    assert roms[0].path == gdi  # .gdi wins over .bin
    assert t1 in roms[0].companion_files


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
