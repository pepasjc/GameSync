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


def test_scan_dedupes_card_named_simply_vs_rom_with_region_tags(monkeypatch, tmp_path):
    """The previous dedup compared card display_name to a cleaned ROM label —
    that failed whenever the card's filename did NOT mirror the ROM's tags
    (e.g. card "Crash_1.mcd" vs ROM "Crash Bandicoot (USA).bin").  The flipped
    ROM-first scan matches cards by a fully normalised name, so this now
    collapses to a single serial-keyed entry."""
    emulation = tmp_path / "Emulation"
    memcards = emulation / "saves" / "duckstation" / "memcards"
    roms_dir = emulation / "roms" / "PS1"
    memcards.mkdir(parents=True)
    roms_dir.mkdir(parents=True)

    card = _write(memcards / "Crash Bandicoot_1.mcd", b"card")
    rom = _write(roms_dir / "Crash Bandicoot (USA) (Rev 1).bin", b"rom")

    monkeypatch.setattr(
        duckstation, "read_ps1_serial", lambda p: "SCUS94900" if p == rom else None
    )

    results = list(duckstation.scan(emulation))
    assert len(results) == 1, [r.title_id for r in results]
    assert results[0].title_id == "SCUS94900"
    assert results[0].save_path == card


def test_scan_merges_multi_disc_roms_under_single_entry(monkeypatch, tmp_path):
    """Both discs of a multi-disc game share a serial — the ROM pass must
    collapse them so the card attaches to a single row, not one per disc."""
    emulation = tmp_path / "Emulation"
    memcards = emulation / "saves" / "duckstation" / "memcards"
    roms_dir = emulation / "roms" / "PS1"
    memcards.mkdir(parents=True)
    roms_dir.mkdir(parents=True)

    disc1 = _write(roms_dir / "Final Fantasy VII (USA) (Disc 1).bin", b"d1")
    disc2 = _write(roms_dir / "Final Fantasy VII (USA) (Disc 2).bin", b"d2")
    _write(memcards / "Final Fantasy VII_1.mcd", b"card")

    monkeypatch.setattr(duckstation, "read_ps1_serial", lambda p: "SCUS94163")

    results = list(duckstation.scan(emulation))
    assert len(results) == 1
    assert results[0].title_id == "SCUS94163"
    # rom_path is whichever disc was encountered first (deterministic sort)
    assert results[0].rom_path in {disc1, disc2}


def test_scan_card_only_entry_has_no_rom(monkeypatch, tmp_path):
    """A card whose game has no ROM on disk should still surface as its own
    entry and should not be accidentally matched to an unrelated ROM."""
    emulation = tmp_path / "Emulation"
    memcards = emulation / "saves" / "duckstation" / "memcards"
    roms_dir = emulation / "roms" / "PS1"
    memcards.mkdir(parents=True)
    roms_dir.mkdir(parents=True)

    _write(memcards / "Some Game (USA)_1.mcd", b"card")
    other_rom = _write(roms_dir / "Different Game (USA).bin", b"other")

    monkeypatch.setattr(
        duckstation,
        "read_ps1_serial",
        lambda p: "SCUS00042" if p == other_rom else None,
    )

    results = list(duckstation.scan(emulation))
    tids = {r.title_id for r in results}
    # One serial-keyed entry for the ROM, one slug-keyed entry for the card.
    assert "SCUS00042" in tids
    assert any(t.startswith("PS1_some_game") for t in tids)
    assert len(results) == 2


def test_scan_dino_crisis_duplicate_collapses_to_serial_and_duckstation_path(
    monkeypatch, tmp_path
):
    """The exact duplicate the user reported pre-fix:

        Dino Crisis 2              (title_id: SLUS01279)  ← from the ROM
        Dino Crisis 2 (USA)        (title_id: PS1_dino_crisis_2_usa)  ← from card

    must collapse to ONE serial-keyed entry with the save_path set to the
    path DuckStation writes: ``<memcards>/Dino Crisis 2_1.mcd``.  The card
    name must NOT include the region tag — that's what breaks round-trip
    sync with the actual emulator.
    """
    emulation = tmp_path / "Emulation"
    memcards = emulation / "saves" / "duckstation" / "memcards"
    roms_dir = emulation / "roms" / "PS1"
    memcards.mkdir(parents=True)
    roms_dir.mkdir(parents=True)

    card = _write(memcards / "Dino Crisis 2_1.mcd", b"existing-card")
    rom = _write(roms_dir / "Dino Crisis 2 (USA).bin", b"rom")

    monkeypatch.setattr(
        duckstation, "read_ps1_serial", lambda p: "SLUS01279" if p == rom else None
    )

    results = list(duckstation.scan(emulation))
    assert len(results) == 1, [r.title_id for r in results]
    entry = results[0]
    assert entry.title_id == "SLUS01279"
    assert entry.save_path == card
    assert entry.rom_path == rom


def test_scan_rom_without_card_gets_duckstation_write_path(monkeypatch, tmp_path):
    """If the user has the ROM but no memory card yet, the entry must still
    carry the DuckStation-expected write path so a server Download Save can
    land there.  DuckStation's per-game cards inherit the ROM's redump
    title (which includes region tags), so the predicted path preserves
    ``(USA)`` / ``(Europe)`` / ``(Japan)`` from the ROM filename."""
    emulation = tmp_path / "Emulation"
    memcards = emulation / "saves" / "duckstation" / "memcards"
    roms_dir = emulation / "roms" / "PS1"
    memcards.mkdir(parents=True)
    roms_dir.mkdir(parents=True)

    rom = _write(roms_dir / "Dino Crisis 2 (USA).bin", b"rom")
    monkeypatch.setattr(
        duckstation, "read_ps1_serial", lambda p: "SLUS01279" if p == rom else None
    )

    results = list(duckstation.scan(emulation))
    assert len(results) == 1
    entry = results[0]
    assert entry.title_id == "SLUS01279"
    # User sees the full ROM title in the UI — same as the server-only
    # rows, so the two stay consistent when the user has the ROM locally.
    assert entry.display_name == "Dino Crisis 2 (USA)"
    # save_path points at the exact file DuckStation would create on first
    # launch: ROM-title-based card name with region preserved.
    assert entry.save_path == memcards / "Dino Crisis 2 (USA)_1.mcd"
    assert not entry.save_path.exists()
    # No content yet -> no hash; status logic will treat this as SERVER_ONLY
    # when the server has a save for SLUS01279.
    assert entry.save_hash is None


def test_scan_local_rom_preserves_region_in_display_name_and_card_path(
    monkeypatch, tmp_path
):
    """Regression: a local ROM named ``Tekken 3 (USA).chd`` used to render
    as ``Tekken 3`` in the UI (region stripped) and the predicted save
    location pointed at ``Tekken 3_1.mcd`` — but DuckStation actually
    writes per-game cards as ``Tekken 3 (USA)_1.mcd`` (the redump DB title
    is what drives the card name).  A server-downloaded save therefore
    landed at the wrong filename and the user's next boot saw no save."""
    emulation = tmp_path / "Emulation"
    memcards = emulation / "saves" / "duckstation" / "memcards"
    roms_dir = emulation / "roms" / "PS1"
    memcards.mkdir(parents=True)
    roms_dir.mkdir(parents=True)

    # CHD can't be parsed by our lightweight ISO reader, so the title_id
    # falls back to to_ps1_title_id(label) — this is exactly the path the
    # user reported the bug on.
    _write(roms_dir / "Tekken 3 (USA).chd", b"rom-bytes")

    results = list(duckstation.scan(emulation))
    assert len(results) == 1
    entry = results[0]
    assert entry.display_name == "Tekken 3 (USA)"
    assert entry.save_path == memcards / "Tekken 3 (USA)_1.mcd"
    # The title_id slug itself already preserved the region (to_ps1_title_id)
    # so this stays stable under the fix.
    assert entry.title_id.endswith("_usa")


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
