import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


ROOT = Path(__file__).resolve().parents[2]
STEAMDECK_ROOT = ROOT / "steamdeck"
if str(STEAMDECK_ROOT) not in sys.path:
    sys.path.insert(0, str(STEAMDECK_ROOT))

from scanner.models import GameEntry  # noqa: E402
from ui.game_list import _find_selection_row  # noqa: E402


def _entry(title_id: str, name: str) -> GameEntry:
    return GameEntry(
        title_id=title_id,
        display_name=name,
        system="PS3",
        emulator="RPCS3",
    )


def test_find_selection_row_preserves_selected_title():
    entries = [
        _entry("BLUS00003-SLOT0", "Charlie"),
        _entry("BLUS00002-SLOT0", "Bravo"),
        _entry("BLUS00001-SLOT0", "Alpha"),
    ]

    assert _find_selection_row(entries, "BLUS00002-SLOT0") == 1


def test_find_selection_row_falls_back_to_first_when_missing():
    entries = [
        _entry("BLUS00001-SLOT0", "Alpha"),
        _entry("BLUS00003-SLOT0", "Charlie"),
    ]

    assert _find_selection_row(entries, "BLUS00002-SLOT0") == 0


def test_find_selection_row_handles_empty_entries():
    assert _find_selection_row([], "BLUS00002-SLOT0") == -1
