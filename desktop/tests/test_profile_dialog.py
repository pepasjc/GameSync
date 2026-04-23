from dialogs.profile_dialog import (
    DEVICE_SYSTEMS,
    RETROARCH_AUTO_CORE_LABEL,
    _retroarch_core_options,
    _retroarch_display_value,
    _retroarch_storage_values,
    _retroarch_saturn_display_value,
    _retroarch_saturn_storage_values,
)


def test_retroarch_saturn_display_value_maps_legacy_extensions_to_core_names():
    assert _retroarch_saturn_display_value(".bkr", "") == "Beetle Saturn"
    assert _retroarch_saturn_display_value(".srm", "") == "Yabause"
    assert _retroarch_saturn_display_value(".bin", "") == "YabaSanshiro"
    assert (
        _retroarch_saturn_display_value(".bkr", r"E:\retroarch\saves\yabasanshiro")
        == "YabaSanshiro"
    )


def test_retroarch_saturn_storage_values_preserve_compatibility():
    assert _retroarch_saturn_storage_values("Beetle Saturn", "") == (".bkr", "")
    assert _retroarch_saturn_storage_values("Yabause", "") == (".srm", "")
    assert _retroarch_saturn_storage_values("YabaSanshiro", "") == (".bin", "")


def test_retroarch_saturn_storage_values_clear_stale_yabasanshiro_override():
    assert _retroarch_saturn_storage_values(
        "Beetle Saturn",
        r"E:\retroarch\saves\yabasanshiro",
    ) == (".bkr", "")
    assert _retroarch_saturn_storage_values(
        "Yabause",
        r"E:\retroarch\saves\yabasanshiro",
    ) == (".srm", "")


def test_retroarch_core_options_include_auto_and_known_cores():
    assert _retroarch_core_options("GBA") == ["Auto", "mGBA", "VBA-M"]
    assert _retroarch_core_options("SEGACD") == ["Auto", "Genesis Plus GX", "PicoDrive"]


def test_retroarch_display_value_prefers_saved_core_for_non_saturn():
    assert _retroarch_display_value("GBA", ".srm", "", "mGBA") == "mGBA"
    assert _retroarch_display_value("GBA", ".srm", "", "") == RETROARCH_AUTO_CORE_LABEL


def test_retroarch_storage_values_store_core_and_hide_extension_choice():
    assert _retroarch_storage_values(
        "GBA",
        "mGBA",
        "",
        current_ext=".sav",
    ) == (".srm", "", "mGBA")
    assert _retroarch_storage_values(
        "GBA",
        RETROARCH_AUTO_CORE_LABEL,
        "",
        current_ext=".sav",
    ) == (".sav", "", "")


def test_retroarch_profile_does_not_offer_ps3_system():
    assert "PS3" not in DEVICE_SYSTEMS["RetroArch"]
