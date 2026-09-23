"""Round-trip tests for the ps1mc filesystem builder/parser."""

import os
import struct

import pytest

from app.services import ps1mc


def _place_single_block_save(card: bytearray, block: int, name: str, data: bytes):
    """Write one single-block save into ``block`` (1..15) of a formatted card."""
    assert len(data) <= ps1mc.BLOCK_SIZE
    padded = data + b"\x00" * (ps1mc.BLOCK_SIZE - len(data))
    card[block * ps1mc.BLOCK_SIZE : (block + 1) * ps1mc.BLOCK_SIZE] = padded
    fr = bytearray(ps1mc.FRAME_SIZE)
    fr[0] = ps1mc.ST_FIRST
    struct.pack_into("<I", fr, 0x04, ps1mc.BLOCK_SIZE)
    struct.pack_into("<H", fr, 0x08, ps1mc.NO_NEXT)
    nm = name.encode("ascii")
    fr[0x0A : 0x0A + len(nm)] = nm
    fr[0x7F] = ps1mc._xor(fr[:0x7F])
    card[block * ps1mc.FRAME_SIZE : (block + 1) * ps1mc.FRAME_SIZE] = fr


def test_format_empty_card_parses_empty():
    card = ps1mc.format_empty_card()
    assert len(card) == ps1mc.CARD_SIZE
    assert card[:2] == ps1mc.MC_MAGIC
    assert ps1mc.parse_card(bytes(card)) == []


def test_single_block_round_trip():
    data = os.urandom(ps1mc.BLOCK_SIZE)
    card = ps1mc.build_single_save_card("BASLUS-00067HERO", data)
    saves = ps1mc.parse_card(card)
    assert len(saves) == 1
    name, got = saves[0]
    assert name == "BASLUS-00067HERO"
    assert got == data


def test_multi_block_round_trip():
    data = os.urandom(ps1mc.BLOCK_SIZE * 3)
    card = ps1mc.build_single_save_card("BESLES-00000RPG", data)
    saves = ps1mc.parse_card(card)
    assert len(saves) == 1
    assert saves[0][1] == data


def test_parse_multiple_saves():
    card = ps1mc.format_empty_card()
    a = os.urandom(2048)
    b = os.urandom(2048)
    _place_single_block_save(card, 1, "BASLUS-00067HERO", a)
    _place_single_block_save(card, 2, "BESLES-12345QUEST", b)
    saves = dict(ps1mc.parse_card(bytes(card)))
    assert set(saves) == {"BASLUS-00067HERO", "BESLES-12345QUEST"}
    assert saves["BASLUS-00067HERO"][:2048] == a
    assert saves["BESLES-12345QUEST"][:2048] == b


def test_rejects_bad_size_and_magic():
    with pytest.raises(ValueError):
        ps1mc.parse_card(b"\x00" * 100)
    bad = bytearray(ps1mc.format_empty_card())
    bad[0:2] = b"XX"
    with pytest.raises(ValueError):
        ps1mc.parse_card(bytes(bad))


@pytest.mark.parametrize(
    "name,expected",
    [
        ("BASLUS-00067HERO", "SLUS00067"),
        ("BESLES-12345QUEST", "SLES12345"),
        ("BISCES-00007", "SCES00007"),
        ("BASCUS-94228", "SCUS94228"),
        ("randomjunk", None),
    ],
)
def test_serial_from_filename(name, expected):
    assert ps1mc.serial_from_filename(name) == expected
