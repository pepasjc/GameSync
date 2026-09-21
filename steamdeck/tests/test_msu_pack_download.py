"""MSU packs land as folders: the download worker hoists the pack's wrapping
folder and drops junk, while ordinary bundles still extract verbatim."""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
STEAMDECK_ROOT = ROOT / "steamdeck"
if str(STEAMDECK_ROOT) not in sys.path:
    sys.path.insert(0, str(STEAMDECK_ROOT))

pytest.importorskip("PyQt6.QtCore")

from download_manager import _bundle_layout  # noqa: E402


def _pack_zip(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Game (USA) (MSU1)/", b"")
        zf.writestr("Game (USA) (MSU1)/Game (USA) (MSU1).sfc", b"R")
        zf.writestr("Game (USA) (MSU1)/Game (USA) (MSU1).msu", b"")
        zf.writestr("Game (USA) (MSU1)/Game (USA) (MSU1)-1.pcm", b"P")
        zf.writestr("Game (USA) (MSU1)/Game (USA) (MSU1).srm", b"S")
    return path


def test_msu_pack_layout_hoists_folder_and_drops_junk(tmp_path):
    with zipfile.ZipFile(_pack_zip(tmp_path / "p.zip")) as zf:
        layout = _bundle_layout(zf, "msu1")
    assert layout == [
        ("Game (USA) (MSU1)/Game (USA) (MSU1).sfc", "Game (USA) (MSU1).sfc"),
        ("Game (USA) (MSU1)/Game (USA) (MSU1).msu", "Game (USA) (MSU1).msu"),
        ("Game (USA) (MSU1)/Game (USA) (MSU1)-1.pcm", "Game (USA) (MSU1)-1.pcm"),
    ]


def test_plain_bundle_layout_is_verbatim(tmp_path):
    with zipfile.ZipFile(_pack_zip(tmp_path / "p.zip")) as zf:
        layout = _bundle_layout(zf, "")
    assert all(member == rel for member, rel in layout)
    assert len(layout) == 4


def test_download_db_migrates_bundle_kind_column(tmp_path):
    import sqlite3

    from download_manager import _REQUIRED_COLUMNS, _SCHEMA

    db = tmp_path / "downloads.db"
    conn = sqlite3.connect(db)
    conn.executescript(_SCHEMA)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(downloads)")}
    assert "bundle_kind" in cols
    assert "bundle_kind" in _REQUIRED_COLUMNS
    conn.close()
