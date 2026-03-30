import hashlib
import re
import time

from fastapi import APIRouter, HTTPException, Query, Request, Response

from app.models.save import (
    BundleFile,
    SaveBundle,
    is_hex_title_id,
    validate_any_title_id,
)
from app.services import storage
from app.services.ps1_cards import (
    create_vmp,
    ensure_raw_slot_files,
    get_slot_raw_from_files,
    is_ps1_title_id,
    psp_visible_files,
    slot_hash_and_size,
    slot_raw_name,
)
from app.services.ps2_cards import (
    canonical_card_name,
    card_hash_and_size as ps2_card_hash_and_size,
    convert_card_for_format,
    extract_canonical_card as extract_ps2_canonical_card,
    get_canonical_card_from_files as get_ps2_canonical_card_from_files,
    normalize_ps2_card_format,
)
from app.services.bundle import BundleError, create_bundle, parse_bundle

router = APIRouter()

# Accepts 16-char hex (3DS/DS) or 4-16 alphanumeric product codes (PSP/Vita)
_TITLE_ID_RE = re.compile(r"^[0-9A-Za-z]{4,16}$")


def _validate_title_id(title_id: str) -> str:
    """Validate and normalize a title ID (hex or product code)."""
    try:
        return validate_any_title_id(title_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid title ID format")


def _console_id_from_request(request: Request, query_console_id: str = "") -> str:
    """Extract console ID from header or query parameter."""
    return query_console_id.strip() or request.headers.get("X-Console-ID", "").strip()


def _resolve_console_id(cid: str, source: str) -> str:
    """Normalise console ID for metadata recording.

    PSP saves (source="psp" or source="psp_emu") are recorded under the
    shared "psp" console ID so that metadata is consistent across PSP hardware,
    the Vita's PSP emulator, and the PC sync tool.
    """
    if source in ("psp", "psp_emu"):
        return "psp"
    return cid


@router.get("/saves/{title_id}/meta")
async def get_save_meta(
    title_id: str,
    request: Request,
    console_id: str = Query(""),
):
    title_id = _validate_title_id(title_id)
    meta = storage.get_metadata(title_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="No save found for this title")
    return meta.to_dict()


@router.get("/saves/{title_id}/ps1-card/meta")
async def get_ps1_card_meta(title_id: str, slot: int = Query(0, ge=0, le=1)):
    title_id = _validate_title_id(title_id)
    if not is_ps1_title_id(title_id):
        raise HTTPException(status_code=400, detail="Not a PS1 title ID")

    meta = storage.get_metadata(title_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="No save found for this title")

    files = storage.load_save_files(title_id)
    if not files:
        raise HTTPException(status_code=404, detail="Save data missing on disk")

    slot_meta = slot_hash_and_size(files, slot)
    if slot_meta is None:
        raise HTTPException(status_code=404, detail=f"No PS1 card found for slot {slot}")

    save_hash, save_size = slot_meta
    return {
        "title_id": title_id,
        "slot": slot,
        "save_hash": save_hash,
        "save_size": save_size,
        "client_timestamp": meta.client_timestamp,
        "server_timestamp": meta.server_timestamp,
        "platform": meta.platform,
        "system": meta.system,
    }


@router.get("/saves/{title_id}/ps1-card")
async def download_ps1_card(title_id: str, slot: int = Query(0, ge=0, le=1)):
    title_id = _validate_title_id(title_id)
    if not is_ps1_title_id(title_id):
        raise HTTPException(status_code=400, detail="Not a PS1 title ID")

    meta = storage.get_metadata(title_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="No save found for this title")

    files = storage.load_save_files(title_id)
    if not files:
        raise HTTPException(status_code=404, detail="Save data missing on disk")

    raw = get_slot_raw_from_files(files, slot)
    if raw is None:
        raise HTTPException(status_code=404, detail=f"No PS1 card found for slot {slot}")

    return Response(
        content=raw,
        media_type="application/octet-stream",
        headers={
            "X-Save-Timestamp": str(meta.client_timestamp),
            "X-Save-Hash": hashlib.sha256(raw).hexdigest(),
            "X-Save-Size": str(len(raw)),
            "X-Save-Path": slot_raw_name(slot),
        },
    )


@router.get("/saves/{title_id}/ps2-card/meta")
async def get_ps2_card_meta(
    title_id: str,
    format: str = Query("mc2"),
):
    title_id = _validate_title_id(title_id)
    card_format = normalize_ps2_card_format(format)

    meta = storage.get_metadata(title_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="No save found for this title")

    files = storage.load_save_files(title_id)
    if not files:
        raise HTTPException(status_code=404, detail="Save data missing on disk")

    card_meta = ps2_card_hash_and_size(files, card_format)
    if card_meta is None:
        raise HTTPException(status_code=404, detail="No PS2 card found for this title")

    save_hash, save_size = card_meta
    return {
        "title_id": title_id,
        "format": card_format,
        "save_hash": save_hash,
        "save_size": save_size,
        "client_timestamp": meta.client_timestamp,
        "server_timestamp": meta.server_timestamp,
        "platform": meta.platform,
        "system": meta.system,
    }


@router.get("/saves/{title_id}/ps2-card")
async def download_ps2_card(
    title_id: str,
    format: str = Query("mc2"),
):
    title_id = _validate_title_id(title_id)
    card_format = normalize_ps2_card_format(format)

    meta = storage.get_metadata(title_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="No save found for this title")

    files = storage.load_save_files(title_id)
    if not files:
        raise HTTPException(status_code=404, detail="Save data missing on disk")

    match = get_ps2_canonical_card_from_files(files)
    if match is None:
        raise HTTPException(status_code=404, detail="No PS2 card found for this title")

    _, canonical = match
    rendered = convert_card_for_format(canonical, card_format)
    return Response(
        content=rendered,
        media_type="application/octet-stream",
        headers={
            "X-Save-Timestamp": str(meta.client_timestamp),
            "X-Save-Hash": hashlib.sha256(rendered).hexdigest(),
            "X-Save-Size": str(len(rendered)),
            "X-Save-Path": f"card.{card_format}",
        },
    )


@router.post("/saves/{title_id}/ps2-card")
async def upload_ps2_card(
    title_id: str,
    request: Request,
    format: str = Query("mc2"),
    console_id: str = Query(""),
):
    title_id = _validate_title_id(title_id)
    card_format = normalize_ps2_card_format(format)

    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Empty request body")

    try:
        canonical = extract_ps2_canonical_card(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    bundle = SaveBundle(
        title_id=int(title_id, 16) if is_hex_title_id(title_id) else 0,
        timestamp=int(time.time()),
        files=[
            BundleFile(
                path=canonical_card_name(),
                size=len(canonical),
                sha256=hashlib.sha256(canonical).digest(),
                data=canonical,
            )
        ],
        title_id_str="" if is_hex_title_id(title_id) else title_id,
    )
    cid = _console_id_from_request(request, console_id)
    meta = storage.store_save(bundle, source="ps2_card", console_id=cid)
    return {
        "status": "ok",
        "timestamp": meta.last_sync,
        "sha256": hashlib.sha256(canonical).hexdigest(),
    }


@router.post("/saves/{title_id}/ps1-card")
async def upload_ps1_card(
    title_id: str,
    request: Request,
    slot: int = Query(0, ge=0, le=1),
    console_id: str = Query(""),
):
    title_id = _validate_title_id(title_id)
    if not is_ps1_title_id(title_id):
        raise HTTPException(status_code=400, detail="Not a PS1 title ID")

    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Empty request body")

    files = ensure_raw_slot_files(storage.load_save_files(title_id) or [])

    replaced = False
    updated_files: list[tuple[str, bytes]] = []
    target = slot_raw_name(slot)
    legacy_target = f"SCEVMC{slot}.VMP"
    for path, data in files:
        if path == target:
            updated_files.append((path, body))
            replaced = True
        elif path == legacy_target:
            updated_files.append((path, create_vmp(body)))
        else:
            updated_files.append((path, data))
    if not replaced:
        updated_files.append((target, body))
    if legacy_target not in {path for path, _ in updated_files}:
        updated_files.append((legacy_target, create_vmp(body)))

    bundle_files = [
        BundleFile(
            path=path,
            size=len(data),
            sha256=hashlib.sha256(data).digest(),
            data=data,
        )
        for path, data in sorted(updated_files, key=lambda item: item[0])
    ]
    bundle = SaveBundle(
        title_id=0,
        timestamp=int(time.time()),
        files=bundle_files,
        title_id_str=title_id,
    )
    cid = _console_id_from_request(request, console_id)
    meta = storage.store_save(bundle, source="ps1_card", console_id=cid)
    return {
        "status": "ok",
        "timestamp": meta.last_sync,
        "sha256": hashlib.sha256(body).hexdigest(),
    }


@router.get("/saves/{title_id}/consoles")
async def list_save_consoles(title_id: str):
    """List all console slots that have saves for a title."""
    title_id = _validate_title_id(title_id)
    consoles = storage.list_consoles(title_id)
    if not consoles:
        raise HTTPException(status_code=404, detail="No saves found for this title")
    return {"title_id": title_id, "consoles": consoles}


@router.get("/saves/{title_id}/raw")
async def download_save_raw(
    title_id: str,
    request: Request,
    console_id: str = Query(""),
):
    """Download a raw single-file save.

    This endpoint is only safe for titles stored as exactly one file. Multi-file
    bundles (for example PPSSPP/PSone Classics slot directories) are not raw-save
    compatible, so returning the "first" file would silently produce bad downloads.
    """
    title_id = _validate_title_id(title_id)
    meta = storage.get_metadata(title_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="No save found for this title")

    files = storage.load_save_files(title_id)
    if files is None or len(files) == 0:
        raise HTTPException(status_code=404, detail="Save data missing on disk")
    if len(files) != 1:
        raise HTTPException(
            status_code=409,
            detail=(
                "This save is stored as a multi-file bundle and cannot be downloaded "
                "through the raw endpoint."
            ),
        )

    path, data = files[0]
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={
            "X-Save-Timestamp": str(meta.client_timestamp),
            "X-Save-Hash": meta.save_hash,
            "X-Save-Size": str(len(data)),
            "X-Save-Path": path,
        },
    )


@router.get("/saves/{title_id}")
async def download_save(
    title_id: str,
    request: Request,
    console_id: str = Query(""),
):
    title_id = _validate_title_id(title_id)
    meta = storage.get_metadata(title_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="No save found for this title")

    files = storage.load_save_files(title_id)
    if files is None:
        raise HTTPException(status_code=404, detail="Save data missing on disk")
    if is_ps1_title_id(title_id):
        files = psp_visible_files(files)

    bundle_files = []
    for path, data in files:
        bundle_files.append(
            BundleFile(
                path=path,
                size=len(data),
                sha256=hashlib.sha256(data).digest(),
                data=data,
            )
        )

    # Use v3 bundle format for PSP/Vita (non-hex title IDs)
    if is_hex_title_id(title_id):
        bundle = SaveBundle(
            title_id=int(title_id, 16),
            timestamp=meta.client_timestamp,
            files=bundle_files,
        )
    else:
        bundle = SaveBundle(
            title_id=0,
            timestamp=meta.client_timestamp,
            files=bundle_files,
            title_id_str=title_id,
        )

    bundle_data = create_bundle(bundle)
    return Response(
        content=bundle_data,
        media_type="application/octet-stream",
        headers={
            "X-Save-Timestamp": str(meta.client_timestamp),
            "X-Save-Hash": meta.save_hash,
            "X-Save-Size": str(meta.save_size),
        },
    )


@router.post("/saves/{title_id}")
async def upload_save(
    title_id: str,
    request: Request,
    force: bool = Query(False),
    source: str = Query("3ds"),
    console_id: str = Query(""),
    game_code: str = Query(""),
):
    title_id = _validate_title_id(title_id)
    cid = _resolve_console_id(_console_id_from_request(request, console_id), source)

    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Empty request body")

    try:
        bundle = parse_bundle(body)
    except BundleError as e:
        raise HTTPException(status_code=400, detail=f"Invalid bundle: {e}")

    # Verify title ID in URL matches bundle
    if bundle.effective_title_id.upper() != title_id.upper():
        raise HTTPException(
            status_code=400,
            detail=f"Title ID mismatch: URL={title_id}, bundle={bundle.effective_title_id}",
        )

    if is_ps1_title_id(title_id):
        ps1_files = ensure_raw_slot_files([(f.path, f.data) for f in bundle.files])
        bundle.files = [
            BundleFile(
                path=path,
                size=len(data),
                sha256=hashlib.sha256(data).digest(),
                data=data,
            )
            for path, data in sorted(ps1_files, key=lambda item: item[0])
        ]

    # Conflict check
    if not force:
        existing = storage.get_metadata(title_id)
        if existing and existing.client_timestamp >= bundle.timestamp:
            raise HTTPException(
                status_code=409,
                detail="Server has a newer or equal save. Use ?force=true to override.",
                headers={
                    "X-Server-Timestamp": str(existing.client_timestamp),
                    "X-Server-Hash": existing.save_hash,
                },
            )

    meta = storage.store_save(bundle, source=source, console_id=cid, game_code=game_code)
    return {
        "status": "ok",
        "timestamp": meta.last_sync,
        "sha256": meta.save_hash,
    }


@router.post("/saves/{title_id}/raw")
async def upload_save_raw(
    title_id: str,
    request: Request,
    force: bool = Query(False),
    console_id: str = Query(""),
):
    """Upload raw save file - wraps into bundle format for DS/PSP compatibility."""
    title_id = _validate_title_id(title_id)
    cid = _console_id_from_request(request, console_id)

    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Empty request body")

    timestamp = int(time.time())

    bundle_file = BundleFile(
        path="save.bin",
        size=len(body),
        sha256=hashlib.sha256(body).digest(),
        data=body,
    )

    if is_hex_title_id(title_id):
        bundle = SaveBundle(
            title_id=int(title_id, 16),
            timestamp=timestamp,
            files=[bundle_file],
        )
    else:
        bundle = SaveBundle(
            title_id=0,
            timestamp=timestamp,
            files=[bundle_file],
            title_id_str=title_id,
        )

    if not force:
        existing = storage.get_metadata(title_id)
        if existing and existing.client_timestamp >= timestamp:
            raise HTTPException(
                status_code=409,
                detail="Server has a newer or equal save. Use ?force=true to override.",
                headers={
                    "X-Server-Timestamp": str(existing.client_timestamp),
                    "X-Server-Hash": existing.save_hash,
                },
            )

    meta = storage.store_save(bundle, source="raw", console_id=cid)
    return {
        "status": "ok",
        "timestamp": meta.last_sync,
        "sha256": meta.save_hash,
    }


@router.get("/saves/{title_id}/history")
async def list_save_history(
    title_id: str,
    request: Request,
    console_id: str = Query(""),
):
    """List all available history versions for a title."""
    title_id = _validate_title_id(title_id)

    if not storage.title_exists(title_id):
        raise HTTPException(status_code=404, detail="No save found for this title")

    history = storage.list_history(title_id)
    return {"title_id": title_id, "versions": history}


@router.get("/saves/{title_id}/history/{timestamp}")
async def download_save_history(
    title_id: str,
    timestamp: int,
    request: Request,
    console_id: str = Query(""),
):
    """Download a specific history version as a bundle."""
    title_id = _validate_title_id(title_id)

    if not storage.title_exists(title_id):
        raise HTTPException(status_code=404, detail="No save found for this title")

    files = storage.load_history_version_by_unix_ts(title_id, timestamp)
    if files is None or len(files) == 0:
        raise HTTPException(status_code=404, detail="History version not found")

    bundle_files = []
    for path, data in files:
        bundle_files.append(
            BundleFile(
                path=path,
                size=len(data),
                sha256=hashlib.sha256(data).digest(),
                data=data,
            )
        )

    if is_hex_title_id(title_id):
        bundle = SaveBundle(
            title_id=int(title_id, 16),
            timestamp=0,
            files=bundle_files,
        )
    else:
        bundle = SaveBundle(
            title_id=0,
            timestamp=0,
            files=bundle_files,
            title_id_str=title_id,
        )

    bundle_data = create_bundle(bundle)
    return Response(
        content=bundle_data,
        media_type="application/octet-stream",
        headers={
            "X-Version-Timestamp": str(timestamp),
        },
    )


@router.delete("/saves/{title_id}")
async def delete_save(
    title_id: str,
    request: Request,
    console_id: str = Query(""),
):
    """Delete a save (removes title folder)."""
    title_id = _validate_title_id(title_id)

    if not storage.title_exists(title_id):
        raise HTTPException(status_code=404, detail="No save found for this title")

    storage.delete_save(title_id)
    return {"status": "ok", "title_id": title_id}
