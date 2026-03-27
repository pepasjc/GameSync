from __future__ import annotations

import hashlib
import struct

PS1_PREFIXES: frozenset[str] = frozenset(
    {
        "SCUS",
        "SLUS",
        "SCES",
        "SLES",
        "SCPS",
        "SLPS",
        "SLPM",
    }
)

VMP_MAGIC = b"\x00PMV"
MC_MAGIC = b"MC\x00\x00"
VMP_HEADER_SIZE = 0x80
RAW_CARD_SIZE = 0x20000
VMP_SIZE = VMP_HEADER_SIZE + RAW_CARD_SIZE
_VMP_MAGIC_LE = 0x564D5000
_VMP_OFFSET_LE = VMP_HEADER_SIZE
_VMP_HASH_OFFSET = 0x20
_VMP_SALT_BASE = bytes.fromhex(
    "71b61141282c3eb8240401760922122f7543deb55d5f06ad"
) + bytes(44)


def is_ps1_title_id(title_id: str) -> bool:
    upper = title_id.upper()
    return len(upper) >= 4 and upper[:4] in PS1_PREFIXES


def extract_raw_card(data: bytes) -> bytes:
    if data.startswith(MC_MAGIC):
        if len(data) != RAW_CARD_SIZE:
            raise ValueError(f"Invalid raw PS1 card size: {len(data)}")
        return data
    if data.startswith(VMP_MAGIC):
        if len(data) < VMP_SIZE:
            raise ValueError(f"Invalid VMP size: {len(data)}")
        raw = data[VMP_HEADER_SIZE : VMP_HEADER_SIZE + RAW_CARD_SIZE]
        if not raw.startswith(MC_MAGIC):
            raise ValueError("VMP payload does not contain a valid PS1 card")
        return raw
    raise ValueError("Unsupported PS1 card format")


def slot_raw_name(slot: int) -> str:
    return f"slot{slot}.mcd"


def slot_vmp_name(slot: int) -> str:
    return f"SCEVMC{slot}.VMP"


def get_slot_raw_from_files(files: list[tuple[str, bytes]], slot: int) -> bytes | None:
    preferred = slot_raw_name(slot)
    legacy = slot_vmp_name(slot)
    for path, data in files:
        if path == preferred:
            return extract_raw_card(data)
    for path, data in files:
        if path == legacy:
            return extract_raw_card(data)
    return None


def ensure_raw_slot_files(files: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
    paths = {path for path, _ in files}
    extra: list[tuple[str, bytes]] = []
    for slot in (0, 1):
        raw_name = slot_raw_name(slot)
        if raw_name in paths:
            continue
        legacy_name = slot_vmp_name(slot)
        legacy_data = next((data for path, data in files if path == legacy_name), None)
        if legacy_data is None:
            continue
        extra.append((raw_name, extract_raw_card(legacy_data)))
    return files + extra


def slot_hash_and_size(files: list[tuple[str, bytes]], slot: int) -> tuple[str, int] | None:
    raw = get_slot_raw_from_files(files, slot)
    if raw is None:
        return None
    return hashlib.sha256(raw).hexdigest(), len(raw)


def psp_visible_files(files: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
    hidden = {slot_raw_name(0), slot_raw_name(1)}
    return [(path, data) for path, data in files if path not in hidden]


def psp_visible_stats(files: list[tuple[str, bytes]]) -> tuple[str, int, int]:
    visible = psp_visible_files(files)
    all_data = b"".join(data for _, data in visible)
    return hashlib.sha256(all_data).hexdigest(), sum(len(data) for _, data in visible), len(visible)


def create_vmp(raw: bytes) -> bytes:
    raw = extract_raw_card(raw)
    vmp = bytearray(VMP_SIZE)
    struct.pack_into("<I", vmp, 0x00, _VMP_MAGIC_LE)
    struct.pack_into("<I", vmp, 0x04, _VMP_OFFSET_LE)
    vmp[VMP_HEADER_SIZE:] = raw
    # Real-world PSP/Vita-generated VMPs for this project's saves match this
    # HMAC-style signature derivation exactly. This is the compatibility path
    # that reproduces known-good SCEVMC*.VMP files byte-for-byte.
    inner_salt = bytes(b ^ 0x36 for b in _VMP_SALT_BASE)
    outer_salt = bytes(b ^ 0x5C for b in _VMP_SALT_BASE)
    vmp[_VMP_HASH_OFFSET : _VMP_HASH_OFFSET + 0x14] = b"\x00" * 0x14
    inner_hash = hashlib.sha1(inner_salt + bytes(vmp)).digest()
    vmp[_VMP_HASH_OFFSET : _VMP_HASH_OFFSET + 0x14] = hashlib.sha1(outer_salt + inner_hash).digest()
    return bytes(vmp)
