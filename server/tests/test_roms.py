import json
import sys
import zipfile
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from app.config import settings


def _load_server_game_name_data():
    from app.services import game_names

    data_dir = Path(__file__).resolve().parents[1] / "data"
    dats_dir = data_dir / "dats"

    game_names.load_libretro_dat_to_dicts(dats_dir / "Nintendo - Nintendo 3DS.dat")
    game_names.load_libretro_dat_to_dicts(
        dats_dir / "Nintendo - Nintendo 3DS (Digital).dat"
    )


@pytest.fixture()
def rom_dir(tmp_path):
    d = tmp_path / "roms"
    d.mkdir()
    return d


@pytest.fixture()
def rom_client(rom_dir, client, auth_headers):
    from app.services import rom_db, rom_scanner

    original = settings.rom_dir
    settings.rom_dir = rom_dir
    original_interval = settings.rom_scan_interval
    settings.rom_scan_interval = 0

    rom_db.init_db(settings.save_dir)
    _load_server_game_name_data()

    (rom_dir / "gba").mkdir()
    (rom_dir / "gba" / "test rom.gba").write_bytes(b"\x00" * 100)

    (rom_dir / "snes").mkdir()
    (rom_dir / "snes" / "Super Mario World (USA).sfc").write_bytes(b"\x01" * 200)

    rom_scanner.init(rom_dir)

    yield client

    settings.rom_dir = original
    settings.rom_scan_interval = original_interval
    rom_scanner._catalog = None


@pytest.fixture()
def rom_client_ps1_eboot(rom_dir, client, auth_headers):
    """Fixture that wires up a stub ``popstation`` command for PS1 → PSP
    EBOOT.PBP conversion.  The stub just prefixes ``PBP:`` to whatever
    bytes the input file contains so tests can assert content end-to-end
    without needing a real popstation install on CI runners."""
    from app.services import rom_db, rom_scanner

    original_rom_dir = settings.rom_dir
    original_interval = settings.rom_scan_interval
    original_cmd = settings.rom_ps1_eboot_command
    original_cwd = settings.rom_ps1_eboot_cwd

    settings.rom_dir = rom_dir
    settings.rom_scan_interval = 0
    settings.rom_ps1_eboot_cwd = ""
    settings.rom_ps1_eboot_command = json.dumps(
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; import sys; "
                "Path(sys.argv[2]).write_bytes(b'PBP:' + Path(sys.argv[1]).read_bytes())"
            ),
            "{input}",
            "{output}",
        ]
    )

    rom_db.init_db(settings.save_dir)
    _load_server_game_name_data()

    (rom_dir / "psx").mkdir()
    iso = rom_dir / "psx" / "Crash Bandicoot (USA).iso"
    iso.write_bytes(b"DISC")

    rom_scanner.init(rom_dir)

    yield client

    settings.rom_dir = original_rom_dir
    settings.rom_scan_interval = original_interval
    settings.rom_ps1_eboot_command = original_cmd
    settings.rom_ps1_eboot_cwd = original_cwd
    rom_scanner._catalog = None


@pytest.fixture()
def rom_client_3ds_zip(rom_dir, client, auth_headers):
    from app.services import rom_db, rom_scanner

    original_rom_dir = settings.rom_dir
    original_interval = settings.rom_scan_interval
    original_cia_cmd = settings.rom_3ds_cia_command
    original_decrypted_cci_cmd = settings.rom_3ds_decrypted_cci_command

    settings.rom_dir = rom_dir
    settings.rom_scan_interval = 0
    settings.rom_3ds_cia_command = json.dumps(
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; import sys; "
                "Path(sys.argv[2]).write_bytes(b'CIA:' + Path(sys.argv[1]).read_bytes())"
            ),
            "{input}",
            "{output}",
        ]
    )
    settings.rom_3ds_decrypted_cci_command = json.dumps(
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; import sys; "
                "Path(sys.argv[2]).write_bytes(b'DCCI:' + Path(sys.argv[1]).read_bytes())"
            ),
            "{input}",
            "{output}",
        ]
    )

    rom_db.init_db(settings.save_dir)
    _load_server_game_name_data()

    (rom_dir / "n3ds").mkdir()
    archive_path = rom_dir / "n3ds" / "Super Mario 3D Land (USA).3ds.zip"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Super Mario 3D Land (USA).3ds", b"CARTROM")

    rom_scanner.init(rom_dir)

    yield client

    settings.rom_dir = original_rom_dir
    settings.rom_scan_interval = original_interval
    settings.rom_3ds_cia_command = original_cia_cmd
    settings.rom_3ds_decrypted_cci_command = original_decrypted_cci_cmd
    rom_scanner._catalog = None


@pytest.fixture()
def rom_client_cci_zip(rom_dir, client, auth_headers):
    from app.services import rom_db, rom_scanner

    original_rom_dir = settings.rom_dir
    original_interval = settings.rom_scan_interval
    original_cia_cmd = settings.rom_3ds_cia_command
    original_decrypted_cci_cmd = settings.rom_3ds_decrypted_cci_command

    settings.rom_dir = rom_dir
    settings.rom_scan_interval = 0
    settings.rom_3ds_cia_command = json.dumps(
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; import sys; "
                "Path(sys.argv[2]).write_bytes(b'CIA:' + Path(sys.argv[1]).read_bytes())"
            ),
            "{input}",
            "{output}",
        ]
    )
    settings.rom_3ds_decrypted_cci_command = json.dumps(
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; import sys; "
                "Path(sys.argv[2]).write_bytes(b'DCCI:' + Path(sys.argv[1]).read_bytes())"
            ),
            "{input}",
            "{output}",
        ]
    )

    rom_db.init_db(settings.save_dir)
    _load_server_game_name_data()

    (rom_dir / "n3ds").mkdir()
    archive_path = rom_dir / "n3ds" / "Pilotwings Resort (USA).cci.zip"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Pilotwings Resort (USA).cci", b"CCICART")

    rom_scanner.init(rom_dir)

    yield client

    settings.rom_dir = original_rom_dir
    settings.rom_scan_interval = original_interval
    settings.rom_3ds_cia_command = original_cia_cmd
    settings.rom_3ds_decrypted_cci_command = original_decrypted_cci_cmd
    rom_scanner._catalog = None


class TestRomCatalog:
    def test_dat_normalizer_uses_aliases_for_translated_titles(self, tmp_path):
        from app.services.dat_normalizer import DatNormalizer

        dats_dir = tmp_path / "dats"
        dats_dir.mkdir()
        (dats_dir / "EN-Dats").mkdir()

        (dats_dir / "Nintendo - Nintendo Entertainment System.dat").write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
<datafile>
  <game name="Ganbare Goemon! - Karakuri Douchuu (Japan)">
    <rom name="Ganbare Goemon! - Karakuri Douchuu (Japan).nes" crc="12345678" />
  </game>
</datafile>
""",
            encoding="utf-8",
        )
        (dats_dir / "EN-Dats" / "aliases.json").write_text(
            """{
  "NES": {
    "Mystical Ninja (Japan)": "Ganbare Goemon! - Karakuri Douchuu (Japan)",
    "Broken Legacy Alias (Japan)": "Missing Canonical Target (Japan)"
  }
}
""",
            encoding="utf-8",
        )

        norm = DatNormalizer(dats_dir)

        translated = norm.normalize("NES", "Mystical Ninja (Japan) [T-En v1.0].nes")
        assert translated["canonical_name"] == "Ganbare Goemon! - Karakuri Douchuu (Japan)"
        assert translated["source"] == "dat_alias"
        assert norm.search_candidates(
            "NES", "Mystical Ninja (Japan) [T-En v1.0].nes"
        ) == ["Ganbare Goemon! - Karakuri Douchuu (Japan)"]

        broken = norm.normalize("NES", "Broken Legacy Alias (Japan).nes")
        assert broken["canonical_name"] == "Broken Legacy Alias (Japan)"
        assert broken["source"] == "filename"

    def test_list_roms(self, rom_client, auth_headers):
        resp = rom_client.get("/api/v1/roms", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        titles = [r["title_id"] for r in body["roms"]]
        rom_ids = [r["rom_id"] for r in body["roms"]]
        assert "GBA_test_rom" in titles
        assert "SNES_super_mario_world_usa" in titles
        assert "GBA_test_rom" in rom_ids

    def test_filter_by_system(self, rom_client, auth_headers):
        resp = rom_client.get("/api/v1/roms?system=GBA", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["roms"][0]["system"] == "GBA"

    def test_search_roms(self, rom_client, auth_headers):
        resp = rom_client.get("/api/v1/roms?search=mario", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert "mario" in body["roms"][0]["name"].lower()

    def test_list_systems(self, rom_client, auth_headers):
        resp = rom_client.get("/api/v1/roms/systems", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert "GBA" in body["systems"]
        assert "SNES" in body["systems"]
        assert body["stats"]["GBA"] == 1

    def test_no_rom_dir(self, client, auth_headers):
        resp = client.get("/api/v1/roms", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_list_roms_pagination(self, rom_client, auth_headers):
        # First page of 1 → total still reflects the full filtered count,
        # and has_more is true because page < total.
        resp = rom_client.get("/api/v1/roms?limit=1&offset=0", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert body["offset"] == 0
        assert body["limit"] == 1
        assert body["has_more"] is True
        assert len(body["roms"]) == 1

        # Second page of 1 exhausts the result set.
        resp = rom_client.get("/api/v1/roms?limit=1&offset=1", headers=auth_headers)
        body = resp.json()
        assert body["total"] == 2
        assert body["offset"] == 1
        assert body["has_more"] is False
        assert len(body["roms"]) == 1

        # Offset past the end — empty page, but total + has_more still sane.
        resp = rom_client.get("/api/v1/roms?limit=1&offset=5", headers=auth_headers)
        body = resp.json()
        assert body["total"] == 2
        assert body["has_more"] is False
        assert body["roms"] == []

    def test_list_roms_limit_applies_after_filters(self, rom_client, auth_headers):
        # Filtering narrows to 1 row; a larger limit must still report total=1.
        resp = rom_client.get(
            "/api/v1/roms?system=GBA&limit=50", headers=auth_headers
        )
        body = resp.json()
        assert body["total"] == 1
        assert body["has_more"] is False
        assert len(body["roms"]) == 1

    def test_list_roms_exposes_3ds_zip_conversion_options(
        self, rom_client_3ds_zip, auth_headers
    ):
        resp = rom_client_3ds_zip.get("/api/v1/roms?system=3DS", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1

        rom = body["roms"][0]
        assert rom["title_id"] == "0004000000054000"
        assert rom["extract_format"] == "3ds"
        assert rom["extract_formats"] == ["cia", "decrypted_cci"]

    def test_list_roms_exposes_cci_zip_conversion_options(
        self, rom_client_cci_zip, auth_headers
    ):
        resp = rom_client_cci_zip.get("/api/v1/roms?system=3DS", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1

        rom = body["roms"][0]
        assert rom["title_id"] == "0004000000031C00"
        assert rom["extract_format"] == "3ds"
        assert rom["extract_formats"] == ["cia", "decrypted_cci"]

    def test_scan_picks_up_ps3_iso_at_top_level(self, tmp_path):
        """Top-level PS3 ISO under ``<rom_dir>/ps3/`` is cataloged with
        system=PS3.  Baseline for the subfolder PKG test below.
        """
        from app.services import rom_db, rom_scanner

        rom_db.init_db(tmp_path)
        rom_dir = tmp_path / "roms"
        (rom_dir / "ps3").mkdir(parents=True)
        iso = rom_dir / "ps3" / "Demon's Souls [BLUS30443].iso"
        iso.write_bytes(b"x" * 1024)

        catalog = rom_scanner.RomCatalog()
        catalog.scan(rom_dir, use_crc32=False)

        ps3_entries = catalog.list_by_system("PS3")
        assert len(ps3_entries) == 1
        assert ps3_entries[0].filename == "Demon's Souls [BLUS30443].iso"

    def test_scan_groups_ps3_subfolders_as_bundles(self, tmp_path):
        """PS3 PKGs under per-game subfolders collapse into one bundle
        entry per subfolder; loose .pkg at the top level is dropped.

        Replaces the old per-file behaviour: each subfolder is now a
        single catalog row whose ``path`` is the subfolder (not the .pkg
        file) and whose ``bundle_files`` list captures the contents.
        """
        from app.services import rom_db, rom_scanner

        rom_db.init_db(tmp_path)
        rom_dir = tmp_path / "roms"

        (rom_dir / "ps3" / "Journey").mkdir(parents=True)
        (rom_dir / "ps3" / "Journey" / "Journey [NPUB30564].pkg").write_bytes(
            b"a" * 512
        )

        (rom_dir / "ps3" / "dlc").mkdir(parents=True)
        (rom_dir / "ps3" / "dlc" / "BLJM-61063 BGM DLC Pack.pkg").write_bytes(
            b"b" * 256
        )

        # Top-level PKG is intentionally dropped (no game name available).
        (rom_dir / "ps3" / "Vampire Resurrection [BLJM60567].pkg").write_bytes(
            b"c" * 128
        )

        catalog = rom_scanner.RomCatalog()
        catalog.scan(rom_dir, use_crc32=False)

        ps3_entries = catalog.list_by_system("PS3")
        # Two bundles, no loose top-level entry.
        assert len(ps3_entries) == 2
        assert all(e.is_bundle for e in ps3_entries)

        names = sorted(e.name for e in ps3_entries)
        assert names == ["Journey", "dlc"]

        paths = sorted(e.path for e in ps3_entries)
        assert "ps3/Journey" in paths
        assert "ps3/dlc" in paths

    def test_scan_picks_up_ps3_iso_and_bundle_mixed(self, tmp_path):
        """Mixed PS3 catalog: top-level .iso → individual entry, per-game
        subfolder with .pkg → bundle entry.  Both end up under system=PS3
        but with different ``is_bundle`` flags.
        """
        from app.services import rom_db, rom_scanner

        rom_db.init_db(tmp_path)
        rom_dir = tmp_path / "roms"
        (rom_dir / "ps3").mkdir(parents=True)
        (rom_dir / "ps3" / "Skate 3 [BLUS30464].iso").write_bytes(b"x" * 1024)

        (rom_dir / "ps3" / "Journey").mkdir()
        (rom_dir / "ps3" / "Journey" / "Journey [NPUB30564].pkg").write_bytes(
            b"y" * 512
        )

        catalog = rom_scanner.RomCatalog()
        catalog.scan(rom_dir, use_crc32=False)

        ps3_entries = catalog.list_by_system("PS3")
        assert len(ps3_entries) == 2
        # Sort to make the assertions stable regardless of scan order.
        by_kind = sorted(ps3_entries, key=lambda e: e.is_bundle)
        iso_entry, bundle_entry = by_kind[0], by_kind[1]
        assert iso_entry.is_bundle is False
        assert iso_entry.filename.endswith(".iso")
        assert bundle_entry.is_bundle is True
        assert bundle_entry.name == "Journey"

    def test_scan_xbox_cci_bundle_and_iso(self, tmp_path):
        """Xbox CCI subfolders collapse into one bundle, while ISO files
        stay regular entries. Loose top-level .cci files are skipped so
        launchers don't get separated from the game image.
        """
        from app.services import rom_db, rom_scanner

        rom_db.init_db(tmp_path)
        rom_dir = tmp_path / "roms"

        bundle = rom_dir / "xbox" / "Halo"
        bundle.mkdir(parents=True)
        (bundle / "Halo.cci").write_bytes(b"C" * 1024)
        (bundle / "default.xbe").write_bytes(b"X" * 128)

        (rom_dir / "xbox" / "Skate 3.iso").write_bytes(b"I" * 2048)
        (rom_dir / "xbox" / "Loose.cci").write_bytes(b"L" * 512)

        catalog = rom_scanner.RomCatalog()
        catalog.scan(rom_dir, use_crc32=False)

        xbox = catalog.list_by_system("XBOX")
        assert len(xbox) == 2
        bundles = [e for e in xbox if e.is_bundle]
        loose = [e for e in xbox if not e.is_bundle]
        assert len(bundles) == 1
        assert len(loose) == 1

        assert bundles[0].name == "Halo"
        assert bundles[0].filename == "Halo.zip"
        assert sorted(f["name"] for f in bundles[0].bundle_files) == [
            "Halo.cci",
            "default.xbe",
        ]
        assert loose[0].filename == "Skate 3.iso"

    def test_scan_ps3_bundle_groups_pkg_and_rap(self, tmp_path):
        """A PS3 subfolder with a .pkg + .rap collapses to one bundle
        entry; the file list is preserved on the catalog row so the
        client can iterate it without a second scan.
        """
        from app.services import rom_db, rom_scanner

        rom_db.init_db(tmp_path)
        rom_dir = tmp_path / "roms"

        (rom_dir / "ps3" / "Vampire Resurrection [BLJM60567]").mkdir(parents=True)
        bundle = rom_dir / "ps3" / "Vampire Resurrection [BLJM60567]"
        (bundle / "BLJM-60567.pkg").write_bytes(b"a" * 1024)
        (bundle / "BLJM-60567.rap").write_bytes(b"b" * 256)

        catalog = rom_scanner.RomCatalog()
        catalog.scan(rom_dir, use_crc32=False)

        ps3 = catalog.list_by_system("PS3")
        assert len(ps3) == 1
        e = ps3[0]
        assert e.is_bundle is True
        assert e.name == "Vampire Resurrection [BLJM60567]"
        # Total size accounts for every kept file in the bundle.
        assert e.size == 1024 + 256
        names = sorted(f["name"] for f in e.bundle_files)
        assert names == ["BLJM-60567.pkg", "BLJM-60567.rap"]

    def test_scan_ps3_skips_loose_pkg_at_top_level(self, tmp_path):
        """``<rom_dir>/ps3/foo.pkg`` (no containing subfolder) is skipped
        per the operator policy — there's no game name to display, and
        the client wouldn't know how to label it.
        """
        from app.services import rom_db, rom_scanner

        rom_db.init_db(tmp_path)
        rom_dir = tmp_path / "roms"
        (rom_dir / "ps3").mkdir(parents=True)
        (rom_dir / "ps3" / "loose-NPUB30024.pkg").write_bytes(b"x" * 100)
        (rom_dir / "ps3" / "Demon's Souls [BLUS30443].iso").write_bytes(b"y" * 200)

        catalog = rom_scanner.RomCatalog()
        catalog.scan(rom_dir, use_crc32=False)

        ps3 = catalog.list_by_system("PS3")
        assert len(ps3) == 1
        assert ps3[0].filename.endswith(".iso")

    def test_scan_ps3_bundle_with_multi_pkg_dlc(self, tmp_path):
        """Subfolder with several .pkg files (e.g. game + DLC) stays as
        ONE bundle entry — the client decides which ones to install.
        """
        from app.services import rom_db, rom_scanner

        rom_db.init_db(tmp_path)
        rom_dir = tmp_path / "roms"

        bundle = rom_dir / "ps3" / "MyGame"
        bundle.mkdir(parents=True)
        (bundle / "MyGame.pkg").write_bytes(b"x" * 64)
        (bundle / "MyGame DLC1.pkg").write_bytes(b"y" * 32)
        (bundle / "MyGame DLC2.pkg").write_bytes(b"z" * 16)
        (bundle / "MyGame.rap").write_bytes(b"r" * 8)

        catalog = rom_scanner.RomCatalog()
        catalog.scan(rom_dir, use_crc32=False)

        ps3 = catalog.list_by_system("PS3")
        assert len(ps3) == 1
        assert ps3[0].is_bundle is True
        assert len(ps3[0].bundle_files) == 4

    def test_bundle_manifest_endpoint(self, tmp_path, client, auth_headers):
        """``GET /api/v1/roms/{rom_id}/manifest`` returns the file list.

        Used by the PS3 client (and steamdeck) to plan multi-file
        downloads without having to scan the bundle ZIP first.
        """
        from app.services import rom_db, rom_scanner
        from app.config import settings

        original = settings.rom_dir
        try:
            rom_db.init_db(tmp_path)
            rom_dir = tmp_path / "roms"
            settings.rom_dir = rom_dir

            bundle = rom_dir / "ps3" / "Journey"
            bundle.mkdir(parents=True)
            (bundle / "Journey.pkg").write_bytes(b"a" * 100)
            (bundle / "Journey.rap").write_bytes(b"b" * 50)

            rom_scanner.init(rom_dir)
            catalog = rom_scanner.get()
            assert catalog is not None
            ps3_entries = catalog.list_by_system("PS3")
            assert len(ps3_entries) == 1
            rom_id = ps3_entries[0].rom_id

            resp = client.get(
                f"/api/v1/roms/{rom_id}/manifest", headers=auth_headers
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["is_bundle"] is True
            assert body["name"] == "Journey"
            assert body["total_size"] == 150
            names = sorted(f["name"] for f in body["files"])
            assert names == ["Journey.pkg", "Journey.rap"]
        finally:
            settings.rom_dir = original
            rom_scanner._catalog = None

    def test_bundle_per_file_download_endpoint(
        self, tmp_path, client, auth_headers
    ):
        """``GET /api/v1/roms/{rom_id}/file/<name>`` streams one file from
        a bundle.  The PS3 client uses this so it can route .pkg vs .rap
        without downloading the ZIP first.
        """
        from app.services import rom_db, rom_scanner
        from app.config import settings

        original = settings.rom_dir
        try:
            rom_db.init_db(tmp_path)
            rom_dir = tmp_path / "roms"
            settings.rom_dir = rom_dir

            bundle = rom_dir / "ps3" / "Journey"
            bundle.mkdir(parents=True)
            (bundle / "Journey.pkg").write_bytes(b"P" * 100)
            (bundle / "Journey.rap").write_bytes(b"R" * 50)
            translated_dlc = (
                "Dengeki Bunko - Fighting Climax Ignition (Japan) "
                "[T-En by Tsukimori v1.03] (DLC).pkg"
            )
            (bundle / translated_dlc).write_bytes(b"DLC")

            rom_scanner.init(rom_dir)
            rom_id = rom_scanner.get().list_by_system("PS3")[0].rom_id

            r1 = client.get(
                f"/api/v1/roms/{rom_id}/file/Journey.pkg",
                headers=auth_headers,
            )
            assert r1.status_code == 200
            assert r1.content == b"P" * 100

            r2 = client.get(
                f"/api/v1/roms/{rom_id}/file/Journey.rap",
                headers=auth_headers,
            )
            assert r2.status_code == 200
            assert r2.content == b"R" * 50

            encoded_dlc = quote(translated_dlc, safe="/")
            r3 = client.get(
                f"/api/v1/roms/{rom_id}/file/{encoded_dlc}",
                headers=auth_headers,
            )
            assert r3.status_code == 200
            assert r3.content == b"DLC"

            # Path traversal guard.
            r4 = client.get(
                f"/api/v1/roms/{rom_id}/file/../escape.txt",
                headers=auth_headers,
            )
            assert r4.status_code in (400, 404)
        finally:
            settings.rom_dir = original
            rom_scanner._catalog = None

    def test_xbox_roms_advertise_cci_and_iso_options(
        self, tmp_path, client, auth_headers
    ):
        """The WebUI should see exactly the two Xbox target formats for
        both source layouts: CCI bundle and ISO file.
        """
        from app.services import rom_db, rom_scanner
        from app.config import settings

        original = settings.rom_dir
        try:
            rom_db.init_db(settings.save_dir)
            rom_dir = tmp_path / "roms"
            settings.rom_dir = rom_dir

            bundle = rom_dir / "xbox" / "Halo"
            bundle.mkdir(parents=True)
            (bundle / "Halo.cci").write_bytes(b"CCIDISC")
            (bundle / "default.xbe").write_bytes(b"LAUNCHER")
            (rom_dir / "xbox" / "Skate 3.iso").write_bytes(b"ISODISC")

            rom_scanner.init(rom_dir)

            resp = client.get("/api/v1/roms?system=XBOX", headers=auth_headers)
            assert resp.status_code == 200
            roms = sorted(resp.json()["roms"], key=lambda r: r["name"])
            assert len(roms) == 2
            assert roms[0]["extract_formats"] == ["cci", "iso", "folder"]
            assert roms[1]["extract_formats"] == ["cci", "iso", "folder"]
        finally:
            settings.rom_dir = original
            rom_scanner._catalog = None

    def test_xbox_cci_bundle_downloads_as_zip(
        self, tmp_path, client, auth_headers
    ):
        """A raw WebUI CCI download for a bundled Xbox game is a ZIP that
        preserves the .cci and launcher files.
        """
        from app.services import rom_db, rom_scanner
        from app.config import settings
        import io
        import zipfile as zf_mod

        original = settings.rom_dir
        try:
            rom_db.init_db(settings.save_dir)
            rom_dir = tmp_path / "roms"
            settings.rom_dir = rom_dir

            bundle = rom_dir / "xbox" / "Halo"
            bundle.mkdir(parents=True)
            (bundle / "Halo.cci").write_bytes(b"CCIDISC")
            (bundle / "default.xbe").write_bytes(b"LAUNCHER")

            rom_scanner.init(rom_dir)
            rom_id = rom_scanner.get().list_by_system("XBOX")[0].rom_id

            resp = client.get(f"/api/v1/roms/{rom_id}", headers=auth_headers)
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "application/zip"
            with zf_mod.ZipFile(io.BytesIO(resp.content)) as zf:
                assert sorted(zf.namelist()) == ["Halo.cci", "default.xbe"]
                assert zf.read("Halo.cci") == b"CCIDISC"
                assert zf.read("default.xbe") == b"LAUNCHER"
        finally:
            settings.rom_dir = original
            rom_scanner._catalog = None

    def test_scan_ps1_subfolder_becomes_bundle(self, tmp_path):
        """PS1 subfolder containing CD images collapses into a single
        bundle entry — same shape as the PS3 PKG flow but triggered by
        any PS1 ROM extension instead of just ``.pkg``.  Loose
        top-level PS1 files stay individual.
        """
        from app.services import rom_db, rom_scanner

        rom_db.init_db(tmp_path)
        rom_dir = tmp_path / "roms"

        # Bundle: cue + bin tracks + sub sidecar
        bundle = rom_dir / "psx" / "Final Fantasy VII (USA) (Disc 1)"
        bundle.mkdir(parents=True)
        (bundle / "FF7-D1.cue").write_bytes(b"FILE \"FF7-D1 (Track 01).bin\"\n")
        (bundle / "FF7-D1 (Track 01).bin").write_bytes(b"x" * 4096)
        (bundle / "FF7-D1 (Track 02).bin").write_bytes(b"y" * 2048)
        (bundle / "FF7-D1.sub").write_bytes(b"s" * 256)

        # Loose top-level CHD — stays as individual entry.
        (rom_dir / "psx" / "Crash Bandicoot (USA).chd").write_bytes(b"c" * 8192)

        catalog = rom_scanner.RomCatalog()
        catalog.scan(rom_dir, use_crc32=False)

        ps1 = catalog.list_by_system("PS1")
        assert len(ps1) == 2

        bundles = [e for e in ps1 if e.is_bundle]
        loose = [e for e in ps1 if not e.is_bundle]
        assert len(bundles) == 1
        assert len(loose) == 1

        b = bundles[0]
        assert b.name == "Final Fantasy VII (USA) (Disc 1)"
        names = sorted(f["name"] for f in b.bundle_files)
        assert "FF7-D1.cue" in names
        assert "FF7-D1 (Track 01).bin" in names
        assert "FF7-D1 (Track 02).bin" in names
        assert "FF7-D1.sub" in names  # companion kept
        assert b.size == 4096 + 2048 + 256 + len(b"FILE \"FF7-D1 (Track 01).bin\"\n")

        assert loose[0].filename == "Crash Bandicoot (USA).chd"

    def test_ps1_eboot_extract_route(self, rom_client_ps1_eboot, auth_headers):
        """``GET /api/v1/roms/<id>?extract=eboot`` runs the configured
        popstation command and streams an EBOOT.PBP back.  Used by the
        PSP client's ROM Catalog so PS1 games convert into a PBP that
        drops into ms0:/PSP/GAME/<id>/.
        """
        resp = rom_client_ps1_eboot.get(
            "/api/v1/roms?system=PS1", headers=auth_headers
        )
        assert resp.status_code == 200
        roms = resp.json()["roms"]
        assert len(roms) == 1
        rom_id = roms[0]["rom_id"]

        # The catalog row should advertise eboot in its extract_formats.
        assert "eboot" in roms[0].get("extract_formats", [])

        r2 = rom_client_ps1_eboot.get(
            f"/api/v1/roms/{rom_id}?extract=eboot", headers=auth_headers
        )
        assert r2.status_code == 200
        assert r2.headers["content-type"] == "application/octet-stream"
        assert r2.content == b"PBP:DISC"

    def test_ps1_eboot_unconfigured_returns_503(
        self, rom_dir, client, auth_headers
    ):
        """Without a command template the route must surface a 503 with
        a hint pointing at the env var, mirroring the 3DS / Xbox UX so
        operators know exactly what to set."""
        from app.services import rom_db, rom_scanner

        original = settings.rom_dir
        original_cmd = settings.rom_ps1_eboot_command
        try:
            rom_db.init_db(tmp_save := settings.save_dir)
            _load_server_game_name_data()
            settings.rom_dir = rom_dir
            settings.rom_ps1_eboot_command = ""
            (rom_dir / "psx").mkdir()
            (rom_dir / "psx" / "Foo.iso").write_bytes(b"x")
            rom_scanner.init(rom_dir)

            rom_id = rom_scanner.get().list_by_system("PS1")[0].rom_id
            resp = client.get(
                f"/api/v1/roms/{rom_id}?extract=eboot", headers=auth_headers
            )
            assert resp.status_code == 503
            assert "SYNC_ROM_PS1_EBOOT_COMMAND" in resp.text
        finally:
            settings.rom_dir = original
            settings.rom_ps1_eboot_command = original_cmd
            rom_scanner._catalog = None

    def test_bundle_zip_download(self, tmp_path, client, auth_headers):
        """Plain ``GET /api/v1/roms/{rom_id}`` on a bundle returns a ZIP
        with every file inside.  The webUI + steamdeck rely on this.
        """
        from app.services import rom_db, rom_scanner
        from app.config import settings
        import io
        import zipfile as zf_mod

        original = settings.rom_dir
        try:
            rom_db.init_db(tmp_path)
            rom_dir = tmp_path / "roms"
            settings.rom_dir = rom_dir

            bundle = rom_dir / "ps3" / "Journey"
            bundle.mkdir(parents=True)
            (bundle / "Journey.pkg").write_bytes(b"P" * 100)
            (bundle / "Journey.rap").write_bytes(b"R" * 50)

            rom_scanner.init(rom_dir)
            rom_id = rom_scanner.get().list_by_system("PS3")[0].rom_id

            resp = client.get(
                f"/api/v1/roms/{rom_id}", headers=auth_headers
            )
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "application/zip"
            with zf_mod.ZipFile(io.BytesIO(resp.content)) as zf:
                names = sorted(zf.namelist())
                assert names == ["Journey.pkg", "Journey.rap"]
                assert zf.read("Journey.pkg") == b"P" * 100
                assert zf.read("Journey.rap") == b"R" * 50
        finally:
            settings.rom_dir = original
            rom_scanner._catalog = None


class TestRomDownload:
    def test_download_rom(self, rom_client, auth_headers):
        roms = rom_client.get("/api/v1/roms?system=GBA", headers=auth_headers).json()
        rom_id = roms["roms"][0]["rom_id"]

        resp = rom_client.get(f"/api/v1/roms/{rom_id}", headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.content) == 100
        assert resp.headers["accept-ranges"] == "bytes"

    def test_download_not_found(self, rom_client, auth_headers):
        resp = rom_client.get("/api/v1/roms/GBA_nonexistent", headers=auth_headers)
        assert resp.status_code == 404

    def test_range_request(self, rom_client, auth_headers):
        roms = rom_client.get("/api/v1/roms?system=GBA", headers=auth_headers).json()
        rom_id = roms["roms"][0]["rom_id"]

        resp = rom_client.get(
            f"/api/v1/roms/{rom_id}",
            headers={**auth_headers, "Range": "bytes=0-9"},
        )
        assert resp.status_code == 206
        assert len(resp.content) == 10
        assert resp.headers["content-range"] == "bytes 0-9/100"

    def test_range_request_suffix(self, rom_client, auth_headers):
        roms = rom_client.get("/api/v1/roms?system=GBA", headers=auth_headers).json()
        rom_id = roms["roms"][0]["rom_id"]

        resp = rom_client.get(
            f"/api/v1/roms/{rom_id}",
            headers={**auth_headers, "Range": "bytes=-10"},
        )
        assert resp.status_code == 206
        assert len(resp.content) == 10

    def test_range_request_open_end(self, rom_client, auth_headers):
        roms = rom_client.get("/api/v1/roms?system=GBA", headers=auth_headers).json()
        rom_id = roms["roms"][0]["rom_id"]

        resp = rom_client.get(
            f"/api/v1/roms/{rom_id}",
            headers={**auth_headers, "Range": "bytes=50-"},
        )
        assert resp.status_code == 206
        assert len(resp.content) == 50

    def test_range_request_invalid(self, rom_client, auth_headers):
        roms = rom_client.get("/api/v1/roms?system=GBA", headers=auth_headers).json()
        rom_id = roms["roms"][0]["rom_id"]

        resp = rom_client.get(
            f"/api/v1/roms/{rom_id}",
            headers={**auth_headers, "Range": "bytes=200-300"},
        )
        assert resp.status_code == 416

    def test_download_3ds_zip_as_cia(self, rom_client_3ds_zip, auth_headers):
        roms = rom_client_3ds_zip.get("/api/v1/roms?system=3DS", headers=auth_headers).json()
        rom_id = roms["roms"][0]["rom_id"]

        resp = rom_client_3ds_zip.get(
            f"/api/v1/roms/{rom_id}?extract=cia",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.content == b"CIA:CARTROM"
        assert resp.headers["content-disposition"].endswith('filename="Super Mario 3D Land (USA).cia"')

    def test_download_3ds_zip_as_decrypted_cci(self, rom_client_3ds_zip, auth_headers):
        roms = rom_client_3ds_zip.get("/api/v1/roms?system=3DS", headers=auth_headers).json()
        rom_id = roms["roms"][0]["rom_id"]

        resp = rom_client_3ds_zip.get(
            f"/api/v1/roms/{rom_id}?extract=decrypted_cci",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.content == b"DCCI:CARTROM"
        assert resp.headers["content-disposition"].endswith(
            'filename="Super Mario 3D Land (USA).cci"'
        )

    def test_download_cci_zip_as_decrypted_cci(self, rom_client_cci_zip, auth_headers):
        roms = rom_client_cci_zip.get("/api/v1/roms?system=3DS", headers=auth_headers).json()
        rom_id = roms["roms"][0]["rom_id"]

        resp = rom_client_cci_zip.get(
            f"/api/v1/roms/{rom_id}?extract=decrypted_cci",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.content == b"DCCI:CCICART"
        assert resp.headers["content-disposition"].endswith(
            'filename="Pilotwings Resort (USA).cci"'
        )

    def test_download_xbox_iso_as_cci_zip(self, tmp_path, client, auth_headers):
        """ISO source + ?extract=cci runs the configured converter, then
        wraps the produced .cci in a ZIP for WebUI downloads.
        """
        from app.services import rom_db, rom_scanner
        from app.config import settings
        import io
        import zipfile as zf_mod

        original_rom_dir = settings.rom_dir
        original_interval = settings.rom_scan_interval
        original_cmd = settings.rom_xbox_cci_command
        try:
            settings.rom_scan_interval = 0
            settings.rom_xbox_cci_command = json.dumps(
                [
                    sys.executable,
                    "-c",
                    (
                        "from pathlib import Path; import sys; "
                        "Path(sys.argv[2]).write_bytes(b'CCI:' + Path(sys.argv[1]).read_bytes())"
                    ),
                    "{input}",
                    "{output}",
                ]
            )
            rom_db.init_db(settings.save_dir)
            rom_dir = tmp_path / "roms"
            settings.rom_dir = rom_dir
            (rom_dir / "xbox").mkdir(parents=True)
            (rom_dir / "xbox" / "Skate 3.iso").write_bytes(b"ISODISC")

            rom_scanner.init(rom_dir)
            rom_id = rom_scanner.get().list_by_system("XBOX")[0].rom_id

            resp = client.get(
                f"/api/v1/roms/{rom_id}?extract=cci", headers=auth_headers
            )
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "application/zip"
            with zf_mod.ZipFile(io.BytesIO(resp.content)) as zf:
                assert zf.namelist() == ["Skate 3.cci"]
                assert zf.read("Skate 3.cci") == b"CCI:ISODISC"
        finally:
            settings.rom_dir = original_rom_dir
            settings.rom_scan_interval = original_interval
            settings.rom_xbox_cci_command = original_cmd
            rom_scanner._catalog = None

    def test_download_xbox_cci_bundle_as_iso(self, tmp_path, client, auth_headers):
        """CCI bundle + ?extract=iso converts the embedded .cci while the
        plain bundle download stays ZIP-based.
        """
        from app.services import rom_db, rom_scanner
        from app.config import settings

        original_rom_dir = settings.rom_dir
        original_interval = settings.rom_scan_interval
        original_cmd = settings.rom_xbox_iso_command
        try:
            settings.rom_scan_interval = 0
            settings.rom_xbox_iso_command = json.dumps(
                [
                    sys.executable,
                    "-c",
                    (
                        "from pathlib import Path; import sys; "
                        "Path(sys.argv[2]).write_bytes(b'ISO:' + Path(sys.argv[1]).read_bytes())"
                    ),
                    "{input}",
                    "{output}",
                ]
            )
            rom_db.init_db(settings.save_dir)
            rom_dir = tmp_path / "roms"
            settings.rom_dir = rom_dir
            bundle = rom_dir / "xbox" / "Halo"
            bundle.mkdir(parents=True)
            (bundle / "Halo.cci").write_bytes(b"CCIDISC")
            (bundle / "default.xbe").write_bytes(b"LAUNCHER")

            rom_scanner.init(rom_dir)
            rom_id = rom_scanner.get().list_by_system("XBOX")[0].rom_id

            resp = client.get(
                f"/api/v1/roms/{rom_id}?extract=iso", headers=auth_headers
            )
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "application/x-iso9660-image"
            assert resp.content == b"ISO:CCIDISC"
            assert resp.headers["content-disposition"].endswith(
                'filename="Halo.iso"'
            )
        finally:
            settings.rom_dir = original_rom_dir
            settings.rom_scan_interval = original_interval
            settings.rom_xbox_iso_command = original_cmd
            rom_scanner._catalog = None

    def test_download_xbox_cci_bundle_as_folder_zip(self, tmp_path, client, auth_headers):
        """CCI bundle + ?extract=folder runs XGDTool's extracted-files flow
        and returns a ZIP of the resulting game directory.
        """
        from app.services import rom_db, rom_scanner
        from app.config import settings
        import io
        import zipfile as zf_mod

        original_rom_dir = settings.rom_dir
        original_interval = settings.rom_scan_interval
        original_cmd = settings.rom_xbox_folder_command
        try:
            settings.rom_scan_interval = 0
            settings.rom_xbox_folder_command = json.dumps(
                [
                    sys.executable,
                    "-c",
                    (
                        "from pathlib import Path; import sys; "
                        "out=Path(sys.argv[2]); out.mkdir(exist_ok=True); "
                        "(out/'default.xbe').write_bytes(b'XBE:' + Path(sys.argv[1]).read_bytes()); "
                        "(out/'media').mkdir(exist_ok=True); "
                        "(out/'media'/'asset.bin').write_bytes(b'ASSET')"
                    ),
                    "{input}",
                    "{output_dir}",
                ]
            )
            rom_db.init_db(settings.save_dir)
            rom_dir = tmp_path / "roms"
            settings.rom_dir = rom_dir
            bundle = rom_dir / "xbox" / "Halo"
            bundle.mkdir(parents=True)
            (bundle / "Halo.cci").write_bytes(b"CCIDISC")

            rom_scanner.init(rom_dir)
            rom_id = rom_scanner.get().list_by_system("XBOX")[0].rom_id

            resp = client.get(
                f"/api/v1/roms/{rom_id}?extract=folder", headers=auth_headers
            )
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "application/zip"
            with zf_mod.ZipFile(io.BytesIO(resp.content)) as zf:
                assert sorted(zf.namelist()) == ["default.xbe", "media/asset.bin"]
                assert zf.read("default.xbe") == b"XBE:CCIDISC"
                assert zf.read("media/asset.bin") == b"ASSET"
        finally:
            settings.rom_dir = original_rom_dir
            settings.rom_scan_interval = original_interval
            settings.rom_xbox_folder_command = original_cmd
            rom_scanner._catalog = None


class TestRomRescan:
    def test_rescan(self, rom_client, auth_headers, rom_dir):
        (rom_dir / "nes").mkdir()
        (rom_dir / "nes" / "tetris.nes").write_bytes(b"\xff" * 50)

        resp = rom_client.get("/api/v1/roms/scan", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["count"] == 3

    def test_rescan_no_dir(self, client, auth_headers):
        original = settings.rom_dir
        settings.rom_dir = None
        try:
            resp = client.get("/api/v1/roms/scan", headers=auth_headers)
            assert resp.status_code == 200
            assert resp.json()["status"] == "no_rom_dir"
        finally:
            settings.rom_dir = original


class TestRomDbCache:
    def test_scan_persists_to_db(self, rom_dir, tmp_path):
        from app.services import rom_db, rom_scanner

        rom_db.init_db(tmp_path / "saves")

        (rom_dir / "gba").mkdir()
        (rom_dir / "gba" / "pokemon.gba").write_bytes(b"\x00" * 64)

        catalog = rom_scanner.RomCatalog()
        catalog.scan(rom_dir, use_crc32=False)

        assert rom_db.count() == 1
        row = rom_db.get("GBA_pokemon")
        assert row is not None
        assert row["name"] == "pokemon"
        assert row["system"] == "GBA"

        rom_scanner._catalog = None

    def test_load_from_db_on_init(self, rom_dir, tmp_path):
        from app.services import rom_db, rom_scanner

        rom_db.init_db(tmp_path / "saves")

        (rom_dir / "gba").mkdir()
        (rom_dir / "gba" / "zelda.gba").write_bytes(b"\x00" * 32)

        rom_scanner.init(rom_dir)
        assert rom_db.count() == 1

        rom_scanner._catalog = None

        catalog = rom_scanner.init(rom_dir)
        assert catalog is not None
        assert len(catalog.entries) == 1
        assert "GBA_zelda" in catalog.entries

        rom_scanner._catalog = None

    def test_rescan_updates_db(self, rom_dir, tmp_path):
        from app.services import rom_db, rom_scanner

        settings.rom_dir = rom_dir
        rom_db.init_db(tmp_path / "saves")

        (rom_dir / "gba").mkdir()
        (rom_dir / "gba" / "a.gba").write_bytes(b"\x00" * 10)

        rom_scanner.init(rom_dir)
        assert rom_db.count() == 1

        (rom_dir / "nes").mkdir()
        (rom_dir / "nes" / "b.nes").write_bytes(b"\x00" * 10)

        rom_scanner.rescan()
        assert rom_db.count() == 2

        settings.rom_dir = None
        rom_scanner._catalog = None

    def test_scan_assigns_unique_rom_ids_for_multi_disc_titles(self, rom_dir, tmp_path):
        from app.services import rom_db, rom_scanner

        rom_db.init_db(tmp_path / "saves")

        (rom_dir / "ps1").mkdir()
        (rom_dir / "ps1" / "Final Fantasy VII (USA) (Disc 1).chd").write_bytes(
            b"\x00" * 64
        )
        (rom_dir / "ps1" / "Final Fantasy VII (USA) (Disc 2).chd").write_bytes(
            b"\x01" * 64
        )

        catalog = rom_scanner.RomCatalog()
        count = catalog.scan(rom_dir, use_crc32=False)

        assert count == 2
        assert rom_db.count() == 2

        rows = rom_db.list_all()
        assert [row["title_id"] for row in rows] == [
            "PS1_final_fantasy_vii_usa",
            "PS1_final_fantasy_vii_usa",
        ]
        assert [row["rom_id"] for row in rows] == [
            "PS1_final_fantasy_vii_usa_disc_1",
            "PS1_final_fantasy_vii_usa_disc_2",
        ]

    def test_scan_maps_3do_and_virtualboy_to_native_systems(self, rom_dir, tmp_path):
        from app.services import rom_db, rom_scanner

        rom_db.init_db(tmp_path / "saves")

        (rom_dir / "3do").mkdir()
        (rom_dir / "3do" / "Crash 'n Burn (USA).chd").write_bytes(b"\x00" * 64)
        (rom_dir / "virtualboy").mkdir()
        (rom_dir / "virtualboy" / "Mario Clash (USA).vb").write_bytes(b"\x01" * 64)

        catalog = rom_scanner.RomCatalog()
        count = catalog.scan(rom_dir, use_crc32=False)

        assert count == 2
        rows = rom_db.list_all()
        assert [row["system"] for row in rows] == ["3DO", "VB"]

    def test_scan_strips_inner_rom_extension_from_archived_rom_name(self, rom_dir, tmp_path):
        from app.services import rom_db, rom_scanner

        rom_db.init_db(tmp_path / "saves")

        (rom_dir / "n3ds").mkdir()
        with zipfile.ZipFile(
            rom_dir / "n3ds" / "Super Mario 3D Land (USA).3ds.zip",
            "w",
            zipfile.ZIP_DEFLATED,
        ) as zf:
            zf.writestr("Super Mario 3D Land (USA).3ds", b"CARTROM")

        catalog = rom_scanner.RomCatalog()
        count = catalog.scan(rom_dir, use_crc32=False)

        assert count == 1
        rows = rom_db.list_all()
        assert rows[0]["title_id"] == "0004000000054000"
        assert rows[0]["name"] == "Super Mario 3D Land (USA)"


class TestSyncRomAvailable:
    def test_sync_includes_rom_available(self, rom_client, auth_headers, tmp_path):
        from app.services import rom_scanner, storage
        from app.models.save import SaveBundle, BundleFile

        catalog = rom_scanner.get()
        title_id = "GBA_test_rom"
        bundle = SaveBundle(
            title_id=0,
            timestamp=1000,
            files=[
                BundleFile(path="save.bin", size=4, sha256=b"\x00" * 32, data=b"test")
            ],
            title_id_str=title_id,
        )
        storage.store_save(bundle)

        resp = rom_client.post(
            "/api/v1/sync",
            json={
                "titles": [
                    {
                        "title_id": title_id,
                        "save_hash": "doesnotmatch",
                        "timestamp": 999,
                        "size": 4,
                    }
                ]
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert title_id in body["rom_available"]
