"""GDEMU / openMenu / MemCard PRO DC profiles.

Covers the three Dreamcast-specific behaviours: numbered-folder ROM installs on
a GDEMU card, virtual-VMU save scanning for the two save-capable devices, and
the Game ID ⇄ ``DC_<slug>`` title id mapping that keeps a MemCard PRO save in
the same server slot as a Flycast one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import dreamcast as dc
import sync_engine as se
from rom_installer import (
    build_install_plan,
    default_rom_format,
    gdemu_folder_name,
    gdemu_target_folder,
    install_rom,
)

VMU = b"\x00" * dc.VMU_SIZE


# ──────────────────────────────────────────────────────────────────────
# Game ID normalization / title id mapping
# ──────────────────────────────────────────────────────────────────────
def _fake_dat(monkeypatch, index: dict[str, str]) -> None:
    """Point the DAT-backed indexes at a fixed serial → name map."""
    monkeypatch.setattr(dc, "serial_index", lambda: index)
    dc._serial_by_slug.cache_clear()
    monkeypatch.undo_hooks = getattr(monkeypatch, "undo_hooks", [])


def test_normalize_game_id_folds_both_spellings_of_a_sega_serial():
    # Sega's own discs say MK-51000 where the Redump DAT says 51000; the
    # canonical form drops the publisher prefix so both land on one id.
    assert dc.normalize_game_id("MK-51000") == "51000"
    assert dc.normalize_game_id("51000") == "51000"
    # The PAL region suffix is part of the product number and is kept.
    assert dc.normalize_game_id("MK-51064-50") == "5106450"
    # Third-party codes are untouched.
    assert dc.normalize_game_id("t-8106n") == "T8106N"
    assert dc.normalize_game_id(" HDR-0080 ") == "HDR0080"
    assert dc.normalize_game_id("") == ""


def test_title_id_is_the_serial_not_the_name():
    assert dc.title_id_for_game_id("T-1249M") == "DC_T1249M"
    assert dc.title_id_for_game_id("MK-51000") == "DC_51000"
    # A disc the DAT has never heard of still gets a serial id.
    assert dc.title_id_for_game_id("ZZ-9999-99") == "DC_ZZ999999"


def test_serial_id_round_trips_to_the_folder_the_console_creates():
    assert dc.game_ids_for_title_id("DC_T1249M") == ["T1249M"]
    # Numeric Sega codes are offered MK-first: that is what IP.BIN says, so
    # that is the folder a card creates.
    assert dc.game_ids_for_title_id("DC_51000") == ["MK51000", "51000"]


def test_a_rom_name_resolves_to_the_same_id_as_the_card_folder(monkeypatch):
    _fake_dat(monkeypatch, {"T8106N": "Shadow Man (USA)"})
    try:
        # An emulator profile only knows the filename...
        assert dc.title_id_for_name("Shadow Man (USA)") == "DC_T8106N"
        # ...and the card only knows the serial.  Same slot.
        assert dc.title_id_for_game_id("T-8106N") == "DC_T8106N"
    finally:
        dc._serial_by_slug.cache_clear()


def test_a_name_the_dat_does_not_know_keeps_a_slug_id(monkeypatch):
    _fake_dat(monkeypatch, {})
    try:
        assert dc.title_id_for_name("Some Homebrew (World)") == (
            "DC_some_homebrew_world"
        )
    finally:
        dc._serial_by_slug.cache_clear()


def test_legacy_slug_id_still_resolves_to_a_card_folder(monkeypatch):
    # Saves stored before Dreamcast moved to serial ids must still be writable
    # back to a card.
    _fake_dat(monkeypatch, {"51000": "Sonic Adventure (USA)"})
    try:
        assert dc.game_ids_for_title_id("DC_sonic_adventure_usa") == [
            "MK51000",
            "51000",
        ]
    finally:
        dc._serial_by_slug.cache_clear()


def test_card_folders_that_are_not_games_are_recognised():
    assert dc.is_game_folder("T1249M")
    assert not dc.is_game_folder("MemoryCard1")
    assert not dc.is_game_folder("openmenu")
    assert not dc.is_game_folder("")


def test_dreamcast_dat_resolves_a_real_serial():
    # Uses the DAT shipped in server/data/dats — skip if it isn't there.
    if not dc.serial_index():
        pytest.skip("Sega - Dreamcast.dat not available")
    assert "Sonic Adventure" in dc.game_name_for_serial("MK-51000")
    assert dc.title_id_for_name("Dead or Alive 2 (USA)") == "DC_T3601N"


# ──────────────────────────────────────────────────────────────────────
# GDEMU / openMenu ROM install layout
# ──────────────────────────────────────────────────────────────────────
def _gdemu_profile(tmp_path: Path, device_type: str = "GDEMU") -> dict:
    return {
        "name": device_type,
        "device_type": device_type,
        "path": str(tmp_path),
        "system": "DC",
        "rom_format": "auto",
    }


def test_folder_names_are_two_digits_until_they_cannot_be():
    assert gdemu_folder_name(2) == "02"
    assert gdemu_folder_name(99) == "99"
    assert gdemu_folder_name(100) == "100"


def test_first_game_installs_after_the_menu_folder(tmp_path):
    (tmp_path / "01").mkdir()
    assert gdemu_target_folder(tmp_path, "Crazy Taxi (USA)") == tmp_path / "02"


def test_empty_card_still_reserves_01_for_the_menu(tmp_path):
    assert gdemu_target_folder(tmp_path, "Crazy Taxi (USA)") == tmp_path / "02"


def test_next_game_takes_the_number_above_every_folder(tmp_path):
    for name in ("01", "02", "03"):
        (tmp_path / name).mkdir()
    assert gdemu_target_folder(tmp_path, "Shenmue (USA) (Disc 1)") == tmp_path / "04"


def test_reinstall_reuses_the_folder_the_game_already_occupies(tmp_path):
    (tmp_path / "01").mkdir()
    (tmp_path / "02").mkdir()
    (tmp_path / "03").mkdir()
    (tmp_path / "03" / "name.txt").write_text("Crazy Taxi (USA)\n", encoding="utf-8")
    assert gdemu_target_folder(tmp_path, "Crazy Taxi (USA)") == tmp_path / "03"


def test_chd_installs_as_a_converted_gdi_set(tmp_path):
    profile = _gdemu_profile(tmp_path)
    rom = {
        "rom_id": "dc1",
        "system": "DC",
        "name": "Crazy Taxi (USA)",
        "filename": "Crazy Taxi (USA).chd",
        "extract_formats": ["gdi"],
    }
    assert default_rom_format(profile, rom, "DC") == "gdi"
    plan = build_install_plan(profile, rom, "DC")
    assert plan.extract_format == "gdi"
    assert plan.extract_archive is True
    assert plan.target_is_directory is True
    assert plan.target_path == tmp_path / "02"
    assert plan.gdemu_name == "Crazy Taxi (USA)"


def test_loose_cdi_installs_as_a_single_file_in_its_folder(tmp_path):
    profile = _gdemu_profile(tmp_path, "openMenu")
    rom = {
        "rom_id": "dc2",
        "system": "DC",
        "name": "Homebrew Demo",
        "filename": "Homebrew Demo.cdi",
    }
    plan = build_install_plan(profile, rom, "DC")
    assert plan.extract_format is None
    assert plan.extract_archive is False
    assert plan.target_path == tmp_path / "02" / "Homebrew Demo.cdi"


def test_each_disc_of_a_multi_disc_game_gets_its_own_folder(tmp_path):
    profile = _gdemu_profile(tmp_path)
    (tmp_path / "01").mkdir()
    disc1 = build_install_plan(
        profile,
        {
            "rom_id": "d1",
            "system": "DC",
            "name": "Shenmue (USA) (Disc 1)",
            "filename": "Shenmue (USA) (Disc 1).chd",
        },
        "DC",
    )
    assert disc1.target_path == tmp_path / "02"
    disc1.target_path.mkdir(parents=True)
    (disc1.target_path / "name.txt").write_text(disc1.gdemu_name, encoding="utf-8")
    disc2 = build_install_plan(
        profile,
        {
            "rom_id": "d2",
            "system": "DC",
            "name": "Shenmue (USA) (Disc 2)",
            "filename": "Shenmue (USA) (Disc 2).chd",
        },
        "DC",
    )
    assert disc2.target_path == tmp_path / "03"


def test_install_writes_name_txt_for_the_menu(tmp_path, monkeypatch):
    profile = _gdemu_profile(tmp_path)
    rom = {
        "rom_id": "dc3",
        "system": "DC",
        "name": "Ikaruga (Japan)",
        "filename": "Ikaruga (Japan).cdi",
    }
    plan = build_install_plan(profile, rom, "DC")

    def fake_download(_plan, tmp_target, _cb=None):
        tmp_target.write_bytes(b"disc image")

    monkeypatch.setattr("rom_installer._download_rom", fake_download)
    written = install_rom(plan)

    # The image is renamed to the canonical short name a GD MENU Card Manager
    # card uses, so the .gdi/.cdi never carries spaces GDEMU has to parse.
    assert (tmp_path / "02" / "disc.cdi").read_bytes() == b"disc image"
    name_file = tmp_path / "02" / "name.txt"
    assert name_file in written
    assert name_file.read_text(encoding="utf-8").strip() == "Ikaruga (Japan)"


# ──────────────────────────────────────────────────────────────────────
# MemCard PRO DC scanning
# ──────────────────────────────────────────────────────────────────────
def _memcard_dc_card(root: Path, game_id: str = "T8106N") -> Path:
    game_dir = root / "Dreamcast" / game_id
    game_dir.mkdir(parents=True)
    slot1 = game_dir / f"{game_id}-1.vmu"
    slot1.write_bytes(VMU)
    (game_dir / f"{game_id}-2.vmu").write_bytes(VMU)
    return slot1


def test_memcard_pro_dc_scan_finds_channel_one_only(tmp_path, monkeypatch):
    _fake_dat(monkeypatch, {"T8106N": "Sonic Adventure (USA)"})
    slot1 = _memcard_dc_card(tmp_path)

    profile = {
        "name": "MemCard PRO DC",
        "device_type": "MemCard Pro DC",
        "path": str(tmp_path),
        "system": "DC",
    }
    saves = se.scan_profile(profile)

    assert len(saves) == 1
    save = saves[0]
    assert save.path == slot1
    assert save.system == "DC"
    assert save.title_id == "DC_T8106N"
    assert save.game_name == "Sonic Adventure (USA)"
    dc._serial_by_slug.cache_clear()


def test_memcard_pro_dc_scan_accepts_the_dreamcast_folder_as_root(tmp_path):
    _memcard_dc_card(tmp_path, "ZZ999999")
    saves = se._scan_memcard_pro_dc(tmp_path / "Dreamcast")
    assert [s.title_id for s in saves] == ["DC_ZZ999999"]


def test_memcard_pro_dc_scan_skips_the_shared_and_menu_cards(tmp_path):
    # A real card also holds MemoryCard1 and openmenu — see K:/Dreamcast.
    _memcard_dc_card(tmp_path, "T1249M")
    _memcard_dc_card(tmp_path, "MemoryCard1")
    _memcard_dc_card(tmp_path, "openmenu")
    saves = se._scan_memcard_pro_dc(tmp_path)
    assert [s.title_id for s in saves] == ["DC_T1249M"]


def test_memcard_pro_dc_skips_a_folder_without_channel_one(tmp_path):
    game_dir = tmp_path / "Dreamcast" / "ZZ999999"
    game_dir.mkdir(parents=True)
    (game_dir / "ZZ999999-2.vmu").write_bytes(VMU)
    assert se._scan_memcard_pro_dc(tmp_path) == []


# ──────────────────────────────────────────────────────────────────────
# openMenu Serial VMU scanning
# ──────────────────────────────────────────────────────────────────────
def test_openmenu_scan_reads_slot1_and_title_txt(tmp_path):
    saves_root = tmp_path / "serial" / "OPENMENU" / "SAVES" / "ZZ999999"
    saves_root.mkdir(parents=True)
    (saves_root / "SLOT1.VMU").write_bytes(VMU)
    (saves_root / "SLOT2.VMU").write_bytes(VMU)
    (saves_root / "TITLE.TXT").write_text("Some Homebrew\n", encoding="utf-8")

    profile = {
        "name": "openMenu",
        "device_type": "openMenu",
        "path": str(tmp_path / "gdemu"),
        "save_folder": str(tmp_path / "serial"),
        "system": "DC",
    }
    (tmp_path / "gdemu").mkdir()
    saves = se.scan_profile(profile)

    assert len(saves) == 1
    assert saves[0].path == saves_root / "SLOT1.VMU"
    assert saves[0].title_id == "DC_ZZ999999"
    assert saves[0].game_name == "Some Homebrew"


def test_gdemu_profile_reports_no_saves(tmp_path):
    (tmp_path / "02").mkdir()
    profile = {
        "name": "GDEMU",
        "device_type": "GDEMU",
        "path": str(tmp_path),
        "system": "DC",
    }
    assert se.scan_profile(profile) == []


# ──────────────────────────────────────────────────────────────────────
# Download destinations for server-only saves
# ──────────────────────────────────────────────────────────────────────
def test_download_path_reuses_an_existing_memcard_folder(tmp_path):
    _memcard_dc_card(tmp_path, "ZZ999999")
    profile = {"device_type": "MemCard Pro DC", "path": str(tmp_path)}
    dest = se.build_dreamcast_vmu_path(profile, "DC_zz999999")
    assert dest == tmp_path / "Dreamcast" / "ZZ999999" / "ZZ999999-1.vmu"


def test_download_path_creates_the_openmenu_layout(tmp_path):
    profile = {"device_type": "openMenu", "save_folder": str(tmp_path)}
    dest = se.build_dreamcast_vmu_path(profile, "DC_ZZ999999")
    assert dest == tmp_path / "OPENMENU" / "SAVES" / "ZZ999999" / "SLOT1.VMU"


def test_download_path_is_none_when_no_serial_is_known(tmp_path, monkeypatch):
    _fake_dat(monkeypatch, {})
    try:
        profile = {"device_type": "MemCard Pro DC", "path": str(tmp_path)}
        # A legacy name-slug id for a game the DAT can't resolve: the card
        # files by serial, so there is nowhere to put it.
        assert se.build_dreamcast_vmu_path(profile, "DC_some_unknown_game") is None
    finally:
        dc._serial_by_slug.cache_clear()


def test_finalize_openmenu_download_labels_the_folder(tmp_path):
    vmu = tmp_path / "OPENMENU" / "SAVES" / "ZZ999999" / "SLOT1.VMU"
    vmu.parent.mkdir(parents=True)
    vmu.write_bytes(VMU)
    se.finalize_openmenu_download(vmu, "Some Homebrew")
    assert (vmu.parent / "TITLE.TXT").read_text(encoding="utf-8").strip() == (
        "Some Homebrew"
    )


def test_download_path_prefers_an_existing_bare_number_folder(tmp_path):
    existing = tmp_path / "Dreamcast" / "51000"
    existing.mkdir(parents=True)
    profile = {"device_type": "MemCard Pro DC", "path": str(tmp_path)}
    dest = se.build_dreamcast_vmu_path(profile, "DC_51000")
    assert dest == existing / "51000-1.vmu"


def test_download_path_creates_the_mk_folder_when_none_exists(tmp_path):
    profile = {"device_type": "MemCard Pro DC", "path": str(tmp_path)}
    dest = se.build_dreamcast_vmu_path(profile, "DC_51000")
    assert dest == tmp_path / "Dreamcast" / "MK51000" / "MK51000-1.vmu"


def test_memcard_pro_dc_scan_ignores_a_volume_without_a_dreamcast_folder(tmp_path):
    # Card readers hand out a different drive letter on each reconnect, so a
    # profile can end up pointed at an unrelated volume.  Walking it would be
    # slow and would invent saves out of whatever folders happened to be there.
    (tmp_path / "Some Other Card").mkdir()
    (tmp_path / "Some Other Card" / "whatever-1.vmu").write_bytes(VMU)
    assert se._scan_memcard_pro_dc(tmp_path) == []


def test_openmenu_scan_ignores_a_volume_without_the_saves_folder(tmp_path):
    (tmp_path / "T1249M").mkdir()
    (tmp_path / "T1249M" / "SLOT1.VMU").write_bytes(VMU)
    assert se._scan_openmenu_vmu(tmp_path) == []


def test_openmenu_scan_accepts_the_saves_folder_as_root(tmp_path):
    saves = tmp_path / "SAVES" / "T1249M"
    saves.mkdir(parents=True)
    (saves / "SLOT1.VMU").write_bytes(VMU)
    assert [s.title_id for s in se._scan_openmenu_vmu(tmp_path / "SAVES")] == [
        "DC_T1249M"
    ]
