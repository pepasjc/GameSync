"""Tests for the build-time ``convert_to_chd`` path in ``rom_collection``."""

from __future__ import annotations

from pathlib import Path

import pytest

import rom_collection as rc


def _make_entry(source_path: Path, canonical_name: str, extension: str):
    """Helper to build a minimal CollectionCandidate for build_collection."""
    return rc.CollectionCandidate(
        source_path=source_path,
        canonical_name=canonical_name,
        source_kind="file",
        extension=extension,
        match_source="crc",
    )


def test_build_collection_falls_back_to_copy_when_chdman_missing(tmp_path, monkeypatch):
    """convert_to_chd=True but no chdman_path → still copies the source as-is."""
    src = tmp_path / "game.iso"
    src.write_bytes(b"pretend-iso")
    entry = _make_entry(src, "Game (USA)", ".iso")
    out = tmp_path / "out"

    written = rc.build_collection(
        [entry],
        out,
        unzip_archives=False,
        convert_to_chd=True,
        chdman_path=None,
    )

    # Without chdman we fall through to shutil.copy2 and the file keeps its
    # original extension.
    assert len(written) == 1
    assert written[0].name == "Game (USA).iso"
    assert written[0].read_bytes() == b"pretend-iso"


def test_build_collection_copies_source_on_chdman_failure(tmp_path, monkeypatch):
    """A chdman failure must not abort the build — it copies the source instead."""
    src = tmp_path / "broken.iso"
    src.write_bytes(b"bad-iso-data")
    entry = _make_entry(src, "Broken Game", ".iso")
    out = tmp_path / "out"

    def _fail(chdman, source, output):
        return False, "simulated failure"

    monkeypatch.setattr(rc, "_run_chdman_createcd", _fail)

    written = rc.build_collection(
        [entry],
        out,
        unzip_archives=False,
        convert_to_chd=True,
        chdman_path=Path("whatever"),
    )

    # A fallback copy runs, so the caller sees the non-CHD target.
    assert len(written) == 1
    assert written[0].suffix == ".iso"
    assert written[0].read_bytes() == b"bad-iso-data"


def test_build_collection_uses_chd_target_on_success(tmp_path, monkeypatch):
    """When chdman succeeds, the written file is the .chd target."""
    src = tmp_path / "game.cue"
    src.write_text('FILE "game.bin" BINARY\n  TRACK 01 MODE1/2352\n')
    (tmp_path / "game.bin").write_bytes(b"payload")
    entry = _make_entry(src, "Game (USA)", ".cue")
    out = tmp_path / "out"

    def _fake_chdman(chdman, source, output):
        # Simulate chdman writing the expected .chd file.
        output.write_bytes(b"fake-chd-output")
        return True, ""

    monkeypatch.setattr(rc, "_run_chdman_createcd", _fake_chdman)

    written = rc.build_collection(
        [entry],
        out,
        unzip_archives=False,
        convert_to_chd=True,
        chdman_path=Path("whatever"),
    )

    assert len(written) == 1
    assert written[0].name == "Game (USA).chd"
    assert written[0].read_bytes() == b"fake-chd-output"


def test_build_collection_chd_target_uses_canonical_dat_name(tmp_path, monkeypatch):
    """The .chd target keeps the canonical DAT name exactly — only the
    extension changes.  Mirrors the user's scenario: ``Neo Contra (USA).iso``
    becomes ``Neo Contra (USA).chd``.
    """
    src = tmp_path / "SLUS_210.64.Neo Contra.iso"  # Redump-style filename
    src.write_bytes(b"iso-payload")
    entry = _make_entry(src, "Neo Contra (USA)", ".iso")
    out = tmp_path / "out"

    def _fake_chdman(chdman, source, output):
        output.write_bytes(b"chd-payload")
        return True, ""

    monkeypatch.setattr(rc, "_run_chdman_createcd", _fake_chdman)

    written = rc.build_collection(
        [entry],
        out,
        unzip_archives=False,
        convert_to_chd=True,
        chdman_path=Path("whatever"),
    )

    assert written[0].name == "Neo Contra (USA).chd"


def test_build_collection_does_not_convert_when_flag_disabled(tmp_path, monkeypatch):
    """convert_to_chd=False must never touch chdman even if chdman_path is set."""
    src = tmp_path / "game.iso"
    src.write_bytes(b"iso-data")
    entry = _make_entry(src, "Game", ".iso")
    out = tmp_path / "out"

    calls: list = []

    def _spy(chdman, source, output):
        calls.append((chdman, source, output))
        return True, ""

    monkeypatch.setattr(rc, "_run_chdman_createcd", _spy)

    written = rc.build_collection(
        [entry],
        out,
        unzip_archives=False,
        convert_to_chd=False,
        chdman_path=Path("doesnt-matter"),
    )

    assert calls == []
    assert len(written) == 1
    assert written[0].suffix == ".iso"


def test_build_collection_only_converts_cd_image_extensions(tmp_path, monkeypatch):
    """Cartridge ROMs are copied verbatim even with convert_to_chd enabled."""
    src = tmp_path / "mario.sfc"
    src.write_bytes(b"snes-rom")
    entry = _make_entry(src, "Super Mario World", ".sfc")
    out = tmp_path / "out"

    calls: list = []

    def _spy(chdman, source, output):
        calls.append((source, output))
        return True, ""

    monkeypatch.setattr(rc, "_run_chdman_createcd", _spy)

    written = rc.build_collection(
        [entry],
        out,
        unzip_archives=False,
        convert_to_chd=True,
        chdman_path=Path("whatever"),
    )

    assert calls == []  # chdman is never invoked for .sfc
    assert written[0].suffix == ".sfc"
