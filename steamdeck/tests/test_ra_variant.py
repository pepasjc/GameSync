"""Installed tab: "Replace with RA version" (shared.ra_variants via ra_variant)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for candidate in (ROOT, ROOT / "steamdeck"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from download_manager import DownloadEntity, _delete_replaced  # noqa: E402
from ra_variant import find_variants, replaced_paths  # noqa: E402
from scanner.installed_roms import InstalledRom  # noqa: E402

EUROPE = dict(ra_achievements=88, ra_match="title", ra_game_id=3395, ra_dump=True)
USA = dict(ra_achievements=88, ra_match="title", ra_game_id=3395)


def _row(name, system="DC", **ra):
    row = {"rom_id": name, "system": system, "name": name, "filename": name + ".chd", "size": 1000}
    row.update(ra)
    return row


def _rom(name, system="DC", root=Path("/roms/dc")):
    path = root / (name + ".chd")
    return InstalledRom(path=path, system=system, display_name=name,
                        filename=path.name, size=1, system_root=root)


def test_usa_copy_is_offered_the_europe_version():
    rows = [_row("Soulcalibur (USA)", **USA),
            _row("Soulcalibur (Europe) (En,Fr,De,Es)", **EUROPE)]
    rom = _rom("Soulcalibur (USA)")
    found = find_variants(rows, [rom])
    assert [r["name"] for r in found[str(rom.path)]] == ["Soulcalibur (Europe) (En,Fr,De,Es)"]


def test_nothing_when_the_ra_version_is_installed_too():
    rows = [_row("Soulcalibur (USA)", **USA),
            _row("Soulcalibur (Europe) (En,Fr,De,Es)", **EUROPE)]
    roms = [_rom("Soulcalibur (USA)"), _rom("Soulcalibur (Europe) (En,Fr,De,Es)")]
    assert find_variants(rows, roms) == {}


def test_multi_disc_games_are_left_alone():
    rows = [_row("Skies of Arcadia (USA) (Disc 1)", **USA),
            _row("Skies of Arcadia (Europe) (Disc 1)", **EUROPE)]
    assert find_variants(rows, [_rom("Skies of Arcadia (USA) (Disc 1)")]) == {}


def test_replaced_paths_prefers_the_game_folder():
    rom = _rom("Game (USA)")
    rom.companion_files = [Path("/roms/dc/Game (USA) (Track 2).bin")]
    assert replaced_paths(rom, None) == [str(rom.path), str(rom.companion_files[0])]
    assert replaced_paths(rom, Path("/roms/dc/Game")) == [str(Path("/roms/dc/Game"))]


def _entity(final, replaces):
    return DownloadEntity(
        id="x", rom_id="r", system="DC", display_name="New", filename="n",
        part_file_path=str(final) + ".part", final_file_path=str(final),
        total_bytes=0, downloaded_bytes=0, status="completed", error_message="",
        extract_format="", created_at=0, updated_at=0, replaces_paths=[str(p) for p in replaces],
    )


def test_completed_replacement_deletes_the_old_copy_only(tmp_path):
    old_dir = tmp_path / "Old Game"
    old_dir.mkdir()
    (old_dir / "disc.gdi").write_text("x")
    old_file = tmp_path / "Old (USA).chd"
    old_file.write_text("x")
    new = tmp_path / "New (Europe).chd"
    new.write_text("new")

    assert _delete_replaced(_entity(new, [old_dir, old_file, new, tmp_path])) == []
    assert not old_dir.exists() and not old_file.exists()
    # Never the new download, nor a folder holding it.
    assert new.exists() and tmp_path.exists()
