from pathlib import Path
import sys
from unittest.mock import MagicMock


DESKTOP_ROOT = Path(__file__).resolve().parents[1]
if str(DESKTOP_ROOT) not in sys.path:
    sys.path.insert(0, str(DESKTOP_ROOT))

_mods = {}
for _mod_name in [
    "PyQt6",
    "PyQt6.QtCore",
    "PyQt6.QtGui",
    "PyQt6.QtWidgets",
    "PyQt6.QtNetwork",
]:
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = MagicMock()
