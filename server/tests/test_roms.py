import io
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
def rom_client_ps1_vcd(rom_dir, client, auth_headers):
    """Fixture that wires up a stub VCD converter for PS1 → POPStarter
    conversion.  The stub prefixes ``VCD:`` to the input bytes so tests
    can assert content end-to-end without a real popstation install."""
    from app.services import rom_db, rom_scanner

    original_rom_dir = settings.rom_dir
    original_interval = settings.rom_scan_interval
    original_cmd = settings.rom_ps1_vcd_command
    original_cwd = settings.rom_ps1_vcd_cwd

    settings.rom_dir = rom_dir
    settings.rom_scan_interval = 0
    settings.rom_ps1_vcd_cwd = ""
    settings.rom_ps1_vcd_command = json.dumps(
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; import sys; "
                "Path(sys.argv[2]).write_bytes(b'VCD:' + Path(sys.argv[1]).read_bytes())"
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
    settings.rom_ps1_vcd_command = original_cmd
    settings.rom_ps1_vcd_cwd = original_cwd
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


@pytest.fixture()
def rom_client_3ds_raw(rom_dir, client, auth_headers):
    """Raw (unzipped) .3ds carts with real NCSD headers: one properly flagged
    decrypted dump, one decrypted-but-still-flagged-encrypted dump, and one
    encrypted dump."""
    from app.services import rom_db, rom_scanner

    from .test_ctr_rom import _build_cart, _build_cart_from, cfa, cxi

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
    carts = {
        "Flagged": _build_cart(decrypted=True, no_crypto_flag=True),
        "Stale": _build_cart(decrypted=True, no_crypto_flag=False),
        "Encrypted": _build_cart(decrypted=False, no_crypto_flag=False),
        # How real decrypted dumps look: plaintext game partition, encrypted
        # manual + update partitions.
        "Retail": _build_cart_from([cxi(0, plaintext=True), cfa(1), cfa(7)]),
    }
    for name, data in carts.items():
        (rom_dir / "n3ds" / f"{name} (USA).3ds").write_bytes(data)

    rom_scanner.init(rom_dir)

    yield client, carts

    settings.rom_dir = original_rom_dir
    settings.rom_scan_interval = original_interval
    settings.rom_3ds_cia_command = original_cia_cmd
    settings.rom_3ds_decrypted_cci_command = original_decrypted_cci_cmd
    rom_scanner._catalog = None


def _rom_id_for(client, auth_headers, name_prefix: str) -> str:
    roms = client.get("/api/v1/roms?system=3DS", headers=auth_headers).json()["roms"]
    match = next(r for r in roms if r["name"].startswith(name_prefix))
    return match["rom_id"]


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

    def test_filter_by_system_accepts_aliases(self, rom_client, auth_headers):
        """A catalog indexed before an alias was normalised must still match.

        Sega CD ROMs indexed as ``SCD`` have to answer a request for the
        canonical ``SEGACD`` (and vice versa), otherwise the ROM installer
        shows an empty list until the server is rescanned.
        """
        from app.routes.roms import _system_match_set

        assert _system_match_set("SEGACD") == {"SEGACD", "SCD"}
        assert _system_match_set("SCD") == {"SEGACD", "SCD"}
        assert _system_match_set("segacd") == {"SEGACD", "SCD"}
        # A system with no aliases matches only itself.
        assert _system_match_set("GBA") == {"GBA"}
        assert _system_match_set("") == set()

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

    def test_fingerprints_change_only_with_the_catalogue(self, rom_client,
                                                         auth_headers):
        from app.services import rom_scanner

        resp = rom_client.get("/api/v1/roms/fingerprints", headers=auth_headers)
        assert resp.status_code == 200
        systems = resp.json()["systems"]
        assert systems["GBA"]["count"] == 1
        assert len(systems["GBA"]["fingerprint"]) == 40
        before = dict(systems)

        # Stable across requests, and memoised (same object back).
        catalog = rom_scanner.get()
        assert catalog.fingerprints() is catalog.fingerprints()
        again = rom_client.get("/api/v1/roms/fingerprints",
                               headers=auth_headers).json()["systems"]
        assert again == before

        # Removing a row moves that system's fingerprint and no other.
        gba = catalog.list_by_system("GBA")[0]
        catalog._entries.pop(gba.rom_id)
        catalog._rebuild_index()
        after = rom_client.get("/api/v1/roms/fingerprints",
                               headers=auth_headers).json()["systems"]
        assert "GBA" not in after
        assert after["SNES"] == before["SNES"]

    def test_no_rom_dir(self, client, auth_headers):
        resp = client.get("/api/v1/roms", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_canonical_names_resolves_emulator_title_ids(
        self, rom_client, auth_headers
    ):
        resp = rom_client.post(
            "/api/v1/titles/canonical-names",
            json={
                "title_ids": [
                    "SNES_super_mario_world_usa",
                    "GBA_test_rom",
                    "SNES_does_not_exist",
                ]
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        names = resp.json()["names"]
        assert names["SNES_super_mario_world_usa"] == "Super Mario World (USA)"
        # GBA test rom has no DAT match; canonical name falls back to the
        # scanner's display name (filename stem cleaned).
        assert "GBA_test_rom" in names
        # Unknown title_id is omitted so the client can detect "no match".
        assert "SNES_does_not_exist" not in names

    def test_canonical_names_empty_catalog(self, client, auth_headers):
        resp = client.post(
            "/api/v1/titles/canonical-names",
            json={"title_ids": ["SNES_super_mario_world_usa"]},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["names"] == {}

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

    def test_scan_xbox_xbe_iso_bundle(self, tmp_path):
        """Xbox subfolders with default.xbe + .iso are treated as bundles
        (same as .cci bundles) so clients can request ?extract=iso and
        get the disc image directly without downloading the whole ZIP.
        """
        from app.services import rom_db, rom_scanner

        rom_db.init_db(tmp_path)
        rom_dir = tmp_path / "roms"

        bundle = rom_dir / "xbox" / "Fable"
        bundle.mkdir(parents=True)
        (bundle / "Fable.iso").write_bytes(b"I" * 2048)
        (bundle / "default.xbe").write_bytes(b"X" * 128)

        # A loose ISO should NOT become a bundle (no default.xbe).
        (rom_dir / "xbox" / "Jet Set Radio Future.iso").write_bytes(b"J" * 1024)

        catalog = rom_scanner.RomCatalog()
        catalog.scan(rom_dir, use_crc32=False)

        xbox = catalog.list_by_system("XBOX")
        assert len(xbox) == 2
        bundles = [e for e in xbox if e.is_bundle]
        loose = [e for e in xbox if not e.is_bundle]
        assert len(bundles) == 1
        assert len(loose) == 1

        assert bundles[0].name == "Fable"
        file_names = sorted(f["name"] for f in bundles[0].bundle_files)
        assert "Fable.iso" in file_names
        assert "default.xbe" in file_names
        assert loose[0].filename == "Jet Set Radio Future.iso"

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

    def test_wiiu_wup_folder_is_a_bundle(self, tmp_path):
        """A WUP installable set collapses into one entry keyed by the title
        id embedded in the folder name, keeping every ``.app``/``.h3``/ticket
        file so the console client can install it.
        """
        from app.services import rom_db, rom_scanner

        rom_db.init_db(tmp_path)
        rom_dir = tmp_path / "roms"

        wup = rom_dir / "wiiu" / "SUPER MARIO 3D WORLD [0005000010145C00]"
        wup.mkdir(parents=True)
        (wup / "00000000.app").write_bytes(b"a" * 4096)
        (wup / "00000000.h3").write_bytes(b"h" * 64)
        (wup / "title.tmd").write_bytes(b"t" * 128)
        (wup / "title.tik").write_bytes(b"k" * 32)
        (wup / "title.cert").write_bytes(b"c" * 16)

        catalog = rom_scanner.RomCatalog()
        catalog.scan(rom_dir, use_crc32=False)

        entries = catalog.list_by_system("WIIU")
        assert len(entries) == 1
        e = entries[0]
        assert e.is_bundle is True
        assert e.title_id == "0005000010145C00"
        assert e.size == 4096 + 64 + 128 + 32 + 16
        assert sorted(f["name"] for f in e.bundle_files) == [
            "00000000.app",
            "00000000.h3",
            "title.cert",
            "title.tik",
            "title.tmd",
        ]

    def test_wiiu_loadiine_folder_is_a_bundle(self, tmp_path):
        """A decrypted ``code``/``content``/``meta`` layout is a bundle too,
        even without a ``title.tmd``.
        """
        from app.services import rom_db, rom_scanner

        rom_db.init_db(tmp_path)
        rom_dir = tmp_path / "roms"

        game = rom_dir / "wiiu" / "Splatoon"
        for sub in ("code", "content", "meta"):
            (game / sub).mkdir(parents=True)
        (game / "code" / "Splatoon.rpx").write_bytes(b"r" * 512)
        (game / "content" / "data.bin").write_bytes(b"d" * 256)
        (game / "meta" / "meta.xml").write_bytes(b"<menu/>")

        catalog = rom_scanner.RomCatalog()
        catalog.scan(rom_dir, use_crc32=False)

        entries = catalog.list_by_system("WIIU")
        assert len(entries) == 1
        e = entries[0]
        assert e.is_bundle is True
        assert e.name == "Splatoon"
        # No embedded title id → falls back to the slug namespace.
        assert e.title_id == "WIIU_splatoon"
        assert sorted(f["name"] for f in e.bundle_files) == [
            "code/Splatoon.rpx",
            "content/data.bin",
            "meta/meta.xml",
        ]

    def test_wiiu_single_file_images_and_archives(self, tmp_path):
        """``.wua``/``.wud``/``.wux`` and zipped dumps stay single entries,
        and a zip that lives *inside* a WUP folder is owned by the bundle.
        """
        from app.services import rom_db, rom_scanner

        rom_db.init_db(tmp_path)
        rom_dir = tmp_path / "roms"
        wiiu = rom_dir / "wiiu"
        wiiu.mkdir(parents=True)

        (wiiu / "Bayonetta 2 [0005000010157F00].wua").write_bytes(b"w" * 1024)
        (wiiu / "Xenoblade Chronicles X.wux").write_bytes(b"x" * 2048)
        (wiiu / "Pikmin 3 [0005000010144F00].zip").write_bytes(b"z" * 512)

        catalog = rom_scanner.RomCatalog()
        catalog.scan(rom_dir, use_crc32=False)

        entries = {e.filename: e for e in catalog.list_by_system("WIIU")}
        assert len(entries) == 3
        assert all(not e.is_bundle for e in entries.values())

        wua = entries["Bayonetta 2 [0005000010157F00].wua"]
        assert wua.title_id == "0005000010157F00"

        zipped = entries["Pikmin 3 [0005000010144F00].zip"]
        assert zipped.title_id == "0005000010144F00"

        # No title id in the name → DAT slug namespace, name keeps the stem.
        wux = entries["Xenoblade Chronicles X.wux"]
        assert wux.title_id.startswith("WIIU_")
        assert wux.name == "Xenoblade Chronicles X"

    def test_wiiu_update_and_dlc_are_named_and_grouped(
        self, tmp_path, client, auth_headers
    ):
        """Updates and DLC are absent from every Wii U DAT, but share their
        base game's low word — so they get named from it, labelled, and linked
        back so a client can queue the whole set.
        """
        from app.services import rom_db, rom_scanner
        from app.config import settings

        original = settings.rom_dir
        try:
            rom_db.init_db(settings.save_dir)
            rom_dir = tmp_path / "roms"
            settings.rom_dir = rom_dir

            for folder in (
                "SUPER MARIO 3D WORLD [0005000010145C00]",
                "SUPER MARIO 3D WORLD [0005000E10145C00]",
                "SUPER MARIO 3D WORLD [0005000C10145C00]",
                "BAYONETTA 2 [0005000010172600]",
            ):
                d = rom_dir / "wiiu" / folder
                d.mkdir(parents=True)
                (d / "title.tmd").write_bytes(b"TMD")
                (d / "00000000.app").write_bytes(b"APP")

            rom_scanner.init(rom_dir)

            resp = client.get("/api/v1/roms?system=WIIU", headers=auth_headers)
            assert resp.status_code == 200
            rows = {r["rom_id"]: r for r in resp.json()["roms"]}

            game = rows["0005000010145C00"]
            update = rows["0005000E10145C00"]
            dlc = rows["0005000C10145C00"]

            assert game["content_type"] == "game"
            assert update["content_type"] == "update"
            assert dlc["content_type"] == "dlc"

            # All three resolve to the same base id...
            for row in (game, update, dlc):
                assert row["base_title_id"] == "0005000010145C00"

            # ...and the update/DLC borrow the base game's DAT name.
            assert update["name"].endswith("(Update)")
            assert dlc["name"].endswith("(DLC)")
            assert update["name"].startswith(game["name"])
            assert dlc["name"].startswith(game["name"])

            # related_rom_ids is in install order: game -> update -> DLC.
            # MCP rejects a DLC whose base game isn't installed yet, so the
            # ordering is load-bearing.
            assert game["related_rom_ids"] == [
                "0005000E10145C00",
                "0005000C10145C00",
            ]
            assert dlc["related_rom_ids"] == [
                "0005000010145C00",
                "0005000E10145C00",
            ]

            # A game with no extras still reports its type, with no siblings.
            solo = rows["0005000010172600"]
            assert solo["content_type"] == "game"
            assert solo["related_rom_ids"] == []
        finally:
            settings.rom_dir = original
            rom_scanner._catalog = None

    def test_wiiu_sorted_folders_scan_like_a_flat_library(self, tmp_path):
        """``wiiu/updates/<Game>/`` must scan the same as ``wiiu/<Game>/``.

        Organiser folders are a natural way to keep a NAS tidy, and without
        an explicit descent the scanner would collapse all of ``updates/``
        into one giant bundle (a ``title.tmd`` exists *somewhere* beneath it).
        """
        from app.services import rom_db, rom_scanner

        def build(root, layout):
            for rel in layout:
                d = root / "wiiu" / rel
                d.mkdir(parents=True)
                (d / "title.tmd").write_bytes(b"TMD")
                (d / "00000000.app").write_bytes(b"A" * 100)

        flat = tmp_path / "flat"
        build(flat, [
            "SUPER MARIO 3D WORLD [0005000010145C00]",
            "SUPER MARIO 3D WORLD [0005000E10145C00]",
            "SUPER MARIO 3D WORLD [0005000C10145C00]",
        ])
        sorted_ = tmp_path / "sorted"
        build(sorted_, [
            "games/SUPER MARIO 3D WORLD [0005000010145C00]",
            "updates/SUPER MARIO 3D WORLD [0005000E10145C00]",
            "dlc/SUPER MARIO 3D WORLD [0005000C10145C00]",
        ])

        def scan(root):
            rom_db.init_db(root)
            catalog = rom_scanner.RomCatalog()
            catalog.scan(root)
            return sorted(
                (e.title_id, e.name, e.size, e.is_bundle)
                for e in catalog.list_by_system("WIIU")
            )

        flat_rows = scan(flat)
        assert len(flat_rows) == 3
        assert scan(sorted_) == flat_rows

    def test_wiiu_content_type_helpers(self):
        """Title-id classification and base-id derivation."""
        from shared import wiiu_meta

        assert wiiu_meta.content_type("0005000010145C00") == "game"
        assert wiiu_meta.content_type("0005000E10145C00") == "update"
        assert wiiu_meta.content_type("0005000C10145C00") == "dlc"
        assert wiiu_meta.content_type("00050002ABCDEF00") == "demo"
        assert wiiu_meta.content_type("DEADBEEFCAFEBABE") == ""

        assert (
            wiiu_meta.base_title_id("0005000E10145C00") == "0005000010145C00"
        )
        # A base id maps to itself, so callers need no special case.
        assert (
            wiiu_meta.base_title_id("0005000010145C00") == "0005000010145C00"
        )

        assert wiiu_meta.decorate_name("Mario", "0005000E10145C00") == "Mario (Update)"
        assert wiiu_meta.decorate_name("Mario", "0005000010145C00") == "Mario"

    def test_wiiu_title_id_split_helper(self):
        """The id parser tolerates brackets, parens, archive suffixes and
        rejects 16-hex runs outside the Wii U ``0005`` space.
        """
        from app.services.rom_scanner import _wiiu_split_title_id

        assert _wiiu_split_title_id("Game [0005000010145C00].zip") == (
            "0005000010145C00",
            "Game",
        )
        assert _wiiu_split_title_id("Game (0005000E10145C00).wud.zip") == (
            "0005000E10145C00",
            "Game",
        )
        assert _wiiu_split_title_id("0005000010145C00") == (
            "0005000010145C00",
            "",
        )
        # A CRC-ish 16-hex run that isn't a Wii U title id is left alone.
        assert _wiiu_split_title_id("Game [DEADBEEFCAFEBABE].wud") == (
            "",
            "Game [DEADBEEFCAFEBABE]",
        )

    def test_wiiu_wup_advertises_nothing_without_a_decrypter(
        self, tmp_path, client, auth_headers
    ):
        """With no decrypter configured a WUP bundle advertises no formats at
        all, so clients download the raw set.

        That raw set is what BOTH targets want — real hardware installs it via
        MCP, and Cemu decrypts it itself from the bundled ticket.  Advertising
        ``loadiine`` here would make every emulator client request a
        guaranteed 503 instead of a working download.
        """
        from app.services import rom_db, rom_scanner
        from app.config import settings

        original = settings.rom_dir
        original_cmd = settings.rom_wiiu_loadiine_command
        original_wua = settings.rom_wiiu_wua_command
        try:
            rom_db.init_db(settings.save_dir)
            rom_dir = tmp_path / "roms"
            settings.rom_dir = rom_dir
            settings.rom_wiiu_loadiine_command = ""
            settings.rom_wiiu_wua_command = ""

            wup = rom_dir / "wiiu" / "Bayonetta 2 [0005000010172600]"
            wup.mkdir(parents=True)
            (wup / "00000000.app").write_bytes(b"ENCRYPTED")
            (wup / "title.tmd").write_bytes(b"TMD")
            (wup / "title.tik").write_bytes(b"TIK")

            rom_scanner.init(rom_dir)

            resp = client.get("/api/v1/roms?system=WIIU", headers=auth_headers)
            assert resp.status_code == 200
            rom = resp.json()["roms"][0]
            assert "extract_formats" not in rom
            assert "extract_format" not in rom

            # ...and once a decrypter is configured, it shows up.
            settings.rom_wiiu_loadiine_command = "cdecrypt {input} {output_dir}"
            resp = client.get("/api/v1/roms?system=WIIU", headers=auth_headers)
            assert resp.json()["roms"][0]["extract_formats"] == ["loadiine"]
            settings.rom_wiiu_loadiine_command = ""

            # No ?extract → raw WUP ZIP, converter never consulted.
            raw = client.get(
                f"/api/v1/roms/{rom['rom_id']}", headers=auth_headers
            )
            assert raw.status_code == 200
            with zipfile.ZipFile(io.BytesIO(raw.content)) as zf:
                assert sorted(zf.namelist()) == [
                    "00000000.app",
                    "title.tik",
                    "title.tmd",
                ]
                assert zf.read("00000000.app") == b"ENCRYPTED"
        finally:
            settings.rom_dir = original
            settings.rom_wiiu_loadiine_command = original_cmd
            settings.rom_wiiu_wua_command = original_wua
            rom_scanner._catalog = None

    def test_wiiu_loadiine_extract_runs_the_decrypter(
        self, tmp_path, client, auth_headers
    ):
        """``?extract=loadiine`` runs the configured command and zips the
        code/content/meta tree it produced.
        """
        from app.services import rom_db, rom_scanner
        from app.config import settings

        original_dir = settings.rom_dir
        original_cmd = settings.rom_wiiu_loadiine_command
        original_cwd = settings.rom_wiiu_cwd
        try:
            rom_db.init_db(settings.save_dir)
            rom_dir = tmp_path / "roms"
            settings.rom_dir = rom_dir
            settings.rom_wiiu_cwd = ""
            # Stub CDecrypt: reads the .app from {input}, writes a decrypted
            # tree under {output_dir}.
            settings.rom_wiiu_loadiine_command = json.dumps(
                [
                    sys.executable,
                    "-c",
                    (
                        "from pathlib import Path; import sys; "
                        "src, out = Path(sys.argv[1]), Path(sys.argv[2]); "
                        "(out / 'code').mkdir(parents=True); "
                        "(out / 'content').mkdir(); (out / 'meta').mkdir(); "
                        "(out / 'code' / 'app.rpx').write_bytes("
                        "b'DEC:' + (src / '00000000.app').read_bytes()); "
                        "(out / 'meta' / 'meta.xml').write_bytes(b'<menu/>')"
                    ),
                    "{input}",
                    "{output_dir}",
                ]
            )

            wup = rom_dir / "wiiu" / "Bayonetta 2 [0005000010172600]"
            wup.mkdir(parents=True)
            (wup / "00000000.app").write_bytes(b"ENCRYPTED")
            (wup / "title.tmd").write_bytes(b"TMD")

            rom_scanner.init(rom_dir)
            rom_id = rom_scanner.get().list_by_system("WIIU")[0].rom_id

            resp = client.get(
                f"/api/v1/roms/{rom_id}?extract=loadiine", headers=auth_headers
            )
            assert resp.status_code == 200
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                assert zf.read("code/app.rpx") == b"DEC:ENCRYPTED"
                assert zf.read("meta/meta.xml") == b"<menu/>"
        finally:
            settings.rom_dir = original_dir
            settings.rom_wiiu_loadiine_command = original_cmd
            settings.rom_wiiu_cwd = original_cwd
            rom_scanner._catalog = None

    def test_wiiu_already_decrypted_needs_no_converter(
        self, tmp_path, client, auth_headers
    ):
        """A loadiine bundle answers ``?extract=loadiine`` with a plain ZIP
        even when no decrypt command is configured — there is nothing to
        decrypt.
        """
        from app.services import rom_db, rom_scanner
        from app.config import settings

        original_dir = settings.rom_dir
        original_cmd = settings.rom_wiiu_loadiine_command
        try:
            rom_db.init_db(settings.save_dir)
            rom_dir = tmp_path / "roms"
            settings.rom_dir = rom_dir
            settings.rom_wiiu_loadiine_command = ""

            game = rom_dir / "wiiu" / "Splatoon"
            for sub in ("code", "content", "meta"):
                (game / sub).mkdir(parents=True)
            (game / "code" / "app.rpx").write_bytes(b"RPX")
            (game / "content" / "data.bin").write_bytes(b"DATA")
            (game / "meta" / "meta.xml").write_bytes(b"<menu/>")

            rom_scanner.init(rom_dir)
            rom_id = rom_scanner.get().list_by_system("WIIU")[0].rom_id

            resp = client.get(
                f"/api/v1/roms/{rom_id}?extract=loadiine", headers=auth_headers
            )
            assert resp.status_code == 200
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                assert zf.read("code/app.rpx") == b"RPX"
        finally:
            settings.rom_dir = original_dir
            settings.rom_wiiu_loadiine_command = original_cmd
            rom_scanner._catalog = None

    def test_wiiu_extract_without_command_returns_503(
        self, tmp_path, client, auth_headers
    ):
        """An encrypted WUP with no configured decrypter returns 503 and
        names the env var, rather than silently serving unusable bytes.
        """
        from app.services import rom_db, rom_scanner
        from app.config import settings

        original_dir = settings.rom_dir
        original_cmd = settings.rom_wiiu_loadiine_command
        try:
            rom_db.init_db(settings.save_dir)
            rom_dir = tmp_path / "roms"
            settings.rom_dir = rom_dir
            settings.rom_wiiu_loadiine_command = ""

            wup = rom_dir / "wiiu" / "Bayonetta 2 [0005000010172600]"
            wup.mkdir(parents=True)
            (wup / "00000000.app").write_bytes(b"ENCRYPTED")
            (wup / "title.tmd").write_bytes(b"TMD")

            rom_scanner.init(rom_dir)
            rom_id = rom_scanner.get().list_by_system("WIIU")[0].rom_id

            resp = client.get(
                f"/api/v1/roms/{rom_id}?extract=loadiine", headers=auth_headers
            )
            assert resp.status_code == 503
            assert "SYNC_ROM_WIIU_LOADIINE_COMMAND" in resp.text
        finally:
            settings.rom_dir = original_dir
            settings.rom_wiiu_loadiine_command = original_cmd
            rom_scanner._catalog = None

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

    def test_ps1_vcd_extract_route(self, rom_client_ps1_vcd, auth_headers):
        """``GET /api/v1/roms/<id>?extract=vcd`` runs the configured VCD
        converter and streams a POPStarter .VCD back.  OPL on PS2 reads
        these from the USB POPS/ folder to play PS1 games via POPS.
        """
        resp = rom_client_ps1_vcd.get(
            "/api/v1/roms?system=PS1", headers=auth_headers
        )
        assert resp.status_code == 200
        roms = resp.json()["roms"]
        assert len(roms) == 1
        rom_id = roms[0]["rom_id"]

        # The catalog row should advertise vcd in its extract_formats.
        assert "vcd" in roms[0].get("extract_formats", [])

        r2 = rom_client_ps1_vcd.get(
            f"/api/v1/roms/{rom_id}?extract=vcd", headers=auth_headers
        )
        assert r2.status_code == 200
        assert r2.headers["content-type"] == "application/octet-stream"
        assert r2.content == b"VCD:DISC"

    def test_ps1_vcd_unconfigured_returns_503(
        self, rom_dir, client, auth_headers
    ):
        """Without a VCD command template the route must surface a 503
        with a hint pointing at SYNC_ROM_PS1_VCD_COMMAND."""
        from app.services import rom_db, rom_scanner

        original = settings.rom_dir
        original_cmd = settings.rom_ps1_vcd_command
        try:
            rom_db.init_db(settings.save_dir)
            _load_server_game_name_data()
            settings.rom_dir = rom_dir
            settings.rom_ps1_vcd_command = ""
            (rom_dir / "psx").mkdir()
            (rom_dir / "psx" / "Foo.iso").write_bytes(b"x")
            rom_scanner.init(rom_dir)

            rom_id = rom_scanner.get().list_by_system("PS1")[0].rom_id
            resp = client.get(
                f"/api/v1/roms/{rom_id}?extract=vcd", headers=auth_headers
            )
            assert resp.status_code == 503
            assert "SYNC_ROM_PS1_VCD_COMMAND" in resp.text
        finally:
            settings.rom_dir = original
            settings.rom_ps1_vcd_command = original_cmd
            rom_scanner._catalog = None

    def test_ps1_cue_bundle_advertises_and_downloads_psio(
        self, tmp_path, client, auth_headers
    ):
        """PSIO profiles need BIN/CU2 output, including PS1 bundle rows."""
        from app.services import rom_db, rom_scanner
        from app.config import settings
        import io
        import zipfile as zf_mod

        original = settings.rom_dir
        try:
            rom_db.init_db(tmp_path)
            rom_dir = tmp_path / "roms"
            settings.rom_dir = rom_dir

            bundle = rom_dir / "psx" / "Ridge Racer"
            bundle.mkdir(parents=True)
            (bundle / "Ridge Racer.bin").write_bytes(b"\0" * 2352 * 300)
            (bundle / "Ridge Racer.cue").write_text(
                "\n".join(
                    [
                        'FILE "Ridge Racer.bin" BINARY',
                        "  TRACK 01 MODE2/2352",
                        "    INDEX 01 00:02:00",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            rom_scanner.init(rom_dir)
            rom = rom_scanner.get().list_by_system("PS1")[0]

            catalog_resp = client.get("/api/v1/roms?system=PS1", headers=auth_headers)
            assert catalog_resp.status_code == 200
            assert "psio" in catalog_resp.json()["roms"][0]["extract_formats"]

            resp = client.get(
                f"/api/v1/roms/{rom.rom_id}?extract=psio", headers=auth_headers
            )
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "application/zip"
            with zf_mod.ZipFile(io.BytesIO(resp.content)) as zf:
                assert sorted(zf.namelist()) == ["Ridge Racer.bin", "Ridge Racer.cu2"]
                assert zf.read("Ridge Racer.bin") == b"\0" * 2352 * 300
                assert "01_01 41 00:00:00 00:00:00 00:02:00" in zf.read(
                    "Ridge Racer.cu2"
                ).decode("utf-8")
        finally:
            settings.rom_dir = original
            rom_scanner._catalog = None

    def test_ps1_disc_groups_require_disc_tag(self):
        """Same-serial single-disc revisions must not be reported as a
        multi-disc set; only explicit (Disc N) files group together."""
        from app.routes.roms import _ps1_compute_disc_groups

        class _E:
            def __init__(self, rom_id, title_id, filename):
                self.rom_id = rom_id
                self.title_id = title_id
                self.filename = filename
                self.system = "PS1"

        entries = [
            _E("SCUS94228", "SCUS94228", "Alundra (USA).chd"),
            _E(
                "SCUS94228__r1",
                "SCUS94228",
                "Alundra (USA) (Rev 1) [Un-Worked Design by Supper v1].chd",
            ),
            _E("SCUS94163", "SCUS94163", "Final Fantasy VII (USA) (Disc 1).chd"),
            _E("SCUS94163__d2", "SCUS94163", "Final Fantasy VII (USA) (Disc 2).chd"),
        ]

        meta = _ps1_compute_disc_groups(entries)

        # Single-disc revisions: each reports (1, 1, self) — not combined.
        assert meta["SCUS94228"] == (1, 1, "SCUS94228")
        assert meta["SCUS94228__r1"] == (1, 1, "SCUS94228__r1")
        # Real two-disc set: total 2, shared primary.
        assert meta["SCUS94163"] == (1, 2, "SCUS94163")
        assert meta["SCUS94163__d2"] == (2, 2, "SCUS94163")

    def test_ps1_psio_multidisc_flat_layout(
        self, tmp_path, client, auth_headers, monkeypatch
    ):
        """Multi-disc PSIO output is flat in one game folder: each disc's
        BIN/CU2 share a clean stem (translation tags stripped) plus a
        MULTIDISC.LST — no per-disc subfolders."""
        from app.routes import roms as roms_mod
        from app.config import settings
        import io
        import zipfile as zf_mod

        original = settings.rom_dir
        try:
            roms = tmp_path / "roms"
            psx = roms / "psx"
            psx.mkdir(parents=True)
            settings.rom_dir = roms

            for n in (1, 2):
                stem = f"Some Game (USA) (Disc {n}) [T-En by X]"
                (psx / f"{stem}.bin").write_bytes(b"\0" * 2352 * 300)
                (psx / f"{stem}.cue").write_text(
                    f'FILE "{stem}.bin" BINARY\n'
                    "  TRACK 01 MODE2/2352\n"
                    "    INDEX 01 00:02:00\n",
                    encoding="utf-8",
                )

            class _Entry:
                def __init__(self, rom_id, n):
                    stem = f"Some Game (USA) (Disc {n}) [T-En by X]"
                    self.rom_id = rom_id
                    self.title_id = "SLUS00001"
                    self.system = "PS1"
                    self.name = stem
                    self.filename = f"{stem}.cue"
                    self.path = f"psx/{stem}.cue"
                    self.is_bundle = False

            entries = [_Entry("SLUS00001", 1), _Entry("SLUS00001__d2", 2)]

            class _Catalog:
                def get(self, rid):
                    return next((e for e in entries if e.rom_id == rid), None)

                def list_all(self):
                    return entries

            monkeypatch.setattr(roms_mod.rom_scanner, "get", lambda: _Catalog())

            resp = client.get(
                "/api/v1/roms/SLUS00001?extract=psio", headers=auth_headers
            )
            assert resp.status_code == 200, resp.content
            assert resp.headers["content-type"] == "application/zip"
            with zf_mod.ZipFile(io.BytesIO(resp.content)) as zf:
                assert sorted(zf.namelist()) == [
                    "MULTIDISC.LST",
                    "Some Game (USA) (Disc 1).bin",
                    "Some Game (USA) (Disc 1).cu2",
                    "Some Game (USA) (Disc 2).bin",
                    "Some Game (USA) (Disc 2).cu2",
                ]
                assert zf.read("MULTIDISC.LST") == (
                    b"Some Game (USA) (Disc 1).bin\r\n"
                    b"Some Game (USA) (Disc 2).bin\r\n"
                )
        finally:
            settings.rom_dir = original

    def test_ps1_psio_base_name_is_ascii_and_short(self):
        """PSIO can't render non-ASCII and caps filenames at 60 chars; the
        base-name helper must drop accents and translation tags up front."""
        from app.routes.roms import _ps1_psio_base_name

        assert _ps1_psio_base_name("Pokémon (USA) [T-En by X]") == "Pokemon (USA)"
        # CJK bytes have no ASCII fallback -> dropped, leaving the region tag.
        assert _ps1_psio_base_name("テトリス (Japan)") == "(Japan)"
        assert _ps1_psio_base_name("Game (Disc 1) (USA)") == "Game (USA)"

    def test_ps1_psio_caps_member_names_at_60_chars(
        self, tmp_path, client, auth_headers, monkeypatch
    ):
        """Single + multi-disc PSIO output must keep every BIN/CU2 member
        name (with extension) within PSIO's 60-char filename limit."""
        from app.routes import roms as roms_mod
        from app.config import settings
        import io
        import zipfile as zf_mod

        original = settings.rom_dir
        try:
            roms = tmp_path / "roms"
            psx = roms / "psx"
            psx.mkdir(parents=True)
            settings.rom_dir = roms

            long_name = (
                "A Very Long PlayStation Game Title That Goes Well Beyond The "
                "Sixty Character PSIO Filename Limit For Sure"
            )

            def _write_disc(stem):
                (psx / f"{stem}.bin").write_bytes(b"\0" * 2352 * 300)
                (psx / f"{stem}.cue").write_text(
                    f'FILE "{stem}.bin" BINARY\n'
                    "  TRACK 01 MODE2/2352\n"
                    "    INDEX 01 00:02:00\n",
                    encoding="utf-8",
                )

            # Two discs so the multi-disc suffix path is exercised too.
            _write_disc(f"{long_name} (Disc 1)")
            _write_disc(f"{long_name} (Disc 2)")

            class _Entry:
                def __init__(self, rom_id, n):
                    stem = f"{long_name} (Disc {n})"
                    self.rom_id = rom_id
                    self.title_id = "SLUS99999"
                    self.system = "PS1"
                    self.name = stem
                    self.filename = f"{stem}.cue"
                    self.path = f"psx/{stem}.cue"
                    self.is_bundle = False

            entries = [_Entry("SLUS99999", 1), _Entry("SLUS99999__d2", 2)]

            class _Catalog:
                def get(self, rid):
                    return next((e for e in entries if e.rom_id == rid), None)

                def list_all(self):
                    return entries

            monkeypatch.setattr(roms_mod.rom_scanner, "get", lambda: _Catalog())

            resp = client.get(
                "/api/v1/roms/SLUS99999?extract=psio", headers=auth_headers
            )
            assert resp.status_code == 200, resp.content
            with zf_mod.ZipFile(io.BytesIO(resp.content)) as zf:
                names = zf.namelist()
                assert "MULTIDISC.LST" in names
                for member in names:
                    assert len(member) <= 60, f"{member!r} exceeds 60 chars"
                # BIN/CU2 within a disc must still share a stem.
                bins = sorted(n for n in names if n.endswith(".bin"))
                for bin_name in bins:
                    assert bin_name.replace(".bin", ".cu2") in names
                # MULTIDISC.LST must reference the (truncated) bin names.
                lst = zf.read("MULTIDISC.LST").decode("utf-8").splitlines()
                assert sorted(line.strip() for line in lst) == sorted(bins)
        finally:
            settings.rom_dir = original

    def test_cue_to_cu2_writer_handles_track_offsets(self, tmp_path):
        from app.routes.roms import _cue_to_cu2_text

        (tmp_path / "game.bin").write_bytes(b"\0" * 2352 * 600)
        cue = tmp_path / "game.cue"
        cue.write_text(
            "\n".join(
                [
                    'FILE "game.bin" BINARY',
                    "  TRACK 01 MODE2/2352",
                    "    INDEX 01 00:02:00",
                    "  TRACK 02 AUDIO",
                    "    INDEX 00 00:04:00",
                    "    INDEX 01 00:06:00",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        text, refs = _cue_to_cu2_text(cue)

        assert refs == [tmp_path / "game.bin"]
        assert text.splitlines() == [
            "01_01 41 00:00:00 00:00:00 00:02:00",
            "02_00 01 00:04:00 00:04:00 00:02:00",
            "02_01 01 00:06:00 00:04:00 00:02:00",
        ]

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

    def test_decrypted_cci_of_already_decrypted_rom_skips_the_converter(
        self, rom_client_3ds_raw, auth_headers
    ):
        """A .3ds whose NCCH headers already say NoCrypto IS a decrypted .cci —
        serve it verbatim instead of running ninfs over it."""
        client, carts = rom_client_3ds_raw
        rom_id = _rom_id_for(client, auth_headers, "Flagged")

        resp = client.get(f"/api/v1/roms/{rom_id}?extract=decrypted_cci", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.content == carts["Flagged"]          # no 'DCCI:' prefix
        assert resp.headers["content-disposition"].endswith('filename="Flagged (USA).cci"')

    def test_decrypted_cci_patches_stale_crypto_flags(self, rom_client_3ds_raw, auth_headers):
        """Plaintext data + 'still encrypted' flags: copy through, fixing the
        flags, rather than letting ninfs decrypt plaintext into garbage."""
        from app.services import ctr_rom

        client, carts = rom_client_3ds_raw
        rom_id = _rom_id_for(client, auth_headers, "Stale")

        resp = client.get(f"/api/v1/roms/{rom_id}?extract=decrypted_cci", headers=auth_headers)
        assert resp.status_code == 200
        assert not resp.content.startswith(b"DCCI:")
        assert len(resp.content) == len(carts["Stale"])

        flags_at = 0x4000 + ctr_rom.NCCH_FLAGS_OFFSET
        assert resp.content[flags_at + 7] & ctr_rom.FLAG_NO_CRYPTO
        assert resp.content[flags_at + 3] == 0x00

    def test_cia_conversion_gets_flag_corrected_input(self, rom_client_3ds_raw, auth_headers):
        """3dsconv trusts the crypto flags, so it must never see a plaintext
        ROM that claims to be encrypted."""
        from app.services import ctr_rom

        client, carts = rom_client_3ds_raw
        rom_id = _rom_id_for(client, auth_headers, "Stale")

        resp = client.get(f"/api/v1/roms/{rom_id}?extract=cia", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.content.startswith(b"CIA:")

        seen_by_converter = resp.content[len(b"CIA:"):]
        flags_at = 0x4000 + ctr_rom.NCCH_FLAGS_OFFSET
        assert seen_by_converter[flags_at + 7] & ctr_rom.FLAG_NO_CRYPTO
        assert seen_by_converter != carts["Stale"]

    def test_retail_dump_with_encrypted_update_partition_converts(
        self, rom_client_3ds_raw, auth_headers
    ):
        """Regression: an encrypted manual/update partition must not make the
        whole cart look encrypted — that sent real decrypted dumps into ninfs
        and 3dsconv, which then decrypted plaintext into garbage."""
        from app.services import ctr_rom

        client, carts = rom_client_3ds_raw
        rom_id = _rom_id_for(client, auth_headers, "Retail")

        resp = client.get(f"/api/v1/roms/{rom_id}?extract=decrypted_cci", headers=auth_headers)
        assert resp.status_code == 200
        assert not resp.content.startswith(b"DCCI:")           # converter skipped

        flags_at = 0x4000 + ctr_rom.NCCH_FLAGS_OFFSET
        assert resp.content[flags_at + 7] & ctr_rom.FLAG_NO_CRYPTO

        cia = client.get(f"/api/v1/roms/{rom_id}?extract=cia", headers=auth_headers)
        assert cia.status_code == 200
        seen_by_converter = cia.content[len(b"CIA:"):]
        assert seen_by_converter[flags_at + 7] & ctr_rom.FLAG_NO_CRYPTO

    def test_cia_conversion_of_encrypted_rom_is_untouched(self, rom_client_3ds_raw, auth_headers):
        client, carts = rom_client_3ds_raw
        rom_id = _rom_id_for(client, auth_headers, "Encrypted")

        resp = client.get(f"/api/v1/roms/{rom_id}?extract=cia", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.content == b"CIA:" + carts["Encrypted"]

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


    def test_download_xbox_xbe_iso_bundle_streams_iso_directly(
        self, tmp_path, client, auth_headers
    ):
        """Xbox bundle with default.xbe + .iso streams the ISO directly
        when ?extract=iso is requested — no CCI conversion needed.
        """
        from app.services import rom_db, rom_scanner
        from app.config import settings

        original_rom_dir = settings.rom_dir
        original_interval = settings.rom_scan_interval
        try:
            settings.rom_scan_interval = 0
            rom_db.init_db(settings.save_dir)
            rom_dir = tmp_path / "roms"
            settings.rom_dir = rom_dir
            bundle = rom_dir / "xbox" / "Fable"
            bundle.mkdir(parents=True)
            (bundle / "Fable.iso").write_bytes(b"ISODISC_FABLE")
            (bundle / "default.xbe").write_bytes(b"LAUNCHER")

            rom_scanner.init(rom_dir)
            entries = rom_scanner.get().list_by_system("XBOX")
            bundles = [e for e in entries if e.is_bundle]
            assert len(bundles) == 1
            rom_id = bundles[0].rom_id

            resp = client.get(
                f"/api/v1/roms/{rom_id}?extract=iso", headers=auth_headers
            )
            assert resp.status_code == 200
            assert resp.content == b"ISODISC_FABLE"
        finally:
            settings.rom_dir = original_rom_dir
            settings.rom_scan_interval = original_interval
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

    def test_scan_indexes_sega_cd_under_the_canonical_code(self, rom_dir, tmp_path):
        """Every Sega CD folder spelling must index as SEGACD, not the SCD alias.

        Saves are keyed ``SEGACD_<slug>``; indexing ROMs as ``SCD_<slug>``
        split the same game across two keys and made the catalog look empty
        to a client asking for the canonical code.
        """
        from app.services import rom_db, rom_scanner

        rom_db.init_db(tmp_path / "saves")

        for folder, name in (
            ("segacd", "Sonic CD (USA).chd"),
            ("megacd", "Snatcher (USA).chd"),
            ("scd", "Popful Mail (USA).chd"),
        ):
            (rom_dir / folder).mkdir()
            (rom_dir / folder / name).write_bytes(b"\x00" * 64)

        catalog = rom_scanner.RomCatalog()
        assert catalog.scan(rom_dir, use_crc32=False) == 3

        rows = rom_db.list_all()
        assert {row["system"] for row in rows} == {"SEGACD"}
        assert all(row["title_id"].startswith("SEGACD_") for row in rows)

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


# ── Wii split-WBFS conversion ───────────────────────────────────────────────


@pytest.fixture()
def rom_client_wii(rom_dir, client, auth_headers, tmp_path):
    """Catalog with a single Wii ISO plus a writable conversion cache."""
    from app.services import rom_db, rom_scanner

    original_rom_dir = settings.rom_dir
    original_interval = settings.rom_scan_interval
    original_tmp = settings.tmp_dir

    settings.rom_dir = rom_dir
    settings.rom_scan_interval = 0
    settings.tmp_dir = tmp_path / "conv_tmp"

    rom_db.init_db(settings.save_dir)

    (rom_dir / "wii").mkdir()
    (rom_dir / "wii" / "Mario Kart Wii (USA).iso").write_bytes(b"WIIDISC" * 64)

    rom_scanner.init(rom_dir)

    yield client

    settings.rom_dir = original_rom_dir
    settings.rom_scan_interval = original_interval
    settings.tmp_dir = original_tmp
    rom_scanner._catalog = None


def _wii_rom_id(client, auth_headers):
    r = client.get("/api/v1/roms?system=WII", headers=auth_headers)
    assert r.status_code == 200
    roms = r.json()["roms"]
    assert roms, "expected the Wii ISO in the catalog"
    return roms[0]["rom_id"]


def _install_fake_wit(monkeypatch, part_bytes):
    """Stand in for the wit binary.

    ``wit id6`` prints an ID6; ``wit copy --wbfs --split`` writes the split
    parts next to the requested output path.  Everything else in the pipeline
    (cache dir, manifest, Range serving) is the real code.
    """
    from app.routes import roms as roms_mod
    import subprocess as _sp

    monkeypatch.setattr(roms_mod, "_wit_binary", lambda: "wit")

    real_run = _sp.run

    def fake_run(cmd, *args, **kwargs):
        if cmd and cmd[0] == "wit":
            if cmd[1] == "id6":
                return _sp.CompletedProcess(cmd, 0, stdout="RMCE01\n", stderr="")
            if cmd[1] == "copy":
                out = Path(cmd[-1])
                out.write_bytes(part_bytes[0])
                out.with_suffix(".wbf1").write_bytes(part_bytes[1])
                return _sp.CompletedProcess(cmd, 0, stdout="", stderr="")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(roms_mod.subprocess, "run", fake_run)


class TestWbfsExtract:
    def test_manifest_503_without_wit(self, rom_client_wii, auth_headers, monkeypatch):
        from app.routes import roms as roms_mod

        monkeypatch.setattr(roms_mod, "_wit_binary", lambda: None)
        rom_id = _wii_rom_id(rom_client_wii, auth_headers)

        r = rom_client_wii.get(
            f"/api/v1/roms/{quote(rom_id, safe='')}/wbfs-manifest",
            headers=auth_headers,
        )
        assert r.status_code == 503
        assert "wit" in r.json()["detail"].lower()

    def test_manifest_lists_split_parts(self, rom_client_wii, auth_headers, monkeypatch):
        _install_fake_wit(monkeypatch, (b"A" * 100, b"B" * 50))
        rom_id = _wii_rom_id(rom_client_wii, auth_headers)

        r = rom_client_wii.get(
            f"/api/v1/roms/{quote(rom_id, safe='')}/wbfs-manifest",
            headers=auth_headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["game_id"] == "RMCE01"
        names = {f["name"]: f["size"] for f in body["files"]}
        assert names == {"RMCE01.wbfs": 100, "RMCE01.wbf1": 50}

    def test_part_download(self, rom_client_wii, auth_headers, monkeypatch):
        _install_fake_wit(monkeypatch, (b"A" * 100, b"B" * 50))
        rom_id = _wii_rom_id(rom_client_wii, auth_headers)
        base = f"/api/v1/roms/{quote(rom_id, safe='')}/wbfs"

        r = rom_client_wii.get(f"{base}/RMCE01.wbfs", headers=auth_headers)
        assert r.status_code == 200
        assert r.content == b"A" * 100

        r = rom_client_wii.get(f"{base}/RMCE01.wbf1", headers=auth_headers)
        assert r.status_code == 200
        assert r.content == b"B" * 50

    def test_part_supports_range_resume(self, rom_client_wii, auth_headers, monkeypatch):
        _install_fake_wit(monkeypatch, (b"0123456789" * 10, b"B" * 50))
        rom_id = _wii_rom_id(rom_client_wii, auth_headers)

        r = rom_client_wii.get(
            f"/api/v1/roms/{quote(rom_id, safe='')}/wbfs/RMCE01.wbfs",
            headers={**auth_headers, "Range": "bytes=90-"},
        )
        assert r.status_code == 206
        assert r.content == b"0123456789"
        assert r.headers["content-range"] == "bytes 90-99/100"

    def test_bad_part_name_rejected(self, rom_client_wii, auth_headers, monkeypatch):
        _install_fake_wit(monkeypatch, (b"A" * 10, b"B" * 10))
        rom_id = _wii_rom_id(rom_client_wii, auth_headers)

        r = rom_client_wii.get(
            f"/api/v1/roms/{quote(rom_id, safe='')}/wbfs/..%2Fsecret.txt",
            headers=auth_headers,
        )
        assert r.status_code in (400, 404)


# ── MSU packs ────────────────────────────────────────────────────────────────

_MSU_MD_CUE = b'FILE "Game (MSU-MD).bin" BINARY\n  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n'
_MD_PLUS_CUE = b'FILE "Game - Track 02.wav" WAVE\n  TRACK 02 AUDIO\n    INDEX 01 00:00:00\n'


def _write_msu1_zip(path: Path, root: str | None, stem: str = "Game (USA) (MSU1)") -> None:
    """A pack zipped the way the SNES sets come: one wrapping folder."""
    prefix = f"{root}/" if root else ""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as zf:
        if root:
            zf.writestr(f"{root}/", b"")
        zf.writestr(f"{prefix}{stem}.sfc", b"R" * 300)
        zf.writestr(f"{prefix}{stem}.msu", b"")
        zf.writestr(f"{prefix}{stem}-1.pcm", b"P" * 1000)
        zf.writestr(f"{prefix}{stem}-2.pcm", b"Q" * 500)
        zf.writestr(f"{prefix}{stem}.srm", b"S" * 64)  # junk: author's save


class TestMsuPacks:
    def test_loose_folder_packs_become_kinded_bundles(self, tmp_path):
        from app.services import rom_db, rom_scanner

        rom_db.init_db(tmp_path)
        rom_dir = tmp_path / "roms"

        snes = rom_dir / "snes" / "ActRaiser (USA) (MSU1)"
        snes.mkdir(parents=True)
        (snes / "ActRaiser (USA) (MSU1).sfc").write_bytes(b"R" * 100)
        (snes / "ActRaiser (USA) (MSU1).msu").write_bytes(b"")
        (snes / "ActRaiser (USA) (MSU1)-1.pcm").write_bytes(b"P" * 200)
        (snes / "ActRaiser (USA) (MSU1).srm").write_bytes(b"S" * 8)
        # Plain ROM of the same game sits beside it.
        (rom_dir / "snes" / "ActRaiser (USA).sfc").write_bytes(b"R" * 100)

        md = rom_dir / "genesis" / "Game (USA) (MSU-MD)"
        md.mkdir(parents=True)
        (md / "Game (MSU-MD).md").write_bytes(b"M" * 100)
        (md / "Game (MSU-MD).cue").write_bytes(_MSU_MD_CUE)
        (md / "Game (MSU-MD).bin").write_bytes(b"A" * 400)

        plus = rom_dir / "genesis" / "Other (USA) (MD+)"
        plus.mkdir(parents=True)
        (plus / "Game.md").write_bytes(b"M" * 100)
        (plus / "Game.cue").write_bytes(_MD_PLUS_CUE)
        (plus / "Game - Track 02.wav").write_bytes(b"W" * 400)

        catalog = rom_scanner.RomCatalog()
        catalog.scan(rom_dir, use_crc32=False)

        snes_entries = catalog.list_by_system("SNES")
        assert len(snes_entries) == 2, [e.filename for e in snes_entries]
        pack = next(e for e in snes_entries if e.is_bundle)
        plain = next(e for e in snes_entries if not e.is_bundle)
        assert pack.bundle_kind == "msu1"
        assert pack.name == "ActRaiser (USA) (MSU1)"
        assert pack.path == "snes/ActRaiser (USA) (MSU1)"
        names = sorted(f["name"] for f in pack.bundle_files)
        assert names == [
            "ActRaiser (USA) (MSU1)-1.pcm",
            "ActRaiser (USA) (MSU1).msu",
            "ActRaiser (USA) (MSU1).sfc",
        ]
        assert pack.size == 300
        # The tag folds away so pack and plain ROM share a save slot...
        assert pack.title_id == plain.title_id
        # ...while the catalog still keys them apart.
        assert pack.rom_id != plain.rom_id
        assert pack.to_dict()["bundle_kind"] == "msu1"
        assert "bundle_kind" not in plain.to_dict()

        md_entries = {e.name: e for e in catalog.list_by_system("MD")}
        assert set(md_entries) == {"Game (USA) (MSU-MD)", "Other (USA) (MD+)"}
        assert md_entries["Game (USA) (MSU-MD)"].bundle_kind == "msu-md"
        assert md_entries["Other (USA) (MD+)"].bundle_kind == "mdplus"

        # Round-trips through SQLite.
        reloaded = rom_scanner.RomCatalog()
        reloaded.load_from_db()
        assert reloaded.get(pack.rom_id).bundle_kind == "msu1"

    def test_pack_named_without_region_keys_off_its_cart(self, tmp_path):
        """``genesis/Sonic The Hedgehog 2/`` holds ``Sonic The Hedgehog 2
        (World) (MSU-MD).md``: the folder misses the DAT, the cart hits it,
        so the pack still shares the plain ROM's save slot."""
        from app.services import rom_db, rom_scanner

        rom_db.init_db(tmp_path)
        rom_dir = tmp_path / "roms"
        pack = rom_dir / "genesis" / "Sonic The Hedgehog 2"
        pack.mkdir(parents=True)
        (pack / "Sonic The Hedgehog 2 (World) (MSU-MD).md").write_bytes(b"M")
        (pack / "Sonic The Hedgehog 2 (World) (MSU-MD).cue").write_bytes(
            b'FILE "Sonic The Hedgehog 2 (World) (MSU-MD).bin" BINARY\n  TRACK 01 AUDIO\n'
        )
        (pack / "Sonic The Hedgehog 2 (World) (MSU-MD).bin").write_bytes(b"A")
        (rom_dir / "genesis" / "Sonic The Hedgehog 2 (World).md").write_bytes(b"M")

        catalog = rom_scanner.RomCatalog()
        catalog.scan(rom_dir, use_crc32=False)
        entries = catalog.list_by_system("MD")
        assert len(entries) == 2
        assert len({e.title_id for e in entries}) == 1
        assert next(e for e in entries if e.is_bundle).name == "Sonic The Hedgehog 2"

    def test_pack_hack_tag_folds_onto_the_plain_rom(self, tmp_path):
        """``[Hack by …]`` names the MSU author; the pack shares the save of
        the ROM it patched.  A translated baseline keeps its own tags, and
        the pack must land on *that* id, not the untranslated one."""
        from app.services import rom_db, rom_scanner

        rom_db.init_db(tmp_path)
        rom_dir = tmp_path / "roms"
        snes = rom_dir / "snes"
        snes.mkdir(parents=True)
        (snes / "ActRaiser (USA).sfc").write_bytes(b"R")
        (snes / "Area 88 (USA) [T-En by Blizzz v1.03] [FastROM hack by Vitor Vilela v1.0] [n].sfc").write_bytes(b"R")
        (snes / "Area 88 (Japan).sfc").write_bytes(b"R")

        def pack(name: str, stem: str) -> None:
            with zipfile.ZipFile(snes / f"{name}.zip", "w") as zf:
                zf.writestr(f"{stem}.sfc", b"R")
                zf.writestr(f"{stem}.msu", b"")
                zf.writestr(f"{stem}-1.pcm", b"P")

        pack("ActRaiser (USA) (MSU1) [Hack by DarkShock v1.0]", "ActRaiser (USA) (MSU1)")
        pack("Area 88 (USA) (MSU1) [T-En by Blizzz v1.03] [Hack by Kurrono & Conn v2] "
             "[FastROM hack by Vitor Vilela v1.0] [n]", "Area 88 (USA) (MSU1) [T-En by Blizzz v1.03]")
        # No plain ROM at all: the stripped name is the fallback key.
        pack("Aerobiz (USA) (MSU1) [Hack by PepilloPev v1.0]", "Aerobiz (USA) (MSU1)")

        catalog = rom_scanner.RomCatalog()
        catalog.scan(rom_dir, use_crc32=False)
        by_name = {e.name: e for e in catalog.list_by_system("SNES")}

        assert (by_name["ActRaiser (USA) (MSU1) [Hack by DarkShock v1.0]"].title_id
                == by_name["ActRaiser (USA)"].title_id)
        area_pack = next(e for e in by_name.values() if e.is_bundle and "Area 88" in e.name)
        assert area_pack.title_id == by_name[
            "Area 88 (USA) [T-En by Blizzz v1.03] [FastROM hack by Vitor Vilela v1.0] [n]"
        ].title_id
        assert area_pack.title_id != by_name["Area 88 (Japan)"].title_id
        assert by_name["Aerobiz (USA) (MSU1) [Hack by PepilloPev v1.0]"].title_id == "SNES_aerobiz_usa"

    def test_zipped_pack_is_a_file_bundle_with_root_stripped(self, tmp_path):
        from app.services import rom_db, rom_scanner

        rom_db.init_db(tmp_path)
        rom_dir = tmp_path / "roms"
        (rom_dir / "snes" / "msu1").mkdir(parents=True)
        _write_msu1_zip(rom_dir / "snes" / "msu1" / "Game (USA) (MSU1).zip",
                        root="Game (USA) (MSU1)")
        # Ordinary zipped ROM: small, no hint in the name — never opened.
        with zipfile.ZipFile(rom_dir / "snes" / "Plain (USA).zip", "w") as zf:
            zf.writestr("Plain (USA).sfc", b"R" * 100)

        catalog = rom_scanner.RomCatalog()
        catalog.scan(rom_dir, use_crc32=False)

        entries = {e.filename: e for e in catalog.list_by_system("SNES")}
        assert set(entries) == {"Game (USA) (MSU1).zip", "Plain (USA).zip"}
        pack = entries["Game (USA) (MSU1).zip"]
        assert pack.is_bundle and pack.bundle_kind == "msu1"
        assert pack.path == "snes/msu1/Game (USA) (MSU1).zip"
        assert sorted(f["name"] for f in pack.bundle_files) == [
            "Game (USA) (MSU1)-1.pcm",
            "Game (USA) (MSU1)-2.pcm",
            "Game (USA) (MSU1).msu",
            "Game (USA) (MSU1).sfc",
        ]
        assert pack.size == 300 + 1000 + 500
        assert not entries["Plain (USA).zip"].is_bundle

    def test_zipped_pack_downloads_as_is_and_streams_members(
        self, tmp_path, client, auth_headers
    ):
        from app.services import rom_db, rom_scanner

        original = settings.rom_dir
        try:
            rom_db.init_db(tmp_path)
            rom_dir = tmp_path / "roms"
            (rom_dir / "snes").mkdir(parents=True)
            zip_path = rom_dir / "snes" / "Game (USA) (MSU1).zip"
            _write_msu1_zip(zip_path, root="Game (USA) (MSU1)")
            settings.rom_dir = rom_dir
            rom_scanner.init(rom_dir)
            rom_id = rom_scanner.get().list_by_system("SNES")[0].rom_id
            key = quote(rom_id, safe="")

            listed = client.get("/api/v1/roms", headers=auth_headers).json()["roms"]
            assert listed[0]["bundle_kind"] == "msu1"
            assert listed[0]["is_bundle"] is True

            # Whole pack: the very bytes on disk, resumable.
            r = client.get(f"/api/v1/roms/{key}", headers=auth_headers)
            assert r.status_code == 200
            assert r.content == zip_path.read_bytes()
            r = client.get(f"/api/v1/roms/{key}",
                           headers={**auth_headers, "Range": "bytes=10-19"})
            assert r.status_code == 206
            assert r.content == zip_path.read_bytes()[10:20]

            manifest = client.get(f"/api/v1/roms/{key}/manifest",
                                  headers=auth_headers).json()
            assert manifest["is_bundle"] is True
            assert {f["name"] for f in manifest["files"]} == {
                "Game (USA) (MSU1)-1.pcm", "Game (USA) (MSU1)-2.pcm",
                "Game (USA) (MSU1).msu", "Game (USA) (MSU1).sfc",
            }

            # One member, addressed without the wrapping folder.
            member = quote("Game (USA) (MSU1)-2.pcm", safe="")
            r = client.get(f"/api/v1/roms/{key}/file/{member}", headers=auth_headers)
            assert r.status_code == 200
            assert r.content == b"Q" * 500
            r = client.get(f"/api/v1/roms/{key}/file/missing.pcm", headers=auth_headers)
            assert r.status_code == 404
            r = client.get(f"/api/v1/roms/{key}/file/..%2Fescape", headers=auth_headers)
            assert r.status_code in (400, 404)

            # cleanup_missing must not think a file-backed bundle is gone.
            assert rom_scanner.cleanup_missing() == 0
            zip_path.unlink()
            assert rom_scanner.cleanup_missing() == 1
        finally:
            settings.rom_dir = original
            rom_scanner._catalog = None

    def test_rootless_zip_and_bad_zip(self, tmp_path):
        from app.services import rom_db, rom_scanner

        rom_db.init_db(tmp_path)
        rom_dir = tmp_path / "roms"
        (rom_dir / "genesis").mkdir(parents=True)
        with zipfile.ZipFile(rom_dir / "genesis" / "Game (MSU-MD).zip", "w") as zf:
            zf.writestr("Game (MSU-MD).md", b"M" * 10)
            zf.writestr("Game (MSU-MD).cue", _MSU_MD_CUE)
            zf.writestr("Game (MSU-MD).bin", b"A" * 10)
        # Name says MSU, contents are garbage: falls back to a plain zip row.
        (rom_dir / "genesis" / "Broken (MSU-MD).zip").write_bytes(b"not a zip")

        catalog = rom_scanner.RomCatalog()
        catalog.scan(rom_dir, use_crc32=False)
        entries = {e.filename: e for e in catalog.list_by_system("MD")}
        assert entries["Game (MSU-MD).zip"].bundle_kind == "msu-md"
        assert not entries["Broken (MSU-MD).zip"].is_bundle


# ── PS2 disc media resolution ───────────────────────────────────────────────
#
# A PS2 CHD is either a DVD rip (extracts to one ISO) or a CD rip (extracts to
# a CUE/BIN zip), and the WebUI has to pick the right button before any
# conversion runs.  The libretro PS2 DAT answers for every listed title; the
# CHD header answers for everything else.

_PS2_DAT = """clrmamepro (
\tname "Sony - PlayStation 2"
)

game (
\tname "Zero (Japan)"
\tregion "Japan"
\tserial "SLPS-25074"
\trom ( name "Zero (Japan).iso" size 2828369920 crc 26A9A7AB serial "SLPS-25074" )
)
game (
\tname "Ridge Racer V (USA)"
\tregion "USA"
\tserial "SLUS-20002"
\trom ( name "Ridge Racer V (USA).cue" size 1000 crc AABBCCDD serial "SLUS-20002" )
\trom ( name "Ridge Racer V (USA).bin" size 600000000 crc 11223344 serial "SLUS-20002" )
)
"""


def _write_ps2_dat(tmp_path):
    dats = tmp_path / "dats"
    dats.mkdir()
    (dats / "Sony - PlayStation 2.dat").write_text(_PS2_DAT, encoding="utf-8")
    return dats


class TestPs2DiscFormatLookup:
    def test_dat_declares_media_per_title(self, tmp_path):
        from app.services.dat_normalizer import DatNormalizer

        n = DatNormalizer(_write_ps2_dat(tmp_path))
        assert n.lookup_disc_format("PS2", "Zero (Japan).chd") == "iso"
        assert n.lookup_disc_format("PS2", "Ridge Racer V (USA).chd") == "cue"

    def test_renamed_rip_resolves_by_serial_or_crc(self, tmp_path):
        from app.services.dat_normalizer import DatNormalizer

        n = DatNormalizer(_write_ps2_dat(tmp_path))
        # Filename tells us nothing — the catalog's serial does.
        assert n.lookup_disc_format("PS2", "my dump.chd") is None
        assert (
            n.lookup_disc_format("PS2", "my dump.chd", serial="SLPS25074") == "iso"
        )
        assert (
            n.lookup_disc_format("PS2", "my dump.chd", serial="SLUS-20002") == "cue"
        )
        assert n.lookup_disc_format("PS2", "x.chd", crc32="26a9a7ab") == "iso"

    def test_unknown_title_stays_none(self, tmp_path):
        from app.services.dat_normalizer import DatNormalizer

        n = DatNormalizer(_write_ps2_dat(tmp_path))
        assert n.lookup_disc_format("PS2", "Some Hack (v1.2).chd") is None


def _fake_chd(path, unit_bytes, meta_tags=()):
    """Minimal v5 CHD: header + a linked metadata chain."""
    import struct

    body = bytearray(124)
    body[0:8] = b"MComprHD"
    struct.pack_into(">I", body, 8, 124)     # header length
    struct.pack_into(">I", body, 12, 5)      # version
    struct.pack_into(">I", body, 56, 4096)   # hunkbytes
    struct.pack_into(">I", body, 60, unit_bytes)

    offsets = []
    for tag in meta_tags:
        offsets.append(len(body))
        entry = bytearray(24)
        entry[0:4] = tag
        struct.pack_into(">I", entry, 4, (1 << 24) | 8)  # flags | length
        body += entry
    for i, off in enumerate(offsets):
        nxt = offsets[i + 1] if i + 1 < len(offsets) else 0
        struct.pack_into(">Q", body, off + 8, nxt)
    if offsets:
        struct.pack_into(">Q", body, 48, offsets[0])

    path.write_bytes(bytes(body))
    return path


class TestChdMediaSniff:
    def test_cd_and_dvd_chds(self, tmp_path):
        from app.routes.roms import _read_chd_media_kind

        # chdman createcd → CD track metadata present.
        cd = _fake_chd(tmp_path / "cd.chd", 2448, (b"GDDD", b"CHT2"))
        assert _read_chd_media_kind(cd) == "cd"
        # Pre-CHT2 dumps used CHTR / CHCD.
        old = _fake_chd(tmp_path / "old.chd", 2448, (b"CHCD",))
        assert _read_chd_media_kind(old) == "cd"
        # chdman createdvd → no track metadata, 2048-byte units.
        dvd = _fake_chd(tmp_path / "dvd.chd", 2048, (b"GDDD",))
        assert _read_chd_media_kind(dvd) == "dvd"
        # Frame size is the tiebreak when metadata is missing entirely.
        assert _read_chd_media_kind(_fake_chd(tmp_path / "bare.chd", 2448)) == "cd"
        assert _read_chd_media_kind(_fake_chd(tmp_path / "raw.chd", 2048)) == "dvd"

    def test_non_chd_and_missing_file(self, tmp_path):
        from app.routes.roms import _chd_media_kind, _read_chd_media_kind

        junk = tmp_path / "junk.chd"
        junk.write_bytes(b"not a chd at all" * 8)
        assert _read_chd_media_kind(junk) is None
        assert _chd_media_kind(tmp_path / "missing.chd") is None

    def test_extract_formats_fall_back_to_the_chd(self, tmp_path, monkeypatch):
        from app.routes import roms
        from app.services.rom_scanner import RomEntry

        rom_dir = tmp_path / "roms"
        (rom_dir / "ps2").mkdir(parents=True)
        _fake_chd(rom_dir / "ps2" / "Unlisted Hack.chd", 2048, (b"GDDD",))
        _fake_chd(rom_dir / "ps2" / "Unlisted CD Hack.chd", 2448, (b"CHT2",))

        monkeypatch.setattr(settings, "rom_dir", rom_dir)
        monkeypatch.setattr(roms, "_dat_normalizer_get", lambda: None)
        roms._chd_media_cache.clear()

        def entry(filename):
            return RomEntry(
                rom_id=filename,
                title_id="SLUS99999",
                system="PS2",
                name=filename,
                filename=filename,
                path=f"ps2/{filename}",
                size=124,
                crc32="",
                source="scan",
            )

        assert roms._extract_formats_for_entry(entry("Unlisted Hack.chd")) == (
            "iso", ["iso"],
        )
        assert roms._extract_formats_for_entry(entry("Unlisted CD Hack.chd")) == (
            "cue", ["cue"],
        )


class TestPcfxCatalog:
    """NEC PC-FX discs are plain CD-ROM CHDs under ``<rom_dir>/pcfx/``.

    They index as ``PCFX`` (slug-keyed, like every non-serial CD system),
    stay one catalog row per disc, and advertise the same chdman CUE/BIN
    extract as PC Engine CD — the raw CHD is what emulators and clients
    take by default.
    """

    def test_pcfx_chds_index_as_pcfx_with_cue_extract(self, tmp_path, monkeypatch):
        from app.routes import roms
        from app.services import rom_db, rom_scanner

        rom_db.init_db(tmp_path)
        rom_dir = tmp_path / "roms"
        folder = rom_dir / "pcfx"
        folder.mkdir(parents=True)
        for name in (
            "Aa Megami-sama (Japan) (Disc 1).chd",
            "Aa Megami-sama (Japan) (Disc 2).chd",
            "Zenki FX - Vajura Fight (Japan).chd",
        ):
            (folder / name).write_bytes(b"x" * 64)

        monkeypatch.setattr(settings, "rom_dir", rom_dir)
        catalog = rom_scanner.RomCatalog()
        assert catalog.scan(rom_dir, use_crc32=False) == 3

        entries = catalog.list_by_system("PCFX")
        assert len(entries) == 3
        for entry in entries:
            assert entry.title_id.startswith("PCFX_")
            assert roms._extract_formats_for_entry(entry) == ("cue", ["cue"])

        discs = [e for e in entries if "Megami" in e.filename]
        # Both discs of one game share the save slot, but stay separate rows.
        assert len({e.title_id for e in discs}) == 1
        assert len({e.rom_id for e in discs}) == 2


class TestSatellaviewCatalog:
    """Satellaview (BS-X) broadcast games are ``.bs`` files, usually kept in
    the SNES folder beside the carts; they must be catalogued, not skipped."""

    def test_bs_files_in_the_snes_folder_are_indexed(self, tmp_path, monkeypatch):
        from app.services import rom_db, rom_scanner

        rom_db.init_db(tmp_path)
        rom_dir = tmp_path / "roms"
        folder = rom_dir / "snes"
        folder.mkdir(parents=True)
        (folder / "BS F-Zero Grand Prix 2 - Practice (Japan) (12-6).bs").write_bytes(b"x" * 64)
        (folder / "Super Metroid (USA).sfc").write_bytes(b"x" * 64)

        monkeypatch.setattr(settings, "rom_dir", rom_dir)
        catalog = rom_scanner.RomCatalog()
        assert catalog.scan(rom_dir, use_crc32=False) == 2
        names = {e.filename for e in catalog.list_by_system("SNES")}
        assert "BS F-Zero Grand Prix 2 - Practice (Japan) (12-6).bs" in names
