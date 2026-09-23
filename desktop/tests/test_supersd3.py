"""Super SD System 3 (TerraOnion PC Engine ODE) profile support.

Card layout under the SD root::

    HuCard/<Game>.pce          cartridge dumps, flat
    Cd/<Game>/<Image>.cue      one folder per CD game
    bup/<Image stem>.bup       per-game 2 KB backup RAM
    bup/backram.bup            the console's shared BRAM (never synced)
"""

from pathlib import Path

from rom_installer import build_install_plan, resolve_profile_rom_folder
from sync_engine import scan_profile


BRAM = b"HUBM" + bytes(2044)


def _card(root: Path) -> Path:
    (root / "HuCard").mkdir()
    (root / "Cd").mkdir()
    (root / "bup").mkdir()
    return root


def _profile(root: Path) -> dict:
    return {
        "name": "Super SD System 3",
        "device_type": "Super SD System 3",
        "path": str(root),
        "save_folder": "",
        "systems": [
            {"system": "PCECD", "enabled": True, "save_ext": ".bup"},
            {"system": "PCE", "enabled": True, "save_ext": ".bup"},
            {"system": "PCSG", "enabled": True, "save_ext": ".bup"},
        ],
    }


def _scan(root: Path):
    saves = scan_profile(_profile(root), enable_auto_normalize=False)
    return {s.game_name: s for s in saves}


def test_hucard_rom_matches_bup_save(tmp_path):
    root = _card(tmp_path)
    (root / "HuCard" / "Bonk's Revenge (USA).pce").write_bytes(b"rom")
    (root / "bup" / "Bonk's Revenge (USA).bup").write_bytes(BRAM)

    save = _scan(root)["Bonk's Revenge (USA)"]
    assert save.system == "PCE"
    assert save.save_exists
    assert save.path == root / "bup" / "Bonk's Revenge (USA).bup"


def test_hucard_rom_without_save_gets_expected_path(tmp_path):
    root = _card(tmp_path)
    (root / "HuCard" / "Alien Crush (USA).pce").write_bytes(b"rom")

    save = _scan(root)["Alien Crush (USA)"]
    assert not save.save_exists
    assert save.path == root / "bup" / "Alien Crush (USA).bup"


def test_supergrafx_rom_scans_as_pcsg(tmp_path):
    root = _card(tmp_path)
    (root / "HuCard" / "Aldynes (Japan).sgx").write_bytes(b"rom")

    assert _scan(root)["Aldynes (Japan)"].system == "PCSG"


def test_cd_save_is_named_after_the_cue_not_the_folder(tmp_path):
    """The ODE names a CD save after the disc image, and the folder is often
    an abbreviation the user typed (``Cd/SR/Super_Raiden_….cue``)."""
    root = _card(tmp_path)
    game = root / "Cd" / "SR"
    game.mkdir()
    (game / "Super_Raiden_(NTSC-J)_[HCD2023].cue").write_bytes(b"cue")
    (game / "Super_Raiden_(NTSC-J)_[HCD2023].bin").write_bytes(b"bin")
    (root / "bup" / "Super_Raiden_(NTSC-J)_[HCD2023].bup").write_bytes(BRAM)

    save = _scan(root)["Super_Raiden_(NTSC-J)_[HCD2023]"]
    assert save.system == "PCECD"
    assert save.save_exists
    assert save.path == root / "bup" / "Super_Raiden_(NTSC-J)_[HCD2023].bup"


def test_multitrack_cd_uses_the_cue_stem_not_a_track_bin(tmp_path):
    root = _card(tmp_path)
    game = root / "Cd" / "Dragon Ball Z"
    game.mkdir()
    (game / "Dragon Ball Z (Japan).cue").write_bytes(b"cue")
    for track in range(1, 4):
        (game / f"Dragon Ball Z (Japan) (Track {track:02d}).bin").write_bytes(b"bin")
    (root / "bup" / "Dragon Ball Z (Japan).bup").write_bytes(BRAM)

    save = _scan(root)["Dragon Ball Z (Japan)"]
    assert save.save_exists
    assert save.path.name == "Dragon Ball Z (Japan).bup"


def test_shared_backram_is_never_synced(tmp_path):
    root = _card(tmp_path)
    (root / "bup" / "backram.bup").write_bytes(BRAM)

    assert _scan(root) == {}


def test_bup_bak_backups_are_ignored(tmp_path):
    root = _card(tmp_path)
    (root / "HuCard" / "Game (USA).pce").write_bytes(b"rom")
    (root / "bup" / "Game (USA).bup.bak").write_bytes(BRAM)

    assert not _scan(root)["Game (USA)"].save_exists


def test_save_without_a_rom_still_syncs(tmp_path):
    """Deleting a ROM from the card must not strand its backup RAM."""
    root = _card(tmp_path)
    (root / "bup" / "Removed Game (Japan).bup").write_bytes(BRAM)

    save = _scan(root)["Removed Game (Japan)"]
    assert save.system == "PCECD"
    assert save.save_exists


def test_disabled_system_is_filtered_out(tmp_path):
    root = _card(tmp_path)
    (root / "HuCard" / "Cart (USA).pce").write_bytes(b"rom")
    game = root / "Cd" / "Disc"
    game.mkdir()
    (game / "Disc (USA).cue").write_bytes(b"cue")

    profile = _profile(root)
    profile["systems"] = [{"system": "PCECD", "enabled": True, "save_ext": ".bup"}]
    names = {s.game_name for s in scan_profile(profile, enable_auto_normalize=False)}
    assert names == {"Disc (USA)"}


def test_rom_install_folders(tmp_path):
    root = _card(tmp_path)
    profile = _profile(root)
    assert resolve_profile_rom_folder(profile, "PCE") == root / "HuCard"
    assert resolve_profile_rom_folder(profile, "PCSG") == root / "HuCard"
    assert resolve_profile_rom_folder(profile, "PCECD") == root / "Cd"


def test_hucard_installs_as_a_loose_file(tmp_path):
    profile = _profile(_card(tmp_path))
    plan = build_install_plan(
        profile,
        {
            "rom_id": "r1",
            "system": "PCE",
            "name": "Bonk's Revenge (USA)",
            "filename": "Bonk's Revenge (USA).pce",
        },
        "PCE",
    )
    assert plan.extract_format is None
    assert not plan.target_is_directory
    assert plan.target_path == tmp_path / "HuCard" / "Bonk's Revenge (USA).pce"


def test_cd_chd_installs_as_a_cue_set_in_its_own_folder(tmp_path):
    profile = _profile(_card(tmp_path))
    plan = build_install_plan(
        profile,
        {
            "rom_id": "r2",
            "system": "PCECD",
            "name": "Ys Book I & II (USA)",
            "filename": "Ys Book I & II (USA).chd",
        },
        "PCECD",
    )
    assert plan.extract_format == "cue"
    assert plan.target_is_directory
    assert plan.target_path == tmp_path / "Cd" / "Ys Book I & II (USA)"


def test_cd_discs_share_one_game_folder(tmp_path):
    """Both discs land in one Cd/<Game>/ folder, disc tag stripped."""
    profile = _profile(_card(tmp_path))
    targets = {
        build_install_plan(
            profile,
            {
                "rom_id": f"d{n}",
                "system": "PCECD",
                "name": f"Sample Game (Japan) (Disc {n})",
                "filename": f"Sample Game (Japan) (Disc {n}).chd",
            },
            "PCECD",
        ).target_path
        for n in (1, 2)
    }
    assert targets == {tmp_path / "Cd" / "Sample Game (Japan)"}
