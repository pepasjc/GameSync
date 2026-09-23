import hashlib
from pathlib import Path

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
        "ra_match": ra_index.MATCH_HASH,
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


def test_disc_systems_are_never_hashed(db, library, tmp_path):
    """A CHD is never opened: disc systems go down the title path instead."""
    cd = _rom(tmp_path, "disc.chd")
    result = ra_index.refresh([_Entry(cd, system="PS1")], tmp_path / "cache")
    assert result["hashed"] == 0
    # It was considered, just not by hashing.
    assert result["titled"] == 0          # the stub library has no PS1 titles


def test_bundles_are_skipped_entirely(db, library, tmp_path):
    bundle = _rom(tmp_path, "pack.zip")
    result = ra_index.refresh([_Entry(bundle, is_bundle=True)], tmp_path / "cache")
    assert result["hashed"] == 0
    assert library == []          # no library fetched when there is nothing to do


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


# --- relative catalog paths ---------------------------------------------------
#
# The catalog stores `path` relative to SYNC_ROM_DIR ("snes/game.sfc"), not
# absolute.  Resolving it against the wrong root silently skips every ROM:
# stat() fails, _needs_hash returns None, and the pass reports "nothing to do"
# on a full library.


def test_relative_catalog_path_is_resolved_against_the_rom_dir(db, library, tmp_path):
    rom_dir = tmp_path / "roms"
    (rom_dir / "snes").mkdir(parents=True)
    (rom_dir / "snes" / "game.sfc").write_bytes(ROM_BYTES)

    entry = _Entry("snes/game.sfc")
    result = ra_index.refresh([entry], tmp_path / "cache", rom_dir=rom_dir)
    assert result["hashed"] == 1 and result["known"] == 1

    # Keyed on the stored relative path, which is what /roms annotates against.
    found = ra_index.lookup(["snes/game.sfc"])
    assert found["snes/game.sfc"]["ra_game_id"] == 42

    payloads = [{"rom_id": "r1"}]
    ra_index.annotate([entry], payloads)
    assert payloads[0]["ra_achievements"] == 7


def test_relative_path_without_a_rom_dir_is_skipped_not_crashed(db, library, tmp_path):
    result = ra_index.refresh([_Entry("snes/game.sfc")], tmp_path / "cache")
    assert result["hashed"] == 0


def test_absolute_paths_still_work_when_a_rom_dir_is_given(db, library, tmp_path):
    rom = _rom(tmp_path)
    result = ra_index.refresh([_Entry(rom)], tmp_path / "cache", rom_dir=tmp_path / "roms")
    assert result["hashed"] == 1


def test_resolve_path_rules():
    assert ra_index.resolve_path("snes/a.sfc", Path("/roms")) == Path("/roms/snes/a.sfc")
    assert ra_index.resolve_path("/abs/a.sfc", Path("/roms")) == Path("/abs/a.sfc")
    assert ra_index.resolve_path("snes/a.sfc", None) == Path("snes/a.sfc")


# --- upgrading a title match once a reader exists -----------------------------
#
# A disc indexed before its system had a reader is cached as a title match.
# The path, size and mtime have not moved, so the ordinary freshness check
# would skip it forever and the weaker badge would stick.


def test_a_cached_title_match_is_reread_when_the_system_gains_a_reader(db, tmp_path):
    from app.services.ra_index import _disc_stat

    rom = tmp_path / "game.chd"
    rom.write_bytes(b"\x00" * 64)
    stat = rom.stat()
    entry = _Entry(rom, system="PS1")

    title_cached = {str(rom): (stat.st_size, stat.st_mtime, ra_index.MATCH_TITLE, "")}
    assert _disc_stat(entry, title_cached, None) is not None

    hash_cached = {str(rom): (stat.st_size, stat.st_mtime, ra_index.MATCH_HASH)}
    assert _disc_stat(entry, hash_cached, None) is None


def test_a_replaced_disc_is_reread_even_when_already_hashed(db, tmp_path):
    from app.services.ra_index import _disc_stat

    rom = tmp_path / "game.chd"
    rom.write_bytes(b"\x00" * 64)
    entry = _Entry(rom, system="PS1")
    stale = {str(rom): (999999, 0.0, ra_index.MATCH_HASH)}
    assert _disc_stat(entry, stale, None) is not None


def test_an_unindexed_disc_is_read(db, tmp_path):
    from app.services.ra_index import _disc_stat

    rom = tmp_path / "game.chd"
    rom.write_bytes(b"\x00" * 64)
    assert _disc_stat(_Entry(rom, system="PS1"), {}, None) is not None


# --- badges have to reach the clients ----------------------------------------
#
# Clients cache the catalogue per system against a fingerprint. If the
# fingerprint ignores the RA data, a system whose ROMs have not moved keeps
# its old fingerprint, every client decides its cache is current, and a
# badge earned by a later indexing pass never reaches the device.


def test_indexing_moves_the_catalog_fingerprint(db, library, tmp_path):
    from app.services import rom_scanner

    rom = _rom(tmp_path)
    entry = rom_scanner.RomEntry(
        rom_id="r1", title_id="SNES_game", system="SNES", name="Game",
        filename="game.sfc", path=str(rom), size=rom.stat().st_size,
    )
    catalog = rom_scanner.RomCatalog()
    assert catalog._add(entry)

    before = catalog.fingerprints()
    assert before["SNES"]["fingerprint"], "a populated catalogue must fingerprint"

    ra_index.refresh([_Entry(rom)], tmp_path / "cache")

    after = catalog.fingerprints()
    assert after["SNES"]["fingerprint"] != before["SNES"]["fingerprint"], (
        "a badge earned after the catalogue was cached must move the "
        "fingerprint, or no client will ever refetch it"
    )


def test_fingerprint_is_stable_when_nothing_changed(db, library, tmp_path):
    """It must not churn: a fingerprint that moved every call would make
    every client refetch the whole catalogue on every start."""
    from app.services import rom_scanner

    rom = _rom(tmp_path)
    entry = rom_scanner.RomEntry(
        rom_id="r1", title_id="SNES_game", system="SNES", name="Game",
        filename="game.sfc", path=str(rom), size=rom.stat().st_size,
    )
    catalog = rom_scanner.RomCatalog()
    catalog._add(entry)
    ra_index.refresh([_Entry(rom)], tmp_path / "cache")

    first = catalog.fingerprints()["SNES"]["fingerprint"]
    assert catalog.fingerprints()["SNES"]["fingerprint"] == first
    # A second pass that finds nothing new must not move it either.
    ra_index.refresh([_Entry(rom)], tmp_path / "cache")
    assert catalog.fingerprints()["SNES"]["fingerprint"] == first


def test_ra_generation_advances_when_rows_are_written(db, library, tmp_path):
    rom = _rom(tmp_path)
    before = ra_index.generation()
    ra_index.refresh([_Entry(rom)], tmp_path / "cache")
    assert ra_index.generation() > before


def test_ra_generation_advances_on_clear(db):
    before = ra_index.generation()
    ra_index.clear()
    assert ra_index.generation() > before


# --- MSU packs ----------------------------------------------------------------
#
# A pack is audio plus one patched cartridge, served as a zip (or folder).
# RA identifies the game by that cartridge alone, so the indexer hashes it -
# and must never pick an audio file or a second ROM variant instead.


class _Pack(_Entry):
    def __init__(self, path, kind="msu1", system="SNES"):
        super().__init__(path, system=system, is_bundle=True)
        self.bundle_kind = kind


def _zip(path, members):
    import zipfile
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def test_msu_pack_is_indexed_by_its_cartridge(db, library, tmp_path):
    pack = _zip(tmp_path / "Game (USA) (MSU1).zip", {
        "Game (USA) (MSU1).sfc": ROM_BYTES,
        "Game (USA) (MSU1).msu": b"",
        "Game (USA) (MSU1)-1.pcm": b"\x00" * 4096,
    })
    result = ra_index.refresh([_Pack(pack)], tmp_path / "cache")
    assert result["hashed"] == 1 and result["known"] == 1
    assert ra_index.lookup([str(pack)])[str(pack)]["ra_achievements"] == 7


def test_pack_zipped_with_its_folder_still_finds_the_cartridge(db, library, tmp_path):
    pack = _zip(tmp_path / "Game.zip", {
        "Game (USA) (MSU1)/Game (USA) (MSU1).sfc": ROM_BYTES,
        "Game (USA) (MSU1)/Game (USA) (MSU1).msu": b"",
    })
    ra_index.refresh([_Pack(pack)], tmp_path / "cache")
    assert ra_index.lookup([str(pack)])[str(pack)]["ra_game_id"] == 42


def test_the_rom_matching_the_msu_sidecar_wins_over_a_variant(db, library, tmp_path):
    """Packs sometimes ship a second ROM ([cheat], [FastROM]) - the one
    named like the .msu is what the launcher opens, so that is hashed."""
    pack = _zip(tmp_path / "Game.zip", {
        "Game (USA) (MSU1).sfc": ROM_BYTES,
        "Game (USA) (MSU1) [Invincibility].sfc": OTHER_BYTES,
        "Game (USA) (MSU1).msu": b"",
    })
    ra_index.refresh([_Pack(pack)], tmp_path / "cache")
    assert ra_index.lookup([str(pack)])[str(pack)]["ra_game_id"] == 42


def test_pack_folder_is_indexed_too(db, library, tmp_path):
    folder = tmp_path / "Game (USA) (MSU1)"
    folder.mkdir()
    (folder / "Game (USA) (MSU1).sfc").write_bytes(ROM_BYTES)
    (folder / "Game (USA) (MSU1).msu").write_bytes(b"")
    ra_index.refresh([_Pack(folder)], tmp_path / "cache")
    assert ra_index.lookup([str(folder)])[str(folder)]["ra_game_id"] == 42


def test_pack_with_no_cartridge_is_cached_as_unknown_not_crashed(db, library, tmp_path):
    pack = _zip(tmp_path / "Audio only.zip", {"track-1.pcm": b"\x00" * 64})
    result = ra_index.refresh([_Pack(pack)], tmp_path / "cache")
    assert result["hashed"] == 0
    assert ra_index.lookup([str(pack)]) == {}


def test_an_ordinary_bundle_is_still_skipped(db, library, tmp_path):
    """Only MSU packs wrap a single cart; a Wii U or PS3 folder does not."""
    folder = tmp_path / "Some Bundle"
    folder.mkdir()
    (folder / "game.sfc").write_bytes(ROM_BYTES)
    entry = _Entry(folder, is_bundle=True)
    entry.bundle_kind = ""
    assert ra_index.refresh([entry], tmp_path / "cache")["hashed"] == 0


def test_zipped_rom_is_matched_through_its_member(db, library, tmp_path):
    import zipfile

    rom = tmp_path / "game.zip"
    with zipfile.ZipFile(rom, "w") as zf:
        zf.writestr("game.sfc", ROM_BYTES)
    ra_index.refresh([_Entry(rom)], tmp_path / "cache")
    assert ra_index.lookup([str(rom)])[str(rom)]["ra_game_id"] == 42


def test_archive_misses_from_the_old_rule_are_forgotten_once(db, tmp_path):
    conn = rom_db.connection()
    conn.execute("DELETE FROM ra_meta")
    rows = [("snes/a.zip", 0), ("snes/pack.zip", 9), ("snes/b.sfc", 0)]
    for path, gid in rows:
        conn.execute("INSERT OR REPLACE INTO ra_roms (path, size, mtime, game_id) VALUES (?, 1, 1, ?)",
                     (path, gid))
    conn.commit()
    ra_index._forget_archive_hashes(conn)
    left = {r[0] for r in conn.execute("SELECT path FROM ra_roms")}
    assert left == {"snes/pack.zip", "snes/b.sfc"}
    # Second run is a no-op: a fresh miss stays cached.
    conn.execute("INSERT INTO ra_roms (path, size, mtime, game_id) VALUES ('snes/c.zip', 1, 1, 0)")
    conn.commit()
    ra_index._forget_archive_hashes(conn)
    assert conn.execute("SELECT count(*) FROM ra_roms WHERE path = 'snes/c.zip'").fetchone()[0] == 1


def test_a_disc_read_but_matched_by_title_is_not_read_again(db, tmp_path):
    from app.services.ra_index import DISC_READ, _disc_stat

    rom = tmp_path / "game.chd"
    rom.write_bytes(b"\x00" * 64)
    stat = rom.stat()
    entry = _Entry(rom, system="PS1")
    for md5 in (DISC_READ, "0" * 32):
        cached = {str(rom): (stat.st_size, stat.st_mtime, ra_index.MATCH_TITLE, md5)}
        assert _disc_stat(entry, cached, None) is None


def test_title_fallback_row_remembers_the_disc_was_read(db, tmp_path, monkeypatch):
    rom = tmp_path / "game.chd"
    rom.write_bytes(b"\x00" * 64)
    reads = []
    monkeypatch.setattr(ra_index, "hash_disc_file",
                        lambda path, system: reads.append(path) or "ab" * 16)
    monkeypatch.setattr(ra_index, "fetch_library",
                        lambda cid, **kw: RaLibrary(cid, {}, {5: "Game"}, achievements={5: 3}))
    entry = _Entry(rom, system="PS1")
    entry.name = "Game"
    ra_index.refresh([entry], tmp_path / "cache")
    row = rom_db.connection().execute(
        "SELECT md5, match_kind, game_id FROM ra_roms WHERE path = ?", (str(rom),)).fetchone()
    assert (row["match_kind"], row["game_id"]) == (ra_index.MATCH_TITLE, 5)
    assert row["md5"]                                   # read: not re-read next pass
    assert "ra_hash" not in ra_index.lookup([str(rom)])[str(rom)]   # not advertised as exact
    # A second pass has nothing to read.
    ra_index.refresh([entry], tmp_path / "cache")
    assert len(reads) == 1


def test_old_title_rows_are_marked_read_once(db):
    conn = rom_db.connection()
    conn.execute("DELETE FROM ra_meta WHERE key = 'disc_rule'")
    conn.execute("INSERT OR REPLACE INTO ra_roms (path, size, mtime, md5, game_id, match_kind) "
                 "VALUES ('ps1/a.chd', 1, 1, '', 5, 'title')")
    conn.commit()
    ra_index._mark_title_discs_read(conn)
    assert conn.execute("SELECT md5 FROM ra_roms WHERE path = 'ps1/a.chd'").fetchone()[0] == ra_index.DISC_READ
