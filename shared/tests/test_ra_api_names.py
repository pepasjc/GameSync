import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shared.ra_api import RaLibrary, fetch_hash_names  # noqa: E402
from shared.ra_titles import build_name_index  # noqa: E402


class _Resp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _Session:
    """Answers API_GetGameHashes from a {game_id: [names]} table."""

    def __init__(self, table):
        self.table = table
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(params["i"])
        return _Resp({"Results": [{"Name": n, "MD5": "x"} for n in self.table.get(params["i"], [])]})


def _library():
    return RaLibrary(
        56,
        {"a1": 23831, "b1": 23827, "c1": 900, "d1": 901},
        {23831: "Neo Turf Masters", 23827: "The Last Blade",
         900: "Empty Set", 901: "The Last Blade [Subset - Bonus]"},
        achievements={23831: 30, 23827: 40, 900: 0, 901: 10},
    )


NAMES = {
    23831: ["Big Tournament Golf ~ Neo Turf Masters (Japan) (En,Ja)"],
    23827: ["Bakumatsu Roman - Gekka no Kenshi ~ The Last Blade (Japan) (En,Ja,Es,Pt)"],
    901: ["Bakumatsu Roman - Gekka no Kenshi ~ The Last Blade (Japan) (En,Ja,Es,Pt)"],
}


def test_names_are_fetched_for_games_with_sets_only(tmp_path):
    session = _Session(NAMES)
    names = fetch_hash_names(_library(), cache_dir=tmp_path, api_key="K", session=session, delay=0)
    assert sorted(session.calls) == [23827, 23831]         # no empty set, no subset
    assert names[23831] == NAMES[23831]


def test_names_are_cached_until_the_hash_count_changes(tmp_path):
    lib = _library()
    fetch_hash_names(lib, cache_dir=tmp_path, api_key="K", session=_Session(NAMES), delay=0)
    again = _Session(NAMES)
    fetch_hash_names(lib, cache_dir=tmp_path, api_key="K", session=again, delay=0)
    assert again.calls == []
    lib.hashes["a2"] = 23831                                # RA registered another dump
    fetch_hash_names(lib, cache_dir=tmp_path, api_key="K", session=again, delay=0)
    assert again.calls == [23831]


def test_without_a_key_only_the_cache_is_used(tmp_path):
    session = _Session(NAMES)
    assert fetch_hash_names(_library(), cache_dir=tmp_path, session=session) == {}
    assert session.calls == []


def test_a_redump_file_name_finds_its_game_whatever_its_region(tmp_path):
    lib = _library()
    names = fetch_hash_names(lib, cache_dir=tmp_path, api_key="K", session=_Session(NAMES), delay=0)
    index = build_name_index(lib, names)
    assert index.lookup("Big Tournament Golf ~ Neo Turf Masters (Japan) (En,Ja).chd") == (23831, 30)
    assert index.lookup("Bakumatsu Roman - Gekka no Kenshi ~ The Last Blade (World).chd") == (23827, 40)


class _ThrottledSession(_Session):
    """429 on the first ``throttled`` requests, then answers normally."""

    def __init__(self, table, throttled):
        super().__init__(table)
        self.throttled = throttled

    def get(self, url, params=None, headers=None, timeout=None):
        if self.throttled:
            self.throttled -= 1
            self.calls.append(("429", params["i"]))
            return _Resp({}, status_code=429)
        return super().get(url, params, headers, timeout)


def test_throttling_is_waited_out_not_given_up_on(tmp_path):
    waits = []
    session = _ThrottledSession(NAMES, throttled=2)
    names = fetch_hash_names(_library(), cache_dir=tmp_path, api_key="K", session=session,
                             delay=0, sleep=waits.append)
    assert set(names) == {23827, 23831}
    assert waits[:2] == [10, 30]


def test_persistent_throttling_keeps_what_was_fetched(tmp_path):
    session = _ThrottledSession(NAMES, throttled=99)
    names = fetch_hash_names(_library(), cache_dir=tmp_path, api_key="K", session=session,
                             delay=0, sleep=lambda s: None)
    assert names == {}


def test_fetch_can_be_limited_to_some_games(tmp_path):
    session = _Session(NAMES)
    fetch_hash_names(_library(), cache_dir=tmp_path, api_key="K", session=session, delay=0,
                     game_ids={23831})
    assert session.calls == [23831]
