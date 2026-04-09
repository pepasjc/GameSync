"""GameCube memory card helpers.

Canonical on-server format
--------------------------
A full 8 MB GC memory card image (``card.raw``).  This is what MemCard Pro
stores locally and what the desktop sync tool uploads directly.

The Android client (Dolphin) uses per-game GCI files instead.  The server
handles the translation:

* **Upload from Android** (GCI):
    If a card image is already stored, the GCI bytes are inserted back into
    it (updating the game's data blocks).  If no card exists yet, the GCI is
    stored verbatim as ``card.gci`` until the desktop overwrites it with a
    full card image.

* **Download to Android** (GCI):
    ``GET /gc-card?format=gci`` — server extracts the game's GCI from the
    stored card image and returns it.

* **Download to desktop** (card image):
    ``GET /gc-card`` — returns the stored card image directly.

GC memory card binary layout
-----------------------------
Block size   : 0x2000 bytes (8 192)
Block 0      : header
Block 1      : primary directory (127 × 64-byte entries)
Block 2      : directory backup
Block 3/4    : BAM + BAM backup
Block 5+     : user data

Directory-entry byte offsets:
    [0:4]   game code (ASCII, e.g. "GM4E")
    [50:52] first_block (big-endian uint16) — absolute block index in card
    [52:54] block_count (big-endian uint16)
"""
from __future__ import annotations

import hashlib
import struct

_GC_BLOCK_SIZE = 0x2000
_GC_DIR1_OFFSET = 0x2000
_GC_DIR2_OFFSET = 0x4000
_GC_DENTRY_SIZE = 64
_GC_MAX_ENTRIES = 127
_GAMECODE_OFF = 0
_FIRST_BLOCK_OFF = 54   # 0x36  (filename field is 0x20 = 32 bytes, per Dolphin source)
_BLOCK_COUNT_OFF = 56   # 0x38

# A GCI file is at minimum 64-byte header + 1 data block
_MIN_GCI_SIZE = _GC_DENTRY_SIZE + _GC_BLOCK_SIZE
# Card images are much larger; use this as the detection threshold
_CARD_IMAGE_THRESHOLD = _MIN_GCI_SIZE * 10


def canonical_card_name() -> str:
    """Filename used when storing a full card image on the server."""
    return "card.raw"


def gc_code_from_title_id(title_id: str) -> str | None:
    """Return the 4-char game code from a ``GC_xxxx`` title ID, or None."""
    upper = title_id.upper()
    if upper.startswith("GC_") and len(upper) >= 7:
        return upper[3:7]
    return None


def is_gc_card_image(data: bytes) -> bool:
    """Return True if ``data`` is a full GC memory card image (not a GCI)."""
    n = len(data)
    return (
        n >= _GC_DIR1_OFFSET + _GC_MAX_ENTRIES * _GC_DENTRY_SIZE
        and n % _GC_BLOCK_SIZE == 0
        and n > _CARD_IMAGE_THRESHOLD
    )


def gc_extract_gci(card_bytes: bytes, game_code: str) -> bytes | None:
    """Extract one game's GCI from a full GC memory card image.

    Returns ``header (64 B) + raw data blocks`` for ``game_code``, or None.
    """
    code = game_code.upper().encode("ascii")
    if len(code) != 4:
        return None
    if len(card_bytes) < _GC_DIR1_OFFSET + _GC_MAX_ENTRIES * _GC_DENTRY_SIZE:
        return None

    for i in range(_GC_MAX_ENTRIES):
        off = _GC_DIR1_OFFSET + i * _GC_DENTRY_SIZE
        entry = card_bytes[off : off + _GC_DENTRY_SIZE]
        if len(entry) < _GC_DENTRY_SIZE or entry[0:4] == b"\xff\xff\xff\xff":
            continue
        if entry[_GAMECODE_OFF : _GAMECODE_OFF + 4] != code:
            continue
        first_block = struct.unpack_from(">H", entry, _FIRST_BLOCK_OFF)[0]
        block_count = struct.unpack_from(">H", entry, _BLOCK_COUNT_OFF)[0]
        data_start = first_block * _GC_BLOCK_SIZE
        data_end = data_start + block_count * _GC_BLOCK_SIZE
        if data_end > len(card_bytes):
            return None
        return entry + card_bytes[data_start:data_end]

    return None


def gc_insert_gci(card_bytes: bytes, gci_bytes: bytes) -> bytes | None:
    """Insert GCI bytes back into a full GC card image.

    The first 64 bytes of ``gci_bytes`` are the directory-entry header; the
    remainder is the raw block data.  Returns a modified copy of
    ``card_bytes``, or None on any error.
    """
    if len(gci_bytes) < _GC_DENTRY_SIZE:
        return None
    gci_header = gci_bytes[:_GC_DENTRY_SIZE]
    gci_data = gci_bytes[_GC_DENTRY_SIZE:]
    code = gci_header[_GAMECODE_OFF : _GAMECODE_OFF + 4]
    gci_block_count = struct.unpack_from(">H", gci_header, _BLOCK_COUNT_OFF)[0]

    if len(gci_data) != gci_block_count * _GC_BLOCK_SIZE:
        return None
    if len(card_bytes) < _GC_DIR1_OFFSET + _GC_MAX_ENTRIES * _GC_DENTRY_SIZE:
        return None

    card = bytearray(card_bytes)
    for i in range(_GC_MAX_ENTRIES):
        off = _GC_DIR1_OFFSET + i * _GC_DENTRY_SIZE
        entry = card[off : off + _GC_DENTRY_SIZE]
        if len(entry) < _GC_DENTRY_SIZE or bytes(entry[0:4]) == b"\xff\xff\xff\xff":
            continue
        if bytes(entry[_GAMECODE_OFF : _GAMECODE_OFF + 4]) != code:
            continue

        first_block = struct.unpack_from(">H", entry, _FIRST_BLOCK_OFF)[0]
        block_count = struct.unpack_from(">H", entry, _BLOCK_COUNT_OFF)[0]
        if block_count != gci_block_count:
            return None  # block-count mismatch would corrupt the card
        data_start = first_block * _GC_BLOCK_SIZE
        data_end = data_start + block_count * _GC_BLOCK_SIZE
        if data_end > len(card):
            return None

        card[data_start:data_end] = gci_data

        # Refresh both directory copies, preserving the card's own block alloc
        incoming = bytearray(gci_header)
        struct.pack_into(">H", incoming, _FIRST_BLOCK_OFF, first_block)
        struct.pack_into(">H", incoming, _BLOCK_COUNT_OFF, block_count)
        for dir_off in (_GC_DIR1_OFFSET, _GC_DIR2_OFFSET):
            e = dir_off + i * _GC_DENTRY_SIZE
            if e + _GC_DENTRY_SIZE <= len(card):
                card[e : e + _GC_DENTRY_SIZE] = incoming

        return bytes(card)

    return None


# ---------------------------------------------------------------------------
# Bundle helpers
# ---------------------------------------------------------------------------

def get_card_from_files(
    files: list[tuple[str, bytes]],
) -> tuple[str, bytes] | None:
    """Return the stored card image (or GCI if that is all that exists).

    Priority:
    1. ``card.raw``  — canonical full card image
    2. Any ``.raw`` file
    3. ``card.gci`` / any ``.gci``  — compact GCI (uploaded by Android before
       desktop ever synced)
    4. ``save.bin``  — legacy single-file raw upload
    5. Single-file bundle fallback
    """
    for path, data in files:
        if path == canonical_card_name():
            return path, data
    for path, data in files:
        if path.lower().endswith(".raw"):
            return path, data
    for path, data in files:
        if path.lower().endswith(".gci"):
            return path, data
    for path, data in files:
        if path == "save.bin":
            return path, data
    if len(files) == 1:
        return files[0]
    return None


def get_gci_from_files(
    files: list[tuple[str, bytes]],
    game_code: str | None = None,
) -> tuple[str, bytes] | None:
    """Return GCI bytes from a bundle, extracting from a card image if needed.

    If the stored file is a full card image, ``game_code`` (4 chars) is used
    to locate and extract the right entry.
    """
    match = get_card_from_files(files)
    if match is None:
        return None
    path, data = match
    if is_gc_card_image(data) and game_code:
        extracted = gc_extract_gci(data, game_code)
        if extracted is not None:
            return path, extracted
    return path, data


def card_hash_and_size(files: list[tuple[str, bytes]]) -> tuple[str, int] | None:
    """Return (sha256_hex, size) for the full card image."""
    match = get_card_from_files(files)
    if match is None:
        return None
    _, data = match
    return hashlib.sha256(data).hexdigest(), len(data)


def gci_hash_and_size(
    files: list[tuple[str, bytes]],
    game_code: str | None = None,
) -> tuple[str, int] | None:
    """Return (sha256_hex, size) for the GCI bytes."""
    match = get_gci_from_files(files, game_code)
    if match is None:
        return None
    _, gci = match
    return hashlib.sha256(gci).hexdigest(), len(gci)
