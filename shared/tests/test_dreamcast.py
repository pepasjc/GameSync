"""Dreamcast disc identification: IP.BIN header, DAT lookup, resolver order.

The Kotlin port of this logic lives in
``android/app/src/main/kotlin/com/savesync/android/emulators/DreamcastSerialDatabase.kt``
and is covered by ``DreamcastSerialTest`` — keep the two in step.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from shared.rom_id.dreamcast import (
    IP_BIN_SIZE,
    canonical_dc_serial,
    lookup_dreamcast_serial_in_dat,
    parse_dreamcast_dat,
    parse_ip_bin,
    read_folder_ip_bin,
    read_ip_bin,
    resolve_dreamcast_title_id,
)


def make_ip_bin(
    product: str = "MK-51035",
    name: str = "CRAZY TAXI",
    version: str = "V1.005",
    date: str = "19991223",
    region: str = "JUE",
    vga: bool = True,
    disc_no: str = "1",
    disc_total: str = "1",
) -> bytes:
    data = bytearray(b" " * IP_BIN_SIZE)

    def put(offset: int, text: str, length: int) -> None:
        raw = text.encode("ascii")[:length]
        data[offset : offset + len(raw)] = raw

    put(0x00, "SEGA SEGAKATANA ", 16)
    put(0x10, "SEGA ENTERPRISES", 16)
    put(0x20, "1234", 4)
    put(0x25, "GD-ROM", 6)
    put(0x2B, disc_no, 1)
    put(0x2C, "/", 1)
    put(0x2D, disc_total, 1)
    put(0x30, region, 8)
    peripherals = list("E000001")
    peripherals[5] = "1" if vga else "0"
    put(0x38, "".join(peripherals), 7)
    put(0x40, product, 10)
    put(0x4A, version, 6)
    put(0x50, date, 8)
    put(0x80, name, 128)
    return bytes(data)


# ──────────────────────────────────────────────────────────────────────
# Header parsing
# ──────────────────────────────────────────────────────────────────────
def test_parse_ip_bin_reads_the_disc_fields():
    ip = parse_ip_bin(make_ip_bin())
    assert ip is not None
    assert ip.product == "MK-51035"
    assert ip.name == "CRAZY TAXI"
    assert ip.disc == "1/1"
    assert ip.vga is True
    assert ip.region == "JUE"
    assert ip.version == "V1.005"
    assert ip.date == "19991223"


def test_header_yields_the_canonical_id_and_the_device_folder_name():
    ip = parse_ip_bin(make_ip_bin(product="MK-51000"))
    # The id folds Sega's prefix away...
    assert ip.serial == "51000"
    assert ip.title_id == "DC_51000"
    # ...but the card names its folder after IP.BIN verbatim.
    assert ip.game_id == "MK51000"


def test_parse_ip_bin_rejects_a_non_dreamcast_header():
    assert parse_ip_bin(b"SEGA SEGASATURN " + b"\x00" * 300) is None
    assert parse_ip_bin(b"too short") is None


def test_multi_disc_and_no_vga_are_read():
    ip = parse_ip_bin(make_ip_bin(disc_no="2", disc_total="4", vga=False, region="J"))
    assert ip.disc == "2/4"
    assert ip.vga is False
    assert ip.region == "J"


def test_blank_disc_field_reads_as_single_disc():
    assert parse_ip_bin(make_ip_bin(disc_no=" ", disc_total=" ")).disc == "1/1"


# ──────────────────────────────────────────────────────────────────────
# Reading from images
# ──────────────────────────────────────────────────────────────────────
def test_reads_a_2048_byte_sector_image(tmp_path):
    image = tmp_path / "game.iso"
    image.write_bytes(make_ip_bin(product="T-1249M"))
    assert read_ip_bin(image).product == "T-1249M"


def test_reads_a_raw_2352_byte_sector_track(tmp_path):
    # Raw sectors put the payload after a 16-byte sync + header.
    track = tmp_path / "track03.bin"
    track.write_bytes(b"\x00" * 16 + make_ip_bin(product="T-3601N"))
    assert read_ip_bin(track).product == "T-3601N"


def test_follows_a_gdi_sheet_to_its_data_track(tmp_path):
    (tmp_path / "track01.bin").write_bytes(b"\x00" * 4096)
    (tmp_path / "track03.bin").write_bytes(make_ip_bin(product="T-8106N"))
    gdi = tmp_path / "disc.gdi"
    gdi.write_text(
        "2\n1 0 4 2352 track01.bin 0\n3 45000 4 2352 track03.bin 0\n",
        encoding="utf-8",
    )
    assert read_ip_bin(gdi).product == "T-8106N"


def test_handles_quoted_track_names_in_a_gdi_sheet(tmp_path):
    (tmp_path / "Some Game (USA)03.bin").write_bytes(make_ip_bin(product="T-9999N"))
    gdi = tmp_path / "Some Game (USA).gdi"
    gdi.write_text(
        '1\n3 45000 4 2352 "Some Game (USA)03.bin" 0\n', encoding="utf-8"
    )
    assert read_ip_bin(gdi).product == "T-9999N"


def test_a_compressed_chd_cannot_be_read_inline(tmp_path):
    chd = tmp_path / "game.chd"
    chd.write_bytes(b"MComprHD" + b"\x00" * 8192)
    assert read_ip_bin(chd) is None


def test_read_folder_ip_bin_prefers_the_gdi(tmp_path):
    (tmp_path / "track03.bin").write_bytes(make_ip_bin(product="T-1111M"))
    (tmp_path / "disc.gdi").write_text(
        "1\n3 45000 4 2352 track03.bin 0\n", encoding="utf-8"
    )
    assert read_folder_ip_bin(tmp_path).product == "T-1111M"


# ──────────────────────────────────────────────────────────────────────
# DAT lookup
# ──────────────────────────────────────────────────────────────────────
DAT_TEXT = """clrmamepro (
\tname "Sega - Dreamcast"
)

game (
\tname "Shenmue (USA) (Disc 1)"
\tregion "USA"
\tserial "51059"
\trom ( name "Shenmue (USA) (Disc 1) (Track 3).bin" size 1185760800 serial "51059" )
)
game (
\tname "Dead or Alive 2 (USA)"
\tregion "USA"
\tserial "T-3601N"
\trom ( name "Dead or Alive 2 (USA) (Track 3).bin" size 1185760800 serial "T-3601N" )
)
"""


def test_dat_parsing_takes_the_game_level_serial():
    parsed = parse_dreamcast_dat(DAT_TEXT)
    assert parsed == {
        "shenmue (usa) (disc 1)": "51059",
        "dead or alive 2 (usa)": "T-3601N",
    }


def test_dat_lookup_strips_bracket_tags_and_trailing_groups():
    parsed = parse_dreamcast_dat(DAT_TEXT)
    assert lookup_dreamcast_serial_in_dat("Dead or Alive 2 (USA)", parsed) == "T-3601N"
    assert (
        lookup_dreamcast_serial_in_dat("Dead or Alive 2 (USA) [T-En v1.0]", parsed)
        == "T-3601N"
    )
    assert (
        lookup_dreamcast_serial_in_dat("Shenmue (USA) (Disc 1) (Rev A)", parsed)
        == "51059"
    )
    assert lookup_dreamcast_serial_in_dat("Nothing Here (World)", parsed) is None


# ──────────────────────────────────────────────────────────────────────
# The resolver every client calls
# ──────────────────────────────────────────────────────────────────────
def test_resolver_prefers_the_disc_over_the_dat(tmp_path):
    dat = tmp_path / "Sega - Dreamcast.dat"
    dat.write_text(DAT_TEXT, encoding="utf-8")
    image = tmp_path / "Dead or Alive 2 (USA).iso"
    # The file says one thing, the disc says another: the disc wins.
    image.write_bytes(make_ip_bin(product="T-1249M"))
    assert (
        resolve_dreamcast_title_id(rom_path=image, dat_path=dat) == "DC_T1249M"
    )


def test_resolver_falls_back_to_the_dat_for_a_chd(tmp_path):
    dat = tmp_path / "Sega - Dreamcast.dat"
    dat.write_text(DAT_TEXT, encoding="utf-8")
    chd = tmp_path / "Dead or Alive 2 (USA).chd"
    chd.write_bytes(b"MComprHD" + b"\x00" * 1024)
    assert resolve_dreamcast_title_id(rom_path=chd, dat_path=dat) == "DC_T3601N"


def test_resolver_takes_a_bare_name(tmp_path):
    dat = tmp_path / "Sega - Dreamcast.dat"
    dat.write_text(DAT_TEXT, encoding="utf-8")
    assert (
        resolve_dreamcast_title_id(rom_name="Shenmue (USA) (Disc 1)", dat_path=dat)
        == "DC_51059"
    )


def test_resolver_returns_none_when_nothing_identifies_the_disc(tmp_path):
    dat = tmp_path / "Sega - Dreamcast.dat"
    dat.write_text(DAT_TEXT, encoding="utf-8")
    assert resolve_dreamcast_title_id(rom_name="Homebrew Demo", dat_path=dat) is None
    assert resolve_dreamcast_title_id() is None


def test_the_bundled_dat_resolves_a_real_game():
    from shared.rom_id.dreamcast import load_dreamcast_dat

    if not load_dreamcast_dat():
        pytest.skip("Sega - Dreamcast.dat not available")
    assert resolve_dreamcast_title_id(rom_name="Sonic Adventure (USA)") == "DC_51000"


def test_canonical_serial_matches_the_kotlin_port():
    # Mirrors DreamcastSerialTest in the Android app.
    assert canonical_dc_serial("MK-51000") == "51000"
    assert canonical_dc_serial("51000") == "51000"
    assert canonical_dc_serial("MK-51064-50") == "5106450"
    assert canonical_dc_serial("t-3601n") == "T3601N"
    assert canonical_dc_serial("HDR-0080") == "HDR0080"
