import re

from fastapi import APIRouter
from pydantic import BaseModel

from app.services import storage, game_names, serialstation

_PS_PREFIX_RE = re.compile(r"^[A-Z]{4}\d{5}")

router = APIRouter()


class NameLookupRequest(BaseModel):
    codes: list[str]


@router.get("/titles")
async def list_titles():
    titles = storage.list_titles()
    return {"titles": titles}


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
