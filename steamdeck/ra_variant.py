"""Installed tab: offer the version of a game RetroAchievements supports.

A thin adapter over :mod:`shared.ra_variants` (shared with the MiSTer
client): catalogue rows become one-row games, installed ROMs are matched by
the same loose name key the MiSTer uses.  Multi-disc games are left out on
both sides - the Steam Deck lists each disc on its own, and swapping disc 1
for another region's while disc 2 stays would leave a mixed set.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from shared.ra_variants import VariantIndex
from shared.rom_id import normalize_rom_name

_MULTI_DISC_RE = re.compile(r"\((?:disc|cd|side)\s*\d", re.IGNORECASE)


@dataclass
class CatalogGame:
    system: str
    name: str
    rows: list = field(default_factory=list)


def _is_multi_disc(row: dict) -> bool:
    try:
        if int(row.get("disc_total") or 0) > 1:
            return True
    except (TypeError, ValueError):
        pass
    return bool(_MULTI_DISC_RE.search(str(row.get("name") or "")))


def _name_key(name: str) -> str:
    # a file key: a translation is a different ROM from the retail one
    return normalize_rom_name(name, keep_tags=True)


def find_variants(catalog_rows: Iterable[dict], installed: Iterable) -> dict[str, list[dict]]:
    """``{installed ROM path: [catalogue row, ...]}`` for every installed
    game RA cannot verify that has a verified catalogue version, best first."""
    games = [
        CatalogGame(str(row.get("system") or "").upper(), str(row.get("name") or ""), [row])
        for row in catalog_rows
        if row.get("name") and not _is_multi_disc(row) and not row.get("is_bundle")
    ]
    if not games:
        return {}
    index = VariantIndex(games, _name_key)
    roms = list(installed)
    installed_keys = {((rom.system or "").upper(), _name_key(rom.display_name)) for rom in roms}

    def is_installed(game) -> bool:
        return (game.system, _name_key(game.name)) in installed_keys

    out: dict[str, list[dict]] = {}
    for rom in roms:
        if _MULTI_DISC_RE.search(rom.display_name):
            continue
        found = index.variants((rom.system or "").upper(), rom.display_name,
                               installed=is_installed)
        if found:
            out[str(rom.path)] = [game.rows[0] for game in found]
    return out


def replaced_paths(rom, whole_folder: "Path | None") -> list[str]:
    """What a replacement deletes once the new version is in: the game's
    own folder when it has one, else its file and companion files."""
    if whole_folder is not None:
        return [str(whole_folder)]
    return [str(rom.path)] + [str(p) for p in rom.companion_files]
