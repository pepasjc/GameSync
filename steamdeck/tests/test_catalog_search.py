import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
STEAMDECK_ROOT = ROOT / "steamdeck"
if str(STEAMDECK_ROOT) not in sys.path:
    sys.path.insert(0, str(STEAMDECK_ROOT))

from scanner.catalog_search import filter_catalog, matches, unique_systems  # noqa: E402
from scanner.rom_target import resolve_rom_target_dir  # noqa: E402


def _rom(title_id, system, name, filename, size=0):
    return {
        "rom_id": f"{system}-{title_id}",
        "title_id": title_id,
        "system": system,
        "name": name,
        "filename": filename,
        "size": size,
    }


CATALOG = [
    _rom("SLUS01324", "PS1", "Breath of Fire IV (USA)", "Breath of Fire IV (USA).chd"),
    _rom("SLUS01041", "PS1", "Final Fantasy VII (USA) (Disc 1)", "Final Fantasy VII (USA) (Disc 1).chd"),
    _rom("SAT_T-4507G", "SAT", "Grandia (Japan) (Disc 1)", "Grandia (Japan) (Disc 1) (4M).chd"),
    _rom("GBA_pokemon_emerald", "GBA", "Pokemon Emerald (USA)", "Pokemon - Emerald Version (USA).gba"),
    _rom("NDS_chrono_trigger", "NDS", "Chrono Trigger (USA)", "Chrono Trigger (USA) (En,Fr).nds"),
]


def test_filter_returns_all_when_query_empty():
    result = filter_catalog(CATALOG)
    assert len(result) == len(CATALOG)
    # Sorted by system then name
    assert result[0]["system"] == "GBA"


def test_filter_respects_system_filter():
    result = filter_catalog(CATALOG, system="PS1")
    assert {r["title_id"] for r in result} == {"SLUS01324", "SLUS01041"}


def test_smart_search_tokens_match_any_order():
    # "fire breath" still matches "Breath of Fire IV" because tokens are
    # checked independently.
    result = filter_catalog(CATALOG, query="fire breath")
    assert len(result) == 1
    assert result[0]["title_id"] == "SLUS01324"


def test_smart_search_roman_numeral_expansion():
    # Typing the arabic digit should match the catalog's roman numeral.
    result = filter_catalog(CATALOG, query="final fantasy 7")
    assert [r["title_id"] for r in result] == ["SLUS01041"]

    # And vice-versa: searching roman should match arabic entries too.
    result = filter_catalog(CATALOG, query="breath of fire iv")
    assert [r["title_id"] for r in result] == ["SLUS01324"]


def test_smart_search_by_arabic_then_roman():
    result = filter_catalog(CATALOG, query="breath of fire 4")
    assert [r["title_id"] for r in result] == ["SLUS01324"]


def test_smart_search_strips_region_tags():
    # "chrono trigger" matches even though the catalog name carries "(USA)"
    result = filter_catalog(CATALOG, query="chrono trigger")
    assert [r["system"] for r in result] == ["NDS"]


def test_smart_search_matches_title_id_fragments():
    # Partial product-code lookup
    result = filter_catalog(CATALOG, query="slus013")
    assert {r["title_id"] for r in result} == {"SLUS01324"}


def test_smart_search_multiple_matches():
    result = filter_catalog(CATALOG, query="usa")
    # All USA rows match — the Japanese Grandia disc doesn't
    systems = {r["system"] for r in result}
    assert "SAT" not in systems
    assert "PS1" in systems and "GBA" in systems


def test_matches_respects_system_filter_even_when_name_matches():
    rom = _rom("SLUS01324", "PS1", "Breath of Fire IV (USA)", "bof4.chd")
    assert matches(rom, "breath", system="PS1")
    assert not matches(rom, "breath", system="GBA")


def test_unique_systems_sorted_and_deduped():
    assert unique_systems(CATALOG) == ["GBA", "NDS", "PS1", "SAT"]


def test_empty_query_with_no_system_shortcuts():
    assert matches(_rom("x", "PS1", "A", "a.bin"), "", system=None)


def test_resolve_rom_target_dir_creates_expected_path(tmp_path):
    roms_base = tmp_path / "roms"
    roms_base.mkdir()
    # Before any subfolder exists the resolver returns the first candidate
    assert resolve_rom_target_dir(roms_base, "PS1") == roms_base / "psx"
    # Once a subfolder exists, it wins (GBA folder pre-created)
    (roms_base / "gba").mkdir()
    assert resolve_rom_target_dir(roms_base, "GBA") == roms_base / "gba"
