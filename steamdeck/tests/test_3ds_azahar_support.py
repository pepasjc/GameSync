from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
STEAMDECK_ROOT = ROOT / "steamdeck"
if str(STEAMDECK_ROOT) not in sys.path:
    sys.path.insert(0, str(STEAMDECK_ROOT))

from scanner import citra  # noqa: E402
from scanner.models import SyncStatus  # noqa: E402


def test_scan_reads_azahar_storage_layout(tmp_path):
    emulation = tmp_path / "Emulation"
    save_dir = (
        emulation
        / "storage"
        / "azahar-emu"
        / "sdmc"
        / "Nintendo 3DS"
        / "00000000000000000000000000000000"
        / "00000000000000000000000000000000"
        / "title"
        / "00040000"
        / "00054000"
        / "data"
        / "00000001"
    )
    save_dir.mkdir(parents=True)
    (save_dir / "progress.sav").write_bytes(b"save-data")
    (save_dir / "nested").mkdir()
    (save_dir / "nested" / "extra.bin").write_bytes(b"more")

    results = list(citra.scan(emulation))

    assert len(results) == 1
    entry = results[0]
    assert entry.title_id == "0004000000054000"
    assert entry.system == "3DS"
    assert entry.emulator == "Azahar"
    assert entry.save_path == save_dir
    assert entry.is_multi_file is True
    assert entry.save_hash
    assert entry.save_size == len(b"save-data") + len(b"more")


def test_build_server_only_entries_use_title_id_path(tmp_path):
    emulation = tmp_path / "Emulation"
    server_saves = {
        "0004000000030800": {
            "title_id": "0004000000030800",
            "name": "Mario Kart 7",
            "system": "3DS",
            "save_hash": "server-hash",
            "client_timestamp": 1700000000.0,
            "save_size": 4096,
        }
    }

    entries = citra.build_server_only_entries(server_saves, set(), emulation)

    assert len(entries) == 1
    entry = entries[0]
    assert entry.title_id == "0004000000030800"
    assert entry.system == "3DS"
    assert entry.status == SyncStatus.SERVER_ONLY
    assert entry.is_multi_file is True
    assert entry.save_path == (
        emulation
        / "storage"
        / "azahar-emu"
        / "sdmc"
        / "Nintendo 3DS"
        / "00000000000000000000000000000000"
        / "00000000000000000000000000000000"
        / "title"
        / "00040000"
        / "00030800"
        / "data"
        / "00000001"
    )


def test_build_server_only_entries_skip_non_3ds_rows(tmp_path):
    emulation = tmp_path / "Emulation"
    server_saves = {
        "0004000000030800": {"name": "Mario Kart 7", "system": "3DS", "save_hash": "h"},
        "GBA_pokemon_emerald": {"name": "Pokemon Emerald", "system": "GBA", "save_hash": "h"},
    }

    entries = citra.build_server_only_entries(server_saves, set(), emulation)

    assert [entry.title_id for entry in entries] == ["0004000000030800"]
