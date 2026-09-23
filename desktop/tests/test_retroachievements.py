import hashlib
import json
import time
import zipfile
from pathlib import Path

import pytest

import retroachievements as ra
from shared import ra_api


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


# --- classification -----------------------------------------------------------


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Bahamut Lagoon (Japan) [T-En by Near v1.0].sfc", ra.KIND_TRANSLATION),
        ("Game (Japan) [T+Eng1.0_Group].nes", ra.KIND_TRANSLATION),
        ("Game (Japan) (Translation).gba", ra.KIND_TRANSLATION),
        ("Game [T-En by X] [Hack].sfc", ra.KIND_TRANSLATION),
        ("Super Mario World (USA) [Hack].sfc", ra.KIND_HACK),
        ("Super Mario World (Hack).sfc", ra.KIND_HACK),
        ("Kaizo Mario World hack.smc", ra.KIND_HACK),
        ("Game (USA) [h1].nes", ra.KIND_HACK),
        ("Hackers (USA).nes", None),
        ("3 Ninjas Kick Back (USA).sfc", None),
    ],
)
def test_classify_name(name, expected):
    assert ra.classify_name(name) == expected


def test_classify_uses_dat_membership_when_untagged():
    dat = {"ABCD1234": "Game (USA)"}
    assert ra.classify("Game (USA).sfc", "abcd1234", dat) == ra.KIND_RETAIL
    assert ra.classify("Game (USA).sfc", "00000000", dat) == ra.KIND_UNMATCHED
    assert ra.classify("Game (USA).sfc", "00000000", None) == ra.KIND_UNKNOWN
    assert ra.classify("Game [T-En].sfc", "ABCD1234", dat) == ra.KIND_TRANSLATION


# --- system detection ---------------------------------------------------------


def test_detect_system_walks_up_to_root(tmp_path):
    rom = tmp_path / "snes" / "hacks" / "game.sfc"
    rom.parent.mkdir(parents=True)
    assert ra.detect_system(rom, tmp_path) == "SNES"
    genesis = tmp_path / "genesis" / "game.md"
    genesis.parent.mkdir()
    assert ra.detect_system(genesis, tmp_path) == "MD"


def test_detect_system_accepts_root_named_after_system(tmp_path):
    root = tmp_path / "nes"
    root.mkdir()
    assert ra.detect_system(root / "game.nes", root) == "NES"
    other = tmp_path / "stuff"
    other.mkdir()
    assert ra.detect_system(other / "game.nes", other) is None


# --- library cache ------------------------------------------------------------


class _FakeLib:
    def __init__(self, hashes: dict[str, int], titles: dict[int, str]):
        self.lib = ra.RaLibrary(console_id=3, hashes=hashes, titles=titles, fetched_at=time.time())


def test_library_lookup_is_case_insensitive():
    lib = ra.RaLibrary(3, {"abc": 5}, {5: "Title"})
    assert lib.lookup("ABC") == (5, "Title")
    assert lib.lookup("zzz") == (None, None)


def test_library_round_trips_through_json():
    lib = ra.RaLibrary(3, {"abc": 5}, {5: "Title"}, fetched_at=123.0)
    again = ra.RaLibrary.from_json(json.loads(json.dumps(lib.to_json())))
    assert again.hashes == {"abc": 5}
    assert again.titles == {5: "Title"}
    assert again.fetched_at == 123.0


def test_fetch_library_uses_cache_then_refreshes(tmp_path, monkeypatch):
    calls = []

    def fake_download(console_id, session=None):
        calls.append(console_id)
        return ra.RaLibrary(console_id, {"abc": 1}, {1: "One"}, fetched_at=time.time())

    monkeypatch.setattr(ra_api, "download_library", fake_download)

    first = ra.fetch_library(3, cache_dir=tmp_path)
    second = ra.fetch_library(3, cache_dir=tmp_path)
    assert calls == [3]
    assert second.hashes == first.hashes
    assert (tmp_path / "console_3.json").exists()

    ra.fetch_library(3, cache_dir=tmp_path, force_refresh=True)
    assert calls == [3, 3]


def test_fetch_library_falls_back_to_stale_cache_when_offline(tmp_path, monkeypatch):
    stale = ra.RaLibrary(3, {"abc": 1}, {1: "One"}, fetched_at=time.time() - 10 * ra.CACHE_MAX_AGE)
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "console_3.json").write_text(json.dumps(stale.to_json()))

    def boom(console_id, session=None):
        raise RuntimeError("no network")

    monkeypatch.setattr(ra_api, "download_library", boom)
    lib = ra.fetch_library(3, cache_dir=tmp_path)
    assert lib.hashes == {"abc": 1}


def test_fetch_library_raises_when_offline_and_no_cache(tmp_path, monkeypatch):
    def boom(console_id, session=None):
        raise RuntimeError("no network")

    monkeypatch.setattr(ra_api, "download_library", boom)
    with pytest.raises(RuntimeError):
        ra.fetch_library(3, cache_dir=tmp_path)


def test_clear_cache_removes_console_files(tmp_path):
    (tmp_path / "console_3.json").write_text("{}")
    (tmp_path / "console_7.json").write_text("{}")
    assert ra.clear_cache(tmp_path) == 2
    assert ra.clear_cache(tmp_path) == 0


# --- scan ---------------------------------------------------------------------

_RETAIL = b"\x11" * 0x8000
_HACK = b"\x22" * 0x8000
_TRANSLATION = b"\x33" * 0x8000


class _NoDat(ra.DatCache):
    def get(self, system):
        return None


class _FakeDat(ra.DatCache):
    def __init__(self, dat):
        super().__init__()
        self._dat = dat

    def get(self, system):
        return self._dat


def _libraries(supported: dict[str, tuple[int, str]]):
    lib = ra.RaLibrary(3, {h: gid for h, (gid, _) in supported.items()},
                       {gid: title for gid, title in supported.values()})

    def provider(console_id):
        assert console_id == 3
        return lib

    return provider


def _make_folder(tmp_path: Path) -> Path:
    root = tmp_path / "roms"
    snes = root / "snes"
    snes.mkdir(parents=True)
    (snes / "Retail Game (USA).sfc").write_bytes(_RETAIL)
    (snes / "Retail Game (USA) [Hack].sfc").write_bytes(_HACK)
    (snes / "Retail Game (Japan) [T-En by X v1.0].sfc").write_bytes(_TRANSLATION)
    return root


def test_scan_only_hacks_skips_retail_and_marks_support(tmp_path):
    root = _make_folder(tmp_path)
    provider = _libraries({md5(_HACK): (42, "~Hack~ Retail Game: Remix")})
    entries = ra.scan_folder(root, provider, only_hacks=True, dats=_NoDat())

    by_name = {e.path.name: e for e in entries}
    assert set(by_name) == {"Retail Game (USA) [Hack].sfc", "Retail Game (Japan) [T-En by X v1.0].sfc"}

    hack = by_name["Retail Game (USA) [Hack].sfc"]
    assert hack.status == ra.STATUS_SUPPORTED
    assert hack.game_id == 42
    assert hack.title == "~Hack~ Retail Game: Remix"
    assert hack.game_url == "https://retroachievements.org/game/42"
    assert hack.system == "SNES"

    translation = by_name["Retail Game (Japan) [T-En by X v1.0].sfc"]
    assert translation.status == ra.STATUS_NOT_FOUND
    assert translation.md5 == md5(_TRANSLATION)


def test_scan_everything_includes_retail(tmp_path):
    root = _make_folder(tmp_path)
    provider = _libraries({md5(_RETAIL): (1, "Retail Game")})
    entries = ra.scan_folder(root, provider, only_hacks=False, dats=_NoDat())
    assert len(entries) == 3
    retail = next(e for e in entries if e.path.name == "Retail Game (USA).sfc")
    assert retail.status == ra.STATUS_SUPPORTED
    assert retail.kind == ra.KIND_UNKNOWN  # no DAT to prove it's retail


def test_scan_uses_dat_to_surface_untagged_hacks(tmp_path):
    root = _make_folder(tmp_path)
    untagged = root / "snes" / "Retail Game (USA) (Rev 1).sfc"
    untagged.write_bytes(b"\x44" * 0x8000)
    retail_crc = f"{__import__('zlib').crc32(_RETAIL) & 0xFFFFFFFF:08X}"
    entries = ra.scan_folder(root, _libraries({}), only_hacks=True,
                             dats=_FakeDat({retail_crc: "Retail Game (USA)"}))
    names = {e.path.name: e.kind for e in entries}
    assert names["Retail Game (USA) (Rev 1).sfc"] == ra.KIND_UNMATCHED
    assert "Retail Game (USA).sfc" not in names


def test_scan_forced_system_overrides_folder(tmp_path):
    root = tmp_path / "misc"
    root.mkdir()
    (root / "thing [Hack].sfc").write_bytes(_HACK)
    provider = _libraries({md5(_HACK): (7, "Hack")})
    entries = ra.scan_folder(root, provider, system="SNES", only_hacks=True, dats=_NoDat())
    assert entries[0].system == "SNES"
    assert entries[0].status == ra.STATUS_SUPPORTED


def test_scan_flags_unknown_system_and_unhashable_system(tmp_path):
    root = tmp_path / "roms"
    (root / "whatever").mkdir(parents=True)
    (root / "ps1").mkdir()
    (root / "whatever" / "game [Hack].gba").write_bytes(b"\x00" * 64)
    (root / "ps1" / "game [T-En].cue").write_text("FILE x BINARY")
    entries = ra.scan_folder(root, _libraries({}), only_hacks=True, dats=_NoDat())
    statuses = {e.path.name: e.status for e in entries}
    assert statuses["game [Hack].gba"] == ra.STATUS_NO_SYSTEM
    assert statuses["game [T-En].cue"] == ra.STATUS_UNSUPPORTED


def test_scan_reads_zip_members(tmp_path):
    root = tmp_path / "roms" / "snes"
    root.mkdir(parents=True)
    with zipfile.ZipFile(root / "Pack [Hack].zip", "w") as zf:
        zf.writestr("Retail Game (USA) [Hack].sfc", _HACK)
        zf.writestr("readme.txt", "ignored")
    provider = _libraries({md5(_HACK): (42, "Hack")})
    entries = ra.scan_folder(root.parent, provider, only_hacks=True, dats=_NoDat())
    assert len(entries) == 1
    assert entries[0].member == "Retail Game (USA) [Hack].sfc"
    assert entries[0].label == "Pack [Hack].zip/Retail Game (USA) [Hack].sfc"
    assert entries[0].status == ra.STATUS_SUPPORTED


def test_scan_arcade_hashes_zip_name(tmp_path):
    root = tmp_path / "roms" / "arcade"
    root.mkdir(parents=True)
    (root / "mslug.zip").write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    lib = ra.RaLibrary(27, {md5(b"mslug"): 9}, {9: "Metal Slug"})
    entries = ra.scan_folder(root.parent, lambda cid: lib, only_hacks=False)
    assert entries[0].status == ra.STATUS_SUPPORTED
    assert entries[0].title == "Metal Slug"


def test_scan_stops_when_asked(tmp_path):
    root = _make_folder(tmp_path)
    entries = ra.scan_folder(root, _libraries({}), only_hacks=False,
                             should_stop=lambda: True, dats=_NoDat())
    assert entries == []


def test_scan_survives_unreadable_archive(tmp_path):
    root = tmp_path / "roms" / "snes"
    root.mkdir(parents=True)
    (root / "broken [Hack].zip").write_bytes(b"not a zip")
    entries = ra.scan_folder(root.parent, _libraries({}), only_hacks=True, dats=_NoDat())
    assert entries[0].status == ra.STATUS_ERROR
    assert entries[0].detail


# --- reporting ----------------------------------------------------------------


def test_csv_and_summary(tmp_path):
    e1 = ra.RaScanEntry(Path("a [Hack].sfc"), "SNES", ra.KIND_HACK, md5="ab", status=ra.STATUS_SUPPORTED,
                        game_id=1, title="A")
    e2 = ra.RaScanEntry(Path("b [T-En].sfc"), "SNES", ra.KIND_TRANSLATION, md5="cd",
                        status=ra.STATUS_NOT_FOUND)
    out = tmp_path / "out.csv"
    ra.write_csv([e1, e2], out)
    text = out.read_text(encoding="utf-8")
    assert "https://retroachievements.org/game/1" in text
    assert "Not on RA" in text
    assert ra.summarize([e1, e2]) == "2 ROM(s) — Supported: 1 — Not on RA: 1"


# --- web API (with key) -------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params))
        return _FakeResponse(self.payload, self.status_code)


_WEB_PAYLOAD = [
    {"ID": 228, "Title": "Super Mario World", "NumAchievements": 63, "Hashes": ["AAA", "bbb"]},
    {"ID": 999, "Title": "~Hack~ Empty Set", "NumAchievements": 0, "Hashes": ["ccc"]},
    {"Title": "broken entry"},
]


def test_download_library_web_parses_hashes_titles_and_counts():
    session = _FakeSession(_WEB_PAYLOAD)
    lib = ra.download_library_web(3, "KEY", "me", session=session)
    url, params = session.calls[0]
    assert url == ra.RA_WEB_API_URL
    assert params == {"i": 3, "h": 1, "y": "KEY", "z": "me"}
    assert lib.lookup("aaa") == (228, "Super Mario World")
    assert lib.achievement_count(228) == 63
    assert lib.achievement_count(999) == 0
    assert lib.has_achievement_counts


def test_download_library_web_bad_key_raises_auth_error():
    with pytest.raises(ra.RaAuthError):
        ra.download_library_web(3, "BAD", session=_FakeSession({"message": "nope"}, 401))


def test_fetch_library_uses_web_api_when_key_given(tmp_path):
    session = _FakeSession(_WEB_PAYLOAD)
    lib = ra.fetch_library(3, cache_dir=tmp_path, session=session, api_key="KEY")
    assert lib.has_achievement_counts
    assert (tmp_path / "console_3_web.json").exists()
    # Web and public caches are kept apart so a key change never serves the wrong shape.
    assert not (tmp_path / "console_3.json").exists()
    again = ra.fetch_library(3, cache_dir=tmp_path, session=session, api_key="KEY")
    assert again.achievement_count(228) == 63
    assert len(session.calls) == 1


def test_fetch_library_does_not_hide_bad_key_behind_stale_cache(tmp_path):
    stale = ra.RaLibrary(3, {"abc": 1}, {1: "One"}, fetched_at=0.0, achievements={1: 5})
    (tmp_path / "console_3_web.json").write_text(json.dumps(stale.to_json()))
    with pytest.raises(ra.RaAuthError):
        ra.fetch_library(3, cache_dir=tmp_path, session=_FakeSession({}, 401), api_key="BAD")


def test_scan_marks_zero_achievement_sets_as_registered(tmp_path):
    root = _make_folder(tmp_path)
    lib = ra.RaLibrary(
        3,
        {md5(_HACK): 42, md5(_TRANSLATION): 43},
        {42: "~Hack~ With Set", 43: "~Hack~ Without Set"},
        achievements={42: 12, 43: 0},
    )
    entries = ra.scan_folder(root, lambda cid: lib, only_hacks=True, dats=_NoDat())
    by_name = {e.path.name: e for e in entries}
    hack = by_name["Retail Game (USA) [Hack].sfc"]
    assert hack.status == ra.STATUS_SUPPORTED
    assert hack.achievements == 12
    translation = by_name["Retail Game (Japan) [T-En by X v1.0].sfc"]
    assert translation.status == ra.STATUS_REGISTERED
    assert translation.achievements == 0
    assert "Registered" in ra.summarize(entries)
    assert ra.entry_to_row(hack)["achievements"] == "12"
    assert ra.entry_to_row(ra.RaScanEntry(Path("x"), "SNES", ra.KIND_HACK))["achievements"] == ""
