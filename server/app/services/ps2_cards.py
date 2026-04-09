from __future__ import annotations

import hashlib
from array import array

PS2_PAGE_SIZE = 512
PS2_PAGES_PER_CARD = 16384
PS2_SPARE_SIZE = 16
PS2_MC2_SIZE = PS2_PAGE_SIZE * PS2_PAGES_PER_CARD
PS2_PS2_SIZE = (PS2_PAGE_SIZE + PS2_SPARE_SIZE) * PS2_PAGES_PER_CARD

_PARITY_TABLE: list[int]
_COLUMN_PARITY_MASKS: list[int]


def _div_round_up(a: int, b: int) -> int:
    return (a + b - 1) // b


def _parity_byte(value: int) -> int:
    value ^= value >> 1
    value ^= value >> 2
    value ^= value >> 4
    return value & 1


def _make_ecc_tables() -> tuple[list[int], list[int]]:
    parity_table = [_parity_byte(byte) for byte in range(256)]
    column_masks = [0] * 256
    cp_masks = [0x55, 0x33, 0x0F, 0x00, 0xAA, 0xCC, 0xF0]

    for byte in range(256):
        mask = 0
        for idx, cp_mask in enumerate(cp_masks):
            mask |= parity_table[byte & cp_mask] << idx
        column_masks[byte] = mask

    return parity_table, column_masks


_PARITY_TABLE, _COLUMN_PARITY_MASKS = _make_ecc_tables()


def canonical_card_name() -> str:
    """Return the canonical on-server filename for PS2 MemCard Pro cards."""
    return "card.mc2"


def normalize_ps2_card_format(card_format: str) -> str:
    normalized = card_format.strip().lower()
    if normalized not in {"mc2", "ps2"}:
        raise ValueError(f"Unsupported PS2 card format: {card_format}")
    return normalized


def _calculate_ecc_chunk(chunk: bytes) -> bytes:
    """Calculate the 3-byte Hamming code for one 128-byte PS2 page chunk."""
    if len(chunk) != 128:
        raise ValueError(f"PS2 ECC expects 128-byte chunks, got {len(chunk)}")

    data = array("B", chunk)
    column_parity = 0x77
    line_parity_0 = 0x7F
    line_parity_1 = 0x7F

    for idx, byte in enumerate(data):
        column_parity ^= _COLUMN_PARITY_MASKS[byte]
        if _PARITY_TABLE[byte]:
            line_parity_0 ^= ~idx
            line_parity_1 ^= idx

    return bytes(
        (
            column_parity & 0xFF,
            line_parity_0 & 0x7F,
            line_parity_1 & 0x7F,
        )
    )


def calculate_page_spare(page: bytes) -> bytes:
    """Build the 16-byte spare/ECC area for one 512-byte PS2 card page."""
    if len(page) != PS2_PAGE_SIZE:
        raise ValueError(f"Invalid PS2 page size: {len(page)}")

    ecc = b"".join(
        _calculate_ecc_chunk(page[offset : offset + 128])
        for offset in range(0, PS2_PAGE_SIZE, 128)
    )
    return ecc + (b"\x00" * (PS2_SPARE_SIZE - len(ecc)))


def detect_ps2_card_format(data: bytes) -> str:
    """Detect whether bytes represent a canonical mc2 card or an ECC ps2 card."""
    size = len(data)
    if size == PS2_MC2_SIZE:
        return "mc2"
    if size == PS2_PS2_SIZE:
        return "ps2"
    raise ValueError(f"Unsupported PS2 card size: {size}")


def strip_ecc(image: bytes) -> bytes:
    """Convert a `.ps2` card image into canonical `.mc2` bytes."""
    if detect_ps2_card_format(image) != "ps2":
        raise ValueError("PS2 ECC stripping expects a .ps2 image")

    raw = bytearray(PS2_MC2_SIZE)
    src = 0
    dst = 0
    raw_page_size = PS2_PAGE_SIZE + PS2_SPARE_SIZE

    while src < len(image):
        raw[dst : dst + PS2_PAGE_SIZE] = image[src : src + PS2_PAGE_SIZE]
        src += raw_page_size
        dst += PS2_PAGE_SIZE

    return bytes(raw)


def add_ecc(image: bytes) -> bytes:
    """Convert canonical `.mc2` bytes into a PCSX2/Aether-compatible `.ps2` image."""
    if detect_ps2_card_format(image) != "mc2":
        raise ValueError("PS2 ECC generation expects an .mc2 image")

    pages: list[bytes] = []
    for offset in range(0, len(image), PS2_PAGE_SIZE):
        page = image[offset : offset + PS2_PAGE_SIZE]
        pages.append(page + calculate_page_spare(page))
    return b"".join(pages)


def extract_canonical_card(data: bytes) -> bytes:
    """Return canonical `.mc2` bytes from either `.mc2` or `.ps2` input."""
    fmt = detect_ps2_card_format(data)
    if fmt == "mc2":
        return data
    return strip_ecc(data)


def convert_card_for_format(data: bytes, card_format: str) -> bytes:
    """Return card bytes in the requested download/upload format."""
    normalized = normalize_ps2_card_format(card_format)
    canonical = extract_canonical_card(data)
    if normalized == "mc2":
        return canonical
    return add_ecc(canonical)


def get_canonical_card_from_files(files: list[tuple[str, bytes]]) -> tuple[str, bytes] | None:
    """Find the stored PS2 card in a save bundle and return canonical `.mc2` bytes.

    We prefer already-canonical `.mc2` files, but we also accept legacy `.ps2`
    payloads or older single-file raw uploads stored as `save.bin`.
    """
    preferred_names = ("card.mc2", "save.mc2", "save.bin")
    for preferred in preferred_names:
        for path, data in files:
            if path == preferred:
                return preferred, extract_canonical_card(data)

    for path, data in files:
        lower = path.lower()
        if lower.endswith(".mc2") or lower.endswith(".ps2"):
            return path, extract_canonical_card(data)

    if len(files) == 1:
        path, data = files[0]
        return path, extract_canonical_card(data)

    return None


def card_hash_and_size(files: list[tuple[str, bytes]], card_format: str = "mc2") -> tuple[str, int] | None:
    match = get_canonical_card_from_files(files)
    if match is None:
        return None
    _, canonical = match
    rendered = convert_card_for_format(canonical, card_format)
    return hashlib.sha256(rendered).hexdigest(), len(rendered)
