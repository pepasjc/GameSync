from pathlib import Path
import zipfile

import rom_collection as rc
import rom_normalizer as rn


def test_scan_collection_prefers_usa_over_other_regions(tmp_path):
    roms = tmp_path / "roms"
    roms.mkdir()
    (roms / "Advance Wars (Europe).gba").write_bytes(b"")
    (roms / "Advance Wars (USA).gba").write_bytes(b"")

    no_intro = {
        "A": "Advance Wars (USA)",
        "B": "Advance Wars (Europe)",
    }

    entries, duplicates, unmatched = rc.scan_collection(roms, "GBA", no_intro)

    assert len(entries) == 1
    assert entries[0].canonical_name == "Advance Wars (USA)"
    assert len(duplicates) == 1
    assert not unmatched


def test_scan_collection_can_include_all_variants_when_1g1r_disabled(tmp_path):
    roms = tmp_path / "roms"
    roms.mkdir()
    (roms / "Advance Wars (Europe).gba").write_bytes(b"")
    (roms / "Advance Wars (USA).gba").write_bytes(b"")

    no_intro = {
        "A": "Advance Wars (USA)",
        "B": "Advance Wars (Europe)",
    }

    entries, duplicates, unmatched = rc.scan_collection(
        roms,
        "GBA",
        no_intro,
        one_game_one_rom=False,
    )

    assert [entry.canonical_name for entry in entries] == [
        "Advance Wars (Europe)",
        "Advance Wars (USA)",
    ]
    assert duplicates == []
    assert unmatched == []


def test_scan_collection_can_match_zip_member_by_filename(tmp_path):
    roms = tmp_path / "roms"
    roms.mkdir()
    archive = roms / "pack.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("Super Dodgeball Advance.gba", b"rom")

    no_intro = {
        "A": "Super Dodge Ball Advance (USA)",
    }

    entries, duplicates, unmatched = rc.scan_collection(roms, "GBA", no_intro)

    assert len(entries) == 1
    assert entries[0].source_kind == "zip"
    assert entries[0].canonical_name == "Super Dodge Ball Advance (USA)"
    assert entries[0].archive_member == "Super Dodgeball Advance.gba"
    assert not duplicates
    assert not unmatched


def test_scan_collection_prefers_english_translation_over_plain_japan(tmp_path):
    roms = tmp_path / "roms"
    roms.mkdir()
    (roms / "Hajime no Ippo - The Fighting! (Japan).gba").write_bytes(b"plain-jp")
    (
        roms / "Hajime no Ippo - The Fighting! (Japan) [T-En by Markliujy v1.0].gba"
    ).write_bytes(b"translated")

    no_intro = {
        "A": "Hajime no Ippo - The Fighting! (Japan)",
    }

    entries, duplicates, unmatched = rc.scan_collection(roms, "GBA", no_intro)

    assert len(entries) == 1
    assert entries[0].canonical_name == "Hajime no Ippo - The Fighting! (Japan)"
    assert entries[0].source_path.name.endswith("[T-En by Markliujy v1.0].gba")
    assert entries[0].is_english_translation is True
    assert len(duplicates) == 1
    assert not unmatched


def test_scan_collection_prefers_official_usa_over_translation(tmp_path):
    roms = tmp_path / "roms"
    roms.mkdir()
    (roms / "Advance Wars (USA).gba").write_bytes(b"usa")
    (roms / "Advance Wars (Japan) [T-En by Someone].gba").write_bytes(b"translated")

    no_intro = {
        "A": "Advance Wars (USA)",
        "B": "Advance Wars (Japan)",
    }

    entries, duplicates, unmatched = rc.scan_collection(roms, "GBA", no_intro)

    assert len(entries) == 1
    assert entries[0].canonical_name == "Advance Wars (USA)"
    assert entries[0].source_path.name == "Advance Wars (USA).gba"
    assert len(duplicates) == 1
    assert not unmatched


def test_scan_collection_prefers_translation_over_non_usa_official_dump(tmp_path):
    roms = tmp_path / "roms"
    roms.mkdir()
    (roms / "Aigle de Guerre, L' (France).gba").write_bytes(b"fr")
    (roms / "Aigle de Guerre, L' (France) [T-En by Mkol103 v1.0].gba").write_bytes(
        b"translated"
    )

    no_intro = {
        "A": "Aigle de Guerre, L' (France)",
    }

    entries, duplicates, unmatched = rc.scan_collection(roms, "GBA", no_intro)

    assert len(entries) == 1
    assert entries[0].canonical_name == "Aigle de Guerre, L' (France)"
    assert entries[0].source_path.name.endswith("[T-En by Mkol103 v1.0].gba")
    assert entries[0].is_english_translation is True
    assert len(duplicates) == 1
    assert not unmatched


def test_validate_collection_flags_wrong_region_and_missing_for_1g1r(tmp_path):
    roms = tmp_path / "roms"
    roms.mkdir()
    (roms / "Advance Wars (Europe).gba").write_bytes(b"eu")

    no_intro = {
        "A": "Advance Wars (USA)",
        "B": "Advance Wars (Europe)",
        "C": "Metroid Fusion (USA)",
    }

    report = rc.validate_collection(
        roms,
        "GBA",
        no_intro,
        one_game_one_rom=True,
        enabled_regions={"USA", "Europe", "Japan", "Other"},
    )

    assert report.expected_total == 2
    # Having the Europe copy counts as "covering" the Advance Wars slot —
    # wrong_region records it but it should NOT appear in missing too.
    assert report.present == []
    assert len(report.wrong_region) == 1
    assert report.wrong_region[0].entry.canonical_name == "Advance Wars (Europe)"
    assert report.wrong_region[0].expected_name == "Advance Wars (USA)"
    # Advance Wars is covered (wrong region) so only the truly absent game is missing.
    assert report.missing == ["Metroid Fusion (USA)"]


def test_validate_collection_version_tag_counts_as_present(tmp_path):
    """A ROM whose CRC matches a versioned DAT entry (e.g. (v1.03)) should count
    as present for the 1G1R slot even when the preferred entry has no version tag."""
    import zlib

    roms = tmp_path / "roms"
    roms.mkdir()
    data = b"v103_rom_data"
    crc = f"{zlib.crc32(data) & 0xFFFFFFFF:08X}"
    (roms / "Advance Wars (USA) (v1.03).gba").write_bytes(data)

    no_intro = {
        "AABBCCDD": "Advance Wars (USA)",       # preferred (no version)
        crc: "Advance Wars (USA) (v1.03)",       # what the user actually has
        "EEFF0011": "Metroid Fusion (USA)",
    }

    report = rc.validate_collection(
        roms,
        "GBA",
        no_intro,
        one_game_one_rom=True,
        enabled_regions={"USA", "Europe", "Japan", "Other"},
    )

    # 1G1R expected: "Advance Wars (USA)" (preferred) + Metroid Fusion = 2 total
    assert report.expected_total == 2
    # The (v1.03) copy covers the Advance Wars slot → present, not wrong_region
    assert len(report.present) == 1
    assert report.present[0].canonical_name == "Advance Wars (USA) (v1.03)"
    assert report.wrong_region == []
    assert report.missing == ["Metroid Fusion (USA)"]


def test_validate_collection_complete_mode_accepts_all_expected_regions(tmp_path):
    roms = tmp_path / "roms"
    roms.mkdir()
    (roms / "Advance Wars (Europe).gba").write_bytes(b"eu")

    no_intro = {
        "A": "Advance Wars (USA)",
        "B": "Advance Wars (Europe)",
        "C": "Metroid Fusion (USA)",
    }

    report = rc.validate_collection(
        roms,
        "GBA",
        no_intro,
        one_game_one_rom=False,
        enabled_regions={"USA", "Europe", "Japan", "Other"},
    )

    assert report.expected_total == 3
    assert [entry.canonical_name for entry in report.present] == [
        "Advance Wars (Europe)"
    ]
    assert report.wrong_region == []
    assert report.missing == ["Advance Wars (USA)", "Metroid Fusion (USA)"]


def test_format_validation_report_includes_summary_and_expected_region(tmp_path):
    roms = tmp_path / "roms"
    roms.mkdir()
    (roms / "Advance Wars (Europe).gba").write_bytes(b"eu")

    no_intro = {
        "A": "Advance Wars (USA)",
        "B": "Advance Wars (Europe)",
    }

    report = rc.validate_collection(
        roms,
        "GBA",
        no_intro,
        one_game_one_rom=True,
        enabled_regions={"USA", "Europe", "Japan", "Other"},
    )
    text = rc.format_validation_report(
        report,
        roms,
        "GBA",
        one_game_one_rom=True,
        enabled_regions={"USA", "Europe", "Japan", "Other"},
    )

    assert "ROM Collection Validation Report" in text
    assert "Mode: 1G1R" in text
    assert "Incorrect region / not in target set: 1" in text
    assert (
        "Advance Wars (Europe) <- Advance Wars (Europe).gba; expected: Advance Wars (USA)"
        in text
    )


def test_scan_collection_prefers_retail_over_beta(tmp_path):
    """Retail release should always beat a Beta, even if Beta sorts first alphabetically."""
    roms = tmp_path / "roms"
    roms.mkdir()
    (roms / "Contra Advance (USA) (Beta).gba").write_bytes(b"beta")
    (roms / "Contra Advance - The Alien Wars EX (USA).gba").write_bytes(b"retail")

    no_intro = {
        "A": "Contra Advance (USA) (Beta)",
        "B": "Contra Advance - The Alien Wars EX (USA)",
    }
    # Both share the same clone group via clone_map
    clone_map = {
        "Contra Advance (USA) (Beta)": "Contra Advance - The Alien Wars EX (USA)",
    }

    entries, duplicates, unmatched = rc.scan_collection(
        roms, "GBA", no_intro, clone_map=clone_map
    )

    assert len(entries) == 1
    assert entries[0].canonical_name == "Contra Advance - The Alien Wars EX (USA)"
    assert len(duplicates) == 1
    assert duplicates[0].canonical_name == "Contra Advance (USA) (Beta)"


def test_status_rank_zero_for_retail():
    c = rc.CollectionCandidate(
        Path("a.gba"), "Advance Wars (USA)", "file", ".gba", "crc"
    )
    assert c.status_rank == 0


def test_status_rank_one_for_beta():
    c = rc.CollectionCandidate(
        Path("a.gba"), "Contra Advance (USA) (Beta)", "file", ".gba", "crc"
    )
    assert c.status_rank == 1


def test_status_rank_one_for_proto():
    c = rc.CollectionCandidate(
        Path("a.gba"), "Some Game (USA) (Proto)", "file", ".gba", "crc"
    )
    assert c.status_rank == 1


def test_status_rank_one_for_demo():
    c = rc.CollectionCandidate(
        Path("a.gba"), "Some Game (USA) (Demo)", "file", ".gba", "crc"
    )
    assert c.status_rank == 1


def test_status_rank_one_for_virtual_console():
    c = rc.CollectionCandidate(
        Path("a.gba"), "Alien Soldier (USA) (Virtual Console)", "file", ".gba", "crc"
    )
    assert c.status_rank == 1


def test_fastrom_rank_zero_when_tagged():
    c = rc.CollectionCandidate(
        Path("Super Metroid (USA) [FastROM].sfc"),
        "Super Metroid (USA)",
        "file",
        ".sfc",
        "crc",
    )
    assert c.fastrom_rank == 0


def test_fastrom_rank_one_when_not_tagged():
    c = rc.CollectionCandidate(
        Path("Super Metroid (USA).sfc"),
        "Super Metroid (USA)",
        "file",
        ".sfc",
        "crc",
    )
    assert c.fastrom_rank == 1


def test_fastrom_rank_case_insensitive():
    c = rc.CollectionCandidate(
        Path("Game [fastrom].sfc"),
        "Game (USA)",
        "file",
        ".sfc",
        "crc",
    )
    assert c.fastrom_rank == 0


def test_fastrom_rank_matches_hack_variant():
    c = rc.CollectionCandidate(
        Path(
            "Actraiser (Japan) [T-En by Aeon Genesis v1.00] [FastROM hack by kandowontu v1.1].sfc"
        ),
        "ActRaiser (Japan)",
        "file",
        ".sfc",
        "crc",
    )
    assert c.fastrom_rank == 0


def test_scan_collection_prefers_fastrom_over_regular(tmp_path):
    """A [FastROM] tagged source file should win over a non-tagged one,
    even if the non-tagged one has a better region."""
    roms = tmp_path / "roms"
    roms.mkdir()
    (roms / "Super Metroid (USA).sfc").write_bytes(b"slow")
    (roms / "Super Metroid (Japan) [FastROM].sfc").write_bytes(b"fast")

    no_intro = {
        "A": "Super Metroid (USA)",
        "B": "Super Metroid (Japan)",
    }
    clone_map = {
        "Super Metroid (Japan)": "Super Metroid (USA)",
    }

    entries, duplicates, unmatched = rc.scan_collection(
        roms, "SNES", no_intro, clone_map=clone_map
    )

    assert len(entries) == 1
    # FastROM Japan file should beat SlowROM USA file
    assert entries[0].canonical_name == "Super Metroid (Japan)"
    assert "[FastROM" in entries[0].source_path.name


def test_scan_collection_excludes_bios_zip(tmp_path):
    roms = tmp_path / "roms"
    roms.mkdir()
    archive = roms / "[BIOS] ST010 (Japan, USA).zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("[BIOS] ST010 (Japan, USA).bin", b"bios")

    no_intro = {
        "A": "ST010 (Japan, USA) (Enhancement Chip)",
    }

    entries, duplicates, unmatched = rc.scan_collection(roms, "SNES", no_intro)

    assert entries == []
    assert duplicates == []
    assert unmatched == []


def test_scan_collection_excludes_enhancement_chip_raw_file(tmp_path):
    roms = tmp_path / "roms"
    roms.mkdir()
    (roms / "ST010 (Japan, USA) (Enhancement Chip).bin").write_bytes(b"bios")

    no_intro = {
        "A": "ST010 (Japan, USA) (Enhancement Chip)",
    }

    entries, duplicates, unmatched = rc.scan_collection(roms, "SNES", no_intro)

    assert entries == []
    assert duplicates == []
    assert unmatched == []


def test_scan_collection_accepts_chd_files(tmp_path):
    roms = tmp_path / "roms"
    roms.mkdir()
    (roms / "Final Fantasy VII (USA).chd").write_bytes(b"fake-chd")

    no_intro = {
        "A": "Final Fantasy VII (USA)",
    }

    entries, duplicates, unmatched = rc.scan_collection(
        roms, "PS1", no_intro, skip_crc=True
    )

    assert len(entries) == 1
    assert entries[0].canonical_name == "Final Fantasy VII (USA)"
    assert entries[0].extension == ".chd"
    assert entries[0].source_path.name == "Final Fantasy VII (USA).chd"
    assert duplicates == []
    assert unmatched == []


def test_scan_collection_accepts_ndd_files(tmp_path):
    roms = tmp_path / "roms"
    roms.mkdir()
    (roms / "F-Zero X - Expansion Kit (Japan).ndd").write_bytes(b"fake-ndd")

    no_intro = {
        "A": "F-Zero X - Expansion Kit (Japan)",
    }

    entries, duplicates, unmatched = rc.scan_collection(
        roms, "N64DD", no_intro, skip_crc=True
    )

    assert len(entries) == 1
    assert entries[0].canonical_name == "F-Zero X - Expansion Kit (Japan)"
    assert entries[0].extension == ".ndd"
    assert entries[0].source_path.name == "F-Zero X - Expansion Kit (Japan).ndd"
    assert duplicates == []
    assert unmatched == []


def test_scan_collection_accepts_32x_files(tmp_path):
    roms = tmp_path / "roms"
    roms.mkdir()
    (roms / "After Burner Complete (Japan, USA) (En).32x").write_bytes(b"fake-32x")

    no_intro = {
        "A": "After Burner Complete (Japan, USA) (En)",
    }

    entries, duplicates, unmatched = rc.scan_collection(
        roms, "32X", no_intro, skip_crc=True
    )

    assert len(entries) == 1
    assert entries[0].canonical_name == "After Burner Complete (Japan, USA) (En)"
    assert entries[0].extension == ".32x"
    assert entries[0].source_path.name == "After Burner Complete (Japan, USA) (En).32x"
    assert duplicates == []
    assert unmatched == []


def test_scan_collection_accepts_vb_files(tmp_path):
    roms = tmp_path / "roms"
    roms.mkdir()
    (roms / "Mario Clash (Japan, USA) (En).vb").write_bytes(b"fake-vb")

    no_intro = {
        "A": "Mario Clash (Japan, USA) (En)",
    }

    entries, duplicates, unmatched = rc.scan_collection(
        roms, "VB", no_intro, skip_crc=True
    )

    assert len(entries) == 1
    assert entries[0].canonical_name == "Mario Clash (Japan, USA) (En)"
    assert entries[0].extension == ".vb"
    assert entries[0].source_path.name == "Mario Clash (Japan, USA) (En).vb"
    assert duplicates == []
    assert unmatched == []


def test_extract_region_hint_prefers_usa_from_multi_region_tag():
    assert rn.extract_region_hint("Super Metroid (Japan, USA) (En,Ja).sfc") == "USA"
    assert rn.extract_region_hints("Super Metroid (Japan, USA) (En,Ja).sfc") == [
        "Japan",
        "USA",
    ]


def test_find_region_preferred_upgrades_europe_to_multi_region_usa_variant():
    no_intro = {
        "A": "Super Metroid (Europe) (En,Fr,De)",
        "B": "Super Metroid (Japan, USA) (En,Ja)",
    }

    canonical = rn.find_region_preferred(
        "Super Metroid (Europe) (En,Fr,De)",
        no_intro,
        "USA",
    )

    assert canonical == "Super Metroid (Japan, USA) (En,Ja)"


def test_scan_collection_prefers_multi_region_usa_variant_over_europe(tmp_path):
    roms = tmp_path / "roms"
    roms.mkdir()
    (roms / "Super Metroid (Europe).sfc").write_bytes(b"eu")
    (roms / "Super Metroid (USA).sfc").write_bytes(b"us")

    no_intro = {
        "A": "Super Metroid (Europe) (En,Fr,De)",
        "B": "Super Metroid (Japan, USA) (En,Ja)",
    }
    clone_map = {
        "Super Metroid (Europe) (En,Fr,De)": "Super Metroid (Japan, USA) (En,Ja)",
    }

    entries, duplicates, unmatched = rc.scan_collection(
        roms, "SNES", no_intro, clone_map=clone_map
    )

    assert len(entries) == 1
    assert entries[0].canonical_name == "Super Metroid (Japan, USA) (En,Ja)"
    assert entries[0].source_path.name == "Super Metroid (USA).sfc"
    assert len(duplicates) == 1
    assert duplicates[0].canonical_name == "Super Metroid (Europe) (En,Fr,De)"
    assert not unmatched


def test_build_collection_unzips_zip_entries(tmp_path):
    roms = tmp_path / "roms"
    roms.mkdir()
    archive = roms / "pack.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("Advance Wars.gba", b"rom-data")

    entry = rc.CollectionCandidate(
        source_path=archive,
        canonical_name="Advance Wars (USA)",
        source_kind="zip",
        extension=".gba",
        match_source="fuzzy",
        archive_member="Advance Wars.gba",
    )

    output = tmp_path / "output"
    written = rc.build_collection([entry], output, unzip_archives=True)

    assert written == [output / "Advance Wars (USA).gba"]
    assert written[0].read_bytes() == b"rom-data"


def test_build_collection_keeps_zip_when_unzip_disabled(tmp_path):
    roms = tmp_path / "roms"
    roms.mkdir()
    archive = roms / "pack.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("Advance Wars.gba", b"rom-data")

    entry = rc.CollectionCandidate(
        source_path=archive,
        canonical_name="Advance Wars (USA)",
        source_kind="zip",
        extension=".gba",
        match_source="fuzzy",
        archive_member="Advance Wars.gba",
    )

    output = tmp_path / "output"
    written = rc.build_collection([entry], output, unzip_archives=False)

    assert written == [output / "Advance Wars (USA).zip"]
    with zipfile.ZipFile(written[0]) as zf:
        assert zf.read("Advance Wars.gba") == b"rom-data"


def test_build_collection_copies_unmatched_files_to_subfolder(tmp_path):
    matched_source = tmp_path / "Advance Wars (USA).gba"
    unmatched_source = tmp_path / "Unknown Game.zip"
    matched_source.write_bytes(b"matched")
    with zipfile.ZipFile(unmatched_source, "w") as zf:
        zf.writestr("Unknown Game.gba", b"unknown")

    entry = rc.CollectionCandidate(
        source_path=matched_source,
        canonical_name="Advance Wars (USA)",
        source_kind="file",
        extension=".gba",
        match_source="fuzzy",
    )

    output = tmp_path / "output"
    written = rc.build_collection(
        [entry],
        output,
        unzip_archives=False,
        unmatched_files=[unmatched_source],
    )

    assert output / "Advance Wars (USA).gba" in written
    unmatched_copy = output / "unmatched files" / "Unknown Game.zip"
    assert unmatched_copy in written
    with zipfile.ZipFile(unmatched_copy) as zf:
        assert zf.read("Unknown Game.gba") == b"unknown"


def test_build_collection_removes_stale_unmatched_folder_when_not_including_unmatched(
    tmp_path,
):
    matched_source = tmp_path / "Advance Wars (USA).gba"
    matched_source.write_bytes(b"matched")

    entry = rc.CollectionCandidate(
        source_path=matched_source,
        canonical_name="Advance Wars (USA)",
        source_kind="file",
        extension=".gba",
        match_source="fuzzy",
    )

    output = tmp_path / "output"
    stale_unmatched = output / "unmatched files"
    stale_unmatched.mkdir(parents=True)
    (stale_unmatched / "Unknown Game.zip").write_bytes(b"stale")

    written = rc.build_collection(
        [entry],
        output,
        unzip_archives=False,
        unmatched_files=[],
    )

    assert written == [output / "Advance Wars (USA).gba"]
    assert not stale_unmatched.exists()


def test_build_letter_buckets_for_four_folders():
    assert rc.build_letter_buckets(4) == [
        ("A", "G"),
        ("H", "N"),
        ("O", "T"),
        ("U", "Z"),
    ]


def test_build_collection_can_split_into_letter_range_folders(tmp_path):
    alpha = tmp_path / "Alpha.gba"
    omega = tmp_path / "Omega.gba"
    alpha.write_bytes(b"a")
    omega.write_bytes(b"o")

    entries = [
        rc.CollectionCandidate(
            source_path=alpha,
            canonical_name="Alpha Force (USA)",
            source_kind="file",
            extension=".gba",
            match_source="fuzzy",
        ),
        rc.CollectionCandidate(
            source_path=omega,
            canonical_name="Omega Boost (USA)",
            source_kind="file",
            extension=".gba",
            match_source="fuzzy",
        ),
    ]

    output = tmp_path / "output"
    written = rc.build_collection(entries, output, unzip_archives=False, folder_count=4)

    assert output / "A-G" / "Alpha Force (USA).gba" in written
    assert output / "O-T" / "Omega Boost (USA).gba" in written


# --- Region property ---


def test_region_property_usa():
    c = rc.CollectionCandidate(
        source_path=Path("x.gba"),
        canonical_name="Advance Wars (USA)",
        source_kind="file",
        extension=".gba",
        match_source="crc",
    )
    assert c.region == "USA"


def test_region_property_translated():
    c = rc.CollectionCandidate(
        source_path=Path("Hajime no Ippo (Japan) [T-En by Someone].gba"),
        canonical_name="Hajime no Ippo (Japan)",
        source_kind="file",
        extension=".gba",
        match_source="fuzzy",
    )
    assert c.region == "Translated"


def test_region_property_other():
    c = rc.CollectionCandidate(
        source_path=Path("x.gba"),
        canonical_name="Some Game (France)",
        source_kind="file",
        extension=".gba",
        match_source="fuzzy",
    )
    assert c.region == "Other"


# --- filter_by_regions ---


def test_filter_by_regions_keeps_only_enabled():
    entries = [
        rc.CollectionCandidate(Path("a.gba"), "Game A (USA)", "file", ".gba", "crc"),
        rc.CollectionCandidate(Path("b.gba"), "Game B (Europe)", "file", ".gba", "crc"),
        rc.CollectionCandidate(Path("c.gba"), "Game C (Japan)", "file", ".gba", "crc"),
        rc.CollectionCandidate(Path("d.gba"), "Game D (France)", "file", ".gba", "crc"),
    ]
    result = rc.filter_by_regions(entries, {"USA", "Europe"})
    names = [e.canonical_name for e in result]
    assert names == ["Game A (USA)", "Game B (Europe)"]


def test_filter_by_regions_translated_passes_with_other():
    entry = rc.CollectionCandidate(
        source_path=Path("Game (Japan) [T-En by X].gba"),
        canonical_name="Game (Japan)",
        source_kind="file",
        extension=".gba",
        match_source="fuzzy",
    )
    assert entry.region == "Translated"
    # "Translated" should pass when "Other" is enabled
    result = rc.filter_by_regions([entry], {"Other"})
    assert len(result) == 1
    # But not when only "USA" is enabled
    result = rc.filter_by_regions([entry], {"USA"})
    assert len(result) == 0


def test_filter_by_regions_empty_set_returns_nothing():
    entry = rc.CollectionCandidate(Path("a.gba"), "Game (USA)", "file", ".gba", "crc")
    assert rc.filter_by_regions([entry], set()) == []


# --- _dedup_key with clone_map ---


def test_dedup_key_without_clone_map():
    c = rc.CollectionCandidate(
        Path("a.gba"), "Advance Wars (USA)", "file", ".gba", "crc"
    )
    assert rc._dedup_key(c, {}) == c.base_key


def test_dedup_key_with_clone_map_uses_leader():
    c = rc.CollectionCandidate(
        Path("a.gba"), "Advance Wars (Europe)", "file", ".gba", "crc"
    )
    clone_map = {"Advance Wars (Europe)": "Advance Wars (USA)"}
    key = rc._dedup_key(c, clone_map)
    assert key == rn.normalize_name("Advance Wars (USA)")


def test_dedup_key_leader_not_in_map_uses_base_key():
    # The leader itself has no cloneof entry (it *is* the leader).
    c = rc.CollectionCandidate(
        Path("a.gba"), "Advance Wars (USA)", "file", ".gba", "crc"
    )
    clone_map = {"Advance Wars (Europe)": "Advance Wars (USA)"}
    assert rc._dedup_key(c, clone_map) == c.base_key


# --- scan_collection with clone_map (cross-language dedup) ---


def test_scan_collection_dedup_cross_language_via_clone_map(tmp_path):
    """Two games with completely different names should dedup via clone_map."""
    roms = tmp_path / "roms"
    roms.mkdir()
    (roms / "Castlevania - Aria of Sorrow (USA).gba").write_bytes(b"usa")
    (roms / "Castlevania - Akatsuki no Minuet (Japan).gba").write_bytes(b"jp")

    no_intro = {
        "A": "Castlevania - Aria of Sorrow (USA)",
        "B": "Castlevania - Akatsuki no Minuet (Japan)",
    }
    clone_map = {
        "Castlevania - Akatsuki no Minuet (Japan)": "Castlevania - Aria of Sorrow (USA)",
    }

    entries, duplicates, unmatched = rc.scan_collection(
        roms, "GBA", no_intro, clone_map=clone_map
    )

    assert len(entries) == 1
    assert entries[0].canonical_name == "Castlevania - Aria of Sorrow (USA)"
    assert len(duplicates) == 1
    assert duplicates[0].canonical_name == "Castlevania - Akatsuki no Minuet (Japan)"


def test_scan_collection_without_clone_map_keeps_both_cross_language(tmp_path):
    """Without clone_map, cross-language games have different base_keys and both survive."""
    roms = tmp_path / "roms"
    roms.mkdir()
    (roms / "Castlevania - Aria of Sorrow (USA).gba").write_bytes(b"usa")
    (roms / "Castlevania - Akatsuki no Minuet (Japan).gba").write_bytes(b"jp")

    no_intro = {
        "A": "Castlevania - Aria of Sorrow (USA)",
        "B": "Castlevania - Akatsuki no Minuet (Japan)",
    }

    entries, duplicates, unmatched = rc.scan_collection(roms, "GBA", no_intro)

    # Without clone_map, these are treated as separate games
    assert len(entries) == 2
    assert len(duplicates) == 0


# --- load_cloneof_map (clrmamepro format) ---


def test_load_cloneof_map_clrmamepro(tmp_path):
    dat = tmp_path / "test.dat"
    dat.write_text(
        "clrmamepro (\n"
        '  name "Test DAT"\n'
        ")\n"
        "\n"
        "game (\n"
        '  name "Advance Wars (USA)"\n'
        '  rom ( name "Advance Wars (USA).gba" size 12345 crc AABBCCDD )\n'
        ")\n"
        "\n"
        "game (\n"
        '  name "Advance Wars (Europe)"\n'
        '  cloneof "Advance Wars (USA)"\n'
        '  rom ( name "Advance Wars (Europe).gba" size 12345 crc 11223344 )\n'
        ")\n"
        "\n"
        "game (\n"
        '  name "Advance Wars (Japan)"\n'
        '  cloneof "Advance Wars (USA)"\n'
        '  rom ( name "Advance Wars (Japan).gba" size 12345 crc 55667788 )\n'
        ")\n",
        encoding="utf-8",
    )

    clone_map = rn.load_cloneof_map(dat)
    assert clone_map == {
        "Advance Wars (Europe)": "Advance Wars (USA)",
        "Advance Wars (Japan)": "Advance Wars (USA)",
    }
    # Leader should not be in the map
    assert "Advance Wars (USA)" not in clone_map


def test_load_cloneof_map_xml(tmp_path):
    dat = tmp_path / "test.xml"
    dat.write_text(
        '<?xml version="1.0"?>\n'
        "<datafile>\n"
        '  <game id="1" name="Advance Wars (USA)">\n'
        '    <rom name="Advance Wars (USA).gba" size="12345" crc="AABBCCDD"/>\n'
        "  </game>\n"
        '  <game id="2" name="Advance Wars (Europe)" cloneofid="1">\n'
        '    <rom name="Advance Wars (Europe).gba" size="12345" crc="11223344"/>\n'
        "  </game>\n"
        "</datafile>\n",
        encoding="utf-8",
    )

    clone_map = rn.load_cloneof_map(dat)
    assert clone_map == {"Advance Wars (Europe)": "Advance Wars (USA)"}
    assert "Advance Wars (USA)" not in clone_map


def test_load_cloneof_map_empty_file(tmp_path):
    dat = tmp_path / "empty.dat"
    dat.write_text("", encoding="utf-8")
    assert rn.load_cloneof_map(dat) == {}


# --- source_region_match_rank ---


def test_source_region_match_rank_zero_when_regions_match():
    """Source filename region matches canonical region => rank 0."""
    c = rc.CollectionCandidate(
        source_path=Path("Advance Wars (USA).gba"),
        canonical_name="Advance Wars (USA)",
        source_kind="file",
        extension=".gba",
        match_source="crc",
    )
    assert c.source_region_match_rank == 0


def test_source_region_match_rank_one_when_regions_differ():
    """France source matched to USA canonical => rank 1 (penalty)."""
    c = rc.CollectionCandidate(
        source_path=Path("Wars Advance (France).n64"),
        canonical_name="Advance Wars (USA)",
        source_kind="file",
        extension=".n64",
        match_source="fuzzy",
    )
    assert c.source_region_match_rank == 1


def test_source_region_match_rank_zero_when_no_regions():
    """No region in either source or canonical => rank 0 (no penalty)."""
    c = rc.CollectionCandidate(
        source_path=Path("some_rom.gba"),
        canonical_name="Some Game",
        source_kind="file",
        extension=".gba",
        match_source="fuzzy",
    )
    assert c.source_region_match_rank == 0


def test_source_region_match_rank_one_when_only_canonical_has_region():
    """Source has no region, canonical does => rank 1 (uncertain, penalty)."""
    c = rc.CollectionCandidate(
        source_path=Path("advance_wars.gba"),
        canonical_name="Advance Wars (USA)",
        source_kind="file",
        extension=".gba",
        match_source="fuzzy",
    )
    assert c.source_region_match_rank == 1


def test_source_region_match_rank_one_japan_to_usa():
    """Japan source upgraded to USA canonical => rank 1."""
    c = rc.CollectionCandidate(
        source_path=Path("Game (Japan).gba"),
        canonical_name="Game (USA)",
        source_kind="file",
        extension=".gba",
        match_source="fuzzy",
    )
    assert c.source_region_match_rank == 1


# --- France-sourced ROM losing to USA-sourced ROM ---


def test_usa_source_beats_france_source_for_same_canonical(tmp_path):
    """A USA-sourced ROM should beat a France-sourced ROM when both resolve
    to the same (USA) canonical name.

    This is the scenario that prompted the source_region_match_rank property:
    The France source gets upgraded to a (USA) canonical via
    find_region_preferred during fuzzy matching.  Both candidates end up with
    canonical_name "Cool Game (USA)" and region_rank 0.  Without
    source_region_match_rank the France-sourced file could win on alphabetical
    tiebreaker.

    We test via _is_better_candidate directly, since the upgrade logic in
    scan_collection depends on fuzzy matching internals.
    """
    # France-sourced file that got upgraded to (USA) canonical
    france_source = rc.CollectionCandidate(
        source_path=Path("Cool Game (France).n64"),
        canonical_name="Cool Game (USA)",
        source_kind="file",
        extension=".n64",
        match_source="fuzzy",
    )
    # Actual USA-sourced file
    usa_source = rc.CollectionCandidate(
        source_path=Path("Cool Game (USA).n64"),
        canonical_name="Cool Game (USA)",
        source_kind="file",
        extension=".n64",
        match_source="fuzzy",
    )

    # Both have the same canonical, region_rank, status_rank, match_rank, etc.
    # The France source should have source_region_match_rank=1 (penalty)
    # while the USA source should have source_region_match_rank=0
    assert france_source.source_region_match_rank == 1
    assert usa_source.source_region_match_rank == 0

    # USA source should be the better candidate
    assert rc._is_better_candidate(usa_source, france_source) is True
    # France source should NOT beat USA source
    assert rc._is_better_candidate(france_source, usa_source) is False


# --- N64 byte-order detection and conversion ---


def test_detect_n64_byte_order_z64():
    assert rn.detect_n64_byte_order(b"\x80\x37\x12\x40") == "z64"


def test_detect_n64_byte_order_v64():
    assert rn.detect_n64_byte_order(b"\x37\x80\x40\x12") == "v64"


def test_detect_n64_byte_order_n64():
    assert rn.detect_n64_byte_order(b"\x40\x12\x37\x80") == "n64"


def test_detect_n64_byte_order_unknown():
    assert rn.detect_n64_byte_order(b"\x00\x00\x00\x00") is None


def test_byteswap_v64():
    """v64 byte-swap: each pair of bytes is swapped."""
    # AB CD -> BA DC
    assert rn._byteswap_v64(b"\x37\x80\x40\x12") == b"\x80\x37\x12\x40"


def test_wordswap_n64():
    """n64 word-swap: each 4-byte group is reversed."""
    # DCBA -> ABCD
    assert rn._wordswap_n64(b"\x40\x12\x37\x80") == b"\x80\x37\x12\x40"


def test_n64_to_z64_noop_for_z64():
    data = b"\x80\x37\x12\x40" + b"\x00" * 60
    assert rn.n64_to_z64(data, "z64") is data  # identity, same object


def test_n64_to_z64_converts_v64():
    v64_data = b"\x37\x80\x40\x12"
    z64_data = rn.n64_to_z64(v64_data, "v64")
    assert z64_data == b"\x80\x37\x12\x40"


def test_n64_to_z64_converts_n64():
    n64_data = b"\x40\x12\x37\x80"
    z64_data = rn.n64_to_z64(n64_data, "n64")
    assert z64_data == b"\x80\x37\x12\x40"


# --- N64 CRC matching for loose files ---


def _make_n64_rom_z64(title: str = "TESTGAME", size: int = 0x100) -> bytes:
    """Build a minimal z64 (big-endian) N64 ROM with the given header title."""
    data = bytearray(size)
    # Magic bytes
    data[0:4] = b"\x80\x37\x12\x40"
    # Title at offset 0x20, 20 bytes, padded with spaces
    title_bytes = title.encode("ascii")[:20].ljust(20, b" ")
    data[0x20:0x34] = title_bytes
    return bytes(data)


def _z64_to_n64(data: bytes) -> bytes:
    """Convert z64 data to n64 (word-swapped) byte order for test purposes."""
    arr = bytearray(data)
    end = len(arr) & ~3
    for i in range(0, end, 4):
        arr[i], arr[i + 1], arr[i + 2], arr[i + 3] = (
            arr[i + 3],
            arr[i + 2],
            arr[i + 1],
            arr[i],
        )
    return bytes(arr)


def _z64_to_v64(data: bytes) -> bytes:
    """Convert z64 data to v64 (byte-swapped) byte order for test purposes."""
    arr = bytearray(data)
    end = len(arr) & ~1
    for i in range(0, end, 2):
        arr[i], arr[i + 1] = arr[i + 1], arr[i]
    return bytes(arr)


def test_crc32_file_n64_matches_z64(tmp_path):
    """CRC of a .n64 file should match the CRC of its .z64 equivalent."""
    z64_data = _make_n64_rom_z64("GOLDENEYE 007")
    n64_data = _z64_to_n64(z64_data)

    z64_file = tmp_path / "game.z64"
    n64_file = tmp_path / "game.n64"
    z64_file.write_bytes(z64_data)
    n64_file.write_bytes(n64_data)

    assert rn._crc32_file(z64_file) == rn._crc32_file(n64_file)


def test_crc32_file_v64_matches_z64(tmp_path):
    """CRC of a .v64 file should match the CRC of its .z64 equivalent."""
    z64_data = _make_n64_rom_z64("MARIO KART 64")
    v64_data = _z64_to_v64(z64_data)

    z64_file = tmp_path / "game.z64"
    v64_file = tmp_path / "game.v64"
    z64_file.write_bytes(z64_data)
    v64_file.write_bytes(v64_data)

    assert rn._crc32_file(z64_file) == rn._crc32_file(v64_file)


def test_crc32_file_non_n64_unchanged(tmp_path):
    """Non-N64 files should not have any byte-swap applied."""
    data = b"\x40\x12\x37\x80" + b"\x00" * 60  # looks like n64 magic but is .gba
    gba_file = tmp_path / "game.gba"
    gba_file.write_bytes(data)
    import zlib

    expected = f"{zlib.crc32(data) & 0xFFFFFFFF:08X}"
    assert rn._crc32_file(gba_file) == expected


# --- N64 CRC matching via scan_collection (loose file) ---


def test_scan_collection_n64_crc_match_for_wordswapped_file(tmp_path):
    """A .n64 (word-swapped) ROM should CRC-match against a DAT with z64 CRCs."""
    import zlib

    z64_data = _make_n64_rom_z64("ZELDA OCARINA")
    n64_data = _z64_to_n64(z64_data)
    z64_crc = f"{zlib.crc32(z64_data) & 0xFFFFFFFF:08X}"

    roms = tmp_path / "roms"
    roms.mkdir()
    (roms / "Zelda (USA).n64").write_bytes(n64_data)

    no_intro = {
        z64_crc: "Legend of Zelda, The - Ocarina of Time (USA)",
    }

    entries, duplicates, unmatched = rc.scan_collection(roms, "N64", no_intro)

    assert len(entries) == 1
    assert entries[0].canonical_name == "Legend of Zelda, The - Ocarina of Time (USA)"
    assert entries[0].match_source == "crc"


# --- N64 CRC matching via scan_collection (zip member) ---


def test_scan_collection_n64_crc_match_for_wordswapped_zip_member(tmp_path):
    """A .n64 zip member should CRC-match after byte-swap."""
    import zlib

    z64_data = _make_n64_rom_z64("SUPER MARIO 64")
    n64_data = _z64_to_n64(z64_data)
    z64_crc = f"{zlib.crc32(z64_data) & 0xFFFFFFFF:08X}"

    roms = tmp_path / "roms"
    roms.mkdir()
    archive = roms / "pack.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("Super Mario 64 (USA).n64", n64_data)

    no_intro = {
        z64_crc: "Super Mario 64 (USA)",
    }

    entries, duplicates, unmatched = rc.scan_collection(roms, "N64", no_intro)

    assert len(entries) == 1
    assert entries[0].canonical_name == "Super Mario 64 (USA)"
    assert entries[0].match_source == "crc"
    assert entries[0].source_kind == "zip"


# --- N64 header reading with byte-swap ---


def test_read_rom_header_title_n64_wordswapped(tmp_path):
    """read_rom_header_title should correctly read titles from .n64 (word-swapped) files."""
    z64_data = _make_n64_rom_z64("GOLDENEYE 007")
    n64_data = _z64_to_n64(z64_data)

    n64_file = tmp_path / "game.n64"
    n64_file.write_bytes(n64_data)

    title = rn.read_rom_header_title(n64_file, "N64")
    assert title == "GOLDENEYE 007"


def test_read_rom_header_title_v64_byteswapped(tmp_path):
    """read_rom_header_title should correctly read titles from .v64 (byte-swapped) files."""
    z64_data = _make_n64_rom_z64("MARIO KART 64")
    v64_data = _z64_to_v64(z64_data)

    v64_file = tmp_path / "game.v64"
    v64_file.write_bytes(v64_data)

    title = rn.read_rom_header_title(v64_file, "N64")
    assert title == "MARIO KART 64"


def test_read_rom_header_title_z64_native(tmp_path):
    """read_rom_header_title should read z64 (native) files correctly (no conversion)."""
    z64_data = _make_n64_rom_z64("PERFECT DARK")

    z64_file = tmp_path / "game.z64"
    z64_file.write_bytes(z64_data)

    title = rn.read_rom_header_title(z64_file, "N64")
    assert title == "PERFECT DARK"


# --- skip_crc option ---


def test_scan_collection_skip_crc_uses_fuzzy_matching(tmp_path):
    """With skip_crc=True, CRC lookup is skipped and matching falls through
    to filename/header/fuzzy.  The match_source should NOT be 'crc'."""
    roms = tmp_path / "roms"
    roms.mkdir()
    (roms / "Advance Wars (USA).gba").write_bytes(b"some-rom-data")

    no_intro = {
        "A": "Advance Wars (USA)",
    }

    entries, duplicates, unmatched = rc.scan_collection(
        roms, "GBA", no_intro, skip_crc=True
    )

    assert len(entries) == 1
    assert entries[0].canonical_name == "Advance Wars (USA)"
    assert entries[0].match_source != "crc"


def test_scan_collection_skip_crc_false_uses_crc(tmp_path):
    """With skip_crc=False (default), CRC lookup is attempted.
    Since our fake CRC 'A' won't match the file's real CRC, it falls
    through to fuzzy.  But if we set the CRC to match, it should hit."""
    import zlib

    roms = tmp_path / "roms"
    roms.mkdir()
    rom_data = b"advance-wars-rom-data"
    (roms / "garbage_filename.gba").write_bytes(rom_data)
    real_crc = f"{zlib.crc32(rom_data) & 0xFFFFFFFF:08X}"

    no_intro = {
        real_crc: "Advance Wars (USA)",
    }

    entries, duplicates, unmatched = rc.scan_collection(
        roms, "GBA", no_intro, skip_crc=False
    )

    assert len(entries) == 1
    assert entries[0].canonical_name == "Advance Wars (USA)"
    assert entries[0].match_source == "crc"


def test_scan_collection_skip_crc_misses_crc_only_match(tmp_path):
    """When skip_crc=True, a file that would only match via CRC (unrecognizable
    filename, no header match) goes to unmatched."""
    import zlib

    roms = tmp_path / "roms"
    roms.mkdir()
    rom_data = b"advance-wars-rom-data"
    (roms / "xyzzy12345.gba").write_bytes(rom_data)
    real_crc = f"{zlib.crc32(rom_data) & 0xFFFFFFFF:08X}"

    no_intro = {
        real_crc: "Advance Wars (USA)",
    }

    entries, duplicates, unmatched = rc.scan_collection(
        roms, "GBA", no_intro, skip_crc=True
    )

    # Without CRC and with a garbage filename, it can't match
    assert len(entries) == 0
    assert len(unmatched) == 1
