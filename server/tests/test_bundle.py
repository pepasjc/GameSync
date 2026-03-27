import hashlib

from app.models.save import BundleFile, SaveBundle
from app.services.bundle import BundleError, create_bundle, parse_bundle
import pytest


def _make_bundle(
    title_id: int = 0x0004000000055D00,
    timestamp: int = 1700000000,
    files: list[tuple[str, bytes]] | None = None,
) -> SaveBundle:
    if files is None:
        files = [("main", b"save data here")]
    bundle_files = [
        BundleFile(
            path=path,
            size=len(data),
            sha256=hashlib.sha256(data).digest(),
            data=data,
        )
        for path, data in files
    ]
    return SaveBundle(title_id=title_id, timestamp=timestamp, files=bundle_files)


class TestBundleRoundTrip:
    def test_single_file(self):
        original = _make_bundle()
        data = create_bundle(original)
        parsed = parse_bundle(data)

        assert parsed.title_id == original.title_id
        assert parsed.timestamp == original.timestamp
        assert len(parsed.files) == 1
        assert parsed.files[0].path == "main"
        assert parsed.files[0].data == b"save data here"

    def test_multiple_files(self):
        original = _make_bundle(
            files=[
                ("main", b"main save"),
                ("backup", b"backup save"),
                ("extra/data.bin", b"\x00\x01\x02\x03"),
            ]
        )
        data = create_bundle(original)
        parsed = parse_bundle(data)

        assert len(parsed.files) == 3
        assert parsed.files[0].path == "main"
        assert parsed.files[0].data == b"main save"
        assert parsed.files[1].path == "backup"
        assert parsed.files[1].data == b"backup save"
        assert parsed.files[2].path == "extra/data.bin"
        assert parsed.files[2].data == b"\x00\x01\x02\x03"

    def test_empty_files(self):
        original = _make_bundle(files=[])
        data = create_bundle(original)
        parsed = parse_bundle(data)

        assert len(parsed.files) == 0

    def test_large_file(self):
        big_data = b"\xff" * 1024 * 512  # 512KB
        original = _make_bundle(files=[("main", big_data)])
        data = create_bundle(original)
        parsed = parse_bundle(data)

        assert parsed.files[0].data == big_data

    def test_title_id_preserved(self):
        original = _make_bundle(title_id=0x00040000001B5000)
        data = create_bundle(original)
        parsed = parse_bundle(data)

        assert parsed.title_id == 0x00040000001B5000
        assert parsed.title_id_hex == "00040000001B5000"


class TestBundleErrors:
    def test_too_small(self):
        with pytest.raises(BundleError, match="too small"):
            parse_bundle(b"3DSS")

    def test_bad_magic(self):
        with pytest.raises(BundleError, match="Invalid magic"):
            parse_bundle(b"XXXX" + b"\x00" * 24)

    def test_bad_version(self):
        import struct

        data = b"3DSS" + struct.pack("<I", 99) + b"\x00" * 20
        with pytest.raises(BundleError, match="Unsupported bundle version"):
            parse_bundle(data)

    def test_corrupted_compressed_data(self):
        """Corrupting compressed data should cause decompression failure."""
        original = _make_bundle()
        data = bytearray(create_bundle(original, compress=True))
        # Corrupt the last byte of compressed data
        data[-1] ^= 0xFF
        with pytest.raises(BundleError, match="Decompression failed"):
            parse_bundle(bytes(data))

    def test_corrupted_hash_uncompressed(self):
        """Corrupting uncompressed file data should cause hash mismatch."""
        original = _make_bundle()
        data = bytearray(create_bundle(original, compress=False))
        # Corrupt the last byte of file data
        data[-1] ^= 0xFF
        with pytest.raises(BundleError, match="Hash mismatch"):
            parse_bundle(bytes(data))
