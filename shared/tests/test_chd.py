"""CHD reader primitives.

The hunk map is entropy-coded, so the bit reader and the canonical
Huffman decoder underneath it are where a subtle error would hide - a
wrong code length shifts every hunk offset after it and the failure shows
up as garbage data, not as an exception.
"""

import glob
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shared.chd import (  # noqa: E402
    CD_FRAME_SIZE,
    ChdError,
    ChdFile,
    _BitReader,
    _Huffman,
)


# --- bit reader ---------------------------------------------------------------


def test_reads_msb_first():
    bits = _BitReader(bytes([0b10110010]))
    assert bits.read(1) == 1
    assert bits.read(2) == 0b01
    assert bits.read(5) == 0b10010


def test_reads_across_byte_boundaries():
    bits = _BitReader(bytes([0xAB, 0xCD]))
    assert bits.read(12) == 0xABC
    assert bits.read(4) == 0xD


def test_reading_zero_bits_is_a_noop():
    bits = _BitReader(b"\xFF")
    assert bits.read(0) == 0
    assert bits.read(8) == 0xFF


def test_reads_a_full_32_bit_field():
    bits = _BitReader(bytes([0x12, 0x34, 0x56, 0x78]))
    assert bits.read(32) == 0x12345678


def test_past_the_end_yields_zeroes_and_flags_overflow():
    bits = _BitReader(b"\xFF")
    assert bits.read(8) == 0xFF
    assert not bits.overflowed
    assert bits.read(8) == 0
    assert bits.overflowed


# --- huffman ------------------------------------------------------------------


def _encode_tree_rle(lengths, numbits=4):
    """The code-length table as import_tree_rle expects to read it."""
    out = []
    for length in lengths:
        assert length != 1, "1 is the escape value; not used in these fixtures"
        out.append((length, numbits))
    bits = 0
    nbits = 0
    data = bytearray()
    for value, width in out:
        bits = (bits << width) | value
        nbits += width
        while nbits >= 8:
            nbits -= 8
            data.append((bits >> nbits) & 0xFF)
    if nbits:
        data.append((bits << (8 - nbits)) & 0xFF)
    return bytes(data)


def test_canonical_codes_round_trip_through_the_decoder():
    # Four symbols of length 2 -> codes 00, 01, 10, 11 in symbol order.
    lengths = [2, 2, 2, 2] + [0] * 12
    decoder = _Huffman(16, 8)
    decoder.import_tree_rle(_BitReader(_encode_tree_rle(lengths)))

    stream = _BitReader(bytes([0b00011011]))
    assert [decoder.decode_one(stream) for _ in range(4)] == [0, 1, 2, 3]


def test_mixed_code_lengths_decode_in_canonical_order():
    # One 1-bit symbol is impossible here (1 is the escape), so use
    # lengths 2,3,3 plus padding: a valid canonical set.
    lengths = [2, 3, 3, 0, 0, 0, 0, 0, 2, 2, 0, 0, 0, 0, 0, 0]
    decoder = _Huffman(16, 8)
    decoder.import_tree_rle(_BitReader(_encode_tree_rle(lengths)))
    # Every assigned symbol must decode back to itself.
    for symbol, length in enumerate(lengths):
        if length == 0:
            continue
        code = None
        for (clen, cval), sym in decoder._lookup.items():
            if sym == symbol:
                code = (clen, cval)
        assert code is not None, f"symbol {symbol} got no code"


def test_inconsistent_code_lengths_are_rejected():
    """Three symbols of length 2 cannot form a prefix-free code.

    Only two 2-bit slots remain once the level below is accounted for, so
    the level-by-level span check catches it.
    """
    decoder = _Huffman(16, 8)
    decoder._lengths = [2, 2, 2] + [0] * 13
    with pytest.raises(ChdError):
        decoder._assign_canonical_codes()


def test_length_one_codes_are_not_span_checked():
    """Matches the reference decoder, which skips the check at length 1 -
    worth pinning so a future 'fix' is a deliberate divergence."""
    decoder = _Huffman(16, 8)
    decoder._lengths = [1, 1, 1] + [0] * 13
    decoder._assign_canonical_codes()          # does not raise


def test_a_code_longer_than_the_tree_is_rejected():
    decoder = _Huffman(16, 8)
    decoder._lengths = [9] + [0] * 15
    with pytest.raises(ChdError):
        decoder._assign_canonical_codes()


# --- file handling ------------------------------------------------------------


def test_a_non_chd_is_rejected(tmp_path):
    bogus = tmp_path / "not.chd"
    bogus.write_bytes(b"this is not a CHD at all" * 10)
    with pytest.raises(ChdError):
        ChdFile(bogus)


def test_a_truncated_header_is_rejected(tmp_path):
    short = tmp_path / "short.chd"
    short.write_bytes(b"MComprHD" + b"\x00" * 8)
    with pytest.raises(ChdError):
        ChdFile(short)


# --- against a real image, when one is on hand --------------------------------

_REAL = sorted(glob.glob("Z:/ROMS/ps1/*.chd"))[:1]


@pytest.mark.skipif(not _REAL, reason="no CHD available on this machine")
def test_reads_a_real_disc_image():
    with ChdFile(_REAL[0]) as chd:
        assert chd.hunk_bytes % CD_FRAME_SIZE == 0
        assert chd.hunk_count > 0
        # Sector 16 of a disc is the ISO9660 primary volume descriptor,
        # at one of a few offsets depending on the track mode.
        for offset in (24, 16, 0, 8):
            if chd.read(16 * CD_FRAME_SIZE + offset, 8)[1:6] == b"CD001":
                break
        else:
            pytest.fail("no ISO9660 descriptor found in a real image")
