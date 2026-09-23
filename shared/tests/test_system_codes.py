"""Guards against the same system landing under two different codes.

``SYSTEM_ALIASES`` declares which spelling is canonical.  Anything that maps
an external name (an EmuDeck folder, a DAT keyword, a MiSTer core folder)
onto a system code must produce the *canonical* one — otherwise a ROM and
its save end up keyed differently and never meet on the server.  That is
exactly what happened with Sega CD: ``FOLDER_TO_SYSTEM`` emitted the alias
``SCD`` while saves used ``SEGACD``, so the ROM catalog looked empty to a
client asking for the canonical code.
"""

from __future__ import annotations

from shared.systems import (
    FOLDER_TO_SYSTEM,
    SYNC_ID_RULES,
    SYSTEM_ALIASES,
    SYSTEM_CHOICES,
    SYSTEM_DEFAULT_SAVE_EXT,
)
from shared.mister import MISTER_FOLDER_TO_SYSTEM, MISTER_SYSTEM_TO_FOLDER


def _alias_offenders(mapping: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in mapping.items() if v in SYSTEM_ALIASES}


def test_folder_map_never_emits_an_alias():
    assert _alias_offenders(FOLDER_TO_SYSTEM) == {}


def test_mister_folder_map_never_emits_an_alias():
    assert _alias_offenders(MISTER_FOLDER_TO_SYSTEM) == {}


def test_mister_system_map_is_keyed_by_canonical_codes():
    assert [s for s in MISTER_SYSTEM_TO_FOLDER if s in SYSTEM_ALIASES] == []


def test_sync_id_rules_are_keyed_by_canonical_codes():
    assert [s for s in SYNC_ID_RULES if s in SYSTEM_ALIASES] == []


def test_aliases_resolve_to_real_systems():
    for alias, canonical in SYSTEM_ALIASES.items():
        assert canonical not in SYSTEM_ALIASES, f"{alias} points at another alias"
        assert canonical in SYNC_ID_RULES, f"{canonical} has no sync-id rule"


def test_sega_cd_is_segacd_everywhere():
    """Regression: the ROM catalog was indexed as SCD while saves used SEGACD."""
    assert SYSTEM_ALIASES["SCD"] == "SEGACD"
    assert "SEGACD" in SYSTEM_CHOICES
    assert "SCD" not in SYSTEM_CHOICES
    for folder in ("segacd", "megacd", "megacdjp"):
        assert FOLDER_TO_SYSTEM[folder] == "SEGACD"
    assert MISTER_FOLDER_TO_SYSTEM["MegaCD"] == "SEGACD"
    assert MISTER_SYSTEM_TO_FOLDER["SEGACD"] == "MegaCD"


def test_default_save_ext_keys_are_canonical():
    assert [s for s in SYSTEM_DEFAULT_SAVE_EXT if s in SYSTEM_ALIASES] == []


def test_make_title_id_never_emits_an_alias():
    """An alias reaching a title_id splits one game across two server slots.

    ``SYSTEM_CODES`` contains aliases on purpose so they validate, which is
    why the check has to live in ``make_title_id`` itself — this is how
    ``GEN_phantasy_star_iv_usa`` ended up beside ``MD_phantasy_star_iv_usa``.
    """
    from shared.rom_id import make_title_id

    for alias, canonical in SYSTEM_ALIASES.items():
        made = make_title_id(alias, "Some Game (USA).bin")
        assert made == make_title_id(canonical, "Some Game (USA).bin")
        assert made.startswith(f"{canonical}_")


def test_slug_title_ids_canonicalize_alias_systems():
    """An older client still sending an alias must land in the right slot."""
    from shared.sync_id import canonicalize_slug_title_id

    assert (
        canonicalize_slug_title_id("GEN_phantasy_star_iv_usa")
        == "MD_phantasy_star_iv_usa"
    )
    assert canonicalize_slug_title_id("SCD_snatcher_usa") == "SEGACD_snatcher_usa"
    # Canonical ids and non-slug ids pass through untouched.
    assert (
        canonicalize_slug_title_id("MD_phantasy_star_iv_usa")
        == "MD_phantasy_star_iv_usa"
    )
    assert canonicalize_slug_title_id("SLUS01324") == "SLUS01324"
