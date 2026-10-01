import hashlib

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
    monkeypatch.setattr(settings, "ra_submit", False)


def test_render_set_keeps_core_only_and_cleans_titles():
    text = ra_connect.render_set(PATCH, MD5.upper())
    assert text.splitlines() == [
        "RASET\t1",
        f"game\t9878\t{MD5}\tTetris DS",
        "ach\t230051\t1\t0xH0001=1_0xH0002=2\tYep, It Ain't Moving\tRotate a Square Block",
        "ach\t230045\t1\t0xH0004=2\tDouble Trouble\t",
    ]


def test_award_signature_matches_rcheevos():
    assert ra_connect.award_signature(5, "user", False) == hashlib.md5(b"5user0").hexdigest()
    assert ra_connect.award_signature(5, "user", False, 30) == \
        hashlib.md5(b"5user0530").hexdigest()


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


def test_unlocks_are_stored_pending_without_calling_ra(client, auth_headers, ra_settings, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("submission is off: RA must not be contacted")

    monkeypatch.setattr(ra_connect, "award", boom)
    body = {"md5": MD5, "game_id": 9878, "unlocks": [{"id": 230051, "ago": 10}]}
    first = client.post("/api/v1/ra/unlocks", json=body, headers=auth_headers).json()
    again = client.post("/api/v1/ra/unlocks", json=body, headers=auth_headers).json()
    assert first == {"submit": False, "results": [{"id": 230051, "status": "pending"}]}
    assert again["results"] == [{"id": 230051, "status": "duplicate"}]
    log = client.get("/api/v1/ra/unlocks", headers=auth_headers).json()["unlocks"]
    assert [(e["id"], e["status"], e["ago"]) for e in log] == [(230051, "pending", 10)]


def test_pending_unlocks_go_out_when_submission_is_on(client, auth_headers, ra_settings,
                                                      monkeypatch, tmp_save_dir):
    calls = []

    def fake_award(ach_id, md5, username, token, seconds_ago):
        calls.append((ach_id, md5, username, token))
        return {"Success": True}

    monkeypatch.setattr(ra_connect, "award", fake_award)
    body = {"md5": MD5, "unlocks": [{"id": 230051}, {"id": 230045}]}
    client.post("/api/v1/ra/unlocks", json=body, headers=auth_headers)
    assert calls == []

    # Turning submission on sends what was stored, once
    assert ra_connect.submit_pending(tmp_save_dir, "tester", "tok") == 2
    assert ra_connect.submit_pending(tmp_save_dir, "tester", "tok") == 0
    assert calls == [(230051, MD5, "tester", "tok"), (230045, MD5, "tester", "tok")]
    log = client.get("/api/v1/ra/unlocks", headers=auth_headers).json()["unlocks"]
    assert {e["status"] for e in log} == {"submitted"}


def test_unlocks_live_submits_new_and_pending(client, auth_headers, ra_settings, monkeypatch):
    calls = []
    monkeypatch.setattr(ra_connect, "award",
                        lambda ach_id, *a, **k: calls.append(ach_id) or {"Success": True})
    client.post("/api/v1/ra/unlocks", json={"md5": MD5, "unlocks": [{"id": 1}]},
                headers=auth_headers)
    monkeypatch.setattr(settings, "ra_submit", True)
    live = client.post("/api/v1/ra/unlocks", json={"md5": MD5, "unlocks": [{"id": 2}]},
                       headers=auth_headers).json()
    again = client.post("/api/v1/ra/unlocks", json={"md5": MD5, "unlocks": [{"id": 2}]},
                        headers=auth_headers).json()
    assert live["results"] == [{"id": 2, "status": "submitted"}]
    assert again["results"] == [{"id": 2, "status": "duplicate"}]
    assert calls == [1, 2]  # the stored one went out with the new one


def test_unlocks_live_error_is_retried(client, auth_headers, ra_settings, monkeypatch):
    monkeypatch.setattr(settings, "ra_submit", True)

    def failing(*a, **k):
        raise ra_connect.RaConnectError("server hiccup")

    monkeypatch.setattr(ra_connect, "award", failing)
    body = {"md5": MD5, "unlocks": [{"id": 1}]}
    resp = client.post("/api/v1/ra/unlocks", json=body, headers=auth_headers).json()
    assert resp["results"] == [{"id": 1, "status": "error", "detail": "server hiccup"}]
    # Stored all the same, so the DS may forget it; the next upload retries
    monkeypatch.setattr(ra_connect, "award", lambda *a, **k: {"Success": True})
    resp = client.post("/api/v1/ra/unlocks", json={"md5": MD5, "unlocks": [{"id": 3}]},
                       headers=auth_headers).json()
    log = client.get("/api/v1/ra/unlocks", headers=auth_headers).json()["unlocks"]
    assert [(e["id"], e["status"]) for e in log] == [(1, "submitted"), (3, "submitted")]


def test_warning_achievement_is_never_recorded(client, auth_headers, ra_settings):
    body = {"md5": MD5, "unlocks": [{"id": 101000001}]}
    resp = client.post("/api/v1/ra/unlocks", json=body, headers=auth_headers).json()
    assert resp["results"] == [{"id": 101000001, "status": "ignored"}]
    assert client.get("/api/v1/ra/unlocks", headers=auth_headers).json()["unlocks"] == []


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
