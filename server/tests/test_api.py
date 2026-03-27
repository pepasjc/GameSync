import hashlib

from app.models.save import BundleFile, SaveBundle
from app.services.bundle import create_bundle
from app.services import game_names
from app.services.ps1_cards import create_vmp, extract_raw_card


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


def _make_ps1_bundle_bytes(
    title_id: str = "SLUS01279",
    timestamp: int = 1700000000,
    files: list[tuple[str, bytes]] | None = None,
) -> bytes:
    if files is None:
        files = [("SCEVMC0.VMP", b"\x00PMV" + b"\x00" * 0x7C + b"MC\x00\x00" + b"\x00" * (0x20000 - 4))]
    bundle_files = [
        BundleFile(
            path=path,
            size=len(data),
            sha256=hashlib.sha256(data).digest(),
            data=data,
        )
        for path, data in files
    ]
    bundle = SaveBundle(title_id=0, timestamp=timestamp, files=bundle_files, title_id_str=title_id)
    return create_bundle(bundle)


class TestStatusEndpoint:
    def test_status_no_auth_needed(self, client):
        r = client.get("/api/v1/status")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["version"] == "1.0.0"
        assert data["save_count"] == 0


class TestAuthMiddleware:
    def test_missing_api_key(self, client):
        r = client.get("/api/v1/titles")
        assert r.status_code == 401

    def test_wrong_api_key(self, client):
        r = client.get("/api/v1/titles", headers={"X-API-Key": "wrong"})
        assert r.status_code == 401

    def test_valid_api_key(self, client, auth_headers):
        r = client.get("/api/v1/titles", headers=auth_headers)
        assert r.status_code == 200


class TestTitlesEndpoint:
    def test_empty_list(self, client, auth_headers):
        r = client.get("/api/v1/titles", headers=auth_headers)
        assert r.status_code == 200
        assert r.json() == {"titles": []}

    def test_list_after_upload(self, client, auth_headers):
        bundle = _make_bundle_bytes()
        client.post(
            "/api/v1/saves/0004000000055D00",
            content=bundle,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )

        r = client.get("/api/v1/titles", headers=auth_headers)
        assert r.status_code == 200
        titles = r.json()["titles"]
        assert len(titles) == 1
        assert titles[0]["title_id"] == "0004000000055D00"


class TestUploadEndpoint:
    def test_upload_success(self, client, auth_headers):
        bundle = _make_bundle_bytes()
        r = client.post(
            "/api/v1/saves/0004000000055D00",
            content=bundle,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert "sha256" in data

    def test_upload_empty_body(self, client, auth_headers):
        r = client.post(
            "/api/v1/saves/0004000000055D00",
            content=b"",
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )
        assert r.status_code == 400

    def test_upload_invalid_bundle(self, client, auth_headers):
        r = client.post(
            "/api/v1/saves/0004000000055D00",
            content=b"garbage data here",
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )
        assert r.status_code == 400

    def test_upload_title_id_mismatch(self, client, auth_headers):
        bundle = _make_bundle_bytes(title_id=0x0004000000055D00)
        r = client.post(
            "/api/v1/saves/00040000001B5000",
            content=bundle,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )
        assert r.status_code == 400
        assert "mismatch" in r.json()["detail"].lower()

    def test_upload_invalid_title_id_format(self, client, auth_headers):
        r = client.post(
            "/api/v1/saves/not-a-hex-id",
            content=b"whatever",
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )
        assert r.status_code == 400

    def test_upload_conflict_older_timestamp(self, client, auth_headers):
        # Upload a save with timestamp 2000
        bundle1 = _make_bundle_bytes(timestamp=2000, files=[("main", b"newer")])
        client.post(
            "/api/v1/saves/0004000000055D00",
            content=bundle1,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )

        # Try uploading with older timestamp 1000
        bundle2 = _make_bundle_bytes(timestamp=1000, files=[("main", b"older")])
        r = client.post(
            "/api/v1/saves/0004000000055D00",
            content=bundle2,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )
        assert r.status_code == 409

    def test_upload_force_override(self, client, auth_headers):
        bundle1 = _make_bundle_bytes(timestamp=2000, files=[("main", b"newer")])
        client.post(
            "/api/v1/saves/0004000000055D00",
            content=bundle1,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )

        bundle2 = _make_bundle_bytes(timestamp=1000, files=[("main", b"older")])
        r = client.post(
            "/api/v1/saves/0004000000055D00?force=true",
            content=bundle2,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )
        assert r.status_code == 200


class TestDownloadEndpoint:
    def test_download_not_found(self, client, auth_headers):
        r = client.get(
            "/api/v1/saves/0004000000055D00", headers=auth_headers
        )
        assert r.status_code == 404

    def test_download_after_upload(self, client, auth_headers):
        save_data = b"pokemon save file data"
        bundle = _make_bundle_bytes(files=[("main", save_data)])
        client.post(
            "/api/v1/saves/0004000000055D00",
            content=bundle,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )

        r = client.get(
            "/api/v1/saves/0004000000055D00", headers=auth_headers
        )
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/octet-stream"
        assert "X-Save-Timestamp" in r.headers

        # Parse the returned bundle and verify contents
        from app.services.bundle import parse_bundle

        downloaded = parse_bundle(r.content)
        assert len(downloaded.files) == 1
        assert downloaded.files[0].data == save_data

    def test_raw_download_rejects_multi_file_bundle(self, client, auth_headers):
        bundle = _make_bundle_bytes(files=[("ICON0.PNG", b"icon"), ("DATA.BIN", b"save")])
        client.post(
            "/api/v1/saves/0004000000055D00",
            content=bundle,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )

        r = client.get("/api/v1/saves/0004000000055D00/raw", headers=auth_headers)
        assert r.status_code == 409
        assert "multi-file bundle" in r.json()["detail"].lower()

    def test_ps1_card_download_extracts_raw_from_vmp(self, client, auth_headers):
        raw = b"MC\x00\x00" + b"\x11" * (0x20000 - 4)
        vmp = b"\x00PMV" + b"\x00" * 0x7C + raw
        bundle = _make_ps1_bundle_bytes(files=[("SCEVMC0.VMP", vmp)])
        client.post(
            "/api/v1/saves/SLUS01279",
            content=bundle,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )

        r = client.get("/api/v1/saves/SLUS01279/ps1-card?slot=0", headers=auth_headers)
        assert r.status_code == 200
        assert r.content == raw

    def test_ps1_bundle_upload_materializes_raw_slot_files(self, client, auth_headers, tmp_save_dir):
        raw = b"MC\x00\x00" + b"\x22" * (0x20000 - 4)
        vmp = b"\x00PMV" + b"\x00" * 0x7C + raw
        bundle = _make_ps1_bundle_bytes(files=[("SCEVMC0.VMP", vmp), ("PARAM.SFO", b"param")])
        r = client.post(
            "/api/v1/saves/SLUS01279",
            content=bundle,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )
        assert r.status_code == 200
        assert (tmp_save_dir / "SLUS01279" / "current" / "slot0.mcd").read_bytes() == raw

    def test_create_vmp_round_trips_raw_card(self):
        raw = b"MC\x00\x00" + b"\x33" * (0x20000 - 4)
        assert extract_raw_card(create_vmp(raw)) == raw

    def test_create_vmp_matches_known_signature(self):
        raw = b"MC\x00\x00" + b"\x11" * (0x20000 - 4)
        vmp = create_vmp(raw)
        assert vmp[0x20:0x34].hex() == "5c85b377344da429461b087cb9134d3adfeedc98"

    def test_ps1_card_upload_regenerates_vmp(self, client, auth_headers, tmp_save_dir):
        raw = b"MC\x00\x00" + b"\x44" * (0x20000 - 4)
        bundle = _make_ps1_bundle_bytes(files=[("SCEVMC0.VMP", create_vmp(raw))])
        client.post(
            "/api/v1/saves/SLUS01279",
            content=bundle,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )

        new_raw = b"MC\x00\x00" + b"\x55" * (0x20000 - 4)
        r = client.post(
            "/api/v1/saves/SLUS01279/ps1-card?slot=0",
            content=new_raw,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )
        assert r.status_code == 200
        vmp = (tmp_save_dir / "SLUS01279" / "current" / "SCEVMC0.VMP").read_bytes()
        assert extract_raw_card(vmp) == new_raw

    def test_ps1_bundle_download_hides_raw_slot_files(self, client, auth_headers):
        raw = b"MC\x00\x00" + b"\x66" * (0x20000 - 4)
        bundle = _make_ps1_bundle_bytes(files=[("SCEVMC0.VMP", create_vmp(raw)), ("PARAM.SFO", b"param")])
        client.post(
            "/api/v1/saves/SLUS01279",
            content=bundle,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )

        r = client.get("/api/v1/saves/SLUS01279", headers=auth_headers)
        assert r.status_code == 200
        from app.services.bundle import parse_bundle
        downloaded = parse_bundle(r.content)
        paths = sorted(f.path for f in downloaded.files)
        assert "SCEVMC0.VMP" in paths
        assert "slot0.mcd" not in paths

    def test_upload_preserves_history(self, client, auth_headers, tmp_save_dir):
        # Upload v1
        bundle1 = _make_bundle_bytes(timestamp=1000, files=[("main", b"v1")])
        client.post(
            "/api/v1/saves/0004000000055D00",
            content=bundle1,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )

        # Upload v2 (newer)
        bundle2 = _make_bundle_bytes(timestamp=2000, files=[("main", b"v2")])
        client.post(
            "/api/v1/saves/0004000000055D00",
            content=bundle2,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )

        # Check that history directory exists with v1
        history_dir = tmp_save_dir / "0004000000055D00" / "history"
        assert history_dir.exists()
        versions = list(history_dir.iterdir())
        assert len(versions) == 1


class TestMetadataEndpoint:
    def test_meta_not_found(self, client, auth_headers):
        r = client.get(
            "/api/v1/saves/0004000000055D00/meta", headers=auth_headers
        )
        assert r.status_code == 404

    def test_meta_after_upload(self, client, auth_headers):
        bundle = _make_bundle_bytes(timestamp=1700000000)
        client.post(
            "/api/v1/saves/0004000000055D00",
            content=bundle,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )

        r = client.get(
            "/api/v1/saves/0004000000055D00/meta", headers=auth_headers
        )
        assert r.status_code == 200
        data = r.json()
        assert data["title_id"] == "0004000000055D00"
        assert data["client_timestamp"] == 1700000000
        assert data["file_count"] == 1
        assert "save_hash" in data

    def test_ps1_meta_uses_psp_visible_hash(self, client, auth_headers):
        raw = b"MC\x00\x00" + b"\x77" * (0x20000 - 4)
        vmp = create_vmp(raw)
        bundle = _make_ps1_bundle_bytes(files=[("SCEVMC0.VMP", vmp), ("PARAM.SFO", b"param")])
        client.post(
            "/api/v1/saves/SLUS01279",
            content=bundle,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )

        r = client.get("/api/v1/saves/SLUS01279/meta", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        expected = hashlib.sha256(b"param" + vmp).hexdigest()
        assert data["save_hash"] == expected

    def test_ps1_card_meta_includes_server_timestamp(self, client, auth_headers):
        raw = b"MC\x00\x00" + b"\x78" * (0x20000 - 4)
        bundle = _make_ps1_bundle_bytes(files=[("SCEVMC0.VMP", create_vmp(raw))])
        client.post(
            "/api/v1/saves/SLUS01279",
            content=bundle,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )

        r = client.get("/api/v1/saves/SLUS01279/ps1-card/meta?slot=0", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert data["title_id"] == "SLUS01279"
        assert data["client_timestamp"] == 1700000000
        assert isinstance(data["server_timestamp"], str)
        assert data["server_timestamp"]


class TestPs1Lookup:
    def test_lookup_psx_serial_prefers_region_hint(self, monkeypatch):
        monkeypatch.setattr(game_names, "_psx_by_slug", {"dino_crisis_2": "SCES02220"})
        monkeypatch.setattr(game_names, "_psx_serials_by_slug", {
            "dino_crisis_2": ["SCES02220", "SLUS01279"]
        })

        assert game_names.lookup_psx_serial("Dino Crisis 2 (USA)") == "SLUS01279"
        assert game_names.lookup_psx_serial("Dino Crisis 2 (Europe)") == "SCES02220"

    def test_normalize_endpoint_uses_region_aware_ps1_serial_lookup(self, client, auth_headers, monkeypatch):
        class FakeNormalizer:
            def normalize(self, system, filename, crc32=None):
                return {
                    "canonical_name": "Dino Crisis 2 (USA)",
                    "slug": "dino_crisis_2",
                    "source": "dat_filename",
                }

            def search_candidates(self, system, filename):
                return ["Dino Crisis 2 (USA)", "Dino Crisis 2 (Europe)"]

        from app.services import dat_normalizer

        monkeypatch.setattr(dat_normalizer, "get", lambda: FakeNormalizer())
        monkeypatch.setattr(game_names, "_psx_by_slug", {"dino_crisis_2": "SCES02220"})
        monkeypatch.setattr(game_names, "_psx_serials_by_slug", {
            "dino_crisis_2": ["SCES02220", "SLUS01279"]
        })

        r = client.post(
            "/api/v1/normalize/batch",
            json={
                "roms": [
                    {"system": "PS1", "filename": "Dino Crisis 2 (USA).cue"}
                ]
            },
            headers=auth_headers,
        )

        assert r.status_code == 200
        result = r.json()["results"][0]
        assert result["canonical_name"] == "Dino Crisis 2 (USA)"
        assert result["title_id"] == "SLUS01279"
