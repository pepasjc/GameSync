"""
FLAC frame decoding for CHD hunks.

chdman compresses every hunk with each codec and keeps the smallest, and
FLAC wins on long runs of zero padding - they compress like silence.  So a
plain read of a disc's *data* can land in a FLAC hunk, and a reader that
cannot decode one cannot read most PS2 discs.

A CHD FLAC hunk is bare FLAC frames with no ``fLaC`` marker or STREAMINFO:
the container fixes the stream as 44.1 kHz, two channels, 16 bits, with a
constant block size derived from the hunk size.  Only what that stream
can contain is implemented - CONSTANT, VERBATIM, FIXED and LPC subframes,
Rice-coded residuals, and the four stereo decorrelation modes - which is
all of the frame-level format a 16-bit stereo encoder emits.

Written from the FLAC format specification (https://xiph.org/flac/format.html,
IETF RFC 9639).  No code copied.  Standard library only, so the server and
the MiSTer client can both use it.
"""

from __future__ import annotations


class FlacError(Exception):
    """The stream is not FLAC this decoder understands."""


_BLOCK_SIZES = {1: 192, 2: 576, 3: 1152, 4: 2304, 5: 4608,
                8: 256, 9: 512, 10: 1024, 11: 2048, 12: 4096,
                13: 8192, 14: 16384, 15: 32768}
_SAMPLE_SIZES = {1: 8, 2: 12, 4: 16, 5: 20, 6: 24, 7: 32}


class _Bits:
    """MSB-first reader with the operations FLAC needs (unary, signed, align)."""

    __slots__ = ("data", "pos", "acc", "nbits")

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0          # next byte to load
        self.acc = 0          # buffered bits
        self.nbits = 0        # how many of them are valid

    def read(self, count: int) -> int:
        if count == 0:
            return 0
        while self.nbits < count:
            if self.pos >= len(self.data):
                raise FlacError("unexpected end of FLAC data")
            self.acc = (self.acc << 8) | self.data[self.pos]
            self.pos += 1
            self.nbits += 8
        self.nbits -= count
        value = self.acc >> self.nbits
        self.acc &= (1 << self.nbits) - 1
        return value

    def read_signed(self, count: int) -> int:
        if count == 0:
            return 0
        value = self.read(count)
        if value & (1 << (count - 1)):
            value -= 1 << count
        return value

    def read_unary(self) -> int:
        """Number of 0 bits before the next 1 bit (which is consumed)."""
        zeros = 0
        while True:
            if self.nbits == 0:
                if self.pos >= len(self.data):
                    raise FlacError("unexpected end of FLAC data")
                self.acc = self.data[self.pos]
                self.pos += 1
                self.nbits = 8
            # Leading zeros within the buffered bits, in one step.
            if self.acc == 0:
                zeros += self.nbits
                self.nbits = 0
                continue
            lead = self.nbits - self.acc.bit_length()
            zeros += lead
            self.nbits -= lead + 1
            self.acc &= (1 << self.nbits) - 1
            return zeros

    def align(self) -> None:
        self.nbits -= self.nbits % 8
        self.acc &= (1 << self.nbits) - 1

    def byte_position(self) -> int:
        return self.pos - self.nbits // 8


def _utf8_number(bits: _Bits) -> None:
    """Skip the frame/sample number: UTF-8-style variable length."""
    first = bits.read(8)
    extra = 0
    mask = 0x80
    while first & mask:
        extra += 1
        mask >>= 1
    extra = max(0, extra - 1)
    for _ in range(extra):
        bits.read(8)


def _residual(bits: _Bits, block_size: int, order: int, out: list) -> None:
    method = bits.read(2)
    if method > 1:
        raise FlacError("reserved residual coding method")
    param_bits, escape = (4, 15) if method == 0 else (5, 31)
    partition_order = bits.read(4)
    partitions = 1 << partition_order
    per_partition = block_size >> partition_order
    for part in range(partitions):
        count = per_partition - (order if part == 0 else 0)
        param = bits.read(param_bits)
        if param == escape:
            raw_bits = bits.read(5)
            for _ in range(count):
                out.append(bits.read_signed(raw_bits))
            continue
        read, unary = bits.read, bits.read_unary
        for _ in range(count):
            value = (unary() << param) | read(param)
            out.append((value >> 1) ^ -(value & 1))


def _subframe(bits: _Bits, block_size: int, bps: int) -> list:
    if bits.read(1):
        raise FlacError("subframe padding bit set")
    kind = bits.read(6)
    wasted = 0
    if bits.read(1):
        wasted = bits.read_unary() + 1
        bps -= wasted

    if kind == 0:                                    # CONSTANT
        samples = [bits.read_signed(bps)] * block_size
    elif kind == 1:                                  # VERBATIM
        samples = [bits.read_signed(bps) for _ in range(block_size)]
    elif 8 <= kind <= 12:                            # FIXED, order 0-4
        order = kind - 8
        samples = [bits.read_signed(bps) for _ in range(order)]
        residual = []
        _residual(bits, block_size, order, residual)
        s = samples
        if order == 0:
            s.extend(residual)
        elif order == 1:
            for r in residual:
                s.append(s[-1] + r)
        elif order == 2:
            for r in residual:
                s.append(2 * s[-1] - s[-2] + r)
        elif order == 3:
            for r in residual:
                s.append(3 * s[-1] - 3 * s[-2] + s[-3] + r)
        else:
            for r in residual:
                s.append(4 * s[-1] - 6 * s[-2] + 4 * s[-3] - s[-4] + r)
    elif kind >= 32:                                 # LPC, order 1-32
        order = (kind & 0x1F) + 1
        samples = [bits.read_signed(bps) for _ in range(order)]
        precision = bits.read(4) + 1
        if precision == 16:
            raise FlacError("invalid LPC precision")
        shift = bits.read_signed(5)
        if shift < 0:
            raise FlacError("negative LPC shift")
        coefs = [bits.read_signed(precision) for _ in range(order)]
        residual = []
        _residual(bits, block_size, order, residual)
        s = samples
        rev = coefs[::-1]                            # pairs with s[-order:]
        for r in residual:
            window = s[-order:]
            s.append((sum(c * v for c, v in zip(rev, window)) >> shift) + r)
    else:
        raise FlacError(f"reserved subframe type {kind}")

    if wasted:
        samples = [v << wasted for v in samples]
    return samples


def decode_frames(data: bytes, pairs_wanted: int, big_endian: bool) -> bytes:
    """Decode stereo 16-bit frames until ``pairs_wanted`` sample pairs exist.

    Returns the interleaved samples as bytes in the requested byte order -
    which for a CHD hunk *is* the original data.
    """
    bits = _Bits(data)
    left_all: list = []
    right_all: list = []
    while len(left_all) < pairs_wanted:
        bits.align()
        sync = bits.read(14)
        if sync != 0x3FFE:
            raise FlacError("lost FLAC frame sync")
        bits.read(2)                                 # reserved + blocking strategy
        size_code = bits.read(4)
        rate_code = bits.read(4)
        channels = bits.read(4)
        bps_code = bits.read(3)
        bits.read(1)
        _utf8_number(bits)
        if size_code == 6:
            block_size = bits.read(8) + 1
        elif size_code == 7:
            block_size = bits.read(16) + 1
        elif size_code in _BLOCK_SIZES:
            block_size = _BLOCK_SIZES[size_code]
        else:
            raise FlacError("reserved block size")
        if rate_code == 12:
            bits.read(8)
        elif rate_code in (13, 14):
            bits.read(16)
        bits.read(8)                                 # CRC-8
        bps = _SAMPLE_SIZES.get(bps_code, 16) if bps_code else 16

        if channels == 1:                            # independent stereo
            left = _subframe(bits, block_size, bps)
            right = _subframe(bits, block_size, bps)
        elif channels == 8:                          # left / side
            left = _subframe(bits, block_size, bps)
            side = _subframe(bits, block_size, bps + 1)
            right = [l - d for l, d in zip(left, side)]
        elif channels == 9:                          # side / right
            side = _subframe(bits, block_size, bps + 1)
            right = _subframe(bits, block_size, bps)
            left = [d + r for d, r in zip(side, right)]
        elif channels == 10:                         # mid / side
            mid = _subframe(bits, block_size, bps)
            side = _subframe(bits, block_size, bps + 1)
            left, right = [], []
            for m, d in zip(mid, side):
                m = (m << 1) | (d & 1)
                left.append((m + d) >> 1)
                right.append((m - d) >> 1)
        else:
            raise FlacError(f"unexpected channel layout {channels}")

        bits.align()
        bits.read(16)                                # CRC-16
        left_all.extend(left)
        right_all.extend(right)

    order = "big" if big_endian else "little"
    out = bytearray(pairs_wanted * 4)
    for index in range(pairs_wanted):
        base = index * 4
        out[base:base + 2] = (left_all[index] & 0xFFFF).to_bytes(2, order)
        out[base + 2:base + 4] = (right_all[index] & 0xFFFF).to_bytes(2, order)
    return bytes(out)
