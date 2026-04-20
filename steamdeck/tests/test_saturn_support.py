import hashlib
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
STEAMDECK_ROOT = ROOT / "steamdeck"
if str(STEAMDECK_ROOT) not in sys.path:
    sys.path.insert(0, str(STEAMDECK_ROOT))

from scanner import retroarch  # noqa: E402
from scanner.models import GameEntry  # noqa: E402
import sync_client  # noqa: E402
from sync_client import SyncClient  # noqa: E402
from saturn_format import (  # noqa: E402
    convert_saturn_save_format,
    list_saturn_archive_names,
    normalize_saturn_save,
)
from saroo_format import _NativeSave, _build_native_saturn  # noqa: E402


def test_retroarch_scan_detects_saturn_yabause_root_save(tmp_path):
    emulation = tmp_path / "Emulation"
    saves_dir = emulation / "saves" / "retroarch" / "saves"
    roms_dir = emulation / "roms" / "saturn"
    saves_dir.mkdir(parents=True)
    roms_dir.mkdir(parents=True)

    (roms_dir / "Panzer Dragoon Saga (USA).chd").write_bytes(b"")
    expected_save = saves_dir / "Panzer Dragoon Saga (USA).srm"
    expected_save.write_bytes(b"saturn-save")

    results = list(retroarch.scan(emulation))

    assert len(results) == 1
    assert results[0].system == "SAT"
    assert results[0].save_path == expected_save


def test_retroarch_scan_uses_shared_saturn_resolver_for_title_id(tmp_path):
    """Saturn ROMs with disc filenames that appear in the libretro DAT must
    resolve to SAT_<product-code> (matching what the Android client produces
    and what the server's saturn_archive_names.json keys on), not the
    filename-slug fallback `SAT_grandia_japan_disc_1`."""
    emulation = tmp_path / "Emulation"
    saves_dir = emulation / "saves" / "retroarch" / "saves"
    roms_dir = emulation / "roms" / "saturn"
    saves_dir.mkdir(parents=True)
    roms_dir.mkdir(parents=True)

    (roms_dir / "Grandia (Japan) (Disc 1) (4M).chd").write_bytes(b"")

    results = list(retroarch.scan(emulation))

    assert len(results) == 1
    assert results[0].system == "SAT"
    assert results[0].title_id == "SAT_T-4507G"


def test_retroarch_scan_prefers_saturn_shared_backup_bin(tmp_path):
    emulation = tmp_path / "Emulation"
    saves_dir = emulation / "saves" / "retroarch" / "saves" / "yabasanshiro"
    roms_dir = emulation / "roms" / "saturn"
    saves_dir.mkdir(parents=True)
    roms_dir.mkdir(parents=True)

    (roms_dir / "Grandia (Japan).chd").write_bytes(b"")
    backup = saves_dir / "backup.bin"
    backup.write_bytes(b"shared-backup")

    results = list(retroarch.scan(emulation))

    assert len(results) == 1
    assert results[0].system == "SAT"
    assert results[0].save_path == backup


def test_sync_client_uploads_saturn_yabause_as_canonical_bkr(monkeypatch, tmp_path):
    canonical = _build_native_saturn(
        [
            _NativeSave(
                name="GRANDIA_001",
                language_code=0,
                comment="Feena's Ho",
                date_code=1,
                raw_data=b"grandia",
            )
        ]
    )
    local_path = tmp_path / "Grandia.srm"
    local_path.write_bytes(convert_saturn_save_format(canonical, "yabause"))

    uploads = []

    class _Resp:
        status_code = 201

        def raise_for_status(self):
            return None

    def fake_post(url, data=None, headers=None, timeout=None, **kwargs):
        uploads.append((url, data, headers))
        return _Resp()

    monkeypatch.setattr(sync_client.requests, "post", fake_post)
    monkeypatch.setattr(sync_client, "_update_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        sync_client, "_set_saturn_archive_names", lambda *args, **kwargs: None
    )

    client = SyncClient("example", 8000, "key")
    entry = GameEntry(
        title_id="SAT_T-4507G",
        display_name="Grandia",
        system="SAT",
        emulator="RetroArch",
        save_path=local_path,
    )

    ok = client.upload_save(entry, force=True)

    assert ok is True
    assert uploads[0][0] == "http://example:8000/api/v1/saves/SAT_T-4507G/raw?force=true"
    assert uploads[0][1] == normalize_saturn_save(local_path.read_bytes())


def test_sync_client_downloads_saturn_yabasanshiro_by_merging_backup_bin(
    monkeypatch, tmp_path
):
    existing = convert_saturn_save_format(
        _build_native_saturn(
            [
                _NativeSave(
                    name="GRANDIA_001",
                    language_code=0,
                    comment="Grandia",
                    date_code=1,
                    raw_data=b"grandia",
                )
            ]
        ),
        "yabasanshiro",
    )
    incoming = _build_native_saturn(
        [
            _NativeSave(
                name="DRACULAX_01",
                language_code=0,
                comment="Dracula",
                date_code=2,
                raw_data=b"dracula",
            )
        ]
    )

    backup_path = tmp_path / "yabasanshiro" / "backup.bin"
    backup_path.parent.mkdir(parents=True)
    backup_path.write_bytes(existing)

    class _Resp:
        status_code = 200
        content = incoming
        headers = {"X-Save-Hash": hashlib.sha256(incoming).hexdigest()}

    monkeypatch.setattr(sync_client.requests, "get", lambda *args, **kwargs: _Resp())
    monkeypatch.setattr(sync_client, "_update_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        sync_client, "_set_saturn_archive_names", lambda *args, **kwargs: None
    )

    client = SyncClient("example", 8000, "key")
    entry = GameEntry(
        title_id="SAT_T-9527G",
        display_name="Dracula X",
        system="SAT",
        emulator="RetroArch",
        save_path=backup_path,
    )

    ok = client.download_save(entry, force=True)

    assert ok is True
    assert list_saturn_archive_names(backup_path.read_bytes()) == [
        "GRANDIA_001",
        "DRACULAX_01",
    ]


def test_resolve_saturn_archive_selection_prefers_exact_current_when_unknown_exists(
    monkeypatch, tmp_path
):
    shared = convert_saturn_save_format(
        _build_native_saturn(
            [
                _NativeSave(
                    name="DRACULAX_01",
                    language_code=0,
                    comment="Dracula",
                    date_code=1,
                    raw_data=b"dracula",
                ),
                _NativeSave(
                    name="MYSTERY_01",
                    language_code=0,
                    comment="Mystery",
                    date_code=2,
                    raw_data=b"mystery",
                ),
            ]
        ),
        "yabasanshiro",
    )
    backup_path = tmp_path / "backup.bin"
    backup_path.write_bytes(shared)

    monkeypatch.setattr(sync_client, "_get_saturn_archive_names", lambda *_: [])
    stored: list[str] = []
    monkeypatch.setattr(
        sync_client,
        "_set_saturn_archive_names",
        lambda _title_id, archive_names: stored.extend(archive_names),
    )
    monkeypatch.setattr(
        sync_client,
        "_lookup_saturn_archive_candidates",
        lambda *args, **kwargs: [
            {
                "archive_family": "DRACULAX",
                "archive_names": ["DRACULAX_01"],
                "status": "exact_current",
                "matches_current_title": True,
                "candidates": [{"title_id": "SAT_T-9527G", "game_name": "Dracula X"}],
            },
            {
                "archive_family": "MYSTERY",
                "archive_names": ["MYSTERY_01"],
                "status": "unknown",
                "matches_current_title": False,
                "candidates": [],
            },
        ],
    )

    selected = sync_client._resolve_saturn_archive_selection(
        "http://example:8000/api/v1",
        {"X-API-Key": "key"},
        "SAT_T-9527G",
        backup_path,
    )

    assert selected == ["DRACULAX_01"]
    assert stored == ["DRACULAX_01"]


def test_compute_status_uses_canonical_saturn_metadata_for_shared_backup(
    monkeypatch, tmp_path
):
    canonical = _build_native_saturn(
        [
            _NativeSave(
                name="DRACULAX_01",
                language_code=0,
                comment="Dracula",
                date_code=2,
                raw_data=b"dracula",
            )
        ]
    )
    shared = convert_saturn_save_format(canonical, "yabasanshiro")
    backup_path = tmp_path / "backup.bin"
    backup_path.write_bytes(shared)

    monkeypatch.setattr(sync_client, "_get_saturn_archive_names", lambda *_: ["DRACULAX_01"])
    monkeypatch.setattr(sync_client, "_set_saturn_archive_names", lambda *args, **kwargs: None)

    client = SyncClient("example", 8000, "key")
    entry = GameEntry(
        title_id="SAT_T-9527G",
        display_name="Dracula X",
        system="SAT",
        emulator="RetroArch",
        save_path=backup_path,
        save_size=backup_path.stat().st_size,
    )
    server_hash = hashlib.sha256(canonical).hexdigest()

    status = client.compute_status(
        entry,
        {"SAT_T-9527G": {"title_id": "SAT_T-9527G", "save_hash": server_hash}},
    )

    assert status.name == "SYNCED"
    assert entry.save_hash == server_hash
    assert entry.save_size == len(canonical)
