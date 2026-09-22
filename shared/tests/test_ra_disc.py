"""PlayStation disc hashing: ISO9660 walking and the RA hash rule.

Built on a synthetic ISO rather than a real disc image, so the layout
being asserted is visible in the test itself.
"""

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shared.ra_disc import (  # noqa: E402
    _iso_name,
    disc_hash_supported,
    find_file,
    hash_playstation_track,
    parse_boot_key,
)

SECTOR = 2048


def dir_record(name: str, extent: int, size: int, is_dir: bool = False) -> bytes:
    """One ISO9660 directory record."""
    raw = name.encode("ascii")
    length = 33 + len(raw)
    length += length % 2                     # records are padded to even
    rec = bytearray(length)
    rec[0] = length
    rec[2:6] = extent.to_bytes(4, "little")
    rec[10:14] = size.to_bytes(4, "little")
    rec[25] = 0x02 if is_dir else 0x00
    rec[32] = len(raw)
    rec[33 : 33 + len(raw)] = raw
    return bytes(rec)


class FakeTrack:
    """A data track backed by a dict of sectors."""

    def __init__(self, sectors: dict[int, bytes]):
        self.sectors = sectors

    def read_sector(self, sector: int, length: int = SECTOR) -> bytes:
        out = bytearray()
        while length > 0:
            data = self.sectors.get(sector, b"\x00" * SECTOR)
            data = data.ljust(SECTOR, b"\x00")
            take = min(length, SECTOR)
            out += data[:take]
            length -= take
            sector += 1
        return bytes(out)


def build_disc(files: dict[str, tuple[int, bytes]], root_sector: int = 20) -> FakeTrack:
    """A disc whose root directory holds ``{name: (sector, contents)}``."""
    pvd = bytearray(SECTOR)
    pvd[0] = 1
    pvd[1:6] = b"CD001"
    root = dir_record("", root_sector, SECTOR, is_dir=True)
    pvd[156 : 156 + len(root)] = root

    entries = bytearray()
    entries += dir_record("", root_sector, SECTOR, is_dir=True)
    for name, (sector, data) in files.items():
        entries += dir_record(name, sector, len(data))

    sectors = {16: bytes(pvd), root_sector: bytes(entries)}
    for name, (sector, data) in files.items():
        for i in range(0, max(len(data), 1), SECTOR):
            sectors[sector + i // SECTOR] = data[i : i + SECTOR]
    return FakeTrack(sectors)


# --- SYSTEM.CNF parsing -------------------------------------------------------


def test_boot_key_strips_cdrom_prefix_and_version():
    assert parse_boot_key(b"BOOT = cdrom:\\SLUS_007.57;1\r\n") == "SLUS_007.57"


def test_boot_key_handles_no_spaces_and_forward_slashes():
    assert parse_boot_key(b"BOOT=cdrom:/SCUS_945.03;1") == "SCUS_945.03"


def test_boot_key_without_a_cdrom_prefix():
    assert parse_boot_key(b"BOOT = SLPS_012.34;1") == "SLPS_012.34"


def test_boot_key_ignores_other_lines():
    cnf = b"TCB = 4\r\nEVENT = 10\r\nBOOT = cdrom:\\SLES_001.23;1\r\nSTACK = 801FFFF0\r\n"
    assert parse_boot_key(cnf) == "SLES_001.23"


def test_boot_key_absent_gives_empty():
    assert parse_boot_key(b"TCB = 4\r\nSTACK = 801FFFF0\r\n") == ""
    assert parse_boot_key(b"") == ""


def test_boot_key_is_case_insensitive():
    assert parse_boot_key(b"boot = cdrom:\\SLUS_007.57;1") == "SLUS_007.57"


# --- ISO9660 ------------------------------------------------------------------


def test_iso_name_drops_the_version_suffix():
    assert _iso_name(b"SLUS_007.57;1") == "SLUS_007.57"
    assert _iso_name(b"SYSTEM.CNF;1") == "SYSTEM.CNF"
    assert _iso_name(b"README.;1") == "README"


def test_find_file_locates_a_root_file():
    disc = build_disc({"SYSTEM.CNF;1": (30, b"BOOT = cdrom:\\ABC;1")})
    assert find_file(disc, "SYSTEM.CNF") == (30, len(b"BOOT = cdrom:\\ABC;1"))


def test_find_file_is_case_insensitive():
    disc = build_disc({"SYSTEM.CNF;1": (30, b"x")})
    assert find_file(disc, "system.cnf") is not None


def test_find_file_returns_none_when_absent():
    disc = build_disc({"SYSTEM.CNF;1": (30, b"x")})
    assert find_file(disc, "PSX.EXE") is None


def test_find_file_rejects_a_disc_with_no_volume_descriptor():
    assert find_file(FakeTrack({}), "SYSTEM.CNF") is None


def test_find_file_ignores_an_empty_path():
    disc = build_disc({"SYSTEM.CNF;1": (30, b"x")})
    assert find_file(disc, "") is None


# --- the hash -----------------------------------------------------------------


def ps_exe(payload: bytes) -> bytes:
    """A PS-X EXE: 2048-byte header stating the payload size, then payload."""
    header = bytearray(2048)
    header[0:8] = b"PS-X EXE"
    header[28:32] = len(payload).to_bytes(4, "little")
    return bytes(header) + payload


def test_hash_covers_the_boot_name_then_the_executable():
    payload = b"\xAB" * 4096
    exe = ps_exe(payload)
    disc = build_disc({
        "SYSTEM.CNF;1": (30, b"BOOT = cdrom:\\SLUS_007.57;1\r\n"),
        "SLUS_007.57;1": (40, exe),
    })
    expected = hashlib.md5(b"SLUS_007.57" + exe).hexdigest()
    assert hash_playstation_track(disc) == expected


def test_size_comes_from_the_exe_header_not_the_directory():
    """The directory entry is padded to a sector; the header is authoritative."""
    payload = b"\x5A" * 100
    exe = ps_exe(payload)
    padded = exe + b"\xFF" * 3000            # trailing slack must not be hashed
    disc = build_disc({
        "SYSTEM.CNF;1": (30, b"BOOT = cdrom:\\SLUS_001.90;1"),
        "SLUS_001.90;1": (40, padded),
    })
    expected = hashlib.md5(b"SLUS_001.90" + exe).hexdigest()
    assert hash_playstation_track(disc) == expected


def test_falls_back_to_psx_exe_when_system_cnf_is_missing():
    exe = ps_exe(b"\x11" * 512)
    disc = build_disc({"PSX.EXE;1": (40, exe)})
    expected = hashlib.md5(b"PSX.EXE" + exe).hexdigest()
    assert hash_playstation_track(disc) == expected


def test_a_boot_file_without_the_marker_is_still_hashed():
    """No PS-X EXE magic: fall back to the directory entry's size."""
    body = b"\x77" * SECTOR
    disc = build_disc({
        "SYSTEM.CNF;1": (30, b"BOOT = cdrom:\\WEIRD.BIN;1"),
        "WEIRD.BIN;1": (40, body),
    })
    expected = hashlib.md5(b"WEIRD.BIN" + body).hexdigest()
    assert hash_playstation_track(disc) == expected


def test_missing_executable_raises():
    import pytest

    from shared.ra_disc import DiscError

    disc = build_disc({"SYSTEM.CNF;1": (30, b"BOOT = cdrom:\\GONE.EXE;1")})
    with pytest.raises(DiscError):
        hash_playstation_track(disc)


# --- dispatch -----------------------------------------------------------------


def test_readable_disc_systems():
    assert disc_hash_supported("PS1")
    assert disc_hash_supported("ps1")
    assert not disc_hash_supported("SAT")
    assert not disc_hash_supported("SNES")


# --- PS2 and PSP ---------------------------------------------------------------

from shared.ra_disc import DvdTrack, hash_ps2_track, hash_psp_track  # noqa: E402


def test_ps2_boot_key_uses_boot2_and_cdrom0():
    cnf = b"BOOT2 = cdrom0:\\SLUS_203.12;1\r\nVER = 1.00\r\nVMODE = NTSC\r\n"
    assert parse_boot_key(cnf, key="BOOT2", prefix="cdrom0:") == "SLUS_203.12"


def test_ps1_lookup_never_takes_a_boot2_line():
    """'BOOT' is a prefix of 'BOOT2' - it must still not match it."""
    assert parse_boot_key(b"BOOT2 = cdrom0:\\SLUS_203.12;1\r\n") == ""


def test_boot_key_must_start_a_line():
    assert parse_boot_key(b"XBOOT = cdrom:\\NOPE.EXE;1\r\n") == ""


def test_ps2_hash_is_boot_name_then_executable():
    elf = b"\x7fELF" + b"\x42" * 5000
    disc = build_disc({
        "SYSTEM.CNF;1": (30, b"BOOT2 = cdrom0:\\SLUS_203.12;1\r\n"),
        "SLUS_203.12;1": (40, elf),
    })
    assert hash_ps2_track(disc) == hashlib.md5(b"SLUS_203.12" + elf).hexdigest()


def test_ps2_without_boot2_is_an_error():
    import pytest
    from shared.ra_disc import DiscError
    disc = build_disc({"SYSTEM.CNF;1": (30, b"BOOT = cdrom:\\PSX.EXE;1")})
    with pytest.raises(DiscError):
        hash_ps2_track(disc)


def _psp_disc(sfo, eboot):
    """PSP_GAME/PARAM.SFO and PSP_GAME/SYSDIR/EBOOT.BIN under the root."""
    root, psp_game, sysdir = 20, 21, 22
    pvd = bytearray(SECTOR); pvd[0] = 1; pvd[1:6] = b"CD001"
    rr = dir_record("", root, SECTOR, is_dir=True); pvd[156:156 + len(rr)] = rr
    sectors = {16: bytes(pvd),
               root: dir_record("", root, SECTOR, True) + dir_record("PSP_GAME", psp_game, SECTOR, True),
               psp_game: dir_record("", psp_game, SECTOR, True) + dir_record("PARAM.SFO;1", 30, len(sfo))
                         + dir_record("SYSDIR", sysdir, SECTOR, True),
               sysdir: dir_record("", sysdir, SECTOR, True) + dir_record("EBOOT.BIN;1", 40, len(eboot))}
    for base, data in ((30, sfo), (40, eboot)):
        for i in range(0, len(data), SECTOR):
            sectors[base + i // SECTOR] = data[i:i + SECTOR]
    return FakeTrack(sectors)


def test_psp_hash_is_param_sfo_then_eboot_with_no_name():
    sfo = b"\x00PSF" + b"\x11" * 300
    eboot = b"~PSP" + b"\x22" * 9000
    assert hash_psp_track(_psp_disc(sfo, eboot)) == hashlib.md5(sfo + eboot).hexdigest()


def test_psp_disc_without_param_sfo_is_an_error():
    import pytest
    from shared.ra_disc import DiscError
    with pytest.raises(DiscError):
        hash_psp_track(build_disc({"SYSTEM.CNF;1": (30, b"x")}))


def test_dvd_track_reads_plain_2048_byte_sectors():
    class Chd:
        def read(self, offset, length): return bytes([offset // 2048 % 256]) * length
    assert DvdTrack(Chd()).read_sector(5, 4) == b"\x05" * 4


def test_ps2_and_psp_are_now_readable():
    assert disc_hash_supported("PS2") and disc_hash_supported("PSP")
