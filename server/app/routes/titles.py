import re

from fastapi import APIRouter
from pydantic import BaseModel

from app.services import storage, game_names, serialstation

_PS_PREFIX_RE = re.compile(r"^[A-Z]{4}\d{5}")

router = APIRouter()


class NameLookupRequest(BaseModel):
    codes: list[str]


class NameHintRequest(BaseModel):
    codes: dict[str, str]  # title_id -> game_code (e.g. "0004000000161E00" -> "CTR-P-A22J")


@router.get("/titles")
async def list_titles():
    titles = storage.list_titles()

    if titles:
        # For titles missing name/platform in metadata (old saves), do a batch lookup
        codes_needing_lookup = [
            t.get("title_id", "")
            for t in titles
            if not t.get("platform") or t.get("name") == t.get("title_id")
        ]
        typed: dict[str, tuple[str, str]] = {}
        if codes_needing_lookup:
            typed = game_names.lookup_names_typed(codes_needing_lookup)

            # For PS codes still missing a name, try Serial Station (same as /titles/names)
            ps_lookup: dict[str, str] = {}
            for c in codes_needing_lookup:
                if c not in typed:
                    m = _PS_PREFIX_RE.match(c.upper())
                    if m:
                        ps_lookup[c] = c[:9].upper()
            if ps_lookup:
                unique_bases = list(set(ps_lookup.values()))
                ss_results = await serialstation.lookup_batch(unique_bases)
                for orig, base in ps_lookup.items():
                    if base in ss_results:
                        typed[orig] = ss_results[base]

        for title in titles:
            tid = title.get("title_id", "")

            # Use stored platform/name if already stamped on the metadata.
            # Re-detect platform for saves stored as "PSP" or "PSX" in case
            # they are actually PSone Classics that were uploaded before the
            # PS1 classification was added.
            if title.get("platform") and title.get("name") != tid:
                title["game_name"] = title["name"]
                platform = title["platform"]
                if platform in ("PSP", "PSX"):
                    platform = game_names.detect_platform(tid)
                title["console_type"] = platform
            elif tid in typed:
                title["game_name"] = typed[tid][0]
                title["console_type"] = typed[tid][1]
            else:
                title["game_name"] = tid
                title["console_type"] = game_names.detect_platform(tid)

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

    Tries Serial Station API first (live PlayStation data), falls back to
    local database for any codes not found there.

    Returns: {"names": {"CODE": "Name", ...}, "types": {"CODE": "VITA|PSX|PSP|3DS|NDS", ...}}
    """
    # Start with local DB results for all codes
    typed: dict[str, tuple[str, str]] = game_names.lookup_names_typed(request.codes)

    # Build a map from original code -> 9-char base for PlayStation codes.
    # PSP save dirs may have slot suffixes (e.g. ULUS10272DATA00); Vita codes
    # are always exactly 9 chars. Serial Station only knows the 9-char base.
    ps_lookup: dict[str, str] = {}  # original code -> 9-char lookup key
    for c in request.codes:
        m = _PS_PREFIX_RE.match(c.upper())
        if m:
            ps_lookup[c] = c[:9].upper()

    if ps_lookup:
        unique_bases = list(set(ps_lookup.values()))
        ss_results = await serialstation.lookup_batch(unique_bases)
        # Map Serial Station results back to original (possibly longer) codes.
        # Serial Station takes priority over local DB.
        for orig, base in ps_lookup.items():
            if base in ss_results:
                typed[orig] = ss_results[base]

    names = {code: entry[0] for code, entry in typed.items()}
    types = {code: entry[1] for code, entry in typed.items()}
    return {"names": names, "types": types}
