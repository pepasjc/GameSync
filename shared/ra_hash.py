"""
RetroAchievements game-identification hashes.

RetroAchievements recognises a ROM by an MD5 computed with per-system rules
(copier headers stripped, N64 byte order normalised, DS hashed from its code
blocks, arcade sets hashed by *name*).  This module reproduces those rules so
a local file can be matched against RA's hash library without any emulator.

Hashing rules ported from rcheevos (``src/rhash/hash.c``,
``src/rhash/hash_rom.c``) by the RetroAchievements team —
https://github.com/RetroAchievements/rcheevos — MIT License.  Rewritten in
Python from reading that implementation; no code copied.

Only cartridge-style systems are covered.  Disc systems (PS1, Saturn, Sega CD,
Dreamcast, PC Engine CD, PSP, GameCube, …) need a CD image parser to reach the
boot executable and are reported as unsupported here.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import PurePath
from typing import BinaryIO

# rcheevos never hashes more than this many bytes of a ROM.
MAX_HASH_BYTES = 64 * 1024 * 1024
_CHUNK = 65536

# ---------------------------------------------------------------------------
# GameSync system code -> RetroAchievements console id(s)
# ---------------------------------------------------------------------------

# A system can map to several RA consoles when RA splits what GameSync merges
# (NDS/DSi) or when two GameSync codes share one RA console (NGP/NGPC).
# Order = lookup priority.
RA_CONSOLE_IDS: dict[str, tuple[int, ...]] = {
    "MD": (1,),
    "GEN": (1,),
    "N64": (2,),
    "N64DD": (2,),
    "SNES": (3,),
    "BS": (3,),
    "GB": (4,),
    "GBA": (5,),
    "GBC": (6,),
    "NES": (7,),
    "FDS": (81, 7),
    "PCE": (8,),
    "TG16": (8,),
    "PCSG": (8,),
    "32X": (10,),
    "SMS": (11,),
    "LYNX": (13,),
    "NGP": (14,),
    "NGPC": (14,),
    "GG": (15,),
    "JAGUAR": (17,),
    "NDS": (18, 78),
    "POKEMINI": (24,),
    "A2600": (25,),
    "ATARI2600": (25,),
    "ARCADE": (27,),
    "MAME": (27,),
    "FBNEO": (27,),
    "FBA": (27,),
    "NEOGEO": (27,),
    "CPS1": (27,),
    "CPS2": (27,),
    "CPS3": (27,),
    "VB": (28,),
    "SG1000": (33,),
    "A7800": (51,),
    "ATARI7800": (51,),
    "WSWAN": (53,),
    "WSWANC": (53,),
}

# Systems whose ROM is hashed as-is (up to MAX_HASH_BYTES).
_WHOLE_FILE_SYSTEMS = frozenset(
    {
        "MD", "GEN", "GB", "GBA", "GBC", "32X", "SMS", "NGP", "NGPC", "GG",
        "JAGUAR", "POKEMINI", "A2600", "ATARI2600", "VB", "SG1000",
        "WSWAN", "WSWANC", "BS",
    }
)
_NES_SYSTEMS = frozenset({"NES", "FDS"})
_SNES_SYSTEMS = frozenset({"SNES"})
_PCE_SYSTEMS = frozenset({"PCE", "TG16", "PCSG"})
_N64_SYSTEMS = frozenset({"N64", "N64DD"})
_NDS_SYSTEMS = frozenset({"NDS"})
_ARCADE_SYSTEMS = frozenset(
    {"ARCADE", "MAME", "FBNEO", "FBA", "NEOGEO", "CPS1", "CPS2", "CPS3"}
)

# FBNeo loads console games through sub-system folders; RA folds the folder
# name into the arcade hash so ``nes/smb.zip`` and ``smb.zip`` differ.
_FBNEO_SUBSYSTEM_FOLDERS = frozenset(
    {
        "nes", "fds", "sms", "msx", "ngp", "pce", "chf", "sgx",
        "tg16", "msx1",
        "neocd",
        "coleco", "sg1000",
        "genesis",
        "gamegear", "megadriv", "pcengine", "channelf", "spectrum",
        "megadrive",
        "supergrafx", "zxspectrum",
        "mastersystem", "colecovision",
    }
)


# Disc systems RA supports, which this module cannot hash: identifying one
# means reading the boot executable out of the disc image, and every disc
# in a real library is a CHD.  They are listed so a caller can still look a
# game up *by title* (see :mod:`shared.ra_titles`) - a weaker claim, kept
# deliberately separate from the hash map so it can never be mistaken for
# one.
RA_DISC_CONSOLE_IDS: dict[str, tuple[int, ...]] = {
    "PS1": (12,),
    "PSX": (12,),
    "PS2": (21,),
    "PSP": (41,),
    "PS3": (82,),
    "SAT": (39,),
    "DC": (40,),
    "SEGACD": (9,),
    "SCD": (9,),
    "PCECD": (76,),
    "3DO": (43,),
    "NEOCD": (56,),
    "JAGCD": (77,),
    "GC": (16,),
    "WII": (19,),
    "PCFX": (49,),
}


def ra_console_ids(system: str) -> tuple[int, ...]:
    """RA console ids to search for a GameSync system code (empty = none).

    Covers both the systems this module can hash and the disc systems it
    can only match by title.
    """
    code = system.upper()
    return RA_CONSOLE_IDS.get(code) or RA_DISC_CONSOLE_IDS.get(code, ())


def ra_hash_supported(system: str) -> bool:
    """True when this module can compute an RA hash for ``system``.

    False for disc systems even though RA knows them - see
    :data:`RA_DISC_CONSOLE_IDS`.
    """
    return system.upper() in RA_CONSOLE_IDS


def ra_title_match_only(system: str) -> bool:
    """True when ``system`` can only be matched by title, not by hash."""
    code = system.upper()
    return code not in RA_CONSOLE_IDS and code in RA_DISC_CONSOLE_IDS


@dataclass(frozen=True)
class RaHash:
    md5: str | None
    reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.md5 is not None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _md5_stream(fh: BinaryIO, limit: int = MAX_HASH_BYTES) -> str:
    h = hashlib.md5()
    remaining = limit
    while remaining > 0:
        chunk = fh.read(min(_CHUNK, remaining))
        if not chunk:
            break
        h.update(chunk)
        remaining -= len(chunk)
    return h.hexdigest()


def _read_capped(fh: BinaryIO, extra: int = 512) -> bytes:
    """Read the bytes rcheevos would buffer: the ROM up to the cap plus any
    header that gets stripped before the cap applies."""
    return fh.read(MAX_HASH_BYTES + extra)


def _md5_buffer(data: bytes) -> str:
    return hashlib.md5(data[:MAX_HASH_BYTES]).hexdigest()


def _byteswap16(data: bytes) -> bytes:
    out = bytearray(len(data))
    out[0::2] = data[1::2]
    out[1::2] = data[0::2]
    return bytes(out)


def _byteswap32(data: bytes) -> bytes:
    out = bytearray(len(data))
    out[0::4] = data[3::4]
    out[1::4] = data[2::4]
    out[2::4] = data[1::4]
    out[3::4] = data[0::4]
    return bytes(out)


# ---------------------------------------------------------------------------
# Per-system rules
# ---------------------------------------------------------------------------


def _hash_nes(data: bytes) -> str:
    if len(data) > 16 and (data[:4] == b"NES\x1a" or data[:4] == b"FDS\x1a"):
        data = data[16:]
    return _md5_buffer(data)


def _hash_snes(data: bytes) -> str:
    # A 512-byte copier header leaves the size 512 past a multiple of 8 KB.
    if len(data) % 0x2000 == 512:
        data = data[512:]
    return _md5_buffer(data)


def _hash_pce(data: bytes) -> str:
    # Beetle PCE treats a size with bit 9 set as "512-byte header present".
    if len(data) & 512:
        data = data[512:]
    return _md5_buffer(data)


def _hash_7800(data: bytes) -> str:
    if len(data) > 128 and data[1:10] == b"ATARI7800":
        data = data[128:]
    return _md5_buffer(data)


def _hash_lynx(data: bytes) -> str:
    if len(data) > 64 and data[:5] == b"LYNX\x00":
        data = data[64:]
    return _md5_buffer(data)


def _hash_n64(fh: BinaryIO) -> RaHash:
    first = fh.read(1)
    if not first:
        return RaHash(None, "empty file")
    magic = first[0]
    if magic == 0x80:            # z64, native big-endian
        swap = None
    elif magic == 0x37:          # v64, 16-bit byteswapped
        swap = _byteswap16
    elif magic == 0x40:          # n64, 32-bit little-endian
        swap = _byteswap32
    elif magic in (0xE8, 0x22):  # 64DD disk image, hashed as-is
        swap = None
    else:
        return RaHash(None, "not a Nintendo 64 ROM")

    fh.seek(0)
    h = hashlib.md5()
    remaining = MAX_HASH_BYTES
    while remaining > 0:
        chunk = fh.read(min(_CHUNK, remaining))
        if not chunk:
            break
        if swap is not None:
            chunk = swap(chunk)
        h.update(chunk)
        remaining -= len(chunk)
    return RaHash(h.hexdigest())


def _hash_nds(fh: BinaryIO) -> RaHash:
    fh.seek(0)
    header = fh.read(512)
    if len(header) != 512:
        return RaHash(None, "failed to read header")

    offset = 0
    # SuperCard wrapper: an ARM branch at 0 and "DF\x96\x00" at 0xB0.
    if header[:4] == b"\x2e\x00\x00\xea" and header[0xB0:0xB4] == b"\x44\x46\x96\x00":
        offset = 512
        fh.seek(offset)
        header = fh.read(512)
        if len(header) != 512:
            return RaHash(None, "failed to read header")

    def u32(pos: int) -> int:
        return int.from_bytes(header[pos:pos + 4], "little")

    arm9_addr, arm9_size = u32(0x20), u32(0x2C)
    arm7_addr, arm7_size = u32(0x30), u32(0x3C)
    icon_addr = u32(0x68)

    if arm9_size + arm7_size > 16 * 1024 * 1024:
        return RaHash(None, "arm9 + arm7 code exceeds 16MB, not a DS ROM")

    h = hashlib.md5()
    h.update(header[:0x160])

    fh.seek(arm9_addr + offset)
    h.update(fh.read(arm9_size))
    fh.seek(arm7_addr + offset)
    h.update(fh.read(arm7_size))

    fh.seek(icon_addr + offset)
    icon = fh.read(0xA00)
    if len(icon) < 0xA00:
        # Homebrew may end inside the icon block; rcheevos zero-pads it.
        icon = icon + bytes(0xA00 - len(icon))
    h.update(icon)
    return RaHash(h.hexdigest())


def _hash_arcade(path: PurePath) -> str:
    """Arcade sets are identified by romset name, optionally prefixed by the
    FBNeo sub-system folder they sit in (``nes_smb``)."""
    stem = path.stem if path.suffix else path.name
    parent = path.parent.name.lower()
    if path.suffix and len(parent) < 16 and parent in _FBNEO_SUBSYSTEM_FOLDERS:
        text = f"{parent}_{stem}"
    else:
        text = stem
    return hashlib.md5(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def ra_hash_stream(fh: BinaryIO, system: str, filename: str = "") -> RaHash:
    """Hash an open binary stream the way RetroAchievements would.

    ``filename`` is the ROM's own name (or path) — needed for arcade sets,
    which hash by name, and for nothing else.  The stream must be seekable
    for N64 and DS.
    """
    sysc = system.upper()
    if sysc not in RA_CONSOLE_IDS:
        return RaHash(None, f"{sysc or '?'}: RetroAchievements hashing not supported")

    if sysc in _ARCADE_SYSTEMS:
        if not filename:
            return RaHash(None, "arcade sets hash by filename; none given")
        return RaHash(_hash_arcade(PurePath(filename)))
    if sysc in _N64_SYSTEMS:
        return _hash_n64(fh)
    if sysc in _NDS_SYSTEMS:
        return _hash_nds(fh)
    if sysc in _WHOLE_FILE_SYSTEMS:
        return RaHash(_md5_stream(fh))

    data = _read_capped(fh)
    if sysc in _NES_SYSTEMS:
        return RaHash(_hash_nes(data))
    if sysc in _SNES_SYSTEMS:
        return RaHash(_hash_snes(data))
    if sysc in _PCE_SYSTEMS:
        return RaHash(_hash_pce(data))
    if sysc == "A7800" or sysc == "ATARI7800":
        return RaHash(_hash_7800(data))
    if sysc == "LYNX":
        return RaHash(_hash_lynx(data))
    return RaHash(None, f"{sysc}: no hash rule")  # pragma: no cover - table drift


def ra_hash_bytes(data: bytes, system: str, filename: str = "") -> RaHash:
    """Convenience wrapper over :func:`ra_hash_stream` for in-memory ROMs."""
    import io

    return ra_hash_stream(io.BytesIO(data), system, filename)


def ra_hash_file(path, system: str) -> RaHash:
    """Hash a ROM on disk.  ``path`` may be ``str`` or ``Path``."""
    from pathlib import Path

    p = Path(path)
    sysc = system.upper()
    if sysc in _ARCADE_SYSTEMS:
        # No need to open the file: the name is the identity.
        return ra_hash_stream(None, sysc, str(p))  # type: ignore[arg-type]
    try:
        with open(p, "rb") as fh:
            return ra_hash_stream(fh, sysc, str(p))
    except OSError as exc:
        return RaHash(None, str(exc))
