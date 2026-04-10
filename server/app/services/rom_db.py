"""SQLite storage for ROM catalog.

DB file lives at save_dir/roms.db alongside metadata.db.
Provides fast startup by loading from DB instead of scanning the filesystem
every time. The filesystem is only scanned during explicit rescan or the
periodic background job.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Optional

_conn: Optional[sqlite3.Connection] = None
_current_db_path: Optional[Path] = None
_lock = threading.Lock()


_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS roms (
    title_id    TEXT PRIMARY KEY,
    system      TEXT NOT NULL DEFAULT '',
    name        TEXT NOT NULL DEFAULT '',
    filename    TEXT NOT NULL DEFAULT '',
    path        TEXT NOT NULL DEFAULT '',
    size        INTEGER NOT NULL DEFAULT 0,
    crc32       TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT ''
)
"""

_CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_roms_system ON roms(system)
"""


def init_db(save_dir: Path) -> None:
    global _conn, _current_db_path
    db_path = save_dir / "roms.db"
    if _conn is not None and _current_db_path == db_path:
        return
    if _conn is not None:
        _conn.close()
    _conn = sqlite3.connect(str(db_path), check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute(_CREATE_TABLE_SQL)
    _conn.execute(_CREATE_INDEX_SQL)
    _conn.commit()
    _current_db_path = db_path


def _get() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        from app.config import settings

        init_db(settings.save_dir)
    else:
        try:
            from app.config import settings

            expected = settings.save_dir / "roms.db"
            if _current_db_path != expected:
                init_db(settings.save_dir)
        except ImportError:
            pass
    return _conn


def upsert(entries: list[dict]) -> int:
    conn = _get()
    with _lock:
        conn.execute("DELETE FROM roms")
        conn.executemany(
            """
            INSERT INTO roms (title_id, system, name, filename, path, size, crc32, source)
            VALUES (:title_id, :system, :name, :filename, :path, :size, :crc32, :source)
            """,
            entries,
        )
        conn.commit()
    return len(entries)


def get(title_id: str) -> Optional[dict]:
    conn = _get()
    row = conn.execute("SELECT * FROM roms WHERE title_id = ?", (title_id,)).fetchone()
    return dict(row) if row is not None else None


def list_all() -> list[dict]:
    conn = _get()
    rows = conn.execute("SELECT * FROM roms ORDER BY title_id").fetchall()
    return [dict(r) for r in rows]


def list_by_system(system: str) -> list[dict]:
    conn = _get()
    rows = conn.execute(
        "SELECT * FROM roms WHERE system = ? ORDER BY title_id", (system,)
    ).fetchall()
    return [dict(r) for r in rows]


def systems() -> list[str]:
    conn = _get()
    rows = conn.execute(
        "SELECT DISTINCT system FROM roms WHERE system != '' ORDER BY system"
    ).fetchall()
    return [r["system"] for r in rows]


def stats() -> dict[str, int]:
    conn = _get()
    rows = conn.execute(
        "SELECT system, COUNT(*) as cnt FROM roms WHERE system != '' GROUP BY system ORDER BY system"
    ).fetchall()
    return {r["system"]: r["cnt"] for r in rows}


def count() -> int:
    conn = _get()
    row = conn.execute("SELECT COUNT(*) as cnt FROM roms").fetchone()
    return row["cnt"] if row else 0


def delete(title_id: str) -> None:
    conn = _get()
    with _lock:
        conn.execute("DELETE FROM roms WHERE title_id = ?", (title_id,))
        conn.commit()
