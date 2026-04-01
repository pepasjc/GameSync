import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
STEAMDECK_ROOT = ROOT / "steamdeck"
if str(STEAMDECK_ROOT) not in sys.path:
    sys.path.insert(0, str(STEAMDECK_ROOT))

from scanner import rpcs3  # noqa: E402
from scanner.models import GameEntry, SyncStatus  # noqa: E402
from sync_client import SyncClient, _create_dir_bundle, _find_server_save  # noqa: E402


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


def test_rpcs3_scan_accepts_emudeck_storage_layout(tmp_path):
    emulation = tmp_path / "Emulation"
    save_dir = (
        emulation
        / "storage"
        / "rpcs3"
        / "dev_hdd0"
        / "home"
        / "00000001"
        / "savedata"
        / "BLUS30464-AUTOSAVE-01"
    )
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


def test_build_ps3_server_only_entries_accepts_console_type(tmp_path):
    emulation = tmp_path / "Emulation"
    server_saves = {
        "BLUS30464-AUTOSAVE-01": {
            "title_id": "BLUS30464-AUTOSAVE-01",
            "name": "Demon's Souls",
            "console_type": "PS3",
            "system": "",
            "platform": "",
            "save_hash": "server-hash",
        }
    }

    entries = rpcs3.build_server_only_entries(server_saves, set(), emulation)

    assert len(entries) == 1
    assert entries[0].title_id == "BLUS30464-AUTOSAVE-01"
    assert entries[0].status == SyncStatus.SERVER_ONLY


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


# ── Prefix-matching helpers ──────────────────────────────────────────────────


def test_find_server_save_exact_match():
    saves = {"BLJS10001": {"save_hash": "abc"}}
    assert _find_server_save(saves, "BLJS10001") == {"save_hash": "abc"}


def test_find_server_save_local_bare_server_suffixed():
    """Local has BLJS10001, server stored it as BLJS10001GAME."""
    saves = {"BLJS10001GAME": {"save_hash": "abc"}}
    result = _find_server_save(saves, "BLJS10001")
    assert result == {"save_hash": "abc"}


def test_find_server_save_local_suffixed_server_bare():
    """Local has BLJS10001GAME, server stored it as BLJS10001."""
    saves = {"BLJS10001": {"save_hash": "xyz"}}
    result = _find_server_save(saves, "BLJS10001GAME")
    assert result == {"save_hash": "xyz"}


def test_find_server_save_no_match_different_game():
    saves = {"BLJS10002": {"save_hash": "abc"}}
    assert _find_server_save(saves, "BLJS10001") is None


def test_find_server_save_ignores_non_ps3_ids():
    """Slug-based IDs (GBA_, NDS_, etc.) don't trigger prefix matching."""
    saves = {"GBA_pokemon_ruby": {"save_hash": "abc"}}
    assert _find_server_save(saves, "GBA_pokemon_ruby_usa") is None


def test_build_server_only_skips_local_suffixed_variant(tmp_path):
    """Server has BLJS10001GAME; local already scanned BLJS10001 — no duplicate."""
    emulation = tmp_path / "Emulation"
    server_saves = {
        "BLJS10001GAME": {
            "title_id": "BLJS10001GAME",
            "system": "PS3",
            "save_hash": "server-hash",
        }
    }
    # Local scan already found the bare code variant
    seen_ids = {"BLJS10001"}
    entries = rpcs3.build_server_only_entries(server_saves, seen_ids, emulation)
    assert entries == []


def test_build_server_only_skips_server_bare_when_local_suffixed(tmp_path):
    """Server has BLJS10001; local already scanned BLJS10001GAME — no duplicate."""
    emulation = tmp_path / "Emulation"
    server_saves = {
        "BLJS10001": {
            "title_id": "BLJS10001",
            "system": "PS3",
            "save_hash": "server-hash",
        }
    }
    seen_ids = {"BLJS10001GAME"}
    entries = rpcs3.build_server_only_entries(server_saves, seen_ids, emulation)
    assert entries == []


def test_compute_status_matches_server_with_suffix(monkeypatch):
    """compute_status finds server save when local is bare code, server has suffix."""
    client = SyncClient("example", 8000, "key")
    monkeypatch.setattr("sync_client.load_sync_state", lambda: {})

    entry = GameEntry(
        title_id="BLJS10001",
        display_name="Ridge Racer",
        system="PS3",
        emulator="RPCS3",
        save_hash="local-hash",
    )
    server_saves = {"BLJS10001GAME": {"save_hash": "local-hash"}}
    status = client.compute_status(entry, server_saves)
    assert status == SyncStatus.SYNCED


def test_compute_status_matches_server_bare_when_local_suffixed(monkeypatch):
    """compute_status finds server save when local has suffix, server is bare code."""
    client = SyncClient("example", 8000, "key")
    monkeypatch.setattr("sync_client.load_sync_state", lambda: {})

    entry = GameEntry(
        title_id="BLJS10001GAME",
        display_name="Ridge Racer",
        system="PS3",
        emulator="RPCS3",
        save_hash="local-hash",
    )
    server_saves = {"BLJS10001": {"save_hash": "local-hash"}}
    status = client.compute_status(entry, server_saves)
    assert status == SyncStatus.SYNCED
