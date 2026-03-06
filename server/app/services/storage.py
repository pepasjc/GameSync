"""Filesystem storage for save data.

Saves are stored as:
  saves/<title_id>/
    metadata.json
    current/          -- extracted save files
    history/
      <timestamp>/    -- previous versions

All clients (3DS, DS, PSP, Vita) share the same flat slot per title ID.
The console_id field in metadata.json is informational only and does not
affect the storage path.
"""

from __future__ import annotations

import json
import hashlib
import shutil
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.models.save import SaveBundle, SaveMetadata
from app.services import game_names


def _title_dir(title_id: str) -> Path:
    return settings.save_dir / title_id


def _current_dir(title_id: str) -> Path:
    return _title_dir(title_id) / "current"


def _history_dir(title_id: str) -> Path:
    return _title_dir(title_id) / "history"


def _metadata_path(title_id: str) -> Path:
    return _title_dir(title_id) / "metadata.json"


def title_exists(title_id: str, console_id: str = "") -> bool:
    """Check if a save exists for a title. console_id is ignored (flat layout)."""
    return _metadata_path(title_id).exists()


def list_consoles(title_id: str) -> list[str]:
    """Return the console_id recorded in metadata, if any."""
    meta = get_metadata(title_id)
    if meta is None:
        return []
    return [meta.console_id] if meta.console_id else []


def update_metadata_name(title_id: str, name: str, platform: str) -> None:
    """Update only the name and platform fields of stored metadata."""
    path = _metadata_path(title_id)
    if not path.exists():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    data["name"] = name
    data["platform"] = platform
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def list_titles() -> list[dict]:
    """Return metadata for all stored titles."""
    results = []
    save_dir = settings.save_dir
    if not save_dir.exists():
        return results

    for entry in sorted(save_dir.iterdir()):
        meta_path = entry / "metadata.json"
        if entry.is_dir() and meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            results.append(meta)

    return results


def get_metadata(title_id: str, console_id: str = "") -> SaveMetadata | None:
    """Load metadata for a title. console_id is ignored (flat layout)."""
    path = _metadata_path(title_id)
    if not path.exists():
        return None

    data = json.loads(path.read_text(encoding="utf-8"))
    return SaveMetadata(**data)


def store_save(
    bundle: SaveBundle, source: str = "3ds", console_id: str = "", game_code: str = ""
) -> SaveMetadata:
    """Store a save bundle to disk, archiving any existing save to history."""
    title_id = bundle.effective_title_id
    current = _current_dir(title_id)
    history = _history_dir(title_id)

    # Archive existing save to history
    if current.exists():
        old_meta = get_metadata(title_id)
        if old_meta:
            ts = old_meta.last_sync.replace(":", "_").replace("+", "_")
            archive_dir = history / ts
            archive_dir.mkdir(parents=True, exist_ok=True)
            for item in current.iterdir():
                if item.is_file():
                    shutil.copy2(item, archive_dir / item.name)
                elif item.is_dir():
                    shutil.copytree(item, archive_dir / item.name, dirs_exist_ok=True)

            _prune_history(title_id)

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

    # Look up game name and platform from local DB.
    # For 3DS titles the hex title ID alone can't resolve a name, so fall back
    # to the product code (e.g. "CTR-P-A22J") when the client provides one.
    game_name, platform = game_names.lookup_name_and_platform(title_id)
    if game_name == title_id and game_code:
        typed = game_names.lookup_names_typed([game_code])
        if game_code in typed:
            game_name, platform = typed[game_code]

    now = datetime.now(timezone.utc).isoformat()
    meta = SaveMetadata(
        title_id=title_id,
        name=game_name,
        last_sync=now,
        last_sync_source=source,
        save_hash=bundle_hash,
        save_size=bundle.total_size,
        file_count=len(bundle.files),
        client_timestamp=bundle.timestamp,
        server_timestamp=now,
        console_id=console_id,
        platform=platform,
    )

    meta_path = _metadata_path(title_id)
    meta_path.write_text(json.dumps(meta.to_dict(), indent=2), encoding="utf-8")

    return meta


def load_save_files(title_id: str, console_id: str = "") -> list[tuple[str, bytes]] | None:
    """Load all save files for a title. console_id is ignored (flat layout)."""
    current = _current_dir(title_id)
    if not current.exists():
        return None

    files = []
    for file_path in sorted(current.rglob("*")):
        if file_path.is_file():
            rel_path = file_path.relative_to(current).as_posix()
            files.append((rel_path, file_path.read_bytes()))
    return files


def _prune_history(title_id: str) -> None:
    """Keep only the most recent N history versions."""
    history = _history_dir(title_id)
    if not history.exists():
        return

    versions = sorted(history.iterdir(), key=lambda p: p.name)
    while len(versions) > settings.max_history_versions:
        oldest = versions.pop(0)
        shutil.rmtree(oldest)


def list_history(title_id: str, console_id: str = "") -> list[dict]:
    """List all history versions for a title. console_id is ignored (flat layout)."""
    history = _history_dir(title_id)
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
    """Load save files from a specific history version. console_id is ignored (flat layout)."""
    history = _history_dir(title_id)
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


def delete_save(title_id: str, console_id: str = "") -> None:
    """Delete a save (removes entire title folder). console_id is ignored (flat layout)."""
    title_dir = _title_dir(title_id)
    if title_dir.exists():
        shutil.rmtree(title_dir)
