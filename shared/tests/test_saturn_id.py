"""Tests for shared.rom_id.saturn — single source of truth for Saturn IDs."""

from __future__ import annotations

import struct
from pathlib import Path
from textwrap import dedent

import pytest

from shared.rom_id.saturn import (
    lookup_saturn_serial_in_dat,
    parse_saturn_dat,
    read_saturn_product_code,
    resolve_saturn_title_id,
)


# ---------------------------------------------------------------------------
# DAT parsing + lookup
# ---------------------------------------------------------------------------


SAMPLE_DAT = dedent(
    """\
    clrmamepro (
        name "Sega - Saturn"
    )

    game (
        name "Grandia (Japan) (Disc 1) (4M)"
        region "Japan"
        serial "T-4507G"
        rom ( name "Grandia.bin" serial "T-4507G" )
    )
    game (
        name "Panzer Dragoon Saga (USA) (Disc 1)"
        region "USA"
        serial "81307"
        rom ( name "PDS.bin" serial "81307" )
    )
    game (
        name "Panzer Dragoon Saga (USA) (Disc 1)"
        region "USA"
        serial "81307-0"
        rom ( name "PDS.bin" serial "81307-0" )
    )
    """
)


def test_parse_dat_extracts_canonical_serial_per_game():
    parsed = parse_saturn_dat(SAMPLE_DAT)
    assert parsed["grandia (japan) (disc 1) (4m)"] == "T-4507G"
    # Disc-index variant "81307-0" is skipped; the canonical "81307" wins.
    assert parsed["panzer dragoon saga (usa) (disc 1)"] == "81307"


def test_lookup_exact_match():
    parsed = parse_saturn_dat(SAMPLE_DAT)
    assert lookup_saturn_serial_in_dat("Grandia (Japan) (Disc 1) (4M)", parsed) == "T-4507G"


def test_lookup_progressive_strip_matches_parent_entry():
    parsed = parse_saturn_dat(SAMPLE_DAT)
    # "(4M)" is not in the DAT as a bare group — stripping it finds "(Japan) (Disc 1)".
    assert (
        lookup_saturn_serial_in_dat("Grandia (Japan) (Disc 1) (4M) (Extra)", parsed)
        == "T-4507G"
    )


def test_lookup_strips_bracket_tags_before_matching():
    parsed = parse_saturn_dat(SAMPLE_DAT)
    assert (
        lookup_saturn_serial_in_dat(
            "Grandia (Japan) (Disc 1) (4M) [T-En by TrekkiesUnite118 v0.9.3 RC]",
            parsed,
        )
        == "T-4507G"
    )


def test_lookup_returns_none_when_no_match():
    parsed = parse_saturn_dat(SAMPLE_DAT)
    assert lookup_saturn_serial_in_dat("Completely Unknown Game (USA)", parsed) is None


# ---------------------------------------------------------------------------
# IP.BIN reader
# ---------------------------------------------------------------------------


def _build_saturn_iso_sector(product_code: str) -> bytes:
    """
    Build a minimal Saturn disc sector: "SEGA SEGASATURN " magic at byte 0,
    product code left-padded into the 10-byte field at byte 0x20.
    """
    buf = bytearray(0x30)
    magic = b"SEGA SEGASATURN "
    buf[: len(magic)] = magic
    code_bytes = product_code.encode("ascii").ljust(10, b" ")[:10]
    buf[0x20:0x2A] = code_bytes
    return bytes(buf)


def test_read_saturn_product_code_iso(tmp_path):
    iso = tmp_path / "Shining Force III (USA).iso"
    iso.write_bytes(_build_saturn_iso_sector("MK-81070  "))
    assert read_saturn_product_code(iso) == "SAT_MK-81070"


def test_read_saturn_product_code_strips_version_suffix(tmp_path):
    iso = tmp_path / "Example.iso"
    # Pad product code field with a version string that the parser must drop.
    raw = b"SEGA SEGASATURN " + b"\x00" * (0x20 - 16)
    code = b"T-10604G  V1.002  "[:10]
    raw += code
    raw += b"V1.002" + b"\x00" * 20
    iso.write_bytes(raw[:0x30].ljust(0x30, b"\x00"))
    # Hand-build a consistent 0x30-byte sector.
    sector = bytearray(0x30)
    sector[:16] = b"SEGA SEGASATURN "
    sector[0x20:0x2A] = b"T-10604G  "
    iso.write_bytes(bytes(sector))
    assert read_saturn_product_code(iso) == "SAT_T-10604G"


def test_read_saturn_product_code_raw_bin_offset(tmp_path):
    """Raw BIN images put user data at byte 0x10 after a 16-byte sync/header."""
    bin_path = tmp_path / "game.bin"
    sector = bytearray(0x10) + _build_saturn_iso_sector("T-12705H  ")
    bin_path.write_bytes(bytes(sector))
    assert read_saturn_product_code(bin_path) == "SAT_T-12705H"


def test_read_saturn_product_code_resolves_cue(tmp_path):
    bin_path = tmp_path / "game.bin"
    sector = bytearray(0x10) + _build_saturn_iso_sector("T-4507G   ")
    bin_path.write_bytes(bytes(sector))

    cue_path = tmp_path / "game.cue"
    cue_path.write_text(f'FILE "{bin_path.name}" BINARY\n  TRACK 01 MODE1/2352\n')

    assert read_saturn_product_code(cue_path) == "SAT_T-4507G"


def test_read_saturn_product_code_rejects_non_saturn(tmp_path):
    iso = tmp_path / "not-saturn.iso"
    iso.write_bytes(b"\x00" * 0x30)
    assert read_saturn_product_code(iso) is None


def test_read_saturn_product_code_rejects_unknown_extension(tmp_path):
    chd = tmp_path / "game.chd"
    chd.write_bytes(_build_saturn_iso_sector("T-4507G   "))
    # .chd isn't directly readable — must fall back to DAT lookup.
    assert read_saturn_product_code(chd) is None


# ---------------------------------------------------------------------------
# resolve_saturn_title_id — end-to-end orchestration
# ---------------------------------------------------------------------------


def _patch_dat(monkeypatch, parsed_map: dict[str, str]) -> None:
    """Install a fixed DAT map so tests don't depend on the repo's DAT file."""
    from shared.rom_id import saturn as mod

    monkeypatch.setattr(mod, "_load_dat", lambda dat_path=None: parsed_map)


def test_resolve_prefers_ip_bin_over_dat(monkeypatch, tmp_path):
    iso = tmp_path / "Whatever (USA).iso"
    iso.write_bytes(_build_saturn_iso_sector("GS-9047   "))
    # DAT would return a wrong serial if consulted — IP.BIN must win.
    _patch_dat(monkeypatch, {"whatever (usa)": "T-0000G"})

    assert resolve_saturn_title_id(rom_path=iso) == "SAT_GS-9047"


def test_resolve_falls_back_to_dat_for_chd(monkeypatch, tmp_path):
    chd = tmp_path / "Grandia (Japan) (Disc 1) (4M).chd"
    chd.write_bytes(b"\x00" * 64)  # CHD can't be parsed inline.
    _patch_dat(monkeypatch, parse_saturn_dat(SAMPLE_DAT))

    assert (
        resolve_saturn_title_id(rom_path=chd, rom_name=chd.stem)
        == "SAT_T-4507G"
    )


def test_resolve_returns_none_when_both_fail(monkeypatch, tmp_path):
    chd = tmp_path / "Never Heard Of It (USA).chd"
    chd.write_bytes(b"\x00" * 64)
    _patch_dat(monkeypatch, {})

    assert resolve_saturn_title_id(rom_path=chd, rom_name=chd.stem) is None
