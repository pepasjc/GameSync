from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtCore")

ROOT = Path(__file__).resolve().parents[2]
STEAMDECK_ROOT = ROOT / "steamdeck"
if str(STEAMDECK_ROOT) not in sys.path:
    sys.path.insert(0, str(STEAMDECK_ROOT))

from scanner.models import GameEntry  # noqa: E402
from ui.main_window import ServerWorker  # noqa: E402


class _FakeClient:
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def normalize_batch(self, roms):
        self.calls.append(roms)
        return self.mapping


def test_server_worker_rekeys_3ds_slug_entries_via_normalize():
    entry = GameEntry(
        title_id="3DS_super_mario_3d_land_usa",
        display_name="Super Mario 3D Land (USA)",
        system="3DS",
        emulator="RetroArch",
        rom_filename="Super Mario 3D Land (USA).3ds",
    )
    client = _FakeClient(
        {
            ("3DS", "Super Mario 3D Land (USA).3ds"): "0004000000054000",
        }
    )

    worker = ServerWorker([entry], client, ".")
    worker._enrich_title_ids()

    assert client.calls == [[{"system": "3DS", "filename": "Super Mario 3D Land (USA).3ds"}]]
    assert entry.title_id == "0004000000054000"
