"""Parse and create the 3DSS binary save bundle format.

Bundle format v1 (uncompressed):
  [4B]  Magic: "3DSS"
  [4B]  Version: 1 (uint32 LE)
  [8B]  Title ID (uint64 BE)
  [4B]  Timestamp - unix epoch (uint32 LE)
  [4B]  File count (uint32 LE)
  [4B]  Total data size (uint32 LE)
  -- File table (for each file): --
    [2B]  Path length (uint16 LE)
    [NB]  Path (UTF-8)
    [4B]  File size (uint32 LE)
    [32B] SHA-256 hash
  -- File data (for each file, same order): --
    [NB]  Raw file data

Bundle format v2 (compressed):
  [4B]  Magic: "3DSS"
  [4B]  Version: 2 (uint32 LE)
  [8B]  Title ID (uint64 BE)
  [4B]  Timestamp - unix epoch (uint32 LE)
  [4B]  File count (uint32 LE)
  [4B]  Uncompressed payload size (uint32 LE)
  -- Zlib compressed payload: --
    File table + file data (same format as v1 payload)

Bundle format v3 (string title_id, compressed):
  [4B]  Magic: "3DSS"
  [4B]  Version: 3 (uint32 LE)
  [16B] Title ID string (ASCII, null-padded; e.g. "ULUS10000\0\0\0\0\0\0\0")
  [4B]  Timestamp - unix epoch (uint32 LE)
  [4B]  File count (uint32 LE)
  [4B]  Uncompressed payload size (uint32 LE)
  -- Zlib compressed payload: --
    File table + file data (same format as v1/v2 payload)

  Used for PSP and PS Vita saves where product codes are ASCII strings
  (e.g. ULUS10000, PCSE00082) rather than 64-bit integer title IDs.
"""

from __future__ import annotations

import hashlib
import struct
import zlib

from app.models.save import (
    BUNDLE_MAGIC,
    BUNDLE_VERSION,
    BUNDLE_VERSION_COMPRESSED,
    BUNDLE_VERSION_V3,
    BUNDLE_VERSION_V4,
    BUNDLE_VERSION_V5,
    BundleFile,
    SaveBundle,
)


class BundleError(Exception):
    pass


def _parse_payload(data: bytes, file_count: int) -> list[BundleFile]:
    """Parse the file table and file data from payload bytes."""
    offset = 0
    files: list[BundleFile] = []

    # File table
    for _ in range(file_count):
        if offset + 2 > len(data):
            raise BundleError("Truncated file table")

        (path_len,) = struct.unpack_from("<H", data, offset)
        offset += 2

        if offset + path_len > len(data):
            raise BundleError("Truncated file path")
        path = data[offset : offset + path_len].decode("utf-8")
        offset += path_len

        if offset + 4 > len(data):
            raise BundleError("Truncated file size")
        (file_size,) = struct.unpack_from("<I", data, offset)
        offset += 4

        if offset + 32 > len(data):
            raise BundleError("Truncated file hash")
        sha256 = data[offset : offset + 32]
        offset += 32

        files.append(BundleFile(path=path, size=file_size, sha256=sha256))

    # File data
    for f in files:
        if offset + f.size > len(data):
            raise BundleError(f"Truncated file data for {f.path}")
        f.data = data[offset : offset + f.size]
        offset += f.size

        # Verify hash
        actual_hash = hashlib.sha256(f.data).digest()
        if actual_hash != f.sha256:
            raise BundleError(
                f"Hash mismatch for {f.path}: "
                f"expected {f.sha256.hex()}, got {actual_hash.hex()}"
            )

    return files


def parse_bundle(data: bytes) -> SaveBundle:
    """Parse a binary save bundle into a SaveBundle object.

    Supports v1 (uncompressed), v2 (zlib compressed), and string title_id
    variants v3/v4/v5 for PSP/Vita/PS3 saves.
    """
    if len(data) < 28:
        raise BundleError("Bundle too small for header")

    offset = 0

    magic = data[offset : offset + 4]
    if magic != BUNDLE_MAGIC:
        raise BundleError(f"Invalid magic: {magic!r}")
    offset += 4

    (version,) = struct.unpack_from("<I", data, offset)
    offset += 4

    if version in (BUNDLE_VERSION, BUNDLE_VERSION_COMPRESSED):
        # v1/v2: 8-byte integer title_id
        if offset + 8 > len(data):
            raise BundleError("Truncated title_id")
        (title_id_int,) = struct.unpack_from(">Q", data, offset)
        offset += 8
        title_id_str = ""

    elif version == BUNDLE_VERSION_V5:
        # v5: 64-byte ASCII null-padded string title_id
        if offset + 64 > len(data):
            raise BundleError("Truncated v5 title_id string")
        raw = data[offset : offset + 64]
        offset += 64
        title_id_str = raw.rstrip(b"\x00").decode("ascii").upper()
        title_id_int = 0

    elif version == BUNDLE_VERSION_V4:
        # v4: 32-byte ASCII null-padded string title_id
        if offset + 32 > len(data):
            raise BundleError("Truncated v4 title_id string")
        raw = data[offset : offset + 32]
        offset += 32
        title_id_str = raw.rstrip(b"\x00").decode("ascii").upper()
        title_id_int = 0

    elif version == BUNDLE_VERSION_V3:
        # v3: 16-byte ASCII null-padded string title_id (legacy)
        if offset + 16 > len(data):
            raise BundleError("Truncated v3 title_id string")
        raw = data[offset : offset + 16]
        offset += 16
        title_id_str = raw.rstrip(b"\x00").decode("ascii").upper()
        title_id_int = 0

    else:
        raise BundleError(f"Unsupported bundle version: {version}")

    if offset + 12 > len(data):
        raise BundleError("Truncated bundle header")

    (timestamp,) = struct.unpack_from("<I", data, offset)
    offset += 4

    (file_count,) = struct.unpack_from("<I", data, offset)
    offset += 4

    (size_field,) = struct.unpack_from("<I", data, offset)
    offset += 4

    # Get payload
    if version == BUNDLE_VERSION:
        # v1: uncompressed
        payload = data[offset:]
    else:
        # v2/v3: zlib compressed
        compressed_payload = data[offset:]
        try:
            payload = zlib.decompress(compressed_payload)
        except zlib.error as e:
            raise BundleError(f"Decompression failed: {e}")

        if len(payload) != size_field:
            raise BundleError(
                f"Decompressed size mismatch: expected {size_field}, got {len(payload)}"
            )

    files = _parse_payload(payload, file_count)

    return SaveBundle(
        title_id=title_id_int,
        timestamp=timestamp,
        files=files,
        title_id_str=title_id_str,
    )


def _build_payload(bundle: SaveBundle) -> bytes:
    """Build the file table + file data payload."""
    parts: list[bytes] = []

    # File table
    for f in bundle.files:
        path_bytes = f.path.encode("utf-8")
        parts.append(struct.pack("<H", len(path_bytes)))
        parts.append(path_bytes)
        parts.append(struct.pack("<I", f.size))
        parts.append(f.sha256)

    # File data
    for f in bundle.files:
        parts.append(f.data)

    return b"".join(parts)


def create_bundle(bundle: SaveBundle, compress: bool = True) -> bytes:
    """Serialize a SaveBundle into the binary bundle format.

    Automatically uses v3 format for PSP/Vita bundles (title_id_str set),
    v2 for 3DS/DS bundles (compressed), or v1 (uncompressed).
    """
    payload = _build_payload(bundle)
    parts: list[bytes] = []
    parts.append(BUNDLE_MAGIC)

    if bundle.title_id_str:
        # v4/v5: string title_id, always compressed
        compressed_payload = zlib.compress(payload, level=6)
        raw_tid = bundle.title_id_str.encode("ascii")
        if len(raw_tid) <= 31:
            parts.append(struct.pack("<I", BUNDLE_VERSION_V4))
            tid_bytes = raw_tid[:31].ljust(32, b"\x00")
        else:
            parts.append(struct.pack("<I", BUNDLE_VERSION_V5))
            tid_bytes = raw_tid[:63].ljust(64, b"\x00")
        parts.append(tid_bytes)
        parts.append(struct.pack("<I", bundle.timestamp))
        parts.append(struct.pack("<I", len(bundle.files)))
        parts.append(struct.pack("<I", len(payload)))
        parts.append(compressed_payload)

    elif compress:
        # v2: integer title_id, compressed
        compressed_payload = zlib.compress(payload, level=6)
        parts.append(struct.pack("<I", BUNDLE_VERSION_COMPRESSED))
        parts.append(struct.pack(">Q", bundle.title_id))
        parts.append(struct.pack("<I", bundle.timestamp))
        parts.append(struct.pack("<I", len(bundle.files)))
        parts.append(struct.pack("<I", len(payload)))
        parts.append(compressed_payload)

    else:
        # v1: integer title_id, uncompressed
        parts.append(struct.pack("<I", BUNDLE_VERSION))
        parts.append(struct.pack(">Q", bundle.title_id))
        parts.append(struct.pack("<I", bundle.timestamp))
        parts.append(struct.pack("<I", len(bundle.files)))
        parts.append(struct.pack("<I", bundle.total_size))
        parts.append(payload)

    return b"".join(parts)
