"""Generic server-only placeholder builder — shows saves for every system."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
STEAMDECK_ROOT = ROOT / "steamdeck"
if str(STEAMDECK_ROOT) not in sys.path:
    sys.path.insert(0, str(STEAMDECK_ROOT))

from scanner import server_only  # noqa: E402
from scanner.models import SyncStatus  # noqa: E402


def test_builder_emits_entry_per_uncovered_server_save(tmp_path):
    server = {
        "GBA_pokemon_emerald_usa": {
            "save_hash": "abc",
            "name": "Pokemon Emerald",
            "console_type": "GBA",
            "save_size": 128000,
        },
        "SNES_super_metroid": {
            "save_hash": "def",
            "name": "Super Metroid",
            "console_type": "SNES",
        },
    }
    entries = server_only.build_server_only_entries(server, set(), tmp_path)

    tids = {e.title_id for e in entries}
    assert tids == {"GBA_pokemon_emerald_usa", "SNES_super_metroid"}
    assert all(e.status == SyncStatus.SERVER_ONLY for e in entries)
    # RetroArch-backed systems get a predicted .srm path so Download Save
    # is immediately usable even without a local ROM.
    by_id = {e.title_id: e for e in entries}
    assert by_id["GBA_pokemon_emerald_usa"].save_path == (
        tmp_path / "saves" / "retroarch" / "saves" / "Pokemon Emerald.srm"
    )
    assert by_id["SNES_super_metroid"].save_path == (
        tmp_path / "saves" / "retroarch" / "saves" / "Super Metroid.srm"
    )


def test_builder_skips_saves_already_in_seen_ids(tmp_path):
    server = {
        "GBA_zelda": {"save_hash": "h1", "name": "Zelda", "console_type": "GBA"},
        "SNES_chrono": {"save_hash": "h2", "name": "Chrono", "console_type": "SNES"},
    }
    entries = server_only.build_server_only_entries(server, {"GBA_zelda"}, tmp_path)
    assert [e.title_id for e in entries] == ["SNES_chrono"]


def test_builder_skips_ps3_gc_and_3ds_handled_by_dedicated_builders(tmp_path):
    """PS3, GC, and 3DS have their own builders that set up save_path placeholders;
    the generic one must not duplicate those rows with null save_paths."""
    server = {
        "BLUS30464-AUTOSAVE-01": {
            "save_hash": "h",
            "console_type": "PS3",
            "name": "Some PS3 Save",
        },
        "GC_GALE01": {
            "save_hash": "h",
            "console_type": "GC",
            "name": "Melee",
        },
        "0004000000030800": {
            "save_hash": "h",
            "console_type": "3DS",
            "name": "Mario Kart 7",
        },
        "GBA_metroid_zero_mission": {
            "save_hash": "h",
            "console_type": "GBA",
            "name": "Metroid",
        },
    }
    entries = server_only.build_server_only_entries(server, set(), tmp_path)
    assert [e.title_id for e in entries] == ["GBA_metroid_zero_mission"]


def test_builder_falls_back_to_title_id_prefix_when_no_platform(tmp_path):
    server = {
        "GBA_unknown_platform_save": {"save_hash": "h", "name": "X"},
        "SLUS01234": {"save_hash": "h", "name": "Y"},  # bare PS1 code, no console_type
    }
    entries = server_only.build_server_only_entries(server, set(), tmp_path)
    by_id = {e.title_id: e for e in entries}
    assert by_id["GBA_unknown_platform_save"].system == "GBA"
    assert by_id["SLUS01234"].system == "PS1"


def test_builder_maps_psx_console_type_to_ps1(tmp_path):
    server = {"SLUS98765": {"console_type": "PSX", "save_hash": "h", "name": "Z"}}
    entries = server_only.build_server_only_entries(server, set(), tmp_path)
    assert entries[0].system == "PS1"


def test_builder_collapses_genesis_and_md_to_single_system(tmp_path):
    """Server may report Mega Drive saves as "Genesis", "GEN", or "MD".
    All three must share one canonical system code so the system filter
    doesn't list duplicates."""
    server = {
        "MD_sonic_the_hedgehog_usa": {
            "console_type": "MD",
            "save_hash": "h1",
            "name": "Sonic",
        },
        "MD_streets_of_rage_usa": {
            "console_type": "Genesis",
            "save_hash": "h2",
            "name": "Streets of Rage",
        },
        "GEN_gunstar_heroes_usa": {
            "console_type": "GEN",
            "save_hash": "h3",
            "name": "Gunstar Heroes",
        },
        "MD_phantasy_star_iv_usa": {
            "platform": "Mega Drive",
            "save_hash": "h4",
            "name": "Phantasy Star IV",
        },
    }
    entries = server_only.build_server_only_entries(server, set(), tmp_path)
    assert {e.system for e in entries} == {"MD"}


def test_builder_drops_saves_with_no_derivable_system(tmp_path):
    # Weird title_id and no platform info — we can't classify it, so skip.
    server = {"abc": {"save_hash": "h"}}
    entries = server_only.build_server_only_entries(server, set(), tmp_path)
    assert entries == []


def test_builder_populates_server_side_metadata(tmp_path):
    server = {
        "GBA_zelda": {
            "save_hash": "aabbcc",
            "name": "Legend of Zelda",
            "console_type": "GBA",
            "save_size": 64 * 1024,
            "client_timestamp": 1700000000.0,
            "title_id": "GBA_zelda",
        }
    }
    entry = server_only.build_server_only_entries(server, set(), tmp_path)[0]
    assert entry.server_hash == "aabbcc"
    assert entry.server_size == 64 * 1024
    assert entry.server_timestamp == 1700000000.0
    assert entry.server_title_id == "GBA_zelda"
    assert entry.display_name == "Legend of Zelda"


# ──────────────────────────────────────────────────────────────────────────────
# Predicted save_path per system — ensures SERVER_ONLY entries are directly
# downloadable rather than requiring the user to install the ROM first.
# ──────────────────────────────────────────────────────────────────────────────


def test_ps1_server_only_predicts_duckstation_memcard_path(tmp_path):
    server = {
        "SLUS01324": {
            "console_type": "PS1",
            "name": "Breath of Fire IV (USA)",
            "save_hash": "h",
        }
    }
    entry = server_only.build_server_only_entries(server, set(), tmp_path)[0]
    assert entry.save_path == (
        tmp_path / "saves" / "duckstation" / "memcards"
        / "Breath of Fire IV (USA)_1.mcd"
    )
    assert entry.is_multi_file is False
    assert entry.is_psp_slot is False


def test_ps1_server_only_strips_disc_tags_from_stem(tmp_path):
    server = {
        "SCUS94163": {
            "console_type": "PS1",
            "name": "Final Fantasy VII (USA) (Disc 1)",
            "save_hash": "h",
        }
    }
    entry = server_only.build_server_only_entries(server, set(), tmp_path)[0]
    # Disc tag stripped so multi-disc saves share one card, matching
    # DuckStation's _clean_card_label convention.
    assert entry.save_path == (
        tmp_path / "saves" / "duckstation" / "memcards"
        / "Final Fantasy VII (USA)_1.mcd"
    )


def test_ps2_server_only_predicts_pcsx2_memcard_path(tmp_path):
    server = {
        "SLUS20002": {
            "console_type": "PS2",
            "name": "Final Fantasy X",
            "save_hash": "h",
        }
    }
    entry = server_only.build_server_only_entries(server, set(), tmp_path)[0]
    assert entry.save_path == (
        tmp_path / "saves" / "pcsx2" / "memcards" / "Final Fantasy X.ps2"
    )


def test_psp_server_only_uses_savedata_slot_dir(tmp_path):
    server = {
        "ULUS10567DATA": {
            "console_type": "PSP",
            "name": "Final Fantasy Tactics: The War of the Lions",
            "save_hash": "h",
        }
    }
    entry = server_only.build_server_only_entries(server, set(), tmp_path)[0]
    assert entry.save_path == (
        tmp_path / "saves" / "ppsspp" / "SAVEDATA" / "ULUS10567DATA"
    )
    # PSP downloads the save bundle and extracts into the slot directory.
    assert entry.is_psp_slot is True
    assert entry.is_multi_file is False


def test_vita_server_only_uses_ux0_savedata_dir(tmp_path):
    server = {
        "PCSE00305": {
            "console_type": "VITA",
            "name": "Persona 4 Golden",
            "save_hash": "h",
        }
    }
    entry = server_only.build_server_only_entries(server, set(), tmp_path)[0]
    assert entry.save_path == (
        tmp_path / "saves" / "vita3k" / "ux0" / "user" / "00" / "savedata"
        / "PCSE00305"
    )
    assert entry.is_multi_file is True


def test_nds_server_only_predicts_sav_next_to_rom(tmp_path):
    server = {
        "NDS_chrono_trigger_usa": {
            "console_type": "NDS",
            "name": "Chrono Trigger (USA)",
            "save_hash": "h",
        }
    }
    entry = server_only.build_server_only_entries(server, set(), tmp_path)[0]
    assert entry.save_path == (
        tmp_path / "roms" / "nds" / "Chrono Trigger (USA).sav"
    )


def test_saturn_server_only_predicts_srm_at_retroarch_saves_root(tmp_path):
    server = {
        "SAT_T-4507G": {
            "console_type": "SAT",
            "name": "Grandia (USA)",
            "save_hash": "h",
        }
    }
    entry = server_only.build_server_only_entries(server, set(), tmp_path)[0]
    assert entry.save_path == (
        tmp_path / "saves" / "retroarch" / "saves" / "Grandia (USA).srm"
    )


def test_unknown_system_leaves_save_path_none(tmp_path):
    # Fake system code the predictor doesn't know — save_path stays None so
    # the Download button is hidden and the user is prompted to install the
    # ROM + rescan instead of writing saves to an unpredictable location.
    server = {
        "ZZZ_unknown_game": {
            "console_type": "ZZZ",
            "name": "Mystery Platform Game",
            "save_hash": "h",
        }
    }
    entry = server_only.build_server_only_entries(server, set(), tmp_path)[0]
    assert entry.save_path is None
