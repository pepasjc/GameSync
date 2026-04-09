import hashlib
import logging
import re
import time

from fastapi import APIRouter, HTTPException, Query, Request, Response

from app.models.save import (
    BundleFile,
    SaveBundle,
    is_hex_title_id,
    validate_any_title_id,
)
from app.services import game_names, storage
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
from app.services.gc_cards import (
    canonical_card_name as gc_canonical_card_name,
    gc_code_from_title_id,
    gc_extract_gci,
    gc_insert_gci,
    get_card_from_files as gc_get_card_from_files,
    get_gci_from_files as gc_get_gci_from_files,
    is_gc_card_image,
)
from app.services.bundle import BundleError, create_bundle, parse_bundle

router = APIRouter()
logger = logging.getLogger(__name__)

# Accepts 16-char hex (3DS/DS) or 4-16 alphanumeric product codes (PSP/Vita)
_TITLE_ID_RE = re.compile(r"^[0-9A-Za-z]{4,16}$")

_TRACE_TITLE_IDS = {"BLJS10001GAME"}


def _trace_title_files(stage: str, title_id: str, files: list[tuple[str, bytes]]) -> None:
    if title_id not in _TRACE_TITLE_IDS:
        return
    details = ", ".join(
        f"{path}({len(data)}:{hashlib.sha256(data).hexdigest()})"
        for path, data in files
    )
    logger.info("ps3 trace %s %s files=[%s]", stage, title_id, details)


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


def _resolve_ps1_title_alias(title_id: str) -> str:
    """Resolve a PS1 retail serial to the stored server title ID when possible."""
    if storage.get_metadata(title_id) is not None:
        return title_id

    if not is_ps1_title_id(title_id):
        return title_id

    wanted = title_id.upper()
    for row in storage.list_titles():
        candidate = str(row.get("title_id", "")).upper()
        if not candidate or candidate == wanted:
            continue
        if game_names.get_psx_retail_serial(candidate) == wanted:
            return candidate

    return title_id


@router.get("/saves/{title_id}/meta")
async def get_save_meta(
    title_id: str,
    request: Request,
    console_id: str = Query(""),
):
    title_id = _validate_title_id(title_id)
    meta = storage.get_metadata_for_sync(title_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="No save found for this title")
    return meta.to_dict()


@router.get("/saves/{title_id}/manifest")
async def get_save_manifest(
    title_id: str,
    request: Request,
    console_id: str = Query(""),
):
    title_id = _validate_title_id(title_id)
    meta = storage.get_metadata_for_sync(title_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="No save found for this title")

    files = storage.load_save_files(title_id)
    if not files:
        raise HTTPException(status_code=404, detail="Save data missing on disk")

    comparable = storage.comparable_files(title_id, files)
    lines = [
        f"{path}\t{len(data)}\t{hashlib.sha256(data).hexdigest()}"
        for path, data in comparable
    ]
    return Response(
        content=("\n".join(lines) + ("\n" if lines else "")).encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={
            "X-Save-Timestamp": str(meta.client_timestamp),
            "X-Save-Hash": meta.save_hash,
            "X-Save-File-Count": str(len(comparable)),
        },
    )


@router.get("/saves/{title_id}/ps1-card/meta")
async def get_ps1_card_meta(title_id: str, slot: int = Query(0, ge=0, le=1)):
    title_id = _validate_title_id(title_id)
    if not is_ps1_title_id(title_id):
        raise HTTPException(status_code=400, detail="Not a PS1 title ID")

    resolved_title_id = _resolve_ps1_title_alias(title_id)
    meta = storage.get_metadata(resolved_title_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="No save found for this title")

    files = storage.load_save_files(resolved_title_id)
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

    resolved_title_id = _resolve_ps1_title_alias(title_id)
    meta = storage.get_metadata(resolved_title_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="No save found for this title")

    files = storage.load_save_files(resolved_title_id)
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

    resolved_title_id = _resolve_ps1_title_alias(title_id)

    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Empty request body")

    files = ensure_raw_slot_files(storage.load_save_files(resolved_title_id) or [])

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
        title_id_str=resolved_title_id,
    )
    cid = _console_id_from_request(request, console_id)
    meta = storage.store_save(bundle, source="ps1_card", console_id=cid)
    return {
        "status": "ok",
        "timestamp": meta.last_sync,
        "sha256": hashlib.sha256(body).hexdigest(),
    }


@router.get("/saves/{title_id}/gc-card/meta")
async def get_gc_card_meta(title_id: str):
    """Return save metadata with a hash computed over the GCI bytes.

    Both desktop (card image) and Android (gci) clients compare the same
    GCI-derived hash so neither appears perpetually out of date.
    """
    title_id = _validate_title_id(title_id)
    meta = storage.get_metadata(title_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="No save found for this title")

    files = storage.load_save_files(title_id)
    if not files:
        raise HTTPException(status_code=404, detail="Save data missing on disk")

    game_code = gc_code_from_title_id(title_id)
    match = gc_get_gci_from_files(files, game_code)
    if match is None:
        raise HTTPException(status_code=404, detail="No GC save found for this title")
    _, gci = match

    d = meta.to_dict()
    d["save_hash"] = hashlib.sha256(gci).hexdigest()
    d["save_size"] = len(gci)
    return d


@router.get("/saves/{title_id}/gc-card")
async def download_gc_card(
    title_id: str,
    format: str = Query("raw"),
):
    """Download a GC save.

    ``?format=raw`` (default) — returns the full 8 MB card image for MemCard Pro.
    ``?format=gci`` — extracts and returns the compact GCI for Dolphin/Android.
    """
    title_id = _validate_title_id(title_id)
    fmt = format.strip().lower()
    if fmt not in {"raw", "gci"}:
        raise HTTPException(status_code=400, detail="format must be 'raw' or 'gci'")

    meta = storage.get_metadata(title_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="No save found for this title")

    files = storage.load_save_files(title_id)
    if not files:
        raise HTTPException(status_code=404, detail="Save data missing on disk")

    if fmt == "gci":
        game_code = gc_code_from_title_id(title_id)
        match = gc_get_gci_from_files(files, game_code)
        if match is None:
            raise HTTPException(status_code=404, detail="No GC save found for this title")
        _, content = match
        filename = "card.gci"
    else:
        match = gc_get_card_from_files(files)
        if match is None:
            raise HTTPException(status_code=404, detail="No GC save found for this title")
        _, content = match
        filename = "card.raw"

    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={
            "X-Save-Timestamp": str(meta.client_timestamp),
            "X-Save-Hash": hashlib.sha256(content).hexdigest(),
            "X-Save-Size": str(len(content)),
            "X-Save-Path": filename,
        },
    )


@router.post("/saves/{title_id}/gc-card")
async def upload_gc_card(
    title_id: str,
    request: Request,
    format: str = Query("raw"),
    console_id: str = Query(""),
):
    """Upload a GC save.

    ``?format=raw`` (default) — accepts a full 8 MB card image from MemCard Pro.
    ``?format=gci`` — accepts compact GCI bytes from Dolphin/Android; if a card
    image is already stored the GCI is inserted into it, otherwise the GCI is
    stored verbatim until the desktop overwrites with a full card.
    """
    title_id = _validate_title_id(title_id)
    fmt = format.strip().lower()
    if fmt not in {"raw", "gci"}:
        raise HTTPException(status_code=400, detail="format must be 'raw' or 'gci'")

    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Empty request body")

    if fmt == "gci":
        # Try to merge GCI into an existing card image
        existing_files = storage.load_save_files(title_id) or []
        existing = gc_get_card_from_files(existing_files)
        if existing is not None and is_gc_card_image(existing[1]):
            updated_card = gc_insert_gci(existing[1], body)
            if updated_card is not None:
                store_data = updated_card
                store_name = gc_canonical_card_name()
            else:
                # Insert failed (block-count mismatch etc.) — store GCI as-is
                store_data = body
                store_name = "card.gci"
        else:
            # No card on server yet — store GCI; desktop will overwrite later
            store_data = body
            store_name = "card.gci"
    else:
        store_data = body
        store_name = gc_canonical_card_name()

    bundle = SaveBundle(
        title_id=int(title_id, 16) if is_hex_title_id(title_id) else 0,
        timestamp=int(time.time()),
        files=[
            BundleFile(
                path=store_name,
                size=len(store_data),
                sha256=hashlib.sha256(store_data).digest(),
                data=store_data,
            )
        ],
        title_id_str="" if is_hex_title_id(title_id) else title_id,
    )
    cid = _console_id_from_request(request, console_id)
    meta = storage.store_save(bundle, source="gc_card", console_id=cid)
    return {
        "status": "ok",
        "timestamp": meta.last_sync,
        "sha256": hashlib.sha256(store_data).hexdigest(),
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

    _trace_title_files("download-response", title_id, files)

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

    _trace_title_files(
        "upload-request",
        title_id,
        [(f.path, f.data) for f in bundle.files],
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
