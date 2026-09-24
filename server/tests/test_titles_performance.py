"""/titles and /sync must not re-read save files per request.

On the live server /titles took 5-8 s: every call re-read and re-hashed all
137 PS3 saves (71 MB) to cover rows written before the PS3 hash scheme
changed, wrote each back to the DB, and ran one query per title. That is
now a one-off migration recorded in db_flags.
"""

import hashlib
import json

import pytest

from app.services import db, storage


@pytest.fixture()
def ps3_save(client, auth_headers):
    from tests.test_api import _make_string_bundle_bytes

    title_id = "NPUB30096-SAVEGAME"
    bundle = _make_string_bundle_bytes(
        title_id=title_id,
        files=[("PARAM.SFO", b"param"), ("SAVEDATA", b"v1")],
    )
    r = client.post(
        f"/api/v1/saves/{title_id}",
        content=bundle,
        headers={**auth_headers, "Content-Type": "application/octet-stream"},
    )
    assert r.status_code == 200
    return title_id


def test_migration_runs_once_per_db(ps3_save, monkeypatch):
    assert not storage.ps3_hashes_current()
    storage.migrate_ps3_hashes()
    assert db.get_flag(storage.PS3_HASH_FLAG) == storage.PS3_HASH_SCHEME
    assert storage.ps3_hashes_current()

    def boom(*_a, **_k):
        raise AssertionError("re-read save files after the migration")

    monkeypatch.setattr(storage, "load_save_files", boom)
    assert storage.migrate_ps3_hashes() == 0


def test_migration_fixes_an_old_scheme_hash(ps3_save):
    row = db.get(ps3_save)
    row["save_hash"] = "old-scheme-hash"
    db.upsert(row)
    assert storage.migrate_ps3_hashes() == 1
    assert db.get(ps3_save)["save_hash"] == hashlib.sha256(b"v1").hexdigest()


def test_titles_after_migration_reads_no_files_and_writes_nothing(
        ps3_save, client, auth_headers, monkeypatch):
    storage.migrate_ps3_hashes()

    def boom(*_a, **_k):
        raise AssertionError("GET /titles touched save files or wrote the DB")

    monkeypatch.setattr(storage, "load_save_files", boom)
    monkeypatch.setattr(db, "upsert", boom)
    monkeypatch.setattr(db, "get", boom)  # one query for all, not one each
    r = client.get("/api/v1/titles", headers=auth_headers)
    assert r.status_code == 200
    assert [t["title_id"] for t in r.json()["titles"]] == [ps3_save]


def test_sync_after_migration_reads_no_files(ps3_save, client, auth_headers,
                                             monkeypatch):
    storage.migrate_ps3_hashes()

    def boom(*_a, **_k):
        raise AssertionError("/sync re-read save files")

    monkeypatch.setattr(storage, "load_save_files", boom)
    r = client.post("/api/v1/sync", headers=auth_headers, json={"titles": [{
        "title_id": ps3_save,
        "save_hash": hashlib.sha256(b"v1").hexdigest(),
        "timestamp": 0, "size": 2,
    }]})
    assert r.status_code == 200
    assert r.json()["up_to_date"] == [ps3_save]


def test_flag_does_not_leak_between_databases(ps3_save, tmp_path):
    storage.migrate_ps3_hashes()
    assert storage.ps3_hashes_current()
    from app.config import settings

    other = tmp_path / "other_saves"
    other.mkdir()
    settings.save_dir = other
    assert not storage.ps3_hashes_current()


def test_legacy_json_title_appears_when_its_folder_is_added(client, auth_headers,
                                                           tmp_save_dir):
    assert client.get("/api/v1/titles", headers=auth_headers).json()["titles"] == []
    folder = tmp_save_dir / "0004000000055D00"
    folder.mkdir()
    (folder / "metadata.json").write_text(json.dumps({
        "title_id": "0004000000055D00", "name": "Legacy", "save_hash": "abc",
        "save_size": 1, "file_count": 1, "last_sync": "", "last_sync_source": "",
        "client_timestamp": 0, "server_timestamp": "", "console_id": "",
        "platform": "3DS",
    }))
    titles = client.get("/api/v1/titles", headers=auth_headers).json()["titles"]
    assert [t["title_id"] for t in titles] == ["0004000000055D00"]
