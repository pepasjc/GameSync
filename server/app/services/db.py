"""SQLite metadata storage for save data.

The DB file lives at save_dir/metadata.db alongside the save folders.
This module replaces per-title metadata.json files with a single indexed DB.

File layout on disk remains unchanged:
  saves/<title_id>/current/   -- extracted save files
  saves/<title_id>/history/   -- previous versions
  saves/metadata.db           -- this module's responsibility
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

_conn: Optional[sqlite3.Connection] = None
_current_db_path: Optional[Path] = None


_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS saves (
    title_id         TEXT PRIMARY KEY,
    name             TEXT NOT NULL DEFAULT '',
    system           TEXT NOT NULL DEFAULT '',
    last_sync        TEXT NOT NULL DEFAULT '',
    last_sync_source TEXT NOT NULL DEFAULT '',
    save_hash        TEXT NOT NULL DEFAULT '',
    save_size        INTEGER NOT NULL DEFAULT 0,
    file_count       INTEGER NOT NULL DEFAULT 0,
    client_timestamp INTEGER NOT NULL DEFAULT 0,
    server_timestamp TEXT NOT NULL DEFAULT '',
    console_id       TEXT NOT NULL DEFAULT '',
    platform         TEXT NOT NULL DEFAULT ''
)
"""


def init_db(save_dir: Path) -> None:
    """Initialise the SQLite DB at save_dir/metadata.db.

    Called from the FastAPI lifespan hook. Safe to call multiple times;
    reinitialises if save_dir changes (important for test isolation).
    """
    global _conn, _current_db_path
    db_path = save_dir / "metadata.db"
    if _conn is not None and _current_db_path == db_path:
        return  # Already initialised at this path
    if _conn is not None:
        _conn.close()
    _conn = sqlite3.connect(str(db_path), check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute(_CREATE_TABLE_SQL)
    _conn.commit()
    _current_db_path = db_path


def _get() -> sqlite3.Connection:
    """Return active connection, auto-initialising from settings if needed.

    Two cases:
    - _conn is None: auto-init using settings.save_dir (handles tests where
      the FastAPI lifespan doesn't run).
    - _conn is set: check if settings.save_dir changed (test isolation between
      tests that each patch settings.save_dir to a fresh temp dir).
      The import of app.config is guarded so this works even when pydantic_settings
      is unavailable (e.g. the migration script running outside the venv).
    """
    global _conn
    if _conn is None:
        from app.config import settings
        init_db(settings.save_dir)
    else:
        try:
            from app.config import settings
            expected = settings.save_dir / "metadata.db"
            if _current_db_path != expected:
                init_db(settings.save_dir)
        except ImportError:
            pass  # running outside full app environment; use existing connection
    return _conn


def upsert(data: dict) -> None:
    """Insert or replace a save metadata row."""
    conn = _get()
    conn.execute(
        """
        INSERT INTO saves (
            title_id, name, system, last_sync, last_sync_source,
            save_hash, save_size, file_count, client_timestamp,
            server_timestamp, console_id, platform
        ) VALUES (
            :title_id, :name, :system, :last_sync, :last_sync_source,
            :save_hash, :save_size, :file_count, :client_timestamp,
            :server_timestamp, :console_id, :platform
        )
        ON CONFLICT(title_id) DO UPDATE SET
            name=excluded.name,
            system=excluded.system,
            last_sync=excluded.last_sync,
            last_sync_source=excluded.last_sync_source,
            save_hash=excluded.save_hash,
            save_size=excluded.save_size,
            file_count=excluded.file_count,
            client_timestamp=excluded.client_timestamp,
            server_timestamp=excluded.server_timestamp,
            console_id=excluded.console_id,
            platform=excluded.platform
        """,
        {
            "title_id": data.get("title_id", ""),
            "name": data.get("name", ""),
            "system": data.get("system", ""),
            "last_sync": data.get("last_sync", ""),
            "last_sync_source": data.get("last_sync_source", ""),
            "save_hash": data.get("save_hash", ""),
            "save_size": data.get("save_size", 0),
            "file_count": data.get("file_count", 0),
            "client_timestamp": data.get("client_timestamp", 0),
            "server_timestamp": data.get("server_timestamp", ""),
            "console_id": data.get("console_id", ""),
            "platform": data.get("platform", ""),
        },
    )
    conn.commit()


def get(title_id: str) -> Optional[dict]:
    """Return a row as a dict, or None if not found."""
    conn = _get()
    row = conn.execute(
        "SELECT * FROM saves WHERE title_id = ?", (title_id,)
    ).fetchone()
    return dict(row) if row is not None else None


def list_all() -> list[dict]:
    """Return all rows ordered by title_id."""
    conn = _get()
    rows = conn.execute("SELECT * FROM saves ORDER BY title_id").fetchall()
    return [dict(r) for r in rows]


def delete(title_id: str) -> None:
    """Delete a metadata row."""
    conn = _get()
    conn.execute("DELETE FROM saves WHERE title_id = ?", (title_id,))
    conn.commit()


def exists(title_id: str) -> bool:
    """Return True if a row exists for this title_id."""
    conn = _get()
    row = conn.execute(
        "SELECT 1 FROM saves WHERE title_id = ?", (title_id,)
    ).fetchone()
    return row is not None


def update_name_and_platform(title_id: str, name: str, platform: str) -> None:
    """Update only the name and platform columns."""
    conn = _get()
    conn.execute(
        "UPDATE saves SET name=?, platform=? WHERE title_id=?",
        (name, platform, title_id),
    )
    conn.commit()
