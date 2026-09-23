"""Tests for the GameCube memory card formatter.

The reference invariants here were derived by decoding a real 8 MB card image
produced by MemCard Pro (Metroid Prime, single 1-block save).
"""
import struct

from app.services.gc_cards import (
    _GC_BLOCK_SIZE,
    _GC_CARD_SIZE,
    _GC_DATA_OFFSET,
    _GC_USABLE_BLOCKS,
    _checksums_be,
    gc_card_from_gci,
    gc_extract_gci,
    is_gc_card_image,
    parse_card_entries,
)


def _make_gci(game_code: str, block_count: int, fill: int = 0xAB) -> bytes:
    """Build a minimal valid GCI: 64-byte dentry + block_count data blocks."""
    dentry = bytearray(b"\x00" * 64)
    dentry[0:4] = game_code.encode("ascii")
    dentry[4:6] = b"01"
    struct.pack_into(">H", dentry, 54, 99)            # first_block (arbitrary source value)
    struct.pack_into(">H", dentry, 56, block_count)   # block_count
    data = bytes([fill]) * (block_count * _GC_BLOCK_SIZE)
    return bytes(dentry) + data


def test_parse_card_entries_roundtrip():
    gci = _make_gci("GM8E", 2)
    card = gc_card_from_gci(gci)
    entries = parse_card_entries(card)
    assert len(entries) == 1
    code, out_gci = entries[0]
    assert code == "GM8E"
    # Data blocks are preserved (the card relocates the save to block 5, but the
    # extracted GCI's payload must match the source byte for byte).
    assert out_gci[64:] == gci[64:]


def test_parse_card_entries_empty_card():
    assert parse_card_entries(b"\xff" * _GC_CARD_SIZE) == []


def test_checksum_matches_real_header():
    """Reproduce the MemCard Pro header checksum (0xFF57 / 0xFFAB)."""
    header = bytearray(b"\xff" * 0x1FC)
    header[0:0x26] = bytes(0x26)
    struct.pack_into(">I", header, 0x18, 1)   # sram_language
    struct.pack_into(">H", header, 0x22, 64)  # size_mb
    struct.pack_into(">H", header, 0x24, 0)   # encoding
    struct.pack_into(">H", header, 0x1FA, 0)  # update_counter
    csum, inv = _checksums_be(bytes(header))
    assert csum == 0xFF57
    assert inv == 0xFFAB


def test_card_from_gci_is_valid_card_image():
    card = gc_card_from_gci(_make_gci("GM8E", 1))
    assert card is not None
    assert len(card) == _GC_CARD_SIZE
    assert is_gc_card_image(card)


def test_card_header_fields():
    card = gc_card_from_gci(_make_gci("GALE", 2))
    assert struct.unpack_from(">I", card, 0x18)[0] == 1     # sram_language
    assert struct.unpack_from(">H", card, 0x22)[0] == 64    # size in Mbit
    assert struct.unpack_from(">H", card, 0x24)[0] == 0     # encoding


def test_card_bam_reflects_allocation():
    block_count = 3
    card = gc_card_from_gci(_make_gci("GT3E", block_count))
    # Primary BAM at 0x6000
    free = struct.unpack_from(">H", card, 0x6006)[0]
    last = struct.unpack_from(">H", card, 0x6008)[0]
    assert free == _GC_USABLE_BLOCKS - block_count
    assert last == 5 + block_count - 1
    # Block chain: 5 -> 6 -> end
    assert struct.unpack_from(">H", card, 0x600A)[0] == 6
    assert struct.unpack_from(">H", card, 0x600C)[0] == 7
    assert struct.unpack_from(">H", card, 0x600E)[0] == 0xFFFF


def test_roundtrip_extract_matches_source():
    """The GCI extracted back out equals the original (first_block rewritten)."""
    gci = _make_gci("GOYE", 2)
    card = gc_card_from_gci(gci)
    extracted = gc_extract_gci(card, "GOYE")
    assert extracted is not None
    # Data blocks are identical
    assert extracted[64:] == gci[64:]
    # Allocated at block 5
    assert struct.unpack_from(">H", extracted, 54)[0] == 5


def test_free_blocks_are_zero_not_ff():
    """Free BAM entries must be 0x0000; 0xFFFF would read as allocated -> corrupt."""
    block_count = 2
    card = gc_card_from_gci(_make_gci("GEOE", block_count))
    for bam_off in (0x6000, 0x8000):
        # chain entries first
        assert struct.unpack_from(">H", card, bam_off + 0x000A)[0] == 6
        assert struct.unpack_from(">H", card, bam_off + 0x000C)[0] == 0xFFFF
        # every entry past the chain is free (0x0000), checked across the block
        for off in range(bam_off + 0x000A + block_count * 2, bam_off + _GC_BLOCK_SIZE, 2):
            assert struct.unpack_from(">H", card, off)[0] == 0x0000


def test_data_written_at_block_5():
    gci = _make_gci("GENE", 1, fill=0x7C)
    card = gc_card_from_gci(gci)
    block = card[_GC_DATA_OFFSET : _GC_DATA_OFFSET + _GC_BLOCK_SIZE]
    assert block == bytes([0x7C]) * _GC_BLOCK_SIZE


def test_rejects_malformed_gci():
    assert gc_card_from_gci(b"") is None
    assert gc_card_from_gci(b"\x00" * 32) is None
    # block_count says 2 but only 1 block of data present
    bad = bytearray(_make_gci("GBZE", 1))
    struct.pack_into(">H", bad, 56, 2)
    assert gc_card_from_gci(bytes(bad)) is None
