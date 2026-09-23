"""FLAC frame decoding for CHD hunks.

The frames here are built by hand with a tiny bit writer, so every path the
decoder takes - subframe types, Rice residuals, escapes, stereo modes,
wasted bits - is exercised against samples the test chose itself.  Real
hunks are checked against chdman's own extraction in test_chd.py.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shared.chd_flac import FlacError, decode_frames  # noqa: E402


class Writer:
    def __init__(self):
        self.bits = []

    def put(self, value, count):
        for i in range(count - 1, -1, -1):
            self.bits.append((value >> i) & 1)

    def signed(self, value, count):
        self.put(value & ((1 << count) - 1), count)

    def unary(self, zeros):
        self.bits.extend([0] * zeros + [1])

    def align(self):
        while len(self.bits) % 8:
            self.bits.append(0)

    def bytes(self):
        self.align()
        return bytes(int("".join(map(str, self.bits[i:i + 8])), 2) for i in range(0, len(self.bits), 8))


def header(w, block_size, channels):
    w.align()
    w.put(0x3FFE, 14); w.put(0, 2)
    w.put(6, 4)                 # block size in the next 8 bits
    w.put(0, 4)                 # sample rate from the (implied) stream
    w.put(channels, 4)
    w.put(4, 3)                 # 16 bits per sample
    w.put(0, 1)
    w.put(0, 8)                 # frame number 0
    w.put(block_size - 1, 8)
    w.put(0, 8)                 # CRC-8, not verified


def footer(w):
    w.align()
    w.put(0, 16)                # CRC-16, not verified


def rice(w, values, param, order):
    w.put(0, 2); w.put(0, 4)    # method 0, one partition
    w.put(param, 4)
    for v in values[order:]:
        z = (v << 1) ^ (v >> 31)
        w.unary(z >> param); w.put(z & ((1 << param) - 1), param)


def constant(w, value, bps=16):
    w.put(0, 1); w.put(0, 6); w.put(0, 1); w.signed(value, bps)


def verbatim(w, values, bps=16):
    w.put(0, 1); w.put(1, 6); w.put(0, 1)
    for v in values: w.signed(v, bps)


def fixed(w, values, order, bps=16, param=4):
    w.put(0, 1); w.put(8 + order, 6); w.put(0, 1)
    for v in values[:order]: w.signed(v, bps)
    # residuals for a FIXED predictor of this order
    res = list(values)
    for _ in range(order):
        res = [res[0]] + [res[i] - res[i - 1] for i in range(1, len(res))]
    rice(w, res, param, order)


def pcm(left, right, big_endian=False):
    order = "big" if big_endian else "little"
    return b"".join(l.to_bytes(2, order, signed=True) + r.to_bytes(2, order, signed=True)
                    for l, r in zip(left, right))


def test_constant_subframes_decode_to_a_run():
    w = Writer(); header(w, 8, 1); constant(w, 0); constant(w, -5); footer(w)
    assert decode_frames(w.bytes(), 8, big_endian=False) == pcm([0] * 8, [-5] * 8)


def test_verbatim_and_byte_order():
    left = [1, -2, 300, -32768, 32767, 0]
    right = [9, 8, 7, 6, 5, 4]
    w = Writer(); header(w, 6, 1); verbatim(w, left); verbatim(w, right); footer(w)
    data = w.bytes()
    assert decode_frames(data, 6, big_endian=False) == pcm(left, right)
    assert decode_frames(data, 6, big_endian=True) == pcm(left, right, big_endian=True)


@pytest.mark.parametrize("order", [0, 1, 2, 3, 4])
def test_fixed_predictors_with_rice_residuals(order):
    left = [100, 104, 111, 120, 126, 129, 128, 120, 101, 80, 65, 60]
    right = [-7, -3, 2, 9, 20, 18, 11, 0, -12, -20, -30, -33]
    w = Writer(); header(w, 12, 1); fixed(w, left, order); fixed(w, right, order); footer(w)
    assert decode_frames(w.bytes(), 12, big_endian=False) == pcm(left, right)


def test_left_side_stereo():
    left = [10, 20, 30, 40]; right = [7, 21, 29, 45]
    side = [l - r for l, r in zip(left, right)]
    w = Writer(); header(w, 4, 8); verbatim(w, left); verbatim(w, side, bps=17); footer(w)
    assert decode_frames(w.bytes(), 4, big_endian=False) == pcm(left, right)


def test_side_right_stereo():
    left = [10, 20, 30, 40]; right = [7, 21, 29, 45]
    side = [l - r for l, r in zip(left, right)]
    w = Writer(); header(w, 4, 9); verbatim(w, side, bps=17); verbatim(w, right); footer(w)
    assert decode_frames(w.bytes(), 4, big_endian=False) == pcm(left, right)


def test_mid_side_stereo_restores_the_odd_bit():
    left = [10, 21, -30, 41]; right = [7, 20, 29, -44]
    mid = [(l + r) >> 1 for l, r in zip(left, right)]
    side = [l - r for l, r in zip(left, right)]
    w = Writer(); header(w, 4, 10); verbatim(w, mid); verbatim(w, side, bps=17); footer(w)
    assert decode_frames(w.bytes(), 4, big_endian=False) == pcm(left, right)


def test_escaped_partition_reads_raw_samples():
    values = [5, -3, 12, 0]
    w = Writer(); header(w, 4, 1)
    for _ in range(2):
        w.put(0, 1); w.put(8, 6); w.put(0, 1)       # FIXED order 0
        w.put(0, 2); w.put(0, 4); w.put(15, 4)      # escape
        w.put(6, 5)                                  # 6 raw bits per sample
        for v in values: w.signed(v, 6)
    footer(w)
    assert decode_frames(w.bytes(), 4, big_endian=False) == pcm(values, values)


def test_wasted_bits_are_shifted_back_in():
    w = Writer(); header(w, 2, 1)
    for _ in range(2):
        w.put(0, 1); w.put(1, 6); w.put(1, 1); w.unary(1)   # 2 wasted bits
        w.signed(3, 14); w.signed(-1, 14)
    footer(w)
    assert decode_frames(w.bytes(), 2, big_endian=False) == pcm([12, -4], [12, -4])


def test_several_frames_are_joined_until_enough_samples():
    w = Writer()
    for value in (1, 2):
        header(w, 3, 1); constant(w, value); constant(w, -value); footer(w)
    assert decode_frames(w.bytes(), 6, big_endian=False) == pcm([1, 1, 1, 2, 2, 2], [-1, -1, -1, -2, -2, -2])


def test_lost_sync_is_an_error_not_garbage():
    with pytest.raises(FlacError):
        decode_frames(b"\x00" * 32, 4, big_endian=False)


def test_truncated_stream_is_an_error():
    w = Writer(); header(w, 8, 1); constant(w, 0)
    with pytest.raises(FlacError):
        decode_frames(w.bytes(), 8, big_endian=False)
