"""Coverage for the ROM-conversion output cache.

The cache is the difference between "instant" and "seven minutes per
attempt" for repeat downloads of the same converted ROM (3DS in
particular — Pi can't beat ~250s of pure AES decrypt CPU time).  These
tests pin down the contract callers depend on:

    1. Cache disabled when settings.tmp_dir is unset
    2. Cache key is stable for the same (source, format) combo
    3. Cache key changes when source mtime/size/format change
    4. Hit returns the cached path; miss returns None
    5. _save_to_cache moves the file into the cache and returns the new path
    6. _save_to_cache no-ops gracefully when caching is disabled
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.config import settings
from app.routes.roms import (
    _conversion_cache_dir,
    _conversion_cache_key,
    _cached_output_path,
    _lookup_cached_output,
    _save_to_cache,
)


@pytest.fixture
def restore_tmp_dir():
    original = settings.tmp_dir
    yield
    settings.tmp_dir = original


@pytest.fixture
def configured_tmp(restore_tmp_dir, tmp_path) -> Path:
    """Point settings.tmp_dir at a writable test directory."""
    target = tmp_path / "rom_tmp"
    settings.tmp_dir = target
    return target


@pytest.fixture
def fake_source(tmp_path) -> Path:
    """A small file standing in for a ROM source (we never run the actual
    converter — these tests are about path resolution + move semantics)."""
    src = tmp_path / "fake_rom.3ds"
    src.write_bytes(b"x" * 1024)
    return src


# ── disabled cache (no tmp_dir) ─────────────────────────────────────────────

def test_cache_dir_returns_none_when_no_tmp_dir(restore_tmp_dir):
    settings.tmp_dir = None
    assert _conversion_cache_dir() is None


def test_lookup_returns_none_when_caching_disabled(restore_tmp_dir, fake_source):
    settings.tmp_dir = None
    assert _lookup_cached_output(fake_source, "decrypted_cci", ".cci") is None


def test_save_to_cache_no_ops_when_disabled(restore_tmp_dir, tmp_path, fake_source):
    """A misconfigured server should still serve the converted file — just
    without the cache benefit.  Caller gets the original path back."""
    settings.tmp_dir = None
    temp_output = tmp_path / "fresh_output.cci"
    temp_output.write_bytes(b"y" * 2048)

    result = _save_to_cache(temp_output, fake_source, "decrypted_cci", ".cci")

    assert result == temp_output
    assert temp_output.is_file()  # not moved


# ── enabled cache ────────────────────────────────────────────────────────────

def test_cache_dir_creates_subdir_under_tmp_dir(configured_tmp):
    cache = _conversion_cache_dir()
    assert cache is not None
    assert cache == configured_tmp / "_conversion_cache"
    assert cache.is_dir()


def test_cache_key_is_stable_for_unchanged_source(configured_tmp, fake_source):
    """Same (source, fmt) produces same key — that's the whole point."""
    k1 = _conversion_cache_key(fake_source, "decrypted_cci")
    k2 = _conversion_cache_key(fake_source, "decrypted_cci")
    assert k1 == k2
    assert len(k1) == 16


def test_cache_key_changes_when_source_size_changes(configured_tmp, fake_source):
    """If a user re-downloads a different dump of the same game (different
    bytes), the cache must invalidate so they don't get the old conversion."""
    k1 = _conversion_cache_key(fake_source, "decrypted_cci")
    fake_source.write_bytes(b"z" * 4096)  # different size
    k2 = _conversion_cache_key(fake_source, "decrypted_cci")
    assert k1 != k2


def test_cache_key_changes_when_format_changes(configured_tmp, fake_source):
    """CIA and decrypted_cci of the same source produce different files —
    they can't share a cache slot."""
    k_cia = _conversion_cache_key(fake_source, "cia")
    k_cci = _conversion_cache_key(fake_source, "decrypted_cci")
    assert k_cia != k_cci


def test_lookup_returns_none_on_cache_miss(configured_tmp, fake_source):
    assert _lookup_cached_output(fake_source, "decrypted_cci", ".cci") is None


def test_save_then_lookup_round_trip(configured_tmp, tmp_path, fake_source):
    """Producer-consumer flow: a request runs the conversion + saves to
    cache; a follow-up request finds the cached file."""
    temp_output = tmp_path / "fresh_output.cci"
    temp_output.write_bytes(b"converted_content_here")

    # Round 1 — save the conversion result into the cache.
    cached = _save_to_cache(temp_output, fake_source, "decrypted_cci", ".cci")

    assert cached.is_file()
    assert cached.read_bytes() == b"converted_content_here"
    # Source temp output was MOVED into the cache (not copied), no leftover.
    assert not temp_output.exists()

    # Round 2 — a fresh request finds the cached output.
    looked_up = _lookup_cached_output(fake_source, "decrypted_cci", ".cci")
    assert looked_up == cached


def test_cache_invalidates_on_source_change_round_trip(configured_tmp, tmp_path, fake_source):
    """Source bytes change → cache miss on the next lookup.  Stale entries
    are still on disk (no eviction), but the new request goes through the
    full conversion path because the key differs."""
    out1 = tmp_path / "out1.cci"
    out1.write_bytes(b"first_conversion")
    cached1 = _save_to_cache(out1, fake_source, "decrypted_cci", ".cci")

    # Simulate the user replacing the source ROM with a different dump.
    fake_source.write_bytes(b"x" * 99999)  # different size → different mtime + size
    # Ensure mtime moves forward even on coarse-resolution filesystems.
    new_mtime = fake_source.stat().st_mtime + 5
    os.utime(fake_source, (new_mtime, new_mtime))

    # Lookup with the changed source must miss (different key).
    assert _lookup_cached_output(fake_source, "decrypted_cci", ".cci") is None
    # The first conversion's cached file is still on disk under the old key.
    assert cached1.is_file()


def test_predicted_path_includes_source_stem(configured_tmp, fake_source):
    """The cache file's name should embed the source stem — so a human
    inspecting the cache can recognise which game each file is from."""
    predicted = _cached_output_path(fake_source, "decrypted_cci", ".cci")
    assert predicted is not None
    assert "fake_rom" in predicted.name
    assert predicted.name.endswith(".cci")
