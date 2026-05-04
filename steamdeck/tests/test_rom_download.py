"""Tests for the server-only ROM download feature."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
STEAMDECK_ROOT = ROOT / "steamdeck"
if str(STEAMDECK_ROOT) not in sys.path:
    sys.path.insert(0, str(STEAMDECK_ROOT))

from config import normalize_rom_dir_overrides  # noqa: E402
from scanner.rom_target import (  # noqa: E402
    SYSTEM_ROM_DIRS,
    prepare_rom_folders,
    resolve_rom_target_dir,
)
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


def test_rom_target_canonicalises_alias_codes(tmp_path):
    # Regression: the server emitted ``SCD`` for Sega CD ROMs; the client
    # used to drop them into ``roms/SCD`` next to the user's existing
    # ``roms/segacd`` folder.  After canonicalisation via
    # ``normalize_system_code``, ``SCD`` resolves exactly like ``SEGACD``.
    assert (
        resolve_rom_target_dir(tmp_path, "SCD")
        == tmp_path / SYSTEM_ROM_DIRS["SEGACD"][0]
    )
    # And if the user already has ``segacd/`` on disk, the existing folder
    # wins for both spellings.
    (tmp_path / "segacd").mkdir()
    assert resolve_rom_target_dir(tmp_path, "SCD") == tmp_path / "segacd"
    assert resolve_rom_target_dir(tmp_path, "SEGACD") == tmp_path / "segacd"


def test_rom_target_canonicalises_folder_style_system_names(tmp_path):
    # Folder-style names (``megadrive``, ``Genesis``) also collapse to the
    # canonical code so downloads don't fragment into a second folder.
    (tmp_path / "megadrive").mkdir()
    assert resolve_rom_target_dir(tmp_path, "genesis") == tmp_path / "megadrive"
    assert resolve_rom_target_dir(tmp_path, "Mega Drive") == tmp_path / "megadrive"


def test_rom_target_override_uses_canonical_key(tmp_path):
    # An override configured under the canonical code should apply even if
    # the server emits the alias spelling.
    custom = tmp_path / "sd2" / "segacd"
    overrides = {"SEGACD": str(custom)}
    assert resolve_rom_target_dir(tmp_path, "SCD", overrides) == custom


def test_rom_target_override_wins_over_existing_candidate(tmp_path):
    # Even when the default ``psx`` folder exists, a user-provided override
    # for PS1 takes priority so downloads land on a custom SD card / drive.
    (tmp_path / "psx").mkdir()
    custom = tmp_path / "sd2" / "games" / "playstation"
    overrides = {"PS1": str(custom)}
    assert resolve_rom_target_dir(tmp_path, "PS1", overrides) == custom


def test_rom_target_override_is_case_insensitive_on_system_key(tmp_path):
    # Overrides from config are normalized to upper-case, but we still
    # look them up safely regardless of how the caller passes the system.
    custom = tmp_path / "custom"
    overrides = {"PS1": str(custom)}
    assert resolve_rom_target_dir(tmp_path, "ps1", overrides) == custom


def test_rom_target_empty_override_value_falls_back_to_candidates(tmp_path):
    # An empty string (or whitespace) shouldn't be treated as "the root of
    # the filesystem" — we fall back to the normal candidate search.
    (tmp_path / "psx").mkdir()
    assert resolve_rom_target_dir(tmp_path, "PS1", {"PS1": "   "}) == tmp_path / "psx"


def test_rom_target_override_with_relative_path_resolves_under_roms_base(tmp_path):
    # Relative override paths (rare, but possible if someone hand-edits the
    # JSON) resolve under the rom base instead of Path.cwd().
    overrides = {"PS1": "my-ps1-games"}
    assert (
        resolve_rom_target_dir(tmp_path, "PS1", overrides)
        == tmp_path / "my-ps1-games"
    )


def test_rom_target_override_missing_system_uses_candidates(tmp_path):
    # An override for *another* system doesn't affect PS1.
    (tmp_path / "psx").mkdir()
    assert (
        resolve_rom_target_dir(tmp_path, "PS1", {"GBA": "/tmp/gba"})
        == tmp_path / "psx"
    )


# ---------------------------------------------------------------------------
# normalize_rom_dir_overrides
# ---------------------------------------------------------------------------


def test_normalize_overrides_uppercases_keys_and_strips_paths():
    src = {"ps1": "  /mnt/sd2/ps1  ", "GBA": "/mnt/gba"}
    out = normalize_rom_dir_overrides(src)
    assert out == {"PS1": "/mnt/sd2/ps1", "GBA": "/mnt/gba"}


def test_normalize_overrides_drops_empty_values():
    src = {"PS1": "", "GBA": "   ", "SAT": "/mnt/saturn"}
    assert normalize_rom_dir_overrides(src) == {"SAT": "/mnt/saturn"}


def test_normalize_overrides_rejects_non_dict_inputs():
    assert normalize_rom_dir_overrides(None) == {}
    assert normalize_rom_dir_overrides("not a dict") == {}
    assert normalize_rom_dir_overrides([("PS1", "/x")]) == {}


def test_normalize_overrides_skips_non_string_keys():
    assert normalize_rom_dir_overrides({42: "/x", "PS1": "/y"}) == {"PS1": "/y"}


# ---------------------------------------------------------------------------
# prepare_rom_folders
# ---------------------------------------------------------------------------


def test_prepare_creates_missing_folders_for_every_system(tmp_path):
    report = prepare_rom_folders(tmp_path)
    # Every built-in system gets a folder created.
    assert report.created_count == len(SYSTEM_ROM_DIRS)
    assert report.existing == []
    assert report.errors == []
    # Specifically: the first candidate (lowercase, EmuDeck convention)
    # is the one created.
    for system, candidates in SYSTEM_ROM_DIRS.items():
        assert (tmp_path / candidates[0]).is_dir(), system


def test_prepare_leaves_existing_alias_folders_alone(tmp_path):
    # User's existing ``psx`` folder for PS1 should be left untouched —
    # we don't create a second ``psx`` and don't pick a different alias.
    (tmp_path / "psx").mkdir()
    # Also a Title-Case alias for another system.
    (tmp_path / "PlayStation 2").mkdir()
    report = prepare_rom_folders(tmp_path)

    # PS1 counted as existing, PS2 counted as existing.
    existing_systems = {s for s, _ in report.existing}
    assert "PS1" in existing_systems
    assert "PS2" in existing_systems
    # No duplicate folder got created under another PS1 alias.
    assert not (tmp_path / "PS1").exists()
    assert not (tmp_path / "PlayStation").exists()
    assert not (tmp_path / "ps2").exists()
    # And the created list excludes the existing ones.
    created_systems = {s for s, _ in report.created}
    assert "PS1" not in created_systems
    assert "PS2" not in created_systems


def test_prepare_respects_per_system_overrides(tmp_path):
    custom = tmp_path / "external" / "sega-saturn"
    overrides = {"SAT": str(custom)}
    report = prepare_rom_folders(tmp_path, overrides)

    # The override target is created instead of the default ``saturn``.
    assert custom.is_dir()
    assert not (tmp_path / "saturn").exists()
    created_targets = {p for _, p in report.created}
    assert custom in created_targets


def test_prepare_canonicalises_alias_override_keys(tmp_path):
    # Legacy-keyed overrides (``SCD``) should land on the canonical
    # system folder after canonicalisation, not on a separate "SCD"
    # entry.
    custom = tmp_path / "external" / "segacd"
    report = prepare_rom_folders(tmp_path, {"SCD": str(custom)})
    assert custom.is_dir()
    created_systems = {s for s, _ in report.created}
    assert "SEGACD" in created_systems


def test_prepare_reports_existing_and_created_exclusively(tmp_path):
    # One existing alias + one missing.
    (tmp_path / "gba").mkdir()
    report = prepare_rom_folders(tmp_path)
    created_systems = {s for s, _ in report.created}
    existing_systems = {s for s, _ in report.existing}
    assert created_systems & existing_systems == set()
    # GBA is the existing one; PS1 (missing) is in created.
    assert "GBA" in existing_systems
    assert "PS1" in created_systems


def test_prepare_is_idempotent(tmp_path):
    first = prepare_rom_folders(tmp_path)
    second = prepare_rom_folders(tmp_path)
    # Re-running after a successful prepare should create nothing new.
    assert first.created_count > 0
    assert second.created_count == 0
    assert len(second.existing) == first.created_count


def test_prepare_records_errors_when_target_is_a_file(tmp_path):
    # Simulate a permission / FS error by putting a regular file where
    # a system folder should go.  mkdir(parents=True, exist_ok=True)
    # raises FileExistsError because the path exists but isn't a dir.
    (tmp_path / "psx").write_text("not a folder")
    report = prepare_rom_folders(tmp_path)
    err_systems = {s for s, _ in report.errors}
    assert "PS1" in err_systems


# ---------------------------------------------------------------------------
# SyncClient.plan_rom_download
# ---------------------------------------------------------------------------


def test_plan_rom_download_prefers_decrypted_cci_for_3ds():
    client = SyncClient("localhost", 8000, "key")
    filename, extract = client.plan_rom_download(
        {
            "rom_id": "3ds-1",
            "system": "3DS",
            "filename": "Super Mario 3D Land (USA).3ds.zip",
            "extract_format": "3ds",
            "extract_formats": ["cia", "decrypted_cci"],
        },
        "3DS",
    )

    # Filename preserves the ROM stem verbatim so save-folder detection on
    # the Deck keeps working — no `_decrypted` marker.
    assert filename == "Super Mario 3D Land (USA).cci"
    assert extract == "decrypted_cci"


def test_plan_rom_download_ignores_legacy_3ds_hint_without_real_formats():
    client = SyncClient("localhost", 8000, "key")
    filename, extract = client.plan_rom_download(
        {
            "rom_id": "3ds-1",
            "system": "3DS",
            "filename": "Pilotwings Resort (USA).3ds",
            "extract_format": "3ds",
        },
        "3DS",
    )

    assert filename == "Pilotwings Resort (USA).3ds"
    assert extract is None


def test_plan_rom_download_does_not_fall_back_to_3ds_cia_outputs():
    # If the server has a CIA wrapper configured but NOT a decrypted-CCI
    # wrapper, the desktop emulator client falls back to the raw .3ds/.cci
    # cart image rather than downloading a CIA it can't boot.
    client = SyncClient("localhost", 8000, "key")
    filename, extract = client.plan_rom_download(
        {
            "rom_id": "3ds-1",
            "system": "3DS",
            "filename": "Pilotwings Resort (USA).3ds.zip",
            "extract_format": "3ds",
            "extract_formats": ["cia"],
        },
        "3DS",
    )

    assert filename == "Pilotwings Resort (USA).3ds.zip"
    assert extract is None


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


def test_download_rom_uses_long_read_timeout_for_chd_extraction(monkeypatch, tmp_path):
    """Server-side CHD/RVZ extraction can run for minutes before the first
    byte arrives, so the client must not apply the tight 10-second timeout
    it uses for the rest of the API."""
    captured = {}

    def fake_get(url, params=None, headers=None, stream=None, timeout=None):
        captured["timeout"] = timeout
        return _FakeResponse(200, b"payload", content_length=7)

    monkeypatch.setattr(sync_client_mod.requests, "get", fake_get)
    client = SyncClient("localhost", 8000, "key")
    target = tmp_path / "PS1" / "dino.chd"

    assert client.download_rom("SLUS00922", target) is True
    # Tuple form (connect, read): connect stays tight for fast-fail on
    # unreachable servers, read is large enough to survive chdman.
    assert isinstance(captured["timeout"], tuple) and len(captured["timeout"]) == 2
    connect_to, read_to = captured["timeout"]
    assert connect_to >= 10 and read_to >= 300


def test_download_rom_records_http_error_body_for_the_ui(monkeypatch, tmp_path):
    """When the server returns 503 ('chdman not installed'), the user
    should see that message in the failure dialog, not a bare
    'Download failed.'"""

    class _WithText(_FakeResponse):
        def __init__(self):
            super().__init__(503, b"")
            self.text = "chdman not installed. Run: sudo apt install mame-tools"

    monkeypatch.setattr(sync_client_mod.requests, "get", lambda *a, **kw: _WithText())
    client = SyncClient("localhost", 8000, "key")
    assert client.download_rom("SLUS00922", tmp_path / "a.chd") is False
    assert "chdman" in client.last_download_error
    assert "503" in client.last_download_error


def test_download_rom_records_timeout_as_actionable_message(monkeypatch, tmp_path):
    import requests as _requests

    def fake_get(*a, **kw):
        raise _requests.exceptions.Timeout("took too long")

    monkeypatch.setattr(sync_client_mod.requests, "get", fake_get)
    client = SyncClient("localhost", 8000, "key")
    assert client.download_rom("SLUS00922", tmp_path / "a.chd") is False
    # The message should explain the CHD extraction wait, not just echo
    # "took too long".
    assert "extract" in client.last_download_error.lower() or \
        "several minutes" in client.last_download_error.lower()


def test_download_rom_resets_last_error_on_success(monkeypatch, tmp_path):
    _install_fake_get(monkeypatch, lambda: _FakeResponse(200, b"ok", content_length=2))
    client = SyncClient("localhost", 8000, "key")
    client.last_download_error = "stale from a prior failure"
    assert client.download_rom("SLUS00922", tmp_path / "a.chd") is True
    assert client.last_download_error == ""


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


# ---------------------------------------------------------------------------
# plan_rom_download — Xbox ISO for xemu
# ---------------------------------------------------------------------------


def test_plan_rom_download_xbox_cci_requests_iso():
    """Xbox .cci source must be converted to ISO since xemu can't load CCI."""
    client = SyncClient("localhost", 8000, "key")
    filename, extract = client.plan_rom_download(
        {
            "rom_id": "xbox-halo",
            "system": "XBOX",
            "filename": "Halo - Combat Evolved.cci",
            "extract_format": "xbox",
            "extract_formats": ["cci", "iso", "folder"],
        },
        "XBOX",
    )
    assert filename == "Halo - Combat Evolved.iso"
    assert extract == "iso"


def test_plan_rom_download_xbox_iso_source_no_conversion():
    """Xbox .iso source needs no conversion — download raw."""
    client = SyncClient("localhost", 8000, "key")
    filename, extract = client.plan_rom_download(
        {
            "rom_id": "xbox-halo",
            "system": "XBOX",
            "filename": "Halo - Combat Evolved.iso",
        },
        "XBOX",
    )
    assert filename == "Halo - Combat Evolved.iso"
    assert extract is None


def test_plan_rom_download_x360_requests_iso():
    """X360 entries also request ISO for xemu compatibility."""
    client = SyncClient("localhost", 8000, "key")
    filename, extract = client.plan_rom_download(
        {
            "rom_id": "x360-gears",
            "system": "X360",
            "filename": "Gears of War.cci",
            "extract_formats": ["cci", "iso", "folder"],
        },
        "X360",
    )
    assert filename == "Gears of War.iso"
    assert extract == "iso"
