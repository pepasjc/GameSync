from __future__ import annotations

import json
import re
from pathlib import Path

from app.services import game_names

_archive_map: dict[str, list[str]] = {}
_ARCHIVE_SUFFIX_RE = re.compile(r"^(.*?)(?:_(\d{2,3}))$")


def _title_family_counts() -> dict[str, int]:
    counts: dict[str, set[str]] = {}
    for archive_name, title_ids in _archive_map.items():
        family = archive_family(archive_name)
        for title_id in title_ids:
            counts.setdefault(title_id, set()).add(family)
    return {title_id: len(families) for title_id, families in counts.items()}


def load_seed(path: Path) -> int:
    global _archive_map
    if not path.exists():
        _archive_map = {}
        return 0

    raw = json.loads(path.read_text(encoding="utf-8"))
    normalized: dict[str, list[str]] = {}
    for archive_name, title_ids in raw.items():
        name = archive_name.strip().upper()
        if not name:
            continue
        titles = sorted({tid.strip().upper() for tid in title_ids if tid and tid.strip()})
        if titles:
            normalized[name] = titles

    _archive_map = normalized
    return len(_archive_map)


def _ensure_loaded() -> None:
    if _archive_map:
        return
    default_path = Path(__file__).resolve().parents[2] / "data" / "saturn_archive_names.json"
    load_seed(default_path)


def archive_family(name: str) -> str:
    normalized = name.strip().upper()
    match = _ARCHIVE_SUFFIX_RE.match(normalized)
    return match.group(1) if match else normalized


def lookup_archive_candidates(
    current_title_id: str, archive_names: list[str]
) -> list[dict[str, object]]:
    _ensure_loaded()
    current_title = current_title_id.strip().upper()
    family_counts = _title_family_counts()
    requested_names = [archive_name.strip().upper() for archive_name in archive_names if archive_name.strip()]
    family_to_archives: dict[str, list[str]] = {}
    for archive_name in requested_names:
        family_to_archives.setdefault(archive_family(archive_name), []).append(archive_name)

    candidate_ids = sorted(
        {
            title_id
            for family in family_to_archives
            for archive_name, title_ids in _archive_map.items()
            if archive_family(archive_name) == family
            for title_id in title_ids
        }
    )
    typed_names = game_names.lookup_names_typed(candidate_ids)

    results: list[dict[str, object]] = []
    for family, family_archives in family_to_archives.items():
        candidate_title_ids = sorted(
            {
                title_id
                for archive_name, title_ids in _archive_map.items()
                if archive_family(archive_name) == family
                for title_id in title_ids
            }
        )
        matches_current = current_title in candidate_title_ids
        if matches_current and len(candidate_title_ids) == 1:
            status = "exact_current"
        elif matches_current:
            current_family_count = family_counts.get(current_title, 0)
            other_counts = [
                family_counts.get(title_id, 0)
                for title_id in candidate_title_ids
                if title_id != current_title
            ]
            if current_family_count == 1 and all(count > current_family_count for count in other_counts):
                status = "exact_current"
            else:
                status = "includes_current"
        elif candidate_title_ids:
            status = "other_title"
        else:
            status = "unknown"

        candidates = [
            {
                "title_id": title_id,
                "game_name": typed_names.get(title_id, (title_id, "SAT"))[0],
            }
            for title_id in candidate_title_ids
        ]
        results.append(
            {
                "archive_family": family,
                "archive_names": family_archives,
                "status": status,
                "matches_current_title": matches_current,
                "candidates": candidates,
            }
        )

    return results
