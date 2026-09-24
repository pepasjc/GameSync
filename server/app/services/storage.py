"""Filesystem storage for save data.

Saves are stored as:
  saves/<title_id>/
    current/          -- extracted save files
    history/
      <timestamp>/    -- previous versions
  saves/metadata.db   -- SQLite metadata (replaces per-title metadata.json)

The JSON metadata.json files are retained as read-only backups after migration
(renamed to metadata.json.bak by migrate_to_sqlite.py). During the transition
period, storage.py falls back to reading JSON if a title is absent from the DB.

All clients (3DS, DS, PSP, Vita, emulators) share the same flat slot per title ID.
The console_id field in metadata is informational only and does not affect storage path.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.models.save import SaveBundle, SaveMetadata
from app.services import db, game_names
from app.services.ps1_cards import is_ps1_title_id, psp_visible_stats
from app.services.rom_id import parse_title_id as _parse_emulator_id

logger = logging.getLogger(__name__)

_TRACE_TITLE_IDS = {"BLJS10001GAME"}
_PS3_HASH_SKIP_NAMES = {"PARAM.SFO", "PARAM.PFD"}


def _trace_files(stage: str, title_id: str, files: list[tuple[str, bytes]]) -> None:
    if title_id not in _TRACE_TITLE_IDS:
        return
    details = ", ".join(
        f"{path}({len(data)}:{hashlib.sha256(data).hexdigest()})"
        for path, data in files
    )
    logger.info("ps3 trace %s %s files=[%s]", stage, title_id, details)


def _is_ps3_hash_ignored(path: str) -> bool:
    name = Path(path).name.upper()
    if name in _PS3_HASH_SKIP_NAMES:
        return True
    return Path(name).suffix.upper() == ".PNG"


def _ps3_visible_stats(files: list[tuple[str, bytes]]) -> tuple[str, int, int]:
    visible = sorted(
        ((path, data) for path, data in files if not _is_ps3_hash_ignored(path)),
        key=lambda item: item[0],
    )
    h = hashlib.sha256()
    total_size = 0

    for _path, data in visible:
        h.update(data)
        total_size += len(data)

    return h.hexdigest(), total_size, len(visible)


def comparable_files(title_id: str, files: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
    """Return the file set that should participate in sync comparison."""
    if is_ps1_title_id(title_id):
        from app.services.ps1_cards import psp_visible_files

        return psp_visible_files(files)
    if game_names.detect_platform(title_id) == "PS3":
        return [
            (path, data)
            for path, data in files
            if not _is_ps3_hash_ignored(path)
        ]
    return files


def _title_dir(title_id: str) -> Path:
    return settings.save_dir / title_id


def _current_dir(title_id: str) -> Path:
    return _title_dir(title_id) / "current"


def _history_dir(title_id: str) -> Path:
    return _title_dir(title_id) / "history"


def _metadata_path(title_id: str) -> Path:
    return _title_dir(title_id) / "metadata.json"


def _row_to_metadata(row: dict) -> SaveMetadata:
    return SaveMetadata(
        title_id=row.get("title_id", ""),
        name=row.get("name", ""),
        last_sync=row.get("last_sync", ""),
        last_sync_source=row.get("last_sync_source", ""),
        save_hash=row.get("save_hash", ""),
        save_size=row.get("save_size", 0),
        file_count=row.get("file_count", 0),
        client_timestamp=row.get("client_timestamp", 0),
        server_timestamp=row.get("server_timestamp", ""),
        console_id=row.get("console_id", ""),
        platform=row.get("platform", ""),
        system=row.get("system", ""),
    )


def _load_json_metadata(title_id: str) -> SaveMetadata | None:
    """Fallback: read legacy metadata.json for unmigrated saves."""
    path = _metadata_path(title_id)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("system", "")
    return SaveMetadata(**{k: data[k] for k in SaveMetadata.__dataclass_fields__ if k in data})


def title_exists(title_id: str, console_id: str = "") -> bool:
    """Check if a save exists for a title. console_id is ignored (flat layout)."""
    return db.exists(title_id) or _metadata_path(title_id).exists()


def list_consoles(title_id: str) -> list[str]:
    """Return the console_id recorded in metadata, if any."""
    meta = get_metadata(title_id)
    if meta is None:
        return []
    return [meta.console_id] if meta.console_id else []


def update_metadata_name(title_id: str, name: str, platform: str) -> None:
    """Update only the name and platform fields of stored metadata."""
    if db.exists(title_id):
        db.update_name_and_platform(title_id, name, platform)
    # Also update legacy JSON if present (for unmigrated saves)
    path = _metadata_path(title_id)
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        data["name"] = name
        data["platform"] = platform
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        # An in-place rewrite does not move the save dir's mtime.
        global _legacy_cache
        _legacy_cache = None


def list_titles() -> list[dict]:
    """Return metadata dicts for all stored titles."""
    # Start from SQLite
    db_rows = {r["title_id"]: r for r in db.list_all()}

    # Fallback: include any JSON-only saves not yet migrated to DB
    for meta in _legacy_json_metadata():
        tid = meta.get("title_id", "")
        if tid and tid not in db_rows:
            db_rows[tid] = dict(meta)

    return list(db_rows.values())


#: (save_dir, its mtime) -> the legacy metadata.json dicts found under it.
_legacy_cache: tuple | None = None


def _legacy_json_metadata() -> list[dict]:
    """Every legacy metadata.json under the save dir.

    Walking it costs a stat per title on every listing - over a thousand on
    the live server - to find files that stopped being written when the DB
    arrived. Cached until the save dir's own mtime moves, which it does
    whenever a title folder is added or removed.
    """
    global _legacy_cache
    save_dir = settings.save_dir
    try:
        stamp = save_dir.stat().st_mtime_ns
    except OSError:
        return []
    if _legacy_cache is not None and _legacy_cache[0] == (save_dir, stamp):
        return _legacy_cache[1]

    found = []
    for entry in sorted(save_dir.iterdir()):
        if not entry.is_dir():
            continue
        meta_path = entry / "metadata.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if meta.get("title_id", ""):
                    meta.setdefault("system", "")
                    found.append(meta)
            except json.JSONDecodeError as exc:
                logger.warning("Skipping malformed JSON metadata %s: %s", meta_path, exc)
            except Exception as exc:
                logger.warning("Unexpected error reading metadata %s: %s", meta_path, exc)
    _legacy_cache = ((save_dir, stamp), found)
    return found


def get_metadata(title_id: str, console_id: str = "") -> SaveMetadata | None:
    """Load metadata for a title. console_id is ignored (flat layout)."""
    row = db.get(title_id)
    if row is not None:
        return _row_to_metadata(row)
    # Fallback to legacy JSON for unmigrated saves
    return _load_json_metadata(title_id)


#: db_flags key recording that every PS3 row carries the current hash scheme
#: (PARAM.SFO, PARAM.PFD and *.PNG excluded - see _is_ps3_hash_ignored).
PS3_HASH_FLAG = "ps3_hash_scheme"
PS3_HASH_SCHEME = "2"
#: The DB path migrate_ps3_hashes() is known to have finished on. A path,
#: not a bool: tests (and a changed SYNC_SAVE_DIR) switch databases.
_ps3_hashes_current_for = None


def ps3_hashes_current() -> bool:
    """Has migrate_ps3_hashes() finished on this DB? Cached once true."""
    global _ps3_hashes_current_for
    db._get()
    path = db._current_db_path
    if _ps3_hashes_current_for == path:
        return True
    if db.get_flag(PS3_HASH_FLAG) == PS3_HASH_SCHEME:
        _ps3_hashes_current_for = path
        return True
    return False


def migrate_ps3_hashes() -> int:
    """Bring every PS3 row to the current hash scheme, once per DB.

    PS3 hashes were redefined to leave out PARAM.* and PNG files, and rows
    written before that still held the old value. That used to be handled
    by re-reading and re-hashing every PS3 save on every /titles and /sync
    request - 71 MB per call on the live server, for a result that only
    ever changes on upload, where store_save already hashes the new way.
    Returns how many rows changed.
    """
    global _ps3_hashes_current_for
    if ps3_hashes_current():
        return 0
    changed = 0
    for row in db.list_all():
        title_id = row.get("title_id", "")
        if not title_id or game_names.detect_platform(title_id) != "PS3":
            continue
        before = row.get("save_hash", "")
        refreshed = rebuild_metadata_from_current(title_id)
        if refreshed is not None and refreshed.save_hash != before:
            changed += 1
    db.set_flag(PS3_HASH_FLAG, PS3_HASH_SCHEME)
    _ps3_hashes_current_for = db._current_db_path
    return changed


def _for_sync(meta: SaveMetadata | None) -> SaveMetadata | None:
    """Until migrate_ps3_hashes() has run, a PS3 row may hold the old hash."""
    if meta is None or ps3_hashes_current():
        return meta
    if game_names.detect_platform(meta.title_id) == "PS3":
        refreshed = rebuild_metadata_from_current(meta.title_id)
        if refreshed is not None:
            return refreshed
    return meta


def get_metadata_for_sync(title_id: str, console_id: str = "") -> SaveMetadata | None:
    """Load metadata used for sync comparison."""
    return _for_sync(get_metadata(title_id, console_id))


def list_metadata_for_sync() -> list[SaveMetadata]:
    """Every title's sync metadata, from one DB read.

    What calling get_metadata_for_sync() per title returns, without a
    query per title on top of the one that listed them.
    """
    out = []
    for row in list_titles():
        tid = row.get("title_id", "")
        if not tid:
            continue
        if "save_hash" in row and "last_sync" in row:
            meta = _row_to_metadata(row)
        else:
            meta = get_metadata(tid)
        meta = _for_sync(meta)
        if meta is not None:
            out.append(meta)
    return out


def store_save(
    bundle: SaveBundle,
    source: str = "3ds",
    console_id: str = "",
    game_code: str = "",
    game_name_hint: str = "",
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
    _trace_files("store-write", title_id, [(f.path, f.data) for f in bundle.files])
    for f in bundle.files:
        file_path = current / f.path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(f.data)

    # Compute sync metadata hash. For PS1, preserve PSP/Vita-compatible hashing
    # over the PSP-visible file set so legacy clients continue to compare correctly.
    if is_ps1_title_id(title_id):
        bundle_hash, save_size, file_count = psp_visible_stats([(f.path, f.data) for f in bundle.files])
    elif game_names.detect_platform(title_id) == "PS3":
        bundle_hash, save_size, file_count = _ps3_visible_stats(
            [(f.path, f.data) for f in bundle.files]
        )
    else:
        all_data = b"".join(f.data for f in bundle.files)
        bundle_hash = hashlib.sha256(all_data).hexdigest()
        save_size = bundle.total_size
        file_count = len(bundle.files)

    # Look up game name and platform from local DB.
    game_name, platform = game_names.lookup_name_and_platform(title_id)
    if game_name == title_id and game_code:
        typed = game_names.lookup_names_typed([game_code])
        if game_code in typed:
            game_name, platform = typed[game_code]

    # Client-supplied name, used only when nothing local resolved it.  Wii U
    # saves are keyed by a 16-hex title id whose low word is not the product
    # code, so no DAT can name them — the client reads the title's meta.xml
    # and passes the name it found.
    if game_name == title_id and game_name_hint.strip():
        game_name = game_name_hint.strip()

    # For emulator saves with no name found, derive readable name from slug
    if game_name == title_id:
        parsed = _parse_emulator_id(title_id)
        if parsed:
            _, slug = parsed
            game_name = slug.replace("_", " ").title()

    # Determine detailed system code
    parsed = _parse_emulator_id(title_id)
    system = parsed[0] if parsed else platform

    now = datetime.now(timezone.utc).isoformat()
    meta = SaveMetadata(
        title_id=title_id,
        name=game_name,
        last_sync=now,
        last_sync_source=source,
        save_hash=bundle_hash,
        save_size=save_size,
        file_count=file_count,
        client_timestamp=bundle.timestamp,
        server_timestamp=now,
        console_id=console_id,
        platform=platform,
        system=system,
    )

    db.upsert(meta.to_dict())
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
    _trace_files("load-current", title_id, files)
    return files


def rebuild_metadata_from_current(title_id: str, source: str | None = None) -> SaveMetadata | None:
    """Recompute metadata from the current on-disk files for a title."""
    files = load_save_files(title_id)
    if not files:
        return None

    existing = get_metadata(title_id)
    if is_ps1_title_id(title_id):
        bundle_hash, total_size, file_count = psp_visible_stats(files)
    elif game_names.detect_platform(title_id) == "PS3":
        bundle_hash, total_size, file_count = _ps3_visible_stats(files)
    else:
        all_data = b"".join(data for _, data in files)
        bundle_hash = hashlib.sha256(all_data).hexdigest()
        total_size = sum(len(data) for _, data in files)
        file_count = len(files)

    if existing is not None:
        meta = SaveMetadata(
            title_id=existing.title_id,
            name=existing.name,
            last_sync=existing.last_sync,
            last_sync_source=source or existing.last_sync_source,
            save_hash=bundle_hash,
            save_size=total_size,
            file_count=file_count,
            client_timestamp=existing.client_timestamp,
            server_timestamp=existing.server_timestamp,
            console_id=existing.console_id,
            platform=existing.platform,
            system=existing.system,
        )
    else:
        game_name, platform = game_names.lookup_name_and_platform(title_id)
        parsed = _parse_emulator_id(title_id)
        system = parsed[0] if parsed else platform
        meta = SaveMetadata(
            title_id=title_id,
            name=game_name,
            last_sync="",
            last_sync_source=source or "migration",
            save_hash=bundle_hash,
            save_size=total_size,
            file_count=file_count,
            client_timestamp=0,
            server_timestamp="",
            console_id="",
            platform=platform,
            system=system,
        )

    db.upsert(meta.to_dict())
    return meta


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
    """Delete a save (removes entire title folder and DB row). console_id is ignored (flat layout)."""
    title_dir = _title_dir(title_id)
    if title_dir.exists():
        shutil.rmtree(title_dir)
    db.delete(title_id)
