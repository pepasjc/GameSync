"""A game played from a real disc and from its ISO has two memory cards.

The PSX core names a card after the ROM file, but when booting a real CD all
it has is the disc serial, so the same game ends up with ``Dino Crisis 2
(USA).sav`` *and* ``SLUS_012.79.sav``. Both key to the same server slot. They
must both stay visible, the disc card clearly marked, and after one is synced
the other should carry the same progress - unless it has moved on too, which
is a fork the user has to settle.
"""

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for candidate in (ROOT, ROOT / "mister"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import pytest  # noqa: E402

from gamesync import config as gsconfig  # noqa: E402
from gamesync import sync as gssync  # noqa: E402
from gamesync.hashcache import HashCache  # noqa: E402
from shared.mister_scan import LocalProvider  # noqa: E402


def ps1_card(entries=()):
    card = bytearray(b"\x00" * (128 * 1024))
    card[0:2] = b"MC"
    for index, (name, fill) in enumerate(entries):
        frame = (index + 1) * 128
        card[frame] = 0x51
        encoded = name.encode("ascii")
        card[frame + 0x0A:frame + 0x0A + len(encoded)] = encoded
        block = (index + 1) * 8192
        card[block:block + 16] = bytes([fill]) * 16
    return bytes(card)


class FakeClient:
    def __init__(self):
        self.uploaded = {}

    def upload_save(self, title_id, data, system="", console_id="",
                    game_name=""):
        self.uploaded[title_id] = (data, game_name)

    def download_save(self, title_id, system=""):
        return self.uploaded.get(title_id, (None, ""))[0]


def make_engine(tmp_path, monkeypatch, cards):
    """An engine over real files in tmp_path, with no config or state I/O."""
    monkeypatch.setattr(gsconfig, "save_state", lambda state: None)
    root = str(tmp_path).replace("\\", "/")
    engine = gssync.SyncEngine.__new__(gssync.SyncEngine)
    engine.config = None
    engine.provider = LocalProvider()
    engine.client = FakeClient()
    engine.state = {}
    engine.cache = HashCache(path=root + "/hash_cache.json")
    engine.net = None
    engine.entries = []
    engine._titles_cache = {}
    for stem, data in cards.items():
        path = "%s/%s.sav" % (root, stem)
        with open(path, "wb") as handle:
            handle.write(data)
        identity = gssync.mister_saves.resolve_save_identity("PS1", data)
        entry = gssync.SaveEntry(
            "SLUS01279", "PS1", stem, path, len(data), 0.0,
            hashlib.sha256(data).hexdigest(), gssync.LOCAL,
            exists=not identity.is_blank, is_blank=identity.is_blank,
            is_cd=gssync.mister_saves.ps1_serial_from_filename(stem) is not None)
        engine.entries.append(entry)
    engine._label_entries()
    return engine


ISO = "Dino Crisis 2 (USA)"
CD = "SLUS_012.79"
SAVE_V1 = [("BASLUS-01279-DINO200", 0x11)]
SAVE_V2 = [("BASLUS-01279-DINO200", 0x22)]


def by_stem(engine, stem):
    return next(e for e in engine.entries if e.name == stem)


def read(entry):
    with open(entry.path, "rb") as handle:
        return handle.read()


def test_disc_card_is_labelled_with_the_iso_cards_name(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch,
                         {ISO: ps1_card(SAVE_V1), CD: ps1_card(SAVE_V1)})
    assert by_stem(engine, ISO).display == "Dino Crisis 2 (USA)"
    assert by_stem(engine, CD).display == "Dino Crisis 2 (USA) [CD]"
    assert by_stem(engine, CD).is_cd and not by_stem(engine, ISO).is_cd
    assert engine.siblings(by_stem(engine, ISO)) == [by_stem(engine, CD)]


def test_disc_card_alone_is_named_from_the_server(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, {CD: ps1_card(SAVE_V1)})
    engine._titles_cache = {"SLUS01279": {"name": "Dino Crisis 2 (USA)"}}
    engine._label_entries()
    assert by_stem(engine, CD).display == "Dino Crisis 2 (USA) [CD]"


def test_uploading_the_disc_card_mirrors_into_an_unchanged_iso_card(
        tmp_path, monkeypatch):
    """Played on the real disc; the ISO card was last synced at v1."""
    engine = make_engine(tmp_path, monkeypatch,
                         {ISO: ps1_card(SAVE_V1), CD: ps1_card(SAVE_V2)})
    iso, cd = by_stem(engine, ISO), by_stem(engine, CD)
    engine.state["SLUS01279"] = iso.hash        # ISO synced before
    cd.status = gssync.UPLOAD

    assert engine.sync_entry(cd) is True
    assert engine.client.uploaded["SLUS01279"][1] == ""   # no "SLUS_012.79" name
    assert read(iso) == ps1_card(SAVE_V2)
    assert iso.status == gssync.SYNCED
    assert iso.hash == cd.hash
    assert "mirrored from the CD card" in iso.message


def test_blank_iso_card_receives_the_disc_cards_save(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch,
                         {ISO: ps1_card(), CD: ps1_card(SAVE_V2)})
    iso, cd = by_stem(engine, ISO), by_stem(engine, CD)
    assert iso.is_blank
    cd.status = gssync.UPLOAD
    engine.sync_entry(cd)
    assert read(iso) == ps1_card(SAVE_V2)
    assert iso.status == gssync.SYNCED and not iso.is_blank


def test_a_card_that_also_changed_is_a_conflict_not_overwritten(
        tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch,
                         {ISO: ps1_card([("BASLUS-01279-DINO200", 0x33)]),
                          CD: ps1_card(SAVE_V2)})
    iso, cd = by_stem(engine, ISO), by_stem(engine, CD)
    engine.state["SLUS01279"] = hashlib.sha256(ps1_card(SAVE_V1)).hexdigest()
    cd.status = gssync.UPLOAD

    engine.sync_entry(cd)
    assert read(iso) != ps1_card(SAVE_V2)          # untouched
    assert iso.status == gssync.CONFLICT
    assert iso.message == "also changed on the ISO card"


def test_a_card_holding_other_games_saves_is_not_mirrored_over(
        tmp_path, monkeypatch):
    """The ISO card is unchanged, but it is a shared card: mirroring would
    delete Tony Hawk's save. Left for the user, as a conflict."""
    shared = ps1_card(SAVE_V1 + [("BASLUS-01066CI^AG01", 0x44)])
    engine = make_engine(tmp_path, monkeypatch,
                         {ISO: shared, CD: ps1_card(SAVE_V2)})
    iso, cd = by_stem(engine, ISO), by_stem(engine, CD)
    engine.state["SLUS01279"] = iso.hash
    cd.status = gssync.UPLOAD

    engine.sync_entry(cd)
    assert read(iso) == shared
    assert iso.status == gssync.CONFLICT
    assert "1 save(s) the other does not" in iso.message


def test_housekeeping_only_sibling_is_simply_marked_synced(
        tmp_path, monkeypatch):
    frame63 = bytearray(ps1_card(SAVE_V2))
    frame63[8064:8066] = b"MC"
    engine = make_engine(tmp_path, monkeypatch,
                         {ISO: bytes(frame63), CD: ps1_card(SAVE_V2)})
    iso, cd = by_stem(engine, ISO), by_stem(engine, CD)
    cd.status = gssync.UPLOAD
    engine.sync_entry(cd)
    assert read(iso) == bytes(frame63)
    assert iso.status == gssync.SYNCED


def test_download_also_mirrors(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch,
                         {ISO: ps1_card(SAVE_V1), CD: ps1_card(SAVE_V1)})
    iso, cd = by_stem(engine, ISO), by_stem(engine, CD)
    engine.state["SLUS01279"] = iso.hash
    engine.client.uploaded["SLUS01279"] = (ps1_card(SAVE_V2), "")
    iso.status = gssync.DOWNLOAD

    assert engine.sync_entry(iso) is True
    assert read(iso) == ps1_card(SAVE_V2)
    assert read(cd) == ps1_card(SAVE_V2)
    assert cd.status == gssync.SYNCED


def test_sync_plan_asks_about_each_slot_once(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch,
                         {ISO: ps1_card(SAVE_V1), CD: ps1_card(SAVE_V2)})
    by_stem(engine, CD).mtime = 200.0
    by_stem(engine, ISO).mtime = 100.0
    seen = {}

    class PlanClient(FakeClient):
        def sync_plan(self, titles, console_id="", platforms=None):
            seen["titles"] = titles
            return {}

    engine.client = PlanClient()
    engine._recheck_card_systems = lambda: None
    engine._settle_housekeeping_differences = lambda: None
    engine.fetch_plan()
    assert [t["title_id"] for t in seen["titles"]] == ["SLUS01279"]
    # The card touched most recently is the one described.
    assert seen["titles"][0]["save_hash"] == by_stem(engine, CD).hash


@pytest.mark.parametrize("stem,is_cd", [
    ("SLUS_012.79", True), ("SLUS-01279", True), ("SLUS01279", True),
    ("Dino Crisis 2 (USA)", False), ("PSX", False),
])
def test_disc_card_detection(stem, is_cd):
    from shared.mister_saves import ps1_serial_from_filename
    assert (ps1_serial_from_filename(stem) is not None) is is_cd


def test_server_only_row_still_guards_the_card_at_its_destination(tmp_path, monkeypatch):
    """A "server only" row carries no path, but the file a download would be
    written to may already be a card the scan keyed under another serial.
    The shared-card guard must run against that destination, not be skipped
    because the row itself says nothing is here."""
    engine = make_engine(tmp_path, monkeypatch, {ISO: ps1_card(SAVE_V1)})
    existing = by_stem(engine, ISO)
    # Server: a card for the same slot that lacks this game's save entirely.
    engine.client.uploaded["SLUS01279"] = (ps1_card([("BASLUS-99999-OTHER", 0x33)]), "")
    monkeypatch.setattr(gssync, "build_save_path",
                        lambda *a, **k: existing.path)
    engine._catalog_lookup = None
    ghost = gssync.SaveEntry(
        "SLUS01279", "PS1", ISO, "", 0, 0.0, "", gssync.SERVER_ONLY,
        exists=False, is_blank=False)

    with pytest.raises(RuntimeError, match="would delete yours"):
        engine._download(ghost)
    assert read(existing) == ps1_card(SAVE_V1)
    assert engine.download_target(ghost) == existing.path
