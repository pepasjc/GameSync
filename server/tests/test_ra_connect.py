import pytest

from app.config import settings
from app.services import ra_connect

MD5 = "9dbd0337235cd8acf032c0fbfd649d70"

PATCH = {
    "ID": 9878,
    "Title": "Tetris DS",
    "Achievements": [
        {"ID": 230051, "Flags": 3, "Points": 1, "MemAddr": "0xH0001=1_0xH0002=2",
         "Title": "Yep, It Ain't Moving", "Description": "Rotate a\tSquare Block"},
        {"ID": 101000001, "Flags": 3, "Points": 0, "MemAddr": "1=1.300.",
         "Title": "Warning: Unknown Emulator"},
        {"ID": 999, "Flags": 5, "Points": 5, "MemAddr": "0xH0003=1", "Title": "Unofficial"},
        {"ID": 230045, "Flags": 3, "Points": 1, "MemAddr": "0xH0004=2",
         "Title": "Double\tTrouble\n"},
    ],
}


@pytest.fixture()
def ra_settings(monkeypatch):
    monkeypatch.setattr(settings, "ra_username", "tester")
    monkeypatch.setattr(settings, "ra_token", "tok")


def test_render_set_keeps_core_only_and_cleans_titles():
    text = ra_connect.render_set(PATCH, MD5.upper())
    assert text.splitlines() == [
        "RASET\t1",
        f"game\t9878\t{MD5}\tTetris DS",
        "ach\t230051\t1\t0xH0001=1_0xH0002=2\tYep, It Ain't Moving\tRotate a Square Block",
        "ach\t230045\t1\t0xH0004=2\tDouble Trouble\t",
    ]


def test_get_set(client, auth_headers, ra_settings, monkeypatch):
    monkeypatch.setattr(ra_connect, "fetch_patch", lambda gid, *a, **k: PATCH)
    resp = client.get(f"/api/v1/ra/set/{MD5}?game_id=9878", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.text.startswith("RASET\t1\ngame\t9878\t")


def test_get_set_without_token(client, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "ra_token", "")
    resp = client.get(f"/api/v1/ra/set/{MD5}?game_id=9878", headers=auth_headers)
    assert resp.status_code == 503


def test_get_set_rejects_bad_md5(client, auth_headers, ra_settings):
    assert client.get("/api/v1/ra/set/nothex", headers=auth_headers).status_code == 400


# ---------------------------------------------------------------------------
# POST /ra/sets: several sets in one response
# ---------------------------------------------------------------------------

MD5_B = "0123456789abcdef0123456789abcdef"
MD5_UNKNOWN = "ffffffffffffffffffffffffffffffff"
MD5_FAILS = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"


def _parse_batch(text: str) -> tuple[dict, list[str], bool]:
    """Read a /ra/sets body the way the DS does: by byte counts."""
    data = text.encode("utf-8")
    assert data.startswith(b"RASETS\t1\n")
    pos = len(b"RASETS\t1\n")
    sets, others, ended = {}, [], False
    while pos < len(data):
        nl = data.index(b"\n", pos)
        line = data[pos:nl].decode()
        pos = nl + 1
        if line == "END":
            ended = True
            break
        if line.startswith("=== "):
            _, md5, length = line.split(" ")
            sets[md5] = data[pos:pos + int(length)].decode("utf-8")
            pos += int(length)
        else:
            others.append(line)
    return sets, others, ended


@pytest.fixture()
def batch_ra(monkeypatch, ra_settings):
    from app.routes import ra as ra_routes

    game_ids = {MD5: 9878, MD5_B: 9878, MD5_FAILS: 5}
    monkeypatch.setattr(ra_routes, "_game_id_for",
                        lambda md5, libraries=None: game_ids.get(md5, 0))
    fetched = []

    def fake_fetch(game_id, *a, **k):
        fetched.append(game_id)
        if game_id == 5:
            raise ra_connect.RaConnectError("server said no")
        return PATCH

    monkeypatch.setattr(ra_connect, "fetch_patch", fake_fetch)
    return fetched


def test_batch_returns_every_known_set_with_byte_lengths(client, auth_headers, batch_ra):
    resp = client.post("/api/v1/ra/sets", headers=auth_headers,
                       json={"md5s": [MD5, MD5_UNKNOWN, MD5_B.upper(), MD5_FAILS]})
    assert resp.status_code == 200
    sets, others, ended = _parse_batch(resp.text)
    assert ended
    # Same text as the single-set route, md5 line included.
    assert sets[MD5] == ra_connect.render_set(PATCH, MD5)
    assert sets[MD5_B] == ra_connect.render_set(PATCH, MD5_B)
    assert others == [f"--- {MD5_UNKNOWN} unknown",
                      f"--- {MD5_FAILS} error server said no"]


def test_batch_byte_length_counts_utf8_bytes(client, auth_headers, batch_ra, monkeypatch):
    patch = dict(PATCH, Title="Pokémon Café")
    monkeypatch.setattr(ra_connect, "fetch_patch", lambda *a, **k: patch)
    resp = client.post("/api/v1/ra/sets", headers=auth_headers, json={"md5s": [MD5, MD5_B]})
    sets, _, ended = _parse_batch(resp.text)
    assert ended
    assert "Pokémon Café" in sets[MD5] and "Pokémon Café" in sets[MD5_B]


def test_batch_asks_once_per_repeated_md5(client, auth_headers, batch_ra):
    resp = client.post("/api/v1/ra/sets", headers=auth_headers, json={"md5s": [MD5, MD5]})
    sets, _, _ = _parse_batch(resp.text)
    assert list(sets) == [MD5]
    assert batch_ra == [9878]


def test_batch_rejects_bad_md5(client, auth_headers, batch_ra):
    resp = client.post("/api/v1/ra/sets", headers=auth_headers, json={"md5s": [MD5, "nothex"]})
    assert resp.status_code == 400


def test_batch_is_capped(client, auth_headers, batch_ra):
    from app.routes.ra import MAX_BATCH_SETS

    md5s = [f"{i:032x}" for i in range(MAX_BATCH_SETS + 1)]
    resp = client.post("/api/v1/ra/sets", headers=auth_headers, json={"md5s": md5s})
    assert resp.status_code == 422


def test_batch_without_token(client, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "ra_token", "")
    resp = client.post("/api/v1/ra/sets", headers=auth_headers, json={"md5s": [MD5]})
    assert resp.status_code == 503


def test_empty_batch_is_just_the_frame(client, auth_headers, batch_ra):
    resp = client.post("/api/v1/ra/sets", headers=auth_headers, json={"md5s": []})
    assert resp.text == "RASETS\t1\nEND\n"
