"""Tests for shared.sync_id — the canonical ID resolver.

Covers every strategy in SYNC_ID_RULES plus the slug→canonical upgrade
helper used by the server on upload.
"""

from __future__ import annotations

import pytest

from shared.sync_id import (
    ResolveInput,
    ResolveResult,
    canonicalize_serial,
    canonicalize_slug_title_id,
    is_hex_title_id,
    nds_gamecode_to_sync_id,
    resolve,
    slug_sync_id,
)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestCanonicalizeSerial:
    def test_strips_punctuation(self):
        assert canonicalize_serial("SCUS-94163") == "SCUS94163"
        assert canonicalize_serial("SCUS_94163") == "SCUS94163"
        assert canonicalize_serial("SCUS 94163") == "SCUS94163"
        assert canonicalize_serial("SCUS.94163") == "SCUS94163"

    def test_uppercases(self):
        assert canonicalize_serial("slus-12345") == "SLUS12345"

    def test_empty_input(self):
        assert canonicalize_serial("") == ""

    def test_already_clean(self):
        assert canonicalize_serial("SCUS94163") == "SCUS94163"


class TestIsHexTitleId:
    def test_valid(self):
        assert is_hex_title_id("0004000000055D00")
        assert is_hex_title_id("ffffffffffffffff")
        assert is_hex_title_id("0000000000000000")

    def test_invalid_length(self):
        assert not is_hex_title_id("0004")
        assert not is_hex_title_id("0004000000055D000")  # 17 chars

    def test_non_hex_chars(self):
        assert not is_hex_title_id("0004000000055G00")  # G isn't hex
        assert not is_hex_title_id("SLUS-94163")

    def test_empty(self):
        assert not is_hex_title_id("")


class TestNdsGamecodeToSyncId:
    def test_basic_ascii(self):
        # "AMCE" → 0x41 0x4D 0x43 0x45
        assert nds_gamecode_to_sync_id("AMCE") == "00048000414D4345"

    def test_custom_prefix(self):
        assert nds_gamecode_to_sync_id("AMCE", prefix="DEADBEEF") == "DEADBEEF414D4345"

    def test_lowercase_prefix_upcased(self):
        assert nds_gamecode_to_sync_id("AMCE", prefix="deadbeef") == "DEADBEEF414D4345"

    def test_rejects_wrong_length(self):
        assert nds_gamecode_to_sync_id("AMC") is None
        assert nds_gamecode_to_sync_id("AMCEE") is None
        assert nds_gamecode_to_sync_id("") is None

    def test_rejects_non_ascii(self):
        # Non-printable byte
        assert nds_gamecode_to_sync_id("AMC\x00") is None

    def test_rejects_bad_prefix(self):
        assert nds_gamecode_to_sync_id("AMCE", prefix="XYZW") is None
        assert nds_gamecode_to_sync_id("AMCE", prefix="DEAD") is None  # too short


class TestSlugSyncId:
    def test_basic(self):
        # GBA has strategy=slug; should produce GBA_<slug>
        result = slug_sync_id("GBA", "Super Mario Advance (USA).gba")
        assert result.startswith("GBA_")
        assert "super_mario_advance" in result.lower()


# ---------------------------------------------------------------------------
# resolve() — one test per strategy, plus fallbacks
# ---------------------------------------------------------------------------


class TestResolveTitleIdStrategy:
    """3DS uses native 16-char hex title_ids."""

    def test_accepts_hex(self):
        r = resolve(ResolveInput(system="3DS", title_id="0004000000055D00"))
        assert r == ResolveResult(
            sync_id="0004000000055D00", strategy="title_id", fallback=False
        )

    def test_upcases_lower_hex(self):
        r = resolve(ResolveInput(system="3DS", title_id="0004000000055d00"))
        assert r.sync_id == "0004000000055D00"

    def test_rejects_non_hex_falls_back_to_slug(self):
        r = resolve(
            ResolveInput(
                system="3DS",
                title_id="not-hex",
                rom_filename="Some Game (USA).3ds",
            )
        )
        assert r.strategy == "slug"
        assert r.fallback is True
        assert r.sync_id.startswith("3DS_")

    def test_no_inputs_emits_placeholder(self):
        r = resolve(ResolveInput(system="3DS"))
        assert r.sync_id == "3DS_unknown"
        assert r.fallback is True


class TestResolvePrefixHexSerialStrategy:
    """NDS uses 00048000 + hex(gamecode)."""

    def test_direct_gamecode(self):
        r = resolve(ResolveInput(system="NDS", gamecode="AMCE"))
        assert r.strategy == "prefix_hex_serial"
        assert r.sync_id == "00048000414D4345"
        assert r.fallback is False

    def test_serial_as_4char_gamecode(self):
        # Some DATs put the gamecode in the `serial` field.
        r = resolve(ResolveInput(system="NDS", serial="AMCE"))
        assert r.strategy == "prefix_hex_serial"
        assert r.sync_id == "00048000414D4345"

    def test_falls_back_to_dat_lookup(self):
        def lookup(system, filename):
            return "AMCE"

        r = resolve(
            ResolveInput(system="NDS", rom_filename="Mario Kart DS.nds"),
            serial_lookup=lookup,
        )
        assert r.strategy == "prefix_hex_serial"
        assert r.sync_id == "00048000414D4345"

    def test_dat_lookup_returning_bad_length_falls_back_to_slug(self):
        def lookup(system, filename):
            return "BADLENGTH"  # not 4 chars

        r = resolve(
            ResolveInput(system="NDS", rom_filename="Mario Kart DS.nds"),
            serial_lookup=lookup,
        )
        assert r.strategy == "slug"
        assert r.fallback is True

    def test_no_gamecode_no_dat_falls_back_to_slug(self):
        r = resolve(ResolveInput(system="NDS", rom_filename="Mario Kart DS.nds"))
        assert r.strategy == "slug"
        assert r.fallback is True
        assert r.sync_id.startswith("NDS_")

    def test_no_inputs_emits_placeholder(self):
        r = resolve(ResolveInput(system="NDS"))
        assert r.sync_id == "NDS_unknown"
        assert r.fallback is True


class TestResolveSerialStrategy:
    """PS1/PS2/PSP/Vita/Saturn use the disc serial."""

    def test_direct_serial_canonicalized(self):
        r = resolve(ResolveInput(system="PS1", serial="SCUS-94163"))
        assert r == ResolveResult(
            sync_id="SCUS94163", strategy="serial", fallback=False
        )

    def test_dat_lookup(self):
        def lookup(system, filename):
            return "SCUS-01140"

        r = resolve(
            ResolveInput(system="PS1", rom_filename="Crash Bandicoot (USA).cue"),
            serial_lookup=lookup,
        )
        assert r.strategy == "serial"
        assert r.sync_id == "SCUS01140"

    def test_serial_falls_back_to_slug_when_empty(self):
        r = resolve(
            ResolveInput(system="PS1", serial="", rom_filename="Game (USA).cue")
        )
        assert r.strategy == "slug"
        assert r.fallback is True

    def test_dat_lookup_exception_is_swallowed(self):
        def lookup(system, filename):
            raise RuntimeError("DAT index corrupt")

        r = resolve(
            ResolveInput(system="PS1", rom_filename="Game (USA).cue"),
            serial_lookup=lookup,
        )
        # No crash — degrades to slug.
        assert r.strategy == "slug"
        assert r.fallback is True


class TestResolveSlugStrategy:
    """Everything else (GBA, SNES, NES, ...)."""

    def test_basic(self):
        r = resolve(ResolveInput(system="GBA", rom_filename="Zelda Minish Cap.gba"))
        assert r.strategy == "slug"
        assert r.fallback is False
        assert r.sync_id.startswith("GBA_")

    def test_no_filename_emits_placeholder(self):
        r = resolve(ResolveInput(system="GBA"))
        assert r.sync_id == "GBA_unknown"
        assert r.fallback is True


class TestResolveSystemNormalization:
    """The resolver should handle free-form system codes."""

    def test_lowercase_system(self):
        r = resolve(ResolveInput(system="nds", gamecode="AMCE"))
        assert r.strategy == "prefix_hex_serial"
        assert r.sync_id == "00048000414D4345"

    def test_unknown_system_defaults_to_slug(self):
        r = resolve(
            ResolveInput(system="MADEUP", rom_filename="Game (USA).bin")
        )
        assert r.strategy == "slug"


# ---------------------------------------------------------------------------
# canonicalize_slug_title_id — server-side upgrade path
# ---------------------------------------------------------------------------


class TestCanonicalizeSlugTitleId:
    def test_already_hex_unchanged(self):
        # Hex IDs aren't slug form, so nothing to upgrade.
        assert (
            canonicalize_slug_title_id("0004000000055D00")
            == "0004000000055D00"
        )

    def test_already_serial_unchanged(self):
        assert canonicalize_slug_title_id("SLUS-94163") == "SLUS-94163"

    def test_slug_for_slug_system_unchanged(self):
        # GBA always uses slug form as canonical.
        assert (
            canonicalize_slug_title_id("GBA_zelda_minish_cap_usa")
            == "GBA_zelda_minish_cap_usa"
        )

    def test_nds_slug_upgraded_via_dat(self):
        # Simulates DatNormalizer.lookup_serial returning the gamecode.
        def lookup(system, filename):
            assert system == "NDS"
            return "AMCE"

        result = canonicalize_slug_title_id(
            "NDS_mario_kart_ds_usa", serial_lookup=lookup
        )
        assert result == "00048000414D4345"

    def test_ps1_slug_upgraded_via_dat(self):
        def lookup(system, filename):
            assert system == "PS1"
            return "SCUS-94163"

        result = canonicalize_slug_title_id(
            "PS1_crash_bandicoot_usa", serial_lookup=lookup
        )
        assert result == "SCUS94163"

    def test_nds_slug_no_dat_match_unchanged(self):
        # Lookup returns None → resolver falls back → keep original slug.
        def lookup(system, filename):
            return None

        assert (
            canonicalize_slug_title_id(
                "NDS_unknown_homebrew_game", serial_lookup=lookup
            )
            == "NDS_unknown_homebrew_game"
        )

    def test_no_serial_lookup_leaves_slug(self):
        # Server without a DAT still accepts slugs; just can't upgrade.
        assert (
            canonicalize_slug_title_id("NDS_mario_kart_ds_usa")
            == "NDS_mario_kart_ds_usa"
        )

    def test_dat_raising_doesnt_break(self):
        def lookup(system, filename):
            raise RuntimeError("DAT explode")

        # Any failure in DAT lookup leaves the slug unchanged.
        assert (
            canonicalize_slug_title_id(
                "NDS_mario_kart_ds_usa", serial_lookup=lookup
            )
            == "NDS_mario_kart_ds_usa"
        )
