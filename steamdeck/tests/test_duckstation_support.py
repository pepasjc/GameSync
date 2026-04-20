"""Regression tests for the DuckStation PS1 scanner."""

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
STEAMDECK_ROOT = ROOT / "steamdeck"
if str(STEAMDECK_ROOT) not in sys.path:
    sys.path.insert(0, str(STEAMDECK_ROOT))

from scanner import duckstation  # noqa: E402


def _write(path: Path, content: bytes = b"") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_scan_dedupes_region_tagged_card_with_serial_rom(monkeypatch, tmp_path):
    """
    Card on disk: "Final Fantasy VII (USA)_1.mcd"
    ROM on disk: "Final Fantasy VII (USA) (Disc 1).bin" with serial SCUS94163

    Pass A creates a slug-backed placeholder from the card filename, Pass B
    finds the serial from the ROM.  The serial entry must supersede the slug
    entry — previously the region tag in the card stem prevented the stale
    slug from being dropped and both entries surfaced as duplicates.
    """
    emulation = tmp_path / "Emulation"
    memcards = emulation / "saves" / "duckstation" / "memcards"
    roms_dir = emulation / "roms" / "PS1"
    memcards.mkdir(parents=True)
    roms_dir.mkdir(parents=True)

    card = _write(memcards / "Final Fantasy VII (USA)_1.mcd", b"card")
    rom = _write(roms_dir / "Final Fantasy VII (USA) (Disc 1).bin", b"rom")

    monkeypatch.setattr(
        duckstation, "read_ps1_serial", lambda p: "SCUS94163" if p == rom else None
    )

    results = list(duckstation.scan(emulation))

    assert len(results) == 1, [r.title_id for r in results]
    entry = results[0]
    assert entry.title_id == "SCUS94163"
    assert entry.save_path == card
    assert entry.rom_path == rom


def test_scan_keeps_card_only_entry_when_no_rom_match(tmp_path):
    """Card with no matching ROM should still yield one slug-backed entry."""
    emulation = tmp_path / "Emulation"
    memcards = emulation / "saves" / "duckstation" / "memcards"
    memcards.mkdir(parents=True)

    _write(memcards / "Chrono Cross (USA)_1.mcd", b"card")

    results = list(duckstation.scan(emulation))

    assert len(results) == 1
    assert results[0].title_id.startswith("PS1_chrono_cross")
    assert results[0].save_path is not None


def test_scan_serial_filename_card_matches_rom_serial(monkeypatch, tmp_path):
    """
    A card already named by serial (e.g. "SLUS01234_1.mcd") plus a ROM whose
    SYSTEM.CNF reports the same serial should collapse to one entry keyed by
    the bare serial.
    """
    emulation = tmp_path / "Emulation"
    memcards = emulation / "saves" / "duckstation" / "memcards"
    roms_dir = emulation / "roms" / "PS1"
    memcards.mkdir(parents=True)
    roms_dir.mkdir(parents=True)

    card = _write(memcards / "SLUS01234_1.mcd", b"card")
    rom = _write(roms_dir / "Some Game.bin", b"rom")

    monkeypatch.setattr(
        duckstation, "read_ps1_serial", lambda p: "SLUS01234" if p == rom else None
    )

    results = list(duckstation.scan(emulation))

    assert len(results) == 1
    entry = results[0]
    assert entry.title_id == "SLUS01234"
    assert entry.save_path == card
    assert entry.rom_path == rom
