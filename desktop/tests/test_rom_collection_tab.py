from pathlib import Path

from tabs.rom_collection_tab import format_build_confirmation_message


def test_build_confirmation_message_excludes_unmatched_from_total_when_disabled():
    message = format_build_confirmation_message(
        output_folder=Path(r"H:\ROMs\snes"),
        matched_count=1609,
        unmatched_found_count=329,
        include_unmatched=False,
        split_into_ranges=False,
        bucket_count=4,
        unzip_archives=True,
        one_game_one_rom=True,
    )

    assert "Total files to write: 1609" in message
    assert "Mode: 1G1R preferred set." in message
    assert "Matched games: 1609 file(s), copied flat into the output folder." in message
    assert "Unmatched files: 329 found, 0 will be copied." in message


def test_build_confirmation_message_includes_unmatched_in_total_when_enabled():
    message = format_build_confirmation_message(
        output_folder=Path(r"H:\ROMs\snes"),
        matched_count=1609,
        unmatched_found_count=329,
        include_unmatched=True,
        split_into_ranges=True,
        bucket_count=4,
        unzip_archives=False,
        one_game_one_rom=False,
    )

    assert "Total files to write: 1938" in message
    assert "Mode: complete collection (all matched variants)." in message
    assert "Matched games: 1609 file(s), split into 4 letter-range folders." in message
    assert (
        "Unmatched files: 329 found, 329 will be copied into the 'unmatched files' subfolder."
        in message
    )
    assert "Zipped ROMs will be copied as .zip files." in message
