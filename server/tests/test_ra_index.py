import hashlib

import pytest

from app.services import ra_index, rom_db
from shared.ra_api import RaLibrary


ROM_BYTES = b"\x42" * 0x8000          # clean 32 KB SNES ROM, no copier header
OTHER_BYTES = b"\x77" * 0x8000
ROM_MD5 = hashlib.md5(ROM_BYTES).hexdigest()


class _Entry:
    """The bits of rom_scanner.RomEntry that ra_index reads."""

    def __init__(self, path, system="SNES", is_bundle=False, rom_id="r1"):
        self.path = str(path)
        self.system = system
        self.is_bundle = is_bundle
        self.rom_id = rom_id


@pytest.fixture
def db(tmp_path, monkeypatch):
    rom_db.init_db(tmp_path)
    ra_index.clear()
    yield tmp_path


@pytest.fixture
def library(monkeypatch):
    """RA knows ROM_BYTES as game 42 with 7 achievements."""
    lib = RaLibrary(3, {ROM_MD5: 42}, {42: "Test Game"}, achievements={42: 7})
    calls = []

    def fake_fetch(console_id, cache_dir=None, api_key="", username="", **kw):
        calls.append(console_id)
        return lib if console_id == 3 else RaLibrary(console_id)

    monkeypatch.setattr(ra_index, "fetch_library", fake_fetch)
    return calls


def _rom(tmp_path, name="game.sfc", data=ROM_BYTES):
    p = tmp_path / name
    p.write_bytes(data)
    return p


def test_refresh_indexes_and_lookup_reports_the_game(db, library, tmp_path):
    rom = _rom(tmp_path)
    result = ra_index.refresh([_Entry(rom)], tmp_path / "cache")
    assert result["hashed"] == 1 and result["known"] == 1

    found = ra_index.lookup([str(rom)])
    assert found[str(rom)] == {
        "ra_game_id": 42,
        "ra_achievements": 7,
        "ra_title": "Test Game",
        "ra_hash": ROM_MD5,
    }


def test_rom_unknown_to_ra_is_cached_but_not_reported(db, library, tmp_path):
    rom = _rom(tmp_path, "unknown.sfc", OTHER_BYTES)
    result = ra_index.refresh([_Entry(rom)], tmp_path / "cache")
    assert result["hashed"] == 1 and result["known"] == 0
    assert ra_index.lookup([str(rom)]) == {}
    # Cached, so a second pass does no work.
    assert ra_index.refresh([_Entry(rom)], tmp_path / "cache")["hashed"] == 0


def test_unchanged_rom_is_not_rehashed(db, library, tmp_path):
    rom = _rom(tmp_path)
    entries = [_Entry(rom)]
    ra_index.refresh(entries, tmp_path / "cache")
    again = ra_index.refresh(entries, tmp_path / "cache")
    assert again["hashed"] == 0
    assert again["skipped"] == 1


def test_replaced_rom_is_rehashed(db, library, tmp_path):
    rom = _rom(tmp_path, "game.sfc", OTHER_BYTES)
    entries = [_Entry(rom)]
    ra_index.refresh(entries, tmp_path / "cache")
    assert ra_index.lookup([str(rom)]) == {}

    rom.write_bytes(ROM_BYTES + b"\x00" * 512)   # different size -> stale cache
    rom.write_bytes(ROM_BYTES)
    import os
    os.utime(rom, (0, 0))                        # force a visible mtime change
    assert ra_index.refresh(entries, tmp_path / "cache")["hashed"] == 1
    assert ra_index.lookup([str(rom)])[str(rom)]["ra_game_id"] == 42


def test_disc_systems_and_bundles_are_skipped(db, library, tmp_path):
    cd = _rom(tmp_path, "disc.chd")
    bundle = _rom(tmp_path, "pack.zip")
    result = ra_index.refresh(
        [_Entry(cd, system="PS1"), _Entry(bundle, is_bundle=True)],
        tmp_path / "cache",
    )
    assert result["hashed"] == 0
    assert library == []          # no library fetched when there is nothing to hash


def test_missing_file_does_not_raise(db, library, tmp_path):
    result = ra_index.refresh([_Entry(tmp_path / "gone.sfc")], tmp_path / "cache")
    assert result["hashed"] == 0


def test_rows_for_departed_roms_are_dropped(db, library, tmp_path):
    rom = _rom(tmp_path)
    ra_index.refresh([_Entry(rom)], tmp_path / "cache")
    assert ra_index.stats()["hashed"] == 1

    result = ra_index.refresh([], tmp_path / "cache")
    assert result["removed"] == 1
    assert ra_index.stats()["hashed"] == 0


def test_missing_achievement_counts_are_marked_unknown(db, tmp_path, monkeypatch):
    """Public endpoints report no counts; that must not read as 'zero'."""
    lib = RaLibrary(3, {ROM_MD5: 42}, {42: "Test Game"})      # achievements=None
    monkeypatch.setattr(ra_index, "fetch_library",
                        lambda console_id, **kw: lib if console_id == 3 else RaLibrary(console_id))
    rom = _rom(tmp_path)
    ra_index.refresh([_Entry(rom)], tmp_path / "cache")
    assert ra_index.lookup([str(rom)])[str(rom)]["ra_achievements"] == ra_index.ACHIEVEMENTS_UNKNOWN


def test_offline_library_leaves_catalog_usable(db, tmp_path, monkeypatch):
    def boom(console_id, **kw):
        raise OSError("no network")

    monkeypatch.setattr(ra_index, "fetch_library", boom)
    rom = _rom(tmp_path)
    result = ra_index.refresh([_Entry(rom)], tmp_path / "cache")
    assert result["hashed"] == 1 and result["known"] == 0
    assert ra_index.lookup([str(rom)]) == {}


def test_annotate_merges_into_payloads(db, library, tmp_path):
    rom = _rom(tmp_path)
    other = _rom(tmp_path, "other.sfc", OTHER_BYTES)
    entries = [_Entry(rom, rom_id="a"), _Entry(other, rom_id="b")]
    ra_index.refresh(entries, tmp_path / "cache")

    payloads = [{"rom_id": "a"}, {"rom_id": "b"}]
    ra_index.annotate(entries, payloads)
    assert payloads[0]["ra_game_id"] == 42
    assert payloads[0]["ra_achievements"] == 7
    assert "ra_game_id" not in payloads[1]


def test_annotate_is_a_noop_on_an_empty_index(db, tmp_path):
    payloads = [{"rom_id": "a"}]
    ra_index.annotate([_Entry(tmp_path / "x.sfc")], payloads)
    assert payloads == [{"rom_id": "a"}]


def test_lookup_handles_more_paths_than_sqlite_variable_limit(db, library, tmp_path):
    rom = _rom(tmp_path)
    ra_index.refresh([_Entry(rom)], tmp_path / "cache")
    paths = [f"/nope/{i}.sfc" for i in range(1200)] + [str(rom)]
    assert list(ra_index.lookup(paths)) == [str(rom)]
