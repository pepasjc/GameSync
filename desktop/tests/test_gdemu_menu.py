"""IP.BIN parsing and GDMENU / openMenu game-list generation."""

from __future__ import annotations

from pathlib import Path

import dreamcast_ipbin as ipbin
import gdemu_menu as menu
from rom_installer import build_install_plan, install_rom


def make_ip_bin(
    name: str = "CRAZY TAXI",
    product: str = "MK-51035",
    version: str = "V1.005",
    date: str = "19991223",
    region: str = "JUE",
    vga: bool = True,
    disc_no: str = "1",
    disc_total: str = "1",
    crc: str = "1234",
) -> bytes:
    data = bytearray(b" " * ipbin.IP_BIN_SIZE)

    def put(offset: int, text: str, length: int) -> None:
        raw = text.encode("ascii")[:length]
        data[offset : offset + len(raw)] = raw

    put(0x00, "SEGA SEGAKATANA ", 16)
    put(0x10, "SEGA ENTERPRISES", 16)
    put(0x20, crc, 4)
    put(0x25, "GD-ROM", 6)
    put(0x2B, disc_no, 1)
    put(0x2C, "/", 1)
    put(0x2D, disc_total, 1)
    put(0x30, region, 8)
    # Peripheral flags are 7 hex chars; byte 5 is the VGA-box bit.
    peripherals = list("E000001")
    peripherals[5] = "1" if vga else "0"
    put(0x38, "".join(peripherals), 7)
    put(0x40, product, 10)
    put(0x4A, version, 6)
    put(0x50, date, 8)
    put(0x60, "1ST_READ.BIN", 12)
    put(0x70, "SEGA", 16)
    put(0x80, name, 128)
    return bytes(data)


# ──────────────────────────────────────────────────────────────────────
# IP.BIN
# ──────────────────────────────────────────────────────────────────────
def test_parse_ip_bin_reads_every_menu_field():
    ip = ipbin.parse_ip_bin(make_ip_bin())
    assert ip is not None
    assert ip.name == "CRAZY TAXI"
    assert ip.disc == "1/1"
    assert ip.vga is True
    assert ip.region == "JUE"
    assert ip.version == "V1.005"
    assert ip.date == "19991223"
    assert ip.product == "MK-51035"
    assert ip.game_id == "MK51035"


def test_parse_ip_bin_reads_multi_disc_and_no_vga():
    ip = ipbin.parse_ip_bin(
        make_ip_bin(disc_no="2", disc_total="4", vga=False, region="J")
    )
    assert ip.disc == "2/4"
    assert ip.vga is False
    assert ip.region == "J"


def test_blank_disc_field_reads_as_single_disc():
    ip = ipbin.parse_ip_bin(make_ip_bin(disc_no=" ", disc_total=" "))
    assert ip.disc == "1/1"


def test_parse_ip_bin_rejects_a_non_dreamcast_header():
    assert ipbin.parse_ip_bin(b"PSP GAME" + b"\x00" * 300) is None


def test_read_ip_bin_finds_the_header_in_a_raw_2352_track(tmp_path):
    # Raw sectors put the payload at offset 16 behind the sync/header bytes.
    track = tmp_path / "track03.bin"
    track.write_bytes(b"\x00" * 16 + make_ip_bin() + b"\x00" * 4096)
    assert ipbin.read_ip_bin(track).product == "MK-51035"


def test_read_ip_bin_finds_a_header_past_the_first_chunk(tmp_path):
    image = tmp_path / "game.cdi"
    image.write_bytes(b"\x00" * (2 * 1024 * 1024) + make_ip_bin(name="SHENMUE"))
    assert ipbin.read_ip_bin(image).name == "SHENMUE"


def test_read_ip_bin_follows_a_gdi_to_its_data_track(tmp_path):
    (tmp_path / "track01.bin").write_bytes(b"\x00" * 4096)
    (tmp_path / "track03.bin").write_bytes(make_ip_bin(name="IKARUGA"))
    gdi = tmp_path / "game.gdi"
    gdi.write_text(
        "2\n"
        '1 0 4 2352 "track01.bin" 0\n'
        '3 45000 4 2352 "track03.bin" 0\n',
        encoding="utf-8",
    )
    assert ipbin.read_ip_bin(gdi).name == "IKARUGA"


def test_find_disc_image_prefers_the_gdi_sheet(tmp_path):
    (tmp_path / "track03.bin").write_bytes(b"")
    (tmp_path / "game.gdi").write_text("0\n", encoding="utf-8")
    assert ipbin.find_disc_image(tmp_path).name == "game.gdi"


# ──────────────────────────────────────────────────────────────────────
# List ini generation
# ──────────────────────────────────────────────────────────────────────
def _game_folder(root: Path, number: str, label: str, **ip_kwargs) -> Path:
    """A numbered folder holding a name.txt label and a disc with an IP.BIN."""
    folder = root / number
    folder.mkdir(parents=True)
    (folder / "name.txt").write_text(label + "\n", encoding="utf-8")
    ip_kwargs.setdefault("name", label.upper())
    (folder / "disc.cdi").write_bytes(make_ip_bin(**ip_kwargs))
    return folder


def test_openmenu_list_matches_the_card_manager_format(tmp_path):
    _game_folder(tmp_path, "01", "openMenu", name="OPENMENU", product="T0000")
    _game_folder(tmp_path, "02", "Crazy Taxi (USA)")

    text = menu.render_list_ini(menu.build_entries(tmp_path), menu.MENU_KIND_OPENMENU)

    assert text.splitlines()[:4] == [
        "[OPENMENU]",
        "num_items=2",
        "",
        "[ITEMS]",
    ]
    assert "02.name=Crazy Taxi (USA)" in text
    assert "02.disc=1/1" in text
    assert "02.vga=1" in text
    assert "02.region=JUE" in text
    assert "02.version=V1.005" in text
    assert "02.date=19991223" in text
    assert "02.product=MK51035" in text


def test_gdmenu_list_omits_the_product_key(tmp_path):
    _game_folder(tmp_path, "02", "Crazy Taxi (USA)")
    text = menu.render_list_ini(menu.build_entries(tmp_path), menu.MENU_KIND_GDMENU)
    assert text.startswith("[GDMENU]")
    assert "num_items" not in text
    assert ".product=" not in text
    assert "02.name=Crazy Taxi (USA)" in text


def test_name_txt_wins_over_the_disc_header_label(tmp_path):
    _game_folder(tmp_path, "02", "Crazy Taxi (USA)", name="CRAZY TAXI")
    entries = menu.build_entries(tmp_path)
    assert entries[0].name == "Crazy Taxi (USA)"


def test_folder_without_a_readable_header_still_gets_an_entry(tmp_path):
    folder = tmp_path / "02"
    folder.mkdir()
    (folder / "name.txt").write_text("Mystery Disc\n", encoding="utf-8")
    (folder / "disc.cdi").write_bytes(b"\x00" * 1024)
    entries = menu.build_entries(tmp_path)
    assert entries[0].name == "Mystery Disc"
    assert entries[0].disc == "1/1"
    assert entries[0].region == "JUE"


def test_entries_are_ordered_by_folder_number(tmp_path):
    for number in ("01", "10", "02", "100"):
        _game_folder(tmp_path, number, f"Game {number}")
    assert [e.number for e in menu.build_entries(tmp_path)] == [
        "01",
        "02",
        "10",
        "100",
    ]


def test_cached_entries_are_reused_while_name_txt_agrees(tmp_path, monkeypatch):
    _game_folder(tmp_path, "02", "Crazy Taxi (USA)")
    first = menu.build_entries(tmp_path)
    cache = {e.number: e for e in first}

    def explode(_folder):
        raise AssertionError("disc header re-read despite an unchanged name.txt")

    monkeypatch.setattr(menu, "read_folder_ip_bin", explode)
    again = menu.build_entries(tmp_path, cache)
    assert again[0].product == "MK51035"


def test_cache_is_dropped_when_the_folder_holds_a_different_game(tmp_path):
    _game_folder(tmp_path, "02", "Crazy Taxi (USA)")
    stale = {
        "02": menu.MenuEntry(number="02", name="Some Other Game", product="T00000")
    }
    entries = menu.build_entries(tmp_path, stale)
    assert entries[0].name == "Crazy Taxi (USA)"
    assert entries[0].product == "MK51035"


def test_parse_list_ini_round_trips_a_generated_file(tmp_path):
    _game_folder(tmp_path, "01", "openMenu", name="OPENMENU", product="T0000")
    _game_folder(tmp_path, "02", "Crazy Taxi (USA)")
    written = menu.refresh_menu_list(tmp_path, menu.MENU_KIND_OPENMENU)
    parsed = menu.parse_list_ini(written)
    assert set(parsed) == {"01", "02"}
    assert parsed["02"].name == "Crazy Taxi (USA)"
    assert parsed["02"].vga is True
    assert parsed["02"].product == "MK51035"


def test_menu_kind_detected_from_the_folder_01_disc(tmp_path):
    _game_folder(tmp_path, "01", "GDMENU", name="GDMENU")
    assert menu.detect_menu_kind(tmp_path) == menu.MENU_KIND_GDMENU


def test_menu_kind_falls_back_to_an_existing_list_file(tmp_path):
    (tmp_path / menu.GDMENU_LIST_FILE).write_text("[GDMENU]\n", encoding="utf-8")
    assert menu.detect_menu_kind(tmp_path) == menu.MENU_KIND_GDMENU


def test_menu_kind_defaults_to_openmenu_on_a_bare_card(tmp_path):
    assert menu.detect_menu_kind(tmp_path) == menu.MENU_KIND_OPENMENU


# ──────────────────────────────────────────────────────────────────────
# Install wiring
# ──────────────────────────────────────────────────────────────────────
def test_install_restages_the_list_at_the_card_root(tmp_path, monkeypatch):
    _game_folder(tmp_path, "01", "openMenu", name="OPENMENU", product="T0000")
    profile = {
        "name": "openMenu",
        "device_type": "openMenu",
        "path": str(tmp_path),
        "system": "DC",
        "rom_format": "auto",
    }
    rom = {
        "rom_id": "dc9",
        "system": "DC",
        "name": "Crazy Taxi (USA)",
        "filename": "Crazy Taxi (USA).cdi",
    }
    plan = build_install_plan(profile, rom, "DC")

    def fake_download(_plan, tmp_target, _cb=None):
        tmp_target.write_bytes(make_ip_bin())

    monkeypatch.setattr("rom_installer._download_rom", fake_download)
    written = install_rom(plan)

    list_file = tmp_path / menu.OPENMENU_LIST_FILE
    assert list_file in written
    text = list_file.read_text(encoding="utf-8")
    assert "num_items=2" in text
    assert "02.name=Crazy Taxi (USA)" in text
    assert "02.product=MK51035" in text
    # The menu image in folder 01 is left alone.
    assert not (tmp_path / "01" / menu.OPENMENU_LIST_FILE).exists()


# ──────────────────────────────────────────────────────────────────────
# Repair of folders installed before this layout existed
# ──────────────────────────────────────────────────────────────────────
def test_repair_renames_long_track_names_and_writes_metadata(tmp_path):
    from rom_installer import repair_gdemu_card

    folder = tmp_path / "03"
    folder.mkdir()
    long_stem = "Capcom vs. SNK 2 (Japan)"
    (folder / f"{long_stem}01.bin").write_bytes(b"\x00" * 32)
    (folder / f"{long_stem}03.bin").write_bytes(make_ip_bin(name="CAPCOM VS SNK 2"))
    (folder / f"{long_stem}.gdi").write_text(
        "2\n"
        f'1 0 4 2352 "{long_stem}01.bin" 0\n'
        f'3 45000 4 2352 "{long_stem}03.bin" 0\n',
        encoding="utf-8",
    )
    (folder / "name.txt").write_text(long_stem + "\n", encoding="utf-8")

    changes = repair_gdemu_card(
        {"device_type": "GDEMU", "path": str(tmp_path), "system": "DC"}, "DC"
    )

    # Repair also closes the gap left at 02, so the game moves down.
    folder = tmp_path / "02"
    assert (folder / "disc.gdi").is_file()
    assert (folder / "track01.bin").is_file()
    assert (folder / "track03.bin").is_file()
    sheet = (folder / "disc.gdi").read_text(encoding="utf-8")
    assert '"' not in sheet
    assert "3 45000 4 2352 track03.bin 0" in sheet
    assert (folder / "serial.txt").read_text(encoding="utf-8") == "MK-51035"
    assert (folder / "vga.txt").read_text(encoding="utf-8") == "1"
    assert (folder / "type.txt").read_text(encoding="utf-8") == "game"
    assert changes


def test_repair_leaves_the_menu_folder_alone(tmp_path):
    from rom_installer import repair_gdemu_card

    menu_folder = tmp_path / "01"
    menu_folder.mkdir()
    (menu_folder / "openmenu.gdi").write_text("0\n", encoding="utf-8")
    before = (menu_folder / "openmenu.gdi").read_text(encoding="utf-8")

    repair_gdemu_card({"device_type": "openMenu", "path": str(tmp_path)}, "DC")

    assert (menu_folder / "openmenu.gdi").read_text(encoding="utf-8") == before
    assert not (menu_folder / "disc.gdi").exists()


# ──────────────────────────────────────────────────────────────────────
# Alphabetical renumbering
# ──────────────────────────────────────────────────────────────────────
def test_folders_are_renumbered_alphabetically_from_02(tmp_path):
    from rom_installer import sort_gdemu_folders

    _game_folder(tmp_path, "01", "openMenu", name="OPENMENU")
    _game_folder(tmp_path, "02", "Sonic Adventure (USA)")
    _game_folder(tmp_path, "03", "Crazy Taxi (USA)")
    _game_folder(tmp_path, "04", "Ikaruga (Japan)")

    sort_gdemu_folders(tmp_path)

    assert menu.folder_name_txt(tmp_path / "02") == "Crazy Taxi (USA)"
    assert menu.folder_name_txt(tmp_path / "03") == "Ikaruga (Japan)"
    assert menu.folder_name_txt(tmp_path / "04") == "Sonic Adventure (USA)"
    # The menu disc stays put.
    assert menu.folder_name_txt(tmp_path / "01") == "openMenu"


def test_sorting_is_case_insensitive(tmp_path):
    from rom_installer import sort_gdemu_folders

    _game_folder(tmp_path, "01", "openMenu", name="OPENMENU")
    _game_folder(tmp_path, "02", "zombie revenge")
    _game_folder(tmp_path, "03", "ChuChu Rocket!")

    sort_gdemu_folders(tmp_path)

    assert menu.folder_name_txt(tmp_path / "02") == "ChuChu Rocket!"
    assert menu.folder_name_txt(tmp_path / "03") == "zombie revenge"


def test_already_sorted_card_is_left_alone(tmp_path):
    from rom_installer import sort_gdemu_folders

    _game_folder(tmp_path, "01", "openMenu", name="OPENMENU")
    _game_folder(tmp_path, "02", "Crazy Taxi (USA)")
    _game_folder(tmp_path, "03", "Ikaruga (Japan)")

    assert sort_gdemu_folders(tmp_path) == []


def test_renumber_closes_gaps_and_reports_moves(tmp_path):
    from rom_installer import sort_gdemu_folders

    _game_folder(tmp_path, "01", "openMenu", name="OPENMENU")
    _game_folder(tmp_path, "05", "Alpha")
    _game_folder(tmp_path, "09", "Beta")

    moves = sort_gdemu_folders(tmp_path)

    assert sorted(d.name for d in tmp_path.iterdir() if d.is_dir()) == [
        "01",
        "02",
        "03",
    ]
    assert menu.folder_name_txt(tmp_path / "02") == "Alpha"
    assert len(moves) == 2


def test_renumber_survives_a_run_that_died_mid_rename(tmp_path):
    from rom_installer import GDEMU_SORT_TMP_PREFIX, sort_gdemu_folders

    _game_folder(tmp_path, "01", "openMenu", name="OPENMENU")
    _game_folder(tmp_path, "02", "Beta")
    # A folder parked by a previous, interrupted run.
    stray = _game_folder(tmp_path, "07", "Alpha")
    stray.rename(tmp_path / f"{GDEMU_SORT_TMP_PREFIX}07")

    sort_gdemu_folders(tmp_path)

    assert not list(tmp_path.glob(f"{GDEMU_SORT_TMP_PREFIX}*"))
    assert menu.folder_name_txt(tmp_path / "02") == "Alpha"
    assert menu.folder_name_txt(tmp_path / "03") == "Beta"


def test_list_cache_follows_a_game_that_changed_folder(tmp_path, monkeypatch):
    _game_folder(tmp_path, "02", "Crazy Taxi (USA)")
    cache = {e.number: e for e in menu.build_entries(tmp_path)}

    # Same game, new folder number — the cache should still cover it.
    (tmp_path / "02").rename(tmp_path / "04")

    def explode(_folder):
        raise AssertionError("disc header re-read for a game that only moved")

    monkeypatch.setattr(menu, "read_folder_ip_bin", explode)
    entries = menu.build_entries(tmp_path, cache)
    assert [(e.number, e.name, e.product) for e in entries] == [
        ("04", "Crazy Taxi (USA)", "MK51035")
    ]
