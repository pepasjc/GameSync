"""RetroAchievements for real-hardware DS play (nds-bootstrap-ra + ndssync).

``GET  /ra/set/{md5}``  achievement set in the DS's text format
``POST /ra/unlocks``    unlocks logged on the DS; dry-run unless SYNC_RA_SUBMIT
``GET  /ra/unlocks``    what the server has received so far
"""

import asyncio
import logging

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


def _game_id_for(md5: str) -> int:
    from app.services.ra_index import _Libraries

    libraries = _Libraries(settings.save_dir / ".ra_cache",
                           settings.ra_api_key, settings.ra_username)
    game_id, _, _ = libraries.find(md5, "NDS")
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


class Unlock(BaseModel):
    id: int
    # Seconds between the unlock and the upload, from the DS clock.
    ago: int = Field(default=0, ge=0)


class UnlockUpload(BaseModel):
    md5: str
    game_id: int = 0
    unlocks: list[Unlock]


@router.post("/unlocks")
async def post_unlocks(body: UnlockUpload):
    md5 = _check_md5(body.md5)
    live = settings.ra_submit
    if live:
        _require_token()
    results = await asyncio.to_thread(
        ra_connect.record_unlocks, settings.save_dir, md5, body.game_id,
        [u.model_dump() for u in body.unlocks], live,
        settings.ra_username, settings.ra_token,
    )
    for r in results:
        logger.info("[ra] unlock %s -> %s", r["id"], r["status"])
    return {"submit": live, "results": results}


@router.get("/unlocks")
async def get_unlocks():
    return {"submit": settings.ra_submit, "unlocks": ra_connect.load_unlocks(settings.save_dir)}
