"""Tests for the NCSD/NCCH "is this cart image already decrypted?" probe."""

import struct

from app.services import ctr_rom

MEDIA_UNIT = 0x200
PARTITION_OFFSET = 0x4000
EXHEADER_OFFSET = 0x200          # relative to the NCCH header
EXHEADER_SIZE = 0x400
EXEFS_MEDIA_OFFSET = 0x8         # 0x1000 bytes into the partition
EXEFS_MEDIA_SIZE = 0x4
ROMFS_MEDIA_OFFSET = 0x10        # 0x2000 bytes into the partition
PARTITION_MEDIA_SIZE = 0x20      # 0x4000 bytes


def _plaintext_exefs_header() -> bytes:
    header = bytearray(0x200)
    header[0x00:0x08] = b'.code\0\0\0'
    struct.pack_into('<II', header, 0x08, 0, 0x100)
    header[0x10:0x18] = b'icon\0\0\0\0'
    struct.pack_into('<II', header, 0x18, 0x200, 0x40)
    return bytes(header)


def _encrypted_blob(size: int, seed: int) -> bytes:
    # Deterministic high-entropy filler — every byte has the high bit set so it
    # can never be mistaken for the ASCII names/titles the heuristic looks for.
    return bytes(0x80 | ((seed + i * 37) & 0x7F) for i in range(size))


def cxi(index: int, plaintext: bool = True, no_crypto_flag: bool = False) -> dict:
    """An executable partition: ExHeader + ExeFS + RomFS."""
    return {'index': index, 'kind': 'cxi', 'plaintext': plaintext, 'no_crypto': no_crypto_flag}


def cfa(index: int, plaintext: bool = False) -> dict:
    """A manual / update / DLP partition: RomFS only, no ExeFS, no ExHeader.

    Real "decrypted" dumps routinely leave these encrypted, which is what makes
    them worth modelling.
    """
    return {'index': index, 'kind': 'cfa', 'plaintext': plaintext, 'no_crypto': False}


def _build_cart_from(specs: list[dict]) -> bytes:
    """Synthesize a minimal but structurally valid NCSD cart image."""
    total = PARTITION_OFFSET + len(specs) * PARTITION_MEDIA_SIZE * MEDIA_UNIT
    image = bytearray(total)
    image[0x100:0x104] = ctr_rom.NCSD_MAGIC
    struct.pack_into('<I', image, 0x104, total // MEDIA_UNIT)

    for slot, spec in enumerate(specs):
        index = spec['index']
        is_cxi = spec['kind'] == 'cxi'
        part_offset = PARTITION_OFFSET + slot * PARTITION_MEDIA_SIZE * MEDIA_UNIT
        struct.pack_into(
            '<II', image, 0x120 + index * 8,
            part_offset // MEDIA_UNIT, PARTITION_MEDIA_SIZE,
        )

        ncch = bytearray(0x200)
        ncch[0x100:0x104] = ctr_rom.NCCH_MAGIC
        flags = bytearray(8)
        if spec['no_crypto']:
            flags[7] = ctr_rom.FLAG_NO_CRYPTO
        else:
            flags[3] = 0x01                      # pretend: 7.x key slot
            flags[7] = ctr_rom.FLAG_FIXED_CRYPTO_KEY
        ncch[0x188:0x190] = bytes(flags)
        if is_cxi:
            struct.pack_into('<I', ncch, 0x180, EXHEADER_SIZE)
            struct.pack_into('<II', ncch, 0x1A0, EXEFS_MEDIA_OFFSET, EXEFS_MEDIA_SIZE)
        struct.pack_into('<II', ncch, 0x1B0, ROMFS_MEDIA_OFFSET, 0x8)
        image[part_offset:part_offset + 0x200] = ncch

        exheader_at = part_offset + EXHEADER_OFFSET
        exefs_at = part_offset + EXEFS_MEDIA_OFFSET * MEDIA_UNIT
        romfs_at = part_offset + ROMFS_MEDIA_OFFSET * MEDIA_UNIT
        if spec['plaintext']:
            if is_cxi:
                image[exheader_at:exheader_at + 8] = b'AppTitl\0'
                image[exefs_at:exefs_at + 0x200] = _plaintext_exefs_header()
            image[romfs_at:romfs_at + len(ctr_rom.IVFC_MAGIC)] = ctr_rom.IVFC_MAGIC
        else:
            if is_cxi:
                image[exheader_at:exheader_at + EXHEADER_SIZE] = _encrypted_blob(EXHEADER_SIZE, index + 1)
                image[exefs_at:exefs_at + 0x200] = _encrypted_blob(0x200, index + 9)
            image[romfs_at:romfs_at + 0x200] = _encrypted_blob(0x200, index + 17)

    return bytes(image)


def _build_cart(decrypted: bool, no_crypto_flag: bool, partitions: int = 1) -> bytes:
    """Convenience wrapper: ``partitions`` identical CXI partitions."""
    return _build_cart_from(
        [cxi(i, plaintext=decrypted, no_crypto_flag=no_crypto_flag) for i in range(partitions)]
    )


def _write(tmp_path, name: str, data: bytes):
    path = tmp_path / name
    path.write_bytes(data)
    return path


class TestProbe:
    def test_non_ncsd_file_is_not_a_cart(self, tmp_path):
        info = ctr_rom.probe(_write(tmp_path, "junk.3ds", b'CARTROM'))
        assert info.is_ncsd is False
        assert info.decrypted is False
        assert info.needs_flag_patch is False

    def test_missing_file_reports_unreadable(self, tmp_path):
        info = ctr_rom.probe(tmp_path / "nope.3ds")
        assert info.is_ncsd is False
        assert "unreadable" in info.detail

    def test_encrypted_cart(self, tmp_path):
        info = ctr_rom.probe(_write(tmp_path, "enc.3ds", _build_cart(decrypted=False, no_crypto_flag=False)))
        assert info.is_ncsd is True
        assert info.decrypted is False
        assert info.needs_flag_patch is False

    def test_decrypted_cart_with_flags_set(self, tmp_path):
        info = ctr_rom.probe(_write(tmp_path, "dec.3ds", _build_cart(decrypted=True, no_crypto_flag=True)))
        assert info.decrypted is True
        assert info.flags_marked is True
        assert info.needs_flag_patch is False

    def test_decrypted_cart_with_stale_flags_is_detected(self, tmp_path):
        """The real-world failure case: plaintext data, headers still say encrypted."""
        info = ctr_rom.probe(_write(tmp_path, "dec.3ds", _build_cart(decrypted=True, no_crypto_flag=False)))
        assert info.decrypted is True
        assert info.flags_marked is False
        assert info.needs_flag_patch is True
        assert "plaintext ExeFS header" in info.detail

    def test_exheader_fallback_when_exefs_is_absent(self, tmp_path):
        data = bytearray(_build_cart(decrypted=True, no_crypto_flag=False))
        struct.pack_into('<II', data, PARTITION_OFFSET + 0x1A0, 0, 0)   # no ExeFS
        info = ctr_rom.probe(_write(tmp_path, "dec.3ds", bytes(data)))
        assert info.decrypted is True
        assert "plaintext ExHeader title" in info.detail

    def test_multi_partition_cart(self, tmp_path):
        info = ctr_rom.probe(
            _write(tmp_path, "dec.3ds", _build_cart(decrypted=True, no_crypto_flag=False, partitions=3))
        )
        assert len(info.partitions) == 3
        assert info.decrypted is True

    def test_encrypted_extra_partitions_do_not_veto(self, tmp_path):
        """Retail layout of an actual decrypted dump: the game partition is
        plaintext while the manual (p1) and update (p7) partitions were left
        encrypted.  Emulators and 3dsconv only ever read partition 0, so the
        cart still counts as decrypted."""
        data = _build_cart_from([cxi(0, plaintext=True), cfa(1), cfa(7)])
        info = ctr_rom.probe(_write(tmp_path, "retail.3ds", data))
        assert [p.index for p in info.partitions] == [0, 1, 7]
        assert info.decrypted is True
        assert info.needs_flag_patch is True
        assert info.detail == (
            "p0:plaintext ExeFS header; p1:encrypted; p7:encrypted"
        )

    def test_encrypted_executable_partition_vetoes(self, tmp_path):
        data = _build_cart_from([cxi(0, plaintext=False), cfa(1, plaintext=True)])
        info = ctr_rom.probe(_write(tmp_path, "enc.3ds", data))
        assert info.decrypted is False

    def test_cart_without_partition_zero_is_not_decrypted(self, tmp_path):
        data = _build_cart_from([cfa(1, plaintext=True)])
        info = ctr_rom.probe(_write(tmp_path, "odd.3ds", data))
        assert info.decrypted is False

    def test_cfa_partition_detected_via_romfs_ivfc(self, tmp_path):
        """A fully decrypted dump: the CFA partitions have no ExeFS and no
        ExHeader, so the RomFS IVFC magic is the only plaintext marker."""
        data = _build_cart_from([cxi(0, plaintext=True), cfa(1, plaintext=True)])
        info = ctr_rom.probe(_write(tmp_path, "dec.3ds", data))
        assert info.decrypted is True
        assert "p1:plaintext RomFS (IVFC)" in info.detail

    def test_partition_with_nothing_testable_is_unknown(self, tmp_path):
        data = bytearray(_build_cart_from([cxi(0, plaintext=True), cfa(1)]))
        second = PARTITION_OFFSET + PARTITION_MEDIA_SIZE * MEDIA_UNIT
        struct.pack_into('<II', data, second + 0x1B0, 0, 0)      # no RomFS either
        info = ctr_rom.probe(_write(tmp_path, "odd.3ds", bytes(data)))
        assert info.partitions[1].state == ctr_rom.UNKNOWN
        assert info.decrypted is True

    def test_trimmed_image_does_not_crash(self, tmp_path):
        data = _build_cart(decrypted=True, no_crypto_flag=False)[:0x4100]
        info = ctr_rom.probe(_write(tmp_path, "trim.3ds", data))
        assert info.is_ncsd is True


class TestWriteDecryptedCopy:
    def test_copy_patches_flags_and_preserves_payload(self, tmp_path):
        src = _write(tmp_path, "dec.3ds", _build_cart(decrypted=True, no_crypto_flag=False))
        dst = tmp_path / "out.cci"
        ctr_rom.write_decrypted_copy(src, dst)

        original = src.read_bytes()
        copied = dst.read_bytes()
        assert len(copied) == len(original)

        flags_at = PARTITION_OFFSET + ctr_rom.NCCH_FLAGS_OFFSET
        assert copied[flags_at + 3] == 0x00
        assert copied[flags_at + 7] & ctr_rom.FLAG_NO_CRYPTO
        assert not copied[flags_at + 7] & ctr_rom.FLAG_FIXED_CRYPTO_KEY

        # Only the 8 flag bytes changed.
        assert copied[:flags_at] == original[:flags_at]
        assert copied[flags_at + 8:] == original[flags_at + 8:]

        # The copy now reports as a fully marked decrypted image.
        info = ctr_rom.probe(dst)
        assert info.decrypted is True
        assert info.flags_marked is True

    def test_copy_of_encrypted_image_leaves_flags_alone(self, tmp_path):
        src = _write(tmp_path, "enc.3ds", _build_cart(decrypted=False, no_crypto_flag=False))
        dst = tmp_path / "out.cci"
        ctr_rom.write_decrypted_copy(src, dst)
        assert dst.read_bytes() == src.read_bytes()

    def test_copy_patches_every_plaintext_partition(self, tmp_path):
        src = _write(
            tmp_path, "dec.3ds", _build_cart(decrypted=True, no_crypto_flag=False, partitions=2)
        )
        dst = tmp_path / "out.cci"
        ctr_rom.write_decrypted_copy(src, dst)
        info = ctr_rom.probe(dst)
        assert len(info.partitions) == 2
        assert all(p.no_crypto for p in info.partitions)

    def test_still_encrypted_partitions_keep_their_flags(self, tmp_path):
        """Flagging a genuinely encrypted update partition as NoCrypto would
        tell an emulator to read ciphertext as content — don't touch it."""
        src = _write(tmp_path, "retail.3ds", _build_cart_from([cxi(0, plaintext=True), cfa(7)]))
        dst = tmp_path / "out.cci"
        ctr_rom.write_decrypted_copy(src, dst)

        info = ctr_rom.probe(dst)
        by_index = {p.index: p for p in info.partitions}
        assert by_index[0].no_crypto is True
        assert by_index[7].no_crypto is False
        assert info.flags_marked is True          # nothing left to patch


class TestLargeCopy:
    def test_copy_spans_multiple_chunks(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ctr_rom, '_COPY_CHUNK', 4096)
        src = _write(tmp_path, "dec.3ds", _build_cart(decrypted=True, no_crypto_flag=False))
        dst = tmp_path / "out.cci"
        ctr_rom.write_decrypted_copy(src, dst)
        assert ctr_rom.probe(dst).flags_marked is True
