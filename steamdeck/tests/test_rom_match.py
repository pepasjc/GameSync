"""Tests for the ROM catalog index and disc-slug dedup helpers."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
STEAMDECK_ROOT = ROOT / "steamdeck"
if str(STEAMDECK_ROOT) not in sys.path:
    sys.path.insert(0, str(STEAMDECK_ROOT))

from scanner.models import GameEntry, SyncStatus  # noqa: E402
from scanner.rom_match import (  # noqa: E402
    RomIndex,
    core_name_slug,
    dedup_disc_slug_entries,
    is_disc_slug_title_id,
)


# ---------------------------------------------------------------------------
# is_disc_slug_title_id
# ---------------------------------------------------------------------------


def test_disc_slug_detection_distinguishes_serial_from_slug():
    # PS1 retail serials like SLUS01324 are NOT slug fallbacks.
    assert is_disc_slug_title_id("SLUS01324", "PS1") is False
    # The slug fallback emitted when CHD parsing fails IS a slug.
    assert is_disc_slug_title_id("PS1_breath_of_fire_iv_usa", "PS1") is True
    # Saturn product codes (upper-case + hyphen) are NOT slugs.
    assert is_disc_slug_title_id("SAT_T-12705H", "SAT") is False
    # Saturn slug fallback (lower-case) IS a slug.
    assert is_disc_slug_title_id("SAT_grandia_usa", "SAT") is True
    # Non-disc systems never get the disc-slug treatment, even if the
    # title_id format would qualify on a disc system.
    assert is_disc_slug_title_id("GBA_zelda_minish_cap_usa", "GBA") is False


# ---------------------------------------------------------------------------
# core_name_slug
# ---------------------------------------------------------------------------


def test_core_name_slug_strips_region_and_language_tags():
    # Both "Chrono Trigger.sav" and "Chrono Trigger (USA) (En,Fr).nds" must
    # collapse to the same key so a region-less local save still finds the
    # server's regional ROM entry.
    assert core_name_slug("Chrono Trigger.sav") == "chrono_trigger"
    assert core_name_slug("Chrono Trigger (USA) (En,Fr).nds") == "chrono_trigger"
    assert core_name_slug("Breath of Fire IV (USA)") == "breath_of_fire_iv"


def test_core_name_slug_handles_empty_input():
    assert core_name_slug(None) == ""
    assert core_name_slug("") == ""


# ---------------------------------------------------------------------------
# RomIndex
# ---------------------------------------------------------------------------


def _catalog():
    return [
        {
            "rom_id": "r1",
            "title_id": "SLUS01324",
            "system": "PS1",
            "name": "Breath of Fire IV (USA)",
            "filename": "Breath of Fire IV (USA).chd",
        },
        {
            "rom_id": "r2",
            "title_id": "NDS_chrono_trigger_usa",
            "system": "NDS",
            "name": "Chrono Trigger (USA) (En,Fr)",
            "filename": "Chrono Trigger (USA) (En,Fr).nds",
        },
        {
            "rom_id": "r3",
            "title_id": "GBA_zelda_minish_cap_usa",
            "system": "GBA",
            "name": "Legend of Zelda, The - The Minish Cap (USA)",
            "filename": "Legend of Zelda, The - The Minish Cap (USA).gba",
        },
    ]


def test_rom_index_resolves_by_exact_filename():
    idx = RomIndex.build(_catalog())
    assert idx.title_id_for_filename(
        "PS1", "Breath of Fire IV (USA).chd"
    ) == "SLUS01324"
    assert idx.title_id_for_filename("PS1", "Wrong File.chd") is None


def test_rom_index_resolves_by_full_normalized_name():
    idx = RomIndex.build(_catalog())
    # Server stored "Chrono Trigger (USA) (En,Fr)" — normalize_rom_name
    # collapses that to "chrono_trigger_usa", the same slug a local save
    # named "Chrono Trigger (USA).sav" would produce.
    assert idx.title_id_for_name(
        "NDS", "Chrono Trigger (USA).sav"
    ) == "NDS_chrono_trigger_usa"


def test_rom_index_falls_back_to_region_stripped_match():
    idx = RomIndex.build(_catalog())
    # Local save dropped the region tag entirely — full slug
    # "chrono_trigger" doesn't match the catalog's "chrono_trigger_usa"
    # but the core slug (region-stripped) does.
    assert idx.title_id_for_name(
        "NDS", "Chrono Trigger.sav"
    ) == "NDS_chrono_trigger_usa"


def test_rom_index_falls_back_to_roman_arabic_core_match():
    idx = RomIndex.build(
        [
            {
                "rom_id": "r1",
                "title_id": "SLUS01324",
                "system": "PS1",
                "name": "Breath of Fire 4",
                "filename": "Breath of Fire 4.chd",
            }
        ]
    )

    assert idx.title_id_for_name("PS1", "Breath of Fire IV (USA)") == "SLUS01324"


def test_rom_index_matches_for_entry_uses_all_strategies(tmp_path):
    idx = RomIndex.build(_catalog())

    # Local PS1 entry with slug fallback + correct rom_filename → matched
    # via the catalog's filename index.
    local_slug = GameEntry(
        title_id="PS1_breath_of_fire_iv_usa",
        display_name="Breath of Fire IV (USA)",
        system="PS1",
        emulator="DuckStation",
        rom_filename="Breath of Fire IV (USA).chd",
    )
    matches = idx.matches_for(local_slug)
    assert len(matches) == 1 and matches[0]["title_id"] == "SLUS01324"

    # NDS entry with no region tag — matched via the core-slug index.
    nds_no_region = GameEntry(
        title_id="NDS_chrono_trigger",
        display_name="Chrono Trigger",
        system="NDS",
        emulator="melonDS",
    )
    matches = idx.matches_for(nds_no_region)
    assert len(matches) == 1 and matches[0]["title_id"] == "NDS_chrono_trigger_usa"

    # Title not present in catalog → empty list (Download-ROM stays hidden).
    missing = GameEntry(
        title_id="N64_super_mario_64_usa",
        display_name="Super Mario 64",
        system="N64",
        emulator="RetroArch",
    )
    assert idx.matches_for(missing) == []


def test_rom_index_resolves_sega_cd_across_system_aliases():
    """Regression: the server's folder-based scanner tags Sega CD ROMs with
    the alias ``SCD`` (``FOLDER_TO_SYSTEM["segacd"] = "SCD"``), but the Steam
    Deck's RetroArch scanner emits the canonical ``SEGACD``.  Before
    ``RomIndex`` canonicalised system codes, the ``(system, …)`` keys never
    lined up, so the ``Download ROM`` affordance stayed hidden on every
    Sega CD row even when the server had the ROM.

    Assert that catalog row system=``SCD`` + local entry system=``SEGACD``
    (and vice versa) resolve to each other."""
    catalog = [
        {
            "rom_id": "r1",
            "title_id": "SEGACD_sonic_cd_usa",
            "system": "SCD",  # server-side alias
            "name": "Sonic CD (USA)",
            "filename": "Sonic CD (USA).chd",
        }
    ]
    idx = RomIndex.build(catalog)

    # Lookup by the canonical code still resolves even though the catalog
    # row was indexed under the alias.
    assert idx.title_id_for_filename(
        "SEGACD", "Sonic CD (USA).chd"
    ) == "SEGACD_sonic_cd_usa"
    assert idx.title_id_for_name(
        "SEGACD", "Sonic CD (USA)"
    ) == "SEGACD_sonic_cd_usa"

    # And the end-to-end path the UI relies on: a local SEGACD entry
    # finds the SCD-keyed catalog row.
    local = GameEntry(
        title_id="SEGACD_sonic_cd_usa",
        display_name="Sonic CD (USA)",
        system="SEGACD",
        emulator="RetroArch",
        rom_filename="Sonic CD (USA).chd",
    )
    matches = idx.matches_for(local)
    assert len(matches) == 1
    assert matches[0]["title_id"] == "SEGACD_sonic_cd_usa"

    # Reverse direction — catalog tagged SEGACD, local scanner reports SCD
    # (hypothetical, but we want the index symmetric to avoid future
    # regressions if the canonical label ever flips).
    idx2 = RomIndex.build(
        [
            {
                "rom_id": "r2",
                "title_id": "SEGACD_lunar_ssh_usa",
                "system": "SEGACD",
                "name": "Lunar - Silver Star Story Complete (USA)",
                "filename": "Lunar - Silver Star Story Complete (USA).chd",
            }
        ]
    )
    assert idx2.title_id_for_filename(
        "SCD", "Lunar - Silver Star Story Complete (USA).chd"
    ) == "SEGACD_lunar_ssh_usa"


# ---------------------------------------------------------------------------
# dedup_disc_slug_entries
# ---------------------------------------------------------------------------


def test_dedup_keeps_serial_entry_and_absorbs_local_save(tmp_path):
    save_path = tmp_path / "Breath of Fire IV (USA)_1.mcd"
    save_path.write_text("save")

    local_slug = GameEntry(
        title_id="PS1_breath_of_fire_iv_usa",
        display_name="Breath of Fire IV (USA)",
        system="PS1",
        emulator="DuckStation",
        save_path=save_path,
        save_hash="abcd",
        save_mtime=1700000000.0,
        save_size=128 * 1024,
        status=SyncStatus.LOCAL_ONLY,
    )
    server_only = GameEntry(
        title_id="SLUS01324",
        display_name="Breath of Fire IV (USA)",
        system="PS1",
        emulator="Server",
        server_hash="defg",
        status=SyncStatus.SERVER_ONLY,
    )

    result = dedup_disc_slug_entries([local_slug, server_only])
    assert len(result) == 1
    winner = result[0]
    # Serial-keyed entry survives.
    assert winner.title_id == "SLUS01324"
    # Local save data was merged in.
    assert winner.save_path == save_path
    assert winner.save_hash == "abcd"
    assert winner.save_size == 128 * 1024
    # Server-side metadata is preserved.
    assert winner.server_hash == "defg"
    # Status got upgraded from SERVER_ONLY to the local row's status.
    assert winner.status == SyncStatus.LOCAL_ONLY


def test_dedup_merges_roman_arabic_ps1_title_variants(tmp_path):
    save_path = tmp_path / "Breath of Fire IV (USA)_1.mcd"
    save_path.write_text("save")

    # Server stored "Breath of Fire 4" (psxdb name); local has "Breath of
    # Fire IV (USA)".  Roman/arithmetic sequel markers should still collapse
    # to one serial-backed row, preserving the real local save path.
    local_slug = GameEntry(
        title_id="PS1_breath_of_fire_iv_usa",
        display_name="Breath of Fire IV (USA)",
        system="PS1",
        emulator="DuckStation",
        save_path=save_path,
        save_hash="abcd",
        save_mtime=1700000000.0,
        save_size=128 * 1024,
        status=SyncStatus.LOCAL_ONLY,
    )
    server_only = GameEntry(
        title_id="SLUS01324",
        display_name="Breath of Fire 4",
        system="PS1",
        emulator="Server",
        status=SyncStatus.SERVER_ONLY,
    )

    result = dedup_disc_slug_entries([local_slug, server_only])
    assert len(result) == 1
    winner = result[0]
    assert winner.title_id == "SLUS01324"
    assert winner.save_path == save_path
    assert winner.save_hash == "abcd"
    assert winner.status == SyncStatus.LOCAL_ONLY


def test_dedup_prefers_real_local_card_over_predicted_serial_path(tmp_path):
    predicted = tmp_path / "Breath of Fire 4_1.mcd"
    actual = tmp_path / "Breath of Fire IV (USA)_1.mcd"
    actual.write_text("save")

    serial_entry = GameEntry(
        title_id="SLUS01324",
        display_name="Breath of Fire 4",
        system="PS1",
        emulator="DuckStation",
        save_path=predicted,
        status=SyncStatus.SERVER_ONLY,
    )
    local_slug = GameEntry(
        title_id="PS1_breath_of_fire_iv_usa",
        display_name="Breath of Fire IV (USA)",
        system="PS1",
        emulator="DuckStation",
        save_path=actual,
        save_hash="abcd",
        save_mtime=1700000000.0,
        save_size=128 * 1024,
        status=SyncStatus.LOCAL_ONLY,
    )

    result = dedup_disc_slug_entries([serial_entry, local_slug])
    assert len(result) == 1
    winner = result[0]
    assert winner.title_id == "SLUS01324"
    assert winner.save_path == actual
    assert winner.save_hash == "abcd"


def test_dedup_leaves_non_disc_systems_alone():
    a = GameEntry(
        title_id="GBA_pokemon_emerald",
        display_name="Pokemon Emerald",
        system="GBA",
        emulator="RetroArch",
    )
    b = GameEntry(
        title_id="GBA_pokemon_emerald",
        display_name="Pokemon Emerald",
        system="GBA",
        emulator="Server",
    )
    # Two GBA entries with the same title_id: dedup must NOT touch them
    # (the upstream seen_ids guard already prevents this for non-disc).
    assert dedup_disc_slug_entries([a, b]) == [a, b]


def test_dedup_leaves_two_serial_entries_alone():
    # Multi-disc games can legitimately produce two serial-keyed rows
    # for distinct serials of the same franchise (e.g. JP + USA).  The
    # dedup must not collapse those.
    a = GameEntry(
        title_id="SLUS00101",
        display_name="Final Fantasy VII (USA)",
        system="PS1",
        emulator="DuckStation",
    )
    b = GameEntry(
        title_id="SCES00867",
        display_name="Final Fantasy VII (Europe)",
        system="PS1",
        emulator="DuckStation",
    )
    # Different core slugs (FF7 USA vs Europe normalize identically since
    # we strip parens) → both share the same key, but neither is a slug
    # entry, so both survive.
    result = dedup_disc_slug_entries([a, b])
    assert len(result) == 2
