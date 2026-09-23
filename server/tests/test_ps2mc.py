"""Round-trip tests for the ps2mc filesystem builder/parser."""

import os

import pytest

from app.services import ps2mc
from app.services.ps2_cards import add_ecc, strip_ecc, detect_ps2_card_format


def test_format_empty_card_is_valid_mc2():
    card = ps2mc.format_empty_card()
    assert len(card) == ps2mc.CARD_SIZE
    sb = ps2mc.parse_superblock(card)
    assert sb.clusters_per_card == ps2mc.CLUSTERS_PER_CARD
    assert sb.alloc_offset == ps2mc.ALLOC_OFFSET
    assert sb.ifc_list[0] == ps2mc.IFC_CLUSTER
    # An empty card parses to zero games.
    assert ps2mc.parse_card(card) == {}


def test_single_game_round_trip():
    games = {
        "BASLUS-20312": [
            ("icon.sys", b"\x01" * 964),
            ("BASLUS-20312.sav", os.urandom(8200)),
        ]
    }
    card = ps2mc.build_card(games, timestamp=1_700_000_000)
    parsed = ps2mc.parse_card(card)
    assert set(parsed) == {"BASLUS-20312"}
    assert dict(parsed["BASLUS-20312"]) == dict(games["BASLUS-20312"])


def test_multi_game_round_trip():
    games = {
        "BASLUS-20312": [("a.bin", os.urandom(100))],
        "BESLES-50490": [("save0", os.urandom(2048)), ("save1", os.urandom(1))],
        "BISLPM-65432": [("only", os.urandom(1024 * 5))],
    }
    parsed = ps2mc.parse_card(ps2mc.build_card(games))
    assert set(parsed) == set(games)
    for name, files in games.items():
        assert dict(parsed[name]) == dict(files)


def test_empty_file_round_trips():
    games = {"BASLUS-20312": [("empty", b""), ("nonempty", b"hi")]}
    parsed = ps2mc.parse_card(ps2mc.build_card(games))
    assert dict(parsed["BASLUS-20312"]) == {"empty": b"", "nonempty": b"hi"}


def test_built_card_survives_ecc_round_trip():
    """Card -> .ps2 (add ECC) -> back to .mc2 must be byte-identical and parse."""
    games = {"BASLUS-20312": [("save", os.urandom(4096))]}
    card = ps2mc.build_card(games)
    assert detect_ps2_card_format(card) == "mc2"
    ps2_image = add_ecc(card)
    assert detect_ps2_card_format(ps2_image) == "ps2"
    assert strip_ecc(ps2_image) == card
    assert dict(ps2mc.parse_card(strip_ecc(ps2_image))["BASLUS-20312"]) == \
        dict(games["BASLUS-20312"])


@pytest.mark.parametrize(
    "dirname,expected",
    [
        ("BASLUS-20312", "SLUS20312"),
        ("BISLPM-65432", "SLPM65432"),
        ("BESLES-50490", "SLES50490"),
        ("SCES_503.10", "SCES50310"),
        ("BWNTM2", None),
        ("BADATA-SYSTEM", None),
    ],
)
def test_serial_from_dirname(dirname, expected):
    assert ps2mc.serial_from_dirname(dirname) == expected
