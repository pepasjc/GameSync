from pathlib import Path
import zipfile

import pytest

from rom_installer import (
    PSIO_MAX_NAME,
    build_install_plan,
    build_install_plans,
    choose_extract_format,
    clean_ps1_title,
    default_rom_format,
    derive_download_filename,
    group_multidisc_roms,
    mister_remote_rom_dir,
    opl_disc_id,
    opl_ps2_media,
    profile_systems,
    psio_safe_name,
    resolve_profile_rom_folder,
    sanitize_installed_files,
    strip_disc_tag,
    repair_installed_files,
    repair_opl_popstarter,
    _safe_extract_zip,
    _install_popstarter_app,
)


def test_real_hardware_disc_chd_defaults_to_extracted_cue(tmp_path):
    profile = {
        "name": "PS1 SD",
        "device_type": "CD Folder",
        "path": str(tmp_path),
        "system": "PS1",
        "rom_format": "auto",
    }
    rom = {
        "rom_id": "SLUS00001",
        "system": "PS1",
        "name": "Example Game",
        "filename": "Example Game (USA).chd",
        "extract_format": "cue",
        "extract_formats": ["cue"],
    }

    plan = build_install_plan(profile, rom, "PS1")

    assert plan.extract_format == "cue"
    assert plan.extract_archive is True
    assert plan.target_path == tmp_path / "Example Game"


def test_psio_profile_defaults_to_psio_archive(tmp_path):
    profile = {
        "name": "PSIO SD",
        "device_type": "PSIO",
        "path": str(tmp_path),
        "system": "PS1",
        "rom_format": "auto",
    }
    rom = {
        "rom_id": "SLUS00001",
        "system": "PS1",
        "name": "Example Game",
        "filename": "Example Game (USA).chd",
        "extract_format": "cue",
        "extract_formats": ["cue", "eboot", "psio"],
    }

    plan = build_install_plan(profile, rom, "PS1")

    assert plan.extract_format == "psio"
    assert plan.extract_archive is True
    assert plan.target_path == tmp_path / "Example Game"
    assert derive_download_filename("Example Game (USA).chd", "psio") == "Example Game (USA).cu2"


def test_3ds_profile_defaults_follow_hardware_vs_emulator(tmp_path):
    rom = {
        "rom_id": "0004000000000000",
        "system": "3DS",
        "name": "3DS Game",
        "filename": "3DS Game.3ds",
        "extract_formats": ["cia", "decrypted_cci"],
    }

    hardware = {"device_type": "Generic", "path": str(tmp_path), "system": "3DS"}
    emulator = {"device_type": "RetroArch", "path": str(tmp_path), "systems": [{"system": "3DS"}]}

    assert choose_extract_format(hardware, rom, "3DS") == "cia"
    assert choose_extract_format(emulator, rom, "3DS") == "decrypted_cci"
    assert derive_download_filename("3DS Game.3ds", "cia") == "3DS Game.cia"
    assert derive_download_filename("3DS Game.3ds", "decrypted_cci") == "3DS Game.cci"


def test_profile_rom_folder_uses_override_before_global_root(tmp_path):
    root = tmp_path / "root"
    override = tmp_path / "saturn"
    profile = {
        "device_type": "RetroArch",
        "path": str(root),
        "systems": [
            {"system": "SAT", "enabled": True, "rom_folder": str(override)},
        ],
    }

    assert resolve_profile_rom_folder(profile, "SAT") == override


def test_emudeck_profile_uses_system_subfolder(tmp_path):
    profile = {
        "device_type": "EmuDeck",
        "path": str(tmp_path),
        "systems": [{"system": "GBA", "enabled": True}],
    }

    assert resolve_profile_rom_folder(profile, "GBA") == tmp_path / "gba"


def test_emudeck_pcfx_installs_into_pcfx_folder(tmp_path):
    profile = {
        "device_type": "EmuDeck",
        "path": str(tmp_path),
        "systems": [{"system": "PCFX", "enabled": True}],
    }

    assert resolve_profile_rom_folder(profile, "PCFX") == tmp_path / "pcfx"


def test_retroarch_beetle_pcfx_core_folder_maps_to_pcfx():
    from sync_engine import RETROARCH_CORE_MAP, RETROARCH_SYSTEM_CORES

    assert RETROARCH_CORE_MAP["Beetle PC-FX"] == "PCFX"
    assert RETROARCH_SYSTEM_CORES["PCFX"] == ["Beetle PC-FX"]


def test_mister_network_install_refuses_pcfx_without_a_core():
    profile = _mister_profile(target="sd")
    with pytest.raises(ValueError):
        mister_remote_rom_dir(profile, "PCFX")


def _mister_profile(path: str = "", target: str = "") -> dict:
    profile: dict = {
        "name": "MiSTer",
        "device_type": "MiSTer",
        "path": path,
        "systems": [
            {"system": "PS1", "enabled": True},
            {"system": "MD", "enabled": True},
        ],
    }
    if target:
        profile["mister_target"] = target
    return profile


def test_mister_local_prefers_existing_legacy_folder(tmp_path):
    (tmp_path / "Genesis").mkdir()
    profile = _mister_profile(str(tmp_path))
    assert resolve_profile_rom_folder(profile, "MD") == tmp_path / "Genesis"


def test_mister_local_defaults_to_modern_folder_name(tmp_path):
    profile = _mister_profile(str(tmp_path))
    assert resolve_profile_rom_folder(profile, "MD") == tmp_path / "MegaDrive"
    assert resolve_profile_rom_folder(profile, "PS1") == tmp_path / "PSX"


def test_mister_chd_installs_raw_no_conversion(tmp_path):
    profile = _mister_profile(str(tmp_path))
    rom = {
        "rom_id": "SLUS00001",
        "system": "PS1",
        "name": "Example Game",
        "filename": "Example Game (USA).chd",
        "extract_format": "cue",
        "extract_formats": ["cue", "eboot", "psio"],
    }
    assert default_rom_format(profile, rom, "PS1") is None
    plan = build_install_plan(profile, rom, "PS1")
    assert plan.extract_format is None
    assert plan.extract_archive is False
    # CD games get a per-game subfolder — the PSX core names the autosave
    # memory card after the folder.
    assert (
        plan.target_path
        == tmp_path / "PSX" / "Example Game (USA)" / "Example Game (USA).chd"
    )


def test_mister_network_usb_plan_targets_posix_path():
    profile = _mister_profile(target="usb")
    rom = {
        "rom_id": "SLUS00001",
        "system": "PS1",
        "name": "Example Game",
        "filename": "Example Game (USA).chd",
        "extract_formats": ["cue", "eboot", "psio"],
    }
    plan = build_install_plan(profile, rom, "PS1")
    assert plan.mister_remote == "usb"
    assert (
        str(plan.target_path)
        == "/media/usb0/games/PSX/Example Game (USA)/Example Game (USA).chd"
    )


def test_mister_multidisc_chds_share_one_game_folder():
    profile = _mister_profile(target="usb")
    plans = [
        build_install_plan(
            profile,
            {
                "rom_id": f"SLUS0086{n}",
                "system": "PS1",
                "name": f"Final Fantasy VII (Disc {n}) (USA)",
                "filename": f"Final Fantasy VII (Disc {n}) (USA).chd",
            },
            "PS1",
        )
        for n in (1, 2)
    ]
    parents = {str(p.target_path).rsplit("/", 1)[0] for p in plans}
    # Disc tag stripped from the folder → both discs share one card.
    assert parents == {"/media/usb0/games/PSX/Final Fantasy VII (USA)"}
    assert str(plans[0].target_path).endswith("Final Fantasy VII (Disc 1) (USA).chd")


def test_index_catalog_by_title_groups_discs_and_dumps():
    from rom_installer import index_catalog_by_title

    index = index_catalog_by_title(_multidisc_catalog())
    assert set(index) == {"SCUS94163", "SLUS00001"}
    assert len(index["SCUS94163"]) == 3  # three discs of one game
    assert len(index["SLUS00001"]) == 1


def test_catalog_install_groups_collapses_discs_but_keeps_dumps():
    """One entry per dump; a multi-disc set is a single entry."""
    from rom_installer import catalog_install_groups

    profile = _mister_profile(target="usb")
    # Two dumps of one Saturn game (the real SAT_T-9527G case) …
    dumps = [
        {
            "rom_id": "SAT_T-9527G__castlevania",
            "title_id": "SAT_T-9527G",
            "system": "SAT",
            "name": "Castlevania - Symphony of the Night (Japan) (2M) [T-En]",
            "filename": "Castlevania - Symphony of the Night (Japan) (2M) [T-En].chd",
        },
        {
            "rom_id": "SAT_T-9527G__dracula_x",
            "title_id": "SAT_T-9527G",
            "system": "SAT",
            "name": "Dracula X - Nocturne in the Moonlight (Japan) [T-En]",
            "filename": "Dracula X - Nocturne in the Moonlight (Japan) [T-En].chd",
        },
    ]
    assert len(catalog_install_groups(profile, dumps, "SAT")) == 2

    # … while three discs of one PS1 game collapse into a single entry.
    discs = [r for r in _multidisc_catalog() if r["title_id"] == "SCUS94163"]
    groups = catalog_install_groups(profile, discs, "PS1")
    assert len(groups) == 1
    assert groups[0]["disc_total"] == 3


def test_saturn_discs_group_without_server_disc_metadata():
    """The server only computes disc groups for PS1.

    A Saturn set arrives as unrelated rows sharing a serial, so the disc tag
    in the filename has to do the grouping — while two different dumps of
    the same game stay separate entries.
    """
    from rom_installer import build_title_install_plans, catalog_install_groups

    profile = _mister_profile(target="usb")
    rows = [
        {
            "rom_id": f"SAT_T-4507G__{dump}_{disc}",
            "title_id": "SAT_T-4507G",
            "system": "SAT",
            "name": f"Grandia (Japan) (Disc {disc}) (4M) [{dump}]",
            "filename": f"Grandia (Japan) (Disc {disc}) (4M) [{dump}].chd",
            # No disc_total / primary_rom_id — exactly what the server sends.
        }
        for dump in ("T-En v0.9.3", "T-En v1.1.1")
        for disc in (2, 1)  # deliberately out of order
    ]

    groups = catalog_install_groups(profile, rows, "SAT")
    assert len(groups) == 2  # one per dump, not four loose discs
    assert all(g["disc_total"] == 2 for g in groups)

    plans = build_title_install_plans(profile, groups[0], "SAT")
    assert len(plans) == 2
    # Both discs share one folder, and Disc 1 comes first.
    assert {str(p.target_path.parent) for p in plans} == {
        "/media/usb0/games/Saturn/Grandia (Japan) (4M) [T-En v0.9.3]"
    }
    assert "(Disc 1)" in plans[0].target_path.name
    assert "(Disc 2)" in plans[1].target_path.name


def test_mister_refuses_a_system_it_has_no_folder_for():
    """A 3DS ROM must not land loose in the MiSTer games root."""
    profile = _mister_profile(target="usb")
    rom = {
        "rom_id": "0004000000030800",
        "system": "3DS",
        "name": "Mario Kart 7",
        "filename": "Mario Kart 7 (USA).cia",
    }
    with pytest.raises(ValueError, match="no games folder"):
        build_install_plan(profile, rom, "3DS")


def test_build_title_install_plans_covers_every_disc():
    from rom_installer import build_title_install_plans, catalog_install_groups

    profile = _mister_profile(target="usb")
    discs = [r for r in _multidisc_catalog() if r["title_id"] == "SCUS94163"]
    group = catalog_install_groups(profile, discs, "PS1")[0]
    plans = build_title_install_plans(profile, group, "PS1")

    assert len(plans) == 3
    assert {str(p.target_path.parent) for p in plans} == {
        "/media/usb0/games/PSX/Final Fantasy VII (USA)"
    }


def test_profile_can_install():
    from rom_installer import profile_can_install

    assert profile_can_install({"device_type": "Generic", "path": "C:/roms"}) is True
    assert profile_can_install(_mister_profile(target="usb")) is True  # no path
    assert profile_can_install({"device_type": "Generic", "path": ""}) is False
    assert profile_can_install(None) is False


def test_catalog_for_system_is_cached(monkeypatch):
    import rom_installer

    calls = []

    def fake_fetch(system="", search=""):
        calls.append(system)
        return [{"title_id": "GBA_x", "filename": "x.gba"}]

    monkeypatch.setattr(rom_installer, "fetch_rom_catalog", fake_fetch)
    rom_installer.clear_catalog_cache()

    assert rom_installer.catalog_for_system("GBA")
    assert rom_installer.catalog_for_system("gba")  # same system, cached
    assert calls == ["GBA"]
    assert rom_installer.catalog_roms_for_title("GBA", "GBA_x")
    assert rom_installer.catalog_roms_for_title("GBA", "GBA_missing") == []
    rom_installer.clear_catalog_cache()


def test_safe_folder_name_keeps_version_tags(tmp_path):
    from rom_installer import safe_folder_name

    # A real ROM extension is dropped …
    assert safe_folder_name("Some Game (USA).chd") == "Some Game (USA)"
    # … but a trailing version/translation tag is not an extension.
    assert (
        safe_folder_name("Ecsaform (Japan) [T-En by Aishsha & Pennywise v1.1]")
        == "Ecsaform (Japan) [T-En by Aishsha & Pennywise v1.1]"
    )
    assert safe_folder_name("Game v1.2") == "Game v1.2"
    assert safe_folder_name("") == "download"


def test_mister_multidisc_folder_keeps_full_translation_tag():
    profile = _mister_profile(target="usb")
    rom = {
        "rom_id": "SLPS00001",
        "system": "PS1",
        "name": "Ecsaform (Japan) (Disc 1) [T-En by Aishsha v1.1]",
        "filename": "Ecsaform (Japan) (Disc 1) [T-En by Aishsha v1.1].chd",
    }
    plan = build_install_plan(profile, rom, "PS1")
    assert (
        str(plan.target_path.parent)
        == "/media/usb0/games/PSX/Ecsaform (Japan) [T-En by Aishsha v1.1]"
    )


def test_mister_catalog_collapses_multidisc_into_one_row():
    profile = _mister_profile(target="usb")
    rows = group_multidisc_roms(profile, _multidisc_catalog(), "PS1")

    # 3 FFVII discs collapse to one row; the single-disc game passes through.
    assert len(rows) == 2
    group = rows[0]
    assert group["install_members"] is True
    assert group["disc_total"] == 3
    assert group["name"] == "Final Fantasy VII (USA)"  # disc tag stripped
    assert group["size"] == 700 + 710 + 720
    assert rows[1]["rom_id"] == "SLUS00001"
    assert "disc_members" not in rows[1]


def test_mister_grouped_row_expands_to_one_plan_per_disc():
    profile = _mister_profile(target="usb")
    rows = group_multidisc_roms(profile, _multidisc_catalog(), "PS1")

    plans = build_install_plans(profile, rows[0], "PS1")
    assert len(plans) == 3
    assert {str(p.target_path.parent) for p in plans} == {
        "/media/usb0/games/PSX/Final Fantasy VII (USA)"
    }
    assert [p.target_path.name for p in plans] == [
        "Final Fantasy VII (Disc 1) (USA).chd",
        "Final Fantasy VII (Disc 2) (USA).chd",
        "Final Fantasy VII (Disc 3) (USA).chd",
    ]
    # Each disc is fetched by its own rom_id, not the group's primary.
    assert [p.rom_id for p in plans] == ["SLUS00868", "SLUS00869", "SLUS00870"]

    # A plain single-disc row still yields exactly one plan.
    assert len(build_install_plans(profile, rows[1], "PS1")) == 1


def test_psio_multidisc_still_collapses_to_one_combined_download(tmp_path):
    profile = {
        "device_type": "PSIO",
        "path": str(tmp_path),
        "system": "PS1",
        "rom_format": "auto",
    }
    rows = group_multidisc_roms(profile, _multidisc_catalog(), "PS1")
    group = rows[0]
    assert group.get("install_members") is None  # server stitches the set
    plans = build_install_plans(profile, group, "PS1")
    assert len(plans) == 1
    assert plans[0].rom_id == "SLUS00868"  # primary id returns all discs


def test_mister_cart_systems_stay_flat(tmp_path):
    profile = _mister_profile(str(tmp_path))
    rom = {
        "rom_id": "GBA_game",
        "system": "GBA",
        "name": "Game",
        "filename": "Game (USA).gba",
    }
    profile["systems"].append({"system": "GBA", "enabled": True})
    plan = build_install_plan(profile, rom, "GBA")
    assert plan.target_path == tmp_path / "GBA" / "Game (USA).gba"


def test_mister_saturn_chd_gets_game_folder(tmp_path):
    profile = _mister_profile(str(tmp_path))
    rom = {
        "rom_id": "SAT_panzer",
        "system": "SAT",
        "name": "Panzer Dragoon",
        "filename": "Panzer Dragoon (USA).chd",
    }
    plan = build_install_plan(profile, rom, "SAT")
    assert plan.extract_format is None  # CHD stays raw on MiSTer
    assert (
        plan.target_path
        == tmp_path / "Saturn" / "Panzer Dragoon (USA)" / "Panzer Dragoon (USA).chd"
    )


def test_mister_network_sd_plan_targets_fat_games():
    profile = _mister_profile(target="sd")
    rom = {
        "rom_id": "MD_sonic",
        "system": "MD",
        "name": "Sonic",
        "filename": "Sonic (USA).md",
    }
    plan = build_install_plan(profile, rom, "MD")
    assert plan.mister_remote == "sd"
    assert str(plan.target_path) == "/media/fat/games/MegaDrive/Sonic (USA).md"


def test_mister_local_target_keeps_local_paths(tmp_path):
    profile = _mister_profile(str(tmp_path), target="local")
    rom = {
        "rom_id": "MD_sonic",
        "system": "MD",
        "name": "Sonic",
        "filename": "Sonic (USA).md",
    }
    plan = build_install_plan(profile, rom, "MD")
    assert plan.mister_remote == ""
    assert plan.target_path == tmp_path / "MegaDrive" / "Sonic (USA).md"


def _multidisc_catalog():
    # Three discs of one game (shared title_id/serial) plus a single-disc game.
    return [
        {
            "rom_id": "SLUS00868",
            "primary_rom_id": "SLUS00868",
            "title_id": "SCUS94163",
            "system": "PS1",
            "name": "Final Fantasy VII (Disc 1) (USA)",
            "filename": "Final Fantasy VII (Disc 1) (USA).chd",
            "size": 700,
            "disc_index": 1,
            "disc_total": 3,
            "extract_formats": ["cue", "eboot", "psio"],
        },
        {
            "rom_id": "SLUS00869",
            "primary_rom_id": "SLUS00868",
            "title_id": "SCUS94163",
            "system": "PS1",
            "name": "Final Fantasy VII (Disc 2) (USA)",
            "filename": "Final Fantasy VII (Disc 2) (USA).chd",
            "size": 710,
            "disc_index": 2,
            "disc_total": 3,
            "extract_formats": ["cue", "eboot", "psio"],
        },
        {
            "rom_id": "SLUS00870",
            "primary_rom_id": "SLUS00868",
            "title_id": "SCUS94163",
            "system": "PS1",
            "name": "Final Fantasy VII (Disc 3) (USA)",
            "filename": "Final Fantasy VII (Disc 3) (USA).chd",
            "size": 720,
            "disc_index": 3,
            "disc_total": 3,
            "extract_formats": ["cue", "eboot", "psio"],
        },
        {
            "rom_id": "SLUS00001",
            "primary_rom_id": "SLUS00001",
            "title_id": "SLUS00001",
            "system": "PS1",
            "name": "Single Disc Game (USA)",
            "filename": "Single Disc Game (USA).chd",
            "size": 500,
            "disc_index": 1,
            "disc_total": 1,
            "extract_formats": ["cue", "eboot", "psio"],
        },
    ]


def test_strip_disc_tag():
    assert strip_disc_tag("Final Fantasy VII (Disc 1) (USA)") == "Final Fantasy VII (USA)"
    assert strip_disc_tag("Game (Disk 2 of 4)") == "Game"
    assert strip_disc_tag("No Disc Game") == "No Disc Game"


def test_clean_ps1_title_strips_translation_and_disc_tags():
    assert (
        clean_ps1_title(
            "Ace Combat 3 - Electrosphere (Japan, Asia) (Rev 1) [T-En by Team NEMO v0.9] (Disc 1)"
        )
        == "Ace Combat 3 - Electrosphere (Japan, Asia) (Rev 1)"
    )


def test_psio_folder_strips_translation_tag(tmp_path):
    profile = {"device_type": "PSIO", "path": str(tmp_path), "system": "PS1", "rom_format": "auto"}
    rom = {
        "rom_id": "SLPS00001",
        "system": "PS1",
        "name": "Some Game (Japan) [T-En by Someone]",
        "filename": "Some Game (Japan) [T-En by Someone].chd",
        "extract_formats": ["cue", "eboot", "psio"],
    }
    plan = build_install_plan(profile, rom, "PS1")
    assert plan.extract_format == "psio"
    assert plan.target_path == tmp_path / "Some Game (Japan)"


def test_psio_collapses_multidisc_into_single_entry(tmp_path):
    profile = {"device_type": "PSIO", "path": str(tmp_path), "system": "PS1", "rom_format": "auto"}
    grouped = group_multidisc_roms(profile, _multidisc_catalog(), "PS1")

    # 3-disc FFVII becomes one row; single-disc game untouched.
    assert len(grouped) == 2
    combined = next(r for r in grouped if r["rom_id"] == "SLUS00868")
    assert len(combined["disc_members"]) == 3
    assert combined["disc_total"] == 3
    assert combined["size"] == 700 + 710 + 720
    assert combined["name"] == "Final Fantasy VII (USA)"

    # Installing the combined entry targets the primary rom id (server returns
    # the full multi-disc set) as a PSIO archive folder.
    plan = build_install_plan(profile, combined, "PS1")
    assert plan.rom_id == "SLUS00868"
    assert plan.extract_format == "psio"
    assert plan.extract_archive is True
    assert plan.target_path == tmp_path / "Final Fantasy VII (USA)"


def test_same_serial_single_disc_revisions_not_combined(tmp_path):
    # Two versions of a single-disc game share a serial (title_id) but carry
    # NO (Disc N) tag — must stay as two separate rows, never a "multi-disc".
    profile = {"device_type": "PSIO", "path": str(tmp_path), "system": "PS1", "rom_format": "auto"}
    roms = [
        {
            "rom_id": "SCUS94228",
            "primary_rom_id": "SCUS94228",
            "title_id": "SCUS94228",
            "system": "PS1",
            "name": "Alundra (USA)",
            "filename": "Alundra (USA).chd",
            "disc_index": 1,
            "disc_total": 1,
            "extract_formats": ["cue", "eboot", "psio"],
        },
        {
            "rom_id": "SCUS94228__rev1",
            "primary_rom_id": "SCUS94228",
            "title_id": "SCUS94228",
            "system": "PS1",
            "name": "Alundra (USA) (Rev 1) [Un-Worked Design by Supper v1]",
            "filename": "Alundra (USA) (Rev 1) [Un-Worked Design by Supper v1].chd",
            "disc_index": 1,
            "disc_total": 1,
            "extract_formats": ["cue", "eboot", "psio"],
        },
    ]
    grouped = group_multidisc_roms(profile, roms, "PS1")
    assert len(grouped) == 2
    assert all("disc_members" not in r for r in grouped)


def test_raw_format_keeps_discs_separate(tmp_path):
    # Generic profile + raw output: server can't stitch discs, so each disc
    # must remain its own catalog row.
    profile = {"device_type": "Generic", "path": str(tmp_path), "system": "PS1"}
    grouped = group_multidisc_roms(profile, _multidisc_catalog(), "PS1", override_format="raw")
    assert len(grouped) == 4
    assert all("disc_members" not in r for r in grouped)


def test_safe_extract_zip_rejects_path_traversal(tmp_path):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../escape.bin", b"bad")

    with pytest.raises(ValueError):
        _safe_extract_zip(archive, tmp_path / "out")


def test_clean_ps1_title_strips_non_ascii_and_caps_length():
    assert clean_ps1_title("Pokémon (USA) [T-En]") == "Pokemon (USA)"
    assert clean_ps1_title("A" * 80) == "A" * PSIO_MAX_NAME
    assert len(clean_ps1_title("A" * 80)) <= PSIO_MAX_NAME


def test_psio_safe_name_reserves_suffix_room():
    assert psio_safe_name("A" * 80, reserve=4) == "A" * (PSIO_MAX_NAME - 4)
    assert psio_safe_name("Pokémon", reserve=0) == "Pokemon"


def test_sanitize_renames_non_ascii_pair_and_folder(tmp_path):
    profile = {"device_type": "PSIO", "path": str(tmp_path), "system": "PS1"}
    game = tmp_path / "Pokémon (USA)"
    game.mkdir()
    (game / "Pokémon (USA).bin").write_bytes(b"BIN")
    (game / "Pokémon (USA).cu2").write_bytes(b"CU2")

    renames = sanitize_installed_files(profile, "PS1")

    new_game = tmp_path / "Pokemon (USA)"
    assert new_game.is_dir()
    assert sorted(p.name for p in new_game.iterdir()) == [
        "Pokemon (USA).bin",
        "Pokemon (USA).cu2",
    ]
    assert len(renames) == 3  # two files + the folder


def test_sanitize_keeps_multidisc_pairs_and_updates_lst(tmp_path):
    profile = {"device_type": "PSIO", "path": str(tmp_path), "system": "PS1"}
    game = tmp_path / "Gámé"
    game.mkdir()
    for n in (1, 2):
        stem = f"Gámé (Disc {n})"
        (game / f"{stem}.bin").write_bytes(b"BIN")
        (game / f"{stem}.cu2").write_bytes(b"CU2")
    (game / "MULTIDISC.LST").write_bytes(
        "Gámé (Disc 1).bin\r\nGámé (Disc 2).bin\r\n".encode("utf-8")
    )

    sanitize_installed_files(profile, "PS1")

    new_game = tmp_path / "Game"
    names = sorted(p.name for p in new_game.iterdir())
    assert "Game (Disc 1).bin" in names
    assert "Game (Disc 1).cu2" in names
    assert "Game (Disc 2).bin" in names
    assert "Game (Disc 2).cu2" in names
    lst_lines = sorted(
        line.strip()
        for line in (new_game / "MULTIDISC.LST")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    assert lst_lines == ["Game (Disc 1).bin", "Game (Disc 2).bin"]


def test_sanitize_collision_keeps_pairs_aligned(tmp_path):
    profile = {"device_type": "PSIO", "path": str(tmp_path), "system": "PS1"}
    game = tmp_path / "Collide"
    game.mkdir()
    # Two source stems that both reduce to ASCII "Game".
    (game / "Gámé.bin").write_bytes(b"1")
    (game / "Gámé.cu2").write_bytes(b"1")
    (game / "Game.bin").write_bytes(b"2")  # already ASCII -> not renamed
    (game / "Game.cu2").write_bytes(b"2")

    sanitize_installed_files(profile, "PS1")

    names = sorted(p.name for p in game.iterdir())
    assert "Game.bin" in names
    assert "Game.cu2" in names
    assert "Game~2.bin" in names  # accented pair displaced together
    assert "Game~2.cu2" in names


def test_sanitize_truncates_long_names_under_sixty(tmp_path):
    profile = {"device_type": "PSIO", "path": str(tmp_path), "system": "PS1"}
    long_stem = "A" * 80
    game = tmp_path / long_stem
    game.mkdir()
    (game / f"{long_stem}.bin").write_bytes(b"BIN")
    (game / f"{long_stem}.cu2").write_bytes(b"CU2")

    sanitize_installed_files(profile, "PS1")

    for folder in tmp_path.iterdir():
        assert len(folder.name) <= PSIO_MAX_NAME
        for f in folder.iterdir():
            assert len(f.name) <= PSIO_MAX_NAME


def test_sanitize_missing_folder_is_noop(tmp_path):
    profile = {"device_type": "PSIO", "path": str(tmp_path / "missing"), "system": "PS1"}
    assert sanitize_installed_files(profile, "PS1") == []


# ── OPL (Open PS2 Loader) install layout ────────────────────────────────────


def test_opl_disc_id_formats_sony_serial():
    assert opl_disc_id("SLPS20436") == "SLPS_204.36"
    assert opl_disc_id("SLPS-20436") == "SLPS_204.36"
    assert opl_disc_id("slps_204.36") == "SLPS_204.36"
    assert opl_disc_id("SCUS94503") == "SCUS_945.03"
    assert opl_disc_id("not a serial") == ""
    assert opl_disc_id(None) == ""


def test_opl_ps2_dvd_goes_to_dvd_folder_with_serial_name(tmp_path):
    profile = {"device_type": "OPL", "path": str(tmp_path), "system": "PS2"}
    rom = {
        "rom_id": "SLPS_204.36",
        "title_id": "SLPS20436",
        "system": "PS2",
        "name": "Sakigake Otokojuku",
        "filename": "Sakigake Otokojuku (Japan).chd",
        "extract_format": "iso",
        "extract_formats": ["iso"],
    }
    plan = build_install_plan(profile, rom, "PS2")

    assert plan.extract_format == "iso"
    assert plan.target_path == tmp_path / "DVD" / "SLPS_204.36.Sakigake Otokojuku.iso"


def test_opl_ps2_cd_uses_cd_folder(tmp_path):
    # The server advertises 'cue' for PS2 CD media — OPL still wants .iso,
    # routed to the CD/ folder.
    profile = {"device_type": "OPL", "path": str(tmp_path), "system": "PS2"}
    rom = {
        "rom_id": "SLUS_203.44",
        "title_id": "SLUS20344",
        "system": "PS2",
        "name": "Mr. Mosquito",
        "filename": "Mr. Mosquito (USA).chd",
        "extract_format": "cue",
        "extract_formats": ["cue"],
    }
    plan = build_install_plan(profile, rom, "PS2")

    assert plan.extract_format == "iso"
    assert plan.target_path == tmp_path / "CD" / "SLUS_203.44.Mr. Mosquito.iso"
    assert opl_ps2_media(rom) == "CD"


def test_opl_ps1_vcd_goes_to_pops_folder_serial_and_name(tmp_path):
    # OPL only lists VCDs named SERIAL.Name.VCD — a serial-only name is ignored.
    profile = {"device_type": "OPL", "path": str(tmp_path), "system": "PS1"}
    rom = {
        "rom_id": "SLUS_012.34",
        "title_id": "SLUS01234",
        "system": "PS1",
        "name": "Castlevania Symphony of the Night",
        "filename": "Castlevania SOTN (USA).chd",
        "extract_format": "cue",
        "extract_formats": ["cue", "vcd"],
    }
    plan = build_install_plan(profile, rom, "PS1")

    assert plan.extract_format == "vcd"
    assert plan.target_path == (
        tmp_path / "POPS" / "SLUS_012.34.Castlevania Symphony of the Night.VCD"
    )
    # PS1 VCDs need an Applications-menu launcher + conf_apps.cfg entry.
    assert plan.opl_popstarter is True


def test_opl_ps2_iso_is_not_popstarter():
    profile = {"device_type": "OPL", "system": "PS2"}
    rom = {
        "rom_id": "SLUS_200.02", "title_id": "SLUS20002", "system": "PS2",
        "name": "Some PS2 Game", "filename": "game.chd",
        "extract_format": "iso", "extract_formats": ["iso"],
    }
    plan = build_install_plan(profile, rom, "PS2")
    assert plan.opl_popstarter is False


def test_install_popstarter_app_writes_appfolder_and_titlecfg(tmp_path):
    pops = tmp_path / "POPS"
    pops.mkdir()
    (pops / "POPSTARTER.ELF").write_bytes(b"ELFDATA")
    vcd = pops / "SLUS_012.34.Castlevania SOTN.vcd"
    vcd.write_bytes(b"vcd")

    written = _install_popstarter_app(vcd, "Castlevania SOTN")

    # APPS/<stem>/ holds the XX.-prefixed launcher + title.cfg; VCD in POPS/.
    app_dir = tmp_path / "APPS" / "SLUS_012.34.Castlevania SOTN"
    launcher = app_dir / "XX.SLUS_012.34.Castlevania SOTN.ELF"
    title_cfg = app_dir / "title.cfg"
    assert launcher.read_bytes() == b"ELFDATA"
    assert launcher in written and title_cfg in written
    assert title_cfg.read_text() == (
        "title=Castlevania SOTN\n"
        "boot=XX.SLUS_012.34.Castlevania SOTN.ELF\n"
    )


def test_install_popstarter_app_idempotent(tmp_path):
    pops = tmp_path / "POPS"
    pops.mkdir()
    (pops / "popstarter.elf").write_bytes(b"ELFDATA")  # case-insensitive source
    vcd = pops / "SLUS_012.34.Game.vcd"
    vcd.write_bytes(b"vcd")

    _install_popstarter_app(vcd, "Game")
    _install_popstarter_app(vcd, "Game")  # reinstall: overwrites, no extra files

    app_dir = tmp_path / "APPS" / "SLUS_012.34.Game"
    files = sorted(p.name for p in app_dir.iterdir())
    assert files == ["XX.SLUS_012.34.Game.ELF", "title.cfg"]


def test_install_popstarter_app_no_elf_skips(tmp_path):
    pops = tmp_path / "POPS"
    pops.mkdir()
    vcd = pops / "SLUS_012.34.Game.vcd"
    vcd.write_bytes(b"vcd")

    # No POPSTARTER.ELF present → nothing written, VCD install still succeeds.
    assert _install_popstarter_app(vcd, "Game") == []
    assert not (tmp_path / "APPS").exists()


def test_repair_removes_orphan_appfolder_keeps_user_apps(tmp_path):
    pops = tmp_path / "POPS"
    pops.mkdir()
    (pops / "POPSTARTER.ELF").write_bytes(b"E")
    apps = tmp_path / "APPS"
    apps.mkdir()
    # Live game: VCD present (its app folder will be backfilled).
    (pops / "SLUS_012.34.Live.vcd").write_bytes(b"v")
    # Orphan app folder: serial-named but VCD gone → should be removed.
    orphan = apps / "SCUS_946.01.Gone"
    orphan.mkdir()
    (orphan / "SCUS_946.01.Gone.ELF").write_bytes(b"E")
    (orphan / "title.cfg").write_bytes(b"title=Gone\nboot=SCUS_946.01.Gone.ELF\n")
    # User's own app folder + loose ELF — must never be touched.
    (apps / "uLaunchELF").mkdir()
    (apps / "uLaunchELF" / "BOOT.ELF").write_bytes(b"u")
    (apps / "ps2sync.elf").write_bytes(b"app")

    fixed = repair_opl_popstarter(
        {"device_type": "OPL", "path": str(tmp_path), "system": "PS1"}, "PS1"
    )

    # Orphan folder gone; live game backfilled; user apps intact.
    assert not orphan.exists()
    assert (apps / "SLUS_012.34.Live" / "title.cfg").exists()
    assert (apps / "uLaunchELF" / "BOOT.ELF").exists()
    assert (apps / "ps2sync.elf").exists()
    assert any("SCUS_946.01.Gone" in old for old, _ in fixed)


def test_opl_profile_always_offers_ps1_ps2():
    # No systems enabled in the profile, but OPL must still offer PS1+PS2.
    profile = {"device_type": "OPL", "systems": []}
    assert set(profile_systems(profile)) == {"PS1", "PS2"}


def test_opl_profile_merges_ps1_ps2_with_enabled_systems():
    profile = {
        "device_type": "OPL",
        "systems": [{"system": "PS2", "enabled": True}],
    }
    systems = profile_systems(profile)
    assert set(systems) == {"PS1", "PS2"}
    # Existing enabled system kept (not duplicated).
    assert systems.count("PS2") == 1


def test_non_opl_profile_respects_enabled_toggles():
    profile = {
        "device_type": "RetroArch",
        "systems": [
            {"system": "PS1", "enabled": False},
            {"system": "GBA", "enabled": True},
        ],
    }
    assert profile_systems(profile) == ["GBA"]


def test_repair_opl_backfills_launcher_and_conf(tmp_path):
    pops = tmp_path / "POPS"
    pops.mkdir()
    (pops / "POPSTARTER.ELF").write_bytes(b"ELF")
    # Two already-installed VCDs with no launcher/conf yet.
    (pops / "SLUS_012.34.Castlevania SOTN.vcd").write_bytes(b"a")
    (pops / "SCUS_946.01.LEMMINGS 3D.vcd").write_bytes(b"b")
    profile = {"device_type": "OPL", "path": str(tmp_path), "system": "PS1"}

    fixed = repair_opl_popstarter(profile, "PS1")

    assert len(fixed) == 2
    cv_dir = tmp_path / "APPS" / "SLUS_012.34.Castlevania SOTN"
    lem_dir = tmp_path / "APPS" / "SCUS_946.01.LEMMINGS 3D"
    assert (cv_dir / "XX.SLUS_012.34.Castlevania SOTN.ELF").read_bytes() == b"ELF"
    assert (cv_dir / "title.cfg").read_text() == (
        "title=Castlevania SOTN\nboot=XX.SLUS_012.34.Castlevania SOTN.ELF\n"
    )
    assert (lem_dir / "title.cfg").read_text() == (
        "title=LEMMINGS 3D\nboot=XX.SCUS_946.01.LEMMINGS 3D.ELF\n"
    )
    # Idempotent: a second run finds nothing to do.
    assert repair_opl_popstarter(profile, "PS1") == []


def test_repair_opl_missing_popstarter_raises(tmp_path):
    pops = tmp_path / "POPS"
    pops.mkdir()
    (pops / "SLUS_012.34.Game.vcd").write_bytes(b"a")
    profile = {"device_type": "OPL", "path": str(tmp_path), "system": "PS1"}
    with pytest.raises(ValueError, match="POPSTARTER.ELF"):
        repair_opl_popstarter(profile, "PS1")


def test_repair_dispatch_by_device(tmp_path):
    # OPL with no POPS folder → no-op (empty), not an error.
    opl = {"device_type": "OPL", "path": str(tmp_path), "system": "PS1"}
    assert repair_installed_files(opl, "PS1") == []
    # Unknown device → no-op.
    other = {"device_type": "RetroArch", "path": str(tmp_path)}
    assert repair_installed_files(other, "PS1") == []


def test_opl_default_rom_format(tmp_path):
    ps2_rom = {"filename": "Game.chd", "extract_format": "iso", "extract_formats": ["iso"]}
    ps1_rom = {"filename": "Game.chd", "extract_format": "cue", "extract_formats": ["cue", "vcd"]}
    profile = {"device_type": "OPL", "path": str(tmp_path)}

    assert default_rom_format(profile, ps2_rom, "PS2") == "iso"
    assert default_rom_format(profile, ps1_rom, "PS1") == "vcd"


def test_opl_strips_disc_tag_and_illegal_chars_in_name(tmp_path):
    profile = {"device_type": "OPL", "path": str(tmp_path), "system": "PS2"}
    rom = {
        "rom_id": "SCES_000.01",
        "title_id": "SCES00001",
        "system": "PS2",
        "name": 'Cool: Game (Disc 1) [Hack]',
        "filename": "Cool Game.chd",
        "extract_format": "iso",
        "extract_formats": ["iso"],
    }
    plan = build_install_plan(profile, rom, "PS2")

    # Disc tag dropped, ':' stripped (illegal on FAT32), serial prefix kept.
    assert plan.target_path == tmp_path / "DVD" / "SCES_000.01.Cool Game [Hack].iso"


def test_archive_install_stages_in_tmp_not_on_target(tmp_path, monkeypatch):
    """The zip is downloaded and unzipped off the target device.

    Only the extracted files ever touch the target folder — no ``.zip.part``,
    and the staging dir is gone afterwards.
    """
    import rom_installer

    target = tmp_path / "card" / "Example Game"
    plan = rom_installer.InstallPlan(
        rom_id="SLUS00001",
        display_name="Example Game",
        system="PS1",
        source_filename="Example Game.chd",
        target_path=target,
        extract_format="cue",
        extract_archive=True,
        target_is_directory=True,
    )

    staged: list[Path] = []

    def fake_download(_plan, tmp_path_, progress_callback=None):
        staged.append(tmp_path_)
        # Nothing may be written to the target while downloading/extracting.
        assert not target.exists()
        with zipfile.ZipFile(tmp_path_, "w") as zf:
            zf.writestr("Example Game.cue", b"cue")
            zf.writestr("sub/Example Game.bin", b"bin-data")
        if progress_callback:
            progress_callback(8, 8)

    monkeypatch.setattr(rom_installer, "_download_rom", fake_download)

    written = rom_installer.install_rom(plan)

    assert sorted(p.relative_to(target).as_posix() for p in written) == [
        "Example Game.cue",
        "sub/Example Game.bin",
    ]
    assert (target / "sub" / "Example Game.bin").read_bytes() == b"bin-data"
    assert sorted(p.name for p in target.rglob("*")) == [
        "Example Game.bin",
        "Example Game.cue",
        "sub",
    ]
    # Staging happened outside the target tree and was cleaned up.
    assert staged and tmp_path not in staged[0].parents
    assert not staged[0].parent.exists()


def test_install_tmp_dir_config_override(tmp_path, monkeypatch):
    import rom_installer

    scratch = tmp_path / "scratch"
    monkeypatch.setattr(
        rom_installer, "load_config", lambda: {"install_tmp_dir": str(scratch)}
    )
    assert rom_installer._install_tmp_dir() == scratch
    assert scratch.is_dir()

    monkeypatch.setattr(rom_installer, "load_config", lambda: {})
    assert rom_installer._install_tmp_dir() is None


# ── MSU packs ────────────────────────────────────────────────────────────────


def _msu_rom(kind: str, system: str, name: str) -> dict:
    return {
        "rom_id": f"{system}_{name}",
        "title_id": f"{system}_x",
        "system": system,
        "name": name,
        "filename": f"{name}.zip",
        "is_bundle": True,
        "bundle_kind": kind,
        "files": [],
    }


def test_msu_pack_installs_into_its_own_folder_on_a_generic_device(tmp_path):
    profile = {
        "name": "FXPak",
        "device_type": "Everdrive",
        "path": str(tmp_path),
        "systems": [{"system": "SNES", "enabled": True}],
    }
    rom = _msu_rom("msu1", "SNES", "ActRaiser (USA) (MSU1)")
    plan = build_install_plan(profile, rom, "SNES")
    assert plan.extract_archive and plan.target_is_directory
    assert plan.target_path == tmp_path / "ActRaiser (USA) (MSU1)"
    assert plan.bundle_kind == "msu1"
    assert plan.rom_rename is None


def test_msu_md_pack_goes_to_the_megacd_core_on_mister(tmp_path):
    (tmp_path / "MegaCD").mkdir()
    (tmp_path / "Genesis").mkdir()
    profile = _mister_profile(str(tmp_path))

    plan = build_install_plan(profile, _msu_rom("msu-md", "MD", "Sonic 2 (MSU-MD)"), "MD")
    assert plan.target_path == tmp_path / "MegaCD" / "Sonic 2 (MSU-MD)"
    assert plan.rom_rename == "cart.rom"
    assert plan.bundle_kind == "msu-md"

    plan = build_install_plan(profile, _msu_rom("mdplus", "MD", "Sonic 2 (MD+)"), "MD")
    assert plan.target_path == tmp_path / "Genesis" / "Sonic 2 (MD+)"
    assert plan.rom_rename is None

    remote = _mister_profile("", target="usb")
    plan = build_install_plan(remote, _msu_rom("msu-md", "MD", "Sonic 2 (MSU-MD)"), "MD")
    assert str(plan.target_path) == "/media/usb0/games/MegaCD/Sonic 2 (MSU-MD)"
    assert plan.mister_remote == "usb"


def test_safe_extract_zip_hoists_pack_folder_and_renames_cart(tmp_path):
    zip_path = tmp_path / "pack.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("Sonic 2 (MSU-MD)/", b"")
        zf.writestr("Sonic 2 (MSU-MD)/Sonic 2 (MSU-MD).md", b"ROM")
        zf.writestr("Sonic 2 (MSU-MD)/Sonic 2 (MSU-MD).cue",
                    'FILE "Sonic 2 (MSU-MD).bin" BINARY\n  TRACK 01 AUDIO\n')
        zf.writestr("Sonic 2 (MSU-MD)/Sonic 2 (MSU-MD).bin", b"AUDIO")
        zf.writestr("Sonic 2 (MSU-MD)/Sonic 2 (MSU-MD).srm", b"junk")

    target = tmp_path / "out"
    written = _safe_extract_zip(zip_path, target, "msu-md", "cart.rom")
    assert sorted(p.name for p in written) == [
        "Sonic 2 (MSU-MD).bin", "Sonic 2 (MSU-MD).cue", "cart.rom",
    ]
    assert (target / "cart.rom").read_bytes() == b"ROM"
    assert not (target / "Sonic 2 (MSU-MD)").exists()

    # Plain bundles keep the old byte-for-byte behaviour.
    plain = tmp_path / "plain"
    written = _safe_extract_zip(zip_path, plain)
    assert (plain / "Sonic 2 (MSU-MD)" / "Sonic 2 (MSU-MD).srm").exists()
