from pathlib import Path
import zipfile

import sync_engine as se


def _stub_nointro_cache(monkeypatch, entries: dict[str, str]) -> None:
    """Provide a deterministic No-Intro cache for sync tests.

    The desktop regression tests should not depend on the large local DAT set
    being present in the workspace or CI runner. Stubbing the derived cache
    keeps the sync tests focused on resolution behavior rather than DAT I/O.
    """
    import rom_normalizer as rn

    monkeypatch.setattr(
        se,
        "_get_nointro_cache",
        lambda system: {
            "no_intro": entries,
            "name_index": rn.build_name_index(entries),
            "cache_tag": f"{system}:stub",
        },
    )


def test_resolve_canonical_sync_name_uses_nointro_fuzzy_match(monkeypatch, tmp_path):
    rom = tmp_path / "Advance Wars.gba"
    rom.write_bytes(b"")

    _stub_nointro_cache(
        monkeypatch,
        {"A": "Advance Wars (USA)"},
    )

    canonical, source, confidence = se._resolve_canonical_sync_name("GBA", rom)

    assert canonical == "Advance Wars (USA)"
    assert source == "fuzzy"
    assert confidence == "low"


def test_scan_roms_match_saves_uses_canonical_title_id_without_renaming_files(
    monkeypatch, tmp_path
):
    rom_folder = tmp_path / "roms"
    save_folder = tmp_path / "saves"
    rom_folder.mkdir()
    save_folder.mkdir()

    rom = rom_folder / "Advance Wars.gba"
    save = save_folder / "Advance Wars.sav"
    rom.write_bytes(b"")
    save.write_bytes(b"local save")

    _stub_nointro_cache(
        monkeypatch,
        {"A": "Advance Wars (USA)"},
    )

    results = se._scan_roms_match_saves(rom_folder, save_folder, "GBA")

    assert len(results) == 1
    entry = results[0]
    assert entry.title_id == "GBA_advance_wars_usa"
    assert entry.path == save
    assert entry.game_name == "Advance Wars"
    assert entry.save_exists is True


def test_scan_roms_match_saves_can_disable_auto_normalize(tmp_path):
    rom_folder = tmp_path / "roms"
    save_folder = tmp_path / "saves"
    rom_folder.mkdir()
    save_folder.mkdir()

    rom = rom_folder / "Advance Wars.gba"
    save = save_folder / "Advance Wars.sav"
    rom.write_bytes(b"")
    save.write_bytes(b"local save")

    results = se._scan_roms_match_saves(
        rom_folder,
        save_folder,
        "GBA",
        enable_auto_normalize=False,
    )

    assert len(results) == 1
    assert results[0].title_id == "GBA_advance_wars"


def test_scan_roms_match_saves_supports_zip_roms(monkeypatch, tmp_path):
    rom_folder = tmp_path / "roms"
    save_folder = tmp_path / "saves"
    rom_folder.mkdir()
    save_folder.mkdir()

    rom_zip = rom_folder / "Advance Wars.zip"
    with zipfile.ZipFile(rom_zip, "w") as zf:
        zf.writestr("Advance Wars.gba", b"rom")
    save = save_folder / "Advance Wars.sav"
    save.write_bytes(b"local save")

    _stub_nointro_cache(
        monkeypatch,
        {"A": "Advance Wars (USA)"},
    )

    results = se._scan_roms_match_saves(rom_folder, save_folder, "GBA")

    assert len(results) == 1
    entry = results[0]
    assert entry.title_id == "GBA_advance_wars_usa"
    assert entry.path == save
    assert entry.game_name == "Advance Wars"
    assert entry.save_exists is True


def test_resolve_canonical_sync_name_supports_zip_member_filename(monkeypatch, tmp_path):
    rom_zip = tmp_path / "Super Dodgeball Advance.zip"
    with zipfile.ZipFile(rom_zip, "w") as zf:
        zf.writestr("Super Dodgeball Advance.gba", b"rom")

    _stub_nointro_cache(
        monkeypatch,
        {"A": "Super Dodge Ball Advance (USA)"},
    )

    canonical, source, confidence = se._resolve_canonical_sync_name("GBA", rom_zip)

    assert canonical == "Super Dodge Ball Advance (USA)"
    assert source == "fuzzy"
    assert confidence == "low"


def test_resolve_canonical_sync_name_uses_persistent_cache(monkeypatch, tmp_path):
    rom = tmp_path / "Advance Wars.gba"
    rom.write_bytes(b"")

    cache_file = tmp_path / ".scan_cache.json"
    monkeypatch.setattr(se, "SCAN_CACHE_FILE", cache_file)
    monkeypatch.setattr(se, "_SCAN_CACHE", None)
    monkeypatch.setattr(se, "_SCAN_CACHE_DIRTY", False)
    monkeypatch.setattr(se, "_NOINTRO_CACHE", {})

    _stub_nointro_cache(
        monkeypatch,
        {"A": "Advance Wars (USA)"},
    )

    import rom_normalizer as rn

    calls = {"fuzzy": 0}
    original_fuzzy = rn.fuzzy_filename_search

    def counting_fuzzy(filename, name_index):
        calls["fuzzy"] += 1
        return original_fuzzy(filename, name_index)

    monkeypatch.setattr(rn, "fuzzy_filename_search", counting_fuzzy)

    first, source1, confidence1 = se._resolve_canonical_sync_name("GBA", rom)
    se._flush_scan_cache()
    assert first == "Advance Wars (USA)"
    assert source1 == "fuzzy"
    assert confidence1 == "low"
    assert calls["fuzzy"] >= 1

    monkeypatch.setattr(se, "_SCAN_CACHE", None)
    monkeypatch.setattr(se, "_SCAN_CACHE_DIRTY", False)
    monkeypatch.setattr(rn, "fuzzy_filename_search", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("cache miss")))

    second, source2, confidence2 = se._resolve_canonical_sync_name("GBA", rom)

    assert second == "Advance Wars (USA)"
    assert source2 == "fuzzy"
    assert confidence2 == "low"


def test_make_title_id_with_region_includes_multi_region_suffix():
    title_id = se._make_title_id_with_region(
        "GBA",
        "Yu Yu Hakusho - Ghostfiles - Tournament Tactics (USA, Europe).sav",
    )

    assert title_id == "GBA_yu_yu_hakusho_ghostfiles_tournament_tactics_usa_europe"


def test_fuzzy_matching_handles_trailing_the_titles():
    import rom_normalizer as rn

    no_intro = {
        "X": "Revenge of Shinobi, The (USA)",
    }
    name_index = rn.build_name_index(no_intro)

    canonical = rn.fuzzy_filename_search("The Revenge of Shinobi.gba", name_index)

    assert canonical == "Revenge of Shinobi, The (USA)"


def test_fuzzy_matching_handles_article_and_digit_spacing_titles():
    import rom_normalizer as rn

    no_intro = {
        "X": "King of Fighters EX 2, The - Howling Blood (USA)",
    }
    name_index = rn.build_name_index(no_intro)

    canonical = rn.fuzzy_filename_search("The King of Fighters EX2.gba", name_index)

    assert canonical == "King of Fighters EX 2, The - Howling Blood (USA)"


def test_fuzzy_matching_handles_collapsed_spacing_titles():
    import rom_normalizer as rn

    no_intro = {
        "X": "Super Dodge Ball Advance (USA)",
    }
    name_index = rn.build_name_index(no_intro)

    canonical = rn.fuzzy_filename_search("Super Dodgeball Advance.gba", name_index)

    assert canonical == "Super Dodge Ball Advance (USA)"


def test_sync_resolution_prefers_filename_fuzzy_before_header(monkeypatch, tmp_path):
    rom = tmp_path / "Zone of The Enders.gba"
    rom.write_bytes(b"")

    import rom_normalizer as rn

    monkeypatch.setattr(se, "_NOINTRO_CACHE", {})
    monkeypatch.setattr(rn, "find_dat_for_system", lambda system: tmp_path / "fake.dat")
    monkeypatch.setattr(
        rn,
        "load_no_intro_dat",
        lambda path: {
            "A": "Zone of the Enders - The Fist of Mars (USA)",
            "B": "Z.O.E. 2173 - Testament (Japan)",
        },
    )
    monkeypatch.setattr(
        rn,
        "build_name_index",
        lambda no_intro: {
            "zone_of_the_enders_the_fist_of_mars": "Zone of the Enders - The Fist of Mars (USA)",
            "z_o_e_2173_testament": "Z.O.E. 2173 - Testament (Japan)",
        },
    )
    monkeypatch.setattr(
        rn,
        "fuzzy_filename_search",
        lambda filename, idx: "Zone of the Enders - The Fist of Mars (USA)",
    )
    monkeypatch.setattr(rn, "read_rom_header_title", lambda path, system: "Z.O.E. 2173 TESTAMENT")
    monkeypatch.setattr(
        rn,
        "lookup_header_in_index",
        lambda header, idx: "Z.O.E. 2173 - Testament (Japan)",
    )

    canonical, source, confidence = se._resolve_canonical_sync_name("GBA", rom)

    assert canonical == "Zone of the Enders - The Fist of Mars (USA)"
    assert source == "fuzzy"
    assert confidence == "low"


def test_find_dat_for_system_distinguishes_gb_from_gba(monkeypatch, tmp_path):
    import rom_normalizer as rn

    dats = tmp_path / "dats"
    dats.mkdir()
    gb = dats / "Nintendo - Game Boy.dat"
    gbc = dats / "Nintendo - Game Boy Color.dat"
    gba = dats / "Nintendo - Game Boy Advance.dat"
    for path in (gb, gbc, gba):
        path.write_text("", encoding="utf-8")

    monkeypatch.setattr(rn, "DATS_DIR", dats)

    assert rn.find_dat_for_system("GB") == gb
    assert rn.find_dat_for_system("GBC") == gbc
    assert rn.find_dat_for_system("GBA") == gba


def test_find_dat_for_system_distinguishes_pce_from_pcsg(monkeypatch, tmp_path):
    import rom_normalizer as rn

    dats = tmp_path / "dats"
    dats.mkdir()
    pce = dats / "NEC - PC Engine - TurboGrafx 16.dat"
    pcsg = dats / "NEC - PC Engine SuperGrafx.dat"
    pcecd = dats / "NEC - PC Engine CD - TurboGrafx-CD.dat"
    for path in (pce, pcsg, pcecd):
        path.write_text("", encoding="utf-8")

    monkeypatch.setattr(rn, "DATS_DIR", dats)

    assert rn.find_dat_for_system("PCE") == pce
    assert rn.find_dat_for_system("PCSG") == pcsg
    assert rn.find_dat_for_system("PCECD") == pcecd


def test_find_dat_for_system_distinguishes_wswan_from_wswanc(monkeypatch, tmp_path):
    import rom_normalizer as rn

    dats = tmp_path / "dats"
    dats.mkdir()
    wswan = dats / "Bandai - WonderSwan.dat"
    wswanc = dats / "Bandai - WonderSwan Color.dat"
    for path in (wswan, wswanc):
        path.write_text("", encoding="utf-8")

    monkeypatch.setattr(rn, "DATS_DIR", dats)

    assert rn.find_dat_for_system("WSWAN") == wswan
    assert rn.find_dat_for_system("WSWANC") == wswanc


def test_find_dat_for_system_distinguishes_ngp_from_ngpc(monkeypatch, tmp_path):
    import rom_normalizer as rn

    dats = tmp_path / "dats"
    dats.mkdir()
    ngp = dats / "SNK - Neo Geo Pocket.dat"
    ngpc = dats / "SNK - Neo Geo Pocket Color.dat"
    for path in (ngp, ngpc):
        path.write_text("", encoding="utf-8")

    monkeypatch.setattr(rn, "DATS_DIR", dats)

    assert rn.find_dat_for_system("NGP") == ngp
    assert rn.find_dat_for_system("NGPC") == ngpc


def test_find_dat_for_system_finds_virtual_boy(monkeypatch, tmp_path):
    import rom_normalizer as rn

    dats = tmp_path / "dats"
    dats.mkdir()
    vb = dats / "Nintendo - Virtual Boy.dat"
    vb.write_text("", encoding="utf-8")

    monkeypatch.setattr(rn, "DATS_DIR", dats)

    assert rn.find_dat_for_system("VB") == vb


def test_scan_profile_handles_single_system_analogue_pocket_subroot(tmp_path):
    rom_root = tmp_path / "Assets" / "gba" / "common"
    save_root = tmp_path / "Saves" / "gba" / "common"
    rom_dir = rom_root / "all" / "A-M"
    save_dir = save_root / "all" / "A-M"
    rom_dir.mkdir(parents=True)
    save_dir.mkdir(parents=True)

    rom = rom_dir / "Advance Wars.gba"
    save = save_dir / "Advance Wars.sav"
    rom.write_bytes(b"")
    save.write_bytes(b"save")

    profile = {
        "name": "Pocket_GBA",
        "device_type": "Analogue Pocket",
        "path": str(rom_root),
        "save_folder": str(save_root),
        "systems": [
            {"system": "GBA", "enabled": True, "save_ext": ".sav", "save_folder": ""},
        ],
    }

    results = se.scan_profile(profile, enable_auto_normalize=False)

    assert len(results) == 1
    assert results[0].system == "GBA"
    assert results[0].path == save


def test_scan_profile_preserves_relative_mirrored_save_path_for_pocket_subroot(tmp_path):
    rom_root = tmp_path / "Assets" / "gba" / "common"
    save_root = tmp_path / "Saves" / "gba" / "common"
    rom_dir = rom_root / "all" / "japan"
    rom_dir.mkdir(parents=True)
    save_root.mkdir(parents=True)
    rom = rom_dir / "Guru Logic Champ (Japan).gba"
    rom.write_bytes(b"")

    profile = {
        "name": "Pocket_GBA",
        "device_type": "Analogue Pocket",
        "path": str(rom_root),
        "save_folder": str(save_root),
        "systems": [
            {"system": "GBA", "enabled": True, "save_ext": ".sav", "save_folder": ""},
        ],
    }

    results = se.scan_profile(profile, enable_auto_normalize=False)

    assert len(results) == 1
    assert results[0].path == save_root / "all" / "japan" / "Guru Logic Champ (Japan).sav"
    assert results[0].save_exists is False


def test_scan_profile_keeps_alternate_paths_for_duplicate_rom_locations(tmp_path):
    rom_root = tmp_path / "Assets" / "gba" / "common"
    save_root = tmp_path / "Saves" / "gba" / "common"
    rom_dir_a = rom_root / "all" / "japan"
    rom_dir_b = rom_root / "favorites" / "japan"
    rom_dir_a.mkdir(parents=True)
    rom_dir_b.mkdir(parents=True)
    save_root.mkdir(parents=True)
    (rom_dir_a / "Guru Logic Champ (Japan).gba").write_bytes(b"")
    (rom_dir_b / "Guru Logic Champ (Japan).gba").write_bytes(b"")

    profile = {
        "name": "Pocket_GBA",
        "device_type": "Analogue Pocket",
        "path": str(rom_root),
        "save_folder": str(save_root),
        "systems": [
            {"system": "GBA", "enabled": True, "save_ext": ".sav", "save_folder": ""},
        ],
    }

    results = se.scan_profile(profile, enable_auto_normalize=False)

    assert len(results) == 1
    expected = {
        save_root / "all" / "japan" / "Guru Logic Champ (Japan).sav",
        save_root / "favorites" / "japan" / "Guru Logic Champ (Japan).sav",
    }
    found = {results[0].path, *results[0].alternate_paths}
    assert found == expected


def test_scan_profile_saturn_uses_bkr_for_legacy_generic_profiles(tmp_path):
    rom_root = tmp_path / "roms"
    save_root = tmp_path / "saves"
    rom_root.mkdir()
    save_root.mkdir()

    rom = rom_root / "Panzer Dragoon Saga (USA).chd"
    rom.write_bytes(b"")

    profile = {
        "name": "Saturn Generic",
        "device_type": "Generic",
        "path": str(rom_root),
        "save_folder": str(save_root),
        "system": "SAT",
        "save_ext": ".srm",
    }

    results = se.scan_profile(profile, enable_auto_normalize=False)

    assert len(results) == 1
    assert results[0].path == save_root / "Panzer Dragoon Saga (USA).bkr"
    assert results[0].save_exists is False


def test_scan_profile_retroarch_detects_saturn_bkr_saves(tmp_path):
    save_root = tmp_path / "retroarch"
    rom_root = tmp_path / "roms"
    core_dir = save_root / "Beetle Saturn"
    core_dir.mkdir(parents=True)
    rom_root.mkdir(parents=True)
    (rom_root / "Panzer Dragoon Saga (USA).chd").write_bytes(b"")
    save_file = core_dir / "Panzer Dragoon Saga (USA).bkr"
    save_file.write_bytes(b"saturn-save")

    profile = {
        "name": "RetroArch",
        "device_type": "RetroArch",
        "path": str(rom_root),
        "save_folder": str(save_root),
        "systems": [
            {"system": "SAT", "enabled": True, "save_ext": ".bkr", "save_folder": ""},
        ],
    }

    results = se.scan_profile(profile, enable_auto_normalize=False)

    assert len(results) == 1
    assert results[0].system == "SAT"
    assert results[0].path == save_file
    assert results[0].save_exists is True


def test_scan_profile_retroarch_detects_saturn_yabause_root_saves(tmp_path):
    save_root = tmp_path / "retroarch"
    rom_root = tmp_path / "roms"
    save_root.mkdir(parents=True)
    rom_root.mkdir(parents=True)
    (rom_root / "Panzer Dragoon Saga (USA).chd").write_bytes(b"")
    save_file = save_root / "Panzer Dragoon Saga (USA).srm"
    save_file.write_bytes(b"saturn-save")

    profile = {
        "name": "RetroArch",
        "device_type": "RetroArch",
        "path": str(rom_root),
        "save_folder": str(save_root),
        "systems": [
            {"system": "SAT", "enabled": True, "save_ext": ".srm", "save_folder": ""},
        ],
    }

    results = se.scan_profile(profile, enable_auto_normalize=False)

    assert len(results) == 1
    assert results[0].system == "SAT"
    assert results[0].path == save_file
    assert results[0].save_exists is True


def test_scan_cache_is_scoped_per_profile(tmp_path, monkeypatch):
    rom = tmp_path / "Advance Wars.gba"
    rom.write_bytes(b"")

    cache_file = tmp_path / ".scan_cache.json"
    monkeypatch.setattr(se, "SCAN_CACHE_FILE", cache_file)
    monkeypatch.setattr(se, "_SCAN_CACHE", None)
    monkeypatch.setattr(se, "_SCAN_CACHE_DIRTY", False)

    se._set_cached_canonical_name(
        "profile-a",
        "GBA",
        rom,
        None,
        "tag",
        "Advance Wars (USA)",
        "fuzzy",
        "low",
    )
    se._set_cached_canonical_name(
        "profile-b",
        "GBA",
        rom,
        None,
        "tag",
        "Advance Wars (Europe)",
        "fuzzy",
        "low",
    )

    cached_a = se._get_cached_canonical_name("profile-a", "GBA", rom, None, "tag")
    cached_b = se._get_cached_canonical_name("profile-b", "GBA", rom, None, "tag")

    assert cached_a == ("Advance Wars (USA)", "fuzzy", "low")
    assert cached_b == ("Advance Wars (Europe)", "fuzzy", "low")


def test_profile_runtime_scope_includes_volume_identity(monkeypatch):
    profile = {
        "name": "GBA_Everdrive",
        "device_type": "Everdrive",
        "path": "J:/",
        "save_folder": "J:/saver",
        "system": "GBA",
        "save_ext": ".sav",
    }

    monkeypatch.setattr(se, "_volume_identity", lambda path: f"VOL:{path}")

    scope = se._profile_runtime_scope(profile)

    assert '"rom_volume":"VOL:J:/"' in scope
    assert '"save_volume":"VOL:J:/saver"' in scope
