import hashlib
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shared.ra_hash import (  # noqa: E402
    MAX_HASH_BYTES,
    ra_console_ids,
    ra_hash_bytes,
    ra_hash_file,
    ra_hash_stream,
    ra_hash_supported,
)


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


# --- console mapping ---------------------------------------------------------


def test_console_ids_cover_common_systems():
    assert ra_console_ids("SNES") == (3,)
    assert ra_console_ids("snes") == (3,)
    assert ra_console_ids("NDS") == (18, 78)
    assert ra_console_ids("FDS")[0] == 81
    assert ra_console_ids("PS1") == ()
    assert ra_hash_supported("GBA")
    assert not ra_hash_supported("SEGACD")


def test_unsupported_system_reports_reason():
    result = ra_hash_bytes(b"\x00" * 64, "PS1")
    assert not result.ok
    assert "PS1" in result.reason


# --- whole-file systems -------------------------------------------------------


@pytest.mark.parametrize("system", ["MD", "GB", "GBA", "GBC", "SMS", "GG", "VB", "WSWAN", "A2600"])
def test_whole_file_systems_hash_raw_bytes(system):
    data = bytes(range(256)) * 40
    assert ra_hash_bytes(data, system).md5 == md5(data)


def test_whole_file_hash_caps_at_64mb():
    class Limited(io.RawIOBase):
        """Pretend to be a 70 MB file of 0xAB without allocating it."""

        def __init__(self):
            self.pos = 0
            self.size = 70 * 1024 * 1024

        def readinto(self, buf):
            n = min(len(buf), self.size - self.pos)
            buf[:n] = b"\xab" * n
            self.pos += n
            return n

        def readable(self):
            return True

    result = ra_hash_stream(io.BufferedReader(Limited()), "GBA")
    assert result.md5 == md5(b"\xab" * MAX_HASH_BYTES)


# --- header stripping ---------------------------------------------------------


def test_nes_strips_ines_header():
    body = b"\x12" * 32768
    headered = b"NES\x1a\x02\x01\x00\x00" + b"\x00" * 8 + body
    assert ra_hash_bytes(headered, "NES").md5 == md5(body)
    assert ra_hash_bytes(body, "NES").md5 == md5(body)


def test_fds_strips_fwnes_header():
    body = b"\x01*NINTENDO-HVC*" + b"\x00" * 65500
    headered = b"FDS\x1a\x01" + b"\x00" * 11 + body
    assert ra_hash_bytes(headered, "FDS").md5 == md5(body)


def test_snes_strips_512_byte_copier_header_only_when_size_says_so():
    body = b"\x42" * 0x8000
    assert ra_hash_bytes(b"\xff" * 512 + body, "SNES").md5 == md5(body)
    assert ra_hash_bytes(body, "SNES").md5 == md5(body)
    # 1024 extra bytes is not a copier header.
    odd = b"\xff" * 1024 + body
    assert ra_hash_bytes(odd, "SNES").md5 == md5(odd)


def test_pce_strips_header_when_bit9_set():
    body = b"\x24" * 0x40000
    assert ra_hash_bytes(b"\x00" * 512 + body, "PCE").md5 == md5(body)
    assert ra_hash_bytes(body, "TG16").md5 == md5(body)


def test_atari_7800_strips_a78_header():
    body = b"\x78" * 16384
    headered = b"\x01ATARI7800" + b"\x00" * 118 + body
    assert len(headered) - len(body) == 128
    assert ra_hash_bytes(headered, "A7800").md5 == md5(body)
    assert ra_hash_bytes(body, "A7800").md5 == md5(body)


def test_lynx_strips_lnx_header():
    body = b"\x13" * 65536
    headered = b"LYNX\x00" + b"\x00" * 59 + body
    assert ra_hash_bytes(headered, "LYNX").md5 == md5(body)
    # "LYNX" followed by a non-NUL byte is not the header magic.
    not_header = b"LYNXA" + b"\x00" * 59 + body
    assert ra_hash_bytes(not_header, "LYNX").md5 == md5(not_header)


# --- N64 byte order -----------------------------------------------------------

_Z64 = bytes([0x80, 0x37, 0x12, 0x40, 0x00, 0x00, 0x00, 0x0F]) + bytes(range(256)) * 4


def test_n64_z64_hashes_as_is():
    assert ra_hash_bytes(_Z64, "N64").md5 == md5(_Z64)


def test_n64_v64_is_byteswapped_to_z64_before_hashing():
    v64 = bytearray(len(_Z64))
    v64[0::2] = _Z64[1::2]
    v64[1::2] = _Z64[0::2]
    assert v64[0] == 0x37
    assert ra_hash_bytes(bytes(v64), "N64").md5 == md5(_Z64)


def test_n64_n64_is_wordswapped_to_z64_before_hashing():
    n64 = bytearray(len(_Z64))
    for i in range(0, len(_Z64), 4):
        n64[i:i + 4] = _Z64[i:i + 4][::-1]
    assert n64[0] == 0x40
    assert ra_hash_bytes(bytes(n64), "N64").md5 == md5(_Z64)


def test_n64_rejects_non_rom():
    result = ra_hash_bytes(b"NOTAROM!" * 8, "N64")
    assert not result.ok
    assert "Nintendo 64" in result.reason


# --- Nintendo DS --------------------------------------------------------------


def _build_nds(prefix: bytes = b"") -> tuple[bytes, bytes]:
    """Return (rom, expected_hashed_bytes) for a tiny synthetic DS ROM."""
    header = bytearray(512)
    header[0:12] = b"TESTGAME    "
    arm9 = b"\x9a" * 0x300
    arm7 = b"\x7b" * 0x200
    icon = b"\xcc" * 0xA00
    arm9_off, arm7_off, icon_off = 0x4000, 0x4400, 0x4800
    header[0x20:0x24] = arm9_off.to_bytes(4, "little")
    header[0x2C:0x30] = len(arm9).to_bytes(4, "little")
    header[0x30:0x34] = arm7_off.to_bytes(4, "little")
    header[0x3C:0x40] = len(arm7).to_bytes(4, "little")
    header[0x68:0x6C] = icon_off.to_bytes(4, "little")

    rom = bytearray(icon_off + len(icon))
    rom[0:512] = header
    rom[arm9_off:arm9_off + len(arm9)] = arm9
    rom[arm7_off:arm7_off + len(arm7)] = arm7
    rom[icon_off:icon_off + len(icon)] = icon
    expected = bytes(header[:0x160]) + arm9 + arm7 + icon
    return prefix + bytes(rom), expected


def test_nds_hashes_header_code_blocks_and_icon():
    rom, expected = _build_nds()
    assert ra_hash_bytes(rom, "NDS").md5 == md5(expected)


def test_nds_skips_supercard_wrapper():
    wrapper = bytearray(512)
    wrapper[0:4] = b"\x2e\x00\x00\xea"
    wrapper[0xB0:0xB4] = b"\x44\x46\x96\x00"
    rom, expected = _build_nds(prefix=bytes(wrapper))
    assert ra_hash_bytes(rom, "NDS").md5 == md5(expected)


def test_nds_pads_short_icon_block():
    rom, expected = _build_nds()
    truncated = rom[:-0x100]
    padded_expected = expected[:-0x100] + bytes(0x100)
    assert ra_hash_bytes(truncated, "NDS").md5 == md5(padded_expected)


def test_nds_rejects_absurd_code_sizes():
    header = bytearray(512)
    header[0x2C:0x30] = (20 * 1024 * 1024).to_bytes(4, "little")
    result = ra_hash_bytes(bytes(header) + b"\x00" * 1024, "NDS")
    assert not result.ok
    assert "16MB" in result.reason


# --- Arcade -------------------------------------------------------------------


def test_arcade_hashes_romset_name_not_contents():
    assert ra_hash_bytes(b"garbage", "ARCADE", "roms/arcade/mslug.zip").md5 == md5(b"mslug")
    assert ra_hash_bytes(b"other", "MAME", "C:\\roms\\mslug.7z").md5 == md5(b"mslug")


def test_arcade_includes_fbneo_subsystem_folder():
    assert ra_hash_bytes(b"", "FBNEO", "roms/fbneo/nes/smb.zip").md5 == md5(b"nes_smb")
    assert ra_hash_bytes(b"", "FBNEO", "roms/fbneo/NES/smb.zip").md5 == md5(b"nes_smb")
    # Unrelated parent folders are ignored.
    assert ra_hash_bytes(b"", "FBNEO", "roms/fbneo/smb.zip").md5 == md5(b"smb")


def test_arcade_folder_romset_hashes_bare_name():
    assert ra_hash_bytes(b"", "ARCADE", "roms/arcade/mslug").md5 == md5(b"mslug")


def test_arcade_needs_a_filename():
    assert not ra_hash_bytes(b"", "ARCADE").ok


# --- files --------------------------------------------------------------------


def test_ra_hash_file_reads_from_disk(tmp_path):
    body = b"\x55" * 0x8000
    rom = tmp_path / "game.sfc"
    rom.write_bytes(b"\x00" * 512 + body)
    assert ra_hash_file(rom, "SNES").md5 == md5(body)


def test_ra_hash_file_missing_file_is_an_error(tmp_path):
    result = ra_hash_file(tmp_path / "missing.gba", "GBA")
    assert not result.ok
    assert result.reason
