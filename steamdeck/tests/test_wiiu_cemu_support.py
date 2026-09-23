"""Cemu (Wii U) scanner: layout, hashing, meta.xml names, server-only rows."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
STEAMDECK_ROOT = ROOT / "steamdeck"
if str(STEAMDECK_ROOT) not in sys.path:
    sys.path.insert(0, str(STEAMDECK_ROOT))

from scanner import cemu  # noqa: E402
from scanner.models import SyncStatus  # noqa: E402


META_XML = """<?xml version="1.0" encoding="utf-8"?>
<menu>
  <product_code type="string" length="32">WUP-P-ARDE</product_code>
  <longname_ja type="string" length="512">マリオ</longname_ja>
  <longname_en type="string" length="512">Super Mario 3D World&apos;s
Special Edition</longname_en>
</menu>
"""


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Keep the developer's real ~/.config/Cemu out of the candidate search."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    return fake_home


def _make_save(mlc: Path, tidlo: str = "10143500") -> Path:
    save_dir = mlc / "usr" / "save" / "00050000" / tidlo / "user"
    (save_dir / "80000001").mkdir(parents=True)
    (save_dir / "common").mkdir(parents=True)
    (save_dir / "80000001" / "game.dat").write_bytes(b"slot-one")
    (save_dir / "common" / "shared.bin").write_bytes(b"common-data")
    return save_dir


def _write_meta(mlc: Path, tidlo: str = "10143500", high: str = "00050000") -> None:
    meta_dir = mlc / "usr" / "title" / high / tidlo / "meta"
    meta_dir.mkdir(parents=True)
    (meta_dir / "meta.xml").write_text(META_XML, encoding="utf-8")


def test_scan_reads_emudeck_storage_layout(tmp_path):
    emulation = tmp_path / "Emulation"
    mlc = emulation / "storage" / "cemu" / "mlc01"
    save_dir = _make_save(mlc)
    _write_meta(mlc)

    results = list(cemu.scan(emulation))

    assert len(results) == 1
    entry = results[0]
    assert entry.title_id == "0005000010143500"
    assert entry.system == "WIIU"
    assert entry.emulator == "Cemu"
    assert entry.save_path == save_dir
    assert entry.is_multi_file is True
    assert entry.display_name == "Super Mario 3D World's Special Edition"
    assert entry.game_code == "WIIU_ARDE"
    assert entry.save_size == len(b"slot-one") + len(b"common-data")


def test_save_hash_matches_server_bundle_hash(tmp_path):
    """The server hashes the concatenated bundle contents in path order."""
    emulation = tmp_path / "Emulation"
    mlc = emulation / "storage" / "cemu" / "mlc01"
    _make_save(mlc)

    entry = next(iter(cemu.scan(emulation)))

    # Bundle order is the relative path sort: 80000001/game.dat then
    # common/shared.bin.
    expected = hashlib.sha256(b"slot-one" + b"common-data").hexdigest()
    assert entry.save_hash == expected


def test_name_falls_back_to_update_title_meta(tmp_path):
    """A disc game has no 00050000 content — its update's meta.xml names it."""
    emulation = tmp_path / "Emulation"
    mlc = emulation / "storage" / "cemu" / "mlc01"
    _make_save(mlc)
    _write_meta(mlc, high="0005000E")

    entry = next(iter(cemu.scan(emulation)))

    assert entry.display_name == "Super Mario 3D World's Special Edition"
    assert entry.game_code == "WIIU_ARDE"


def test_title_without_meta_falls_back_to_title_id(tmp_path):
    emulation = tmp_path / "Emulation"
    mlc = emulation / "storage" / "cemu" / "mlc01"
    _make_save(mlc)

    entry = next(iter(cemu.scan(emulation)))

    assert entry.display_name == "0005000010143500"
    assert entry.game_code is None


def test_empty_user_dir_is_skipped(tmp_path):
    emulation = tmp_path / "Emulation"
    mlc = emulation / "storage" / "cemu" / "mlc01"
    (mlc / "usr" / "save" / "00050000" / "10143500" / "user").mkdir(parents=True)

    assert list(cemu.scan(emulation)) == []


def test_settings_xml_mlc_path_wins_over_default_location(tmp_path):
    """A user who moved the MLC has an empty default folder; follow settings."""
    emulation = tmp_path / "Emulation"
    # Default location exists but holds no saves.
    (emulation / "storage" / "cemu" / "mlc01" / "usr" / "save").mkdir(parents=True)

    real_mlc = tmp_path / "sdcard" / "mlc01"
    _make_save(real_mlc)

    settings = emulation / "storage" / "cemu" / "settings.xml"
    settings.write_text(
        f"<content><mlc_path>{real_mlc}</mlc_path></content>", encoding="utf-8"
    )

    entry = next(iter(cemu.scan(emulation)))
    assert entry.save_path == real_mlc / "usr" / "save" / "00050000" / "10143500" / "user"


def test_settings_xml_wine_path_is_translated(tmp_path, isolated_home):
    """EmuDeck's Proton Cemu writes Windows paths; Z: is Wine's root."""
    emulation = tmp_path / "Emulation"
    real_mlc = tmp_path / "wine" / "mlc01"
    _make_save(real_mlc)

    settings = isolated_home / ".config" / "Cemu" / "settings.xml"
    settings.parent.mkdir(parents=True)
    windows_path = "Z:" + str(real_mlc).replace("/", "\\")
    settings.write_text(
        f"<content><mlc_path>{windows_path}</mlc_path></content>", encoding="utf-8"
    )

    assert cemu.resolve_mlc_root(emulation) == real_mlc


def test_default_save_path_is_predictable(tmp_path):
    emulation = tmp_path / "Emulation"
    mlc = emulation / "storage" / "cemu" / "mlc01"
    _make_save(mlc)

    path = cemu.default_save_path(emulation, "0005000010143500")
    assert path == mlc / "usr" / "save" / "00050000" / "10143500" / "user"


def test_build_server_only_entries(tmp_path):
    emulation = tmp_path / "Emulation"
    mlc = emulation / "storage" / "cemu" / "mlc01"
    _make_save(mlc, tidlo="10143500")

    server_saves = {
        "0005000010101D00": {
            "title_id": "0005000010101D00",
            "name": "Splatoon",
            "system": "WIIU",
            "save_hash": "server-hash",
            "client_timestamp": 1700000000.0,
            "save_size": 4096,
        },
        # Already scanned locally — must not be duplicated.
        "0005000010143500": {"title_id": "0005000010143500", "system": "WIIU"},
        # 3DS ids are 16-hex too; they belong to the Azahar builder.
        "0004000000030800": {"title_id": "0004000000030800", "system": "3DS"},
    }

    entries = cemu.build_server_only_entries(
        server_saves, {"0005000010143500"}, emulation
    )

    assert [e.title_id for e in entries] == ["0005000010101D00"]
    entry = entries[0]
    assert entry.system == "WIIU"
    assert entry.status is SyncStatus.SERVER_ONLY
    assert entry.is_multi_file is True
    assert entry.display_name == "Splatoon"
    assert entry.save_path == (
        mlc / "usr" / "save" / "00050000" / "10101d00" / "user"
    )


GAME_META_XML = """<?xml version="1.0" encoding="utf-8"?>
<menu>
  <title_id type="hexBinary" length="8">0005000010101d00</title_id>
  <product_code type="string" length="32">WUP-P-AGMP</product_code>
  <longname_en type="string" length="512">Splatoon</longname_en>
</menu>
"""

UPDATE_META_XML = """<?xml version="1.0" encoding="utf-8"?>
<menu>
  <title_id type="hexBinary" length="8">0005000e10143500</title_id>
  <product_code type="string" length="32">WUP-P-ARDE</product_code>
  <longname_en type="string" length="512">Patched Disc Game</longname_en>
</menu>
"""


def test_loose_game_folder_names_a_save(tmp_path):
    """The common Cemu library is loose dumps that never touch the MLC."""
    emulation = tmp_path / "Emulation"
    mlc = emulation / "storage" / "cemu" / "mlc01"
    _make_save(mlc, tidlo="10101d00")

    game_meta = emulation / "roms" / "wiiu" / "Splatoon" / "meta"
    game_meta.mkdir(parents=True)
    (game_meta / "meta.xml").write_text(GAME_META_XML, encoding="utf-8")

    entry = next(iter(cemu.scan(emulation)))

    assert entry.title_id == "0005000010101D00"
    assert entry.display_name == "Splatoon"
    assert entry.game_code == "WIIU_AGMP"


def test_update_meta_title_id_folds_onto_the_application_id(tmp_path):
    """An update's meta.xml carries 0005000E but names the base game."""
    emulation = tmp_path / "Emulation"
    mlc = emulation / "storage" / "cemu" / "mlc01"
    _make_save(mlc, tidlo="10143500")

    game_meta = emulation / "roms" / "wiiu" / "Disc Game" / "meta"
    game_meta.mkdir(parents=True)
    (game_meta / "meta.xml").write_text(UPDATE_META_XML, encoding="utf-8")

    entry = next(iter(cemu.scan(emulation)))

    assert entry.title_id == "0005000010143500"
    assert entry.display_name == "Patched Disc Game"


def test_cemu_configured_game_path_is_searched(tmp_path, isolated_home):
    emulation = tmp_path / "Emulation"
    mlc = emulation / "storage" / "cemu" / "mlc01"
    _make_save(mlc, tidlo="10101d00")

    games = tmp_path / "elsewhere" / "wiiu-games"
    game_meta = games / "Splatoon" / "meta"
    game_meta.mkdir(parents=True)
    (game_meta / "meta.xml").write_text(GAME_META_XML, encoding="utf-8")

    settings = isolated_home / ".config" / "Cemu" / "settings.xml"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        f"<content><GamePaths><Entry>{games}</Entry></GamePaths></content>",
        encoding="utf-8",
    )

    entry = next(iter(cemu.scan(emulation)))
    assert entry.display_name == "Splatoon"


def test_server_only_entry_uses_local_meta_when_server_has_raw_id(tmp_path):
    emulation = tmp_path / "Emulation"
    mlc = emulation / "storage" / "cemu" / "mlc01"
    (mlc / "usr" / "save").mkdir(parents=True)

    game_meta = emulation / "roms" / "wiiu" / "Splatoon" / "meta"
    game_meta.mkdir(parents=True)
    (game_meta / "meta.xml").write_text(GAME_META_XML, encoding="utf-8")

    server_saves = {
        "0005000010101D00": {
            "title_id": "0005000010101D00",
            # The Wii U console cannot name its own uploads server-side.
            "name": "0005000010101D00",
            "system": "WIIU",
        }
    }

    entry = cemu.build_server_only_entries(server_saves, set(), emulation)[0]

    assert entry.display_name == "Splatoon"
    assert entry.game_code == "WIIU_AGMP"


# ──────────────────────────────────────────────────────────────────────────────
# Explicit Cemu folder override
#
# EmuDeck installs Cemu outside the Emulation folder more often than not, so
# every path below is one auto-detection cannot reach.
# ──────────────────────────────────────────────────────────────────────────────


def test_override_points_at_mlc01(tmp_path):
    emulation = tmp_path / "Emulation"
    mlc = tmp_path / "sdcard" / "cemu-install" / "mlc01"
    save_dir = _make_save(mlc)

    entry = next(iter(cemu.scan(emulation, save_dir_override=str(mlc))))
    assert entry.save_path == save_dir


def test_override_points_at_the_cemu_folder(tmp_path):
    """The directory picker lands on the install folder as often as mlc01."""
    emulation = tmp_path / "Emulation"
    install = tmp_path / "Applications" / "Cemu"
    save_dir = _make_save(install / "mlc01")

    entry = next(iter(cemu.scan(emulation, save_dir_override=str(install))))
    assert entry.save_path == save_dir


def test_override_wins_over_a_populated_default_location(tmp_path):
    """Two installs: the override decides which one syncs."""
    emulation = tmp_path / "Emulation"
    _make_save(emulation / "storage" / "cemu" / "mlc01", tidlo="10101d00")

    real_mlc = tmp_path / "sdcard" / "mlc01"
    save_dir = _make_save(real_mlc, tidlo="10143500")

    results = list(cemu.scan(emulation, save_dir_override=str(real_mlc)))
    assert [e.title_id for e in results] == ["0005000010143500"]
    assert results[0].save_path == save_dir


def test_override_follows_settings_xml_in_that_folder(tmp_path):
    """Pointing at an install whose MLC was relocated still finds the saves."""
    emulation = tmp_path / "Emulation"
    install = tmp_path / "Applications" / "Cemu"
    install.mkdir(parents=True)

    real_mlc = tmp_path / "sdcard" / "mlc01"
    save_dir = _make_save(real_mlc)
    (install / "settings.xml").write_text(
        f"<content><mlc_path>{real_mlc}</mlc_path></content>", encoding="utf-8"
    )

    entry = next(iter(cemu.scan(emulation, save_dir_override=str(install))))
    assert entry.save_path == save_dir


def test_override_accepts_an_mlc_with_no_saves_yet(tmp_path):
    """A fresh install has usr/title and no usr/save — downloads still land."""
    emulation = tmp_path / "Emulation"
    mlc = tmp_path / "sdcard" / "mlc01"
    (mlc / "usr" / "title").mkdir(parents=True)

    path = cemu.default_save_path(
        emulation, "0005000010143500", save_dir_override=str(mlc)
    )
    assert path == mlc / "usr" / "save" / "00050000" / "10143500" / "user"


def test_unusable_override_falls_back_to_auto_detection(tmp_path):
    """A stale path must not turn the scan into a silent no-op."""
    emulation = tmp_path / "Emulation"
    mlc = emulation / "storage" / "cemu" / "mlc01"
    save_dir = _make_save(mlc)

    bogus = tmp_path / "gone"
    entry = next(iter(cemu.scan(emulation, save_dir_override=str(bogus))))
    assert entry.save_path == save_dir


def test_override_drives_server_only_download_path(tmp_path):
    emulation = tmp_path / "Emulation"
    mlc = tmp_path / "sdcard" / "mlc01"
    (mlc / "usr" / "save").mkdir(parents=True)

    server_saves = {
        "0005000010101D00": {"title_id": "0005000010101D00", "system": "WIIU"}
    }

    entry = cemu.build_server_only_entries(
        server_saves, set(), emulation, save_dir_override=str(mlc)
    )[0]
    assert entry.save_path == (
        mlc / "usr" / "save" / "00050000" / "10101d00" / "user"
    )


def test_scan_all_passes_the_override_through(tmp_path):
    """The aggregator is what the UI calls — the override has to survive it."""
    from scanner import scan_all

    emulation = tmp_path / "Emulation"
    emulation.mkdir()
    mlc = tmp_path / "sdcard" / "mlc01"
    save_dir = _make_save(mlc)

    results = scan_all(
        str(emulation), save_dir_overrides={"Cemu": str(mlc)}
    )
    wiiu = [e for e in results if e.system == "WIIU"]
    assert [e.save_path for e in wiiu] == [save_dir]
