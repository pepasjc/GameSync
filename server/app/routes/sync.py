from fastapi import APIRouter

from app.models.save import ConflictInfo, SyncPlan, SyncRequest
from app.services import storage

router = APIRouter()


def _add_conflict(
    conflict_list: list[str],
    conflict_info: list[ConflictInfo],
    title_id: str,
    server_meta,
    client_hash: str,
    client_size: int,
    console_id: str | None,
) -> None:
    """Add a conflict with detailed info."""
    conflict_list.append(title_id)
    same_console = bool(console_id and server_meta.console_id == console_id)
    conflict_info.append(
        ConflictInfo(
            title_id=title_id,
            server_hash=server_meta.save_hash,
            server_size=server_meta.save_size,
            server_timestamp=server_meta.server_timestamp,
            server_console_id=server_meta.console_id or "unknown",
            client_hash=client_hash,
            client_size=client_size,
            same_console=same_console,
        )
    )


@router.post("/sync")
async def sync(request: SyncRequest) -> SyncPlan:
    """Compare client title metadata against server state and return a sync plan.

    All consoles share a single save slot per title (flat storage). Conflicts
    are resolved by hash comparison using the last_synced_hash for three-way merge.
    """
    upload: list[str] = []
    download: list[str] = []
    conflict: list[str] = []
    conflict_info: list[ConflictInfo] = []
    up_to_date: list[str] = []

    client_title_ids = set()
    console_id = request.console_id or ""

    for title in request.titles:
        client_title_ids.add(title.title_id)

        # Look up the shared save slot for the title
        server_meta = storage.get_metadata(title.title_id)

        if server_meta is None:
            # No save on server for this title -> upload
            upload.append(title.title_id)
            continue

        if title.save_hash == server_meta.save_hash:
            up_to_date.append(title.title_id)
            continue

        # Hashes differ -> three-way comparison with last_synced_hash
        if title.last_synced_hash is not None:
            if title.last_synced_hash == server_meta.save_hash:
                # Server unchanged since last sync, client changed -> upload
                upload.append(title.title_id)
            elif title.last_synced_hash == title.save_hash:
                # Client unchanged since last sync, server changed -> download
                download.append(title.title_id)
            else:
                # Both changed since last sync -> conflict
                _add_conflict(
                    conflict, conflict_info, title.title_id,
                    server_meta, title.save_hash, title.size, console_id
                )
        else:
            # No sync history: save exists but hashes differ.
            # Default: download (prefer server version to avoid overwriting).
            download.append(title.title_id)

    # Find titles on the server that the client doesn't have
    server_only: list[str] = []
    for meta in storage.list_titles():
        tid = meta["title_id"]
        if tid not in client_title_ids:
            server_only.append(tid)

    return SyncPlan(
        upload=upload,
        download=download,
        conflict=conflict,
        up_to_date=up_to_date,
        server_only=server_only,
        conflict_info=conflict_info,
    )
