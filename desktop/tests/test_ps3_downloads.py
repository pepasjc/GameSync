import os
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

import config
import sync_engine as se
from tabs.sync_tab import SyncTab


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
