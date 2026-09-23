"""
RetroAchievements web client — hash library fetch and on-disk cache.

Shared by the desktop RetroAchievements tab and the server's ROM-catalog
indexer, so there is one copy of "how do we ask RA what it knows".

Two ways in.  Unauthenticated, two ``dorequest.php`` calls per console
(``r=hashlibrary`` → md5 → game id, ``r=gameslist`` → game id → title) — the
same endpoints RAIntegration uses, no account needed.  Or, with a web API
key, one ``API_GetGameList.php`` call per console that also carries each
game's achievement count, which is what lets a caller tell a hash RA merely
knows from one that actually earns achievements.

Either way the result is cached on disk for a day, and a stale cache is
preferred over failing when the network is down.

Hashing rules live next door in :mod:`shared.ra_hash`.

Deliberately standard-library only: the server venv carries httpx rather
than requests, and the MiSTer client has nothing but Python 3.9 itself.
Callers may still inject a ``session`` object (anything with a
``requests``-shaped ``.get()``) — the desktop tests use that to run offline.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

#: Raised for any network-level failure, whichever transport is in play.
NETWORK_ERRORS = (urllib.error.URLError, OSError, ValueError)

_REPO_ROOT = Path(__file__).resolve().parent.parent

RA_BASE_URL = "https://retroachievements.org"
RA_GAME_URL = RA_BASE_URL + "/game/{game_id}"
RA_WEB_API_URL = RA_BASE_URL + "/API/API_GetGameList.php"
CACHE_DIR = _REPO_ROOT / "desktop" / ".ra_cache"
CACHE_MAX_AGE = 24 * 60 * 60
_REQUEST_TIMEOUT = 60


def _user_agent() -> str:
    try:
        version = (_REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        version = "dev"
    return f"GameSync/{version}"


# ---------------------------------------------------------------------------
# RA hash library (per console)
# ---------------------------------------------------------------------------


@dataclass
class RaLibrary:
    console_id: int
    hashes: dict[str, int] = field(default_factory=dict)   # md5 -> game id
    titles: dict[int, str] = field(default_factory=dict)   # game id -> title
    fetched_at: float = 0.0
    # game id -> achievement count; None when the public endpoints were used,
    # which don't expose it.
    achievements: dict[int, int] | None = None

    @property
    def has_achievement_counts(self) -> bool:
        return self.achievements is not None

    def lookup(self, md5: str) -> tuple[int | None, str | None]:
        game_id = self.hashes.get(md5.lower())
        if game_id is None:
            return None, None
        return game_id, self.titles.get(game_id)

    def achievement_count(self, game_id: int) -> int | None:
        if self.achievements is None:
            return None
        return self.achievements.get(game_id, 0)

    def to_json(self) -> dict:
        data = {
            "console_id": self.console_id,
            "fetched_at": self.fetched_at,
            "hashes": self.hashes,
            "titles": {str(k): v for k, v in self.titles.items()},
        }
        if self.achievements is not None:
            data["achievements"] = {str(k): v for k, v in self.achievements.items()}
        return data

    @classmethod
    def from_json(cls, data: dict) -> "RaLibrary":
        achievements = data.get("achievements")
        return cls(
            console_id=int(data["console_id"]),
            hashes={str(k).lower(): int(v) for k, v in data.get("hashes", {}).items()},
            titles={int(k): str(v) for k, v in data.get("titles", {}).items()},
            fetched_at=float(data.get("fetched_at", 0.0)),
            achievements=(
                {int(k): int(v) for k, v in achievements.items()}
                if achievements is not None else None
            ),
        )


def _cache_path(cache_dir: Path, console_id: int, web_api: bool = False) -> Path:
    suffix = "_web" if web_api else ""
    return cache_dir / f"console_{console_id}{suffix}.json"


def load_cached_library(
    console_id: int, cache_dir: Path = CACHE_DIR, web_api: bool = False
) -> RaLibrary | None:
    path = _cache_path(cache_dir, console_id, web_api)
    if not path.exists():
        return None
    try:
        return RaLibrary.from_json(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError):
        return None


class _Response:
    """The little slice of a ``requests`` response this module uses."""

    __slots__ = ("status_code", "_body")

    def __init__(self, status_code: int, body: bytes):
        self.status_code = status_code
        self._body = body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise urllib.error.HTTPError(
                "", self.status_code, f"HTTP {self.status_code}", None, None
            )

    def json(self):
        return json.loads(self._body.decode("utf-8", "replace"))


def _get(url: str, params: dict, session=None) -> _Response:
    """GET ``url`` with query ``params``, via ``session`` when one is given."""
    if session is not None:
        return session.get(
            url,
            params=params,
            headers={"User-Agent": _user_agent()},
            timeout=_REQUEST_TIMEOUT,
        )
    full = url + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(full, headers={"User-Agent": _user_agent()})
    try:
        with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT) as resp:
            return _Response(resp.getcode(), resp.read())
    except urllib.error.HTTPError as exc:
        # A 401/403 body is meaningful to the caller, so it comes back as a
        # response rather than an exception.
        return _Response(exc.code, exc.read() or b"{}")


def _dorequest(session, **params) -> dict:
    resp = _get(RA_BASE_URL + "/dorequest.php", params, session)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("Success"):
        raise RuntimeError(f"RetroAchievements refused {params.get('r')}: {data.get('Error') or data}")
    return data


def download_library(console_id: int, session=None) -> RaLibrary:
    """Pull the full hash library and title list for one RA console (public)."""
    hashes_raw = _dorequest(session, r="hashlibrary", c=console_id).get("MD5List") or {}
    titles_raw = _dorequest(session, r="gameslist", c=console_id).get("Response") or {}
    return RaLibrary(
        console_id=console_id,
        hashes={str(k).lower(): int(v) for k, v in hashes_raw.items()},
        titles={int(k): str(v) for k, v in titles_raw.items()},
        fetched_at=time.time(),
    )


class RaAuthError(RuntimeError):
    """The web API rejected the configured key."""


def download_library_web(
    console_id: int,
    api_key: str,
    username: str = "",
    session=None,
) -> RaLibrary:
    """Same as :func:`download_library` via the authenticated web API.

    ``API_GetGameList.php?h=1`` returns every game on the console with its
    registered hashes and ``NumAchievements`` in one call.
    """
    params = {"i": console_id, "h": 1, "y": api_key}
    if username:
        params["z"] = username
    resp = _get(RA_WEB_API_URL, params, session)
    if resp.status_code in (401, 403):
        raise RaAuthError("RetroAchievements rejected the web API key")
    resp.raise_for_status()
    games = resp.json()
    if not isinstance(games, list):
        raise RuntimeError(f"Unexpected web API response: {str(games)[:200]}")

    library = RaLibrary(console_id=console_id, fetched_at=time.time(), achievements={})
    for game in games:
        try:
            game_id = int(game["ID"])
        except (KeyError, TypeError, ValueError):
            continue
        library.titles[game_id] = str(game.get("Title", ""))
        library.achievements[game_id] = int(game.get("NumAchievements") or 0)
        for md5 in game.get("Hashes") or []:
            library.hashes[str(md5).lower()] = game_id
    return library


def fetch_library(
    console_id: int,
    cache_dir: Path = CACHE_DIR,
    max_age: float = CACHE_MAX_AGE,
    force_refresh: bool = False,
    session=None,
    api_key: str = "",
    username: str = "",
) -> RaLibrary:
    """Cached :func:`download_library` / :func:`download_library_web`.

    A stale cache is still returned when the download fails, so an offline
    scan keeps working against yesterday's data.  A bad key is never papered
    over: :class:`RaAuthError` propagates so the user can fix the config.
    """
    web_api = bool(api_key)
    cached = None if force_refresh else load_cached_library(console_id, cache_dir, web_api)
    if cached is not None and time.time() - cached.fetched_at < max_age:
        return cached

    try:
        if web_api:
            library = download_library_web(console_id, api_key, username, session)
        else:
            library = download_library(console_id, session)
    except RaAuthError:
        raise
    except (RuntimeError,) + NETWORK_ERRORS:
        if cached is not None:
            return cached
        raise

    cache_dir.mkdir(parents=True, exist_ok=True)
    _cache_path(cache_dir, console_id, web_api).write_text(
        json.dumps(library.to_json()), encoding="utf-8"
    )
    return library


# ---------------------------------------------------------------------------
# Registered dump names (per game)
# ---------------------------------------------------------------------------

RA_GAME_HASHES_URL = RA_BASE_URL + "/API/API_GetGameHashes.php"


def _names_path(cache_dir: Path, console_id: int) -> Path:
    return cache_dir / f"console_{console_id}_names.json"


def _write_names(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def fetch_hash_names(
    library: RaLibrary,
    cache_dir: Path = CACHE_DIR,
    api_key: str = "",
    username: str = "",
    session=None,
    delay: float = 0.25,
    should_stop=None,
) -> dict[int, list[str]]:
    """RA's registered file names for each game with achievements.

    Every hash RA accepts carries the name of the dump it came from - a
    Redump / No-Intro name like ``"Big Tournament Golf ~ Neo Turf Masters
    (Japan) (En,Ja)"``.  For systems matched by name rather than by hash,
    that is a far better key than the game's display title, which a Redump
    name often shares no words with.

    One ``API_GetGameHashes.php`` call per game, so the answer is cached per
    game together with how many hashes the library listed for it; a game is
    only asked about again when that number changes (a dump was added).
    Needs the web API key - without one, or offline, whatever is cached is
    returned.
    """
    path = _names_path(cache_dir, library.console_id)
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cached = {}
    counts: dict[int, int] = {}
    for game_id in library.hashes.values():
        counts[game_id] = counts.get(game_id, 0) + 1
    # Subsets are extra sets for a disc indexed under its base game.
    wanted = [g for g, title in library.titles.items()
              if (library.achievement_count(g) or 0) > 0 and "[subset" not in title.lower()]
    stale = [g for g in wanted if cached.get(str(g), {}).get("n") != counts.get(g, 0)]

    if stale and api_key:
        fetched = 0
        for game_id in stale:
            if should_stop and should_stop():
                break
            params = {"i": game_id, "y": api_key}
            if username:
                params["z"] = username
            try:
                resp = _get(RA_GAME_HASHES_URL, params, session)
                if resp.status_code in (401, 403):
                    raise RaAuthError("RetroAchievements rejected the web API key")
                resp.raise_for_status()
                results = resp.json().get("Results") or []
            except RaAuthError:
                raise
            except (RuntimeError, AttributeError) + NETWORK_ERRORS:
                break           # offline or throttled: keep what we have
            cached[str(game_id)] = {
                "n": counts.get(game_id, 0),
                "names": [str(r["Name"]) for r in results if isinstance(r, dict) and r.get("Name")],
            }
            fetched += 1
            if fetched % 50 == 0:
                _write_names(path, cached)
            if delay:
                time.sleep(delay)
        if fetched:
            _write_names(path, cached)

    return {int(k): v.get("names", []) for k, v in cached.items() if int(k) in library.titles}


def clear_cache(cache_dir: Path = CACHE_DIR) -> int:
    """Delete cached libraries; returns how many files went."""
    if not cache_dir.exists():
        return 0
    removed = 0
    for path in cache_dir.glob("console_*.json"):
        path.unlink()
        removed += 1
    return removed
