"""Background ROM download queue with pause/resume support.

This module mirrors the Android client's ``DownloadManager`` (under
``android/app/src/main/kotlin/.../sync/DownloadManager.kt``) so the Steam
Deck client behaves the same way: downloads run in the background,
survive a Wi-Fi blip via HTTP Range resume, and persist across app
restarts so a 4 GB CHD doesn't have to start over if the user closes
the app halfway through.

Architecture
------------

* **``DownloadEntity``** — dataclass mirroring the row layout in the
  ``downloads`` SQLite table.  Status is one of ``queued``,
  ``downloading``, ``paused``, ``completed``, ``failed``, ``cancelled``;
  the same six values Android uses, so the two clients can read each
  other's tables conceptually if we ever decide to share state.
* **``_DownloadWorker``** — ``QObject`` that streams a single ROM from
  the server to a ``.part`` file beside the final destination.  Sends a
  ``Range: bytes=<offset>-`` header so resumes append rather than
  re-download from zero.  Polls a ``_control`` flag set by the manager
  to honour pause/cancel between chunks.
* **``DownloadManager``** — singleton-style ``QObject`` exposed via
  Qt signals (``list_changed``, ``progress``, ``status_changed``,
  ``completed``).  Owns the SQLite connection and at most
  ``MAX_CONCURRENT`` worker threads at a time.  Public API is the
  ``enqueue``/``pause``/``resume``/``cancel``/``remove``/``clear_finished``
  set called from the UI tab and the legacy trigger sites.

Concurrency
-----------

For the first cut we run **one** download at a time (Steam Deck WiFi is
shared with whatever the user is playing).  ``MAX_CONCURRENT`` is a
constant rather than a config knob because the UI doesn't expose it
yet; bumping it only requires changing the constant.

Persistence
-----------

The DB is written every ~250 ms during the first 5 seconds of a
transfer (so a quick crash doesn't lose all progress) and every ~1500
ms thereafter (cheap for steady-state downloads).  ``fsync`` is called
on the ``.part`` file every 5 seconds so the OS actually flushes data
even on lossy power loss.

On app start, ``recover_interrupted()`` flips any rows still in
``downloading`` / ``queued`` to ``paused`` — we don't auto-resume
because the user might have closed the app deliberately and we don't
want a freshly-launched UI to start hammering the network without
permission.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import requests
from PyQt6.QtCore import QObject, QThread, pyqtSignal


# ── Public constants ─────────────────────────────────────────────────────────

# Status string values.  Stored verbatim in SQLite so they're queryable
# without a join, and mirror the Android ``DownloadEntity.Status``
# constants for cross-client parity.
STATUS_QUEUED = "queued"
STATUS_DOWNLOADING = "downloading"
STATUS_PAUSED = "paused"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

TERMINAL_STATUSES = (STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED)
ACTIVE_STATUSES = (STATUS_QUEUED, STATUS_DOWNLOADING)

# How many downloads can run in parallel.  Steam Deck connections are
# shared with games, so single-stream is the safer default.  Raising
# this also requires expanding the manager's worker map (already keyed
# by id).
MAX_CONCURRENT = 3

# 256 KB I/O buffer — large enough that ``iter_content`` doesn't
# Python-overhead us to death on multi-GB transfers, small enough that
# pause/cancel responsiveness stays sub-second on slow disks.
_CHUNK_SIZE = 256 * 1024

# Progress / persist throttles, in seconds.
_PROGRESS_EMIT_INTERVAL = 0.20      # 5 Hz progress UI updates
_DB_PERSIST_FAST_INTERVAL = 0.25    # first 5s: persist often
_DB_PERSIST_SLOW_INTERVAL = 1.50    # after that: relaxed
_DB_PERSIST_FAST_WINDOW = 5.0       # seconds of "fast" persistence at start
_FSYNC_INTERVAL = 5.0               # how often to flush .part to disk

# (connect_timeout, read_timeout) for the HTTP request.  Match
# SyncClient._download_timeout — long read so server-side CHD/RVZ
# extraction has room to finish before the first byte.
_DOWNLOAD_TIMEOUT = (30, 900)


# ── Data model ───────────────────────────────────────────────────────────────


@dataclass
class DownloadEntity:
    """Row of the ``downloads`` SQLite table.

    Field names match the Android ``DownloadEntity`` so a future
    cross-client tool can read both schemas with the same parser.
    ``progress_fraction`` is computed on demand because it depends on
    a known total size.
    """

    id: str
    rom_id: str
    system: str
    display_name: str
    filename: str
    part_file_path: str
    final_file_path: str
    total_bytes: int
    downloaded_bytes: int
    status: str
    error_message: str
    extract_format: str
    created_at: float
    updated_at: float

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def progress_fraction(self) -> Optional[float]:
        if self.total_bytes <= 0:
            return None
        return max(0.0, min(1.0, self.downloaded_bytes / self.total_bytes))


# ── SQLite helpers ───────────────────────────────────────────────────────────


_SCHEMA = """
CREATE TABLE IF NOT EXISTS downloads (
    id               TEXT PRIMARY KEY,
    rom_id           TEXT NOT NULL,
    system           TEXT NOT NULL DEFAULT '',
    display_name     TEXT NOT NULL DEFAULT '',
    filename         TEXT NOT NULL DEFAULT '',
    part_file_path   TEXT NOT NULL DEFAULT '',
    final_file_path  TEXT NOT NULL DEFAULT '',
    total_bytes      INTEGER NOT NULL DEFAULT 0,
    downloaded_bytes INTEGER NOT NULL DEFAULT 0,
    status           TEXT NOT NULL DEFAULT 'queued',
    error_message    TEXT NOT NULL DEFAULT '',
    extract_format   TEXT NOT NULL DEFAULT '',
    created_at       REAL NOT NULL DEFAULT 0,
    updated_at       REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_downloads_status ON downloads(status);
CREATE INDEX IF NOT EXISTS idx_downloads_updated ON downloads(updated_at DESC);
"""


def _row_to_entity(row: sqlite3.Row) -> DownloadEntity:
    return DownloadEntity(
        id=row["id"],
        rom_id=row["rom_id"],
        system=row["system"],
        display_name=row["display_name"],
        filename=row["filename"],
        part_file_path=row["part_file_path"],
        final_file_path=row["final_file_path"],
        total_bytes=int(row["total_bytes"]),
        downloaded_bytes=int(row["downloaded_bytes"]),
        status=row["status"],
        error_message=row["error_message"],
        extract_format=row["extract_format"],
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
    )


# ── Worker thread ────────────────────────────────────────────────────────────


class _Cancelled(Exception):
    """Internal sentinel — user requested ``cancel``; .part is deleted."""


class _Paused(Exception):
    """Internal sentinel — user requested ``pause``; .part is preserved."""


class _DownloadWorker(QObject):
    """Streams a single ROM in a worker thread.

    The manager owns the QThread; this object only handles the I/O
    loop and emits progress / completion signals back to the manager
    on the GUI thread (queued connections via ``moveToThread``).
    """

    # downloaded_bytes, total_bytes
    progress = pyqtSignal(str, int, int)
    # final_status (one of TERMINAL_STATUSES + STATUS_PAUSED), error_message
    finished = pyqtSignal(str, str, str)

    def __init__(self, entity: DownloadEntity, base_url: str, headers: dict):
        super().__init__()
        self._entity = entity
        self._base_url = base_url
        self._headers = dict(headers)
        # Set externally by the manager: ``None`` means keep going.
        # ``"pause"`` and ``"cancel"`` flip the loop into the matching
        # exception path on the next chunk boundary.
        self._control: Optional[str] = None
        self._control_lock = threading.Lock()

    def request_pause(self) -> None:
        with self._control_lock:
            self._control = "pause"

    def request_cancel(self) -> None:
        with self._control_lock:
            self._control = "cancel"

    def _check_control(self) -> None:
        with self._control_lock:
            ctl = self._control
        if ctl == "cancel":
            raise _Cancelled
        if ctl == "pause":
            raise _Paused

    def run(self) -> None:
        ent = self._entity
        eid = ent.id
        part_path = Path(ent.part_file_path)
        final_path = Path(ent.final_file_path)
        # Tracks bytes actually written to disk this session (for the
        # progress signal).  Loaded from DB so a resume reports the
        # full byte count, not just the new chunk delta.
        downloaded = ent.downloaded_bytes
        total = ent.total_bytes if ent.total_bytes > 0 else 0

        params = {}
        if ent.extract_format:
            params["extract"] = ent.extract_format

        # Pre-flight: if the .part file no longer exists (user wiped
        # config dir, etc.) restart from zero rather than ask the
        # server for a Range it can't honour against an empty file.
        if downloaded > 0 and not part_path.exists():
            downloaded = 0

        headers = dict(self._headers)
        if downloaded > 0:
            headers["Range"] = f"bytes={downloaded}-"

        try:
            part_path.parent.mkdir(parents=True, exist_ok=True)

            with requests.get(
                f"{self._base_url}/roms/{ent.rom_id}",
                params=params,
                headers=headers,
                stream=True,
                timeout=_DOWNLOAD_TIMEOUT,
            ) as r:
                # Server didn't honour our Range — most likely because
                # the ?extract= variant streams from a fresh tempfile.
                # Fall back to a full restart so we don't end up with
                # a corrupted .part where the head is stale.
                honoured_range = (r.status_code == 206)
                if not honoured_range and downloaded > 0:
                    downloaded = 0

                if r.status_code not in (200, 206):
                    detail = ""
                    try:
                        detail = r.text.strip()
                    except Exception:
                        pass
                    msg = f"HTTP {r.status_code}"
                    if detail:
                        msg += f": {detail}"
                    self.finished.emit(eid, STATUS_FAILED, msg)
                    return

                # Trust ``Content-Range: bytes A-B/C`` over
                # ``Content-Length`` for a 206 (length there is just the
                # remaining bytes).  Falls back to ``Content-Length`` +
                # current offset for 206, or ``Content-Length`` alone
                # for 200.
                cr = r.headers.get("Content-Range", "")
                if cr and "/" in cr:
                    try:
                        total = int(cr.split("/", 1)[1])
                    except ValueError:
                        pass
                if total <= 0:
                    cl = int(r.headers.get("Content-Length", "0") or 0)
                    if cl > 0:
                        total = downloaded + cl if honoured_range else cl

                # Open the .part for append-or-create.  ``r+b`` errors if
                # the file doesn't exist, so use ``a+b`` then seek.
                mode = "r+b" if (honoured_range and part_path.exists()) else "wb"
                with open(part_path, mode) as f:
                    if mode == "r+b":
                        f.seek(downloaded)
                    else:
                        downloaded = 0

                    last_emit = 0.0
                    last_persist = 0.0
                    last_fsync = time.monotonic()
                    started_at = time.monotonic()

                    # Initial emit so the UI flips from "Queued" to a
                    # real progress bar without waiting a chunk.
                    self.progress.emit(eid, downloaded, total)

                    for chunk in r.iter_content(chunk_size=_CHUNK_SIZE):
                        # Cancel/pause check between every chunk —
                        # bounded responsiveness even for big chunks.
                        self._check_control()
                        if not chunk:
                            continue
                        f.write(chunk)
                        downloaded += len(chunk)

                        now = time.monotonic()
                        # Throttled UI signal.
                        if now - last_emit >= _PROGRESS_EMIT_INTERVAL:
                            self.progress.emit(eid, downloaded, total)
                            last_emit = now
                        # Throttled DB persist (manager listens to
                        # progress signal; persist piggybacks on it).
                        # Tighter cadence for the first few seconds so
                        # a crash near the start doesn't lose much.
                        elapsed = now - started_at
                        persist_interval = (
                            _DB_PERSIST_FAST_INTERVAL
                            if elapsed < _DB_PERSIST_FAST_WINDOW
                            else _DB_PERSIST_SLOW_INTERVAL
                        )
                        if now - last_persist >= persist_interval:
                            # Manager handles the actual SQL write via
                            # the ``progress`` signal; we just nudge it.
                            self.progress.emit(eid, downloaded, total)
                            last_persist = now

                        if now - last_fsync >= _FSYNC_INTERVAL:
                            try:
                                f.flush()
                                os.fsync(f.fileno())
                            except OSError:
                                # Some FS / network mounts reject
                                # fsync; not fatal, the OS will flush
                                # on close anyway.
                                pass
                            last_fsync = now

                    # Flush + final progress before we rename.
                    try:
                        f.flush()
                        os.fsync(f.fileno())
                    except OSError:
                        pass

                # Atomic rename .part → final.  Cross-filesystem moves
                # raise OSError; fall back to copy+unlink.
                final_path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    part_path.replace(final_path)
                except OSError:
                    import shutil
                    shutil.copy2(part_path, final_path)
                    try:
                        part_path.unlink()
                    except OSError:
                        pass

                # If we got here without knowing the total size, assume
                # downloaded == total so the bar locks at 100%.
                final_total = total if total > 0 else downloaded
                self.progress.emit(eid, downloaded, final_total)
                self.finished.emit(eid, STATUS_COMPLETED, "")

        except _Paused:
            # Leave .part in place so the next resume picks up where
            # we stopped.
            self.progress.emit(eid, downloaded, total)
            self.finished.emit(eid, STATUS_PAUSED, "")
        except _Cancelled:
            try:
                part_path.unlink(missing_ok=True)
            except OSError:
                pass
            self.finished.emit(eid, STATUS_CANCELLED, "")
        except requests.exceptions.Timeout:
            self.finished.emit(
                eid,
                STATUS_FAILED,
                "Timed out waiting for the server.  CHD/RVZ extractions "
                "can take several minutes for very large games.",
            )
        except Exception as exc:  # noqa: BLE001 — surface anything else
            self.finished.emit(eid, STATUS_FAILED, str(exc) or exc.__class__.__name__)


# ── Manager ──────────────────────────────────────────────────────────────────


class DownloadManager(QObject):
    """Singleton-ish manager for the ROM download queue.

    Owns one SQLite connection, at most ``MAX_CONCURRENT`` worker
    threads, and emits Qt signals for the UI tab to subscribe to.
    """

    # Whole-list change (insert / delete / status flip).  Coarse —
    # the view re-pulls ``list_all()`` when this fires.  Used for
    # structural changes only; per-row progress goes through
    # ``progress``.
    list_changed = pyqtSignal()
    # id, downloaded_bytes, total_bytes
    progress = pyqtSignal(str, int, int)
    # id, new_status
    status_changed = pyqtSignal(str, str)
    # Fired once per row that transitions to COMPLETED.  The main
    # window listens so it can rescan and refresh the Installed tab.
    completed = pyqtSignal(str)

    def __init__(self, client, db_path: Path, parent: QObject | None = None):
        super().__init__(parent)
        self._client = client
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        # ``check_same_thread=False`` because the manager methods may
        # be called from worker callbacks (signal handlers run on the
        # GUI thread, but we still want the lock for safety).
        self._conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
            timeout=10.0,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._db_lock = threading.Lock()

        # id → (QThread, _DownloadWorker).  Bounded by MAX_CONCURRENT.
        self._workers: dict[str, tuple[QThread, _DownloadWorker]] = {}

        # Recover from any state left over from a previous run.
        self._recover_interrupted()

    # ── Recovery / lifecycle ────────────────────────────────────────

    def _recover_interrupted(self) -> None:
        """Flip stuck DOWNLOADING/QUEUED rows to PAUSED on startup.

        We don't auto-resume — that would surprise users who closed the
        app on purpose to stop the download.  The Downloads tab makes
        the ``Resume`` button obvious enough.
        """
        with self._db_lock:
            cur = self._conn.execute(
                "UPDATE downloads SET status = ?, updated_at = ? "
                "WHERE status IN (?, ?)",
                (STATUS_PAUSED, time.time(), STATUS_DOWNLOADING, STATUS_QUEUED),
            )
            self._conn.commit()
            recovered = cur.rowcount
        if recovered:
            print(f"[downloads] flipped {recovered} interrupted rows → paused")

    def shutdown(self) -> None:
        """Best-effort: pause any running downloads before app exit.

        Called from MainWindow.closeEvent.  We don't wait for the HTTP
        connection to close — that can hang for the read timeout — but
        we do flag the workers so the .part files keep their content.
        """
        for _eid, (thread, worker) in list(self._workers.items()):
            try:
                worker.request_pause()
            except Exception:
                pass
            thread.quit()
            thread.wait(1500)
        try:
            self._conn.close()
        except sqlite3.Error:
            pass

    # ── Queries ─────────────────────────────────────────────────────

    def list_all(self) -> list[DownloadEntity]:
        """Return every row, newest-updated first (matches Android UI)."""
        with self._db_lock:
            cur = self._conn.execute(
                "SELECT * FROM downloads ORDER BY updated_at DESC"
            )
            return [_row_to_entity(r) for r in cur.fetchall()]

    def get(self, eid: str) -> Optional[DownloadEntity]:
        with self._db_lock:
            cur = self._conn.execute(
                "SELECT * FROM downloads WHERE id = ? LIMIT 1", (eid,)
            )
            row = cur.fetchone()
        return _row_to_entity(row) if row else None

    def active_count(self) -> int:
        with self._db_lock:
            cur = self._conn.execute(
                "SELECT COUNT(*) FROM downloads WHERE status IN (?, ?)",
                (STATUS_DOWNLOADING, STATUS_QUEUED),
            )
            return int(cur.fetchone()[0])

    # ── Mutations ───────────────────────────────────────────────────

    def enqueue(
        self,
        rom_id: str,
        system: str,
        display_name: str,
        target_path: Path,
        extract_format: Optional[str] = None,
        expected_size: int = 0,
    ) -> str:
        """Create a new row in QUEUED state and kick the worker.

        Returns the new download id so the UI can navigate to the
        Downloads tab and highlight the freshly-added row.
        """
        eid = str(uuid.uuid4())
        target_path = Path(target_path)
        part_path = target_path.with_suffix(target_path.suffix + ".part")
        now = time.time()
        with self._db_lock:
            self._conn.execute(
                "INSERT INTO downloads "
                "(id, rom_id, system, display_name, filename, "
                "part_file_path, final_file_path, total_bytes, "
                "downloaded_bytes, status, error_message, "
                "extract_format, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    eid,
                    rom_id,
                    (system or "").upper(),
                    display_name or rom_id,
                    target_path.name,
                    str(part_path),
                    str(target_path),
                    int(expected_size or 0),
                    0,
                    STATUS_QUEUED,
                    "",
                    extract_format or "",
                    now,
                    now,
                ),
            )
            self._conn.commit()
        self.list_changed.emit()
        self.status_changed.emit(eid, STATUS_QUEUED)
        self._kick_next()
        return eid

    def pause(self, eid: str) -> None:
        worker_pair = self._workers.get(eid)
        if worker_pair is not None:
            # Active: ask worker to stop on next chunk; the finished
            # signal does the DB flip + signal emit.
            worker_pair[1].request_pause()
            return
        # Queued but not yet started — flip directly.
        ent = self.get(eid)
        if ent and ent.status == STATUS_QUEUED:
            self._update_status(eid, STATUS_PAUSED, "")
            self.list_changed.emit()
            self.status_changed.emit(eid, STATUS_PAUSED)

    def resume(self, eid: str) -> None:
        ent = self.get(eid)
        if not ent or ent.status == STATUS_COMPLETED:
            return
        # Sync downloaded_bytes with the actual .part file size in
        # case the process died between the last persist and a flush.
        part = Path(ent.part_file_path)
        on_disk = part.stat().st_size if part.exists() else 0
        if on_disk != ent.downloaded_bytes:
            with self._db_lock:
                self._conn.execute(
                    "UPDATE downloads SET downloaded_bytes = ?, "
                    "updated_at = ? WHERE id = ?",
                    (on_disk, time.time(), eid),
                )
                self._conn.commit()
        self._update_status(eid, STATUS_QUEUED, "")
        self.list_changed.emit()
        self.status_changed.emit(eid, STATUS_QUEUED)
        self._kick_next()

    def cancel(self, eid: str) -> None:
        worker_pair = self._workers.get(eid)
        if worker_pair is not None:
            worker_pair[1].request_cancel()
            return
        # Queued / paused / failed — flip directly + delete .part.
        ent = self.get(eid)
        if not ent or ent.status == STATUS_COMPLETED:
            return
        try:
            Path(ent.part_file_path).unlink(missing_ok=True)
        except OSError:
            pass
        self._update_status(eid, STATUS_CANCELLED, "")
        self.list_changed.emit()
        self.status_changed.emit(eid, STATUS_CANCELLED)

    def remove(self, eid: str) -> None:
        """Delete the row entirely.  Active downloads are cancelled first."""
        ent = self.get(eid)
        if ent and ent.status in ACTIVE_STATUSES:
            self.cancel(eid)
        # Always wipe any leftover .part — terminal-state rows can
        # still have one if the user "removed" mid-download.
        if ent:
            try:
                Path(ent.part_file_path).unlink(missing_ok=True)
            except OSError:
                pass
        with self._db_lock:
            self._conn.execute("DELETE FROM downloads WHERE id = ?", (eid,))
            self._conn.commit()
        self.list_changed.emit()

    def clear_finished(self) -> None:
        """Drop all completed/failed/cancelled rows from the list."""
        with self._db_lock:
            self._conn.execute(
                "DELETE FROM downloads WHERE status IN (?, ?, ?)",
                TERMINAL_STATUSES,
            )
            self._conn.commit()
        self.list_changed.emit()

    # ── Worker lifecycle ────────────────────────────────────────────

    def _kick_next(self) -> None:
        """Start workers up to MAX_CONCURRENT for any queued rows."""
        if len(self._workers) >= MAX_CONCURRENT:
            return
        with self._db_lock:
            cur = self._conn.execute(
                "SELECT * FROM downloads WHERE status = ? "
                "ORDER BY created_at ASC LIMIT ?",
                (STATUS_QUEUED, MAX_CONCURRENT - len(self._workers)),
            )
            rows = cur.fetchall()
        for row in rows:
            ent = _row_to_entity(row)
            self._spawn_worker(ent)

    def _spawn_worker(self, ent: DownloadEntity) -> None:
        if ent.id in self._workers:
            return
        self._update_status(ent.id, STATUS_DOWNLOADING, "")

        thread = QThread(self)
        worker = _DownloadWorker(
            ent,
            base_url=self._client.base_url,
            headers=self._client.headers,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_worker_progress)
        worker.finished.connect(self._on_worker_finished)
        worker.finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        self._workers[ent.id] = (thread, worker)
        # Emit list_changed AFTER putting the worker in the dict so the
        # view sees the row in DOWNLOADING state with controls available.
        self.list_changed.emit()
        self.status_changed.emit(ent.id, STATUS_DOWNLOADING)
        thread.start()

    # ── Worker signal handlers (GUI thread) ─────────────────────────

    def _on_worker_progress(self, eid: str, downloaded: int, total: int) -> None:
        # Persist + relay.  Cheap UPDATE — sqlite handles the locking.
        with self._db_lock:
            self._conn.execute(
                "UPDATE downloads SET downloaded_bytes = ?, "
                "total_bytes = MAX(total_bytes, ?), updated_at = ? "
                "WHERE id = ?",
                (downloaded, total, time.time(), eid),
            )
            self._conn.commit()
        self.progress.emit(eid, downloaded, total)

    def _on_worker_finished(
        self, eid: str, final_status: str, error_message: str
    ) -> None:
        # Drop worker bookkeeping first so _kick_next sees a free slot.
        self._workers.pop(eid, None)
        self._update_status(eid, final_status, error_message)
        self.list_changed.emit()
        self.status_changed.emit(eid, final_status)
        if final_status == STATUS_COMPLETED:
            self.completed.emit(eid)
        # Drain any remaining queued rows.
        self._kick_next()

    # ── Helpers ─────────────────────────────────────────────────────

    def _update_status(self, eid: str, status: str, error_message: str) -> None:
        with self._db_lock:
            self._conn.execute(
                "UPDATE downloads SET status = ?, error_message = ?, "
                "updated_at = ? WHERE id = ?",
                (status, error_message, time.time(), eid),
            )
            self._conn.commit()
