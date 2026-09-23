"""Coverage for the Nintendo - Wii U DAT.

Three things go wrong if the Wii U DAT is treated like the Wii one:

  1. dat_normalizer's filename map matches on substrings, so "Nintendo -
     Wii U.dat" hit the ("nintendo - wii", "WII") entry and merged 2871 Wii U
     titles into the Wii ROM-rename index.
  2. game_names' loader has the same prefix trap, and would have parsed bare
     Wii U product codes (AMKE) as RVL-XXXX-RGN, dropping them all.
  3. The gametdb Wii U DAT writes multi-line ``rom (`` blocks, whose inner
     ``name`` line overwrote the game name — every entry came out as
     "Foo (USA).wux".
"""

from pathlib import Path

import pytest

from app.services import game_names
from app.services.dat_normalizer import _DAT_SYSTEM_MAP

DATS = Path(__file__).resolve().parents[1] / "data" / "dats"
WIIU_DAT = DATS / "Nintendo - Wii U.dat"
WII_DAT = DATS / "Nintendo - Wii.dat"


def _system_for(filename: str) -> str | None:
    lower = filename.lower()
    for keyword, code in _DAT_SYSTEM_MAP:
        if keyword in lower:
            return code
    return None


class TestDatSystemMapping:
    def test_wii_u_dat_is_not_classified_as_wii(self):
        assert _system_for("Nintendo - Wii U.dat") == "WIIU"

    def test_plain_wii_dat_still_maps_to_wii(self):
        assert _system_for("Nintendo - Wii.dat") == "WII"
        assert _system_for("Nintendo - Wii (Digital).dat") == "WII"

    def test_wiiu_is_a_known_system_code(self):
        from shared.systems import ALL_CONSOLE_TYPES, SYSTEM_CHOICES, SYSTEM_CODES

        assert "WIIU" in SYSTEM_CODES
        assert "WIIU" in SYSTEM_CHOICES
        assert "WIIU" in ALL_CONSOLE_TYPES


@pytest.mark.skipif(not WIIU_DAT.is_file(), reason="Wii U DAT not present")
class TestWiiUNames:
    @pytest.fixture(autouse=True)
    def _load(self):
        game_names.load_libretro_dat_to_dicts(WII_DAT)
        game_names.load_libretro_dat_to_dicts(WIIU_DAT)

    def test_wiiu_codes_land_in_their_own_dict(self):
        assert game_names._wiiu_names, "Wii U DAT produced no entries"
        # Wii U product codes must NOT pollute the shared GC/Wii table —
        # a collision there would rename a GameCube save's game.
        assert "ADRP" not in game_names._wii_names

    def test_multi_line_rom_block_does_not_leak_into_the_name(self):
        name = game_names._wiiu_names.get("AMKE", "")
        assert name, "Mario Kart 8 missing from the Wii U DAT"
        assert not name.endswith((".wux", ".wud", ".iso"))

    def test_lookup_resolves_wiiu_rom_ids(self):
        got = game_names.lookup_names_typed(["WIIU_AMKE"])
        assert got["WIIU_AMKE"][1] == "WIIU"
        assert "Mario Kart 8" in got["WIIU_AMKE"][0]

    def test_lookup_resolves_vwii_save_ids(self):
        """WII_<code> is what the Wii U client sends for vWii NAND saves."""
        got = game_names.lookup_names_typed(["WII_RMCE"])
        assert got["WII_RMCE"][1] == "WII"
        assert "Mario Kart Wii" in got["WII_RMCE"][0]

    def test_gc_ids_still_resolve(self):
        game_names.load_libretro_dat_to_dicts(DATS / "Nintendo - GameCube.dat")
        got = game_names.lookup_names_typed(["GC_GALE"])
        assert got["GC_GALE"][1] == "GC"
        assert "Melee" in got["GC_GALE"][0]

    def test_wiiu_save_ids_resolve_through_the_title_id_index_only(self):
        """A Wii U save is a 16-hex title id whose low word is NOT the ASCII
        product code (unlike vWii), so it can only be named by the DAT's
        explicit ``title_id`` lines — never by decoding the id itself."""
        got = game_names.lookup_names_typed(["0005000010101D00"])
        assert got["0005000010101D00"][1] == "WIIU"
        assert "Mario Bros. U" in got["0005000010101D00"][0]

        # An id with no DAT entry stays unresolved rather than being guessed
        # at from its low word (which is not text at all).
        assert game_names.lookup_names_typed(["00050000FFFFFF00"]) == {}


class TestWiiUTitleIdIndex:
    """The Wii U DAT carries title_id lines, so a save names itself.

    A Wii U save is keyed by a 16-hex title id whose low word is not the
    product code, so before these lines existed nothing server-side could name
    one — every client had to supply the name from a console/Cemu meta.xml.
    """

    # Synthetic ids/codes throughout: the loader keeps a module-level index
    # that the shipped DAT also populates, and a real id would resolve to the
    # real entry no matter what this test wrote.
    def _load(self, tmp_path, body: str) -> None:
        dat = tmp_path / "Nintendo - Wii U.dat"
        dat.write_text(body, encoding="utf-8")
        game_names.load_libretro_dat_to_dicts(dat)

    def test_title_id_line_names_a_save(self, tmp_path):
        self._load(
            tmp_path,
            'game (\n\tname "Test Kart 8 (USA)"\n\tserial "ZZKE01"\n'
            '\ttitle_id "00050000FF00EC00"\n)\n',
        )

        typed = game_names.lookup_names_typed(["00050000FF00EC00"])
        assert typed["00050000FF00EC00"] == ("Test Kart 8 (USA)", "WIIU")
        assert game_names.lookup_name_and_platform("00050000FF00EC00") == (
            "Test Kart 8 (USA)",
            "WIIU",
        )

    def test_product_code_lookup_still_works(self, tmp_path):
        self._load(
            tmp_path,
            'game (\n\tname "Test Kart 8 (USA)"\n\tserial "ZZKE01"\n'
            '\ttitle_id "00050000FF00EC00"\n)\n',
        )

        assert game_names.lookup_names_typed(["WIIU_ZZKE"])["WIIU_ZZKE"] == (
            "Test Kart 8 (USA)",
            "WIIU",
        )

    def test_several_title_ids_share_one_entry(self, tmp_path):
        """Revisions/demos hang off the same product code."""
        self._load(
            tmp_path,
            'game (\n\tname "Test Splat (USA)"\n\tserial "ZZME01"\n'
            '\ttitle_id "00050000FF176900"\n\ttitle_id "00050000FF176A00"\n)\n',
        )

        for tid in ("00050000FF176900", "00050000FF176A00"):
            assert game_names.lookup_names_typed([tid])[tid][0] == "Test Splat (USA)"

    def test_title_id_only_block_is_accepted(self, tmp_path):
        """Appended wiiubrew-only titles have no serial the DAT ever knew."""
        self._load(
            tmp_path,
            'game (\n\tname "Test Chat"\n\ttitle_id "00050000FF01800A"\n)\n',
        )

        assert (
            game_names.lookup_names_typed(["00050000FF01800A"])["00050000FF01800A"][0]
            == "Test Chat"
        )

    def test_rom_block_serial_does_not_swallow_the_title_id(self, tmp_path):
        """The id must sit at game level; inside rom ( … ) it is ignored."""
        self._load(
            tmp_path,
            'game (\n\tname "Test Waker (USA)"\n\tserial "ZZZE01"\n'
            '\ttitle_id "00050000FF143500"\n'
            '\trom (\n\t\tname "Test Waker (USA).wux"\n\t\tserial "ZZZE01"\n\t)\n)\n',
        )

        assert (
            game_names.lookup_names_typed(["00050000FF143500"])["00050000FF143500"][0]
            == "Test Waker (USA)"
        )

    def test_shipped_dat_resolves_real_title_ids(self):
        """Guards the enrichment in server/data/dats/Nintendo - Wii U.dat."""
        from pathlib import Path

        dat = Path(__file__).parent.parent / "data" / "dats" / "Nintendo - Wii U.dat"
        game_names.load_libretro_dat_to_dicts(dat)

        typed = game_names.lookup_names_typed(
            ["000500001010EC00", "0005000010143500", "00050000101C9500"]
        )
        assert len(typed) == 3
        assert all(kind == "WIIU" for _name, kind in typed.values())
        assert "Mario Kart 8" in typed["000500001010EC00"][0]
