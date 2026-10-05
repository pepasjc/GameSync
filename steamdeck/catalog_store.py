"""The server's ROM catalog, cached on disk and refreshed by difference.

Same strategy as the MiSTer client and the console clients: the server
publishes one fingerprint per system (``GET /roms/fingerprints``), the catalog
is stored per system with the fingerprint it was fetched under, and only the
systems whose fingerprint moved are fetched again.  An unreachable server
leaves the cached copy usable (shown as offline); a server too old to publish
fingerprints is fetched whole and nothing is cached.

Both the Catalog tab and the save scan (which matches local saves against the
catalog) go through :func:`load_catalog`, so opening the app no longer pulls
the whole catalog twice.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import config  # noqa: F401  (puts the repo root on sys.path for 'shared')
from shared.catalog_cache import CatalogCache  # noqa: E402

#: Bump when the meaning of a stored row changes, so an older cache is
#: refetched rather than misread.  Rows are stored whole, as the server sent
#: them.
VERSION = 1

# The Catalog tab and the save scan can both load at start-up, each on its
# own worker thread.  One at a time: the second finds every fingerprint
# fresh and costs a single small request.
_LOCK = threading.Lock()


@dataclass
class CatalogResult:
    rows: list = field(default_factory=list)
    #: The server could not be reached; ``rows`` is the cached copy.
    offline: bool = False
    #: Systems fetched again / taken from the cache unchanged.
    refreshed: int = 0
    unchanged: int = 0
    #: False when the server predates fingerprints (fetched whole, not cached).
    cached: bool = True
    #: Set when nothing could be loaded at all.
    error: str = ""
    #: Non-fatal problems worth telling the user (a refused server rescan).
    notes: list = field(default_factory=list)

    def summary(self) -> str:
        if self.error:
            return f"Catalog failed: {self.error}"
        if self.offline:
            return "Server unreachable - showing the cached catalog"
        if not self.cached:
            return f"{len(self.rows)} ROMs (server has no catalog fingerprints)"
        if self.refreshed:
            plural = "" if self.refreshed == 1 else "s"
            return (f"Catalog: refreshed {self.refreshed} system{plural}, "
                    f"{self.unchanged} unchanged")
        return f"Catalog up to date ({self.unchanged} systems cached)"


def load_catalog(
    client,
    cache_path: Path,
    force: bool = False,
    progress: Optional[Callable[[str], None]] = None,
) -> CatalogResult:
    """Every catalog row the server has, from the cache where it is current.

    ``force`` is the explicit "the library changed" gesture (Settings >
    Refresh catalog): the server is asked to rescan its ROM folder, the cached
    copy is thrown away and every system is fetched again.
    """
    with _LOCK:
        return _load_locked(client, cache_path, force, progress)


def _load_locked(client, cache_path, force, progress) -> CatalogResult:
    def report(text: str) -> None:
        if progress is not None:
            progress(text)

    cache = CatalogCache(str(cache_path), version=VERSION)
    result = CatalogResult()

    if force:
        report("Asking the server to rescan its ROMs…")
        try:
            if client.rescan_roms() is None:
                result.notes.append(
                    "The server did not allow a rescan - refetched anyway")
        except Exception as exc:
            result.notes.append(f"Server rescan failed: {exc}")
        cache.clear()
        cache.save()

    try:
        server = client.rom_fingerprints()
    except Exception as exc:
        if len(cache):
            result.rows = cache.all_rows()
            result.offline = True
            result.unchanged = len(cache.systems())
            return result
        result.error = str(exc) or exc.__class__.__name__
        return result

    try:
        if server is None:
            report("Loading catalog…")
            result.rows = client.list_roms(strict=True)
            result.cached = False
            return result

        fresh, stale = cache.plan(server, sorted(server))
        for index, system in enumerate(stale, 1):
            report(f"Loading catalog… {system} ({index}/{len(stale)})")
            rows = client.list_roms(system, strict=True)
            # The server's system filter also matches aliases; keep exactly
            # this system's rows so no row is cached twice.
            rows = [row for row in rows
                    if str(row.get("system") or "").upper() == system.upper()]
            cache.put(system, str(server[system].get("fingerprint") or ""), rows)
        cache.save()
        result.rows = cache.all_rows()
        result.refreshed = len(stale)
        result.unchanged = len(fresh)
    except Exception as exc:
        # Keep whatever was stored before the failure.
        cache.save()
        if len(cache):
            result.rows = cache.all_rows()
            result.offline = True
            result.unchanged = len(cache.systems())
            result.notes.append(f"Catalog refresh failed: {exc}")
            return result
        result.error = str(exc) or exc.__class__.__name__
    return result


def clear_catalog_cache(cache_path: Path) -> None:
    with _LOCK:
        cache = CatalogCache(str(cache_path), version=VERSION)
        cache.clear()
        cache.save()
