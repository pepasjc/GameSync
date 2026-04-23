"""Regression tests for the melonDS NDS scanner.

The scanner's primary contract: when the matching NDS ROM is present on the
device the save must be yielded under the canonical hex title_id
(``00048000`` + gamecode, matching the 3DS/NDS homebrew & Android clients).
When the ROM is absent the scanner falls back to the legacy slug id so saves
are never dropped.
"""

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
STEAMDECK_ROOT = ROOT / "steamdeck"
if str(STEAMDECK_ROOT) not in sys.path:
    sys.path.insert(0, str(STEAMDECK_ROOT))

from scanner import melonds  # noqa: E402
from scanner.base import nds_gamecode_to_title_id  # noqa: E402


def _write(path: Path, content: bytes = b"") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _nds_rom(path: Path, gamecode: str) -> Path:
    """Create a minimal .nds file with the given 4-char gamecode at 0x0C."""
    assert len(gamecode) == 4
    header = bytearray(0x100)
    header[0x0C:0x10] = gamecode.encode("ascii")
    return _write(path, bytes(header))


def test_nds_gamecode_to_title_id_matches_homebrew_format():
    # Gamecode "AMKJ" (Mario Kart DS, Japan) → "00048000" + hex("AMKJ")
    assert nds_gamecode_to_title_id("AMKJ") == "00048000414D4B4A"
    # Gamecode "ASME" (Super Mario 64 DS, USA)
    assert nds_gamecode_to_title_id("ASME") == "0004800041534D45"


def test_nds_gamecode_to_title_id_rejects_bad_input():
    assert nds_gamecode_to_title_id("") is None
    assert nds_gamecode_to_title_id("ABC") is None
    assert nds_gamecode_to_title_id("ABCDE") is None
    # Non-printable bytes are rejected
    assert nds_gamecode_to_title_id("A\x00CD") is None


def test_scan_prefers_gamecode_when_rom_is_present(tmp_path):
    emulation = tmp_path / "Emulation"
    saves_dir = emulation / "saves" / "melonds"
    roms_dir = emulation / "roms" / "nds"
    saves_dir.mkdir(parents=True)
    roms_dir.mkdir(parents=True)

    _write(saves_dir / "Super Mario 64 DS (USA).sav", b"save-bytes")
    _nds_rom(roms_dir / "Super Mario 64 DS (USA).nds", "ASME")

    results = list(melonds.scan(emulation))

    assert len(results) == 1
    entry = results[0]
    assert entry.title_id == "00048000" + "41534D45"  # hex("ASME")
    assert entry.system == "NDS"
    assert entry.emulator == "melonDS"
    assert entry.rom_path is not None
    assert entry.rom_path.name == "Super Mario 64 DS (USA).nds"


def test_scan_falls_back_to_slug_when_rom_missing(tmp_path):
    emulation = tmp_path / "Emulation"
    saves_dir = emulation / "saves" / "melonds"
    saves_dir.mkdir(parents=True)

    _write(saves_dir / "Obscure Homebrew Game.sav", b"save-bytes")

    results = list(melonds.scan(emulation))

    assert len(results) == 1
    entry = results[0]
    # Slug preserves legacy behaviour so the save is still discoverable.
    assert entry.title_id.startswith("NDS_")
    assert "obscure_homebrew_game" in entry.title_id
    assert entry.rom_path is None


def test_scan_uses_rom_scan_dir_for_gamecode_lookup(tmp_path):
    emulation = tmp_path / "Emulation"
    saves_dir = emulation / "saves" / "melonds"
    external_roms = tmp_path / "external" / "NDS"
    saves_dir.mkdir(parents=True)
    external_roms.mkdir(parents=True)

    _write(saves_dir / "Mario Kart DS (Japan).sav", b"save-bytes")
    _nds_rom(external_roms / "Mario Kart DS (Japan).nds", "AMKJ")

    # Pass external roms via rom_scan_dir — emulation/roms has no NDS dir
    results = list(
        melonds.scan(emulation, rom_scan_dir=str(tmp_path / "external"))
    )

    assert len(results) == 1
    assert results[0].title_id == "00048000414D4B4A"


def test_scan_matches_ds_roms_case_insensitively(tmp_path):
    emulation = tmp_path / "Emulation"
    saves_dir = emulation / "saves" / "melonds"
    roms_dir = emulation / "roms" / "nds"
    saves_dir.mkdir(parents=True)
    roms_dir.mkdir(parents=True)

    # Save stem lower-case, ROM stem title case — should still match.
    _write(saves_dir / "pokemon diamond (usa).sav", b"s")
    _nds_rom(roms_dir / "Pokemon Diamond (USA).nds", "ADAE")

    results = list(melonds.scan(emulation))

    assert len(results) == 1
    assert results[0].title_id == "00048000" + "41444145"  # hex("ADAE")


def test_scan_dedupes_saves_sharing_a_title_id(tmp_path):
    """Two saves with the same canonical title_id should only yield once."""
    emulation = tmp_path / "Emulation"
    saves_dir = emulation / "saves" / "melonds"
    flatpak_saves = Path.home() / ".var/app/net.kuribo64.melonDS/data/melonDS"
    roms_dir = emulation / "roms" / "nds"
    saves_dir.mkdir(parents=True)
    roms_dir.mkdir(parents=True)

    _write(saves_dir / "Mario Kart DS (USA).sav", b"v1")
    _nds_rom(roms_dir / "Mario Kart DS (USA).nds", "AMCE")

    # Even if a stray duplicate with a different stem exists, the canonical
    # title_id guards against duplicates.
    _write(saves_dir / "Mario Kart DS.sav", b"v2")
    _nds_rom(roms_dir / "Mario Kart DS.nds", "AMCE")

    results = list(melonds.scan(emulation))

    # Both saves map to the same canonical title_id; the scanner keeps the
    # first occurrence and drops the duplicate.
    title_ids = [r.title_id for r in results]
    assert title_ids.count("00048000" + "414D4345") == 1  # hex("AMCE")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
