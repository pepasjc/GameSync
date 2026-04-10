from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import settings


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

    (rom_dir / "gba").mkdir()
    (rom_dir / "gba" / "test rom.gba").write_bytes(b"\x00" * 100)

    (rom_dir / "snes").mkdir()
    (rom_dir / "snes" / "Super Mario World (USA).sfc").write_bytes(b"\x01" * 200)

    rom_scanner.init(rom_dir)

    yield client

    settings.rom_dir = original
    settings.rom_scan_interval = original_interval
    rom_scanner._catalog = None


class TestRomCatalog:
    def test_list_roms(self, rom_client, auth_headers):
        resp = rom_client.get("/api/v1/roms", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        titles = [r["title_id"] for r in body["roms"]]
        assert "GBA_test_rom" in titles
        assert "SNES_super_mario_world_usa" in titles

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


class TestRomDownload:
    def test_download_rom(self, rom_client, auth_headers):
        roms = rom_client.get("/api/v1/roms?system=GBA", headers=auth_headers).json()
        title_id = roms["roms"][0]["title_id"]

        resp = rom_client.get(f"/api/v1/roms/{title_id}", headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.content) == 100
        assert resp.headers["accept-ranges"] == "bytes"

    def test_download_not_found(self, rom_client, auth_headers):
        resp = rom_client.get("/api/v1/roms/GBA_nonexistent", headers=auth_headers)
        assert resp.status_code == 404

    def test_range_request(self, rom_client, auth_headers):
        roms = rom_client.get("/api/v1/roms?system=GBA", headers=auth_headers).json()
        title_id = roms["roms"][0]["title_id"]

        resp = rom_client.get(
            f"/api/v1/roms/{title_id}",
            headers={**auth_headers, "Range": "bytes=0-9"},
        )
        assert resp.status_code == 206
        assert len(resp.content) == 10
        assert resp.headers["content-range"] == "bytes 0-9/100"

    def test_range_request_suffix(self, rom_client, auth_headers):
        roms = rom_client.get("/api/v1/roms?system=GBA", headers=auth_headers).json()
        title_id = roms["roms"][0]["title_id"]

        resp = rom_client.get(
            f"/api/v1/roms/{title_id}",
            headers={**auth_headers, "Range": "bytes=-10"},
        )
        assert resp.status_code == 206
        assert len(resp.content) == 10

    def test_range_request_open_end(self, rom_client, auth_headers):
        roms = rom_client.get("/api/v1/roms?system=GBA", headers=auth_headers).json()
        title_id = roms["roms"][0]["title_id"]

        resp = rom_client.get(
            f"/api/v1/roms/{title_id}",
            headers={**auth_headers, "Range": "bytes=50-"},
        )
        assert resp.status_code == 206
        assert len(resp.content) == 50

    def test_range_request_invalid(self, rom_client, auth_headers):
        roms = rom_client.get("/api/v1/roms?system=GBA", headers=auth_headers).json()
        title_id = roms["roms"][0]["title_id"]

        resp = rom_client.get(
            f"/api/v1/roms/{title_id}",
            headers={**auth_headers, "Range": "bytes=200-300"},
        )
        assert resp.status_code == 416


class TestRomRescan:
    def test_rescan(self, rom_client, auth_headers, rom_dir):
        (rom_dir / "nes").mkdir()
        (rom_dir / "nes" / "tetris.nes").write_bytes(b"\xff" * 50)

        resp = rom_client.get("/api/v1/roms/scan", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["count"] == 3

    def test_rescan_no_dir(self, client, auth_headers):
        resp = client.get("/api/v1/roms/scan", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "no_rom_dir"


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
