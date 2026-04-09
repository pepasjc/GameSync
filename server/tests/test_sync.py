import hashlib

from app.models.save import BundleFile, SaveBundle
from app.services.bundle import create_bundle
from app.services import db


def _make_bundle_bytes(
    title_id: int = 0x0004000000055D00,
    timestamp: int = 1700000000,
    files: list[tuple[str, bytes]] | None = None,
) -> bytes:
    if files is None:
        files = [("main", b"save data here")]
    bundle_files = [
        BundleFile(
            path=path,
            size=len(data),
            sha256=hashlib.sha256(data).digest(),
            data=data,
        )
        for path, data in files
    ]
    bundle = SaveBundle(title_id=title_id, timestamp=timestamp, files=bundle_files)
    return create_bundle(bundle)


def _save_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _upload(client, auth_headers, title_id_hex: str, bundle_bytes: bytes):
    """Helper to upload a save bundle."""
    r = client.post(
        f"/api/v1/saves/{title_id_hex}",
        content=bundle_bytes,
        headers={**auth_headers, "Content-Type": "application/octet-stream"},
    )
    assert r.status_code == 200
    return r


class TestSyncEndpoint:
    def test_empty_sync(self, client, auth_headers):
        """No titles on either side -> empty plan."""
        r = client.post(
            "/api/v1/sync",
            json={"titles": []},
            headers=auth_headers,
        )
        assert r.status_code == 200
        plan = r.json()
        assert plan["upload"] == []
        assert plan["download"] == []
        assert plan["conflict"] == []
        assert plan["up_to_date"] == []
        assert plan["server_only"] == []

    def test_client_has_new_title(self, client, auth_headers):
        """Title exists only on 3DS -> should upload."""
        r = client.post(
            "/api/v1/sync",
            json={
                "titles": [
                    {
                        "title_id": "0004000000055D00",
                        "save_hash": "abcd1234" * 8,
                        "timestamp": 1700000000,
                        "size": 512,
                    }
                ]
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        plan = r.json()
        assert "0004000000055D00" in plan["upload"]
        assert plan["download"] == []

    def test_no_last_synced_hash_downloads(self, client, auth_headers):
        """Without last_synced_hash, differing hashes prefer the server copy."""
        save_data = b"server save data"
        bundle = _make_bundle_bytes(
            title_id=0x0004000000055D00,
            timestamp=2000,
            files=[("main", save_data)],
        )
        _upload(client, auth_headers, "0004000000055D00", bundle)

        r = client.post(
            "/api/v1/sync",
            json={
                "titles": [
                    {
                        "title_id": "0004000000055D00",
                        "save_hash": "different_hash__" * 4,
                        "timestamp": 1000,
                        "size": 512,
                    }
                ]
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        plan = r.json()
        assert "0004000000055D00" in plan["download"]

    def test_three_way_client_changed(self, client, auth_headers):
        """last_synced == server hash, client differs -> upload."""
        save_data = b"shared version"
        bundle = _make_bundle_bytes(
            title_id=0x0004000000055D00,
            timestamp=1000,
            files=[("main", save_data)],
        )
        _upload(client, auth_headers, "0004000000055D00", bundle)
        server_hash = _save_hash(save_data)

        r = client.post(
            "/api/v1/sync",
            json={
                "titles": [
                    {
                        "title_id": "0004000000055D00",
                        "save_hash": "client_modified_" * 4,
                        "timestamp": 2000,
                        "size": 512,
                        "last_synced_hash": server_hash,
                    }
                ]
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        plan = r.json()
        assert "0004000000055D00" in plan["upload"]

    def test_three_way_server_changed(self, client, auth_headers):
        """last_synced == client hash, server differs -> download."""
        save_data_v1 = b"version 1"
        bundle_v1 = _make_bundle_bytes(
            title_id=0x0004000000055D00,
            timestamp=1000,
            files=[("main", save_data_v1)],
        )
        _upload(client, auth_headers, "0004000000055D00", bundle_v1)
        shared_hash = _save_hash(save_data_v1)

        # Another console uploads newer version
        save_data_v2 = b"version 2 from other console"
        bundle_v2 = _make_bundle_bytes(
            title_id=0x0004000000055D00,
            timestamp=2000,
            files=[("main", save_data_v2)],
        )
        _upload(client, auth_headers, "0004000000055D00", bundle_v2)

        # This console hasn't changed (current == last_synced)
        r = client.post(
            "/api/v1/sync",
            json={
                "titles": [
                    {
                        "title_id": "0004000000055D00",
                        "save_hash": shared_hash,
                        "timestamp": 1000,
                        "size": 100,
                        "last_synced_hash": shared_hash,
                    }
                ]
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        plan = r.json()
        assert "0004000000055D00" in plan["download"]

    def test_three_way_both_changed(self, client, auth_headers):
        """All three hashes differ -> conflict."""
        save_data_v1 = b"original"
        bundle_v1 = _make_bundle_bytes(
            title_id=0x0004000000055D00,
            timestamp=1000,
            files=[("main", save_data_v1)],
        )
        _upload(client, auth_headers, "0004000000055D00", bundle_v1)
        last_synced = _save_hash(save_data_v1)

        # Server changes
        bundle_v2 = _make_bundle_bytes(
            title_id=0x0004000000055D00,
            timestamp=2000,
            files=[("main", b"server changed")],
        )
        _upload(client, auth_headers, "0004000000055D00", bundle_v2)

        r = client.post(
            "/api/v1/sync",
            json={
                "titles": [
                    {
                        "title_id": "0004000000055D00",
                        "save_hash": "client_also_chgd" * 4,
                        "timestamp": 2000,
                        "size": 500,
                        "last_synced_hash": last_synced,
                    }
                ]
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        plan = r.json()
        assert "0004000000055D00" in plan["conflict"]

    def test_ps3_sync_rebuilds_stale_server_hash_metadata(self, client, auth_headers):
        bundle = _make_bundle_bytes(
            title_id=0,
            timestamp=1000,
            files=[
                ("GAME", b"game"),
                ("PARAM.SFO", b"param"),
                ("ICON0.PNG", b"icon"),
            ],
        )
        # Use string-title-id bundle for PS3 save-dir IDs.
        bundle_files = [
            BundleFile(
                path=path,
                size=len(data),
                sha256=hashlib.sha256(data).digest(),
                data=data,
            )
            for path, data in [
                ("GAME", b"game"),
                ("PARAM.SFO", b"param"),
                ("ICON0.PNG", b"icon"),
            ]
        ]
        ps3_bundle = SaveBundle(title_id=0, timestamp=1000, files=bundle_files, title_id_str="BLJS10001GAME")
        r = client.post(
            "/api/v1/saves/BLJS10001GAME",
            content=create_bundle(ps3_bundle),
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )
        assert r.status_code == 200

        stale = db.get("BLJS10001GAME")
        assert stale is not None
        stale["save_hash"] = "stale_hash_" * 6 + "st"
        db.upsert(stale)

        r = client.post(
            "/api/v1/sync",
            json={
                "titles": [
                    {
                        "title_id": "BLJS10001GAME",
                        "save_hash": hashlib.sha256(b"game").hexdigest(),
                        "timestamp": 1000,
                        "size": 4,
                    }
                ]
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        plan = r.json()
        assert "BLJS10001GAME" in plan["up_to_date"]

    def test_multi_console_scenario(self, client, auth_headers):
        """Full A -> B -> A multi-console flow."""
        # Console A uploads
        save_a = b"console A save"
        bundle_a = _make_bundle_bytes(
            title_id=0x0004000000055D00,
            timestamp=1000,
            files=[("main", save_a)],
        )
        _upload(client, auth_headers, "0004000000055D00", bundle_a)
        hash_a = _save_hash(save_a)

        # Console B plays and modifies, syncs with last_synced from download
        r = client.post(
            "/api/v1/sync",
            json={
                "titles": [
                    {
                        "title_id": "0004000000055D00",
                        "save_hash": "console_b_played" * 4,
                        "timestamp": 2000,
                        "size": 200,
                        "last_synced_hash": hash_a,
                    }
                ]
            },
            headers=auth_headers,
        )
        plan = r.json()
        assert "0004000000055D00" in plan["upload"]

        # Console B uploads
        save_b = b"console B played"
        bundle_b = _make_bundle_bytes(
            title_id=0x0004000000055D00,
            timestamp=2000,
            files=[("main", save_b)],
        )
        _upload(client, auth_headers, "0004000000055D00", bundle_b)

        # Console A syncs: hasn't played, last_synced == current -> download
        r = client.post(
            "/api/v1/sync",
            json={
                "titles": [
                    {
                        "title_id": "0004000000055D00",
                        "save_hash": hash_a,
                        "timestamp": 1000,
                        "size": 100,
                        "last_synced_hash": hash_a,
                    }
                ]
            },
            headers=auth_headers,
        )
        plan = r.json()
        assert "0004000000055D00" in plan["download"]

    def test_hashes_match_up_to_date(self, client, auth_headers):
        """Same hash -> up to date, no transfer needed."""
        save_data = b"same save data"
        bundle = _make_bundle_bytes(
            title_id=0x0004000000055D00,
            timestamp=1000,
            files=[("main", save_data)],
        )
        _upload(client, auth_headers, "0004000000055D00", bundle)

        # Get the server's stored hash
        meta_r = client.get(
            "/api/v1/saves/0004000000055D00/meta", headers=auth_headers
        )
        server_hash = meta_r.json()["save_hash"]

        r = client.post(
            "/api/v1/sync",
            json={
                "titles": [
                    {
                        "title_id": "0004000000055D00",
                        "save_hash": server_hash,
                        "timestamp": 1000,
                        "size": len(save_data),
                    }
                ]
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        plan = r.json()
        assert "0004000000055D00" in plan["up_to_date"]
        assert plan["upload"] == []
        assert plan["download"] == []

    def test_same_timestamp_different_hash_downloads_without_history(
        self, client, auth_headers
    ):
        """Same timestamp still prefers download when sync history is missing."""
        save_data = b"some save"
        bundle = _make_bundle_bytes(
            title_id=0x0004000000055D00,
            timestamp=1000,
            files=[("main", save_data)],
        )
        _upload(client, auth_headers, "0004000000055D00", bundle)

        r = client.post(
            "/api/v1/sync",
            json={
                "titles": [
                    {
                        "title_id": "0004000000055D00",
                        "save_hash": "completely_different" * 4,
                        "timestamp": 1000,
                        "size": 512,
                    }
                ]
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        plan = r.json()
        assert "0004000000055D00" in plan["download"]

    def test_server_only_titles(self, client, auth_headers):
        """Titles on server but not in 3DS list -> server_only."""
        bundle = _make_bundle_bytes(
            title_id=0x0004000000055D00,
            timestamp=1000,
            files=[("main", b"data")],
        )
        _upload(client, auth_headers, "0004000000055D00", bundle)

        # Sync with empty title list
        r = client.post(
            "/api/v1/sync",
            json={"titles": []},
            headers=auth_headers,
        )
        assert r.status_code == 200
        plan = r.json()
        assert "0004000000055D00" in plan["server_only"]

    def test_server_only_platform_filter_can_scope_non_hex_clients(self, client, auth_headers):
        ps3_bundle = create_bundle(
            SaveBundle(
                title_id=0,
                timestamp=1700000000,
                title_id_str="BLUS30464-SAVE00",
                files=[
                    BundleFile(
                        path="PARAM.SFO",
                        size=5,
                        sha256=hashlib.sha256(b"param").digest(),
                        data=b"param",
                    )
                ],
            )
        )
        psp_bundle = create_bundle(
            SaveBundle(
                title_id=0,
                timestamp=1700000000,
                title_id_str="ULUS10272DATA00",
                files=[
                    BundleFile(
                        path="DATA.BIN",
                        size=4,
                        sha256=hashlib.sha256(b"save").digest(),
                        data=b"save",
                    )
                ],
            )
        )

        _upload(client, auth_headers, "BLUS30464-SAVE00", ps3_bundle)
        _upload(client, auth_headers, "ULUS10272DATA00", psp_bundle)

        r = client.post(
            "/api/v1/sync",
            json={"titles": [], "platforms": ["PS3"]},
            headers=auth_headers,
        )
        assert r.status_code == 200
        plan = r.json()
        assert "BLUS30464-SAVE00" in plan["server_only"]
        assert "ULUS10272DATA00" not in plan["server_only"]

    def test_mixed_scenario(self, client, auth_headers):
        """Multiple titles in different states."""
        # Upload two saves to server
        bundle_a = _make_bundle_bytes(
            title_id=0x0004000000055D00,  # will be up-to-date
            timestamp=1000,
            files=[("main", b"game A")],
        )
        _upload(client, auth_headers, "0004000000055D00", bundle_a)

        # Upload initial version of game B, then update it
        old_b_data = b"game B old"
        bundle_b_old = _make_bundle_bytes(
            title_id=0x00040000001B5000,
            timestamp=1000,
            files=[("main", old_b_data)],
        )
        _upload(client, auth_headers, "00040000001B5000", bundle_b_old)
        last_synced_b = _save_hash(old_b_data)

        # Another console updates game B on server
        bundle_b_new = _make_bundle_bytes(
            title_id=0x00040000001B5000,
            timestamp=2000,
            files=[("main", b"game B newer")],
        )
        _upload(client, auth_headers, "00040000001B5000", bundle_b_new)

        # Get hash for game A to simulate up-to-date
        meta_a = client.get(
            "/api/v1/saves/0004000000055D00/meta", headers=auth_headers
        ).json()

        r = client.post(
            "/api/v1/sync",
            json={
                "titles": [
                    {
                        # Game A: same hash -> up_to_date
                        "title_id": "0004000000055D00",
                        "save_hash": meta_a["save_hash"],
                        "timestamp": 1000,
                        "size": 6,
                    },
                    {
                        # Game B: client unchanged, server changed -> download
                        "title_id": "00040000001B5000",
                        "save_hash": last_synced_b,
                        "timestamp": 1000,
                        "size": 6,
                        "last_synced_hash": last_synced_b,
                    },
                    {
                        # Game C: only on client -> upload
                        "title_id": "0004000000044B00",
                        "save_hash": "new_game_hash_______" * 4,
                        "timestamp": 3000,
                        "size": 100,
                    },
                ],
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        plan = r.json()
        assert "0004000000055D00" in plan["up_to_date"]
        assert "00040000001B5000" in plan["download"]
        assert "0004000000044B00" in plan["upload"]

    def test_sync_requires_auth(self, client):
        """Sync endpoint requires API key."""
        r = client.post("/api/v1/sync", json={"titles": []})
        assert r.status_code == 401

    def test_invalid_title_id_rejected(self, client, auth_headers):
        """Invalid title ID format is rejected by validation."""
        r = client.post(
            "/api/v1/sync",
            json={
                "titles": [
                    {
                        "title_id": "not-hex",
                        "save_hash": "abc123",
                        "timestamp": 1000,
                        "size": 100,
                    }
                ]
            },
            headers=auth_headers,
        )
        assert r.status_code == 422
