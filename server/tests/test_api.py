import hashlib

from app.models.save import BundleFile, SaveBundle
from app.services.bundle import create_bundle
from app.services import game_names
from app.services.ps1_cards import create_vmp, extract_raw_card
from app.services.ps2_cards import add_ecc, strip_ecc
from app.services import serialstation


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
        files = [
            (
                "SCEVMC0.VMP",
                b"\x00PMV" + b"\x00" * 0x7C + b"MC\x00\x00" + b"\x00" * (0x20000 - 4),
            )
        ]
    bundle_files = [
        BundleFile(
            path=path,
            size=len(data),
            sha256=hashlib.sha256(data).digest(),
            data=data,
        )
        for path, data in files
    ]
    bundle = SaveBundle(
        title_id=0, timestamp=timestamp, files=bundle_files, title_id_str=title_id
    )
    return create_bundle(bundle)


def _make_ps2_bundle_bytes(
    title_id: str = "SLUS20002",
    timestamp: int = 1700000000,
    files: list[tuple[str, bytes]] | None = None,
) -> bytes:
    if files is None:
        files = [("card.mc2", bytes([0xAB]) * (512 * 16384))]
    bundle_files = [
        BundleFile(
            path=path,
            size=len(data),
            sha256=hashlib.sha256(data).digest(),
            data=data,
        )
        for path, data in files
    ]
    bundle = SaveBundle(
        title_id=0, timestamp=timestamp, files=bundle_files, title_id_str=title_id
    )
    return create_bundle(bundle)


def _make_string_bundle_bytes(
    title_id: str,
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
    bundle = SaveBundle(
        title_id=0,
        timestamp=timestamp,
        files=bundle_files,
        title_id_str=title_id,
    )
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

    def test_titles_refresh_ps3_hash_from_current_files(
        self, client, auth_headers, tmp_save_dir
    ):
        title_id = "NPUB30096-SAVEGAME"
        bundle = _make_string_bundle_bytes(
            title_id=title_id,
            files=[("PARAM.SFO", b"param"), ("SAVEDATA", b"v1")],
        )
        client.post(
            f"/api/v1/saves/{title_id}",
            content=bundle,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )

        current = tmp_save_dir / title_id / "current"
        (current / "SAVEDATA").write_bytes(b"v2")

        r = client.get("/api/v1/titles", headers=auth_headers)
        assert r.status_code == 200
        titles = r.json()["titles"]
        assert len(titles) == 1
        assert titles[0]["title_id"] == title_id
        assert titles[0]["save_hash"] == hashlib.sha256(b"v2").hexdigest()

    def test_titles_names_uses_serialstation_for_ps2_codes(
        self, client, auth_headers, monkeypatch
    ):
        async def fake_lookup_batch(codes):
            assert codes == ["SLPM65590"]
            return {"SLPM65590": ("Densha de Go! FINAL", "PS2")}

        monkeypatch.setattr(serialstation, "lookup_batch", fake_lookup_batch)

        r = client.post(
            "/api/v1/titles/names",
            json={"codes": ["SLPM65590"]},
            headers=auth_headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["names"]["SLPM65590"] == "Densha de Go! FINAL"
        assert body["types"]["SLPM65590"] == "PS2"

    def test_titles_names_prefers_ps3_db_for_psn_style_ps3_codes(
        self, client, auth_headers, monkeypatch
    ):
        monkeypatch.setitem(game_names._ps3_names, "NPUB30096", "Hard Corps Uprising")

        async def fake_lookup_batch(codes):
            return {}

        monkeypatch.setattr(serialstation, "lookup_batch", fake_lookup_batch)

        r = client.post(
            "/api/v1/titles/names",
            json={"codes": ["NPUB30096-SAVEGAME"]},
            headers=auth_headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["names"]["NPUB30096-SAVEGAME"] == "Hard Corps Uprising"
        assert body["types"]["NPUB30096-SAVEGAME"] == "PS3"

    def test_detect_platform_uses_playstation_serial_heuristics(self):
        assert game_names.detect_platform("NPUB30096-SAVEGAME") == "PS3"
        assert game_names.detect_platform("NPUH10001") == "PSP"
        assert game_names.detect_platform("PCSE00082") == "VITA"
        assert game_names.detect_platform("SLUS01279") == "PS1"
        assert game_names.detect_platform("SLUS20002") == "PS2"

    def test_saturn_archive_lookup_classifies_results(self, client, auth_headers):
        r = client.post(
            "/api/v1/titles/saturn-archives",
            json={
                "title_id": "SAT_T-4507G",
                "archive_names": ["GRANDIA_001", "DRACULAX_01", "UNKNOWN_SLOT"],
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["title_id"] == "SAT_T-4507G"

        result_map = {item["archive_family"]: item for item in body["results"]}
        assert result_map["GRANDIA"]["status"] == "exact_current"
        assert result_map["GRANDIA"]["matches_current_title"] is True
        assert result_map["GRANDIA"]["archive_names"] == ["GRANDIA_001"]
        assert "SAT_T-4507G" in [c["title_id"] for c in result_map["GRANDIA"]["candidates"]]

        assert result_map["DRACULAX"]["status"] == "other_title"
        assert result_map["DRACULAX"]["matches_current_title"] is False
        assert result_map["DRACULAX"]["archive_names"] == ["DRACULAX_01"]
        assert [c["title_id"] for c in result_map["DRACULAX"]["candidates"]] == ["SAT_T-9527G"]
        assert result_map["UNKNOWN_SLOT"]["status"] == "unknown"
        assert result_map["UNKNOWN_SLOT"]["archive_names"] == ["UNKNOWN_SLOT"]
        assert result_map["UNKNOWN_SLOT"]["candidates"] == []

    def test_saturn_archive_lookup_prefers_specific_title_over_collection_overlap(
        self, client, auth_headers
    ):
        r = client.post(
            "/api/v1/titles/saturn-archives",
            json={
                "title_id": "SAT_T-9527G",
                "archive_names": ["DRACULAX_01", "DRACULAX_02"],
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        body = r.json()
        result = body["results"][0]
        assert result["archive_family"] == "DRACULAX"
        assert result["status"] == "exact_current"
        assert result["matches_current_title"] is True
        assert [c["title_id"] for c in result["candidates"]] == ["SAT_T-9527G"]

    def test_titles_can_filter_by_console_type(self, client, auth_headers, monkeypatch):
        bundle_ps1 = _make_ps1_bundle_bytes(title_id="SLUS01279")
        client.post(
            "/api/v1/saves/SLUS01279",
            content=bundle_ps1,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )

        bundle_psp = _make_ps1_bundle_bytes(title_id="ULUS10272")
        client.post(
            "/api/v1/saves/ULUS10272",
            content=bundle_psp,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )

        async def fake_lookup_batch(codes):
            result = {}
            if "SLUS01279" in codes:
                result["SLUS01279"] = ("Dino Crisis 2", "PS1")
            if "ULUS10272" in codes:
                result["ULUS10272"] = ("God of War", "PSP")
            return result

        monkeypatch.setattr(serialstation, "lookup_batch", fake_lookup_batch)

        r = client.get("/api/v1/titles?console_type=PS1", headers=auth_headers)
        assert r.status_code == 200
        titles = r.json()["titles"]
        assert [t["title_id"] for t in titles] == ["SLUS01279"]
        assert titles[0]["console_type"] == "PS1"

    def test_titles_can_filter_by_multiple_console_types(
        self, client, auth_headers, monkeypatch
    ):
        bundle_ps1 = _make_ps1_bundle_bytes(title_id="SLUS01279")
        client.post(
            "/api/v1/saves/SLUS01279",
            content=bundle_ps1,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )

        bundle_ps3 = _make_string_bundle_bytes(
            title_id="NPUB30096-SAVEGAME",
            files=[("SAVEDATA", b"rr7")],
        )
        client.post(
            "/api/v1/saves/NPUB30096-SAVEGAME",
            content=bundle_ps3,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )

        bundle_psp = _make_ps1_bundle_bytes(title_id="ULUS10272")
        client.post(
            "/api/v1/saves/ULUS10272",
            content=bundle_psp,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )

        async def fake_lookup_batch(codes):
            result = {}
            if "SLUS01279" in codes:
                result["SLUS01279"] = ("Dino Crisis 2", "PS1")
            if "NPUB30096" in codes or "NPUB30096-SAVEGAME" in codes:
                result["NPUB30096"] = ("Ridge Racer 7", "PS3")
                result["NPUB30096-SAVEGAME"] = ("Ridge Racer 7", "PS3")
            if "ULUS10272" in codes:
                result["ULUS10272"] = ("God of War", "PSP")
            return result

        monkeypatch.setattr(serialstation, "lookup_batch", fake_lookup_batch)

        r = client.get(
            "/api/v1/titles?console_type=PS1&console_type=PS3",
            headers=auth_headers,
        )
        assert r.status_code == 200
        titles = r.json()["titles"]
        assert {t["title_id"] for t in titles} == {"SLUS01279", "NPUB30096-SAVEGAME"}
        assert {t["console_type"] for t in titles} == {"PS1", "PS3"}


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

    def test_upload_ps3_hash_ignores_metadata_and_pngs(self, client, auth_headers):
        bundle = _make_string_bundle_bytes(
            title_id="BLJS10001GAME",
            files=[
                ("GAME", b"game"),
                ("PARAM.SFO", b"param"),
                ("PARAM.PFD", b"pfd"),
                ("ICON0.PNG", b"icon"),
                ("PIC1.PNG", b"pic"),
            ],
        )
        r = client.post(
            "/api/v1/saves/BLJS10001GAME",
            content=bundle,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )
        assert r.status_code == 200
        assert r.json()["sha256"] == hashlib.sha256(b"game").hexdigest()

        meta = client.get("/api/v1/saves/BLJS10001GAME/meta", headers=auth_headers)
        assert meta.status_code == 200
        assert meta.json()["save_hash"] == hashlib.sha256(b"game").hexdigest()

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
        r = client.get("/api/v1/saves/0004000000055D00", headers=auth_headers)
        assert r.status_code == 404

    def test_download_after_upload(self, client, auth_headers):
        save_data = b"pokemon save file data"
        bundle = _make_bundle_bytes(files=[("main", save_data)])
        client.post(
            "/api/v1/saves/0004000000055D00",
            content=bundle,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )

        r = client.get("/api/v1/saves/0004000000055D00", headers=auth_headers)
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/octet-stream"
        assert "X-Save-Timestamp" in r.headers

        # Parse the returned bundle and verify contents
        from app.services.bundle import parse_bundle

        downloaded = parse_bundle(r.content)
        assert len(downloaded.files) == 1
        assert downloaded.files[0].data == save_data

    def test_ps3_manifest_filters_metadata_and_pngs(self, client, auth_headers):
        bundle = _make_string_bundle_bytes(
            title_id="BLJS10001GAME",
            files=[
                ("GAME", b"game"),
                ("PARAM.SFO", b"param"),
                ("PARAM.PFD", b"pfd"),
                ("ICON0.PNG", b"icon"),
                ("PIC1.PNG", b"pic"),
                ("USR-DATA/SAVE2.DAT", b"save2"),
            ],
        )
        client.post(
            "/api/v1/saves/BLJS10001GAME",
            content=bundle,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )

        r = client.get("/api/v1/saves/BLJS10001GAME/manifest", headers=auth_headers)
        assert r.status_code == 200
        lines = [line for line in r.text.splitlines() if line]
        assert lines == [
            f"GAME\t4\t{hashlib.sha256(b'game').hexdigest()}",
            f"USR-DATA/SAVE2.DAT\t5\t{hashlib.sha256(b'save2').hexdigest()}",
        ]
        assert r.headers["X-Save-File-Count"] == "2"

    def test_raw_download_rejects_multi_file_bundle(self, client, auth_headers):
        bundle = _make_bundle_bytes(
            files=[("ICON0.PNG", b"icon"), ("DATA.BIN", b"save")]
        )
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

    def test_ps1_bundle_upload_materializes_raw_slot_files(
        self, client, auth_headers, tmp_save_dir
    ):
        raw = b"MC\x00\x00" + b"\x22" * (0x20000 - 4)
        vmp = b"\x00PMV" + b"\x00" * 0x7C + raw
        bundle = _make_ps1_bundle_bytes(
            files=[("SCEVMC0.VMP", vmp), ("PARAM.SFO", b"param")]
        )
        r = client.post(
            "/api/v1/saves/SLUS01279",
            content=bundle,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )
        assert r.status_code == 200
        assert (
            tmp_save_dir / "SLUS01279" / "current" / "slot0.mcd"
        ).read_bytes() == raw

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

    def test_ps2_card_download_defaults_to_mc2(self, client, auth_headers):
        mc2 = bytes((i % 251 for i in range(512 * 16384)))
        bundle = _make_ps2_bundle_bytes(files=[("card.mc2", mc2)])
        client.post(
            "/api/v1/saves/SLUS20002",
            content=bundle,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )

        r = client.get("/api/v1/saves/SLUS20002/ps2-card", headers=auth_headers)
        assert r.status_code == 200
        assert r.content == mc2
        assert r.headers["X-Save-Path"] == "card.mc2"

    def test_ps2_card_download_can_render_ps2_format(self, client, auth_headers):
        mc2 = bytes((i % 239 for i in range(512 * 16384)))
        bundle = _make_ps2_bundle_bytes(files=[("card.mc2", mc2)])
        client.post(
            "/api/v1/saves/SLUS20002",
            content=bundle,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )

        r = client.get(
            "/api/v1/saves/SLUS20002/ps2-card?format=ps2", headers=auth_headers
        )
        assert r.status_code == 200
        assert len(r.content) == 528 * 16384
        assert strip_ecc(r.content) == mc2

    def test_ps2_card_upload_accepts_ps2_and_stores_mc2(
        self, client, auth_headers, tmp_save_dir
    ):
        mc2 = bytes((i % 197 for i in range(512 * 16384)))
        ps2 = add_ecc(mc2)

        r = client.post(
            "/api/v1/saves/SLUS20002/ps2-card?format=ps2",
            content=ps2,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )
        assert r.status_code == 200
        assert (tmp_save_dir / "SLUS20002" / "current" / "card.mc2").read_bytes() == mc2

    def test_ps2_card_meta_uses_requested_format_hash(self, client, auth_headers):
        mc2 = bytes((i % 211 for i in range(512 * 16384)))
        bundle = _make_ps2_bundle_bytes(files=[("card.mc2", mc2)])
        client.post(
            "/api/v1/saves/SLUS20002",
            content=bundle,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )

        r = client.get(
            "/api/v1/saves/SLUS20002/ps2-card/meta?format=ps2", headers=auth_headers
        )
        assert r.status_code == 200
        data = r.json()
        expected = add_ecc(mc2)
        assert data["format"] == "ps2"
        assert data["save_hash"] == hashlib.sha256(expected).hexdigest()
        assert data["save_size"] == len(expected)

    def test_ps1_bundle_download_hides_raw_slot_files(self, client, auth_headers):
        raw = b"MC\x00\x00" + b"\x66" * (0x20000 - 4)
        bundle = _make_ps1_bundle_bytes(
            files=[("SCEVMC0.VMP", create_vmp(raw)), ("PARAM.SFO", b"param")]
        )
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

    def test_ps3_save_dir_round_trips_as_string_bundle(self, client, auth_headers):
        title_id = "BLUS30464-AUTOSAVE-SLOT-0000000000000000000000000001"
        bundle = _make_string_bundle_bytes(
            title_id=title_id,
            files=[("PARAM.SFO", b"param"), ("USR-DATA/SAVE.DAT", b"save-data")],
        )
        r = client.post(
            f"/api/v1/saves/{title_id}",
            content=bundle,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )
        assert r.status_code == 200

        meta = client.get(f"/api/v1/saves/{title_id}/meta", headers=auth_headers).json()
        assert meta["title_id"] == title_id
        assert meta["platform"] == "PS3"
        assert meta["system"] == "PS3"

        r = client.get(f"/api/v1/saves/{title_id}", headers=auth_headers)
        assert r.status_code == 200
        from app.services.bundle import parse_bundle

        downloaded = parse_bundle(r.content)
        assert downloaded.effective_title_id == title_id
        assert sorted(f.path for f in downloaded.files) == [
            "PARAM.SFO",
            "USR-DATA/SAVE.DAT",
        ]

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
        r = client.get("/api/v1/saves/0004000000055D00/meta", headers=auth_headers)
        assert r.status_code == 404

    def test_meta_after_upload(self, client, auth_headers):
        bundle = _make_bundle_bytes(timestamp=1700000000)
        client.post(
            "/api/v1/saves/0004000000055D00",
            content=bundle,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )

        r = client.get("/api/v1/saves/0004000000055D00/meta", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert data["title_id"] == "0004000000055D00"
        assert data["client_timestamp"] == 1700000000
        assert data["file_count"] == 1
        assert "save_hash" in data

    def test_ps1_meta_uses_psp_visible_hash(self, client, auth_headers):
        raw = b"MC\x00\x00" + b"\x77" * (0x20000 - 4)
        vmp = create_vmp(raw)
        bundle = _make_ps1_bundle_bytes(
            files=[("SCEVMC0.VMP", vmp), ("PARAM.SFO", b"param")]
        )
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

        r = client.get(
            "/api/v1/saves/SLUS01279/ps1-card/meta?slot=0", headers=auth_headers
        )
        assert r.status_code == 200
        data = r.json()
        assert data["title_id"] == "SLUS01279"
        assert data["client_timestamp"] == 1700000000
        assert isinstance(data["server_timestamp"], str)
        assert data["server_timestamp"]


class TestPs1Lookup:
    def test_lookup_psx_serial_prefers_region_hint(self, monkeypatch):
        monkeypatch.setattr(game_names, "_psx_by_slug", {"dino_crisis_2": "SCES02220"})
        monkeypatch.setattr(
            game_names,
            "_psx_serials_by_slug",
            {"dino_crisis_2": ["SCES02220", "SLUS01279"]},
        )

        assert game_names.lookup_psx_serial("Dino Crisis 2 (USA)") == "SLUS01279"
        assert game_names.lookup_psx_serial("Dino Crisis 2 (Europe)") == "SCES02220"

    def test_normalize_endpoint_uses_region_aware_ps1_serial_lookup(
        self, client, auth_headers, monkeypatch
    ):
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
        monkeypatch.setattr(
            game_names,
            "_psx_serials_by_slug",
            {"dino_crisis_2": ["SCES02220", "SLUS01279"]},
        )

        r = client.post(
            "/api/v1/normalize/batch",
            json={"roms": [{"system": "PS1", "filename": "Dino Crisis 2 (USA).cue"}]},
            headers=auth_headers,
        )

        assert r.status_code == 200
        result = r.json()["results"][0]
        assert result["canonical_name"] == "Dino Crisis 2 (USA)"
        assert result["title_id"] == "SLUS01279"


class TestSaturnLookup:
    def test_lookup_saturn_serial_prefers_region_hint(self, monkeypatch):
        monkeypatch.setattr(game_names, "_sat_by_slug", {"alien_trilogy": "T-8113G"})
        monkeypatch.setattr(
            game_names,
            "_sat_serials_by_slug",
            {"alien_trilogy": ["T-8113G", "T-8113H", "T-8113H-50"]},
        )

        assert game_names.lookup_saturn_serial("Alien Trilogy (USA)") == "T-8113H"
        assert game_names.lookup_saturn_serial("Alien Trilogy (Europe)") == "T-8113H-50"

    def test_normalize_endpoint_uses_saturn_serial_lookup(
        self, client, auth_headers, monkeypatch
    ):
        class FakeNormalizer:
            def normalize(self, system, filename, crc32=None):
                return {
                    "canonical_name": "Albert Odyssey - Legend of Eldean (USA)",
                    "slug": "albert_odyssey_legend_of_eldean_usa",
                    "source": "dat_filename",
                }

            def search_candidates(self, system, filename):
                return ["Albert Odyssey - Legend of Eldean (USA)"]

        from app.services import dat_normalizer

        monkeypatch.setattr(dat_normalizer, "get", lambda: FakeNormalizer())
        monkeypatch.setattr(
            game_names, "_sat_by_slug", {"albert_odyssey_legend_of_eldean": "T-12705H"}
        )
        monkeypatch.setattr(
            game_names,
            "_sat_serials_by_slug",
            {"albert_odyssey_legend_of_eldean": ["T-12705H"]},
        )
        monkeypatch.setattr(game_names, "_sat_safe_to_serial", {"T-12705H": "T-12705H"})

        r = client.post(
            "/api/v1/normalize/batch",
            json={
                "roms": [
                    {
                        "system": "SAT",
                        "filename": "Albert Odyssey - Legend of Eldean (USA).cue",
                    }
                ]
            },
            headers=auth_headers,
        )

        assert r.status_code == 200
        result = r.json()["results"][0]
        assert result["canonical_name"] == "Albert Odyssey - Legend of Eldean (USA)"
        assert result["title_id"] == "SAT_T-12705H"

    def test_lookup_names_typed_supports_saroo_style_saturn_title_id(self, monkeypatch):
        monkeypatch.setattr(
            game_names,
            "_sat_names",
            {"T-12705H": "Albert Odyssey - Legend of Eldean (USA)"},
        )
        monkeypatch.setattr(game_names, "_sat_safe_to_serial", {"T-12705H": "T-12705H"})

        result = game_names.lookup_names_typed(["SAT_T-12705H"])

        assert result["SAT_T-12705H"] == (
            "Albert Odyssey - Legend of Eldean (USA)",
            "SAT",
        )
        assert game_names.detect_platform("SAT_T-12705H") == "SAT"
