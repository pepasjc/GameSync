"""Confirm ``settings.tmp_dir`` actually controls where conversion workdirs land.

This guards against the regression that bit us on the Pi: ``TMPDIR`` had been
stripped by uv's bundled python-build-standalone interpreter, so even a
correctly-set env var didn't reach ``tempfile.mkdtemp``.  By passing ``dir=``
explicitly from a setting we sidestep that entire class of failure.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app.config import settings
from app.routes.roms import _conversion_tmp_dir as roms_conversion_tmp_dir
from app.services.mcr2vmp_tool import _conversion_tmp_dir as mcr2vmp_conversion_tmp_dir


@pytest.fixture
def restore_tmp_dir():
    """Snapshot + restore ``settings.tmp_dir`` so tests don't leak state."""
    original = settings.tmp_dir
    yield
    settings.tmp_dir = original


def test_helper_returns_none_when_unset(restore_tmp_dir):
    """When the user hasn't configured a tmp dir we must return ``None`` so
    ``tempfile.mkdtemp`` falls back to the system default — same behaviour as
    before the setting existed."""
    settings.tmp_dir = None
    assert roms_conversion_tmp_dir() is None
    assert mcr2vmp_conversion_tmp_dir() is None


def test_helper_returns_configured_path_as_string(restore_tmp_dir, tmp_path):
    """When set, the helper returns the path as a string (what
    ``tempfile.mkdtemp(dir=...)`` expects)."""
    target = tmp_path / "rom_tmp"
    settings.tmp_dir = target

    result = roms_conversion_tmp_dir()

    assert result == str(target)
    # And it created the directory so a subsequent mkdtemp won't fail
    assert target.exists() and target.is_dir()


def test_helper_creates_missing_intermediate_dirs(restore_tmp_dir, tmp_path):
    """A multi-level path that doesn't exist yet should be created."""
    target = tmp_path / "deep" / "nested" / "tmp"
    settings.tmp_dir = target

    assert roms_conversion_tmp_dir() == str(target)
    assert target.exists() and target.is_dir()


def test_helper_falls_back_to_none_when_path_unwritable(restore_tmp_dir, tmp_path):
    """A misconfigured path (parent is a regular file, can't be a dir) must
    NOT crash the request — the helper returns None so ``mkdtemp`` falls
    back to the system default and the conversion still runs."""
    blocker = tmp_path / "i_am_a_file"
    blocker.write_text("not a directory")
    target = blocker / "tmp"  # would need to mkdir under a regular file
    settings.tmp_dir = target

    # Should NOT raise — just returns None for the safe fallback.
    assert roms_conversion_tmp_dir() is None


def test_mkdtemp_actually_lands_in_configured_dir(restore_tmp_dir, tmp_path):
    """End-to-end smoke check: feed the helper into ``tempfile.mkdtemp`` and
    confirm the workdir lands in our configured location, not the system
    default.  This is the behaviour the conversion endpoints depend on."""
    target = tmp_path / "rom_tmp"
    settings.tmp_dir = target

    workdir = tempfile.mkdtemp(prefix="test_extract_", dir=roms_conversion_tmp_dir())

    try:
        workdir_path = Path(workdir)
        assert workdir_path.parent == target
        assert workdir_path.name.startswith("test_extract_")
    finally:
        Path(workdir).rmdir()


def test_mkdtemp_falls_back_to_system_default_when_unset(restore_tmp_dir):
    """With no setting, ``mkdtemp`` should use ``tempfile.gettempdir()``.
    We don't assert the exact path (it varies by host) — just that the
    workdir doesn't end up under any configured location."""
    settings.tmp_dir = None

    workdir = tempfile.mkdtemp(prefix="test_extract_", dir=roms_conversion_tmp_dir())

    try:
        # Should land under the system default temp dir, whatever that is.
        assert Path(workdir).parent == Path(tempfile.gettempdir())
    finally:
        Path(workdir).rmdir()
