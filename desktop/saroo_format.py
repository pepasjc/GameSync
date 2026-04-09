"""Saroo SS_SAVE.BIN parser/writer and Mednafen Saturn save converter.

The Saroo is an ODE (Optical Drive Emulator) for the Sega Saturn.  It stores
per-game internal-memory saves in SS_SAVE.BIN on its SD card.

File layout (all big-endian):
    Slot 0      (0x00000 – 0x0FFFF): reserved — magic + game-ID index
    Slot 1      (0x10000 – 0x1FFFF): saves for game 1
    Slot 2      (0x20000 – 0x2FFFF): saves for game 2
    ...

Each slot is exactly SLOT_SIZE (0x10000) bytes.

The reserved slot (slot 0) layout:
    0x00–0x0F:  "Saroo Save File"   (16-byte magic, null-terminated US-ASCII)
    0x10–0x1F:  Game ID for slot 1  (16 bytes, US-ASCII, null-padded)
    0x20–0x2F:  Game ID for slot 2
    ...

Game slot layout (block size = 0x80 bytes):
    Block 0 (header block):
        0x00–0x07:  "SaroSave"          (magic)
        0x08–0x0B:  Total slot size     (uint32 BE) — always SLOT_SIZE
        0x0C–0x0D:  Block size          (uint16 BE) — always 0x80
        0x0E–0x0F:  Free block count    (uint16 BE)
        0x10–0x1F:  Unused
        0x20–0x2F:  Game ID             (16 bytes US-ASCII)
        0x30–0x3D:  Unused
        0x3E–0x3F:  First save block #  (uint16 BE; 0 = none)
        0x40–0x7F:  Block occupancy bitmap (64 bytes, LSB-first per byte)

    Archive-entry block (one per save, at block_num * BLOCK_SIZE):
        0x00–0x0A:  Archive name        (11 bytes US-ASCII, null-terminated)
        0x0B:       (padding/unused)
        0x0C–0x0F:  Save size bytes     (uint32 BE)
        0x10–0x19:  Comment             (10 bytes, shift-jis, null-terminated)
        0x1A:       0x00 padding
        0x1B:       Language code       (0=JP 1=EN 2=FR 3=DE 4=ES 5=IT)
        0x1C–0x1F:  Date code           (uint32 BE, minutes since 1980-01-01)
        0x3E–0x3F:  Next save block #   (uint16 BE; 0 = end of chain)
        0x40–0x7F:  Save-data block occupancy bitmap (64 bytes)

    Data blocks (raw save bytes, packed into BLOCK_SIZE chunks):
        each at block_num * BLOCK_SIZE, directly following the archive entry

Mednafen / Beetle Saturn internal saves are raw Saturn backup-memory images:
    32 768 bytes, block size 0x40, "BackUpRam Format" magic in block 0.

This module provides:
    parse_ss_save_bin(data)         -> list of GameSlot
    build_ss_save_bin(slots)        -> bytes
    mednafen_to_saroo_slot(raw)     -> bytes   (32KB raw -> 64KB Saroo slot)
    saroo_slot_to_mednafen(slot_data, game_id) -> bytes  (64KB slot -> 32KB raw)

References:
    https://github.com/tpunix/SAROO/blob/master/tools/savetool/sr_bup.c
    https://github.com/euan-forrester/save-file-converter/tree/main/frontend/src/save-formats/SegaSaturn
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SLOT_SIZE = 0x10000  # 65 536 bytes per slot
BLOCK_SIZE = 0x80  # 128 bytes per block within a slot
BLOCKS_PER_SLOT = SLOT_SIZE // BLOCK_SIZE  # 512

GAME_ID_LENGTH = 0x10  # 16 bytes
BITMAP_LENGTH = 64  # bytes in the occupancy bitmap

RESERVED_SLOT_MAGIC = b"Saroo Save File\x00"  # 16 bytes
SLOT_MAGIC = b"SaroSave"  # 8 bytes

# Offsets in the reserved slot (slot 0)
RESERVED_MAGIC_OFFSET = 0x00
RESERVED_GAME_ID_BASE = 0x10  # Game ID for slot N starts at N * GAME_ID_LENGTH

# Offsets in a game slot header block
SLOT_MAGIC_OFFSET = 0x00
SLOT_TOTAL_SIZE_OFFSET = 0x08
SLOT_BLOCK_SIZE_OFFSET = 0x0C
SLOT_FREE_BLOCKS_OFFSET = 0x0E
SLOT_GAME_ID_OFFSET = 0x20
SLOT_FIRST_SAVE_OFFSET = 0x3E
SLOT_BITMAP_OFFSET = 0x40

# Offsets in an archive entry block
ARCH_NAME_OFFSET = 0x00
ARCH_NAME_LENGTH = 11
ARCH_SAVE_SIZE_OFFSET = 0x0C
ARCH_COMMENT_OFFSET = 0x10
ARCH_COMMENT_LENGTH = 10
ARCH_LANG_OFFSET = 0x1B
ARCH_DATE_OFFSET = 0x1C
ARCH_NEXT_SAVE_OFFSET = 0x3E
ARCH_BITMAP_OFFSET = 0x40

NO_NEXT_SAVE = 0
NUM_RESERVED_BLOCKS = 1  # block 0 in each slot is the header

# Native Saturn backup-memory (mednafen) constants
SAT_INTERNAL_SIZE = 0x8000  # 32 768 bytes
SAT_BLOCK_SIZE = 0x40  # 64 bytes
SAT_MAGIC = b"BackUpRam Format"  # 16 bytes
SAT_TOTAL_BLOCKS = SAT_INTERNAL_SIZE // SAT_BLOCK_SIZE  # 512

SAT_BLOCK_ARCHIVE = 0x80000000
SAT_BLOCK_DATA = 0x00000000


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ArchiveEntry:
    """One save-file record extracted from a Saroo game slot."""

    name: str  # up to 11 US-ASCII chars
    comment: str  # up to 10 chars (shift-jis tolerated, kept as str)
    language_code: int  # 0-5 or 0xFF
    date_code: int  # minutes since 1980-01-01
    raw_data: bytes  # actual save payload


@dataclass
class GameSlot:
    """All saves belonging to one game in SS_SAVE.BIN."""

    game_id: str  # up to 16 US-ASCII chars
    saves: list[ArchiveEntry] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Bitmap helpers (LSB-first within each byte)
# ---------------------------------------------------------------------------


def _read_bitmap(bitmap: bytes, total_blocks: int) -> list[bool]:
    """Return list[bool] of length total_blocks (True = occupied)."""
    occupied = []
    for i in range(total_blocks):
        byte_idx = i >> 3
        bit_idx = i & 7
        if byte_idx < len(bitmap):
            occupied.append(bool((bitmap[byte_idx] >> bit_idx) & 1))
        else:
            occupied.append(False)
    return occupied


def _make_bitmap(occupied_set: set[int], num_bytes: int) -> bytes:
    """Build a bitmap byte array from a set of occupied block numbers."""
    buf = bytearray(num_bytes)
    for block in occupied_set:
        byte_idx = block >> 3
        bit_idx = block & 7
        if byte_idx < num_bytes:
            buf[byte_idx] |= 1 << bit_idx
    return bytes(buf)


# ---------------------------------------------------------------------------
# String helpers
# ---------------------------------------------------------------------------


def _read_cstr(data: bytes, offset: int, max_len: int, encoding: str = "ascii") -> str:
    """Read a null-terminated string, stopping at null or max_len."""
    raw = data[offset : offset + max_len]
    end = raw.find(b"\x00")
    if end >= 0:
        raw = raw[:end]
    try:
        return raw.decode(encoding, errors="replace")
    except Exception:
        return raw.decode("ascii", errors="replace")


def _write_cstr(s: str, max_len: int, encoding: str = "ascii") -> bytes:
    """Encode a string, null-pad to max_len bytes."""
    try:
        encoded = s.encode(encoding, errors="replace")
    except Exception:
        encoded = s.encode("ascii", errors="replace")
    encoded = encoded[:max_len]
    return encoded.ljust(max_len, b"\x00")


# ---------------------------------------------------------------------------
# SS_SAVE.BIN parser
# ---------------------------------------------------------------------------


def _read_game_id(reserved_slot: bytes, slot_num: int) -> str:
    """Read the game ID for slot_num from the reserved slot."""
    offset = slot_num * GAME_ID_LENGTH
    if offset + GAME_ID_LENGTH > len(reserved_slot):
        return ""
    return _read_cstr(reserved_slot, offset, GAME_ID_LENGTH)


def _parse_game_slot(slot_data: bytes) -> Optional[GameSlot]:
    """Parse one 64KB game slot and return a GameSlot, or None if invalid."""
    if len(slot_data) < BLOCK_SIZE:
        return None
    if slot_data[SLOT_MAGIC_OFFSET : SLOT_MAGIC_OFFSET + len(SLOT_MAGIC)] != SLOT_MAGIC:
        return None

    game_id = _read_cstr(slot_data, SLOT_GAME_ID_OFFSET, GAME_ID_LENGTH)
    block_size = struct.unpack_from(">H", slot_data, SLOT_BLOCK_SIZE_OFFSET)[0]
    if block_size == 0:
        block_size = BLOCK_SIZE

    first_save_block = struct.unpack_from(">H", slot_data, SLOT_FIRST_SAVE_OFFSET)[0]

    saves: list[ArchiveEntry] = []
    next_block = first_save_block

    while next_block != NO_NEXT_SAVE:
        arch_offset = next_block * block_size
        if arch_offset + block_size > len(slot_data):
            break  # corrupted / truncated

        arch = slot_data[arch_offset : arch_offset + block_size]

        name = _read_cstr(arch, ARCH_NAME_OFFSET, ARCH_NAME_LENGTH)
        save_size = struct.unpack_from(">I", arch, ARCH_SAVE_SIZE_OFFSET)[0]
        comment = _read_cstr(
            arch, ARCH_COMMENT_OFFSET, ARCH_COMMENT_LENGTH, "shift_jis"
        )
        lang = arch[ARCH_LANG_OFFSET]
        date_code = struct.unpack_from(">I", arch, ARCH_DATE_OFFSET)[0]
        next_block = struct.unpack_from(">H", arch, ARCH_NEXT_SAVE_OFFSET)[0]

        # Data occupancy bitmap for this save's payload
        save_bitmap_raw = arch[ARCH_BITMAP_OFFSET : ARCH_BITMAP_OFFSET + BITMAP_LENGTH]
        save_occupied = _read_bitmap(save_bitmap_raw, BLOCKS_PER_SLOT)

        # Collect data blocks sequentially — the Saroo tool lays them out
        # contiguously starting immediately after the archive entry block.
        raw_data = bytearray()
        for blk_idx in range(BLOCKS_PER_SLOT):
            if len(raw_data) >= save_size:
                break
            if save_occupied[blk_idx]:
                blk_off = blk_idx * block_size
                chunk = slot_data[blk_off : blk_off + block_size]
                raw_data.extend(chunk)

        saves.append(
            ArchiveEntry(
                name=name,
                comment=comment,
                language_code=lang,
                date_code=date_code,
                raw_data=bytes(raw_data[:save_size]),
            )
        )

    return GameSlot(game_id=game_id, saves=saves)


def parse_ss_save_bin(data: bytes) -> list[GameSlot]:
    """Parse SS_SAVE.BIN and return a list of GameSlot objects (one per game).

    Slot 0 is the reserved slot (magic + index).  Slots 1+ each contain saves
    for one game.  The file may be any multiple of SLOT_SIZE; slots beyond the
    file length are ignored.
    """
    if len(data) < SLOT_SIZE:
        return []
    reserved_slot = data[0:SLOT_SIZE]
    if reserved_slot[: len(RESERVED_SLOT_MAGIC)] != RESERVED_SLOT_MAGIC:
        return []

    total_slots = len(data) // SLOT_SIZE
    result: list[GameSlot] = []

    for slot_num in range(1, total_slots):
        # A slot is valid if the game-ID entry in the reserved slot is non-zero
        game_id_raw = reserved_slot[
            slot_num * GAME_ID_LENGTH : (slot_num + 1) * GAME_ID_LENGTH
        ]
        if not any(game_id_raw):
            break  # sequential layout — first empty entry ends the list

        slot_data = data[slot_num * SLOT_SIZE : (slot_num + 1) * SLOT_SIZE]
        parsed = _parse_game_slot(slot_data)
        if parsed and parsed.game_id:
            result.append(parsed)

    return result


# ---------------------------------------------------------------------------
# SS_SAVE.BIN writer
# ---------------------------------------------------------------------------


def _build_game_slot(slot: GameSlot) -> bytes:
    """Serialize one GameSlot into a SLOT_SIZE (64KB) byte blob."""
    buf = bytearray(SLOT_SIZE)

    # ---- layout allocation ----
    # Block 0: header
    # For each save: 1 archive-entry block + ceil(save_size / BLOCK_SIZE) data blocks
    next_block = NUM_RESERVED_BLOCKS

    save_layouts: list[
        tuple[int, int, int]
    ] = []  # (arch_block, data_start_block, num_data_blocks)
    for entry in slot.saves:
        num_data = (len(entry.raw_data) + BLOCK_SIZE - 1) // BLOCK_SIZE
        save_layouts.append((next_block, next_block + 1, num_data))
        next_block += 1 + num_data

    total_used = next_block
    free_blocks = BLOCKS_PER_SLOT - total_used

    # ---- header block ----
    slot_occupied: set[int] = set(range(total_used))
    slot_bitmap = _make_bitmap(slot_occupied, BITMAP_LENGTH)

    first_save_block = save_layouts[0][0] if save_layouts else NO_NEXT_SAVE

    buf[SLOT_MAGIC_OFFSET : SLOT_MAGIC_OFFSET + len(SLOT_MAGIC)] = SLOT_MAGIC
    struct.pack_into(">I", buf, SLOT_TOTAL_SIZE_OFFSET, SLOT_SIZE)
    struct.pack_into(">H", buf, SLOT_BLOCK_SIZE_OFFSET, BLOCK_SIZE)
    struct.pack_into(">H", buf, SLOT_FREE_BLOCKS_OFFSET, free_blocks)
    buf[SLOT_GAME_ID_OFFSET : SLOT_GAME_ID_OFFSET + GAME_ID_LENGTH] = _write_cstr(
        slot.game_id, GAME_ID_LENGTH
    )
    struct.pack_into(">H", buf, SLOT_FIRST_SAVE_OFFSET, first_save_block)
    buf[SLOT_BITMAP_OFFSET : SLOT_BITMAP_OFFSET + BITMAP_LENGTH] = slot_bitmap

    # ---- archive entry + data blocks ----
    for i, (entry, (arch_blk, data_blk, num_data)) in enumerate(
        zip(slot.saves, save_layouts)
    ):
        # Archive entry block
        arch_off = arch_blk * BLOCK_SIZE
        arch_buf = bytearray(BLOCK_SIZE)

        arch_buf[ARCH_NAME_OFFSET : ARCH_NAME_OFFSET + ARCH_NAME_LENGTH] = _write_cstr(
            entry.name, ARCH_NAME_LENGTH
        )
        struct.pack_into(">I", arch_buf, ARCH_SAVE_SIZE_OFFSET, len(entry.raw_data))
        arch_buf[ARCH_COMMENT_OFFSET : ARCH_COMMENT_OFFSET + ARCH_COMMENT_LENGTH] = (
            _write_cstr(entry.comment, ARCH_COMMENT_LENGTH, "shift_jis")
        )
        arch_buf[ARCH_LANG_OFFSET] = entry.language_code & 0xFF
        struct.pack_into(">I", arch_buf, ARCH_DATE_OFFSET, entry.date_code)

        # Next save block
        if i + 1 < len(slot.saves):
            next_arch = save_layouts[i + 1][0]
        else:
            next_arch = NO_NEXT_SAVE
        struct.pack_into(">H", arch_buf, ARCH_NEXT_SAVE_OFFSET, next_arch)

        # Data occupancy bitmap (blocks used for this save's payload)
        data_occupied: set[int] = set(range(data_blk, data_blk + num_data))
        arch_buf[ARCH_BITMAP_OFFSET : ARCH_BITMAP_OFFSET + BITMAP_LENGTH] = (
            _make_bitmap(data_occupied, BITMAP_LENGTH)
        )

        buf[arch_off : arch_off + BLOCK_SIZE] = arch_buf

        # Data blocks
        payload = entry.raw_data
        for j in range(num_data):
            off = (data_blk + j) * BLOCK_SIZE
            chunk = payload[j * BLOCK_SIZE : (j + 1) * BLOCK_SIZE]
            buf[off : off + len(chunk)] = chunk

    return bytes(buf)


def build_ss_save_bin(slots: list[GameSlot]) -> bytes:
    """Serialize a list of GameSlot objects into a complete SS_SAVE.BIN blob."""
    # Reserved slot
    reserved = bytearray(SLOT_SIZE)
    reserved[0 : len(RESERVED_SLOT_MAGIC)] = RESERVED_SLOT_MAGIC
    for i, slot in enumerate(slots, start=1):
        off = i * GAME_ID_LENGTH
        reserved[off : off + GAME_ID_LENGTH] = _write_cstr(slot.game_id, GAME_ID_LENGTH)

    parts: list[bytes] = [bytes(reserved)]
    for slot in slots:
        parts.append(_build_game_slot(slot))

    return b"".join(parts)


# ---------------------------------------------------------------------------
# Native Saturn (mednafen) <-> Saroo conversion helpers
# ---------------------------------------------------------------------------


@dataclass
class _NativeSave:
    name: str
    language_code: int
    comment: str
    date_code: int
    raw_data: bytes


def _parse_native_saturn(data: bytes) -> list[_NativeSave]:
    """Parse a raw Saturn internal memory image (32KB, block size 0x40).

    Returns list of save entries found.
    """
    if len(data) < SAT_INTERNAL_SIZE:
        data = data.ljust(SAT_INTERNAL_SIZE, b"\x00")

    saves: list[_NativeSave] = []
    total_blocks = SAT_INTERNAL_SIZE // SAT_BLOCK_SIZE

    for blk in range(2, total_blocks):  # blocks 0 and 1 are reserved
        offset = blk * SAT_BLOCK_SIZE
        block_type = struct.unpack_from(">I", data, offset)[0]
        if block_type != SAT_BLOCK_ARCHIVE:
            continue

        # Archive entry block
        name = _read_cstr(data, offset + 0x04, 11)
        lang = data[offset + 0x0F]
        comment = _read_cstr(data, offset + 0x10, 10, "shift_jis")
        date = struct.unpack_from(">I", data, offset + 0x1A)[0]
        size = struct.unpack_from(">I", data, offset + 0x1E)[0]

        # Block list at 0x22
        bl_offset = offset + 0x22
        block_list: list[int] = []
        bl_read_idx = 0
        cur_block_offset = bl_offset
        cur_data = data

        while True:
            if cur_block_offset + 2 > len(cur_data):
                break
            bnum = struct.unpack_from(">H", cur_data, cur_block_offset)[0]
            if bnum == 0x0000:
                cur_block_offset += 2
                break
            block_list.append(bnum)
            cur_block_offset += 2
            # If block list overflows into a data block:
            if cur_block_offset >= (blk * SAT_BLOCK_SIZE) + SAT_BLOCK_SIZE:
                next_bl_block = block_list[bl_read_idx]
                bl_read_idx += 1
                cur_block_offset = (
                    next_bl_block * SAT_BLOCK_SIZE + 0x04
                )  # skip block-type uint32

        # Gather raw data
        data_blocks = block_list[bl_read_idx:]
        segments = [cur_data[cur_block_offset:]]
        for db in data_blocks:
            db_off = db * SAT_BLOCK_SIZE + 0x04
            segments.append(data[db_off : db_off + SAT_BLOCK_SIZE - 0x04])

        raw = b"".join(segments)[:size]

        saves.append(
            _NativeSave(
                name=name,
                language_code=lang,
                comment=comment,
                date_code=date,
                raw_data=raw,
            )
        )

    return saves


def _build_native_saturn(saves: list[_NativeSave]) -> bytes:
    """Build a 32KB raw Saturn internal memory image from a list of saves."""
    buf = bytearray(SAT_INTERNAL_SIZE)

    # Block 0: "BackUpRam Format" repeated
    off = 0
    while off < SAT_BLOCK_SIZE:
        end = min(off + len(SAT_MAGIC), SAT_BLOCK_SIZE)
        buf[off:end] = SAT_MAGIC[: end - off]
        off += len(SAT_MAGIC)

    # Block 1: all zeros (already zero from bytearray init)

    current_block = 2  # first usable block

    for sv in saves:
        arch_block = current_block
        current_block += 1

        # Figure out data blocks needed
        # Available space in archive block for data (after block list end marker):
        # block list entry = 2 bytes each, end marker = 2 bytes
        # position of block list: 0x22
        arch_available = SAT_BLOCK_SIZE - 0x22 - 2  # -2 for end-marker
        data_block_payload = SAT_BLOCK_SIZE - 4  # minus 4-byte block type header

        if len(sv.raw_data) <= arch_available:
            data_blocks: list[int] = []
        else:
            extra = len(sv.raw_data) - arch_available
            num_data = (extra + data_block_payload - 1) // data_block_payload
            data_blocks = list(range(current_block, current_block + num_data))
            current_block += num_data

        # Write archive entry block
        arch_off = arch_block * SAT_BLOCK_SIZE
        struct.pack_into(">I", buf, arch_off + 0x00, SAT_BLOCK_ARCHIVE)
        buf[arch_off + 0x04 : arch_off + 0x0F] = _write_cstr(sv.name, 11)
        buf[arch_off + 0x0F] = sv.language_code & 0xFF
        buf[arch_off + 0x10 : arch_off + 0x1A] = _write_cstr(
            sv.comment, 10, "shift_jis"
        )
        struct.pack_into(">I", buf, arch_off + 0x1A, sv.date_code)
        struct.pack_into(">I", buf, arch_off + 0x1E, len(sv.raw_data))

        # Block list
        bl_off = arch_off + 0x22
        for db in data_blocks:
            struct.pack_into(">H", buf, bl_off, db)
            bl_off += 2
        struct.pack_into(">H", buf, bl_off, 0x0000)  # end marker
        bl_off += 2

        # Inline data (in the archive block, after end marker)
        inline_data = sv.raw_data[:arch_available] if data_blocks else sv.raw_data
        buf[bl_off : bl_off + len(inline_data)] = inline_data

        # Overflow data blocks
        remaining = sv.raw_data[arch_available:] if data_blocks else b""
        for db in data_blocks:
            db_off = db * SAT_BLOCK_SIZE
            struct.pack_into(">I", buf, db_off + 0x00, SAT_BLOCK_DATA)
            chunk = remaining[:data_block_payload]
            remaining = remaining[data_block_payload:]
            buf[db_off + 0x04 : db_off + 0x04 + len(chunk)] = chunk

    return bytes(buf)


def saroo_slot_to_mednafen(slot_data: bytes) -> bytes:
    """Convert a Saroo game slot (64KB) to a mednafen-compatible 32KB image.

    Parses the Saroo slot format, extracts all saves, then rebuilds them into
    the native Saturn backup-memory format that mednafen reads/writes directly.
    """
    parsed = _parse_game_slot(slot_data)
    if parsed is None or not parsed.saves:
        # Return an empty (but valid) 32KB native image
        return _build_native_saturn([])

    native_saves = [
        _NativeSave(
            name=s.name,
            language_code=s.language_code,
            comment=s.comment,
            date_code=s.date_code,
            raw_data=s.raw_data,
        )
        for s in parsed.saves
    ]
    return _build_native_saturn(native_saves)


def mednafen_to_saroo_slot(raw: bytes, game_id: str) -> bytes:
    """Convert a mednafen 32KB raw Saturn image to a Saroo game slot (64KB).

    raw:     raw backup-memory image (must be exactly SAT_INTERNAL_SIZE bytes
             or it will be zero-padded / truncated)
    game_id: the 16-char game ID that the Saroo BIOS writes for this disc
             (e.g. "T-10604G        " for a specific game)
    """
    if len(raw) > SAT_INTERNAL_SIZE:
        raw = raw[:SAT_INTERNAL_SIZE]
    elif len(raw) < SAT_INTERNAL_SIZE:
        raw = raw.ljust(SAT_INTERNAL_SIZE, b"\x00")

    native_saves = _parse_native_saturn(raw)
    entries = [
        ArchiveEntry(
            name=s.name,
            comment=s.comment,
            language_code=s.language_code,
            date_code=s.date_code,
            raw_data=s.raw_data,
        )
        for s in native_saves
    ]
    slot = GameSlot(game_id=game_id, saves=entries)
    return _build_game_slot(slot)


# ---------------------------------------------------------------------------
# Hash helpers
# ---------------------------------------------------------------------------


def slot_content_hash(slot: GameSlot) -> str:
    """Return a stable SHA-256 hex digest of the save *content* of a slot.

    Only covers save names + raw data, not metadata (date, comment).
    Used for change detection during sync.
    """
    import hashlib

    h = hashlib.sha256()
    h.update(slot.game_id.encode("ascii", errors="replace"))
    for s in sorted(slot.saves, key=lambda x: x.name):
        h.update(s.name.encode("ascii", errors="replace"))
        h.update(s.raw_data)
    return h.hexdigest()
