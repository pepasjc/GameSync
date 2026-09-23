import hashlib
import os

from app.models.save import BundleFile, SaveBundle
from app.services.bundle import create_bundle
from app.services import game_names
from app.services.ps1_cards import create_vmp, extract_raw_card
from app.services.ps2_cards import add_ecc, strip_ecc
from app.services import storage


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

    def test_titles_names_uses_local_db_for_ps2_codes(
        self, client, auth_headers, monkeypatch
    ):
        monkeypatch.setitem(game_names._ps2_names, "SLPM65590", "Densha de Go! FINAL")

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

    def test_detect_platform_recognizes_xbox_8hex_title_id(self):
        # Original Xbox uses 8-char hex Title IDs as UDATA folder names.
        assert game_names.detect_platform("4D530004") == "XBOX"  # Halo: Combat Evolved
        assert game_names.detect_platform("4541000D") == "XBOX"  # 007 Agent Under Fire
        assert game_names.detect_platform("4d530004") == "XBOX"  # case-insensitive
        # 16-hex still resolves to 3DS, not Xbox.
        assert game_names.detect_platform("0004000000055D00") == "3DS"

    def test_lookup_names_typed_resolves_xbox_titles(self, monkeypatch):
        """Xbox 8-hex Title IDs must resolve from _xbox_names with platform=XBOX."""
        monkeypatch.setitem(
            game_names._xbox_names, "4D530004", "Halo - Combat Evolved (USA)"
        )
        monkeypatch.setitem(
            game_names._xbox_names, "4541000D", "007 - Agent Under Fire (USA)"
        )

        result = game_names.lookup_names_typed(["4D530004", "4541000D"])
        assert result["4D530004"] == ("Halo - Combat Evolved (USA)", "XBOX")
        assert result["4541000D"] == ("007 - Agent Under Fire (USA)", "XBOX")

    def test_validate_any_title_id_accepts_xbox_8hex(self):
        from app.models.save import (
            is_xbox_title_id,
            validate_any_title_id,
        )

        # Valid Xbox 8-hex IDs round-trip uppercased.
        assert validate_any_title_id("4d530004") == "4D530004"
        assert validate_any_title_id("4541000D") == "4541000D"
        assert is_xbox_title_id("4D530004") is True
        assert is_xbox_title_id("4d530004") is True
        # 16-hex is not a Xbox ID.
        assert is_xbox_title_id("0004000000055D00") is False
        # Non-hex 8-char strings are rejected by is_xbox_title_id but the
        # broader product-code path may still accept them as PSP/Vita-style.
        assert is_xbox_title_id("ULUS1000G") is False

    def test_lookup_names_typed_resolves_ps2_serials_from_local_db(self, monkeypatch):
        """PS2 serials (SCUS97203 = Wild Arms 3) must resolve to a name
        from the local PS2 DAT.  Before routing "Sony - PlayStation 2.dat"
        into its own dict and giving lookup_names_typed a PS2 branch,
        these codes fell through every conditional and came back empty —
        so the UI listed raw serials like SCUS97203 / PBPX95503 instead
        of real game names."""
        monkeypatch.setitem(game_names._ps2_names, "SCUS97203", "Wild Arms 3 (USA)")
        monkeypatch.setitem(game_names._ps2_names, "SCES51920", "Gran Turismo 4 (Europe)")

        result = game_names.lookup_names_typed(["SCUS97203", "SCES51920"])
        assert result["SCUS97203"] == ("Wild Arms 3 (USA)", "PS2")
        assert result["SCES51920"] == ("Gran Turismo 4 (Europe)", "PS2")

    def test_lookup_names_typed_falls_back_to_psx_dict_for_legacy_ps2_entries(
        self, monkeypatch
    ):
        """Data loaded before the PS2 DAT got its own dict lives in
        _psx_names.  The PS2 branch must still find those names so
        redeployment doesn't wipe out existing lookups."""
        # Simulate legacy state: PS2 DAT was loaded into _psx_names.
        monkeypatch.setitem(game_names._psx_names, "SLUS20002", "Armored Core 2 (USA)")

        result = game_names.lookup_names_typed(["SLUS20002"])
        # Heuristic identifies SLUS20002 as PS2 (serial ≥ 20000); the
        # fallback lookup finds the legacy name and tags it PS2.
        assert result["SLUS20002"] == ("Armored Core 2 (USA)", "PS2")

    def test_lookup_names_typed_prefers_ps1_dat_for_ambiguous_japanese_prefixes(
        self, monkeypatch
    ):
        monkeypatch.setitem(game_names._psx_names, "SLPM86034", "Parasite Eve (Japan)")

        result = game_names.lookup_names_typed(["SLPM86034"])
        assert result["SLPM86034"] == ("Parasite Eve (Japan)", "PS1")

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

    def test_titles_list_prefers_local_db_name_over_stale_metadata(
        self, client, auth_headers, monkeypatch
    ):
        monkeypatch.setitem(game_names._psx_names, "SLUS01324", "Breath of Fire IV (USA)")

        bundle_ps1 = _make_ps1_bundle_bytes(title_id="SLUS01324")
        client.post(
            "/api/v1/saves/SLUS01324",
            content=bundle_ps1,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )

        storage.update_metadata_name("SLUS01324", "Breath of Fire 4", "PS1")

        r = client.get("/api/v1/titles", headers=auth_headers)
        assert r.status_code == 200
        titles = {t["title_id"]: t for t in r.json()["titles"]}
        assert titles["SLUS01324"]["name"] == "Breath of Fire IV (USA)"
        assert titles["SLUS01324"]["game_name"] == "Breath of Fire IV (USA)"
        assert titles["SLUS01324"]["console_type"] == "PS1"

    def test_titles_can_filter_by_console_type(self, client, auth_headers):
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

        r = client.get("/api/v1/titles?console_type=PS1", headers=auth_headers)
        assert r.status_code == 200
        titles = r.json()["titles"]
        assert [t["title_id"] for t in titles] == ["SLUS01279"]
        assert titles[0]["console_type"] == "PS1"

    def test_titles_can_filter_by_multiple_console_types(self, client, auth_headers):
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

    def test_lookup_psx_serial_falls_back_to_roman_arabic_variants(self, monkeypatch):
        monkeypatch.setattr(game_names, "_psx_by_slug", {})
        monkeypatch.setattr(
            game_names,
            "_psx_serials_by_slug",
            {"breath_of_fire_4": ["SLUS01324"]},
        )

        assert game_names.lookup_psx_serial("Breath of Fire IV (USA)") == "SLUS01324"

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


class Test3dsLookup:
    def test_load_3ds_dat_with_title_ids_populates_lookup(self, tmp_path, monkeypatch):
        dat_path = tmp_path / "Nintendo - Nintendo 3DS.dat"
        dat_path.write_text(
            "\n".join(
                [
                    "game (",
                    '\tname "Mario Kart 7 (USA)"',
                    '\tserial "CTR-P-AMKE"',
                    '\ttitle_id "0004000000030800"',
                    ")",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        monkeypatch.setattr(game_names, "_3ds_names", {})
        monkeypatch.setattr(game_names, "_3ds_priority", {})
        monkeypatch.setattr(game_names, "_3ds_title_ids", {})
        monkeypatch.setattr(game_names, "_3ds_title_id_priority", {})
        monkeypatch.setattr(game_names, "_3ds_serial_to_title_id", {})
        monkeypatch.setattr(game_names, "_3ds_by_slug", {})
        monkeypatch.setattr(game_names, "_3ds_title_ids_by_slug", {})
        monkeypatch.setattr(game_names, "_3ds_title_priority", {})

        added = game_names.load_libretro_dat_to_dicts(dat_path)

        assert added == 1
        assert game_names.lookup_names_typed(["0004000000030800"]) == {
            "0004000000030800": ("Mario Kart 7 (USA)", "3DS")
        }
        assert game_names._3ds_serial_to_title_id["CTR-P-AMKE"] == "0004000000030800"
        assert (
            game_names.lookup_disc_serial("3DS", "Mario Kart 7 (USA).3ds")
            == "0004000000030800"
        )

    def test_load_3ds_dat_with_title_ids_supports_ktr_serials(
        self, tmp_path, monkeypatch
    ):
        dat_path = tmp_path / "Nintendo - Nintendo 3DS.dat"
        dat_path.write_text(
            "\n".join(
                [
                    "game (",
                    '\tname "Fire Emblem Warriors (USA)"',
                    '\tserial "KTR-P-CFME"',
                    '\ttitle_id "000400000F70CC00"',
                    ")",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        monkeypatch.setattr(game_names, "_3ds_names", {})
        monkeypatch.setattr(game_names, "_3ds_priority", {})
        monkeypatch.setattr(game_names, "_3ds_title_ids", {})
        monkeypatch.setattr(game_names, "_3ds_title_id_priority", {})
        monkeypatch.setattr(game_names, "_3ds_serial_to_title_id", {})
        monkeypatch.setattr(game_names, "_3ds_by_slug", {})
        monkeypatch.setattr(game_names, "_3ds_title_ids_by_slug", {})
        monkeypatch.setattr(game_names, "_3ds_title_priority", {})

        added = game_names.load_libretro_dat_to_dicts(dat_path)

        assert added == 1
        assert game_names.lookup_names_typed(["000400000F70CC00"]) == {
            "000400000F70CC00": ("Fire Emblem Warriors (USA)", "3DS")
        }
        assert game_names.lookup_names_typed(["KTR-P-CFME"]) == {
            "KTR-P-CFME": ("Fire Emblem Warriors (USA)", "3DS")
        }
        assert game_names._3ds_serial_to_title_id["KTR-P-CFME"] == "000400000F70CC00"
        assert (
            game_names.lookup_disc_serial("3DS", "Fire Emblem Warriors (USA).3ds")
            == "000400000F70CC00"
        )

    def test_load_3ds_digital_dat_supports_title_id_only_blocks(
        self, tmp_path, monkeypatch
    ):
        dat_path = tmp_path / "Nintendo - Nintendo 3DS (Digital).dat"
        dat_path.write_text(
            "\n".join(
                [
                    "game (",
                    '\tname "BlockForm (USA)"',
                    '\tdescription "BlockForm (USA)"',
                    '\tregion "USA"',
                    '\trom ( name "000400000f707000tmd" size 4708 crc 71162761 md5 B653B088048B47C1EF5D0209000F5803 sha1 78F8AA94D50360371257A2089768BDB625517C99 )',
                    '\ttitle_id "000400000F707000"',
                    ")",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        monkeypatch.setattr(game_names, "_3ds_names", {})
        monkeypatch.setattr(game_names, "_3ds_priority", {})
        monkeypatch.setattr(game_names, "_3ds_title_ids", {})
        monkeypatch.setattr(game_names, "_3ds_title_id_priority", {})
        monkeypatch.setattr(game_names, "_3ds_serial_to_title_id", {})
        monkeypatch.setattr(game_names, "_3ds_by_slug", {})
        monkeypatch.setattr(game_names, "_3ds_title_ids_by_slug", {})
        monkeypatch.setattr(game_names, "_3ds_title_priority", {})

        added = game_names.load_libretro_dat_to_dicts(dat_path)

        assert added == 1
        assert game_names.lookup_names_typed(["000400000F707000"]) == {
            "000400000F707000": ("BlockForm (USA)", "3DS")
        }
        assert game_names.lookup_disc_serial("3DS", "BlockForm (USA).cia") == "000400000F707000"

    def test_normalize_endpoint_uses_3ds_title_id_lookup(
        self, client, auth_headers, monkeypatch
    ):
        class FakeNormalizer:
            def normalize(self, system, filename, crc32=None):
                return {
                    "canonical_name": "Mario Kart 7 (USA)",
                    "slug": "mario_kart_7_usa",
                    "source": "dat_filename",
                }

            def search_candidates(self, system, filename):
                return ["Mario Kart 7 (USA)", "Mario Kart 7 (Europe)"]

        from app.services import dat_normalizer

        monkeypatch.setattr(dat_normalizer, "get", lambda: FakeNormalizer())
        monkeypatch.setattr(
            game_names,
            "_3ds_by_slug",
            {"mario_kart_7_usa": "0004000000030800"},
        )
        monkeypatch.setattr(
            game_names,
            "_3ds_title_ids_by_slug",
            {"mario_kart_7_usa": ["0004000000030800"]},
        )

        r = client.post(
            "/api/v1/normalize/batch",
            json={"roms": [{"system": "3DS", "filename": "Mario Kart 7 (USA).3ds"}]},
            headers=auth_headers,
        )

        assert r.status_code == 200
        result = r.json()["results"][0]
        assert result["canonical_name"] == "Mario Kart 7 (USA)"
        assert result["title_id"] == "0004000000030800"


class TestPs2VmcImport:
    def test_import_splits_card_into_per_game_saves(self, client, auth_headers):
        from app.services import ps2mc

        games = {
            "BASLUS-20312": [("icon.sys", b"\x01" * 964), ("save.bin", b"A" * 5000)],
            "BESLES-50490": [("data", b"B" * 2048)],
            "BADATA-SYSTEM": [("sys", b"C" * 16)],  # no serial -> skipped
        }
        card = ps2mc.build_card(games)

        r = client.post(
            "/api/v1/saves/ps2-vmc/import",
            content=card,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )
        assert r.status_code == 200
        body = r.json()
        serials = {row["serial"] for row in body["imported"]}
        assert serials == {"SLUS20312", "SLES50490"}
        assert body["skipped"] == ["BADATA-SYSTEM"]

        # Each imported game is now downloadable as its own card and round-trips.
        dl = client.get("/api/v1/saves/SLUS20312/ps2-card", headers=auth_headers)
        assert dl.status_code == 200
        parsed = ps2mc.parse_card(dl.content)
        assert dict(parsed["BASLUS-20312"]) == dict(games["BASLUS-20312"])

    def test_import_accepts_ecc_ps2_image(self, client, auth_headers):
        from app.services import ps2mc
        from app.services.ps2_cards import add_ecc

        card = ps2mc.build_card({"BASLUS-20312": [("s", b"Z" * 100)]})
        r = client.post(
            "/api/v1/saves/ps2-vmc/import",
            content=add_ecc(card),
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )
        assert r.status_code == 200
        assert r.json()["imported"][0]["serial"] == "SLUS20312"

    def test_import_rejects_non_card(self, client, auth_headers):
        r = client.post(
            "/api/v1/saves/ps2-vmc/import",
            content=b"not a card",
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )
        assert r.status_code == 400


class TestGcVmcImport:
    def _make_card(self, game_code="GM8E", fill=0x5A):
        import struct

        from app.services.gc_cards import gc_card_from_gci

        de = bytearray(b"\x00" * 64)
        de[0:4] = game_code.encode("ascii")
        de[4:6] = b"01"
        struct.pack_into(">H", de, 54, 99)   # source first_block
        struct.pack_into(">H", de, 56, 1)    # 1 block
        gci = bytes(de) + bytes([fill]) * 8192
        return gc_card_from_gci(gci), gci

    def test_import_splits_card_into_per_game_saves(self, client, auth_headers):
        card, gci = self._make_card("GM8E")
        r = client.post(
            "/api/v1/saves/gc-vmc/import",
            content=card,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )
        assert r.status_code == 200
        assert r.json()["imported"][0]["title_id"] == "GC_GM8E"

        dl = client.get("/api/v1/saves/GC_GM8E/gc-card?format=gci", headers=auth_headers)
        assert dl.status_code == 200
        assert dl.content[64:] == gci[64:]

    def test_import_rejects_non_card(self, client, auth_headers):
        r = client.post(
            "/api/v1/saves/gc-vmc/import",
            content=b"not a card",
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )
        assert r.status_code == 400

    def test_lowercase_gc_title_id_hits_the_same_save(self, client, auth_headers):
        """The Dolphin scanners emit GC_gm8e while the GC/Wii U homebrew and
        this import emit GC_GM8E.  Both must address one storage key or the
        same game duplicates on the server."""
        card, gci = self._make_card("GM8E")
        r = client.post(
            "/api/v1/saves/gc-vmc/import",
            content=card,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )
        assert r.status_code == 200

        dl = client.get("/api/v1/saves/GC_gm8e/gc-card?format=gci", headers=auth_headers)
        assert dl.status_code == 200
        assert dl.content[64:] == gci[64:]

        # And only one title is listed, under the canonical uppercase ID.
        titles = client.get("/api/v1/titles", headers=auth_headers).json()
        gc_ids = [
            t["title_id"] for t in titles["titles"] if t["title_id"].upper().startswith("GC_")
        ]
        assert gc_ids == ["GC_GM8E"]

    def test_lowercase_gc_upload_lands_on_canonical_id(self, client, auth_headers):
        """A lowercase-ID upload must not create a second directory."""
        card, _ = self._make_card("GM4E")
        r = client.post(
            "/api/v1/saves/GC_gm4e/gc-card?format=raw",
            content=card,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )
        assert r.status_code == 200

        meta = client.get("/api/v1/saves/GC_GM4E/meta", headers=auth_headers)
        assert meta.status_code == 200
        assert meta.json()["title_id"] == "GC_GM4E"


class TestCodeFormTitleIdCanonicalisation:
    """GC_/WII_ IDs carry a 4-char gamecode, which is case-insensitive.
    Real slug IDs (GBA_zelda_the_minish_cap) must keep their lowercase slug."""

    def test_gamecode_form_is_uppercased(self):
        from app.models.save import validate_any_title_id

        assert validate_any_title_id("GC_grse") == "GC_GRSE"
        assert validate_any_title_id("GC_GRSE") == "GC_GRSE"
        assert validate_any_title_id("gc_grse") == "GC_GRSE"
        assert validate_any_title_id("WII_rmce") == "WII_RMCE"

    def test_slug_ids_keep_their_case(self):
        from app.models.save import validate_any_title_id

        assert (
            validate_any_title_id("GBA_zelda_the_minish_cap")
            == "GBA_zelda_the_minish_cap"
        )
        # A 4-char slug on a slug-strategy system is NOT a gamecode.
        assert validate_any_title_id("GBA_doom") == "GBA_doom"
        assert validate_any_title_id("SAT_GS-9188") == "SAT_GS-9188"

    def test_is_code_form_predicate(self):
        from shared.sync_id import is_code_form_title_id

        assert is_code_form_title_id("GC_grse") is True
        assert is_code_form_title_id("WII_RMCE") is True
        assert is_code_form_title_id("GBA_doom") is False
        assert is_code_form_title_id("GC_zelda_wind_waker") is False
        assert is_code_form_title_id("") is False


class TestPs2Files:
    """Physical-card path: P2FD folder payload <-> stored single-game card."""

    def test_push_folder_then_download_as_files(self, client, auth_headers):
        from app.services import ps2mc

        files = [("icon.sys", b"\x07" * 964), ("BASLUS-20312.save", b"S" * 6000)]
        payload = ps2mc.build_p2fd("BASLUS-20312", files)

        up = client.post(
            "/api/v1/saves/SLUS20312/ps2-files",
            content=payload,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )
        assert up.status_code == 200

        # Downloadable both as a P2FD folder and as a full card (cross-source).
        dl = client.get("/api/v1/saves/SLUS20312/ps2-files", headers=auth_headers)
        assert dl.status_code == 200
        assert dl.headers["X-Save-Dir"] == "BASLUS-20312"
        got_dir, got_files = ps2mc.parse_p2fd(dl.content)
        assert got_dir == "BASLUS-20312"
        assert dict(got_files) == dict(files)

        card = client.get("/api/v1/saves/SLUS20312/ps2-card", headers=auth_headers)
        assert card.status_code == 200
        assert dict(ps2mc.parse_card(card.content)["BASLUS-20312"]) == dict(files)

    def test_vmc_import_then_files_download(self, client, auth_headers):
        """A save imported from a VMC is restorable to a physical card via P2FD."""
        from app.services import ps2mc

        files = [("data.bin", b"D" * 3000)]
        card = ps2mc.build_card({"BESLES-50490": files})
        client.post(
            "/api/v1/saves/ps2-vmc/import",
            content=card,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )

        dl = client.get("/api/v1/saves/SLES50490/ps2-files", headers=auth_headers)
        assert dl.status_code == 200
        got_dir, got_files = ps2mc.parse_p2fd(dl.content)
        assert got_dir == "BESLES-50490"
        assert dict(got_files) == dict(files)

    def test_push_rejects_garbage(self, client, auth_headers):
        r = client.post(
            "/api/v1/saves/SLUS20312/ps2-files",
            content=b"nope",
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )
        assert r.status_code == 400


class TestPs1VmcImport:
    def test_import_splits_card_and_downloads(self, client, auth_headers):
        import struct
        from app.services import ps1mc

        card = ps1mc.format_empty_card()

        def place(block, name, data):
            padded = data + b"\x00" * (ps1mc.BLOCK_SIZE - len(data))
            card[block * ps1mc.BLOCK_SIZE:(block + 1) * ps1mc.BLOCK_SIZE] = padded
            fr = bytearray(ps1mc.FRAME_SIZE)
            fr[0] = ps1mc.ST_FIRST
            struct.pack_into("<I", fr, 0x04, ps1mc.BLOCK_SIZE)
            struct.pack_into("<H", fr, 0x08, ps1mc.NO_NEXT)
            nm = name.encode("ascii")
            fr[0x0A:0x0A + len(nm)] = nm
            fr[0x7F] = ps1mc._xor(fr[:0x7F])
            card[block * ps1mc.FRAME_SIZE:(block + 1) * ps1mc.FRAME_SIZE] = fr

        save_a = bytes(range(256)) * 8  # 2048 bytes
        place(1, "BASLUS-00067HERO", save_a)
        place(2, "BESLES-12345QUEST", os.urandom(2048))

        r = client.post(
            "/api/v1/saves/ps1-vmc/import",
            content=bytes(card),
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )
        assert r.status_code == 200
        serials = {row["serial"] for row in r.json()["imported"]}
        assert serials == {"SLUS00067", "SLES12345"}

        # The imported save is downloadable as a single-save card and round-trips.
        dl = client.get("/api/v1/saves/SLUS00067/ps1-card", headers=auth_headers)
        assert dl.status_code == 200
        parsed = dict(ps1mc.parse_card(dl.content))
        assert "BASLUS-00067HERO" in parsed
        assert parsed["BASLUS-00067HERO"][:2048] == save_a

    def test_import_accepts_vmp(self, client, auth_headers):
        from app.services import ps1mc
        from app.services.ps1_cards import create_vmp

        card = ps1mc.build_single_save_card("BASLUS-00067HERO", os.urandom(8192))
        r = client.post(
            "/api/v1/saves/ps1-vmc/import",
            content=create_vmp(card),
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )
        assert r.status_code == 200
        assert r.json()["imported"][0]["serial"] == "SLUS00067"

    def test_import_rejects_non_card(self, client, auth_headers):
        r = client.post(
            "/api/v1/saves/ps1-vmc/import",
            content=b"junk",
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )
        assert r.status_code == 400


class TestWiiUTitles:
    """`/titles?console_type=WIIU` is how the Wii U client lists its saves.

    Also covers WII_<code>, the vWii id scheme, which resolves through the
    existing emulator-style SYSTEM_slug parser with no server change.
    """

    def test_titles_filter_wiiu(self, client, auth_headers):
        wiiu = _make_ps1_bundle_bytes(title_id="0005000010143500")
        client.post(
            "/api/v1/saves/0005000010143500",
            content=wiiu,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )
        threeds = _make_ps1_bundle_bytes(title_id="0004000000055D00")
        client.post(
            "/api/v1/saves/0004000000055D00",
            content=threeds,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )

        r = client.get("/api/v1/titles?console_type=WIIU", headers=auth_headers)
        assert r.status_code == 200
        titles = r.json()["titles"]
        assert [t["title_id"] for t in titles] == ["0005000010143500"]
        assert titles[0]["console_type"] == "WIIU"

    def test_titles_filter_vwii(self, client, auth_headers):
        vwii = _make_ps1_bundle_bytes(title_id="WII_RMCE")
        client.post(
            "/api/v1/saves/WII_RMCE",
            content=vwii,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )

        r = client.get("/api/v1/titles?console_type=WII", headers=auth_headers)
        assert r.status_code == 200
        titles = r.json()["titles"]
        assert [t["title_id"] for t in titles] == ["WII_RMCE"]
        assert titles[0]["console_type"] == "WII"


class TestWiiUGameNames:
    """A Wii U save can only be named by the client that has its meta.xml.

    No DAT can resolve a 16-hex Wii U title id — its low word is not the
    product code — so a save uploaded without a hint is listed as raw hex by
    every client, and the desktop app (which has no console NAND or Cemu
    install to read) can never show anything better.
    """

    WIIU_TID = "0005000010143500"

    def _upload(self, client, auth_headers, params: str = "") -> None:
        bundle = _make_bundle_bytes(
            title_id=0x0005000010143500,
            files=[("common/data.bin", b"wiiu save")],
        )
        r = client.post(
            f"/api/v1/saves/{self.WIIU_TID}{params}",
            content=bundle,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )
        assert r.status_code in (200, 201)

    def _stored_name(self, client, auth_headers) -> str:
        r = client.get("/api/v1/titles?console_type=WIIU", headers=auth_headers)
        assert r.status_code == 200
        return r.json()["titles"][0]["game_name"]

    def test_upload_without_hint_keeps_raw_id(self, client, auth_headers):
        self._upload(client, auth_headers)
        assert self._stored_name(client, auth_headers) == self.WIIU_TID

    def test_upload_game_name_hint_is_used(self, client, auth_headers):
        self._upload(client, auth_headers, "?game_name=Super%20Mario%203D%20World")
        assert self._stored_name(client, auth_headers) == "Super Mario 3D World"

    def test_dat_name_wins_over_client_hint(self, client, auth_headers):
        """The DAT is authoritative — a client hint only fills a blank."""
        game_names._wiiu_names["ARDE"] = "Canonical DAT Name"
        try:
            self._upload(
                client, auth_headers, "?game_code=WIIU_ARDE&game_name=Client%20Name"
            )
            assert self._stored_name(client, auth_headers) == "Canonical DAT Name"
        finally:
            game_names._wiiu_names.pop("ARDE", None)

    def test_update_names_backfills_by_name(self, client, auth_headers):
        """Backfill for saves the console already uploaded under a raw id."""
        self._upload(client, auth_headers)

        r = client.post(
            "/api/v1/titles/update_names",
            json={"names": {self.WIIU_TID: "Splatoon"}},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert self._stored_name(client, auth_headers) == "Splatoon"

    def test_update_names_backfills_by_code(self, client, auth_headers):
        self._upload(client, auth_headers)
        game_names._wiiu_names["ARDE"] = "Canonical DAT Name"
        try:
            r = client.post(
                "/api/v1/titles/update_names",
                json={
                    "codes": {self.WIIU_TID: "WIIU_ARDE"},
                    "names": {self.WIIU_TID: "Client Name"},
                },
                headers=auth_headers,
            )
            assert r.status_code == 200
            assert self._stored_name(client, auth_headers) == "Canonical DAT Name"
        finally:
            game_names._wiiu_names.pop("ARDE", None)

    def test_update_names_never_overwrites_a_real_name(self, client, auth_headers):
        self._upload(client, auth_headers, "?game_name=Real%20Name")

        r = client.post(
            "/api/v1/titles/update_names",
            json={"names": {self.WIIU_TID: "Wrong Name"}},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert self._stored_name(client, auth_headers) == "Real Name"
