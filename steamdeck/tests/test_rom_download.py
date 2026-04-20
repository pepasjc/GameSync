"""Tests for the server-only ROM download feature."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
STEAMDECK_ROOT = ROOT / "steamdeck"
if str(STEAMDECK_ROOT) not in sys.path:
    sys.path.insert(0, str(STEAMDECK_ROOT))

from scanner.rom_target import SYSTEM_ROM_DIRS, resolve_rom_target_dir  # noqa: E402
import sync_client as sync_client_mod  # noqa: E402
from sync_client import SyncClient  # noqa: E402


# ---------------------------------------------------------------------------
# resolve_rom_target_dir
# ---------------------------------------------------------------------------


def test_rom_target_prefers_existing_folder(tmp_path):
    (tmp_path / "PS1").mkdir()
    assert resolve_rom_target_dir(tmp_path, "PS1") == tmp_path / "PS1"


def test_rom_target_is_case_sensitive_and_picks_first_matching_alias(tmp_path):
    # Both the lowercase and Title-Case alias exist — "psx" comes first in the
    # alias list so it wins.
    (tmp_path / "psx").mkdir()
    (tmp_path / "PlayStation").mkdir()
    assert resolve_rom_target_dir(tmp_path, "PS1") == tmp_path / "psx"


def test_rom_target_falls_back_to_first_candidate_when_none_exist(tmp_path):
    target = resolve_rom_target_dir(tmp_path, "SAT")
    assert target == tmp_path / SYSTEM_ROM_DIRS["SAT"][0]
    # Should not have been created — caller is responsible for mkdir.
    assert not target.exists()


def test_rom_target_handles_unknown_system(tmp_path):
    target = resolve_rom_target_dir(tmp_path, "NOPE")
    assert target == tmp_path / "NOPE"


# ---------------------------------------------------------------------------
# SyncClient.download_rom
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code=200, body=b"", content_length=None, stream=True):
        self.status_code = status_code
        self._body = body
        self.headers: dict[str, str] = {}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)
        elif stream:
            self.headers["Content-Length"] = str(len(body))

    def iter_content(self, chunk_size=65536):
        # Return the body in a single chunk; plenty for unit tests.
        if self._body:
            yield self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _install_fake_get(monkeypatch, response_factory):
    calls: list[dict] = []

    def fake_get(url, params=None, headers=None, stream=None, timeout=None):
        calls.append(
            {"url": url, "params": params or {}, "stream": stream, "headers": headers}
        )
        return response_factory()

    monkeypatch.setattr(sync_client_mod.requests, "get", fake_get)
    return calls


def test_download_rom_writes_atomic_file(monkeypatch, tmp_path):
    body = b"ROM_CONTENTS_12345"
    calls = _install_fake_get(
        monkeypatch, lambda: _FakeResponse(200, body, content_length=len(body))
    )

    client = SyncClient("localhost", 8000, "key")
    target = tmp_path / "saturn" / "Grandia.chd"

    progress_updates: list[tuple[int, int]] = []
    ok = client.download_rom(
        "SAT_T-4507G",
        target,
        progress_cb=lambda d, t: progress_updates.append((d, t)),
    )

    assert ok is True
    assert target.read_bytes() == body
    # Target dir was created on demand.
    assert target.parent.is_dir()
    # The .part file is gone after the atomic rename.
    assert not target.with_suffix(target.suffix + ".part").exists()
    # Progress is reported with the total from Content-Length.
    assert progress_updates[-1] == (len(body), len(body))
    assert calls[0]["url"].endswith("/api/v1/roms/SAT_T-4507G")


def test_download_rom_propagates_extract_query_param(monkeypatch, tmp_path):
    calls = _install_fake_get(
        monkeypatch,
        lambda: _FakeResponse(200, b"extracted-iso", content_length=13),
    )

    client = SyncClient("localhost", 8000, "key")
    ok = client.download_rom(
        "PSP_UCUS98632",
        tmp_path / "game.iso",
        extract_format="iso",
    )

    assert ok is True
    assert calls[0]["params"] == {"extract": "iso"}


def test_download_rom_returns_false_on_http_error(monkeypatch, tmp_path):
    _install_fake_get(monkeypatch, lambda: _FakeResponse(404, b"missing"))

    client = SyncClient("localhost", 8000, "key")
    target = tmp_path / "PS1" / "fake.chd"
    assert client.download_rom("PS1_unknown", target) is False
    # No partial file left behind on error.
    assert not target.exists()
    assert not target.with_suffix(target.suffix + ".part").exists()


def test_download_rom_cleans_up_partial_on_write_error(monkeypatch, tmp_path):
    class _ExplodingResponse:
        status_code = 200
        headers = {"Content-Length": "10"}

        def iter_content(self, chunk_size=65536):
            yield b"halfway"
            raise IOError("disk full")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(
        sync_client_mod.requests, "get",
        lambda *a, **kw: _ExplodingResponse(),
    )

    client = SyncClient("localhost", 8000, "key")
    target = tmp_path / "roms" / "bad.chd"
    assert client.download_rom("PS1_oops", target) is False
    # Partial .part file must be cleaned up so the scanner never sees it.
    assert not target.exists()
    assert not target.with_suffix(target.suffix + ".part").exists()


# ---------------------------------------------------------------------------
# SyncClient.find_roms_for_title
# ---------------------------------------------------------------------------


def test_find_roms_for_title_filters_by_title_id(monkeypatch):
    response_body = {
        "roms": [
            {"rom_id": "a", "title_id": "SAT_T-4507G", "system": "SAT", "filename": "Grandia D1.chd"},
            {"rom_id": "b", "title_id": "SAT_T-4507G", "system": "SAT", "filename": "Grandia D2.chd"},
            {"rom_id": "c", "title_id": "SAT_OTHER", "system": "SAT", "filename": "Other.chd"},
        ],
        "total": 3,
    }

    class _JsonResponse:
        status_code = 200

        def json(self):
            return response_body

    monkeypatch.setattr(
        sync_client_mod.requests, "get",
        lambda *a, **kw: _JsonResponse(),
    )

    client = SyncClient("localhost", 8000, "key")
    roms = client.find_roms_for_title("SAT_T-4507G", "SAT")
    assert [r["rom_id"] for r in roms] == ["a", "b"]
