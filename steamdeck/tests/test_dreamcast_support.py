"""Dreamcast saves are keyed by disc serial, not by ROM filename.

A Flycast save on the Deck has to land on the same ``DC_<serial>`` key a
MemCard PRO DC or openMenu card produces, so the scanner resolves the serial
from the disc (IP.BIN) or the bundled DAT before falling back to a name slug.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STEAMDECK_ROOT = ROOT / "steamdeck"
if str(STEAMDECK_ROOT) not in sys.path:
    sys.path.insert(0, str(STEAMDECK_ROOT))

from scanner import retroarch  # noqa: E402
from shared.rom_id.dreamcast import IP_BIN_SIZE  # noqa: E402


def _ip_bin(product: str) -> bytes:
    data = bytearray(b" " * IP_BIN_SIZE)

    def put(offset: int, text: str, length: int) -> None:
        raw = text.encode("ascii")[:length]
        data[offset : offset + len(raw)] = raw

    put(0x00, "SEGA SEGAKATANA ", 16)
    put(0x38, "E000001", 7)
    put(0x40, product, 10)
    put(0x80, "TEST GAME", 128)
    return bytes(data)


def test_title_id_comes_from_the_disc_header(tmp_path):
    image = tmp_path / "Weirdly Renamed File.iso"
    image.write_bytes(_ip_bin("T-1249M"))
    assert (
        retroarch._title_id_for("DC", image, image.stem) == "DC_T1249M"
    ), "a renamed ROM must still key by its disc serial"


def test_sega_serials_fold_to_the_shared_canonical_form(tmp_path):
    image = tmp_path / "Sonic Adventure (USA).iso"
    image.write_bytes(_ip_bin("MK-51000"))
    # The DAT spells this one "51000"; both must produce DC_51000.
    assert retroarch._title_id_for("DC", image, image.stem) == "DC_51000"


def test_chd_falls_back_to_the_dat(tmp_path):
    # CHDs are compressed, so the header can't be read inline — the bundled
    # Dreamcast DAT names them instead.
    chd = tmp_path / "Dead or Alive 2 (USA).chd"
    chd.write_bytes(b"MComprHD" + b"\x00" * 1024)
    assert retroarch._title_id_for("DC", chd, chd.stem) == "DC_T3601N"


def test_an_unidentifiable_disc_keeps_a_slug_id(tmp_path):
    chd = tmp_path / "Some Homebrew Demo.chd"
    chd.write_bytes(b"MComprHD" + b"\x00" * 1024)
    assert retroarch._title_id_for("DC", chd, chd.stem) == "DC_some_homebrew_demo"


def test_other_systems_are_untouched(tmp_path):
    rom = tmp_path / "Advance Wars (USA).gba"
    rom.write_bytes(b"\x00" * 32)
    assert retroarch._title_id_for("GBA", rom, rom.stem) == "GBA_advance_wars_usa"


def test_saturn_still_resolves_by_product_code(tmp_path):
    # The shared Saturn path must keep working alongside the new DC branch.
    assert retroarch._title_id_for(
        "SAT", None, "Panzer Dragoon (USA)"
    ).startswith("SAT_")
