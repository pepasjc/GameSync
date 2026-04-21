"""Regression tests for SyncClient.download_save — save metadata refresh.

The ``/titles`` listing returns the hash of the server's stored blob (for
PS1/PS2/GC that's a VMP-style multi-slot file), while the per-format
download endpoints (``/ps1-card``, ``/ps2-card``, ``/gc-card``) return
the hash of the extracted single-slot file that the client actually
writes to disk.  After a successful download the entry's server_hash /
server_size must reflect the extracted-format values so the Save Info
dialog compares apples-to-apples.  Previously it kept the listing-hash
and the UI reported "Hashes differ" forever.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
STEAMDECK_ROOT = ROOT / "steamdeck"
if str(STEAMDECK_ROOT) not in sys.path:
    sys.path.insert(0, str(STEAMDECK_ROOT))

import sync_client as sync_client_mod  # noqa: E402
from scanner.models import GameEntry  # noqa: E402
from sync_client import SyncClient  # noqa: E402


class _CardResponse:
    """Stand-in for the requests.Response returned by /ps1-card, /ps2-card, etc."""

    def __init__(self, body: bytes, save_hash: str, save_size: int, status=200):
        self.status_code = status
        self.content = body
        self.headers = {
            "X-Save-Hash": save_hash,
            "X-Save-Size": str(save_size),
        }


def _patch_get(monkeypatch, response):
    calls: list[str] = []

    def fake_get(url, headers=None, timeout=None, **kwargs):
        calls.append(url)
        return response

    monkeypatch.setattr(sync_client_mod.requests, "get", fake_get)
    monkeypatch.setattr(sync_client_mod, "_update_state", lambda *a, **kw: None)
    return calls


def test_download_ps1_save_refreshes_entry_server_hash_from_headers(
    monkeypatch, tmp_path
):
    """After a PS1 save download entry.server_hash must equal the hash the
    server returned for the extracted 128 KB raw card — the same bytes we
    just wrote locally.  Without this refresh, the detail dialog kept the
    listing hash (a VMP blob) and reported 'Hashes differ' forever."""
    body = b"\x00" * 131072  # 128 KB raw PS1 card
    extracted_hash = hashlib.sha256(body).hexdigest()
    _patch_get(
        monkeypatch,
        _CardResponse(body=body, save_hash=extracted_hash, save_size=len(body)),
    )

    save_path = tmp_path / "memcards" / "Breath of Fire IV (USA)_1.mcd"
    entry = GameEntry(
        title_id="SLUS01324",
        display_name="Breath of Fire IV (USA)",
        system="PS1",
        emulator="DuckStation",
        save_path=save_path,
        # What the /titles listing said before download — the VMP blob hash.
        server_hash="listing-blob-hash",
        server_size=274432,
    )
    client = SyncClient("example", 8000, "key")

    ok = client.download_save(entry, force=True)

    assert ok is True
    assert save_path.read_bytes() == body
    # Entry's server_hash now matches what's on disk — dialog shows "match".
    assert entry.server_hash == extracted_hash
    assert entry.save_hash == extracted_hash
    assert entry.server_size == len(body)
    assert entry.save_size == len(body)


def test_download_ps2_save_refreshes_entry_server_hash_from_headers(
    monkeypatch, tmp_path
):
    body = b"PS2CARD" * 1000
    extracted_hash = hashlib.sha256(body).hexdigest()
    _patch_get(
        monkeypatch,
        _CardResponse(body=body, save_hash=extracted_hash, save_size=len(body)),
    )

    save_path = tmp_path / "memcards" / "Mcd001.ps2"
    entry = GameEntry(
        title_id="SLUS21214",
        display_name="God of War (USA)",
        system="PS2",
        emulator="PCSX2",
        save_path=save_path,
        server_hash="old-listing-hash",
    )
    client = SyncClient("example", 8000, "key")

    assert client.download_save(entry, force=True) is True
    assert entry.server_hash == extracted_hash
    assert entry.save_hash == extracted_hash


def test_download_save_without_header_preserves_prior_server_hash(
    monkeypatch, tmp_path
):
    """If the server doesn't populate X-Save-Hash (older builds), leave the
    listing's hash alone rather than blanking it out — we'd rather show a
    stale hash than nothing."""
    body = b"raw-bytes"
    response = _CardResponse(body=body, save_hash="", save_size=len(body))
    response.headers = {}  # no X-Save-Hash / X-Save-Size
    _patch_get(monkeypatch, response)

    save_path = tmp_path / "memcards" / "a.mcd"
    entry = GameEntry(
        title_id="SLUS99999",
        display_name="Unknown",
        system="PS1",
        emulator="DuckStation",
        save_path=save_path,
        server_hash="prior-hash",
        server_size=42,
    )
    client = SyncClient("example", 8000, "key")

    assert client.download_save(entry, force=True) is True
    assert entry.server_hash == "prior-hash"
    assert entry.server_size == 42
    # Local hash is still computed from the written bytes.
    assert entry.save_hash == hashlib.sha256(body).hexdigest()
