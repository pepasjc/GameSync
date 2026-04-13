from tabs.sync_tab import sync_row_matches_filters


def test_sync_row_matches_filters_hides_server_only_when_enabled():
    assert (
        sync_row_matches_filters(
            system="SNES",
            game="Super Metroid",
            title_id="SNES_super_metroid",
            status="Server only",
            system_filter="All",
            status_filter="All",
            search="",
            skip_server_only=True,
        )
        is False
    )


def test_sync_row_matches_filters_keeps_server_only_when_disabled():
    assert (
        sync_row_matches_filters(
            system="SNES",
            game="Super Metroid",
            title_id="SNES_super_metroid",
            status="Server only",
            system_filter="All",
            status_filter="All",
            search="",
            skip_server_only=False,
        )
        is True
    )


def test_sync_row_matches_filters_still_applies_other_filters():
    assert (
        sync_row_matches_filters(
            system="SNES",
            game="Super Metroid",
            title_id="SNES_super_metroid",
            status="Local newer",
            system_filter="SNES",
            status_filter="Local newer",
            search="metroid",
            skip_server_only=True,
        )
        is True
    )
