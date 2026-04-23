"""End-to-end: slug-form uploads land under the canonical hex/serial key.

Proves the fix for the original bug: Steam Deck / Android clients that
upload NDS saves as ``NDS_mario_kart_ds_usa_australia`` now share the same
server storage key as the 3DS/NDS homebrew clients, which use the canonical
``00048000`` + hex(gamecode) form.  Without this canonicalization, saves
uploaded from one device family were invisible to the other.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app.models.save import BundleFile, SaveBundle
from app.services import dat_normalizer
from app.services.bundle import create_bundle


# ---------------------------------------------------------------------------
# DAT fixture
# ---------------------------------------------------------------------------
#
# These tests need the DAT-backed serial lookup to be wired up so slug
# uploads can actually be upgraded to the canonical hex/serial form.  The
# global ``client`` fixture in conftest.py doesn't run the lifespan hook
# (it constructs a TestClient without the context-manager protocol), so the
# singleton in ``dat_normalizer`` is None by default.  We populate it once
# per module from the real ``server/data/dats/`` folder.


_DATS_DIR = Path(__file__).parent.parent / "data" / "dats"


@pytest.fixture(autouse=True, scope="module")
def _load_dats():
    if not _DATS_DIR.exists():
        pytest.skip(f"DAT dir missing: {_DATS_DIR}")
    dat_normalizer.init(_DATS_DIR)
    yield
    # Leave the singleton in place for other tests that might benefit — it's
    # idempotent and harmless.


def _make_raw_upload(data: bytes = b"NDS save body") -> bytes:
    return data


def _make_string_bundle_bytes(
    title_id: str,
    timestamp: int = 1700000000,
    files: list[tuple[str, bytes]] | None = None,
) -> bytes:
    if files is None:
        files = [("save.bin", b"save data here")]
    bundle_files = [
        BundleFile(
            path=path,
            size=len(data),
            sha256=hashlib.sha256(data).digest(),
            data=data,
        )
        for path, data in files
    ]
    bundle = SaveBundle(
        title_id=0, timestamp=timestamp, files=bundle_files, title_id_str=title_id
    )
    return create_bundle(bundle)


# ---------------------------------------------------------------------------
# Raw upload path (used by the NDS client + Steam Deck NDS scanner)
# ---------------------------------------------------------------------------


class TestNdsRawUploadCanonicalization:
    NDS_MARIO_KART_SLUG = "NDS_mario_kart_ds_usa_australia"
    # Gamecode "AMCE" → 00048000 + hex(ASCII "AMCE") = 00048000414D4345
    # Note: depending on which DAT entry is matched, the exact gamecode may
    # vary (regional releases).  We assert on the structural invariants.

    def test_slug_upload_stored_under_canonical(self, client, auth_headers):
        body = b"NDS save payload"
        resp = client.post(
            f"/api/v1/saves/{self.NDS_MARIO_KART_SLUG}/raw",
            content=body,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )
        assert resp.status_code == 200, resp.text

        # List titles — the stored key should be canonical hex, not the slug.
        r = client.get("/api/v1/titles", headers=auth_headers)
        assert r.status_code == 200
        titles = {t["title_id"] for t in r.json()["titles"]}

        # Exactly one save was uploaded.
        assert len(titles) == 1
        stored_id = next(iter(titles))

        # It should NOT be stored as the slug.
        assert stored_id != self.NDS_MARIO_KART_SLUG
        # It should be canonical NDS hex form.
        assert stored_id.startswith("00048000")
        assert len(stored_id) == 16

    def test_slug_upload_download_via_canonical(self, client, auth_headers):
        body = b"payload for hex download check"
        up = client.post(
            f"/api/v1/saves/{self.NDS_MARIO_KART_SLUG}/raw",
            content=body,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )
        assert up.status_code == 200

        r = client.get("/api/v1/titles", headers=auth_headers)
        stored_id = r.json()["titles"][0]["title_id"]

        # Download via the canonical ID the 3DS/NDS hardware would use.
        down = client.get(
            f"/api/v1/saves/{stored_id}/raw",
            headers=auth_headers,
        )
        assert down.status_code == 200
        assert down.content == body

    def test_slug_upload_download_via_slug_also_works(self, client, auth_headers):
        """A legacy client that only knows the slug form should still find
        its save.  The server canonicalizes the slug on download too."""
        body = b"payload for slug-roundtrip"
        up = client.post(
            f"/api/v1/saves/{self.NDS_MARIO_KART_SLUG}/raw",
            content=body,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )
        assert up.status_code == 200

        # Query using the slug — server canonicalizes internally.
        down = client.get(
            f"/api/v1/saves/{self.NDS_MARIO_KART_SLUG}/raw",
            headers=auth_headers,
        )
        assert down.status_code == 200
        assert down.content == body


class TestNdsUnknownSlugKeptAsIs:
    """A slug that isn't in the DAT (e.g. homebrew) should still work — it
    just stays as a slug rather than being upgraded."""

    HOMEBREW_SLUG = "NDS_totally_made_up_homebrew_xyz_usa"

    def test_unknown_slug_upload_stored_as_slug(self, client, auth_headers):
        body = b"homebrew save"
        resp = client.post(
            f"/api/v1/saves/{self.HOMEBREW_SLUG}/raw",
            content=body,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )
        assert resp.status_code == 200

        r = client.get("/api/v1/titles", headers=auth_headers)
        titles = {t["title_id"] for t in r.json()["titles"]}
        assert self.HOMEBREW_SLUG in titles

    def test_unknown_slug_roundtrip(self, client, auth_headers):
        body = b"homebrew roundtrip"
        client.post(
            f"/api/v1/saves/{self.HOMEBREW_SLUG}/raw",
            content=body,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )
        down = client.get(
            f"/api/v1/saves/{self.HOMEBREW_SLUG}/raw",
            headers=auth_headers,
        )
        assert down.status_code == 200
        assert down.content == body


# ---------------------------------------------------------------------------
# Bundle upload path — client sends slug in both URL and bundle
# ---------------------------------------------------------------------------


class TestBundleSlugCanonicalization:
    """Desktop/Android emulator clients use the bundle format.  When they
    put a slug form in both URL and bundle's title_id_str, the server's
    URL↔bundle match check and canonicalisation should cooperate so the
    save lands under the canonical ID.
    """

    PS1_SLUG = "PS1_crash_bandicoot_usa"

    def test_bundle_slug_upload_may_canonicalize(self, client, auth_headers):
        bundle = _make_string_bundle_bytes(title_id=self.PS1_SLUG)
        resp = client.post(
            f"/api/v1/saves/{self.PS1_SLUG}",
            content=bundle,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )
        assert resp.status_code == 200, resp.text

        r = client.get("/api/v1/titles", headers=auth_headers)
        titles = {t["title_id"] for t in r.json()["titles"]}
        assert len(titles) == 1
        stored_id = next(iter(titles))

        # If the DAT has Crash Bandicoot indexed, the server should
        # upgrade to SCUSxxxxx.  Otherwise the slug stays — both outcomes
        # are valid and tested via slug fallback logic elsewhere.  The
        # invariant we check: no crash, no 400 due to URL/bundle mismatch.
        assert stored_id in (self.PS1_SLUG,) or stored_id.startswith(("SCUS", "SLUS", "SCES", "SLES", "SCPS", "SLPS", "SLPM"))


# ---------------------------------------------------------------------------
# Non-slug systems are untouched
# ---------------------------------------------------------------------------


class TestNonSlugUploadsUnchanged:
    def test_hex_upload_untouched(self, client, auth_headers):
        """A real hex 3DS title_id must not be mutated by canonicalization."""
        bundle = _make_string_bundle_bytes(title_id="0004000000055D00")
        resp = client.post(
            "/api/v1/saves/0004000000055D00",
            content=bundle,
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )
        assert resp.status_code == 200

        r = client.get("/api/v1/titles", headers=auth_headers)
        titles = {t["title_id"] for t in r.json()["titles"]}
        assert "0004000000055D00" in titles

    def test_gba_slug_preserved(self, client, auth_headers):
        """GBA uses slug form as canonical (SYNC_ID_RULES: slug strategy).
        It must not be re-interpreted as something else."""
        slug = "GBA_super_mario_advance_usa"
        resp = client.post(
            f"/api/v1/saves/{slug}/raw",
            content=b"gba save",
            headers={**auth_headers, "Content-Type": "application/octet-stream"},
        )
        assert resp.status_code == 200

        r = client.get("/api/v1/titles", headers=auth_headers)
        titles = {t["title_id"] for t in r.json()["titles"]}
        assert slug in titles
