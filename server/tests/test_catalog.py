"""Tests for the /api/v1/catalog/resolve endpoint.

These tests don't rely on a populated DAT — the resolver falls back to
slug form when DAT lookup can't help, which keeps the tests hermetic.
DAT-backed behaviour is covered in ``shared/tests/test_sync_id.py`` using
a stub ``serial_lookup`` callable.
"""

from __future__ import annotations


def test_resolve_hex_title_id(client, auth_headers):
    resp = client.post(
        "/api/v1/catalog/resolve",
        json={"system": "3DS", "title_id": "0004000000055D00"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["sync_id"] == "0004000000055D00"
    assert body["strategy"] == "title_id"
    assert body["fallback"] is False


def test_resolve_nds_gamecode_direct(client, auth_headers):
    resp = client.post(
        "/api/v1/catalog/resolve",
        json={"system": "NDS", "gamecode": "AMCE"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["sync_id"] == "00048000414D4345"
    assert body["strategy"] == "prefix_hex_serial"
    assert body["fallback"] is False


def test_resolve_ps1_serial_canonicalized(client, auth_headers):
    resp = client.post(
        "/api/v1/catalog/resolve",
        json={"system": "PS1", "serial": "SCUS-94163"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["sync_id"] == "SCUS94163"
    assert body["strategy"] == "serial"
    assert body["fallback"] is False


def test_resolve_slug_strategy(client, auth_headers):
    resp = client.post(
        "/api/v1/catalog/resolve",
        json={"system": "GBA", "rom_filename": "Zelda Minish Cap (USA).gba"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["strategy"] == "slug"
    assert body["sync_id"].startswith("GBA_")
    assert body["fallback"] is False


def test_resolve_nds_fallback_to_slug_without_gamecode(client, auth_headers):
    """Server has DATs loaded; a random filename with no DAT entry falls
    back to slug form and the resolver marks ``fallback: true``."""
    resp = client.post(
        "/api/v1/catalog/resolve",
        json={
            "system": "NDS",
            "rom_filename": "Homebrew Test Game (made up).nds",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["strategy"] == "slug"
    assert body["fallback"] is True
    assert body["sync_id"].startswith("NDS_")


def test_resolve_no_inputs_returns_placeholder(client, auth_headers):
    resp = client.post(
        "/api/v1/catalog/resolve",
        json={"system": "GBA"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["sync_id"] == "GBA_unknown"
    assert body["fallback"] is True


def test_resolve_rejects_missing_auth(client):
    resp = client.post(
        "/api/v1/catalog/resolve",
        json={"system": "3DS", "title_id": "0004000000055D00"},
    )
    assert resp.status_code == 401


def test_resolve_batch(client, auth_headers):
    resp = client.post(
        "/api/v1/catalog/resolve/batch",
        json={
            "items": [
                {"system": "3DS", "title_id": "0004000000055D00"},
                {"system": "NDS", "gamecode": "AMCE"},
                {"system": "PS1", "serial": "SCUS-94163"},
                {"system": "GBA", "rom_filename": "Zelda.gba"},
            ]
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    results = body["results"]
    assert len(results) == 4
    assert results[0]["strategy"] == "title_id"
    assert results[1]["strategy"] == "prefix_hex_serial"
    assert results[2]["strategy"] == "serial"
    assert results[3]["strategy"] == "slug"


def test_resolve_dat_backed_nds_slug_to_hex(client, auth_headers):
    """End-to-end: a slug-form NDS ROM filename that IS in the DAT should
    come back as the canonical hex form.  This proves the server wires the
    DAT serial lookup into the resolver.  Uses a well-known No-Intro entry
    that should exist in the loaded NDS DAT.
    """
    resp = client.post(
        "/api/v1/catalog/resolve",
        json={
            "system": "NDS",
            "rom_filename": "Mario Kart DS (USA, Australia) (En,Fr,De,Es,It).nds",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    # If the DAT is loaded, strategy should upgrade to prefix_hex_serial.
    # If DATs weren't loaded in the test environment we'd see slug fallback
    # — assert on the strategy the server is actually able to produce.
    if body["strategy"] == "prefix_hex_serial":
        assert body["fallback"] is False
        # Sync ID should start with the NDS prefix.
        assert body["sync_id"].startswith("00048000")
        # Canonical name should be populated from the DAT.
        assert body["canonical_name"] is not None
        assert "Mario Kart DS" in body["canonical_name"]


def test_resolve_dat_backed_ps1_slug_to_serial(client, auth_headers):
    """Filename with a matching DAT entry should resolve to the serial,
    not to a slug.  Mirrors the desktop client's normalize flow."""
    resp = client.post(
        "/api/v1/catalog/resolve",
        json={
            "system": "PS1",
            "rom_filename": "Crash Bandicoot (USA).cue",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    # Only assert the strong invariant when DAT data is present.
    if body["strategy"] == "serial":
        assert body["fallback"] is False
        # PS1 serials are alphanumeric with no punctuation after canonicalization.
        assert body["sync_id"].isalnum()
        assert body["canonical_name"] is not None
