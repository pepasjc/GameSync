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
    # save_path is intentionally empty so Download Save stays hidden;
    # user should Download ROM first so the scanner can place it.
    assert all(e.save_path is None for e in entries)


def test_builder_skips_saves_already_in_seen_ids(tmp_path):
    server = {
        "GBA_zelda": {"save_hash": "h1", "name": "Zelda", "console_type": "GBA"},
        "SNES_chrono": {"save_hash": "h2", "name": "Chrono", "console_type": "SNES"},
    }
    entries = server_only.build_server_only_entries(server, {"GBA_zelda"}, tmp_path)
    assert [e.title_id for e in entries] == ["SNES_chrono"]


def test_builder_skips_ps3_and_gc_handled_by_dedicated_builders(tmp_path):
    """PS3 and GC have their own builders that set up save_path placeholders;
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
