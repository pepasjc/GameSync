from pathlib import Path
import zipfile

import rom_collection as rc


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
    (
        roms / "Advance Wars (Japan) [T-En by Someone].gba"
    ).write_bytes(b"translated")

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
    (
        roms / "Aigle de Guerre, L' (France) [T-En by Mkol103 v1.0].gba"
    ).write_bytes(b"translated")

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


def test_build_letter_buckets_for_four_folders():
    assert rc.build_letter_buckets(4) == [("A", "G"), ("H", "N"), ("O", "T"), ("U", "Z")]


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
