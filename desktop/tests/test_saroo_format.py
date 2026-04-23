from pathlib import Path
import struct
import sys


DESKTOP_ROOT = Path(__file__).resolve().parents[1]
if str(DESKTOP_ROOT) not in sys.path:
    sys.path.insert(0, str(DESKTOP_ROOT))

from saroo_format import (
    SAT_BLOCK_ARCHIVE,
    SAT_BLOCK_DATA,
    SAT_BLOCK_SIZE,
    SAT_YABASANSHIRO_SIZE,
    SAT_YABAUSE_SIZE,
    _NativeSave,
    _build_native_saturn,
    convert_saturn_save_format,
    extract_saturn_save_set,
    list_saturn_archive_names,
    merge_saturn_save_set,
)


def _parse_native_reference(data: bytes):
    """Independent Saturn parser based on save-file-converter's logic."""
    total_blocks = len(data) // SAT_BLOCK_SIZE
    saves = []

    for blk in range(2, total_blocks):
        off = blk * SAT_BLOCK_SIZE
        block_type = struct.unpack_from(">I", data, off)[0]
        if block_type != SAT_BLOCK_ARCHIVE:
            continue

        name = data[off + 0x04 : off + 0x0F].split(b"\x00", 1)[0].decode("ascii")
        size = struct.unpack_from(">I", data, off + 0x1E)[0]

        block_list = []
        current_block_num = blk
        block_list_offset = 0x22
        block_list_read_index = 0

        while True:
            absolute = current_block_num * SAT_BLOCK_SIZE + block_list_offset
            entry = struct.unpack_from(">H", data, absolute)[0]
            if entry == 0:
                break
            if entry >= total_blocks:
                raise AssertionError(f"invalid block reference {entry}")
            block_list.append(entry)
            block_list_offset += 2

            if block_list_offset >= SAT_BLOCK_SIZE:
                current_block_num = block_list[block_list_read_index]
                block_list_read_index += 1
                block_type = struct.unpack_from(
                    ">I", data, current_block_num * SAT_BLOCK_SIZE
                )[0]
                if block_type != SAT_BLOCK_DATA:
                    raise AssertionError("block-list continuation must point to data")
                block_list_offset = 0x04

        data_start = current_block_num * SAT_BLOCK_SIZE + block_list_offset + 2
        segments = [data[data_start : (current_block_num + 1) * SAT_BLOCK_SIZE]]
        for db in block_list[block_list_read_index:]:
            block_type = struct.unpack_from(">I", data, db * SAT_BLOCK_SIZE)[0]
            if block_type != SAT_BLOCK_DATA:
                raise AssertionError("data block must start with the data marker")
            segments.append(
                data[db * SAT_BLOCK_SIZE + 0x04 : (db + 1) * SAT_BLOCK_SIZE]
            )

        saves.append((name, b"".join(segments)[:size], len(block_list)))

    return saves


def test_build_native_saturn_handles_block_list_overflow_without_corruption():
    raw = bytes((i % 251 for i in range(3040)))
    image = _build_native_saturn(
        [
            _NativeSave(
                name="GRANDIA_001",
                language_code=0,
                comment="Feena's Ho",
                date_code=23797305,
                raw_data=raw,
            )
        ]
    )

    parsed = _parse_native_reference(image)

    assert len(parsed) == 1
    assert parsed[0][0] == "GRANDIA_001"
    assert parsed[0][1] == raw
    assert parsed[0][2] > 15


def test_convert_saturn_save_format_supports_yabause_and_yabasanshiro():
    raw = bytes((i % 251 for i in range(3040)))
    canonical = _build_native_saturn(
        [
            _NativeSave(
                name="GRANDIA_001",
                language_code=0,
                comment="Feena's Ho",
                date_code=23797305,
                raw_data=raw,
            )
        ]
    )

    yabause = convert_saturn_save_format(canonical, "yabause")
    assert len(yabause) == SAT_YABAUSE_SIZE
    assert yabause[0::2] == b"\xFF" * (len(yabause) // 2)
    assert _parse_native_reference(yabause[1::2])[0][1] == raw

    yabasanshiro = convert_saturn_save_format(canonical, "yabasanshiro")
    assert len(yabasanshiro) == SAT_YABASANSHIRO_SIZE
    assert yabasanshiro[0::2] == b"\xFF" * (len(yabasanshiro) // 2)
    assert _parse_native_reference(yabasanshiro[1::2])[0][1] == raw


def test_extract_saturn_save_set_preserves_requested_archives_only():
    canonical = _build_native_saturn(
        [
            _NativeSave(
                name="DRACULAX_01",
                language_code=0,
                comment="Save 1",
                date_code=1,
                raw_data=b"drac-1",
            ),
            _NativeSave(
                name="DRACULAX_02",
                language_code=0,
                comment="Save 2",
                date_code=2,
                raw_data=b"drac-2",
            ),
            _NativeSave(
                name="GRANDIA_001",
                language_code=0,
                comment="Grandia",
                date_code=3,
                raw_data=b"grandia",
            ),
        ]
    )

    extracted = extract_saturn_save_set(canonical, ["DRACULAX_01", "DRACULAX_02"])

    assert list_saturn_archive_names(extracted) == ["DRACULAX_01", "DRACULAX_02"]


def test_merge_saturn_save_set_preserves_unrelated_yabasanshiro_archives():
    existing = convert_saturn_save_format(
        _build_native_saturn(
            [
                _NativeSave(
                    name="DRACULAX_01",
                    language_code=0,
                    comment="Save 1",
                    date_code=1,
                    raw_data=b"old-drac",
                ),
                _NativeSave(
                    name="GRANDIA_001",
                    language_code=0,
                    comment="Grandia",
                    date_code=2,
                    raw_data=b"grandia",
                ),
            ]
        ),
        "yabasanshiro",
    )
    replacement = _build_native_saturn(
        [
            _NativeSave(
                name="DRACULAX_01",
                language_code=0,
                comment="Save 1",
                date_code=3,
                raw_data=b"new-drac",
            )
        ]
    )

    merged = merge_saturn_save_set(existing, replacement, "yabasanshiro")

    assert list_saturn_archive_names(merged) == ["GRANDIA_001", "DRACULAX_01"]
    parsed = _parse_native_reference(merged[1::2])
    assert {name: raw for name, raw, _ in parsed} == {
        "GRANDIA_001": b"grandia",
        "DRACULAX_01": b"new-drac",
    }
