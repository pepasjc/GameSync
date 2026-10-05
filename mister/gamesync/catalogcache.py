"""The ROM catalogue, kept on disk between runs.

The strategy - one fingerprint per system from ``GET /roms/fingerprints``,
refetch only the systems whose fingerprint moved, keep the last copy when the
server cannot be reached - lives in ``shared/catalog_cache.py`` so the Steam
Deck client uses the same code. This module only pins where the MiSTer keeps
the file and which row schema it stores.
"""

from __future__ import annotations

import os

from shared.catalog_cache import CatalogCache as _CatalogCache
from shared.mister import MISTER_CONFIG_DIR

CACHE_PATH = os.path.join(MISTER_CONFIG_DIR, "catalog_cache.json")

#: Bumped when the meaning of a stored row changes (e.g. a field added that
#: every row must carry), so an older cache is refetched rather than misread.
#: 3 added ``ra_achievements`` for the RetroAchievements badge; 4 added
#: ``ra_match``, which tells an exact hash from a title-only match; 5 added
#: ``ra_game_id`` and ``ra_dump`` for offering the version RA supports.
VERSION = 5


class CatalogCache(_CatalogCache):
    """``system -> {fingerprint, rows}``."""

    def __init__(self, path: str = CACHE_PATH):
        super().__init__(path, version=VERSION)
