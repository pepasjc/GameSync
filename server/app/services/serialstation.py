"""Serial Station API client for PlayStation game name lookups.

API: https://api.serialstation.com/v1/title-ids/{title_id}
Response: {"title_id": "...", "name": "...", "systems": [...], ...}

Results are cached in-memory for the lifetime of the server process.
"""

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.serialstation.com/v1/title-ids"
TIMEOUT = 5.0  # seconds per request

# In-memory cache: code -> (name, type) | None
# None means "tried and not found"; absent means "never tried"
_cache: dict[str, tuple[str, str] | None] = {}


def _platform_from_systems(systems: list[str]) -> str:
    """Map Serial Station system names to our platform type strings."""
    if "PlayStation Vita" in systems:
        return "VITA"
    if "PlayStation Portable" in systems:
        return "PSP"
    if "PlayStation 2" in systems:
        return "PS2"
    if "PlayStation" in systems:
        return "PS1"
    return "PSP"  # fallback for unknown PlayStation platform


async def lookup(client: httpx.AsyncClient, code: str) -> tuple[str, str] | None:
    """Look up a single product code. Returns (name, platform_type) or None.

    Uses the shared AsyncClient for connection reuse across batch lookups.
    Results are cached; a None result is cached to avoid re-requesting missing codes.
    """
    if code in _cache:
        return _cache[code]

    try:
        resp = await client.get(f"{BASE_URL}/{code}", timeout=TIMEOUT)
        if resp.status_code == 404:
            _cache[code] = None
            return None
        if resp.status_code != 200:
            # Don't cache transient errors (rate limits, server errors)
            logger.warning("Serial Station returned %d for %s", resp.status_code, code)
            return None

        data = resp.json()
        name = data.get("name", "").strip()
        if not name:
            _cache[code] = None
            return None

        systems = data.get("systems", [])
        platform = _platform_from_systems(systems)
        result = (name, platform)
        _cache[code] = result
        return result

    except (httpx.TimeoutException, httpx.RequestError) as e:
        logger.debug("Serial Station request failed for %s: %s", code, e)
        return None  # Don't cache network failures


async def lookup_batch(codes: list[str]) -> dict[str, tuple[str, str]]:
    """Look up multiple product codes concurrently.

    Returns a dict of code -> (name, platform_type) for codes that were found.
    Codes not found are omitted.
    """
    # Split into cached hits and codes that need fetching
    results: dict[str, tuple[str, str]] = {}
    to_fetch: list[str] = []

    for code in codes:
        if code in _cache:
            if _cache[code] is not None:
                results[code] = _cache[code]  # type: ignore[assignment]
        else:
            to_fetch.append(code)

    if not to_fetch:
        return results

    async with httpx.AsyncClient() as client:
        fetched = await asyncio.gather(
            *(lookup(client, code) for code in to_fetch),
            return_exceptions=False,
        )

    for code, result in zip(to_fetch, fetched):
        if result is not None:
            results[code] = result

    return results
