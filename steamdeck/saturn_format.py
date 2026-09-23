"""Steam Deck Saturn helpers backed by the shared Saturn converter."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.saturn_format import (  # noqa: E402
    SATURN_DOWNLOAD_FORMATS,
    convert_saturn_save_format,
    extract_saturn_save_set,
    list_saturn_archive_names,
    merge_saturn_save_set,
    normalize_saturn_save,
)

__all__ = [
    "SATURN_DOWNLOAD_FORMATS",
    "convert_saturn_save_format",
    "extract_saturn_save_set",
    "list_saturn_archive_names",
    "merge_saturn_save_set",
    "normalize_saturn_save",
]
