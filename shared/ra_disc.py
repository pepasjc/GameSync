"""
RetroAchievements hashing for disc images.

Identifying a disc game means reading its boot executable out of the
image's filesystem, not hashing the file — so this walks ISO9660 inside a
CHD (:mod:`shared.chd`) and hashes what RetroAchievements hashes.

Implemented: PlayStation and PS2 (``SYSTEM.CNF`` names the boot
executable; the hash is that name followed by the executable), PSP
(``PARAM.SFO`` then ``EBOOT.BIN``), Sega CD and Saturn (the 512-byte volume
and ROM header at sector 0), PC Engine CD (the title and boot program
named by the header sector of the first data track) and PC-FX (the
128-byte program header and the program it names, on the largest data
track).  The ISO9660 walk underneath
is system-agnostic and reads both CD images and DVD-type (UMD) CHDs, so
the other disc systems are additions rather than rewrites.

Hashing rules ported from rcheevos (``src/rhash/hash_disc.c``) by the
RetroAchievements team — https://github.com/RetroAchievements/rcheevos —
MIT License.  Rewritten in Python from reading that implementation; no
code copied.
"""

from __future__ import annotations

import hashlib
import re
from typing import Optional

from shared.chd import (
    CD_FRAME_SIZE,
    SECTOR_USER_DATA,
    ChdError,
    ChdFile,
)

#: Where 2048 bytes of user data start inside a stored 2352-byte sector,
#: by track mode.  Probed rather than trusted — see :func:`_data_offset`.
_MODE_OFFSETS = {
    "MODE1": 0,
    "MODE1_RAW": 16,
    "MODE2": 8,
    "MODE2_RAW": 24,
    "MODE2_FORM1": 0,
    "MODE2_FORM2": 0,
    "MODE2_FORM_MIX": 16,
}

#: Offsets worth probing for the ISO9660 signature when the mode is
#: unhelpful.  Ordered by how common they are.
_PROBE_OFFSETS = (24, 16, 0, 8)

_TRACK_RE = re.compile(rb"TRACK:(\d+)\s+TYPE:(\S+)")
_FIELD_RE = re.compile(rb"([A-Z]+):(\S+)")

#: chdman pads every track to a multiple of this many frames.
_TRACK_PADDING = 4


class DiscError(Exception):
    """The image could not be read as a disc."""


class DataTrack:
    """The first data track of a disc, addressed in 2048-byte sectors."""

    def __init__(self, chd: ChdFile, offset: int, first_frame: int = 0):
        self._chd = chd
        self._offset = offset
        self._first = first_frame

    def read_sector(self, sector: int, length: int = SECTOR_USER_DATA) -> bytes:
        """``length`` bytes of user data from a logical sector."""
        out = bytearray()
        while length > 0:
            base = (self._first + sector) * CD_FRAME_SIZE + self._offset
            take = min(length, SECTOR_USER_DATA)
            chunk = self._chd.read(base, take)
            if not chunk:
                break
            out += chunk
            length -= len(chunk)
            sector += 1
        return bytes(out)


class DvdTrack:
    """A DVD-type CHD (a PSP UMD, a PS2 DVD): plain 2048-byte sectors.

    No frames, subcode or sector headers - the logical bytes *are* the
    ISO image, so a sector is simply ``sector * 2048`` in.
    """

    def __init__(self, chd: ChdFile):
        self._chd = chd

    def read_sector(self, sector: int, length: int = SECTOR_USER_DATA) -> bytes:
        return self._chd.read(sector * SECTOR_USER_DATA, length)


def is_dvd(chd: ChdFile) -> bool:
    return any(tag == b"DVD " for tag, _ in chd.metadata())


def cd_tracks(chd: ChdFile) -> list[dict]:
    """Every track's type and where its first sector sits in the CHD.

    Tracks are stored back to back, each padded to a multiple of four
    frames.  A pregap is only *stored* when its type is marked ``V``
    (``PGTYPE:VAUDIO``); then the track's ``FRAMES`` include it and its data
    - what a disc's sector 0 means - starts after it.
    """
    tracks = []
    offset = 0
    for tag, payload in chd.metadata():
        if tag not in (b"CHTR", b"CHT2"):
            continue
        fields = {k.decode(): v.decode("ascii", "replace") for k, v in _FIELD_RE.findall(payload)}
        frames = int(fields.get("FRAMES", "0"))
        pregap = int(fields.get("PREGAP", "0"))
        stored_pregap = pregap if fields.get("PGTYPE", "").startswith("V") else 0
        tracks.append({
            "number": int(fields.get("TRACK", "0")),
            "type": fields.get("TYPE", "").upper(),
            "start": offset + stored_pregap,
            "sectors": frames - stored_pregap,
        })
        offset += frames + (-frames % _TRACK_PADDING)
    tracks.sort(key=lambda t: t["number"])
    return tracks


def _track_mode(chd: ChdFile) -> str:
    for tag, payload in chd.metadata():
        if tag in (b"CHTR", b"CHT2", b"CHGT", b"CHGD"):
            match = _TRACK_RE.search(payload)
            if match and match.group(1) == b"1":
                return match.group(2).decode("ascii", "replace").upper()
    return ""


def _data_offset(chd: ChdFile) -> int:
    """Byte offset of user data inside a sector, verified against the disc.

    The track metadata says which mode the track is, but the stored sector
    has its sync header and ECC stripped, so the mode byte cannot be read
    back.  The mode is therefore a hint, confirmed by actually finding the
    ISO9660 signature at sector 16 — and if it does not hold, the other
    layouts are tried before giving up.
    """
    hint = _MODE_OFFSETS.get(_track_mode(chd))
    candidates = list(_PROBE_OFFSETS)
    if hint is not None:
        candidates.insert(0, hint)
    for offset in candidates:
        data = chd.read(16 * CD_FRAME_SIZE + offset, 8)
        if len(data) >= 6 and data[1:6] == b"CD001":
            return offset
    raise DiscError("no ISO9660 volume descriptor found")


# ---------------------------------------------------------------------------
# ISO9660
# ---------------------------------------------------------------------------


def _iso_name(raw: bytes) -> str:
    """Directory-record identifier without its ``;1`` version suffix."""
    name = raw.decode("ascii", "replace").upper()
    return name.split(";", 1)[0].rstrip(".")


def find_file(track: DataTrack, path: str) -> Optional[tuple[int, int]]:
    """``(sector, size)`` for a file, or None.

    Accepts a plain name or a ``\\``/``/``-separated path; matching is
    case-insensitive and ignores the ISO version suffix, as the console
    does.
    """
    pvd = track.read_sector(16)
    if len(pvd) < 190 or pvd[1:6] != b"CD001":
        return None
    # Root directory record lives at offset 156 of the PVD.
    extent = int.from_bytes(pvd[158:162], "little")
    size = int.from_bytes(pvd[166:170], "little")

    parts = [p for p in path.replace("\\", "/").split("/") if p]
    if not parts:
        return None

    for depth, part in enumerate(parts):
        found = _find_in_directory(track, extent, size, part)
        if found is None:
            return None
        extent, size, is_dir = found
        last = depth == len(parts) - 1
        if last:
            return None if is_dir else (extent, size)
        if not is_dir:
            return None
    return None


def _find_in_directory(
    track: DataTrack, extent: int, size: int, want: str
) -> Optional[tuple[int, int, bool]]:
    """``(extent, size, is_dir)`` for one entry of a directory."""
    want = want.upper().split(";", 1)[0].rstrip(".")
    sectors = (size + SECTOR_USER_DATA - 1) // SECTOR_USER_DATA
    for index in range(sectors):
        data = track.read_sector(extent + index)
        pos = 0
        while pos < len(data):
            length = data[pos]
            if length == 0:
                break                      # rest of the sector is padding
            if pos + length > len(data):
                break
            record = data[pos : pos + length]
            pos += length
            if len(record) < 33:
                continue
            name_len = record[32]
            if name_len == 0 or 33 + name_len > len(record):
                continue
            name = _iso_name(record[33 : 33 + name_len])
            if name != want:
                continue
            child_extent = int.from_bytes(record[2:6], "little")
            child_size = int.from_bytes(record[10:14], "little")
            is_dir = bool(record[25] & 0x02)
            return child_extent, child_size, is_dir
    return None


# ---------------------------------------------------------------------------
# PlayStation
# ---------------------------------------------------------------------------

def parse_boot_key(system_cnf: bytes, key: str = "BOOT", prefix: str = "cdrom:") -> str:
    """Executable name from a ``SYSTEM.CNF`` boot line.

    ``"BOOT = cdrom:\\SLUS_007.57;1"`` gives ``"SLUS_007.57"``: the
    ``cdrom:`` prefix and any leading separators go, and the name ends at
    the first whitespace or ``;``.  PS2 discs use ``BOOT2`` and
    ``cdrom0:``; the key must be followed by ``=`` (after spaces), so a
    PS1 lookup for ``BOOT`` never mistakes a ``BOOT2`` line for its own.
    """
    pattern = rb"(?:^|[\r\n])[ \t]*" + re.escape(key.encode()) + rb"[ \t]*=[ \t]*([^\r\n]*)"
    match = re.search(pattern, system_cnf, re.IGNORECASE)
    if not match:
        return ""
    value = match.group(1).decode("ascii", "replace").strip()
    if value.lower().startswith(prefix):
        value = value[len(prefix):]
    value = value.lstrip("\\/")
    for index, char in enumerate(value):
        if char.isspace() or char == ";":
            return value[:index]
    return value


def hash_playstation(chd: ChdFile) -> str:
    """RetroAchievements MD5 for a PlayStation disc image."""
    return hash_playstation_track(DataTrack(chd, _data_offset(chd)))


def hash_playstation_track(track) -> str:
    """RetroAchievements MD5 for an already-opened PlayStation data track.

    The hash covers the boot executable's *name* followed by the
    executable itself — a handful of games share an engine and differ
    only in data files, and the serial-derived boot name is what tells
    them apart.
    """
    exe_name = ""
    found = find_file(track, "SYSTEM.CNF")
    if found:
        exe_name = parse_boot_key(track.read_sector(found[0], min(found[1], SECTOR_USER_DATA)))
    if exe_name:
        located = find_file(track, exe_name)
    else:
        located = find_file(track, "PSX.EXE")
        if located:
            exe_name = "PSX.EXE"
    if not located:
        raise DiscError("could not locate the primary executable")

    sector, size = located
    header = track.read_sector(sector, 32)
    if header[:7] == b"PS-X EX":
        # The PS-X EXE header states the payload size 28 bytes in, not
        # counting the 2048-byte header itself.
        size = int.from_bytes(header[28:32], "little") + SECTOR_USER_DATA

    md5 = hashlib.md5()
    md5.update(exe_name.encode("ascii", "replace"))
    remaining = size
    while remaining > 0:
        take = min(remaining, SECTOR_USER_DATA)
        chunk = track.read_sector(sector, take)
        if not chunk:
            break
        md5.update(chunk)
        remaining -= len(chunk)
        sector += 1
    return md5.hexdigest()


# ---------------------------------------------------------------------------
# Sega CD / Saturn
# ---------------------------------------------------------------------------

_SEGA_MAGIC = (b"SEGADISCSYSTEM  ", b"SEGA SEGASATURN ")


def hash_sega(chd: ChdFile) -> str:
    """RetroAchievements MD5 for a Sega CD or Saturn disc.

    RA hashes only the first 512 bytes of sector 0 - the volume header and
    ROM header - because the executables that follow are neither fixed nor
    single.  The header sits at the start of the sector's user data, whose
    offset depends on how the track was stored, so each is tried.
    """
    tracks = cd_tracks(chd)
    base = tracks[0]["start"] * CD_FRAME_SIZE if tracks else 0
    for offset in (16, 0, 24):
        header = chd.read(base + offset, 512)
        if header[:16] in _SEGA_MAGIC:
            return hashlib.md5(header).hexdigest()
    raise DiscError("not a Sega CD or Saturn disc")


# ---------------------------------------------------------------------------
# PC Engine CD
# ---------------------------------------------------------------------------


_PCE_MARKER = b"PC Engine CD-ROM SYSTEM"
_PCFX_MARKER = b"PC-FX:Hu_CD-ROM"


def _offset_candidates(track: dict) -> list[int]:
    """User-data offsets to try for ``track``: its mode's first, then the rest."""
    offset = _MODE_OFFSETS.get(track["type"])
    return ([offset] if offset is not None else []) + [o for o in (16, 0, 24) if o != offset]


def _hash_sectors(md5, track: DataTrack, start: int, count: int,
                  track_sectors: Optional[int] = None) -> None:
    """Feed ``count`` whole sectors from ``start`` into ``md5``.

    With ``track_sectors`` given, a sector past the end of the track is not
    read from whatever follows it in the CHD: rcheevos reads each track of
    a (Redump, one file per track) dump from its own file, where a read past
    the end fails and the previous sector's buffer is hashed again.  RA's
    registered hashes were made that way.
    """
    last = bytes(SECTOR_USER_DATA)
    for sector in range(start, start + count):
        if track_sectors is None or sector < track_sectors:
            last = track.read_sector(sector).ljust(SECTOR_USER_DATA, b"\x00")
        md5.update(last)


def _hash_pce_header(track: DataTrack, head: bytes,
                     track_sectors: Optional[int] = None) -> str:
    """The PC Engine CD rule, given the track and its sector-1 header."""
    md5 = hashlib.md5(head[106:128])
    start = (head[0] << 16) | (head[1] << 8) | head[2]
    _hash_sectors(md5, track, start, head[3], track_sectors)
    return md5.hexdigest()


def hash_pce_cd(chd: ChdFile) -> str:
    """RetroAchievements MD5 for a PC Engine CD.

    The first *data* track's sector 1 names the boot program: its first
    three bytes are the program's sector (relative to the track), the fourth
    the sector count, and bytes 106-127 the title.  The hash is that title
    followed by the program.  Game Express discs use a plain filesystem
    instead, and hash BOOT.BIN.
    """
    data = [t for t in cd_tracks(chd) if t["type"] != "AUDIO"]
    if not data:
        raise DiscError("no data track")
    first = data[0]
    candidates = _offset_candidates(first)
    for offset in candidates:
        track = DataTrack(chd, offset, first["start"])
        head = track.read_sector(1, 128)
        if head[32:55] == _PCE_MARKER:
            return _hash_pce_header(track, head)
    for offset in candidates:
        track = DataTrack(chd, offset, first["start"])
        boot = find_file(track, "BOOT.BIN")
        if boot and boot[1] < MAX_FILE_BYTES:
            md5 = hashlib.md5()
            _hash_iso_file(md5, track, boot)
            return md5.hexdigest()
    raise DiscError("not a PC Engine CD")


# ---------------------------------------------------------------------------
# PC-FX
# ---------------------------------------------------------------------------


def _pcfx_track(chd: ChdFile, info: dict) -> Optional[DataTrack]:
    """``info`` opened at the offset where a PC-FX or PC Engine marker shows."""
    tracks = [DataTrack(chd, offset, info["start"]) for offset in _offset_candidates(info)]
    for track in tracks:
        if track.read_sector(0, 32)[:15] == _PCFX_MARKER:
            return track
    for track in tracks:
        if track.read_sector(1, 128)[32:55] == _PCE_MARKER:
            return track
    return None


def hash_pcfx(chd: ChdFile) -> str:
    """RetroAchievements MD5 for a PC-FX disc.

    The executable can sit in any track, so the *largest* data track is
    checked first (the first of equal size wins), then track 2.  A PC-FX
    track starts with ``PC-FX:Hu_CD-ROM`` in sector 0; sector 1 is the
    program header.  The hash is that header's first 128 bytes (the title
    is bytes 0-31) followed by the program, whose track-relative sector is
    the little-endian 24-bit value at bytes 32-34 and whose length in
    sectors is the one at bytes 36-38.  Some PC-FX discs still identify as
    PC Engine CDs; a track 2 carrying the PC Engine header is hashed by
    that rule instead.

    A program that runs past the end of its track (Angelique: Tenkuu no
    Requiem does) is hashed the way RA's own reader sees it - see
    :func:`_hash_sectors`.
    """
    tracks = cd_tracks(chd)
    largest = None
    for info in tracks:
        if info["type"] != "AUDIO" and (largest is None or info["sectors"] > largest["sectors"]):
            largest = info
    if largest is None:
        raise DiscError("no data track")

    info = largest
    track = _pcfx_track(chd, info)
    if track is None or track.read_sector(0, 32)[:15] != _PCFX_MARKER:
        info = next((t for t in tracks if t["number"] == 2), None)
        if info is None:
            raise DiscError("not a PC-FX CD")
        track = _pcfx_track(chd, info)
        if track is None:
            raise DiscError("not a PC-FX CD")

    head = track.read_sector(1, 128)
    if track.read_sector(0, 32)[:15] != _PCFX_MARKER:
        # Found only the PC Engine header (checked for track 2 alone, as RA does).
        return _hash_pce_header(track, head, info["sectors"])
    md5 = hashlib.md5(head)
    start = head[32] | (head[33] << 8) | (head[34] << 16)
    count = head[36] | (head[37] << 8) | (head[38] << 16)
    _hash_sectors(md5, track, start, count, info["sectors"])
    return md5.hexdigest()


#: RetroAchievements hashes at most this much of any one file.
MAX_FILE_BYTES = 64 * 1024 * 1024


def _hash_iso_file(md5, track, located: tuple[int, int]) -> None:
    sector, size = located
    remaining = min(size, MAX_FILE_BYTES)
    while remaining > 0:
        take = min(remaining, SECTOR_USER_DATA)
        chunk = track.read_sector(sector, take)
        if not chunk:
            break
        md5.update(chunk)
        remaining -= len(chunk)
        sector += 1


def _open_track(chd: ChdFile):
    return DvdTrack(chd) if is_dvd(chd) else DataTrack(chd, _data_offset(chd))


def hash_ps2_track(track) -> str:
    """RetroAchievements MD5 for a PS2 disc: boot name, then the executable.

    ``SYSTEM.CNF``'s ``BOOT2`` line names the ELF.  Unlike PS1 there is no
    size in the executable's header to trust, so the directory entry's size
    is hashed (capped as RA caps every file).
    """
    cnf = find_file(track, "SYSTEM.CNF")
    if not cnf:
        raise DiscError("no SYSTEM.CNF - not a PS2 game disc")
    exe_name = parse_boot_key(track.read_sector(cnf[0], min(cnf[1], SECTOR_USER_DATA)),
                              key="BOOT2", prefix="cdrom0:")
    if not exe_name:
        raise DiscError("SYSTEM.CNF has no BOOT2 line")
    located = find_file(track, exe_name)
    if not located:
        raise DiscError(f"boot executable {exe_name} not found")
    md5 = hashlib.md5()
    md5.update(exe_name.encode("ascii", "replace"))
    _hash_iso_file(md5, track, located)
    return md5.hexdigest()


def hash_ps2(chd: ChdFile) -> str:
    """RetroAchievements MD5 for a PS2 disc image (DVD or CD)."""
    return hash_ps2_track(_open_track(chd))


def hash_psp_track(track) -> str:
    """RetroAchievements MD5 for a PSP disc track: PARAM.SFO then EBOOT.BIN.

    PARAM.SFO carries the serial, title and version; EBOOT.BIN is the
    (encrypted) primary executable.  Nothing else on the disc counts, so a
    translation patch that rewrites both is identified the same way
    whichever regional base it was applied to.
    """
    sfo = find_file(track, "PSP_GAME/PARAM.SFO")
    if not sfo:
        raise DiscError("not a PSP game disc (no PSP_GAME/PARAM.SFO)")
    eboot = find_file(track, "PSP_GAME/SYSDIR/EBOOT.BIN")
    if not eboot:
        raise DiscError("could not find the primary executable")
    md5 = hashlib.md5()
    _hash_iso_file(md5, track, sfo)
    _hash_iso_file(md5, track, eboot)
    return md5.hexdigest()


def hash_psp(chd: ChdFile) -> str:
    """RetroAchievements MD5 for a PSP UMD image."""
    return hash_psp_track(_open_track(chd))


#: System code -> the function that hashes it.
_DISC_HASHERS = {
    "PS1": hash_playstation,
    "PSX": hash_playstation,
    "PSP": hash_psp,
    "PS2": hash_ps2,
    "SEGACD": hash_sega,
    "SCD": hash_sega,
    "SAT": hash_sega,
    "PCECD": hash_pce_cd,
    "PCFX": hash_pcfx,
}


def disc_hash_supported(system: str) -> bool:
    return system.upper() in _DISC_HASHERS


def hash_disc_file(path, system: str) -> Optional[str]:
    """RA hash for a disc image, or None when it cannot be read.

    Only CHD is handled: that is what a real library is stored as, and
    anything else can be converted.
    """
    hasher = _DISC_HASHERS.get(system.upper())
    if hasher is None:
        return None
    try:
        with ChdFile(path) as chd:
            return hasher(chd)
    except (ChdError, DiscError, OSError, ValueError):
        return None
