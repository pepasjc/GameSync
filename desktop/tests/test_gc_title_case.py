"""GC title IDs are case-insensitive gamecodes and must canonicalise to uppercase.

Dolphin scanners used to emit ``GC_grse`` while the GameCube/Wii U homebrew
and the server emitted ``GC_GRSE``, so the same game synced as two saves.
"""

import json

import sync_engine as se


def test_scanned_gc_title_ids_are_uppercase(monkeypatch, tmp_path):
    """The MemCard Pro scan path builds the title_id from the folder code."""
    assert se.canonicalize_code_form_title_id("GC_grse") == "GC_GRSE"
    assert se.canonicalize_code_form_title_id("GC_GRSE") == "GC_GRSE"


def test_slug_title_ids_are_untouched():
    assert se.canonicalize_code_form_title_id("GBA_zelda_the_minish_cap") == (
        "GBA_zelda_the_minish_cap"
    )
    assert se.canonicalize_code_form_title_id("GBA_doom") == "GBA_doom"


def test_state_file_lowercase_keys_are_folded(monkeypatch, tmp_path):
    """A pre-upgrade state file must not lose its GC last-synced hashes —
    otherwise every GameCube game returns as a spurious conflict."""
    state_file = tmp_path / ".sync_state.json"
    state_file.write_text(
        json.dumps({"GC_grse": "hash-gc", "GBA_doom": "hash-gba"}), encoding="utf-8"
    )
    monkeypatch.setattr(se, "STATE_FILE", state_file)

    state = se._load_state()

    assert state["GC_GRSE"] == "hash-gc"
    assert "GC_grse" not in state
    assert state["GBA_doom"] == "hash-gba"


def test_existing_canonical_entry_wins(monkeypatch, tmp_path):
    state_file = tmp_path / ".sync_state.json"
    state_file.write_text(
        json.dumps({"GC_grse": "old", "GC_GRSE": "current"}), encoding="utf-8"
    )
    monkeypatch.setattr(se, "STATE_FILE", state_file)

    assert se._load_state() == {"GC_GRSE": "current"}


def test_update_state_writes_canonical_key(monkeypatch, tmp_path):
    state_file = tmp_path / ".sync_state.json"
    monkeypatch.setattr(se, "STATE_FILE", state_file)

    se._update_state("GC_grse", "abc")

    assert json.loads(state_file.read_text(encoding="utf-8")) == {"GC_GRSE": "abc"}
