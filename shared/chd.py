"""
Minimal CHD (MAME Compressed Hunks of Data) v5 reader.

Enough of the format to read a few kilobytes out of a disc image without
decompressing the whole thing: the header, the hunk map, and the handful
of hunks a caller actually asks for.  That is what makes RetroAchievements
hashing viable for disc systems — identifying a game needs the boot
executable, a few hundred KB at most, out of a 500 MB image.

Format decoded from the layout documented by libchdr
(https://github.com/rtissera/libchdr, BSD-3-Clause, Romain Tisserand) and
MAME's chd.c, which it derives from.  Structure and field offsets only —
no code copied; this is an independent Python implementation.

Supported: v5 only, codecs ``zlib``/``lzma``/``cdzl``/``cdlz``/``none``
and self-referencing hunks.  Parent CHDs are rejected rather than guessed
at, and FLAC (``cdfl``) is not decoded — it only ever carries CD audio,
which nothing here needs to read.

Deliberately standard-library only, so the server and the MiSTer client
can both use it.
"""

from __future__ import annotations

import lzma
import struct
import zlib
from typing import BinaryIO, Optional

CHD_MAGIC = b"MComprHD"
V5_HEADER_SIZE = 124

#: CD images store 2352 bytes of sector plus 96 bytes of subcode per frame.
CD_MAX_SECTOR_DATA = 2352
CD_MAX_SUBCODE_DATA = 96
CD_FRAME_SIZE = CD_MAX_SECTOR_DATA + CD_MAX_SUBCODE_DATA

#: A Mode 1 / Mode 2 Form 1 sector carries 2048 bytes of user data.
SECTOR_USER_DATA = 2048

# Map entry compression types, as written into the decoded map.
_COMP_TYPE_0 = 0
_COMP_TYPE_1 = 1
_COMP_TYPE_2 = 2
_COMP_TYPE_3 = 3
_COMP_NONE = 4
_COMP_SELF = 5
_COMP_PARENT = 6
# Pseudo-types that only appear in the compressed stream.
_COMP_RLE_SMALL = 7
_COMP_RLE_LARGE = 8
_COMP_SELF_0 = 9
_COMP_SELF_1 = 10
_COMP_PARENT_SELF = 11
_COMP_PARENT_0 = 12
_COMP_PARENT_1 = 13


class ChdError(Exception):
    """The file is not a CHD this reader can serve."""


# ---------------------------------------------------------------------------
# Bit-level primitives used by the compressed hunk map
# ---------------------------------------------------------------------------


class _BitReader:
    """MSB-first bit reader over a byte buffer."""

    __slots__ = ("_data", "_pos", "_bits", "_value", "overflowed")

    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0
        self._bits = 0
        self._value = 0
        self.overflowed = False

    def read(self, count: int) -> int:
        if count <= 0:
            return 0
        while self._bits < count:
            if self._pos < len(self._data):
                self._value = (self._value << 8) | self._data[self._pos]
                self._pos += 1
            else:
                # Past the end: keep shifting in zeroes, as the reference
                # decoder does, but remember that it happened.
                self._value <<= 8
                self.overflowed = True
            self._bits += 8
        self._bits -= count
        result = (self._value >> self._bits) & ((1 << count) - 1)
        self._value &= (1 << self._bits) - 1
        return result


class _Huffman:
    """Canonical Huffman decoder for the hunk-map compression codes."""

    __slots__ = ("_lengths", "_lookup", "numcodes", "maxbits")

    def __init__(self, numcodes: int, maxbits: int):
        self.numcodes = numcodes
        self.maxbits = maxbits
        self._lengths = [0] * numcodes
        self._lookup: dict[tuple[int, int], int] = {}

    def import_tree_rle(self, bits: _BitReader) -> None:
        """Read the run-length-encoded table of code lengths."""
        numbits = 5 if self.maxbits >= 16 else (4 if self.maxbits >= 8 else 3)
        index = 0
        while index < self.numcodes:
            nodebits = bits.read(numbits)
            if nodebits != 1:
                self._lengths[index] = nodebits
                index += 1
                continue
            # 1 is an escape: a second 1 means a literal 1, anything else
            # is a repeat count for the length that follows it.
            nodebits = bits.read(numbits)
            if nodebits == 1:
                self._lengths[index] = 1
                index += 1
                continue
            repeat = bits.read(numbits) + 3
            if index + repeat > self.numcodes:
                raise ChdError("corrupt hunk map: run length overruns the table")
            for _ in range(repeat):
                self._lengths[index] = nodebits
                index += 1
        self._assign_canonical_codes()

    def _assign_canonical_codes(self) -> None:
        histogram = [0] * 33
        for length in self._lengths:
            if length > self.maxbits:
                raise ChdError("corrupt hunk map: code longer than the tree allows")
            if length <= 32:
                histogram[length] += 1

        # Codes are laid out from the longest length down, each level
        # starting at half the span of the one below it.
        start = 0
        for length in range(32, 0, -1):
            next_start = (start + histogram[length]) >> 1
            if length != 1 and next_start * 2 != start + histogram[length]:
                raise ChdError("corrupt hunk map: code lengths are inconsistent")
            histogram[length] = start
            start = next_start

        self._lookup = {}
        for symbol, length in enumerate(self._lengths):
            if length > 0:
                self._lookup[(length, histogram[length])] = symbol
                histogram[length] += 1

    def decode_one(self, bits: _BitReader) -> int:
        code = 0
        for length in range(1, self.maxbits + 1):
            code = (code << 1) | bits.read(1)
            symbol = self._lookup.get((length, code))
            if symbol is not None:
                return symbol
        raise ChdError("corrupt hunk map: no code matched")


# ---------------------------------------------------------------------------
# The file
# ---------------------------------------------------------------------------


class _MapEntry:
    __slots__ = ("comp", "length", "offset")

    def __init__(self, comp: int, length: int, offset: int):
        self.comp = comp
        self.length = length
        self.offset = offset


class ChdFile:
    """Random access to the hunks of a v5 CHD.

    Use as a context manager.  Only the hunks actually requested are read
    and decompressed; the map is decoded once up front, which for a disc
    image is a few hundred KB of work regardless of the image's size.
    """

    def __init__(self, path):
        self._fh: Optional[BinaryIO] = open(path, "rb")
        try:
            self._read_header()
            self._read_map()
        except Exception:
            self._fh.close()
            self._fh = None
            raise
        self._hunk_cache: dict[int, bytes] = {}
        self._metadata: Optional[list[tuple[bytes, bytes]]] = None

    # -- lifecycle --

    def __enter__(self) -> "ChdFile":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    # -- header --

    def _read_header(self) -> None:
        head = self._fh.read(V5_HEADER_SIZE)
        if len(head) < V5_HEADER_SIZE or head[:8] != CHD_MAGIC:
            raise ChdError("not a CHD file")
        length, version = struct.unpack(">II", head[8:16])
        if version != 5:
            raise ChdError(f"unsupported CHD version {version} (only v5 is read here)")
        if length != V5_HEADER_SIZE:
            raise ChdError(f"unexpected v5 header length {length}")

        self.compressors = list(struct.unpack(">4s4s4s4s", head[16:32]))
        (self.logical_bytes, self.map_offset, self.meta_offset) = struct.unpack(
            ">QQQ", head[32:56]
        )
        self.hunk_bytes, self.unit_bytes = struct.unpack(">II", head[56:64])
        self.parent_sha1 = head[104:124]

        if self.hunk_bytes == 0 or self.unit_bytes == 0:
            raise ChdError("corrupt header: zero hunk or unit size")
        if self.parent_sha1 != b"\x00" * 20:
            raise ChdError("CHDs with a parent are not supported")

        self.hunk_count = (self.logical_bytes + self.hunk_bytes - 1) // self.hunk_bytes
        self.compressed = self.compressors[0] != b"\x00\x00\x00\x00"

    # -- hunk map --

    def _read_map(self) -> None:
        if not self.compressed:
            # Uncompressed: a flat table of 4-byte hunk offsets, in units.
            self._fh.seek(self.map_offset)
            raw = self._fh.read(self.hunk_count * 4)
            self._map = []
            for i in range(self.hunk_count):
                offset = struct.unpack(">I", raw[i * 4 : i * 4 + 4])[0]
                self._map.append(
                    _MapEntry(_COMP_NONE, self.hunk_bytes, offset * self.unit_bytes)
                )
            return

        self._fh.seek(self.map_offset)
        header = self._fh.read(16)
        if len(header) < 16:
            raise ChdError("truncated hunk map header")
        map_bytes = struct.unpack(">I", header[0:4])[0]
        first_offset = int.from_bytes(header[4:10], "big")
        length_bits = header[12]
        self_bits = header[13]
        parent_bits = header[14]
        if length_bits > 32 or self_bits > 32 or parent_bits > 32:
            raise ChdError("corrupt hunk map: implausible field widths")

        compressed = self._fh.read(map_bytes)
        if len(compressed) < map_bytes:
            raise ChdError("truncated hunk map")

        bits = _BitReader(compressed)
        decoder = _Huffman(16, 8)
        decoder.import_tree_rle(bits)

        # Pass one: the compression type of every hunk, run-length coded.
        types = [0] * self.hunk_count
        repeat = 0
        last = 0
        for index in range(self.hunk_count):
            if repeat > 0:
                types[index] = last
                repeat -= 1
                continue
            value = decoder.decode_one(bits)
            if value == _COMP_RLE_SMALL:
                types[index] = last
                repeat = 2 + decoder.decode_one(bits)
            elif value == _COMP_RLE_LARGE:
                types[index] = last
                repeat = 2 + 16 + (decoder.decode_one(bits) << 4)
                repeat += decoder.decode_one(bits)
            else:
                last = value
                types[index] = value

        # Pass two: lengths and offsets, which follow in the same stream.
        self._map = []
        current = first_offset
        last_self = 0
        last_parent = 0
        units_per_hunk = self.hunk_bytes // self.unit_bytes
        for index in range(self.hunk_count):
            comp = types[index]
            offset = current
            length = 0
            if comp in (_COMP_TYPE_0, _COMP_TYPE_1, _COMP_TYPE_2, _COMP_TYPE_3):
                length = bits.read(length_bits)
                current += length
                bits.read(16)                       # crc16, not verified here
            elif comp == _COMP_NONE:
                length = self.hunk_bytes
                current += length
                bits.read(16)
            elif comp == _COMP_SELF:
                offset = last_self = bits.read(self_bits)
            elif comp == _COMP_PARENT:
                offset = last_parent = bits.read(parent_bits)
            elif comp in (_COMP_SELF_0, _COMP_SELF_1):
                if comp == _COMP_SELF_1:
                    last_self += 1
                comp = _COMP_SELF
                offset = last_self
            elif comp == _COMP_PARENT_SELF:
                comp = _COMP_PARENT
                offset = last_parent = (index * self.hunk_bytes) // self.unit_bytes
            elif comp in (_COMP_PARENT_0, _COMP_PARENT_1):
                if comp == _COMP_PARENT_1:
                    last_parent += units_per_hunk
                comp = _COMP_PARENT
                offset = last_parent
            else:
                raise ChdError(f"corrupt hunk map: unknown compression type {comp}")
            self._map.append(_MapEntry(comp, length, offset))

    # -- hunks --

    def read_hunk(self, index: int) -> bytes:
        """The decompressed bytes of one hunk (always ``hunk_bytes`` long)."""
        if index < 0 or index >= self.hunk_count:
            raise ChdError(f"hunk {index} out of range")
        cached = self._hunk_cache.get(index)
        if cached is not None:
            return cached

        entry = self._map[index]
        if entry.comp == _COMP_SELF:
            data = self.read_hunk(entry.offset)
        elif entry.comp == _COMP_PARENT:
            raise ChdError("hunk refers to a parent CHD, which is not supported")
        elif entry.comp == _COMP_NONE:
            self._fh.seek(entry.offset)
            data = self._fh.read(self.hunk_bytes)
        else:
            self._fh.seek(entry.offset)
            raw = self._fh.read(entry.length)
            if len(raw) < entry.length:
                raise ChdError("truncated hunk data")
            codec = self.compressors[entry.comp]
            data = self._decompress(codec, raw, self.hunk_bytes)

        if len(data) < self.hunk_bytes:
            data = data + b"\x00" * (self.hunk_bytes - len(data))

        # Small cache: reads walk forward through a file, so a handful of
        # recent hunks is all that ever gets reused.
        if len(self._hunk_cache) > 8:
            self._hunk_cache.clear()
        self._hunk_cache[index] = data
        return data

    def _decompress(self, codec: bytes, raw: bytes, out_len: int) -> bytes:
        if codec in (b"zlib", b"zstd"):
            if codec == b"zlib":
                return _inflate(raw, out_len)
            raise ChdError("zstd-compressed CHDs are not supported")
        if codec == b"lzma":
            return _unlzma(raw, out_len, self.hunk_bytes)
        if codec in (b"cdzl", b"cdlz"):
            return self._decompress_cd(codec, raw, out_len)
        if codec == b"cdfl":
            raise ChdError("FLAC-compressed hunks are not supported")
        raise ChdError(f"unsupported codec {codec!r}")

    def _decompress_cd(self, codec: bytes, raw: bytes, out_len: int) -> bytes:
        """A CD hunk: sector data and subcode are compressed separately.

        The leading ECC bitmap is skipped: it only says which sectors need
        their sync header and error-correction fields rebuilt, and none of
        that touches the 2048 bytes of user data this reader exists to get
        at.
        """
        frames = out_len // CD_FRAME_SIZE
        complen_bytes = 2 if out_len < 65536 else 3
        ecc_bytes = (frames + 7) // 8
        header_bytes = ecc_bytes + complen_bytes
        if len(raw) < header_bytes:
            raise ChdError("truncated CD hunk")

        base_len = int.from_bytes(raw[ecc_bytes : ecc_bytes + complen_bytes], "big")
        if len(raw) < header_bytes + base_len:
            raise ChdError("truncated CD hunk payload")

        base_raw = raw[header_bytes : header_bytes + base_len]
        want = frames * CD_MAX_SECTOR_DATA
        if codec == b"cdzl":
            sectors = _inflate(base_raw, want)
        else:
            sectors = _unlzma(base_raw, want, self.hunk_bytes)

        # Subcode is decompressed only to keep frame alignment; nothing
        # reads it, so a failure there is not worth failing the hunk over.
        sub_raw = raw[header_bytes + base_len :]
        subcode = b""
        if sub_raw:
            try:
                subcode = _inflate(sub_raw, frames * CD_MAX_SUBCODE_DATA)
            except Exception:  # noqa: BLE001 - subcode is not load-bearing
                subcode = b""
        if len(subcode) < frames * CD_MAX_SUBCODE_DATA:
            subcode = subcode + b"\x00" * (frames * CD_MAX_SUBCODE_DATA - len(subcode))

        out = bytearray(out_len)
        for frame in range(frames):
            src = frame * CD_MAX_SECTOR_DATA
            dst = frame * CD_FRAME_SIZE
            out[dst : dst + CD_MAX_SECTOR_DATA] = sectors[src : src + CD_MAX_SECTOR_DATA]
            sub_src = frame * CD_MAX_SUBCODE_DATA
            out[dst + CD_MAX_SECTOR_DATA : dst + CD_FRAME_SIZE] = subcode[
                sub_src : sub_src + CD_MAX_SUBCODE_DATA
            ]
        return bytes(out)

    # -- byte-level access --

    def read(self, offset: int, length: int) -> bytes:
        """``length`` logical bytes from ``offset``, spanning hunks as needed."""
        if length <= 0:
            return b""
        out = bytearray()
        while length > 0:
            hunk = offset // self.hunk_bytes
            if hunk >= self.hunk_count:
                break
            start = offset % self.hunk_bytes
            take = min(length, self.hunk_bytes - start)
            out += self.read_hunk(hunk)[start : start + take]
            offset += take
            length -= take
        return bytes(out)

    # -- metadata --

    def metadata(self) -> list[tuple[bytes, bytes]]:
        """``(tag, payload)`` for every metadata entry, in file order."""
        if self._metadata is not None:
            return self._metadata
        entries: list[tuple[bytes, bytes]] = []
        offset = self.meta_offset
        seen = set()
        while offset:
            if offset in seen:                       # malformed ring
                break
            seen.add(offset)
            self._fh.seek(offset)
            head = self._fh.read(16)
            if len(head) < 16:
                break
            tag = head[0:4]
            length = int.from_bytes(head[5:8], "big")
            nxt = int.from_bytes(head[8:16], "big")
            payload = self._fh.read(length)
            entries.append((tag, payload))
            offset = nxt
        self._metadata = entries
        return entries


def _inflate(raw: bytes, out_len: int) -> bytes:
    """Raw deflate, as CHD stores it (no zlib wrapper)."""
    obj = zlib.decompressobj(-zlib.MAX_WBITS)
    data = obj.decompress(raw, out_len)
    if len(data) < out_len:
        data += obj.flush()
    return data


def _unlzma(raw: bytes, out_len: int, dict_size: int) -> bytes:
    """Raw LZMA1 with the fixed properties chdman writes."""
    filters = [{
        "id": lzma.FILTER_LZMA1,
        "lc": 3, "lp": 0, "pb": 2,
        "dict_size": max(dict_size, 4096),
    }]
    decomp = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=filters)
    return decomp.decompress(raw, out_len)
