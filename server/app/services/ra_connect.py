"""
RetroAchievements "connect" client — the calls an emulator makes.

The DS runs achievements on real hardware (nds-bootstrap-ra) but can't do
HTTPS, so this server does the RA side for it:

* ``fetch_patch``  — a game's achievement definitions (``r=patch``), cached.
* ``award``        — report one unlock (``r=awardachievement``).
* ``login``        — trade a password for a connect token, once (ra_login.py).

These ``dorequest.php`` calls need the user's *connect token*, not the web
API key used for catalog badges.  Request shapes follow rcheevos
``src/rapi/rc_api_runtime.c`` / ``rc_api_user.c``.

Unlocks are always softcore: RA only takes hardcore from approved emulators.
Every unlock is stored here until RA has it; until ``SYNC_RA_SUBMIT`` is on
they wait as "pending" and go out when it is (at startup or next upload).
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
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

_log_lock = threading.Lock()


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


# ---------------------------------------------------------------------------
# Unlocks
# ---------------------------------------------------------------------------


def award_signature(achievement_id: int, username: str, hardcore: bool,
                    seconds_since_unlock: int = 0) -> str:
    """The ``v`` parameter of ``awardachievement``."""
    text = f"{achievement_id}{username}{1 if hardcore else 0}"
    if seconds_since_unlock:
        text += f"{achievement_id}{seconds_since_unlock}"
    return hashlib.md5(text.encode()).hexdigest()


def award(achievement_id: int, md5: str, username: str, token: str,
          seconds_since_unlock: int = 0) -> dict:
    """Send one softcore unlock.  Returns RA's response."""
    params = {
        "r": "awardachievement",
        "u": username,
        "t": token,
        "a": achievement_id,
        "h": 0,
        "m": md5.lower(),
    }
    if seconds_since_unlock:
        params["o"] = seconds_since_unlock
    params["v"] = award_signature(achievement_id, username, False, seconds_since_unlock)
    try:
        return _dorequest(params)
    except RaConnectError as exc:
        # RA reports a repeat unlock as a failure; it is not one for us.
        if "already has" in str(exc).lower():
            return {"Success": True, "AlreadyAwarded": True}
        raise


def unlock_log_path(save_dir: Path) -> Path:
    return save_dir / "ra" / "unlocks.json"


def load_unlocks(save_dir: Path) -> list[dict]:
    path = unlock_log_path(save_dir)
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


RETRYABLE = ("pending", "dry-run", "error")  # "dry-run": older logs


def _save_unlocks(save_dir: Path, log: list[dict]) -> None:
    path = unlock_log_path(save_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(log, indent=1), encoding="utf-8")


def _submit_pending(log: list[dict], username: str, token: str) -> int:
    """Send every stored unlock RA hasn't taken yet; returns how many went."""
    now = int(time.time())
    sent = 0
    for entry in log:
        if entry["status"] not in RETRYABLE:
            continue
        unlocked_at = entry.get("unlocked_at", entry["received_at"] - entry.get("ago", 0))
        try:
            resp = award(entry["id"], entry["md5"], username, token, max(0, now - unlocked_at))
            entry["status"] = "already" if resp.get("AlreadyAwarded") else "submitted"
            entry.pop("detail", None)
            sent += 1
        except Exception as exc:  # noqa: BLE001 - keep the rest going; retried later
            entry["status"] = "error"
            entry["detail"] = str(exc)
    return sent


def submit_pending(save_dir: Path, username: str, token: str) -> int:
    """Send stored unlocks to RA, e.g. once SYNC_RA_SUBMIT is turned on."""
    with _log_lock:
        log = load_unlocks(save_dir)
        sent = _submit_pending(log, username, token)
        if sent or any(e["status"] == "error" for e in log):
            _save_unlocks(save_dir, log)
    return sent


def record_unlocks(save_dir: Path, md5: str, game_id: int, unlocks: list[dict],
                   submit: bool, username: str, token: str) -> list[dict]:
    """Store each new unlock; when ``submit``, send everything pending to RA.

    The server keeps every unlock until RA has it, so once this returns the
    DS can forget them: pending ones go out when submission is turned on
    (see :func:`submit_pending`), failed ones on the next upload.  An
    achievement already stored is reported as a duplicate.  Returns one
    result per unlock.
    """
    with _log_lock:
        log = load_unlocks(save_dir)
        by_id = {e["id"]: e for e in log}
        now = int(time.time())
        fresh: list[tuple[int, dict | None]] = []
        for unlock in unlocks:
            ach_id = int(unlock["id"])
            if ach_id >= WARNING_ACHIEVEMENT_ID:
                fresh.append((ach_id, None))
                continue
            if ach_id in by_id:
                fresh.append((ach_id, {"status": "duplicate"}))
                continue
            ago = max(0, int(unlock.get("ago", 0)))
            entry = {"id": ach_id, "md5": md5.lower(), "game_id": game_id,
                     "received_at": now, "ago": ago, "unlocked_at": now - ago,
                     "status": "pending"}
            log.append(entry)
            by_id[ach_id] = entry
            fresh.append((ach_id, entry))
        if submit and token:
            _submit_pending(log, username, token)
        _save_unlocks(save_dir, log)

    results = []
    for ach_id, entry in fresh:
        if entry is None:
            results.append({"id": ach_id, "status": "ignored"})
        else:
            result = {"id": ach_id, "status": entry["status"]}
            if "detail" in entry:
                result["detail"] = entry["detail"]
            results.append(result)
    return results
