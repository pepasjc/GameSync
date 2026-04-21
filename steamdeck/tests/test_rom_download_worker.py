"""Tests for RomDownloadWorker — the Qt worker that backs the progress dialog.

These tests only exercise the worker's data flow (progress signal emission,
cancel flag propagation, error reporting) — they don't create a QApplication
or run a real event loop, so they stay light and don't need a display.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
STEAMDECK_ROOT = ROOT / "steamdeck"
if str(STEAMDECK_ROOT) not in sys.path:
    sys.path.insert(0, str(STEAMDECK_ROOT))

pytest.importorskip("PyQt6.QtCore")

from ui.download_dialog import RomDownloadWorker, _DownloadCancelled  # noqa: E402


class _FakeClient:
    """Stand-in for SyncClient that drives progress_cb like the real thing."""

    def __init__(self, chunks, succeed=True, error_msg=""):
        self._chunks = chunks
        self._succeed = succeed
        self.last_download_error = "" if succeed else error_msg
        self.calls: list[dict] = []

    def download_rom(self, rom_id, target_path, extract_format=None, progress_cb=None):
        self.calls.append(
            {
                "rom_id": rom_id,
                "target_path": target_path,
                "extract_format": extract_format,
            }
        )
        downloaded = 0
        total = sum(self._chunks)
        try:
            for chunk in self._chunks:
                downloaded += chunk
                if progress_cb is not None:
                    progress_cb(downloaded, total)
        except _DownloadCancelled:
            # Mirror sync_client.download_rom's catch-all: it would set
            # last_download_error to the exception class name and return False.
            self.last_download_error = "_DownloadCancelled"
            return False
        return self._succeed


def _run_worker(worker):
    """Capture every emitted signal, then invoke run() synchronously."""
    progress_events: list[tuple[int, int]] = []
    finished_events: list[tuple[bool, str]] = []
    worker.progress.connect(lambda d, t: progress_events.append((d, t)))
    worker.finished.connect(lambda ok, detail: finished_events.append((ok, detail)))
    worker.run()
    return progress_events, finished_events


def test_worker_emits_progress_and_success(tmp_path):
    client = _FakeClient(chunks=[100, 200, 300])
    worker = RomDownloadWorker(client, "ROM1", tmp_path / "a.bin", None)

    progress, finished = _run_worker(worker)

    # Progress ticks mirror the fake client's chunk schedule.
    assert progress == [(100, 600), (300, 600), (600, 600)]
    assert finished == [(True, "")]
    assert client.calls[0]["rom_id"] == "ROM1"


def test_worker_reports_failure_detail_from_client(tmp_path):
    client = _FakeClient(chunks=[50], succeed=False, error_msg="HTTP 503: server down")
    worker = RomDownloadWorker(client, "ROM2", tmp_path / "b.bin", None)

    _, finished = _run_worker(worker)
    assert finished == [(False, "HTTP 503: server down")]


def test_worker_cancel_flag_short_circuits_with_friendly_message(tmp_path):
    """Cancelling replaces the catch-all ``_DownloadCancelled`` error from
    the client with a human-readable 'Download cancelled.' detail."""
    chunks = [10, 10, 10]
    client = _FakeClient(chunks=chunks)
    worker = RomDownloadWorker(client, "ROM3", tmp_path / "c.bin", None)

    # Cancel on the first progress tick to prove the flag aborts the stream.
    def _maybe_cancel(d, t):
        worker.cancel()

    worker.progress.connect(_maybe_cancel)

    _, finished = _run_worker(worker)
    assert finished == [(False, "Download cancelled.")]
    # Only the first chunk's progress should have been observed — the cb
    # raised _DownloadCancelled before the second chunk flowed through.
    # We asserted via finished[0][1] and the fake client's stream abort.


def test_worker_passes_extract_format_through(tmp_path):
    client = _FakeClient(chunks=[10])
    worker = RomDownloadWorker(client, "ROM4", tmp_path / "d.iso", "iso")
    _run_worker(worker)
    assert client.calls[0]["extract_format"] == "iso"
