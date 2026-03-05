"""Filesystem storage for save data, organized per console.

Saves are stored as:
  saves/<title_id>/<console_id>/
    metadata.json
    current/          -- extracted save files
    history/
      <timestamp>/    -- previous versions

Each console gets its own folder under the title directory, so multiple
consoles can independently maintain saves for the same game without conflict.
When no console_id is specified, the most recently updated console slot is used.
"""

from __future__ import annotations

import json
import hashlib
import shutil
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.models.save import SaveBundle, SaveMetadata

_DEFAULT_CONSOLE = "_default"
_PSP_CONSOLE = "psp"


def _title_dir(title_id: str) -> Path:
    return settings.save_dir / title_id


def _console_dir(title_id: str, console_id: str) -> Path:
    cid = console_id.strip() if console_id.strip() else _DEFAULT_CONSOLE
    return _title_dir(title_id) / cid


def _current_dir(title_id: str, console_id: str) -> Path:
    return _console_dir(title_id, console_id) / "current"


def _history_dir(title_id: str, console_id: str) -> Path:
    return _console_dir(title_id, console_id) / "history"


def _metadata_path(title_id: str, console_id: str) -> Path:
    return _console_dir(title_id, console_id) / "metadata.json"


def get_latest_console(title_id: str) -> str | None:
    """Return the console_id with the most recently updated save for a title."""
    title_dir = _title_dir(title_id)
    if not title_dir.exists():
        return None

    latest_mtime = None
    latest_console = None
    for entry in title_dir.iterdir():
        if not entry.is_dir():
            continue
        meta_path = entry / "metadata.json"
        if meta_path.exists():
            mtime = meta_path.stat().st_mtime
            if latest_mtime is None or mtime > latest_mtime:
                latest_mtime = mtime
                latest_console = entry.name

    return latest_console


def title_exists(title_id: str, console_id: str = "") -> bool:
    """Check if a save exists. If console_id given, check that specific slot."""
    if console_id:
        return _metadata_path(title_id, console_id).exists()
    # Check if any console slot exists for this title
    return get_latest_console(title_id) is not None


def list_consoles(title_id: str) -> list[str]:
    """List all console IDs that have saves for a title."""
    title_dir = _title_dir(title_id)
    if not title_dir.exists():
        return []
    return [
        e.name for e in sorted(title_dir.iterdir())
        if e.is_dir() and (e / "metadata.json").exists()
    ]


def list_titles() -> list[dict]:
    """Return metadata for all stored titles (most recently updated console per title)."""
    results = []
    save_dir = settings.save_dir
    if not save_dir.exists():
        return results

    for title_entry in sorted(save_dir.iterdir()):
        if not title_entry.is_dir():
            continue
        # Find the most recently updated console slot
        latest = get_latest_console(title_entry.name)
        if latest:
            meta_path = _metadata_path(title_entry.name, latest)
            if meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                results.append(meta)

    return results


def get_metadata(title_id: str, console_id: str = "") -> SaveMetadata | None:
    """Load metadata for a title/console. Falls back to most-recent console if not specified.

    If console_id is given but that slot doesn't exist, also tries the shared "psp" slot
    so that PSP saves stored under the canonical slot are visible to any PSP/Vita client.
    """
    if not console_id:
        console_id = get_latest_console(title_id) or ""
        if not console_id:
            return None

    path = _metadata_path(title_id, console_id)
    if not path.exists():
        # Fall back to the shared PSP slot, then to most-recently-updated slot
        psp_path = _metadata_path(title_id, _PSP_CONSOLE)
        if psp_path.exists():
            path = psp_path
        else:
            fallback = get_latest_console(title_id) or ""
            if not fallback:
                return None
            path = _metadata_path(title_id, fallback)
            if not path.exists():
                return None

    data = json.loads(path.read_text(encoding="utf-8"))
    return SaveMetadata(**data)


def store_save(
    bundle: SaveBundle, source: str = "3ds", console_id: str = ""
) -> SaveMetadata:
    """Store a save bundle under the appropriate console slot, archiving existing save."""
    title_id = bundle.effective_title_id
    cid = console_id.strip() if console_id.strip() else _DEFAULT_CONSOLE

    current = _current_dir(title_id, cid)
    history = _history_dir(title_id, cid)

    # Archive existing save to history
    if current.exists():
        old_meta = get_metadata(title_id, cid)
        if old_meta:
            ts = old_meta.last_sync.replace(":", "_").replace("+", "_")
            archive_dir = history / ts
            archive_dir.mkdir(parents=True, exist_ok=True)
            for item in current.iterdir():
                if item.is_file():
                    shutil.copy2(item, archive_dir / item.name)
                elif item.is_dir():
                    shutil.copytree(item, archive_dir / item.name, dirs_exist_ok=True)

            _prune_history(title_id, cid)

        shutil.rmtree(current)

    # Write new save files
    current.mkdir(parents=True, exist_ok=True)
    for f in bundle.files:
        file_path = current / f.path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(f.data)

    # Compute bundle hash
    all_data = b"".join(f.data for f in bundle.files)
    bundle_hash = hashlib.sha256(all_data).hexdigest()

    now = datetime.now(timezone.utc).isoformat()
    meta = SaveMetadata(
        title_id=title_id,
        name=title_id,
        last_sync=now,
        last_sync_source=source,
        save_hash=bundle_hash,
        save_size=bundle.total_size,
        file_count=len(bundle.files),
        client_timestamp=bundle.timestamp,
        server_timestamp=now,
        console_id=cid,
    )

    meta_path = _metadata_path(title_id, cid)
    meta_path.write_text(json.dumps(meta.to_dict(), indent=2), encoding="utf-8")

    return meta


def load_save_files(title_id: str, console_id: str = "") -> list[tuple[str, bytes]] | None:
    """Load all save files. Falls back to most-recent console if not specified.

    If console_id is given but that slot doesn't exist, also tries the shared "psp" slot,
    then the most-recently-updated slot.
    """
    if not console_id:
        console_id = get_latest_console(title_id) or ""
        if not console_id:
            return None

    current = _current_dir(title_id, console_id)
    if not current.exists():
        # Fall back to shared PSP slot, then to most-recently-updated slot
        psp_current = _current_dir(title_id, _PSP_CONSOLE)
        if psp_current.exists():
            current = psp_current
        else:
            fallback = get_latest_console(title_id) or ""
            if not fallback:
                return None
            current = _current_dir(title_id, fallback)
            if not current.exists():
                return None

    files = []
    for file_path in sorted(current.rglob("*")):
        if file_path.is_file():
            rel_path = file_path.relative_to(current).as_posix()
            files.append((rel_path, file_path.read_bytes()))
    return files


def _prune_history(title_id: str, console_id: str) -> None:
    """Keep only the most recent N history versions for a console slot."""
    history = _history_dir(title_id, console_id)
    if not history.exists():
        return

    versions = sorted(history.iterdir(), key=lambda p: p.name)
    while len(versions) > settings.max_history_versions:
        oldest = versions.pop(0)
        shutil.rmtree(oldest)


def list_history(title_id: str, console_id: str = "") -> list[dict]:
    """List all history versions for a title/console."""
    if not console_id:
        console_id = get_latest_console(title_id) or ""
        if not console_id:
            return []

    history = _history_dir(title_id, console_id)
    if not history.exists():
        return []

    versions = []
    for ts_dir in sorted(history.iterdir(), reverse=True):
        if not ts_dir.is_dir():
            continue

        ts_str = ts_dir.name.replace("_", ":")
        if ts_str.endswith(":00:00"):
            ts_str = ts_str[:-6] + "+00:00"
        try:
            dt = datetime.fromisoformat(ts_str)
            unix_ts = int(dt.timestamp())
        except Exception as e:
            print(f"Failed to parse timestamp '{ts_str}': {e}")
            unix_ts = 0

        total_size = 0
        file_count = 0
        for f in ts_dir.rglob("*"):
            if f.is_file():
                total_size += f.stat().st_size
                file_count += 1

        versions.append(
            {
                "timestamp": unix_ts,
                "display": ts_str[:19].replace(":", " "),
                "size": total_size,
                "file_count": file_count,
            }
        )

    return versions


def load_history_version_by_unix_ts(
    title_id: str, unix_timestamp: int, console_id: str = ""
) -> list[tuple[str, bytes]] | None:
    """Load save files from a specific history version by Unix timestamp."""
    if not console_id:
        console_id = get_latest_console(title_id) or ""
        if not console_id:
            return None

    history = _history_dir(title_id, console_id)
    if not history.exists():
        return None

    dt = datetime.fromtimestamp(unix_timestamp, tz=timezone.utc)
    iso_ts = dt.isoformat()
    ts_dir_name = iso_ts.replace(":", "_").replace("+", "_")

    history_path = history / ts_dir_name
    if not history_path.exists():
        return None

    files = []
    for file_path in sorted(history_path.rglob("*")):
        if file_path.is_file():
            rel_path = file_path.relative_to(history_path).as_posix()
            files.append((rel_path, file_path.read_bytes()))

    return files
