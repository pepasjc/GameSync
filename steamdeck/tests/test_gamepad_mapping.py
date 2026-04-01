import sys
from pathlib import Path

from PyQt6.QtCore import Qt


ROOT = Path(__file__).resolve().parents[2]
STEAMDECK_ROOT = ROOT / "steamdeck"
if str(STEAMDECK_ROOT) not in sys.path:
    sys.path.insert(0, str(STEAMDECK_ROOT))

from ui.gamepad_modal import modal_gamepad_key  # noqa: E402


def test_modal_gamepad_key_maps_face_buttons():
    assert modal_gamepad_key(0) == Qt.Key.Key_A
    assert modal_gamepad_key(1) == Qt.Key.Key_B
    assert modal_gamepad_key(2) == Qt.Key.Key_X
    assert modal_gamepad_key(3) == Qt.Key.Key_Y


def test_modal_gamepad_key_ignores_non_face_buttons():
    assert modal_gamepad_key(4) is None
    assert modal_gamepad_key(99) is None


def test_modal_gamepad_key_matches_dialog_controls():
    assert modal_gamepad_key(0) == Qt.Key.Key_A
    assert modal_gamepad_key(1) == Qt.Key.Key_B
    assert modal_gamepad_key(2) == Qt.Key.Key_X
