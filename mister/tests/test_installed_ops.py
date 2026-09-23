"""Tests for moving and deleting installed games.

The SD card and USB are separate mounts, so a move cannot be a rename and has
to copy; and a CD game is a folder, not a file. Both of those are easy to get
wrong in a way that loses a game.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for candidate in (ROOT, ROOT / "mister"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from gamesync.app import _copy_file, _move_path, _remove_tree  # noqa: E402


def test_move_a_single_file(tmp_path):
    source = tmp_path / "from" / "Game.sfc"
    source.parent.mkdir()
    source.write_bytes(b"rom data")
    destination = tmp_path / "to" / "Game.sfc"
    destination.parent.mkdir()

    _move_path(str(source), str(destination))

    assert destination.read_bytes() == b"rom data"
    assert not source.exists()


def test_move_a_cd_game_folder_with_every_disc(tmp_path):
    source = tmp_path / "from" / "Final Fantasy IX (USA)"
    source.mkdir(parents=True)
    (source / "Disc 1.chd").write_bytes(b"one")
    (source / "Disc 2.chd").write_bytes(b"two")
    (source / "sub").mkdir()
    (source / "sub" / "extra.bin").write_bytes(b"three")

    destination = tmp_path / "to" / "Final Fantasy IX (USA)"
    destination.parent.mkdir()

    _move_path(str(source), str(destination))

    assert (destination / "Disc 1.chd").read_bytes() == b"one"
    assert (destination / "Disc 2.chd").read_bytes() == b"two"
    assert (destination / "sub" / "extra.bin").read_bytes() == b"three"
    assert not source.exists()


def test_move_falls_back_to_copy_across_filesystems(tmp_path, monkeypatch):
    """os.rename cannot cross mounts, which is exactly the SD/USB case."""
    source = tmp_path / "Game.chd"
    source.write_bytes(b"x" * 1024)
    destination = tmp_path / "moved.chd"

    def no_rename(*_args, **_kwargs):
        raise OSError(18, "Invalid cross-device link")

    monkeypatch.setattr(os, "rename", no_rename)
    _move_path(str(source), str(destination))

    assert destination.read_bytes() == b"x" * 1024
    assert not source.exists()


def test_copy_handles_a_file_larger_than_one_chunk(tmp_path):
    """A CHD is gigabytes; it must stream, not load into 492 MB of RAM."""
    payload = bytes(range(256)) * 4096  # 1 MB, several 256 KB chunks
    source = tmp_path / "big.chd"
    source.write_bytes(payload)
    destination = tmp_path / "copy.chd"

    _copy_file(str(source), str(destination))
    assert destination.read_bytes() == payload


def test_remove_tree_deletes_nested_content(tmp_path):
    root = tmp_path / "Game"
    (root / "a" / "b").mkdir(parents=True)
    (root / "a" / "b" / "deep.bin").write_bytes(b"x")
    (root / "top.chd").write_bytes(b"y")

    _remove_tree(str(root))
    assert not root.exists()


def test_remove_tree_leaves_siblings_alone(tmp_path):
    keep = tmp_path / "Keep Me"
    keep.mkdir()
    (keep / "rom.sfc").write_bytes(b"safe")
    doomed = tmp_path / "Delete Me"
    doomed.mkdir()
    (doomed / "rom.sfc").write_bytes(b"gone")

    _remove_tree(str(doomed))

    assert not doomed.exists()
    assert (keep / "rom.sfc").read_bytes() == b"safe"
