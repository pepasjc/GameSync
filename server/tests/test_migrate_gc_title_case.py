"""Merge of case-variant GC title IDs (GC_grse + GC_GRSE -> GC_GRSE)."""

import json

from app.config import settings
from app.services import db
from migrate_gc_title_case import migrate


def _make_title(title_id: str, payload: bytes, client_timestamp: int) -> None:
    """Create a save directory + DB row the way the server would have."""
    current = settings.save_dir / title_id / "current"
    current.mkdir(parents=True, exist_ok=True)
    (current / "card.raw").write_bytes(payload)
    db.upsert(
        {
            "title_id": title_id,
            "name": "Test Game",
            "last_sync": f"2026-01-0{client_timestamp % 9 + 1}T00_00_00+00_00",
            "last_sync_source": "gc",
            "save_hash": "deadbeef",
            "save_size": len(payload),
            "file_count": 1,
            "client_timestamp": client_timestamp,
            "server_timestamp": f"2026-01-0{client_timestamp % 9 + 1}T00:00:00+00:00",
            "console_id": "test",
            "platform": "GC",
            "system": "GC",
        }
    )


def _title_ids() -> set[str]:
    return {r["title_id"] for r in db.list_all()}


def test_dry_run_changes_nothing(tmp_save_dir):
    _make_title("GC_GRSE", b"upper", 100)
    _make_title("GC_grse", b"lower", 200)

    examined, changed = migrate(apply=False)

    assert examined == 1
    assert changed == 1
    assert _title_ids() == {"GC_GRSE", "GC_grse"}


def test_merge_keeps_newest_and_archives_loser(tmp_save_dir):
    _make_title("GC_GRSE", b"older-save", 100)
    _make_title("GC_grse", b"newer-save", 200)
    same_dir = (settings.save_dir / "GC_GRSE").samefile(settings.save_dir / "GC_grse")

    migrate(apply=True)

    # One canonical row remains.
    assert _title_ids() == {"GC_GRSE"}

    current = settings.save_dir / "GC_GRSE" / "current"
    assert current.is_dir()

    if same_dir:
        # Case-insensitive filesystem (Windows/macOS): both writes hit one
        # directory, so there is only a stale DB row to drop.
        return

    # Case-sensitive filesystem: the newer save wins, the older is preserved.
    assert (current / "card.raw").read_bytes() == b"newer-save"
    assert not (settings.save_dir / "GC_grse").exists()

    archived = [
        p
        for p in (settings.save_dir / "GC_GRSE" / "history").iterdir()
        if (p / "card.raw").read_bytes() == b"older-save"
    ]
    assert archived, "losing variant must be archived to history, not deleted"


def test_lone_lowercase_title_is_renamed(tmp_save_dir):
    _make_title("GC_gm8e", b"save", 100)

    examined, changed = migrate(apply=True)

    assert (examined, changed) == (1, 1)
    assert _title_ids() == {"GC_GM8E"}
    assert (settings.save_dir / "GC_GM8E" / "current" / "card.raw").read_bytes() == b"save"


def test_metadata_json_title_id_is_rewritten(tmp_save_dir):
    _make_title("GC_gm8e", b"save", 100)
    meta_path = settings.save_dir / "GC_gm8e" / "metadata.json"
    meta_path.write_text(json.dumps({"title_id": "GC_gm8e", "name": "Test Game"}))

    migrate(apply=True)

    data = json.loads((settings.save_dir / "GC_GM8E" / "metadata.json").read_text())
    assert data["title_id"] == "GC_GM8E"


def test_is_idempotent(tmp_save_dir):
    _make_title("GC_gm8e", b"save", 100)
    migrate(apply=True)

    examined, changed = migrate(apply=True)

    assert changed == 0
    assert _title_ids() == {"GC_GM8E"}


def test_slug_titles_are_left_alone(tmp_save_dir):
    _make_title("GBA_zelda_the_minish_cap", b"save", 100)
    _make_title("GBA_doom", b"save", 100)

    examined, changed = migrate(apply=True)

    assert (examined, changed) == (0, 0)
    assert _title_ids() == {"GBA_zelda_the_minish_cap", "GBA_doom"}
