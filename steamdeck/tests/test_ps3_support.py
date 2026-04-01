import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
STEAMDECK_ROOT = ROOT / "steamdeck"
if str(STEAMDECK_ROOT) not in sys.path:
    sys.path.insert(0, str(STEAMDECK_ROOT))

from scanner import rpcs3  # noqa: E402
from scanner.models import GameEntry, SyncStatus  # noqa: E402
from sync_client import SyncClient, _create_dir_bundle  # noqa: E402


def test_rpcs3_scan_uses_full_folder_name_as_title_id(tmp_path):
    emulation = tmp_path / "Emulation"
    save_dir = emulation / "saves" / "rpcs3" / "BLUS30464-AUTOSAVE-01"
    save_dir.mkdir(parents=True)
    (save_dir / "PARAM.SFO").write_bytes(b"param")
    (save_dir / "GAME.DAT").write_bytes(b"game")

    results = list(rpcs3.scan(emulation))

    assert len(results) == 1
    assert results[0].title_id == "BLUS30464-AUTOSAVE-01"
    assert results[0].display_name == "BLUS30464-AUTOSAVE-01"
    assert results[0].system == "PS3"
    assert results[0].is_multi_file is True


def test_rpcs3_scan_accepts_nested_saves_layout(tmp_path):
    emulation = tmp_path / "Emudeck"
    save_dir = emulation / "saves" / "rpcs3" / "saves" / "BLUS30464-AUTOSAVE-01"
    save_dir.mkdir(parents=True)
    (save_dir / "PARAM.SFO").write_bytes(b"param")
    (save_dir / "GAME.DAT").write_bytes(b"game")

    results = list(rpcs3.scan(emulation))

    assert len(results) == 1
    assert results[0].title_id == "BLUS30464-AUTOSAVE-01"
    assert results[0].save_path == save_dir


def test_build_ps3_server_only_entries_infers_rpcs3_destination(tmp_path):
    emulation = tmp_path / "Emulation"
    server_saves = {
        "BLUS30464-AUTOSAVE-01": {
            "title_id": "BLUS30464-AUTOSAVE-01",
            "name": "Demon's Souls",
            "system": "PS3",
            "save_hash": "server-hash",
            "client_timestamp": "2026-03-31T12:00:00Z",
            "save_size": 1234,
        }
    }

    entries = rpcs3.build_server_only_entries(server_saves, set(), emulation)

    assert len(entries) == 1
    assert entries[0].title_id == "BLUS30464-AUTOSAVE-01"
    assert entries[0].save_path == emulation / "saves" / "rpcs3" / "saves" / "BLUS30464-AUTOSAVE-01"
    assert entries[0].status == SyncStatus.SERVER_ONLY
    assert entries[0].is_multi_file is True


def test_build_ps3_server_only_entries_uses_nested_saves_layout_when_present(tmp_path):
    emulation = tmp_path / "Emudeck"
    nested_root = emulation / "saves" / "rpcs3" / "saves"
    nested_root.mkdir(parents=True)
    server_saves = {
        "BLUS30464-AUTOSAVE-01": {
            "title_id": "BLUS30464-AUTOSAVE-01",
            "name": "Demon's Souls",
            "system": "PS3",
            "save_hash": "server-hash",
        }
    }

    entries = rpcs3.build_server_only_entries(server_saves, set(), emulation)

    assert len(entries) == 1
    assert entries[0].save_path == nested_root / "BLUS30464-AUTOSAVE-01"


def test_sync_client_downloads_ps3_bundle_into_missing_directory(monkeypatch, tmp_path):
    client = SyncClient("example", 8000, "key")
    dest = tmp_path / "BLUS30464-AUTOSAVE-01"
    source = tmp_path / "server-copy"
    source.mkdir()
    (source / "PARAM.SFO").write_bytes(b"param")
    (source / "GAME.DAT").write_bytes(b"game")
    calls = []

    class Response:
        status_code = 200
        content = _create_dir_bundle("BLUS30464-AUTOSAVE-01", source)
        headers = {"X-Save-Hash": "server-ps3-hash"}

    def fake_get(url, headers=None, timeout=None):
        calls.append(url)
        return Response()

    monkeypatch.setattr("sync_client.requests.get", fake_get)
    monkeypatch.setattr("sync_client._update_state", lambda title_id, hash_value: None)

    entry = GameEntry(
        title_id="BLUS30464-AUTOSAVE-01",
        display_name="BLUS30464-AUTOSAVE-01",
        system="PS3",
        emulator="RPCS3",
        save_path=dest,
        is_multi_file=True,
    )

    ok = client.download_save(entry, force=True)

    assert ok is True
    assert calls == ["http://example:8000/api/v1/saves/BLUS30464-AUTOSAVE-01"]
    assert dest.is_dir()
    assert (dest / "PARAM.SFO").read_bytes() == b"param"
    assert (dest / "GAME.DAT").read_bytes() == b"game"
