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
    """Download raw save file (first file only) - for DS client compatibility."""
    title_id = _validate_title_id(title_id)
    meta = storage.get_metadata(title_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="No save found for this title")

    files = storage.load_save_files(title_id)
    if files is None or len(files) == 0:
        raise HTTPException(status_code=404, detail="Save data missing on disk")

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
