from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.services import storage, game_names, saturn_archives

router = APIRouter()


class NameLookupRequest(BaseModel):
    codes: list[str]


class NameHintRequest(BaseModel):
    codes: dict[str, str]  # title_id -> game_code (e.g. "0004000000161E00" -> "CTR-P-A22J")


class SaturnArchiveLookupRequest(BaseModel):
    title_id: str
    archive_names: list[str]


def _resolve_console_type(title: dict, typed: dict[str, tuple[str, str]]) -> str:
    """Return the normalized console type for a title row."""
    tid = title.get("title_id", "")
    if tid in typed:
        return typed[tid][1]
    if title.get("platform") and title.get("name") != tid:
        platform = title["platform"]
        if platform in ("PSP", "PSX"):
            return game_names.detect_platform(tid)
        return platform
    return game_names.detect_platform(tid)


@router.get("/titles")
async def list_titles(console_type: list[str] | None = Query(default=None)):
    titles = storage.list_titles()

    # Keep PS3 listing hashes aligned with /meta and /sync by refreshing rows
    # from the current on-disk files before we hand them to clients.
    for idx, title in enumerate(titles):
        tid = title.get("title_id", "")
        if not tid:
            continue
        meta = storage.get_metadata_for_sync(tid)
        if meta is not None:
            titles[idx] = meta.to_dict()

    if titles:
        # Overlay locally-known DAT/database names onto the response so old
        # metadata rows do not keep stale PS/PS2/PSP/PS3 labels forever.
        title_ids = [t.get("title_id", "") for t in titles if t.get("title_id", "")]
        typed: dict[str, tuple[str, str]] = (
            game_names.lookup_names_typed(title_ids) if title_ids else {}
        )

        for title in titles:
            tid = title.get("title_id", "")
            retail_serial = game_names.get_psx_retail_serial(tid)
            if retail_serial:
                title["retail_serial"] = retail_serial

            console = _resolve_console_type(title, typed)
            if tid in typed:
                resolved_name, resolved_platform = typed[tid]
                title["name"] = resolved_name
                title["platform"] = resolved_platform
                title["game_name"] = resolved_name
                title["console_type"] = resolved_platform
            else:
                # Use stored platform/name if already stamped on the metadata.
                # Re-detect platform for saves stored as "PSP" or "PSX" in case
                # they are actually PSone Classics that were uploaded before the
                # PS1 classification was added.
                if title.get("platform") and title.get("name") != tid:
                    title["game_name"] = title["name"]
                    title["console_type"] = console
                    continue
                title["game_name"] = tid
                title["console_type"] = console

    if console_type:
        wanted = {value.upper() for value in console_type if value}
        titles = [
            title for title in titles
            if (title.get("console_type") or "").upper() in wanted
        ]

    return {"titles": titles}


@router.post("/titles/update_names")
async def update_game_names(request: NameHintRequest):
    """Resolve and persist game name/platform for titles where it is still unset.

    Accepts a map of title_id -> game_code. For each entry where the stored
    name equals the title_id (i.e. was never resolved), looks up the game_code
    in the local DB and writes the result back to metadata.json.
    """
    for title_id, game_code in request.codes.items():
        if not game_code:
            continue
        meta = storage.get_metadata(title_id)
        if meta is None or meta.name != title_id:
            continue  # already has a name, skip
        typed = game_names.lookup_names_typed([game_code])
        if game_code in typed:
            name, platform = typed[game_code]
            storage.update_metadata_name(title_id, name, platform)
    return {"status": "ok"}


@router.post("/titles/names")
async def lookup_game_names(request: NameLookupRequest):
    """Look up game names and platform types for product codes.

    Returns: {"names": {"CODE": "Name", ...}, "types": {"CODE": "VITA|PSX|PSP|3DS|NDS", ...}}
    """
    typed: dict[str, tuple[str, str]] = game_names.lookup_names_typed(request.codes)

    names = {code: entry[0] for code, entry in typed.items()}
    types = {code: entry[1] for code, entry in typed.items()}
    retail_serials = {
        c: r for c in request.codes
        if (r := game_names.get_psx_retail_serial(c)) is not None
    }
    return {"names": names, "types": types, "retail_serials": retail_serials}


@router.post("/titles/saturn-archives")
async def lookup_saturn_archive_candidates(request: SaturnArchiveLookupRequest):
    results = saturn_archives.lookup_archive_candidates(
        request.title_id, request.archive_names
    )
    return {
        "title_id": request.title_id,
        "results": results,
    }
