"""PS1 memory-card filesystem (ps1mc) parser and builder.

PS1 cards are 128 KB = 16 blocks * 8 KB.  Block 0 is the directory; blocks 1-15
hold one save each (a save may chain several blocks).  This module splits a
shared card into its individual saves and rebuilds a single-save card, so a
real multi-game PS1 card, a POPStarter/MemCard Pro per-game VMC, and an emulator
memory card all map onto the same per-serial server storage.

Format reference: PSXSPX (problemkaputt.de) Memory Card Data Format.
Operates on the raw 128 KB image; callers use ``ps1_cards.extract_raw_card`` to
strip a ``.vmp`` header / validate first.
"""

from __future__ import annotations

import re
import struct

BLOCK_SIZE = 8192
FRAME_SIZE = 128
BLOCK_COUNT = 16
CARD_SIZE = BLOCK_SIZE * BLOCK_COUNT          # 131072
DATA_BLOCKS = BLOCK_COUNT - 1                 # 15 save slots

# Directory-frame state codes (byte 0 of the frame).
ST_FIRST = 0x51        # first (or only) block of a save
ST_MIDDLE = 0x52       # linked middle block
ST_LAST = 0x53         # linked last block
ST_FREE = 0xA0         # free / formatted

NO_NEXT = 0xFFFF

MC_MAGIC = b"MC"
HEADER_CHECKSUM = 0x0E  # 'M' ^ 'C'

# PS1 disc-serial prefixes (save filenames embed e.g. "BASLUS-00067...").
_PS1_SERIAL_RE = re.compile(
    r"(SLUS|SCUS|SLES|SCES|SLPS|SLPM|SCPS|SLKA|SCAJ|SLPN)"
    r"[-_ ]?(\d{3})[.]?(\d{2})",
    re.IGNORECASE,
)


def serial_from_filename(name: str) -> str | None:
    """Map a save filename (``BASLUS-00067ABCD``) to compact serial ``SLUS00067``."""
    m = _PS1_SERIAL_RE.search(name)
    if not m:
        return None
    return (m.group(1) + m.group(2) + m.group(3)).upper()


def _xor(frame: bytes) -> int:
    c = 0
    for b in frame:
        c ^= b
    return c


def _u16(buf: bytes, off: int) -> int:
    return struct.unpack_from("<H", buf, off)[0]


def parse_card(raw: bytes) -> list[tuple[str, bytes]]:
    """Split a raw 128 KB PS1 card into ``[(save_filename, save_data), ...]``.

    ``save_data`` is the concatenation of the save's 8 KB blocks in chain order.
    """
    if len(raw) != CARD_SIZE:
        raise ValueError(f"Unsupported PS1 card size: {len(raw)} (expected {CARD_SIZE})")
    if raw[:2] != MC_MAGIC:
        raise ValueError("Not a PS1 memory card (missing 'MC' magic)")

    block0 = raw[:BLOCK_SIZE]
    saves: list[tuple[str, bytes]] = []
    for i in range(1, BLOCK_COUNT):
        frame = block0[i * FRAME_SIZE : (i + 1) * FRAME_SIZE]
        if frame[0] != ST_FIRST:
            continue
        name = frame[0x0A : 0x0A + 20].split(b"\x00", 1)[0].decode("ascii", "replace")

        blocks: list[int] = []
        b = i
        guard = 0
        while 1 <= b <= DATA_BLOCKS and guard < BLOCK_COUNT:
            blocks.append(b)
            nxt = _u16(block0, b * FRAME_SIZE + 0x08)
            if nxt == NO_NEXT:
                break
            b = nxt + 1        # pointer is (block number - 1)
            guard += 1

        data = b"".join(raw[blk * BLOCK_SIZE : (blk + 1) * BLOCK_SIZE] for blk in blocks)
        saves.append((name, data))
    return saves


def format_empty_card() -> bytearray:
    """Return a freshly formatted, empty 128 KB PS1 card."""
    card = bytearray(CARD_SIZE)

    # Header frame: "MC" + checksum.
    card[0:2] = MC_MAGIC
    card[0x7F] = HEADER_CHECKSUM

    # Directory frames 1..15: free.
    for i in range(1, BLOCK_COUNT):
        fr = bytearray(FRAME_SIZE)
        fr[0] = ST_FREE
        struct.pack_into("<H", fr, 0x08, NO_NEXT)
        fr[0x7F] = _xor(fr[:0x7F])
        card[i * FRAME_SIZE : (i + 1) * FRAME_SIZE] = fr

    # Broken-sector list frames 16..35: no broken sectors.
    for i in range(16, 36):
        fr = bytearray(FRAME_SIZE)
        struct.pack_into("<I", fr, 0x00, 0xFFFFFFFF)
        struct.pack_into("<H", fr, 0x08, NO_NEXT)
        fr[0x7F] = _xor(fr[:0x7F])
        card[i * FRAME_SIZE : (i + 1) * FRAME_SIZE] = fr

    return card


def build_single_save_card(name: str, data: bytes) -> bytes:
    """Build a 128 KB card containing one save (blocks 1..N)."""
    nblocks = max(1, (len(data) + BLOCK_SIZE - 1) // BLOCK_SIZE)
    if nblocks > DATA_BLOCKS:
        raise ValueError("Save exceeds PS1 card capacity")
    padded = data + b"\x00" * (nblocks * BLOCK_SIZE - len(data))

    card = format_empty_card()
    for k in range(nblocks):
        blk = 1 + k
        card[blk * BLOCK_SIZE : (blk + 1) * BLOCK_SIZE] = padded[k * BLOCK_SIZE : (k + 1) * BLOCK_SIZE]

        fr = bytearray(FRAME_SIZE)
        if nblocks == 1:
            fr[0] = ST_FIRST
            nxt = NO_NEXT
        elif k == 0:
            fr[0] = ST_FIRST
            nxt = (blk + 1) - 1
        elif k == nblocks - 1:
            fr[0] = ST_LAST
            nxt = NO_NEXT
        else:
            fr[0] = ST_MIDDLE
            nxt = (blk + 1) - 1

        if k == 0:
            struct.pack_into("<I", fr, 0x04, nblocks * BLOCK_SIZE)
            nm = name.encode("ascii", "replace")[:20]
            fr[0x0A : 0x0A + len(nm)] = nm
        struct.pack_into("<H", fr, 0x08, nxt & 0xFFFF)
        fr[0x7F] = _xor(fr[:0x7F])
        card[blk * FRAME_SIZE : (blk + 1) * FRAME_SIZE] = fr

    return bytes(card)
