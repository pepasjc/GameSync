"""A save for a slug-keyed system follows the installed file's exact name.

Seen live: the server files "Famicom Detective Club Part II … [T-En …].sfc"
under the original title's slug, SNES_famicom_tantei_club_…. Installing that
ROM for its server-only save and then downloading the save wrote
"Famicom Tantei Club Part Ii Ushiro Ni Tatsu Shoujo Japan.sav" - the server's
de-slugged name - which the core never looks for. The catalogue knows both
names, so an exact file-name hit bridges them; a loose match on a slug
system is still refused.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for candidate in (ROOT, ROOT / "mister"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from gamesync import sync as gssync  # noqa: E402
from shared.mister_scan import LocalProvider  # noqa: E402

TITLE = "SNES_famicom_tantei_club_part_ii_ushiro_ni_tatsu_shoujo_japan"
PATCHED = ("Famicom Detective Club Part II (Japan) (NP) "
           "[T-En by Demiforce v1.00] [n]")
CATALOG = [{"title_id": TITLE, "system": "SNES", "filename": PATCHED + ".sfc",
            "name": "Famicom Tantei Club Part II (Japan) (NP)"},
           {"title_id": "SNES_chrono_trigger_usa", "system": "SNES",
            "filename": "Chrono Trigger (USA).sfc", "name": "Chrono Trigger"}]


def make_engine(tmp_path):
    engine = gssync.SyncEngine.__new__(gssync.SyncEngine)
    engine.client = object()
    engine.provider = LocalProvider()
    engine.net = None
    engine.catalog_rows = lambda system: CATALOG if system == "SNES" else []
    return engine


def test_exact_file_name_resolves_to_the_servers_key(tmp_path):
    engine = make_engine(tmp_path)
    assert engine._catalog_lookup("SNES", PATCHED) == TITLE
    assert engine._catalog_lookup("SNES", PATCHED.upper()) == TITLE
    # A regional near miss on a slug system is not bridged.
    assert engine._catalog_lookup("SNES", "Chrono Trigger (Europe)") is None
    assert engine._catalog_lookup("SNES", "Chrono Trigger") is None


def test_server_only_save_is_named_after_the_installed_rom(tmp_path, monkeypatch):
    root = str(tmp_path).replace("\\", "/")
    games = tmp_path / "games" / "SNES"
    games.mkdir(parents=True)
    (games / (PATCHED + ".sfc")).write_bytes(b"\x00" * 16)
    (tmp_path / "saves" / "SNES").mkdir(parents=True)
    monkeypatch.setattr(gssync, "build_save_path",
                        lambda provider, system, title_id, name, **kw:
                        _build(provider, system, title_id, name, root, **kw))
    engine = make_engine(tmp_path)
    ghost = gssync.SaveEntry(
        TITLE, "SNES", "Famicom Tantei Club Part Ii Ushiro Ni Tatsu Shoujo "
        "Japan", "", 0, 0.0, "", gssync.SERVER_ONLY, exists=False)

    assert engine.download_target(ghost) == \
        "%s/saves/SNES/%s.sav" % (root, PATCHED)


def _build(provider, system, title_id, name, root, catalog_lookup=None):
    from shared.mister_scan import build_save_path

    return build_save_path(provider, system, title_id, name,
                           saves_root=root + "/saves",
                           games_roots=[root + "/games"],
                           catalog_lookup=catalog_lookup)
