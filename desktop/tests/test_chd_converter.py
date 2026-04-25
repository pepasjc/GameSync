"""Tests for chd_converter parsers and source discovery."""

from __future__ import annotations

from pathlib import Path

import chd_converter as cc


def _touch(path: Path, size: int = 16) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)
    return path


# ──────────────────────────────────────────────────────────────────────
# parse_cue_tracks
# ──────────────────────────────────────────────────────────────────────
def test_parse_cue_tracks_quoted(tmp_path: Path):
    bin_path = _touch(tmp_path / "Sonic CD (USA) (Track 1).bin")
    cue_path = tmp_path / "Sonic CD (USA).cue"
    cue_path.write_text(
        'FILE "Sonic CD (USA) (Track 1).bin" BINARY\n'
        "  TRACK 01 MODE1/2352\n"
        "    INDEX 01 00:00:00\n"
    )
    tracks = cc.parse_cue_tracks(cue_path)
    assert tracks == [bin_path]


def test_parse_cue_tracks_unquoted(tmp_path: Path):
    bin_path = _touch(tmp_path / "game.bin")
    cue_path = tmp_path / "game.cue"
    cue_path.write_text("FILE game.bin BINARY\n  TRACK 01 MODE1/2352\n")
    tracks = cc.parse_cue_tracks(cue_path)
    assert tracks == [bin_path]


def test_parse_cue_tracks_multiple(tmp_path: Path):
    t1 = _touch(tmp_path / "disc (Track 1).bin")
    t2 = _touch(tmp_path / "disc (Track 2).bin")
    t3 = _touch(tmp_path / "disc (Track 3).bin")
    cue = tmp_path / "disc.cue"
    cue.write_text(
        'FILE "disc (Track 1).bin" BINARY\n'
        "  TRACK 01 MODE1/2352\n"
        'FILE "disc (Track 2).bin" BINARY\n'
        "  TRACK 02 AUDIO\n"
        'FILE "disc (Track 3).bin" BINARY\n'
        "  TRACK 03 AUDIO\n"
    )
    assert cc.parse_cue_tracks(cue) == [t1, t2, t3]


def test_parse_cue_tracks_skips_missing(tmp_path: Path):
    cue = tmp_path / "ghost.cue"
    cue.write_text('FILE "ghost.bin" BINARY\n  TRACK 01 MODE1/2352\n')
    assert cc.parse_cue_tracks(cue) == []


# ──────────────────────────────────────────────────────────────────────
# parse_gdi_tracks
# ──────────────────────────────────────────────────────────────────────
def test_parse_gdi_tracks_standard(tmp_path: Path):
    t1 = _touch(tmp_path / "track01.bin")
    t2 = _touch(tmp_path / "track02.raw")
    t3 = _touch(tmp_path / "track03.bin")
    gdi = tmp_path / "game.gdi"
    gdi.write_text(
        "3\n"
        "1 0 4 2352 track01.bin 0\n"
        "2 600 0 2352 track02.raw 0\n"
        "3 45000 4 2352 track03.bin 0\n"
    )
    assert cc.parse_gdi_tracks(gdi) == [t1, t2, t3]


def test_parse_gdi_tracks_quoted_filenames(tmp_path: Path):
    t1 = _touch(tmp_path / "my track.bin")
    gdi = tmp_path / "game.gdi"
    gdi.write_text('1\n1 0 4 2352 "my track.bin" 0\n')
    assert cc.parse_gdi_tracks(gdi) == [t1]


def test_parse_gdi_tracks_handles_bom(tmp_path: Path):
    t1 = _touch(tmp_path / "track01.bin")
    gdi = tmp_path / "game.gdi"
    gdi.write_bytes("﻿1\n1 0 4 2352 track01.bin 0\n".encode("utf-8"))
    assert cc.parse_gdi_tracks(gdi) == [t1]


# ──────────────────────────────────────────────────────────────────────
# get_source_files_to_delete
# ──────────────────────────────────────────────────────────────────────
def test_delete_list_for_cue_includes_tracks(tmp_path: Path):
    b1 = _touch(tmp_path / "game (Track 1).bin")
    b2 = _touch(tmp_path / "game (Track 2).bin")
    cue = tmp_path / "game.cue"
    cue.write_text(
        'FILE "game (Track 1).bin" BINARY\n  TRACK 01 MODE1/2352\n'
        'FILE "game (Track 2).bin" BINARY\n  TRACK 02 AUDIO\n'
    )
    files = cc.get_source_files_to_delete(cue)
    assert files == [cue, b1, b2]


def test_delete_list_for_iso_is_self(tmp_path: Path):
    iso = _touch(tmp_path / "game.iso", 32)
    assert cc.get_source_files_to_delete(iso) == [iso]


# ──────────────────────────────────────────────────────────────────────
# find_convertible_sources
# ──────────────────────────────────────────────────────────────────────
def test_find_convertible_sources_skips_bins_owned_by_cue(tmp_path: Path):
    b1 = _touch(tmp_path / "game (Track 1).bin")
    cue = tmp_path / "game.cue"
    cue.write_text('FILE "game (Track 1).bin" BINARY\n  TRACK 01 MODE1/2352\n')
    iso = _touch(tmp_path / "other.iso")

    results = cc.find_convertible_sources(tmp_path, recursive=False)
    sources = sorted(r["source"] for r in results)
    # Standalone .bin is never reported (extension filter) so the output should
    # be just the cue and the iso.
    assert sources == sorted([cue, iso])

    cue_entry = next(r for r in results if r["source"] == cue)
    assert cue_entry["related"] == [b1]
    assert cue_entry["output"] == tmp_path / "game.chd"
    assert cue_entry["total_size"] > 0
    assert cue_entry["output_exists"] is False

    iso_entry = next(r for r in results if r["source"] == iso)
    assert iso_entry["related"] == []
    assert iso_entry["output"] == tmp_path / "other.chd"


def test_find_convertible_sources_detects_existing_chd(tmp_path: Path):
    iso = _touch(tmp_path / "game.iso")
    _touch(tmp_path / "game.chd")  # pretend a previous conversion already ran
    results = cc.find_convertible_sources(tmp_path, recursive=False)
    assert len(results) == 1
    assert results[0]["output_exists"] is True


def test_find_convertible_sources_recursive(tmp_path: Path):
    sub = tmp_path / "PS1" / "Crash Bandicoot"
    _touch(sub / "disc.iso")
    results = cc.find_convertible_sources(tmp_path, recursive=True)
    assert len(results) == 1
    assert results[0]["source"] == sub / "disc.iso"


def test_find_convertible_sources_empty_folder(tmp_path: Path):
    results = cc.find_convertible_sources(tmp_path)
    assert results == []


def test_find_convertible_sources_nonexistent_folder(tmp_path: Path):
    results = cc.find_convertible_sources(tmp_path / "nope")
    assert results == []
