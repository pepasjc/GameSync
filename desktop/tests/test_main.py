import os

import main as desktop_main


def test_apply_cli_debug_settings_enables_scan_debug(monkeypatch):
    monkeypatch.delenv("SYNC_DEBUG_SCAN", raising=False)
    monkeypatch.delenv("SYNC_DEBUG_SCAN_FILE", raising=False)

    args = desktop_main._build_parser().parse_args(
        ["--debug-scan", "--debug-scan-file", "C:/tmp/scan.log"]
    )
    desktop_main._apply_cli_debug_settings(args)

    assert os.environ["SYNC_DEBUG_SCAN"] == "1"
    assert os.environ["SYNC_DEBUG_SCAN_FILE"] == "C:/tmp/scan.log"


def test_apply_cli_debug_settings_leaves_env_unchanged_without_flags(monkeypatch):
    monkeypatch.delenv("SYNC_DEBUG_SCAN", raising=False)
    monkeypatch.delenv("SYNC_DEBUG_SCAN_FILE", raising=False)

    args = desktop_main._build_parser().parse_args([])
    desktop_main._apply_cli_debug_settings(args)

    assert "SYNC_DEBUG_SCAN" not in os.environ
    assert "SYNC_DEBUG_SCAN_FILE" not in os.environ
