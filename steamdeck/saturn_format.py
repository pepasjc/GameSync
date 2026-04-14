"""Steam Deck Saturn helpers backed by the desktop Saturn converter."""

from __future__ import annotations

import sys
from pathlib import Path

DESKTOP_ROOT = Path(__file__).resolve().parents[1] / "desktop"
if str(DESKTOP_ROOT) not in sys.path:
    sys.path.insert(0, str(DESKTOP_ROOT))

from saroo_format import (  # noqa: E402
    convert_saturn_save_format,
    extract_saturn_save_set,
    list_saturn_archive_names,
    merge_saturn_save_set,
    normalize_saturn_save,
)

__all__ = [
    "convert_saturn_save_format",
    "extract_saturn_save_set",
    "list_saturn_archive_names",
    "merge_saturn_save_set",
    "normalize_saturn_save",
]
