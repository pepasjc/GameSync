import json
import sys
import zipfile
from pathlib import Path

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
def rom_client_3ds_zip(rom_dir, client, auth_headers):
    from app.services import rom_db, rom_scanner

    original_rom_dir = settings.rom_dir
    original_interval = settings.rom_scan_interval
    original_cia_cmd = settings.rom_3ds_cia_command
    original_decrypted_cmd = settings.rom_3ds_decrypted_cia_command
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
    settings.rom_3ds_decrypted_cia_command = json.dumps(
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; import sys; "
                "Path(sys.argv[2]).write_bytes(b'DEC:' + Path(sys.argv[1]).read_bytes())"
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
    settings.rom_3ds_decrypted_cia_command = original_decrypted_cmd
    settings.rom_3ds_decrypted_cci_command = original_decrypted_cci_cmd
    rom_scanner._catalog = None


@pytest.fixture()
def rom_client_cci_zip(rom_dir, client, auth_headers):
    from app.services import rom_db, rom_scanner

    original_rom_dir = settings.rom_dir
    original_interval = settings.rom_scan_interval
    original_cia_cmd = settings.rom_3ds_cia_command
    original_decrypted_cmd = settings.rom_3ds_decrypted_cia_command
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
    settings.rom_3ds_decrypted_cia_command = json.dumps(
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; import sys; "
                "Path(sys.argv[2]).write_bytes(b'DEC:' + Path(sys.argv[1]).read_bytes())"
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
    settings.rom_3ds_decrypted_cia_command = original_decrypted_cmd
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
        assert rom["extract_formats"] == ["cia", "decrypted_cia", "decrypted_cci"]

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
        assert rom["extract_formats"] == ["cia", "decrypted_cia", "decrypted_cci"]


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

    def test_download_3ds_zip_as_decrypted_cia(self, rom_client_3ds_zip, auth_headers):
        roms = rom_client_3ds_zip.get("/api/v1/roms?system=3DS", headers=auth_headers).json()
        rom_id = roms["roms"][0]["rom_id"]

        resp = rom_client_3ds_zip.get(
            f"/api/v1/roms/{rom_id}?extract=decrypted_cia",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.content == b"DEC:CARTROM"
        assert resp.headers["content-disposition"].endswith(
            'filename="Super Mario 3D Land (USA)_decrypted.cia"'
        )

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
            'filename="Super Mario 3D Land (USA)_decrypted.cci"'
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
            'filename="Pilotwings Resort (USA)_decrypted.cci"'
        )


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
