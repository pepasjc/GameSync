#!/usr/bin/env python3
"""
SaveSync — entry point.

Works on Steam Deck (full-screen) and PC (windowed).

  Steam Deck:   Add as non-Steam game, runs full-screen automatically.
  PC:           python main.py              (windowed 1280×800)
                python main.py --fullscreen (force full-screen)

The app auto-detects Steam Deck by checking for the SteamOS marker.
"""

import os
import sys

# ── Display hints ──────────────────────────────────────────────────────────────
if "WAYLAND_DISPLAY" in os.environ and not os.environ.get("QT_QPA_PLATFORM"):
    os.environ.setdefault("QT_QPA_PLATFORM", "wayland")

os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")

# Ensure our own package root is on the path
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt6.QtWidgets import QApplication

from ui.main_window import MainWindow


def _is_steam_deck() -> bool:
    """Detect Steam Deck by SteamOS marker or device board name."""
    if os.path.exists("/etc/steamos-release"):
        return True
    try:
        with open("/sys/devices/virtual/dmi/id/board_name") as f:
            return "Jupiter" in f.read() or "Galileo" in f.read()
    except OSError:
        return False


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("SaveSync")
    app.setApplicationDisplayName("SaveSync")
    app.setStyle("Fusion")

    window = MainWindow()

    # Determine display mode: fullscreen on Steam Deck, windowed on PC
    force_fullscreen = "--fullscreen" in sys.argv
    force_windowed = "--windowed" in sys.argv

    if force_fullscreen:
        window.showFullScreen()
    elif force_windowed or not _is_steam_deck():
        window.resize(1280, 800)
        window.show()
    else:
        window.showFullScreen()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
