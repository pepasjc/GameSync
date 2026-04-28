"""Tests for Sony disc-serial extraction and DAT lookup."""

from __future__ import annotations

from pathlib import Path

import rom_normalizer as rn


# ──────────────────────────────────────────────────────────────────────
# extract_ps_serial
# ──────────────────────────────────────────────────────────────────────
def test_extract_underscore_dot_format():
    # Canonical user example.
    assert rn.extract_ps_serial("SCES_538.51.game name.iso") == "SCES-53851"


def test_extract_hyphen_format():
    assert (
        rn.extract_ps_serial("SLUS-20265 - Agent Under Fire.iso") == "SLUS-20265"
    )


def test_extract_no_separator():
    assert rn.extract_ps_serial("SLPM65002 - 0 Story.iso") == "SLPM-65002"


def test_extract_pcsx_style():
    # PCSX2 often writes "SLUS_202.65" for PS2.
    assert rn.extract_ps_serial("SLUS_202.65.Agent Under Fire.iso") == "SLUS-20265"


def test_extract_bracketed():
    assert rn.extract_ps_serial("[SLUS-20265] Agent Under Fire.iso") == "SLUS-20265"


def test_extract_ignores_serial_suffix():
    # "GH" is a Greatest Hits reprint marker — we return the base serial so
    # the DAT lookup still hits the main entry.
    assert rn.extract_ps_serial("SLUS-20265GH - X.iso") == "SLUS-20265"


def test_extract_none_for_non_ps_filename():
    assert rn.extract_ps_serial("Super Mario 64.z64") is None
    assert rn.extract_ps_serial("Sonic the Hedgehog.md") is None
    assert rn.extract_ps_serial("007 - Agent Under Fire (USA).iso") is None


def test_extract_rejects_unknown_prefixes():
    # ABCD isn't a real Sony disc prefix — must not match.
    assert rn.extract_ps_serial("ABCD-12345 - whatever.iso") is None


def test_extract_case_insensitive():
    # Real filenames usually upper-case the serial, but accept either.
    assert rn.extract_ps_serial("scus-94163 - spyro.bin") == "SCUS-94163"


def test_extract_psp_prefix():
    assert rn.extract_ps_serial("ULUS-10020 - Wipeout Pure.iso") == "ULUS-10020"
    assert rn.extract_ps_serial("NPJH-50100 - ps2 dl.iso") == "NPJH-50100"


def test_extract_ps3_prefix():
    # Retail Blu-ray serials (BLUS / BLES / BLJM / BCUS / BCES).
    assert rn.extract_ps_serial("BLUS30464 - Demon's Souls.iso") == "BLUS-30464"
    assert (
        rn.extract_ps_serial("BLES-00932 - Metal Gear Solid 4.iso") == "BLES-00932"
    )
    assert rn.extract_ps_serial("BCUS98208.God of War III.iso") == "BCUS-98208"
    # PSN downloadable serials (NP*B*).
    assert (
        rn.extract_ps_serial("NPUB30564 - Journey.iso") == "NPUB-30564"
    )


def test_extract_ps3_opl_bracket_format():
    """PS3 ISO creator emits ``Game Name [SERIAL].iso`` — same bracketed
    layout as PS2 OPL.  Serial sits between square brackets at the end of
    the stem; the existing lookbehind/lookahead accept it because ``[``
    and ``]`` aren't alphanumeric.
    """
    assert (
        rn.extract_ps_serial("Vampire Ressurection [BLJM60567].iso")
        == "BLJM-60567"
    )
    assert (
        rn.extract_ps_serial("Demon's Souls [BLUS30464].iso") == "BLUS-30464"
    )
    assert (
        rn.extract_ps_serial("Metal Gear Solid 4 (USA) [BLUS30109].iso")
        == "BLUS-30109"
    )


def test_extract_ps3_pkg_serial():
    """PS3 PSN ``.pkg`` files carry the same serial in the filename — DAT
    entries from libretro/EN-Dats translation packs use exactly this
    layout (e.g. ``BLJM-61322 (install this pkg).pkg``).
    """
    assert (
        rn.extract_ps_serial("BLJM-61322 (install this pkg).pkg")
        == "BLJM-61322"
    )
    assert (
        rn.extract_ps_serial("Journey [NPUB30564].pkg") == "NPUB-30564"
    )
    assert (
        rn.extract_ps_serial("BLUS30464 - Demon's Souls.pkg") == "BLUS-30464"
    )
    # Sub-folder DLC layout from the EN-Dats DAT.
    assert (
        rn.extract_ps_serial("BLJM-61063 BGM DLC Pack 70 Songs.pkg")
        == "BLJM-61063"
    )


# ──────────────────────────────────────────────────────────────────────
# supports_serial_lookup
# ──────────────────────────────────────────────────────────────────────
def test_supports_serial_lookup_ps_systems():
    assert rn.supports_serial_lookup("PS1")
    assert rn.supports_serial_lookup("PS2")
    assert rn.supports_serial_lookup("PSP")
    assert rn.supports_serial_lookup("PS3")
    assert rn.supports_serial_lookup("ps3")  # case-insensitive


def test_supports_serial_lookup_non_ps():
    assert not rn.supports_serial_lookup("SNES")
    assert not rn.supports_serial_lookup("NES")
    assert not rn.supports_serial_lookup("NDS")
    assert not rn.supports_serial_lookup("")
    assert not rn.supports_serial_lookup(None)  # type: ignore[arg-type]


# ──────────────────────────────────────────────────────────────────────
# lookup_serial
# ──────────────────────────────────────────────────────────────────────
def test_lookup_serial_direct_hit():
    serial_map = {"SLUS-20265": "007 - Agent Under Fire (USA)"}
    assert (
        rn.lookup_serial("SLUS-20265", serial_map) == "007 - Agent Under Fire (USA)"
    )


def test_lookup_serial_missing():
    assert rn.lookup_serial("SLUS-99999", {"SLUS-20265": "foo"}) is None


def test_lookup_serial_empty_inputs():
    assert rn.lookup_serial("", {"SLUS-20265": "foo"}) is None
    assert rn.lookup_serial("SLUS-20265", {}) is None


def test_lookup_serial_suffix_fallback():
    # DAT may only carry the suffixed variant; base serial should still resolve.
    serial_map = {"SLUS-20265GH": "Agent Under Fire (USA) (Greatest Hits)"}
    assert (
        rn.lookup_serial("SLUS-20265", serial_map)
        == "Agent Under Fire (USA) (Greatest Hits)"
    )


# ──────────────────────────────────────────────────────────────────────
# load_serial_map on a real PS2 DAT (integration test)
# ──────────────────────────────────────────────────────────────────────
_PS2_DAT = Path(__file__).resolve().parents[2] / "server" / "data" / "dats" / "Sony - PlayStation 2.dat"


def test_load_serial_map_real_ps2_dat():
    if not _PS2_DAT.exists():
        import pytest

        pytest.skip("PS2 DAT not available")
    serial_map = rn.load_serial_map(_PS2_DAT)
    # A real DAT has thousands of entries.
    assert len(serial_map) > 1000
    # A handful of known serials.
    assert serial_map.get("SLUS-20265") == "007 - Agent Under Fire (USA)"
    assert serial_map.get("SLES-50539", "").startswith("007 - Agent Under Fire")
    assert serial_map.get("SLPM-65002", "").startswith("0 Story")


def test_extract_then_lookup_end_to_end():
    if not _PS2_DAT.exists():
        import pytest

        pytest.skip("PS2 DAT not available")
    serial_map = rn.load_serial_map(_PS2_DAT)

    filename = "SLES_505.39.007 - Agent Under Fire.iso"
    serial = rn.extract_ps_serial(filename)
    assert serial == "SLES-50539"
    name = rn.lookup_serial(serial, serial_map)
    assert name is not None
    assert "Agent Under Fire" in name


# ──────────────────────────────────────────────────────────────────────
# PS3 OPL-style end-to-end (real DAT)
# ──────────────────────────────────────────────────────────────────────
_PS3_DAT = Path(__file__).resolve().parents[2] / "server" / "data" / "dats" / "Sony - PlayStation 3.dat"


def test_ps3_opl_bracket_end_to_end():
    """``Vampire Ressurection [BLJM60567].iso`` → DAT canonical name.

    Mirrors the PS2 OPL flow: extract serial from the bracketed suffix,
    then look up the canonical name in the libretro PS3 DAT.  This is
    the path the ROM normalizer/collection takes for renaming PS3
    catalog drops.
    """
    if not _PS3_DAT.exists():
        import pytest

        pytest.skip("PS3 DAT not available")
    serial_map = rn.load_serial_map(_PS3_DAT)
    assert len(serial_map) > 1000  # Real DAT has thousands

    filename = "Vampire Ressurection [BLJM60567].iso"
    serial = rn.extract_ps_serial(filename)
    assert serial == "BLJM-60567"
    name = rn.lookup_serial(serial, serial_map)
    assert name is not None
    assert "Vampire Resurrection" in name
