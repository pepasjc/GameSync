"""Compatibility shim for shared ROM/title-id helpers.

Authoritative implementations now live in ``shared.rom_id`` so desktop, server,
and other Python tools use the same normalization rules.
"""

import sys
from pathlib import Path

# Make the repo root importable so 'shared' can be found.
_REPO_ROOT = str(Path(__file__).parent.parent.parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from shared.rom_id import make_title_id, normalize_rom_name, parse_title_id  # noqa: E402
from shared.systems import FOLDER_TO_SYSTEM, ROM_EXTENSIONS, SYSTEM_CODES  # noqa: E402

__all__ = [
    "FOLDER_TO_SYSTEM",
    "ROM_EXTENSIONS",
    "SYSTEM_CODES",
    "make_title_id",
    "normalize_rom_name",
    "parse_title_id",
]
