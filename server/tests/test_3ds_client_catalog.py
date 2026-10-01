"""Contract tests for what the 3DS client's game catalog relies on.

The 3DS client (3ds/source/catalog.c, catalog_data.c) lists the catalog per
system, then installs:

* a ``.cia`` on the server as-is (streamed into AM),
* a ``.3ds`` / ``.cci`` cart, or a zip holding one, through ``?extract=cia``,
  falling back to the raw cart file when that answers 503,
* a zipped DS ROM through ``?extract=nds``, a loose ``.nds`` as-is.

It reads ``rom_id``, ``title_id``, ``filename``, ``size``, ``is_bundle`` and
``extract_formats`` from the list, and needs a ``Content-Length`` on every
download for its progress bar and free-space check.
"""

import json
import sys
import zipfile

import pytest

from app.config import settings

from .test_ctr_rom import _build_cart

# Not a real CIA: just the header fields the client reads (header size,
# section sizes) followed by filler. The client finds the title id in the TMD.
FAKE_CIA = (
    (0x2020).to_bytes(4, "little") + bytes(4)
    + (0xA00).to_bytes(4, "little") + (0x350).to_bytes(4, "little")
    + (0xB34).to_bytes(4, "little") + bytes(0x100 - 0x14)
) + bytes(range(256)) * 64

NDS_ROM = bytes(range(256)) * 512


@pytest.fixture()
def catalog_3ds(tmp_path, client):
    from app.services import rom_db, rom_scanner

    rom_dir = tmp_path / "roms"
    original = (
        settings.rom_dir,
        settings.rom_scan_interval,
        settings.rom_3ds_cia_command,
    )
    settings.rom_dir = rom_dir
    settings.rom_scan_interval = 0
    settings.rom_3ds_cia_command = ""
    rom_db.init_db(settings.save_dir)

    n3ds = rom_dir / "n3ds"
    n3ds.mkdir(parents=True)
    cart = _build_cart(decrypted=False, no_crypto_flag=False)
    (n3ds / "Encrypted Cart (USA).3ds").write_bytes(cart)
    (n3ds / "Ready Made (USA).cia").write_bytes(FAKE_CIA)
    with zipfile.ZipFile(n3ds / "Zipped Cart (USA).3ds.zip", "w") as zf:
        zf.writestr("Zipped Cart (USA).3ds", cart)

    nds = rom_dir / "nds"
    nds.mkdir()
    with zipfile.ZipFile(nds / "Alpha Quest (USA).zip", "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Alpha Quest (USA).nds", NDS_ROM)
    (nds / "Loose Game (USA).nds").write_bytes(NDS_ROM[:4096])

    converted = tmp_path / "converted.cia"
    converted.write_bytes(FAKE_CIA)

    rom_scanner.init(rom_dir)
    yield client, cart, converted

    settings.rom_dir, settings.rom_scan_interval, settings.rom_3ds_cia_command = original
    rom_scanner._catalog = None


def _page(client, headers, system):
    resp = client.get(f"/api/v1/roms?system={system}&limit=200&offset=0", headers=headers)
    assert resp.status_code == 200
    return resp.json()


def _entry(page, prefix):
    return next(r for r in page["roms"] if r["name"].startswith(prefix))


def _enable_converter(converted):
    settings.rom_3ds_cia_command = json.dumps(
        [
            sys.executable,
            "-c",
            "import shutil, sys; shutil.copyfile(sys.argv[1], sys.argv[2])",
            str(converted),
            "{output}",
        ]
    )


class TestThreeDsClientCatalog:
    def test_systems_lists_3ds_and_nds(self, catalog_3ds, auth_headers):
        client, _, _ = catalog_3ds
        data = client.get("/api/v1/roms/systems", headers=auth_headers).json()
        assert "3DS" in data["systems"]
        assert "NDS" in data["systems"]
        assert data["stats"]["3DS"] == 3

    def test_list_fields_the_client_reads(self, catalog_3ds, auth_headers):
        client, _, _ = catalog_3ds
        page = _page(client, auth_headers, "3DS")
        assert page["total"] == 3
        assert page["has_more"] is False
        for rom in page["roms"]:
            assert rom["rom_id"]
            assert rom["filename"]
            assert rom["size"] > 0
            assert rom["is_bundle"] is False
            assert "title_id" in rom

        # Carts (raw or zipped) offer a CIA; a ready CIA offers nothing
        assert "cia" in _entry(page, "Encrypted Cart")["extract_formats"]
        assert "cia" in _entry(page, "Zipped Cart")["extract_formats"]
        ready = _entry(page, "Ready Made")
        assert ready["filename"].endswith(".cia")
        assert "cia" not in ready.get("extract_formats", [])

        nds = _page(client, auth_headers, "NDS")
        assert "nds" in _entry(nds, "Alpha Quest")["extract_formats"]
        assert _entry(nds, "Loose Game")["filename"].endswith(".nds")

    def test_ready_cia_downloads_as_is(self, catalog_3ds, auth_headers):
        client, _, _ = catalog_3ds
        rom_id = _entry(_page(client, auth_headers, "3DS"), "Ready Made")["rom_id"]
        resp = client.get(f"/api/v1/roms/{rom_id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.content == FAKE_CIA
        assert int(resp.headers["content-length"]) == len(FAKE_CIA)

    def test_cart_converts_to_cia(self, catalog_3ds, auth_headers):
        client, _, converted = catalog_3ds
        _enable_converter(converted)
        page = _page(client, auth_headers, "3DS")
        for prefix in ("Encrypted Cart", "Zipped Cart"):
            rom_id = _entry(page, prefix)["rom_id"]
            resp = client.get(f"/api/v1/roms/{rom_id}?extract=cia", headers=auth_headers)
            assert resp.status_code == 200, resp.text
            assert resp.content == FAKE_CIA
            assert int(resp.headers["content-length"]) == len(FAKE_CIA)

    def test_unconfigured_conversion_is_503_and_raw_cart_still_downloads(
        self, catalog_3ds, auth_headers
    ):
        """The client offers the raw .3ds when the CIA conversion answers 503."""
        client, cart, _ = catalog_3ds
        rom_id = _entry(_page(client, auth_headers, "3DS"), "Encrypted Cart")["rom_id"]

        resp = client.get(f"/api/v1/roms/{rom_id}?extract=cia", headers=auth_headers)
        assert resp.status_code == 503
        assert "SYNC_ROM_3DS_CIA_COMMAND" in resp.text

        raw = client.get(f"/api/v1/roms/{rom_id}", headers=auth_headers)
        assert raw.status_code == 200
        assert raw.content == cart
        assert int(raw.headers["content-length"]) == len(cart)

    def test_zipped_ds_rom_extracts(self, catalog_3ds, auth_headers):
        client, _, _ = catalog_3ds
        rom_id = _entry(_page(client, auth_headers, "NDS"), "Alpha Quest")["rom_id"]
        resp = client.get(f"/api/v1/roms/{rom_id}?extract=nds", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.content == NDS_ROM
        assert int(resp.headers["content-length"]) == len(NDS_ROM)
