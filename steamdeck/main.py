#!/usr/bin/env python3
"""
SaveSync for Steam Deck — entry point.

Launch full-screen, add to Steam as a non-Steam game:
  Game name:    SaveSync
  Target:       /usr/bin/python3
  Start in:     /path/to/3dssync/steamdeck
  Launch opts:  main.py
"""

import os
import sys

# ── Steam Deck display hints ───────────────────────────────────────────────────
# Force Wayland or X11 depending on what's available.
# EmuDeck typically runs under KDE Plasma (Wayland or X11).
if "WAYLAND_DISPLAY" in os.environ and not os.environ.get("QT_QPA_PLATFORM"):
    os.environ.setdefault("QT_QPA_PLATFORM", "wayland")

# High-DPI scaling for 1280×800 display
os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")

# Ensure our own package root is on the path
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon

from ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("SaveSync")
    app.setApplicationDisplayName("SaveSync for Steam Deck")

    # Dark palette handled via stylesheet in theme.py
    app.setStyle("Fusion")

    window = MainWindow()
    window.showFullScreen()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
