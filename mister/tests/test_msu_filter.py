"""The catalog's system filter gets a packs-only stop after a system that has
MSU packs, and pack rows say what kind they are."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for candidate in (ROOT, ROOT / "mister"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from gamesync.app import (  # noqa: E402
    App,
    Row,
    catalog_systems,
    msu_filter_base,
    msu_filter_label,
    row_pack_kind,
)
from shared.mister_install import DiscGroup  # noqa: E402


def _group(system, name, kind=None):
    row = {"rom_id": name, "system": system, "name": name,
           "filename": name + (".zip" if kind else ".sfc"), "size": 1}
    if kind:
        row["is_bundle"] = True
        row["bundle_kind"] = kind
    return DiscGroup(system, name, [row])


def _rows():
    return [
        Row("SNES", "Mario", "512 KB", "installed", ref=_group("SNES", "Mario")),
        Row("SNES", "ActRaiser (USA) (MSU1)", "MSU-1  214 MB", "not installed",
            ref=_group("SNES", "ActRaiser (USA) (MSU1)", "msu1")),
        Row("MD", "Sonic 2 (MSU-MD)", "MSU-MD  590 MB", "not installed",
            ref=_group("MD", "Sonic 2 (MSU-MD)", "msu-md")),
        Row("GBA", "Zelda", "8 MB", "not installed", ref=_group("GBA", "Zelda")),
    ]


def make_app(rows):
    app = App.__new__(App)
    app.tab = 1
    app.selected = 0
    app.scroll = 0
    app.system_filter = 0
    app.system_filter_name = "All"
    app.search = ""
    app.all_rows = {1: rows}
    app._data_version = 0
    app._rows_key = None
    app._rows_cache = []
    app.tab_systems = {1: catalog_systems(rows)}
    app._resync_system_filter()
    return app


def test_packs_only_stop_follows_each_system_that_has_packs():
    assert catalog_systems(_rows()) == [
        "All", "GBA", "MD", "MD: MSU packs", "SNES", "SNES: MSU packs",
    ]
    assert msu_filter_base("SNES: MSU packs") == "SNES"
    assert msu_filter_base("SNES") is None
    assert msu_filter_label("SNES") == "SNES: MSU packs"


def test_packs_only_view_hides_plain_roms():
    app = make_app(_rows())
    app.system_filter = app.systems.index("SNES")
    assert [r.name for r in app.rows()] == ["Mario", "ActRaiser (USA) (MSU1)"]

    app.system_filter = app.systems.index("SNES: MSU packs")
    app._rows_key = None
    assert [r.name for r in app.rows()] == ["ActRaiser (USA) (MSU1)"]


def test_row_pack_kind_only_for_bundles_with_a_kind():
    rows = _rows()
    assert row_pack_kind(rows[0]) == ""
    assert row_pack_kind(rows[1]) == "msu1"
    assert row_pack_kind(rows[2]) == "msu-md"
    assert row_pack_kind(Row("SNES", "x", "", "")) == ""


def test_installed_plain_rom_does_not_tick_the_pack():
    """ActRaiser (USA).sfc on the card is not ActRaiser (USA) (MSU1)."""
    from gamesync.app import InstalledGame, _normalize

    app = App.__new__(App)
    app.installed_entries = [
        InstalledGame("SNES", "ActRaiser (USA)", "/g/SNES/ActRaiser (USA).sfc", "SD", "SNES"),
        InstalledGame("SNES", "Mario (USA) (MSU1)", "/g/SNES/Mario (USA) (MSU1)/Mario (USA) (MSU1).sfc",
                      "SD", "SNES", is_pack=True),
        InstalledGame("SEGACD", "Sonic 2 (World) (MSU-MD)", "/g/MegaCD/Sonic 2 (World) (MSU-MD)",
                      "SD", "MegaCD", is_pack=True),
    ]
    app.installed_ids = {(e.system, _normalize(e.name), e.is_pack) for e in app.installed_entries}

    assert app.game_installed(_group("SNES", "ActRaiser (USA)"))
    assert not app.game_installed(_group("SNES", "ActRaiser (USA) (MSU1)", "msu1"))
    assert app.game_installed(_group("SNES", "Mario (USA) (MSU1)", "msu1"))
    # The plain ROM is not installed just because its pack is.
    assert not app.game_installed(_group("SNES", "Mario (USA)"))
    # An MSU-MD pack is a MegaCD install, found under that core.
    assert app.game_installed(_group("MD", "Sonic 2 (World) (MSU-MD)", "msu-md"))


def test_scan_flags_pack_folders():
    from shared.mister_scan import list_installed_games

    class Provider:
        tree = {
            "/g/SNES": ["ActRaiser (USA).sfc", "Mario (USA) (MSU1)", "Shelf"],
            "/g/SNES/Mario (USA) (MSU1)": ["Mario (USA) (MSU1).sfc", "Mario (USA) (MSU1).msu",
                                           "Mario (USA) (MSU1)-1.pcm"],
            "/g/SNES/Shelf": ["Zelda (USA).sfc"],
            "/g/MegaCD": ["Sonic 2 (World) (MSU-MD)", "Lunar (USA)"],
            "/g/MegaCD/Sonic 2 (World) (MSU-MD)": ["cart.rom", "Sonic 2 (World) (MSU-MD).cue",
                                                   "Sonic 2 (World) (MSU-MD).bin"],
            "/g/MegaCD/Lunar (USA)": ["Lunar (USA).chd"],
        }

        def listdir(self, path):
            return list(self.tree.get(path.rstrip("/"), []))

        def is_dir(self, path):
            return path.rstrip("/") in self.tree

    snes = {r.name: r.is_pack for r in list_installed_games(Provider(), "/g/SNES", "SNES", "SNES")}
    assert snes == {"ActRaiser (USA)": False, "Mario (USA) (MSU1)": True, "Zelda (USA)": False}
    cd = {r.name: r.is_pack for r in list_installed_games(Provider(), "/g/MegaCD", "MegaCD", "SEGACD")}
    assert cd == {"Sonic 2 (World) (MSU-MD)": True, "Lunar (USA)": False}
