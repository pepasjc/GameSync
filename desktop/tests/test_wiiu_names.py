"""Desktop Wii U naming: read meta.xml locally, teach the server what we found.

A Wii U save's 16-hex title id carries no product code, so nothing the server
knows can name it.  The desktop client has no scanner, but it does run on the
machine where Cemu lives — so it reads the title's meta.xml and both displays
and uploads the name.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import config


GAME_META_XML = """<?xml version="1.0" encoding="utf-8"?>
<menu>
  <title_id type="hexBinary" length="8">0005000010101d00</title_id>
  <product_code type="string" length="32">WUP-P-AGMP</product_code>
  <longname_en type="string" length="512">Splatoon</longname_en>
</menu>
"""


@pytest.fixture(autouse=True)
def clear_index_cache():
    config._wiiu_name_index = None
    yield
    config._wiiu_name_index = None


@pytest.fixture
def cemu_dir(tmp_path, monkeypatch):
    """A Cemu folder holding one loose game dump, with HOME kept out of it."""
    cemu = tmp_path / "Cemu"
    meta = cemu / "games" / "Splatoon" / "meta"
    meta.mkdir(parents=True)
    (meta / "meta.xml").write_text(GAME_META_XML, encoding="utf-8")

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setattr(config, "load_config", lambda: {"cemu_dir": str(cemu)})
    return cemu


def test_detect_console_type_separates_wiiu_from_3ds():
    assert config.detect_console_type("0005000010143500") == "WIIU"
    assert config.detect_console_type("0004000000055D00") == "3DS"


def test_index_reads_loose_game_dumps(cemu_dir):
    index = config.wiiu_name_index()
    assert index["0005000010101D00"] == ("Splatoon", "WIIU_AGMP")


def test_index_reads_installed_mlc_titles(tmp_path, monkeypatch):
    cemu = tmp_path / "Cemu"
    meta = cemu / "mlc01" / "usr" / "title" / "00050000" / "10101d00" / "meta"
    meta.mkdir(parents=True)
    (meta / "meta.xml").write_text(GAME_META_XML, encoding="utf-8")

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setattr(config, "load_config", lambda: {"cemu_dir": str(cemu)})

    assert config.wiiu_name_index()["0005000010101D00"][0] == "Splatoon"


def test_resolve_names_fills_display_and_pushes_hints(cemu_dir, monkeypatch):
    pushed: dict = {}

    def fake_push(codes, names):
        pushed["codes"] = codes
        pushed["names"] = names
        return True

    monkeypatch.setattr(config, "push_name_hints", fake_push)

    saves = [
        {
            "title_id": "0005000010101D00",
            "console_type": "WIIU",
            "game_name": "0005000010101D00",
        }
    ]
    result = config.resolve_wiiu_names(saves)

    assert result[0]["game_name"] == "Splatoon"
    assert pushed["names"] == {"0005000010101D00": "Splatoon"}
    assert pushed["codes"] == {"0005000010101D00": "WIIU_AGMP"}


def test_resolve_names_leaves_named_saves_alone(cemu_dir, monkeypatch):
    called = {"push": False}
    monkeypatch.setattr(
        config, "push_name_hints", lambda c, n: called.__setitem__("push", True)
    )

    saves = [
        {
            "title_id": "0005000010101D00",
            "console_type": "WIIU",
            "game_name": "Server Knows This One",
        }
    ]
    assert config.resolve_wiiu_names(saves)[0]["game_name"] == "Server Knows This One"
    assert called["push"] is False


def test_resolve_names_ignores_unknown_titles(cemu_dir, monkeypatch):
    monkeypatch.setattr(config, "push_name_hints", lambda c, n: True)

    saves = [
        {
            "title_id": "000500001DEADB00",
            "console_type": "WIIU",
            "game_name": "000500001DEADB00",
        }
    ]
    assert config.resolve_wiiu_names(saves)[0]["game_name"] == "000500001DEADB00"
