"""The shared GameSync control scheme on the Steam Deck client: the
Downloads cursor, Sync all's plan and the footer hints per tab."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[2]
STEAMDECK_ROOT = ROOT / "steamdeck"
if str(STEAMDECK_ROOT) not in sys.path:
    sys.path.insert(0, str(STEAMDECK_ROOT))

import pytest  # noqa: E402
from PyQt6.QtCore import QObject, pyqtSignal  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from download_manager import (  # noqa: E402
    STATUS_COMPLETED,
    STATUS_DOWNLOADING,
    STATUS_FAILED,
    STATUS_PAUSED,
    DownloadEntity,
)
from scanner.models import GameEntry, SyncStatus  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _ent(eid, status):
    return DownloadEntity(
        id=eid, rom_id=eid, system="GBA", display_name=eid, filename=f"{eid}.gba",
        part_file_path="", final_file_path="", total_bytes=100,
        downloaded_bytes=10, status=status, error_message="", extract_format="",
        created_at=0, updated_at=0,
    )


class FakeManager(QObject):
    list_changed = pyqtSignal()
    progress = pyqtSignal(str, int, int)
    completed = pyqtSignal(str)

    def __init__(self, entities):
        super().__init__()
        self.entities = {e.id: e for e in entities}
        self.calls = []

    def list_all(self):
        return list(self.entities.values())

    def get(self, eid):
        return self.entities.get(eid)

    def pause(self, eid):
        self.calls.append(("pause", eid))

    def resume(self, eid):
        self.calls.append(("resume", eid))

    def cancel(self, eid):
        self.calls.append(("cancel", eid))

    def remove(self, eid):
        self.calls.append(("remove", eid))
        del self.entities[eid]
        self.list_changed.emit()

    def clear_finished(self):
        pass


def test_downloads_cursor_acts_on_the_selected_row(app):
    from ui.downloads_view import DownloadsView

    manager = FakeManager([
        _ent("a", STATUS_DOWNLOADING),
        _ent("b", STATUS_PAUSED),
        _ent("c", STATUS_COMPLETED),
    ])
    view = DownloadsView(manager)
    assert view.selected_id() == "a"
    assert view.activate_selected() == "paused"
    view.move_selection(1)
    assert view.activate_selected() == "resumed"
    assert manager.calls == [("pause", "a"), ("resume", "b")]

    view.move_selection(5)  # clamps to the last row
    assert view.selected_id() == "c"
    assert view.activate_selected() == ""  # a finished row has no A action
    view.remove_selected()
    assert ("remove", "c") in manager.calls
    assert view.selected_id() == "a"  # cursor falls back to a row that exists

    manager.calls.clear()
    assert view.pause_running() is True
    assert manager.calls == [("pause", "a")]


def test_failed_download_is_retried_by_a(app):
    from ui.downloads_view import DownloadsView

    manager = FakeManager([_ent("x", STATUS_FAILED)])
    view = DownloadsView(manager)
    assert view.activate_selected() == "resumed"
    assert view.pause_running() is False


def _entry(tid, status, save=True, rom=False, server=True, tmp=None):
    path = None
    if save and tmp is not None:
        path = tmp / f"{tid}.srm"
        path.write_bytes(b"x")
    elif save:
        path = Path("/nonexistent") / f"{tid}.srm"
    return GameEntry(
        title_id=tid, display_name=tid, system="GBA", emulator="RetroArch",
        save_path=path, rom_path=Path("/roms/x.gba") if rom else None,
        status=status, server_hash="h" if server else None,
    )


def test_sync_all_plan(tmp_path):
    from ui.main_window import _sync_all_plan

    entries = [
        _entry("up", SyncStatus.LOCAL_NEWER, tmp=tmp_path),
        _entry("new", SyncStatus.LOCAL_ONLY, tmp=tmp_path, server=False),
        _entry("gone", SyncStatus.LOCAL_NEWER),            # file vanished
        _entry("down", SyncStatus.SERVER_NEWER),
        _entry("srv_rom", SyncStatus.SERVER_ONLY, rom=True),
        _entry("srv_norom", SyncStatus.SERVER_ONLY),       # game not installed
        _entry("clash", SyncStatus.CONFLICT),
        _entry("ok", SyncStatus.SYNCED),
    ]
    uploads, downloads, conflicts = _sync_all_plan(entries)
    assert [e.title_id for e in uploads] == ["up", "new"]
    assert [e.title_id for e in downloads] == ["down", "srv_rom"]
    assert [e.title_id for e in conflicts] == ["clash"]


def test_footer_hints_follow_the_shared_scheme(app):
    from ui.controls_bar import ControlsBar

    def labels(mode):
        return {b: label for b, _c, label in ControlsBar._HINTS[mode]}

    assert labels(ControlsBar.MODE_SAVES)["A"] == "Sync"
    assert labels(ControlsBar.MODE_SAVES)["X"] == "Sync all"
    assert labels(ControlsBar.MODE_SAVES)["Y"] == "Details"
    assert labels(ControlsBar.MODE_CATALOG)["X"] == "RA only"
    assert labels(ControlsBar.MODE_CATALOG)["Y"] == "Search"
    # B never starts anything: it is only ever a cancel / back hint.
    for mode, hints in ControlsBar._HINTS.items():
        for button, _c, label in hints:
            if button == "B":
                assert label in ("Clear search", "Pause running"), mode
    assert [b for b, _c, _l in ControlsBar._GLOBAL] == ["L1/R1", "START"]
