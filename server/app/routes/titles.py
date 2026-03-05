from fastapi import APIRouter
from pydantic import BaseModel

from app.services import storage, game_names, serialstation

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

    # Only query Serial Station for PlayStation codes (9-char format)
    # 3DS/DS codes are 4-char game codes — Serial Station doesn't cover them
    ps_codes = [c for c in request.codes if len(c) == 9 and c.isalnum()]

    if ps_codes:
        ss_results = await serialstation.lookup_batch(ps_codes)
        # Serial Station takes priority over local DB
        typed.update(ss_results)

    names = {code: entry[0] for code, entry in typed.items()}
    types = {code: entry[1] for code, entry in typed.items()}
    return {"names": names, "types": types}
