"""
desktop/systems.py — thin shim that re-exports from shared/systems.py.

All definitions live in the repo-root shared/ package so they are shared
with the server and Steam Deck scanner.  Do not add definitions here;
edit shared/systems.py instead.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make the repo root importable so 'shared' can be found.
_REPO_ROOT = str(Path(__file__).parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from shared.systems import (  # noqa: E402
    ALL_CONSOLE_TYPES,
    CD_ALL_EXTENSIONS,
    CD_DATA_EXTENSIONS,
    CD_FOLDER_SYSTEMS,
    COMPANION_EXTENSIONS,
    DEFAULT_SYSTEM_COLOR,
    FOLDER_TO_SYSTEM,
    MEGA_EVERDRIVE_CD_SYSTEMS,
    PSX_RETAIL_PREFIXES,
    ROM_EXTENSIONS,
    SAVE_EXTENSIONS,
    SAVE_EXT_CHOICES,
    SYSTEM_ALIASES,
    SYSTEM_CHOICES,
    SYSTEM_CODES,
    SYSTEM_COLOR,
    SYSTEM_DAT_KEYWORDS,
    SYSTEM_DEFAULT_SAVE_EXT,
)
from shared.mister import (  # noqa: E402
    MISTER_FOLDER_MAP,
    MISTER_FOLDER_TO_SYSTEM,
    MISTER_SYSTEM_TO_FOLDER,
)

__all__ = [
    "ALL_CONSOLE_TYPES",
    "CD_ALL_EXTENSIONS",
    "CD_DATA_EXTENSIONS",
    "CD_FOLDER_SYSTEMS",
    "COMPANION_EXTENSIONS",
    "DEFAULT_SYSTEM_COLOR",
    "FOLDER_TO_SYSTEM",
    "MEGA_EVERDRIVE_CD_SYSTEMS",
    "MISTER_FOLDER_MAP",
    "MISTER_FOLDER_TO_SYSTEM",
    "MISTER_SYSTEM_TO_FOLDER",
    "PSX_RETAIL_PREFIXES",
    "ROM_EXTENSIONS",
    "SAVE_EXTENSIONS",
    "SAVE_EXT_CHOICES",
    "SYSTEM_ALIASES",
    "SYSTEM_CHOICES",
    "SYSTEM_CODES",
    "SYSTEM_COLOR",
    "SYSTEM_DAT_KEYWORDS",
    "SYSTEM_DEFAULT_SAVE_EXT",
]
