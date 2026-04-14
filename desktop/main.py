import argparse
import os
import sys
import traceback
from PyQt6.QtWidgets import QApplication, QMessageBox
from window import SaveManagerWindow


def _global_excepthook(exc_type, exc_value, exc_tb):
    traceback.print_exception(exc_type, exc_value, exc_tb)
    try:
        msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        QMessageBox.critical(None, "Unhandled Error", msg[:4000])
    except Exception:
        pass


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Save Manager desktop client")
    parser.add_argument(
        "--debug-scan",
        action="store_true",
        help="Enable sync scan debug logging to desktop/scan_debug.log",
    )
    parser.add_argument(
        "--debug-scan-file",
        default="",
        help="Optional path for scan debug output when --debug-scan is enabled",
    )
    return parser


def _apply_cli_debug_settings(args: argparse.Namespace) -> None:
    if args.debug_scan:
        os.environ["SYNC_DEBUG_SCAN"] = "1"
    if args.debug_scan_file:
        os.environ["SYNC_DEBUG_SCAN_FILE"] = args.debug_scan_file


def main(argv: list[str] | None = None):
    parser = _build_parser()
    args = parser.parse_args(argv)
    _apply_cli_debug_settings(args)

    qt_argv = [sys.argv[0]]
    app = QApplication(qt_argv)
    sys.excepthook = _global_excepthook
    window = SaveManagerWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
