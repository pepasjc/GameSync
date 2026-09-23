"""In-place patching of the game list inside a GDEMU menu disc image."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

import openmenu_image as img

SECTOR = img.SECTOR_SIZE
OLD_LIST = b"[OPENMENU]\r\nnum_items=1\r\n\r\n[ITEMS]\r\n01.name=openMenu\r\n"


def _dir_record(extent: int, size: int, identifier: bytes = b"OPENMENU.INI;1") -> bytes:
    """A minimal ISO9660 directory record for one file."""
    record = bytearray(33 + len(identifier))
    record[0] = len(record)
    struct.pack_into("<I", record, 2, extent)
    struct.pack_into(">I", record, 6, extent)
    struct.pack_into("<I", record, 10, size)
    struct.pack_into(">I", record, 14, size)
    record[25] = 0  # a file, not a directory
    record[32] = len(identifier)
    record[33:] = identifier
    return bytes(record)


def _make_track(
    path: Path,
    sectors: int,
    record_at: int | None = None,
    record: bytes = b"",
    content_lba: int | None = None,
    content: bytes = b"",
    slack_filler: bytes = b"",
) -> None:
    data = bytearray(b"\x00" * (sectors * SECTOR))
    if record_at is not None:
        data[record_at : record_at + len(record)] = record
    if content_lba is not None:
        start = content_lba * SECTOR
        data[start : start + len(content)] = content
        if slack_filler:
            filler_at = start + len(content)
            data[filler_at : filler_at + len(slack_filler)] = slack_filler
    path.write_bytes(bytes(data))


def _single_track_menu(tmp_path: Path, **kwargs) -> Path:
    """A menu folder whose one data track holds both the record and the file."""
    folder = tmp_path / "01"
    folder.mkdir()
    _make_track(
        folder / "track01.iso",
        sectors=24,
        record_at=20 * SECTOR,
        record=_dir_record(22, len(OLD_LIST)),
        content_lba=22,
        content=OLD_LIST,
        **kwargs,
    )
    (folder / "disc.gdi").write_text(
        "1\n1 0 4 2048 track01.iso 0\n", encoding="utf-8"
    )
    return folder


def _split_track_menu(tmp_path: Path) -> Path:
    """A menu folder shaped like openMenu's: the high-density track's directory
    points at content stored in the last data track."""
    folder = tmp_path / "01"
    folder.mkdir()
    _make_track(
        folder / "track03.iso",
        sectors=30,
        record_at=20 * SECTOR,
        record=_dir_record(45100, len(OLD_LIST)),
    )
    _make_track(
        folder / "track05.iso",
        sectors=8,
        content_lba=3,  # 45100 - 45097
        content=OLD_LIST,
    )
    (folder / "disc.gdi").write_text(
        "3\n"
        "1 0 4 2048 track01.iso 0\n"
        "3 45000 4 2048 track03.iso 0\n"
        "5 45097 4 2048 track05.iso 0\n",
        encoding="utf-8",
    )
    _make_track(folder / "track01.iso", sectors=4)
    return folder


NEW_LIST = "[OPENMENU]\nnum_items=2\n\n[ITEMS]\n01.name=openMenu\n02.name=New Game\n"


# ──────────────────────────────────────────────────────────────────────
# Locating
# ──────────────────────────────────────────────────────────────────────
def test_finds_the_list_in_a_single_track_image(tmp_path):
    folder = _single_track_menu(tmp_path)
    (located,) = img.find_menu_lists(folder)
    assert located.content_track.path.name == "track01.iso"
    assert located.content_offset == 22 * SECTOR
    assert located.size == len(OLD_LIST)
    assert located.capacity == SECTOR


def test_resolves_content_stored_in_another_track(tmp_path):
    folder = _split_track_menu(tmp_path)
    (located,) = img.find_menu_lists(folder)
    assert located.content_track.path.name == "track05.iso"
    assert located.content_offset == 3 * SECTOR
    assert located.records[0].track.path.name == "track03.iso"


def test_read_menu_lists_returns_the_live_text(tmp_path):
    folder = _single_track_menu(tmp_path)
    assert img.read_menu_lists(folder) == [OLD_LIST.decode()]


def test_missing_gdi_is_reported(tmp_path):
    folder = tmp_path / "01"
    folder.mkdir()
    with pytest.raises(img.MenuImageError, match="No .gdi sheet"):
        img.find_menu_lists(folder)


def test_raw_2352_tracks_are_refused_with_an_explanation(tmp_path):
    folder = tmp_path / "01"
    folder.mkdir()
    (folder / "track03.bin").write_bytes(b"\x00" * SECTOR)
    (folder / "disc.gdi").write_text(
        "1\n3 45000 4 2352 track03.bin 0\n", encoding="utf-8"
    )
    with pytest.raises(img.MenuImageError, match="2352-byte sectors"):
        img.find_menu_lists(folder)


# ──────────────────────────────────────────────────────────────────────
# Patching
# ──────────────────────────────────────────────────────────────────────
def test_patch_rewrites_content_and_size_fields(tmp_path):
    folder = _single_track_menu(tmp_path)
    result = img.patch_menu_list(folder, NEW_LIST)

    expected = NEW_LIST.replace("\n", "\r\n")
    assert img.read_menu_lists(folder) == [expected]
    assert result["written"] == len(expected.encode())
    assert result["copies"] == 1

    # The directory record now advertises the new length, both-endian.
    record = (folder / "track01.iso").read_bytes()[20 * SECTOR : 20 * SECTOR + 33]
    assert struct.unpack_from("<I", record, 10)[0] == len(expected.encode())
    assert struct.unpack_from(">I", record, 14)[0] == len(expected.encode())


def test_patch_updates_every_copy_in_the_image(tmp_path):
    folder = _split_track_menu(tmp_path)
    # Give the low-density track its own copy, as a real menu image has.
    _make_track(
        folder / "track01.iso",
        sectors=24,
        record_at=20 * SECTOR,
        record=_dir_record(22, len(OLD_LIST)),
        content_lba=22,
        content=OLD_LIST,
    )
    result = img.patch_menu_list(folder, NEW_LIST)
    assert result["copies"] == 2
    assert img.read_menu_lists(folder) == [NEW_LIST.replace("\n", "\r\n")] * 2


def test_patch_pads_the_rest_of_the_sector_with_zeros(tmp_path):
    folder = _single_track_menu(tmp_path)
    img.patch_menu_list(folder, NEW_LIST)
    sector = (folder / "track01.iso").read_bytes()[22 * SECTOR : 23 * SECTOR]
    assert not sector[len(NEW_LIST.replace("\n", "\r\n")) :].strip(b"\x00")


def test_patch_is_refused_when_the_list_outgrows_its_sectors(tmp_path):
    folder = _single_track_menu(tmp_path)
    before = (folder / "track01.iso").read_bytes()
    with pytest.raises(img.MenuImageError, match="rebuild the menu"):
        img.patch_menu_list(folder, "x" * (SECTOR + 1))
    assert (folder / "track01.iso").read_bytes() == before


def test_patch_is_refused_when_another_file_shares_the_sector(tmp_path):
    folder = _single_track_menu(tmp_path, slack_filler=b"SOMEOTHERFILE")
    before = (folder / "track01.iso").read_bytes()
    with pytest.raises(img.MenuImageError, match="shared with other data"):
        img.patch_menu_list(folder, NEW_LIST)
    assert (folder / "track01.iso").read_bytes() == before


def test_nothing_is_written_when_one_copy_does_not_fit(tmp_path):
    folder = _split_track_menu(tmp_path)
    _make_track(
        folder / "track01.iso",
        sectors=24,
        record_at=20 * SECTOR,
        record=_dir_record(22, len(OLD_LIST)),
        content_lba=22,
        content=OLD_LIST,
        slack_filler=b"SOMEOTHERFILE",  # this copy cannot grow
    )
    before = {
        name: (folder / name).read_bytes()
        for name in ("track01.iso", "track03.iso", "track05.iso")
    }
    with pytest.raises(img.MenuImageError):
        img.patch_menu_list(folder, NEW_LIST)
    for name, data in before.items():
        assert (folder / name).read_bytes() == data, f"{name} was modified"


def test_backup_restores_the_original_list(tmp_path):
    folder = _single_track_menu(tmp_path)
    result = img.patch_menu_list(folder, NEW_LIST)
    assert img.read_menu_lists(folder) != [OLD_LIST.decode()]

    for backup in result["backups"]:
        img.restore_backup(backup)
    assert img.read_menu_lists(folder) == [OLD_LIST.decode()]


def test_patch_round_trips_repeatedly(tmp_path):
    folder = _single_track_menu(tmp_path)
    img.patch_menu_list(folder, NEW_LIST)
    third = NEW_LIST + "03.name=Another\n"
    img.patch_menu_list(folder, third)
    assert img.read_menu_lists(folder) == [third.replace("\n", "\r\n")]
