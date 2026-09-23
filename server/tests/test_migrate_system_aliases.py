"""Merge of alias-prefixed slug title IDs (GEN_sonic + MD_sonic -> MD_sonic)."""

from app.config import settings
from app.services import db
from migrate_system_aliases import canonical_alias_title_id, migrate


def _make_title(title_id: str, payload: bytes, client_timestamp: int) -> None:
    current = settings.save_dir / title_id / "current"
    current.mkdir(parents=True, exist_ok=True)
    (current / "save.srm").write_bytes(payload)
    db.upsert(
        {
            "title_id": title_id,
            "name": "Test Game",
            "last_sync": f"2026-01-0{client_timestamp % 9 + 1}T00_00_00+00_00",
            "last_sync_source": "retroarch",
            "save_hash": "deadbeef",
            "save_size": len(payload),
            "file_count": 1,
            "client_timestamp": client_timestamp,
            "server_timestamp": f"2026-01-0{client_timestamp % 9 + 1}T00:00:00+00:00",
            "console_id": "test",
            "platform": title_id.split("_", 1)[0],
            "system": title_id.split("_", 1)[0],
        }
    )


def _title_ids() -> set[str]:
    return {r["title_id"] for r in db.list_all()}


def test_canonical_alias_title_id():
    assert canonical_alias_title_id("GEN_sonic_usa") == "MD_sonic_usa"
    assert canonical_alias_title_id("SCD_snatcher_usa") == "SEGACD_snatcher_usa"
    assert canonical_alias_title_id("WS_x") == "WSWAN_x"
    # Already canonical, or not a slug id at all.
    assert canonical_alias_title_id("MD_sonic_usa") is None
    assert canonical_alias_title_id("SLUS01324") is None
    assert canonical_alias_title_id("") is None


def test_dry_run_changes_nothing(tmp_save_dir):
    _make_title("MD_phantasy_star_iv_usa", b"older", 100)
    _make_title("GEN_phantasy_star_iv_usa", b"newer", 200)

    examined, changed = migrate(apply=False)

    assert (examined, changed) == (1, 1)
    assert _title_ids() == {"MD_phantasy_star_iv_usa", "GEN_phantasy_star_iv_usa"}


def test_duplicate_merges_newest_wins_and_loser_archived(tmp_save_dir):
    _make_title("MD_phantasy_star_iv_usa", b"older", 100)
    _make_title("GEN_phantasy_star_iv_usa", b"newer", 200)

    migrate(apply=True)

    assert _title_ids() == {"MD_phantasy_star_iv_usa"}
    current = settings.save_dir / "MD_phantasy_star_iv_usa" / "current"
    assert (current / "save.srm").read_bytes() == b"newer"
    assert not (settings.save_dir / "GEN_phantasy_star_iv_usa").exists()

    # The losing copy is preserved, never deleted.
    history = settings.save_dir / "MD_phantasy_star_iv_usa" / "history"
    archived = [
        p for p in history.rglob("save.srm") if p.read_bytes() == b"older"
    ]
    assert archived, "older save should be archived under history/"


def test_orphan_alias_is_renamed(tmp_save_dir):
    """An alias save with no canonical twin just moves to the right key."""
    _make_title("GEN_thunder_force_iii_japan_usa", b"only-copy", 100)

    migrate(apply=True)

    assert _title_ids() == {"MD_thunder_force_iii_japan_usa"}
    moved = settings.save_dir / "MD_thunder_force_iii_japan_usa" / "current"
    assert (moved / "save.srm").read_bytes() == b"only-copy"
    assert not (settings.save_dir / "GEN_thunder_force_iii_japan_usa").exists()
    row = db.get("MD_thunder_force_iii_japan_usa")
    assert row["system"] == "MD"
    assert row["platform"] == "MD"


def test_migration_is_idempotent(tmp_save_dir):
    _make_title("GEN_golden_axe_iii_japan", b"x", 100)
    migrate(apply=True)

    examined, changed = migrate(apply=False)
    assert (examined, changed) == (0, 0)
