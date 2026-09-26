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
         "Title": "Yep, It Ain't Moving"},
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
        "ach\t230051\t1\t0xH0001=1_0xH0002=2\tYep, It Ain't Moving",
        "ach\t230045\t1\t0xH0004=2\tDouble Trouble",
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


def test_unlocks_dry_run_never_calls_ra(client, auth_headers, ra_settings, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("dry run must not contact RA")

    monkeypatch.setattr(ra_connect, "award", boom)
    body = {"md5": MD5, "game_id": 9878, "unlocks": [{"id": 230051, "ago": 10}]}
    first = client.post("/api/v1/ra/unlocks", json=body, headers=auth_headers).json()
    again = client.post("/api/v1/ra/unlocks", json=body, headers=auth_headers).json()
    assert first == {"submit": False, "results": [{"id": 230051, "status": "dry-run"}]}
    assert again["results"] == [{"id": 230051, "status": "duplicate"}]
    log = client.get("/api/v1/ra/unlocks", headers=auth_headers).json()["unlocks"]
    assert [(e["id"], e["status"], e["ago"]) for e in log] == [(230051, "dry-run", 10)]


def test_unlocks_live_submits_once(client, auth_headers, ra_settings, monkeypatch):
    calls = []

    def fake_award(ach_id, md5, username, token, ago):
        calls.append((ach_id, md5, username, token, ago))
        return {"Success": True}

    monkeypatch.setattr(ra_connect, "award", fake_award)
    body = {"md5": MD5, "unlocks": [{"id": 230051}]}
    # A dry-run entry must not stop the later live upload.
    client.post("/api/v1/ra/unlocks", json=body, headers=auth_headers)
    monkeypatch.setattr(settings, "ra_submit", True)
    live = client.post("/api/v1/ra/unlocks", json=body, headers=auth_headers).json()
    again = client.post("/api/v1/ra/unlocks", json=body, headers=auth_headers).json()
    assert live["results"] == [{"id": 230051, "status": "submitted"}]
    assert again["results"] == [{"id": 230051, "status": "duplicate"}]
    assert calls == [(230051, MD5, "tester", "tok", 0)]


def test_unlocks_live_error_is_retried(client, auth_headers, ra_settings, monkeypatch):
    monkeypatch.setattr(settings, "ra_submit", True)

    def failing(*a, **k):
        raise ra_connect.RaConnectError("server hiccup")

    monkeypatch.setattr(ra_connect, "award", failing)
    body = {"md5": MD5, "unlocks": [{"id": 1}]}
    resp = client.post("/api/v1/ra/unlocks", json=body, headers=auth_headers).json()
    assert resp["results"] == [{"id": 1, "status": "error", "detail": "server hiccup"}]
    monkeypatch.setattr(ra_connect, "award", lambda *a, **k: {"Success": True})
    resp = client.post("/api/v1/ra/unlocks", json=body, headers=auth_headers).json()
    assert resp["results"] == [{"id": 1, "status": "submitted"}]
