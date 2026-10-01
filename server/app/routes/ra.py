"""RetroAchievements for real-hardware DS play (nds-bootstrap-ra + ndssync).

``GET  /ra/set/{md5}``  achievement set in the DS's text format
``POST /ra/sets``       up to 64 sets in one response (see ``_render_batch``)
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app.config import settings
from app.services import ra_connect

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ra")


def _require_token() -> None:
    if not settings.ra_username or not settings.ra_token:
        raise HTTPException(
            status_code=503,
            detail="RetroAchievements login missing: set SYNC_RA_USERNAME and "
                   "run `uv run python ra_login.py` to store SYNC_RA_TOKEN",
        )


def _libraries():
    from app.services.ra_index import _Libraries

    return _Libraries(settings.save_dir / ".ra_cache",
                      settings.ra_api_key, settings.ra_username)


def _game_id_for(md5: str, libraries=None) -> int:
    game_id, _, _ = (libraries or _libraries()).find(md5, "NDS")
    return game_id


def _check_md5(md5: str) -> str:
    md5 = md5.lower()
    if len(md5) != 32 or any(c not in "0123456789abcdef" for c in md5):
        raise HTTPException(status_code=400, detail="md5 must be 32 hex digits")
    return md5


@router.get("/set/{md5}", response_class=PlainTextResponse)
async def get_set(md5: str, game_id: int = 0):
    md5 = _check_md5(md5)
    _require_token()
    if not game_id:
        game_id = await asyncio.to_thread(_game_id_for, md5)
    if not game_id:
        raise HTTPException(status_code=404, detail="RetroAchievements does not know this ROM")
    try:
        patch = await asyncio.to_thread(
            ra_connect.fetch_patch, game_id, settings.ra_username, settings.ra_token,
            settings.save_dir / ".ra_cache",
        )
    except ra_connect.RaConnectError as exc:
        raise HTTPException(status_code=502, detail=f"RetroAchievements: {exc}")
    except OSError as exc:
        raise HTTPException(status_code=504, detail=f"RetroAchievements unreachable: {exc}")
    return ra_connect.render_set(patch, md5)


# Hashes per /ra/sets request: bounds the response a DS has to hold in RAM.
MAX_BATCH_SETS = 64


class SetBatch(BaseModel):
    md5s: list[str] = Field(max_length=MAX_BATCH_SETS)


def _render_batch(md5s: list[str]) -> str:
    """Every requested set in one text body, in request order::

        RASETS<TAB>1
        === <md5> <byte length>
        <exactly that many bytes: the set, as GET /ra/set/{md5} returns it>
        --- <md5> unknown
        --- <md5> error <reason>
        END

    ``unknown``: RetroAchievements has no game for the hash.  ``error``: the
    set could not be fetched this time (try again later).  ``END`` lets the
    client tell a complete body from a cut-off one.
    """
    libraries = _libraries()
    cache_dir = settings.save_dir / ".ra_cache"
    game_ids = {md5: _game_id_for(md5, libraries) for md5 in md5s}

    def _fetch(game_id: int):
        try:
            return ra_connect.fetch_patch(game_id, settings.ra_username,
                                          settings.ra_token, cache_dir)
        except (ra_connect.RaConnectError, OSError) as exc:
            return exc

    # A few at a time: sets not cached yet each cost a RetroAchievements
    # round trip, and the whole body has to be ready within the DS's
    # socket timeout.
    wanted = sorted({g for g in game_ids.values() if g})
    with ThreadPoolExecutor(max_workers=4) as pool:
        patches = dict(zip(wanted, pool.map(_fetch, wanted)))

    out = ["RASETS\t1\n"]
    for md5 in md5s:
        game_id = game_ids[md5]
        if not game_id:
            out.append(f"--- {md5} unknown\n")
            continue
        patch = patches[game_id]
        if isinstance(patch, Exception):
            logger.warning("[ra] set for %s (game %d) failed: %s", md5, game_id, patch)
            reason = " ".join(str(patch).split())[:80] or type(patch).__name__
            out.append(f"--- {md5} error {reason}\n")
            continue
        body = ra_connect.render_set(patch, md5).encode("utf-8")
        out.append(f"=== {md5} {len(body)}\n")
        out.append(body.decode("utf-8"))
    out.append("END\n")
    return "".join(out)


@router.post("/sets", response_class=PlainTextResponse)
async def get_sets(body: SetBatch):
    """Several achievement sets in one request.

    The DS network stack runs out of local ports after a few dozen
    connections, so the DS client hashes its whole ROM folder first and asks
    for the sets in batches instead of one ``GET /ra/set/{md5}`` per ROM.
    """
    md5s = list(dict.fromkeys(_check_md5(m) for m in body.md5s))
    _require_token()
    return await asyncio.to_thread(_render_batch, md5s)
