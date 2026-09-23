"""Dolphin GC title IDs canonicalise to uppercase (GC_GRSE, not GC_grse).

The gamecode is case-insensitive — stamped uppercase on the disc — so the
Steam Deck scanner must agree with the GameCube/Wii U homebrew and the server
or the same game is stored twice.
"""

import json

import config
from scanner import dolphin


def _make_gci(emulation_path, code: str, description: str = "MarioKart"):
    card_dir = emulation_path / "saves" / "dolphin-emu" / "GC" / "USA" / "Card A"
    card_dir.mkdir(parents=True, exist_ok=True)
    gci = card_dir / f"01-{code}-{description}.gci"
    gci.write_bytes(b"\x00" * 128)
    return gci


def test_scanner_emits_uppercase_title_id(tmp_path):
    _make_gci(tmp_path, "GM4E")

    entries = list(dolphin.scan(tmp_path))

    assert [e.title_id for e in entries] == ["GC_GM4E"]


def test_state_file_lowercase_keys_are_folded(tmp_path, monkeypatch):
    """Pre-upgrade state must survive, or every GC game shows a conflict."""
    state_path = tmp_path / "steamdeck_state.json"
    state_path.write_text(
        json.dumps({"GC_gm4e": "hash-gc", "GBA_doom": "hash-gba"}), encoding="utf-8"
    )
    monkeypatch.setattr(config, "STATE_PATH", state_path)

    state = config.load_sync_state()

    assert state["GC_GM4E"] == "hash-gc"
    assert "GC_gm4e" not in state
    assert state["GBA_doom"] == "hash-gba"


def test_existing_canonical_entry_wins(tmp_path, monkeypatch):
    state_path = tmp_path / "steamdeck_state.json"
    state_path.write_text(
        json.dumps({"GC_gm4e": "old", "GC_GM4E": "current"}), encoding="utf-8"
    )
    monkeypatch.setattr(config, "STATE_PATH", state_path)

    assert config.load_sync_state() == {"GC_GM4E": "current"}
