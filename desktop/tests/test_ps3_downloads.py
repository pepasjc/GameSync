import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

import config
import tabs.server_saves_tab as server_saves_tab
import sync_engine as se
from saroo_format import _NativeSave, _build_native_saturn
from tabs.server_saves_tab import _raw_download_defaults
from tabs.sync_tab import (
    SyncTab,
    _resolve_retroarch_download_path,
    _resolve_retroarch_saturn_download_path,
)


# conftest.py stubs PyQt6 with MagicMock when the real package isn't
# installed so that import-time references in tabs/*.py succeed.  That makes
# pytest.importorskip above a no-op (the stub is already in sys.modules), so
# tests that actually instantiate Qt widgets like SyncTab have to self-skip.
_HAS_REAL_PYQT6 = (
    type(sys.modules.get("PyQt6.QtWidgets", None)).__name__ != "MagicMock"
)

requires_real_qt = pytest.mark.skipif(
    not _HAS_REAL_PYQT6,
    reason="Needs real PyQt6 — conftest stubs it when the package isn't installed",
)


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class DummyProfilesTab:
    def __init__(self, profiles):
        self._profiles = profiles

    def get_profiles(self):
        return self._profiles


def test_download_ps3_save_extracts_bundle(monkeypatch, tmp_path):
    title_id = "BLUS30464-AUTOSAVE-01"
    source = tmp_path / "server-copy"
    source.mkdir()
    (source / "PARAM.SFO").write_bytes(b"param")
    (source / "GAME.DAT").write_bytes(b"game")
    calls = []

    class GetResponse:
        content = se._create_dir_bundle(title_id, source)

        def raise_for_status(self):
            return None

    def fake_get(url, headers=None, timeout=None):
        calls.append(url)
        return GetResponse()

    monkeypatch.setattr(config.requests, "get", fake_get)

    dest = tmp_path / title_id
    config.download_ps3_save(title_id, dest)

    assert calls == [f"{config.get_base_url()}/api/v1/saves/{title_id}"]
    assert dest.is_dir()
    assert (dest / "PARAM.SFO").read_bytes() == b"param"
    assert (dest / "GAME.DAT").read_bytes() == b"game"


@requires_real_qt
def test_sync_tab_resolves_emudeck_ps3_downloads_to_rpcs3_save_dirs(qt_app, tmp_path):
    saves_root = tmp_path / "Emulation" / "saves"
    saves_root.mkdir(parents=True)
    profile = {
        "name": "EmuDeck",
        "device_type": "EmuDeck",
        "path": str(tmp_path / "Emulation"),
        "save_folder": str(saves_root),
        "systems": [
            {
                "system": "PS3",
                "enabled": True,
                "save_ext": ".srm",
                "save_folder": "",
            }
        ],
    }
    tab = SyncTab(DummyProfilesTab([profile]))
    tab.profile_combo.setCurrentIndex(0)
    status = SimpleNamespace(
        save=SimpleNamespace(
            system="PS3",
            game_name="BLUS30464-AUTOSAVE-01",
            title_id="BLUS30464-AUTOSAVE-01",
        )
    )

    resolved = tab._resolve_download_path(status)

    assert resolved == saves_root / "rpcs3" / "saves" / "BLUS30464-AUTOSAVE-01"


def test_sync_tab_resolves_retroarch_saturn_downloads_to_yabause_root(qt_app, tmp_path):
    saves_root = tmp_path / "retroarch" / "saves"
    saves_root.mkdir(parents=True)
    profile = {
        "name": "RetroArch",
        "device_type": "RetroArch",
        "path": str(tmp_path / "retroarch" / "roms"),
        "save_folder": str(saves_root),
        "systems": [
            {
                "system": "SAT",
                "enabled": True,
                "save_ext": ".srm",
                "save_folder": "",
            }
        ],
    }
    resolved = _resolve_retroarch_saturn_download_path(
        profile, saves_root, "Panzer Dragoon Saga (USA)"
    )

    assert resolved == saves_root / "Panzer Dragoon Saga (USA).srm"


def test_sync_tab_resolves_retroarch_saturn_downloads_to_mednafen_root_when_no_core_dir(
    qt_app, tmp_path
):
    saves_root = tmp_path / "retroarch" / "saves"
    saves_root.mkdir(parents=True)
    profile = {
        "name": "RetroArch",
        "device_type": "RetroArch",
        "path": str(tmp_path / "retroarch" / "roms"),
        "save_folder": str(saves_root),
        "systems": [
            {
                "system": "SAT",
                "enabled": True,
                "save_ext": ".bkr",
                "save_folder": "",
            }
        ],
    }
    resolved = _resolve_retroarch_saturn_download_path(
        profile, saves_root, "Panzer Dragoon Saga (USA)"
    )

    assert resolved == saves_root / "Panzer Dragoon Saga (USA).bkr"


def test_sync_tab_resolves_retroarch_saturn_downloads_to_yabasanshiro_backup(
    qt_app, tmp_path
):
    saves_root = tmp_path / "retroarch" / "saves"
    saves_root.mkdir(parents=True)
    profile = {
        "name": "RetroArch",
        "device_type": "RetroArch",
        "path": str(tmp_path / "retroarch" / "roms"),
        "save_folder": str(saves_root),
        "systems": [
            {
                "system": "SAT",
                "enabled": True,
                "save_ext": ".bin",
                "save_folder": str(saves_root / "yabasanshiro"),
            }
        ],
    }
    resolved = _resolve_retroarch_saturn_download_path(
        profile, saves_root, "Panzer Dragoon Saga (USA)"
    )

    assert resolved == saves_root / "yabasanshiro" / "backup.bin"


@requires_real_qt
def test_sync_tab_do_sync_finalizes_saroo_server_downloads(monkeypatch, qt_app, tmp_path):
    saroo_root = tmp_path / "saroo"
    mednafen_root = tmp_path / "mednafen"
    saroo_root.mkdir()
    mednafen_root.mkdir()
    profile = {
        "name": "SAROO",
        "device_type": "SAROO",
        "path": str(saroo_root),
        "save_folder": str(mednafen_root),
        "system": "SAT",
        "save_ext": ".bkr",
    }
    tab = SyncTab(DummyProfilesTab([profile]))
    tab.profile_combo.setCurrentIndex(0)

    status = SimpleNamespace(
        status="server_newer",
        save=SimpleNamespace(
            system="SAT",
            game_name="Panzer Dragoon Saga (USA)",
            title_id="SAT_T12705H",
            path=saroo_root / "SS_SAVE.BIN",
            alternate_paths=[],
            save_exists=True,
        ),
    )
    tab._statuses = [status]

    downloads = []
    finalized = []
    updated = []

    monkeypatch.setattr(
        tab,
        "_download_to_paths",
        lambda title_id, paths, base_url, headers, system=None: downloads.append(
            (title_id, paths, system)
        )
        or "server-hash",
    )
    monkeypatch.setattr(
        tab,
        "_finalize_saroo_download",
        lambda title_id, dest_path, profile_arg: finalized.append((title_id, dest_path)),
    )
    monkeypatch.setattr(
        tab,
        "_update_row_status",
        lambda idx, new_status, new_path=None: updated.append((idx, new_status, new_path)),
    )
    monkeypatch.setattr("tabs.sync_tab.QMessageBox.information", lambda *args, **kwargs: None)

    tab._do_sync([0])

    expected_bkr = mednafen_root / "T12705H.bkr"
    assert downloads == [("SAT_T12705H", [expected_bkr], "SAT")]
    assert finalized == [("SAT_T12705H", expected_bkr)]
    assert updated == [(0, "up_to_date", expected_bkr)]


def test_server_saves_tab_download_defaults_saturn_to_bkr(monkeypatch, qt_app):
    default_name, file_filter = _raw_download_defaults("SAT_T-4507G", "SAT")

    assert default_name == "SAT_T-4507G.bkr"
    assert file_filter == "Saturn Saves (*.bkr *.sav *.srm);;All Files (*)"


def test_server_saves_tab_download_defaults_saturn_to_game_name_when_available():
    default_name, file_filter = _raw_download_defaults(
        "SAT_T-4507G",
        "SAT",
        saturn_format="yabause",
        game_name="Grandia (Japan) (Disc 1)",
    )

    assert default_name == "Grandia (Japan) (Disc 1).srm"
    assert file_filter == "Saturn Saves (*.srm *.bkr *.sav *.bin);;All Files (*)"


def test_server_saves_tab_downloads_saturn_as_yabause(monkeypatch, tmp_path):
    canonical = _build_native_saturn(
        [
            _NativeSave(
                name="GRANDIA_001",
                language_code=0,
                comment="Feena's Ho",
                date_code=23797305,
                raw_data=b"grandia-save",
            )
        ]
    )

    target = tmp_path / "SAT_T-4507G.srm"
    monkeypatch.setattr(server_saves_tab, "download_raw_save_bytes", lambda title_id: canonical)
    server_saves_tab._download_saturn_save("SAT_T-4507G", target, "yabause")

    assert target.read_bytes()[0::2] == b"\xFF" * len(canonical)
    assert target.read_bytes()[1::2] == canonical


def test_sync_tab_resolve_download_path_uses_retroarch_system_save_and_rom_overrides(
    qt_app, tmp_path
):
    global_rom_root = tmp_path / "roms"
    gba_rom_root = tmp_path / "gba_roms"
    save_root = tmp_path / "saves"
    gba_save_root = tmp_path / "gba_saves"
    global_rom_root.mkdir()
    gba_rom_root.mkdir()
    save_root.mkdir()
    gba_save_root.mkdir()
    (gba_rom_root / "Advance Wars (USA).gba").write_bytes(b"")

    profile = {
        "name": "RetroArch",
        "device_type": "RetroArch",
        "path": str(global_rom_root),
        "save_folder": str(save_root),
        "systems": [
            {
                "system": "GBA",
                "enabled": True,
                "save_ext": ".srm",
                "save_folder": str(gba_save_root),
                "rom_folder": str(gba_rom_root),
            },
        ],
    }

    resolved = _resolve_retroarch_download_path(
        profile=profile,
        save_root=gba_save_root,
        system="GBA",
        filename_stem="Advance Wars (USA)",
        filename="Advance Wars (USA).srm",
        has_system_override=True,
    )

    assert resolved == gba_save_root / "Advance Wars (USA).srm"


def test_sync_tab_resolve_download_path_uses_selected_retroarch_core_folder():
    profile = {
        "name": "RetroArch",
        "device_type": "RetroArch",
        "path": r"E:\roms",
        "save_folder": r"E:\saves",
        "systems": [
            {
                "system": "GBA",
                "enabled": True,
                "save_ext": ".srm",
                "save_folder": "",
                "rom_folder": "",
                "core": "VBA-M",
            },
        ],
    }

    resolved = _resolve_retroarch_download_path(
        profile=profile,
        save_root=Path(r"E:\saves"),
        system="GBA",
        filename_stem="Advance Wars (USA)",
        filename="Advance Wars (USA).srm",
        has_system_override=False,
        core_name="VBA-M",
    )

    assert resolved == Path(r"E:\saves") / "Advance Wars (USA).srm"


def test_sync_tab_resolve_download_path_uses_selected_shared_retroarch_core_folder():
    profile = {
        "name": "RetroArch",
        "device_type": "RetroArch",
        "path": r"E:\roms",
        "save_folder": r"E:\saves",
        "systems": [
            {
                "system": "SEGACD",
                "enabled": True,
                "save_ext": ".srm",
                "save_folder": "",
                "rom_folder": "",
                "core": "Genesis Plus GX",
            },
        ],
    }

    resolved = _resolve_retroarch_download_path(
        profile=profile,
        save_root=Path(r"E:\saves"),
        system="SEGACD",
        filename_stem="Sonic CD (USA)",
        filename="Sonic CD (USA).srm",
        has_system_override=False,
        core_name="Genesis Plus GX",
    )

    assert resolved == Path(r"E:\saves") / "Sonic CD (USA).srm"


@requires_real_qt
def test_sync_tab_download_to_paths_copies_ps3_directories(monkeypatch, qt_app, tmp_path):
    tab = SyncTab(DummyProfilesTab([]))
    primary = tmp_path / "primary"
    mirror = tmp_path / "mirror"

    def fake_download_save(title_id, path, base_url, headers, system=None):
        path.mkdir(parents=True, exist_ok=True)
        (path / "PARAM.SFO").write_bytes(b"param")
        nested = path / "USRDIR"
        nested.mkdir()
        (nested / "SAVE.DAT").write_bytes(b"payload")
        return "server-ps3-hash"

    monkeypatch.setattr(se, "download_save", fake_download_save)

    server_hash = tab._download_to_paths(
        "BLUS30464-AUTOSAVE-01",
        [primary, mirror],
        "http://example",
        {"X-API-Key": "x"},
        system="PS3",
    )

    assert server_hash == "server-ps3-hash"
    assert mirror.is_dir()
    assert (mirror / "PARAM.SFO").read_bytes() == b"param"
    assert (mirror / "USRDIR" / "SAVE.DAT").read_bytes() == b"payload"
