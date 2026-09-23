import hashlib
import logging
import re
import struct
import time

from fastapi import APIRouter, HTTPException, Query, Request, Response

from app.models.save import (
    BundleFile,
    SaveBundle,
    is_hex_title_id,
    validate_any_title_id,
)
from app.services import dat_normalizer, game_names, storage
from shared.sync_id import (
    canonicalize_code_form_title_id,
    canonicalize_slug_title_id,
)
from app.services.ps1_cards import (
    create_vmp,
    ensure_raw_slot_files,
    extract_raw_card,
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
from app.services import ps1mc, ps2mc
from app.services.gc_cards import (
    canonical_card_name as gc_canonical_card_name,
    gc_card_from_gci,
    gc_code_from_title_id,
    gc_extract_gci,
    gc_insert_gci,
    get_card_from_files as gc_get_card_from_files,
    get_full_card_from_files as gc_get_full_card_from_files,
    get_gci_from_files as gc_get_gci_from_files,
    is_gc_card_image,
    parse_card_entries as gc_parse_card_entries,
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
    """Validate and normalize a title ID (hex or product code).

    Also canonicalizes slug-form IDs to their DAT-backed native form when
    possible: a client uploading ``NDS_mario_kart_ds_usa_australia`` is
    silently routed to the canonical ``00048000`` + hex(gamecode) ID that
    the 3DS/NDS homebrew clients also use for the same game.  This means
    saves from Steam Deck/Android slug-form clients land under the same
    storage key as the hardware clients and sync cleanly across all of
    them.  If no DAT entry matches, the slug is kept as-is so the save is
    still addressable.
    """
    try:
        validated = validate_any_title_id(title_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid title ID format")

    norm = dat_normalizer.get()
    if norm is None:
        return validated
    return canonicalize_slug_title_id(
        validated,
        serial_lookup=norm.lookup_serial,
    )


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


def _store_single_game_card(
    serial: str, game_dir: str, files: list[tuple[str, bytes]],
    timestamp: int, console_id: str,
) -> dict:
    """Store one game's save as a single-game 8 MB ``card.mc2`` under its serial.

    Per-game canonical storage reuses the existing PS2 card pipeline, so the
    plain ``GET /saves/{serial}/ps2-card`` download path serves it unchanged.
    """
    title_id = _validate_title_id(serial)
    card = ps2mc.build_card({game_dir: files}, timestamp=timestamp)
    bundle = SaveBundle(
        title_id=0,
        timestamp=timestamp,
        files=[
            BundleFile(
                path=canonical_card_name(),
                size=len(card),
                sha256=hashlib.sha256(card).digest(),
                data=card,
            )
        ],
        title_id_str=title_id,
    )
    storage.store_save(bundle, source="ps2_vmc", console_id=console_id)
    return {
        "dir": game_dir,
        "serial": title_id,
        "size": len(card),
        "sha256": hashlib.sha256(card).hexdigest(),
    }


@router.get("/saves/{title_id}/ps2-files")
async def download_ps2_files(title_id: str):
    """Return one game's save as a P2FD folder payload for the physical-card path.

    The server holds the canonical single-game ``card.mc2``; here it is parsed
    back into its directory + files so the PS2 client can write them straight to
    a physical memory card via libmc.
    """
    title_id = _validate_title_id(title_id)
    meta = storage.get_metadata(title_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="No save found for this title")

    files = storage.load_save_files(title_id)
    if not files:
        raise HTTPException(status_code=404, detail="Save data missing on disk")

    match = get_ps2_canonical_card_from_files(files)
    if match is None:
        raise HTTPException(status_code=404, detail="No PS2 card found for this title")
    _, card = match

    games = ps2mc.parse_card(card)
    if not games:
        raise HTTPException(status_code=404, detail="No game folder in stored card")
    game_dir, game_files = next(iter(games.items()))
    payload = ps2mc.build_p2fd(game_dir, game_files)

    return Response(
        content=payload,
        media_type="application/octet-stream",
        headers={
            "X-Save-Timestamp": str(meta.client_timestamp),
            "X-Save-Hash": hashlib.sha256(payload).hexdigest(),
            "X-Save-Size": str(len(payload)),
            "X-Save-Dir": game_dir,
        },
    )


@router.post("/saves/{title_id}/ps2-files")
async def upload_ps2_files(
    title_id: str,
    request: Request,
    console_id: str = Query(""),
):
    """Accept one game's save as a P2FD folder payload (physical-card push).

    The server builds the canonical single-game ``card.mc2`` from the folder, so
    a save pushed off a physical card is interchangeable with VMC/MemCard Pro
    imports for the same serial.
    """
    title_id = _validate_title_id(title_id)
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Empty request body")

    try:
        game_dir, files = ps2mc.parse_p2fd(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    cid = _console_id_from_request(request, console_id)
    result = _store_single_game_card(
        title_id, game_dir, files, int(time.time()), cid
    )
    return {"status": "ok", **result}


def _store_ps1_single_card(
    serial: str, raw_card: bytes, timestamp: int, console_id: str
) -> dict:
    """Store one save as a single-save 128 KB PS1 card under its serial.

    Reuses the existing PS1 storage layout (``slot0.mcd`` + legacy ``.VMP``) so
    the plain ``GET /saves/{serial}/ps1-card`` download path serves it unchanged.
    """
    title_id = _validate_title_id(serial)
    resolved = _resolve_ps1_title_alias(title_id)
    files = {
        slot_raw_name(0): raw_card,
        "SCEVMC0.VMP": create_vmp(raw_card),
    }
    bundle = SaveBundle(
        title_id=0,
        timestamp=timestamp,
        files=[
            BundleFile(
                path=path,
                size=len(data),
                sha256=hashlib.sha256(data).digest(),
                data=data,
            )
            for path, data in sorted(files.items())
        ],
        title_id_str=resolved,
    )
    storage.store_save(bundle, source="ps1_vmc", console_id=console_id)
    return {
        "serial": title_id,
        "size": len(raw_card),
        "sha256": hashlib.sha256(raw_card).hexdigest(),
    }


@router.get("/saves/{title_id}/ps1-save")
async def download_ps1_save(title_id: str):
    """Return one PS1 save's raw data for restoring to a physical PS1 card.

    Payload: u32 name_len (LE) + name + raw save bytes.  The name is the PS1
    save's on-card filename (needed to recreate the directory entry)."""
    title_id = _validate_title_id(title_id)
    resolved = _resolve_ps1_title_alias(title_id)
    meta = storage.get_metadata(resolved)
    if meta is None:
        raise HTTPException(status_code=404, detail="No save found for this title")

    files = storage.load_save_files(resolved)
    raw = get_slot_raw_from_files(files or [], 0)
    if raw is None:
        raise HTTPException(status_code=404, detail="No PS1 card stored for this title")

    saves = ps1mc.parse_card(raw)
    if not saves:
        raise HTTPException(status_code=404, detail="No save in stored PS1 card")

    name, data = saves[0]
    nb = name.encode("ascii", "replace")
    payload = struct.pack("<I", len(nb)) + nb + data
    return Response(
        content=payload,
        media_type="application/octet-stream",
        headers={
            "X-Save-Name": name,
            "X-Save-Size": str(len(data)),
            "X-Save-Hash": hashlib.sha256(data).hexdigest(),
        },
    )


@router.post("/saves/{title_id}/ps1-save")
async def upload_ps1_save(
    title_id: str,
    request: Request,
    name: str = Query(""),
    console_id: str = Query(""),
):
    """Accept one PS1 save (raw block data read off a physical PS1 card) and
    store it as a single-save 128 KB card under its serial."""
    title_id = _validate_title_id(title_id)
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Empty request body")

    save_name = (name or title_id).strip()
    try:
        card = ps1mc.build_single_save_card(save_name, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    cid = _console_id_from_request(request, console_id)
    result = _store_ps1_single_card(title_id, card, int(time.time()), cid)
    return {"status": "ok", **result}


@router.post("/saves/ps1-vmc/import")
async def import_ps1_vmc(request: Request, console_id: str = Query("")):
    """Split a 128 KB PS1 memory card / VMC into per-game single-save cards.

    Accepts a raw ``.mcd``/``.mcr`` card or a ``.vmp``; each save block-chain is
    extracted, keyed by its disc serial, and stored as its own single-save card.
    """
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Empty request body")

    try:
        raw = extract_raw_card(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        saves = ps1mc.parse_card(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid PS1 card: {exc}")

    cid = _console_id_from_request(request, console_id)
    timestamp = int(time.time())
    imported: list[dict] = []
    skipped: list[str] = []
    for name, data in saves:
        serial = ps1mc.serial_from_filename(name)
        if serial is None:
            skipped.append(name)
            continue
        card = ps1mc.build_single_save_card(name, data)
        imported.append(_store_ps1_single_card(serial, card, timestamp, cid))

    return {"status": "ok", "imported": imported, "skipped": skipped}


@router.post("/saves/ps2-vmc/import")
async def import_ps2_vmc(request: Request, console_id: str = Query("")):
    """Split a full PS2 memory-card / VMC image into per-game saves.

    Accepts an 8 MB ``.mc2`` or ``.ps2`` card image (a physical card dump, an
    OPL VMC file, or a MemCard Pro per-channel image).  Each game directory is
    extracted and stored separately keyed by its disc serial, so a save written
    on one card syncs to every other PS2 source for the same game.
    """
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Empty request body")

    try:
        card = extract_ps2_canonical_card(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        games = ps2mc.parse_card(card)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid PS2 card: {exc}")

    cid = _console_id_from_request(request, console_id)
    timestamp = int(time.time())
    imported: list[dict] = []
    skipped: list[str] = []
    for game_dir, files in games.items():
        serial = ps2mc.serial_from_dirname(game_dir)
        if serial is None:
            skipped.append(game_dir)
            continue
        imported.append(
            _store_single_game_card(serial, game_dir, files, timestamp, cid)
        )

    return {"status": "ok", "imported": imported, "skipped": skipped}


def _store_gc_single_game(game_code: str, gci: bytes, timestamp: int, console_id: str) -> dict:
    """Store one game's GCI under GC_<gamecode> as a full card image."""
    title_id = f"GC_{game_code.upper()}"
    card = gc_card_from_gci(gci)
    if card is not None:
        store_data, store_name = card, gc_canonical_card_name()
    else:
        store_data, store_name = gci, "card.gci"
    bundle = SaveBundle(
        title_id=0,
        timestamp=timestamp,
        files=[
            BundleFile(
                path=store_name,
                size=len(store_data),
                sha256=hashlib.sha256(store_data).digest(),
                data=store_data,
            )
        ],
        title_id_str=title_id,
    )
    storage.store_save(bundle, source="gc_vmc", console_id=console_id)
    return {"title_id": title_id, "size": len(store_data)}


@router.post("/saves/gc-vmc/import")
async def import_gc_vmc(request: Request, console_id: str = Query("")):
    """Split a full GameCube memory-card image into per-game saves.

    Accepts an 8 MB GC card image (a physical card dump, a MemCard Pro GC
    channel image, or a Dolphin ``.raw``).  Each save is extracted and stored
    separately keyed by ``GC_<gamecode>``, so a save written on one card syncs
    to every other GC source for the same game.
    """
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Empty request body")
    if not is_gc_card_image(body):
        raise HTTPException(status_code=400, detail="Not a GC card image")

    entries = gc_parse_card_entries(body)
    if not entries:
        raise HTTPException(status_code=400, detail="No GC saves found in card image")

    cid = _console_id_from_request(request, console_id)
    timestamp = int(time.time())
    imported = [_store_gc_single_game(code, gci, timestamp, cid) for code, gci in entries]
    return {"status": "ok", "imported": imported}


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
        # MemCard Pro always expects a full card image; synthesize one if the
        # server only holds a bare GCI (Dolphin/Android uploaded before desktop).
        match = gc_get_full_card_from_files(files)
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
        # format=raw is meant for full card images. If a client sends a bare
        # GCI here (e.g. a legacy mis-formatted .raw), wrap it into a real card
        # so storage never holds a GCI masquerading as card.raw.
        if not is_gc_card_image(body):
            wrapped = gc_card_from_gci(body)
            store_data = wrapped if wrapped is not None else body
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
    game_name: str = Query(""),
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

    # Verify title ID in URL matches bundle.  The URL title_id has already
    # been canonicalised (slug → hex/serial when the DAT knows the game); do
    # the same to the bundle's internal ID so a client that sends the slug
    # form in both URL and bundle still passes this check — both sides end
    # up comparing the same canonical form.
    bundle_tid = bundle.effective_title_id
    norm = dat_normalizer.get()
    if norm is not None:
        bundle_tid = canonicalize_slug_title_id(
            bundle_tid, serial_lookup=norm.lookup_serial
        )
    # Gamecode-form IDs (GC_grse) are case-insensitive — uppercase them so
    # storage keys off the same directory as the canonicalised URL ID.
    bundle_tid = canonicalize_code_form_title_id(bundle_tid)
    if bundle_tid.upper() != title_id.upper():
        raise HTTPException(
            status_code=400,
            detail=f"Title ID mismatch: URL={title_id}, bundle={bundle.effective_title_id}",
        )

    # Sync the bundle's title_id_str to the canonical form so downstream
    # storage (which reads ``bundle.effective_title_id``) keys off the
    # canonical ID instead of the client's slug.  For non-hex forms this
    # is the non-empty string field; hex forms leave title_id_str empty
    # and storage reads title_id_hex.
    if bundle_tid != bundle.effective_title_id:
        if is_hex_title_id(bundle_tid):
            bundle.title_id = int(bundle_tid, 16)
            bundle.title_id_str = ""
        else:
            bundle.title_id_str = bundle_tid

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

    meta = storage.store_save(
        bundle,
        source=source,
        console_id=cid,
        game_code=game_code,
        game_name_hint=game_name,
    )
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
