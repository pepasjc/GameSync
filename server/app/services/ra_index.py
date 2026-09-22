"""RetroAchievements index for the ROM catalog.

Answers "does this ROM earn achievements?" for every catalog entry, so the
clients can show an RA badge without each of them hashing the library
itself.

RA identifies a game by the ROM's exact MD5 under its own per-system rules
(:mod:`shared.ra_hash`), which means every file has to be read once.  That
happens here, in a background pass after the catalog scan, never during it:
a scan stays as fast as it is today and the badges fill in behind it.  The
result is cached in ``roms.db`` keyed by *path + size + mtime*, so a rescan
— or a server restart — costs nothing, and a ROM that is replaced on disk is
re-hashed because its size or mtime moved.

Only cartridge-shaped systems are covered.  Disc systems need a CD image
parser to reach the boot executable, which ``shared.ra_hash`` deliberately
does not do, so they are skipped and simply never carry a badge.

The hash library comes from :mod:`shared.ra_api`.  With ``SYNC_RA_API_KEY``
set it carries achievement counts, which is what ``achievements > 0`` — a
game you can actually earn something in, as opposed to a hash RA merely
recognises — is read from.  Without a key the counts are unknown and stored
as ``-1``; clients treat that as "registered, count unknown".
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

from app.services import rom_db

# rom_id puts the repo root on sys.path for us, so `shared` imports plainly.
from app.services import rom_id  # noqa: F401
from shared.ra_api import RaAuthError, RaLibrary, fetch_library
from shared.ra_hash import ra_console_ids, ra_hash_file, ra_hash_supported

logger = logging.getLogger(__name__)

# Stored in `achievements` when the count could not be determined (no API
# key).  Distinct from 0, which positively means "registered, no set yet".
ACHIEVEMENTS_UNKNOWN = -1

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ra_roms (
    path         TEXT PRIMARY KEY,
    size         INTEGER NOT NULL DEFAULT 0,
    mtime        REAL    NOT NULL DEFAULT 0,
    md5          TEXT    NOT NULL DEFAULT '',
    game_id      INTEGER NOT NULL DEFAULT 0,
    achievements INTEGER NOT NULL DEFAULT 0,
    title        TEXT    NOT NULL DEFAULT '',
    checked_at   REAL    NOT NULL DEFAULT 0
)
"""

_lock = threading.Lock()
_running = threading.Event()


def _conn():
    conn = rom_db.connection()
    conn.execute(_CREATE_TABLE_SQL)
    return conn


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def lookup(paths: Iterable[str]) -> dict[str, dict]:
    """``{path: {...}}`` for the paths RA recognises.  Unknown paths are absent.

    Only rows with a game id are returned: a ROM that was hashed and found
    to be unknown to RA is cached too (so it is not hashed again), but has
    nothing to say to a client.
    """
    wanted = [p for p in paths if p]
    if not wanted:
        return {}
    conn = _conn()
    out: dict[str, dict] = {}
    # SQLite caps variables per statement; chunk so a 5000-ROM catalog works.
    for i in range(0, len(wanted), 500):
        chunk = wanted[i : i + 500]
        placeholders = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"SELECT path, md5, game_id, achievements, title "
            f"FROM ra_roms WHERE game_id != 0 AND path IN ({placeholders})",
            chunk,
        ).fetchall()
        for row in rows:
            out[row["path"]] = {
                "ra_game_id": row["game_id"],
                "ra_achievements": row["achievements"],
                "ra_title": row["title"],
                "ra_hash": row["md5"],
            }
    return out


def annotate(entries: list, payloads: list[dict]) -> None:
    """Add the ``ra_*`` fields to already-serialised catalog payloads.

    ``entries`` and ``payloads`` are parallel: ``payloads[i]`` is
    ``entries[i].to_dict()``.  Silent no-op when nothing is indexed yet, so
    a catalog served before the first pass finishes is still valid — just
    unbadged.
    """
    if not entries:
        return
    try:
        found = lookup([getattr(e, "path", "") for e in entries])
    except Exception:  # noqa: BLE001 - a broken index must not break /roms
        logger.exception("[ra_index] lookup failed")
        return
    if not found:
        return
    for entry, payload in zip(entries, payloads):
        data = found.get(getattr(entry, "path", ""))
        if data:
            payload.update(data)


def stats() -> dict[str, int]:
    conn = _conn()
    row = conn.execute(
        "SELECT COUNT(*) AS hashed,"
        "       SUM(game_id != 0) AS known,"
        "       SUM(achievements > 0) AS playable "
        "FROM ra_roms"
    ).fetchone()
    return {
        "hashed": row["hashed"] or 0,
        "known": row["known"] or 0,
        "playable": row["playable"] or 0,
        "indexing": _running.is_set(),
    }


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------


def _cached_paths(conn) -> dict[str, tuple[int, float]]:
    rows = conn.execute("SELECT path, size, mtime FROM ra_roms").fetchall()
    return {r["path"]: (r["size"], r["mtime"]) for r in rows}


def _needs_hash(entry, cached: dict[str, tuple[int, float]]) -> Optional[tuple[int, float]]:
    """``(size, mtime)`` when this entry has to be hashed, else None."""
    path = getattr(entry, "path", "")
    if not path or getattr(entry, "is_bundle", False):
        # A bundle is a folder or a zip of many files; RA hashes a single
        # ROM, so there is nothing well-defined to hash here.
        return None
    if not ra_hash_supported(getattr(entry, "system", "")):
        return None
    try:
        stat = Path(path).stat()
    except OSError:
        return None
    previous = cached.get(path)
    if previous is not None and previous[0] == stat.st_size and abs(previous[1] - stat.st_mtime) < 1:
        return None
    return stat.st_size, stat.st_mtime


class _Libraries:
    """Lazy per-console hash libraries, fetched once per indexing pass."""

    def __init__(self, cache_dir: Path, api_key: str, username: str):
        self.cache_dir = cache_dir
        self.api_key = api_key
        self.username = username
        self._cache: dict[int, Optional[RaLibrary]] = {}

    def get(self, console_id: int) -> Optional[RaLibrary]:
        if console_id not in self._cache:
            try:
                self._cache[console_id] = fetch_library(
                    console_id,
                    cache_dir=self.cache_dir,
                    api_key=self.api_key,
                    username=self.username,
                )
            except RaAuthError:
                logger.error(
                    "[ra_index] RetroAchievements rejected SYNC_RA_API_KEY; "
                    "falling back to the public endpoints (no achievement counts)"
                )
                self.api_key = ""
                self._cache.pop(console_id, None)
                return self.get(console_id)
            except Exception as exc:  # noqa: BLE001 - offline is not fatal
                logger.warning(
                    "[ra_index] could not load RA library for console %d: %s",
                    console_id, exc,
                )
                self._cache[console_id] = None
        return self._cache[console_id]

    def find(self, md5: str, system: str) -> tuple[int, int, str]:
        """``(game_id, achievements, title)``; game_id 0 when RA does not know it."""
        for console_id in ra_console_ids(system):
            library = self.get(console_id)
            if library is None:
                continue
            game_id, title = library.lookup(md5)
            if game_id:
                count = library.achievement_count(game_id)
                return game_id, ACHIEVEMENTS_UNKNOWN if count is None else count, title or ""
        return 0, 0, ""


def refresh(
    entries: list,
    cache_dir: Path,
    api_key: str = "",
    username: str = "",
    should_stop: Optional[Callable[[], bool]] = None,
    progress_every: int = 200,
) -> dict[str, int]:
    """Hash and look up every catalog entry that isn't cached yet.

    Safe to call on every scan: entries whose path, size and mtime are
    unchanged are skipped without touching the disk.
    """
    if _running.is_set():
        logger.debug("[ra_index] refresh already running, skipping")
        return {"hashed": 0, "known": 0, "skipped": 0}
    _running.set()
    try:
        return _refresh(entries, cache_dir, api_key, username, should_stop, progress_every)
    finally:
        _running.clear()


def _refresh(entries, cache_dir, api_key, username, should_stop, progress_every) -> dict[str, int]:
    conn = _conn()
    cached = _cached_paths(conn)

    todo = []
    for entry in entries:
        stat = _needs_hash(entry, cached)
        if stat is not None:
            todo.append((entry, stat))

    # Drop rows for ROMs that left the catalog, so the table can't grow
    # without bound across renames and deletions.
    live = {getattr(e, "path", "") for e in entries}
    gone = [p for p in cached if p not in live]
    if gone:
        with _lock:
            for i in range(0, len(gone), 500):
                chunk = gone[i : i + 500]
                conn.execute(
                    f"DELETE FROM ra_roms WHERE path IN ({','.join('?' * len(chunk))})",
                    chunk,
                )
            conn.commit()

    if not todo:
        return {"hashed": 0, "known": 0, "skipped": len(cached), "removed": len(gone)}

    logger.info("[ra_index] hashing %d ROM(s) for RetroAchievements", len(todo))
    libraries = _Libraries(cache_dir, api_key, username)
    hashed = known = 0
    batch: list[tuple] = []
    now = time.time()

    for index, (entry, (size, mtime)) in enumerate(todo, 1):
        if should_stop and should_stop():
            logger.info("[ra_index] stopping early at %d/%d", index, len(todo))
            break
        result = ra_hash_file(entry.path, entry.system)
        if not result.ok:
            # Cache the failure too: an unreadable or malformed ROM should
            # not be retried on every single scan.
            batch.append((entry.path, size, mtime, "", 0, 0, "", now))
            continue
        game_id, achievements, title = libraries.find(result.md5, entry.system)
        batch.append((entry.path, size, mtime, result.md5, game_id, achievements, title, now))
        hashed += 1
        known += bool(game_id)
        if len(batch) >= progress_every:
            _flush(conn, batch)
            batch = []
            logger.info("[ra_index] %d/%d hashed, %d known to RA", index, len(todo), known)

    _flush(conn, batch)
    logger.info("[ra_index] done: %d hashed, %d known to RetroAchievements", hashed, known)
    return {"hashed": hashed, "known": known, "skipped": len(cached), "removed": len(gone)}


def _flush(conn, batch: list[tuple]) -> None:
    if not batch:
        return
    with _lock:
        conn.executemany(
            "INSERT OR REPLACE INTO ra_roms "
            "(path, size, mtime, md5, game_id, achievements, title, checked_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            batch,
        )
        conn.commit()


def clear() -> int:
    """Drop every cached hash so the next pass re-reads the library."""
    conn = _conn()
    with _lock:
        count = conn.execute("SELECT COUNT(*) AS c FROM ra_roms").fetchone()["c"]
        conn.execute("DELETE FROM ra_roms")
        conn.commit()
    return count
