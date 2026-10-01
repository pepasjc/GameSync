"""
RetroAchievements "connect" client — the calls an emulator makes.

The DS runs achievements on real hardware (nds-bootstrap-ra) but can't do
HTTPS, so this server does the RA side for it:

* ``fetch_patch``  — a game's achievement definitions (``r=patch``), cached.
* ``login``        — trade a password for a connect token, once (ra_login.py).

These ``dorequest.php`` calls need the user's *connect token*, not the web
API key used for catalog badges.  Request shapes follow rcheevos
``src/rapi/rc_api_runtime.c`` / ``rc_api_user.c``.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request
from pathlib import Path

# rom_id puts the repo root on sys.path for us, so `shared` imports plainly.
from app.services import rom_id  # noqa: F401
from shared.ra_api import _user_agent

logger = logging.getLogger(__name__)

DOREQUEST_URL = "https://retroachievements.org/dorequest.php"
PATCH_MAX_AGE = 24 * 60 * 60
_TIMEOUT = 60
# rcheevos achievement category: 3 = core (published), 5 = unofficial.
CORE_FLAGS = 3
# RA adds fake achievements from this id up ("Warning: Unknown Emulator")
# for clients it doesn't recognise; rcheevos ignores them, so do we.
WARNING_ACHIEVEMENT_ID = 101000001


class RaConnectError(Exception):
    """RA answered, but said no (bad token, unknown game, ...)."""


def _user_agent_nds() -> str:
    return f"{_user_agent()} (NDS real hardware)"


def _dorequest(params: dict) -> dict:
    """POST ``params`` to dorequest.php, as rcheevos does, and parse the JSON."""
    body = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(
        DOREQUEST_URL,
        data=body,
        headers={
            "User-Agent": _user_agent_nds(),
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        data = json.load(resp)
    if not data.get("Success", False):
        raise RaConnectError(data.get("Error") or data.get("Code") or "request failed")
    return data


def login(username: str, password: str) -> str:
    """Connect token for ``username``.  The password is not kept."""
    data = _dorequest({"r": "login2", "u": username, "p": password})
    token = data.get("Token")
    if not token:
        raise RaConnectError("login succeeded but no token was returned")
    return token


# ---------------------------------------------------------------------------
# Achievement sets
# ---------------------------------------------------------------------------


def fetch_patch(game_id: int, username: str, token: str, cache_dir: Path,
                max_age: float = PATCH_MAX_AGE) -> dict:
    """``PatchData`` for a game; a stale cache wins over a failed download."""
    cache = cache_dir / f"patch_{game_id}.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < max_age:
        return json.loads(cache.read_text(encoding="utf-8"))
    try:
        data = _dorequest({"r": "patch", "u": username, "t": token, "g": game_id})
    except Exception:
        if cache.exists():
            logger.warning("[ra_connect] patch %d download failed; using cache", game_id)
            return json.loads(cache.read_text(encoding="utf-8"))
        raise
    patch = data.get("PatchData") or {}
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(patch), encoding="utf-8")
    return patch


def _clean(text) -> str:
    return " ".join(str(text or "").split())


def render_set(patch: dict, md5: str) -> str:
    """The set as the DS reads it: tab-separated lines, core achievements only.

    ::

        RASET<TAB>1
        game<TAB><game id><TAB><md5><TAB><title>
        ach<TAB><id><TAB><points><TAB><MemAddr><TAB><title><TAB><description>

    Condition strings never contain tabs, and titles and descriptions have
    theirs folded to spaces.
    """
    lines = ["RASET\t1", f"game\t{int(patch.get('ID', 0))}\t{md5.lower()}\t{_clean(patch.get('Title'))}"]
    for ach in patch.get("Achievements") or []:
        if int(ach.get("Flags", 0)) != CORE_FLAGS or int(ach["ID"]) >= WARNING_ACHIEVEMENT_ID:
            continue
        mem = str(ach.get("MemAddr") or "")
        if not mem or "\t" in mem or "\n" in mem:
            continue
        lines.append(f"ach\t{int(ach['ID'])}\t{int(ach.get('Points', 0))}\t{mem}\t{_clean(ach.get('Title'))}"
                     f"\t{_clean(ach.get('Description'))}")
    return "\n".join(lines) + "\n"
