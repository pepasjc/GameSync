from pathlib import Path
import sys


# Allow tests under desktop/tests to import the desktop app modules directly.
DESKTOP_ROOT = Path(__file__).resolve().parents[1]
if str(DESKTOP_ROOT) not in sys.path:
    sys.path.insert(0, str(DESKTOP_ROOT))
