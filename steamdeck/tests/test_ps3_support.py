import hashlib
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
STEAMDECK_ROOT = ROOT / "steamdeck"
if str(STEAMDECK_ROOT) not in sys.path:
    sys.path.insert(0, str(STEAMDECK_ROOT))

from scanner import rpcs3  # noqa: E402
from scanner.models import GameEntry, SyncStatus  # noqa: E402
from sync_client import (  # noqa: E402
    SyncClient,
    _create_dir_bundle,
    _find_server_save,
    _parse_dir_bundle,
)


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


def test_rpcs3_scan_prefers_live_storage_over_mirror_saves_root(tmp_path):
    emulation = tmp_path / "Emulation"
    mirror_dir = emulation / "saves" / "rpcs3" / "saves" / "BLJS10001GAME"
    storage_dir = (
        emulation
        / "storage"
        / "rpcs3"
        / "dev_hdd0"
        / "home"
        / "00000001"
        / "savedata"
        / "BLJS10001GAME"
    )

    mirror_dir.mkdir(parents=True)
    (mirror_dir / "PARAM.SFO").write_bytes(b"mirror")

    storage_dir.mkdir(parents=True)
    (storage_dir / "PARAM.SFO").write_bytes(b"live")

    results = list(rpcs3.scan(emulation))

    assert len(results) == 1
    assert results[0].title_id == "BLJS10001GAME"
    assert results[0].save_path == storage_dir


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


def test_sync_client_upload_skips_ps3_param_pfd(monkeypatch, tmp_path):
    client = SyncClient("example", 8000, "key")
    save_dir = tmp_path / "BLJS10001GAME"
    save_dir.mkdir()
    (save_dir / "PARAM.SFO").write_bytes(b"param")
    (save_dir / "GAME").write_bytes(b"game")
    (save_dir / "PARAM.PFD").write_bytes(b"pfd")
    uploads = []

    class Response:
        status_code = 200

    def fake_post(url, params=None, data=None, headers=None, timeout=None):
        uploads.append((url, params, data))
        return Response()

    monkeypatch.setattr("sync_client.requests.post", fake_post)
    monkeypatch.setattr("sync_client._update_state", lambda title_id, hash_value: None)

    entry = GameEntry(
        title_id="BLJS10001GAME",
        display_name="Ridge Racer 7",
        system="PS3",
        emulator="RPCS3",
        save_path=save_dir,
        is_multi_file=True,
        save_hash="local-hash",
    )

    ok = client.upload_save(entry, force=True)

    assert ok is True
    assert uploads[0][0] == "http://example:8000/api/v1/saves/BLJS10001GAME"
    assert uploads[0][1] == {"source": "ps3_emu", "force": "true"}
    names = [name for name, _data in _parse_dir_bundle(uploads[0][2])]
    assert names == ["GAME", "PARAM.SFO"]


def test_rpcs3_scan_hash_ignores_ps3_metadata_and_pngs(tmp_path):
    emulation = tmp_path / "Emulation"
    save_dir = (
        emulation
        / "storage"
        / "rpcs3"
        / "dev_hdd0"
        / "home"
        / "00000001"
        / "savedata"
        / "BLJS10001GAME"
    )
    save_dir.mkdir(parents=True)
    (save_dir / "GAME").write_bytes(b"game")
    (save_dir / "PARAM.SFO").write_bytes(b"param")
    (save_dir / "PARAM.PFD").write_bytes(b"pfd")
    (save_dir / "ICON0.PNG").write_bytes(b"icon")
    (save_dir / "PIC1.PNG").write_bytes(b"pic")

    results = list(rpcs3.scan(emulation))

    assert len(results) == 1
    assert results[0].save_hash != ""
    assert results[0].save_hash == rpcs3._hash_ps3_save_dir(save_dir)
    assert results[0].save_hash == hashlib.sha256(b"game").hexdigest()
    assert results[0].save_size == len(b"game")


def test_sync_client_downloads_ps3_bundle_using_matched_server_title_id(monkeypatch, tmp_path):
    client = SyncClient("example", 8000, "key")
    dest = tmp_path / "BLUS30767-AUTO_0-"
    source = tmp_path / "server-copy"
    source.mkdir()
    (source / "PARAM.SFO").write_bytes(b"param")
    (source / "GAME.DAT").write_bytes(b"game")
    calls = []

    class Response:
        status_code = 200
        content = _create_dir_bundle("BLUS30767-AUTO_0_0", source)
        headers = {"X-Save-Hash": "server-ps3-hash"}

    def fake_get(url, headers=None, timeout=None):
        calls.append(url)
        return Response()

    monkeypatch.setattr("sync_client.requests.get", fake_get)
    monkeypatch.setattr("sync_client._update_state", lambda title_id, hash_value: None)

    entry = GameEntry(
        title_id="BLUS30767-AUTO_0-",
        display_name="Dragon's Crown",
        system="PS3",
        emulator="RPCS3",
        save_path=dest,
        is_multi_file=True,
        server_title_id="BLUS30767-AUTO_0_0",
    )

    ok = client.download_save(entry, force=True)

    assert ok is True
    assert calls == ["http://example:8000/api/v1/saves/BLUS30767-AUTO_0_0"]
    assert (dest / "PARAM.SFO").read_bytes() == b"param"
    assert (dest / "GAME.DAT").read_bytes() == b"game"


def test_sync_client_downloads_ps3_bundle_replaces_nested_directory(monkeypatch, tmp_path):
    client = SyncClient("example", 8000, "key")
    dest = tmp_path / "BLUS30464-AUTOSAVE-01"
    old_nested = dest / "USRDIR" / "OLD"
    old_nested.mkdir(parents=True)
    (old_nested / "stale.bin").write_bytes(b"old")

    source = tmp_path / "server-copy"
    (source / "USRDIR").mkdir(parents=True)
    (source / "USRDIR" / "NEW").mkdir(parents=True)
    (source / "PARAM.SFO").write_bytes(b"param")
    (source / "USRDIR" / "NEW" / "fresh.bin").write_bytes(b"new")

    class Response:
        status_code = 200
        content = _create_dir_bundle("BLUS30464-AUTOSAVE-01", source)
        headers = {"X-Save-Hash": "server-ps3-hash"}

    monkeypatch.setattr("sync_client.requests.get", lambda *args, **kwargs: Response())
    monkeypatch.setattr("sync_client._update_state", lambda title_id, hash_value: None)

    entry = GameEntry(
        title_id="BLUS30464-AUTOSAVE-01",
        display_name="Demon's Souls",
        system="PS3",
        emulator="RPCS3",
        save_path=dest,
        is_multi_file=True,
    )

    ok = client.download_save(entry, force=True)

    assert ok is True
    assert not (dest / "USRDIR" / "OLD" / "stale.bin").exists()
    assert (dest / "PARAM.SFO").read_bytes() == b"param"
    assert (dest / "USRDIR" / "NEW" / "fresh.bin").read_bytes() == b"new"


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


def test_find_server_save_does_not_match_different_suffixed_slot():
    saves = {"BLUS30767-AUTO_0_0": {"save_hash": "abc"}}
    assert _find_server_save(saves, "BLUS30767-AUTO_0-") is None


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


def test_build_server_only_keeps_different_suffixed_variant(tmp_path):
    """Different suffixed PS3 folders remain separate downloadable saves."""
    emulation = tmp_path / "Emulation"
    server_saves = {
        "BLUS30767-AUTO_0_0": {
            "title_id": "BLUS30767-AUTO_0_0",
            "system": "PS3",
            "save_hash": "server-hash",
        }
    }
    seen_ids = {"BLUS30767-AUTO_0-"}
    entries = rpcs3.build_server_only_entries(server_saves, seen_ids, emulation)
    assert len(entries) == 1
    assert entries[0].title_id == "BLUS30767-AUTO_0_0"


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
