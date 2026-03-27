from pathlib import Path

import sync_engine as se


class DummyResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_compare_with_server_uses_titles_index_once(monkeypatch, tmp_path):
    save_path = tmp_path / "Advance Wars.sav"
    save_path.write_bytes(b"local")

    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append(url)
        assert url.endswith("/api/v1/titles")
        return DummyResponse({
            "titles": [{
                "title_id": "GBA_advance_wars_usa",
                "name": "Advance Wars (USA)",
                "system": "GBA",
                "save_hash": "server-hash",
                "server_timestamp": "2026-03-23T00:00:00Z",
            }]
        })

    monkeypatch.setattr(se.requests, "get", fake_get)
    monkeypatch.setattr(se, "_load_state", lambda: {"GBA_advance_wars_usa": "server-hash"})
    monkeypatch.setattr(se, "SLOT_MAPPING_FILE", tmp_path / ".slot_mappings.json")
    monkeypatch.setattr(se, "_SLOT_MAPPINGS", None)
    monkeypatch.setattr(se, "_SLOT_MAPPINGS_DIRTY", False)

    statuses = se.compare_with_server([
        se.SaveFile(
            title_id="GBA_advance_wars_usa",
            path=Path(save_path),
            hash="local-hash",
            mtime=save_path.stat().st_mtime,
            system="GBA",
            game_name="Advance Wars",
            save_exists=True,
        )
    ], "http://example", {"X-API-Key": "x"})

    assert calls == ["http://example/api/v1/titles"]
    assert len(statuses) == 1
    assert statuses[0].status == "local_newer"


def test_compare_with_server_prefers_existing_legacy_slot(monkeypatch, tmp_path):
    save_path = tmp_path / "Advance Wars.sav"
    save_path.write_bytes(b"local")

    def fake_get(url, headers=None, timeout=None):
        return DummyResponse({
            "titles": [{
                "title_id": "GBA_advance_wars",
                "name": "Advance Wars",
                "system": "GBA",
                "save_hash": "legacy-hash",
                "server_timestamp": "2026-03-23T00:00:00Z",
            }]
        })

    monkeypatch.setattr(se.requests, "get", fake_get)
    monkeypatch.setattr(se, "_load_state", lambda: {"GBA_advance_wars": "legacy-hash"})
    monkeypatch.setattr(se, "SLOT_MAPPING_FILE", tmp_path / ".slot_mappings.json")
    monkeypatch.setattr(se, "_SLOT_MAPPINGS", None)
    monkeypatch.setattr(se, "_SLOT_MAPPINGS_DIRTY", False)

    statuses = se.compare_with_server([
        se.SaveFile(
            title_id="GBA_advance_wars_usa",
            path=Path(save_path),
            hash="local-hash",
            mtime=save_path.stat().st_mtime,
            system="GBA",
            game_name="Advance Wars",
            save_exists=True,
            legacy_title_id="GBA_advance_wars",
            canonical_title_id="GBA_advance_wars_usa",
            title_id_source="fuzzy",
            title_id_confidence="low",
        )
    ], "http://example", {"X-API-Key": "x"})

    assert len(statuses) == 1
    assert statuses[0].save.title_id == "GBA_advance_wars"
    assert statuses[0].status == "local_newer"


def test_compare_with_server_flags_when_both_legacy_and_canonical_exist(monkeypatch, tmp_path):
    save_path = tmp_path / "Advance Wars.sav"
    save_path.write_bytes(b"local")

    def fake_get(url, headers=None, timeout=None):
        return DummyResponse({
            "titles": [
                {
                    "title_id": "GBA_advance_wars",
                    "name": "Advance Wars",
                    "system": "GBA",
                    "save_hash": "legacy-hash",
                    "server_timestamp": "2026-03-23T00:00:00Z",
                },
                {
                    "title_id": "GBA_advance_wars_usa",
                    "name": "Advance Wars (USA)",
                    "system": "GBA",
                    "save_hash": "canonical-hash",
                    "server_timestamp": "2026-03-23T00:00:00Z",
                },
            ]
        })

    monkeypatch.setattr(se.requests, "get", fake_get)
    monkeypatch.setattr(se, "_load_state", lambda: {})
    monkeypatch.setattr(se, "SLOT_MAPPING_FILE", tmp_path / ".slot_mappings.json")
    monkeypatch.setattr(se, "_SLOT_MAPPINGS", None)
    monkeypatch.setattr(se, "_SLOT_MAPPINGS_DIRTY", False)

    statuses = se.compare_with_server([
        se.SaveFile(
            title_id="GBA_advance_wars_usa",
            path=Path(save_path),
            hash="local-hash",
            mtime=save_path.stat().st_mtime,
            system="GBA",
            game_name="Advance Wars",
            save_exists=True,
            legacy_title_id="GBA_advance_wars",
            canonical_title_id="GBA_advance_wars_usa",
            title_id_source="fuzzy",
            title_id_confidence="low",
        )
    ], "http://example", {"X-API-Key": "x"})

    assert len(statuses) == 1
    assert statuses[0].status == "mapping_conflict"
    assert "Both legacy and canonical server slots already exist" in statuses[0].mapping_note


def test_compare_with_server_prefers_canonical_for_new_unseen_games(monkeypatch, tmp_path):
    save_path = tmp_path / "Advance Wars.sav"
    save_path.write_bytes(b"local")

    def fake_get(url, headers=None, timeout=None):
        return DummyResponse({"titles": []})

    monkeypatch.setattr(se.requests, "get", fake_get)
    monkeypatch.setattr(se, "_load_state", lambda: {})
    monkeypatch.setattr(se, "SLOT_MAPPING_FILE", tmp_path / ".slot_mappings.json")
    monkeypatch.setattr(se, "_SLOT_MAPPINGS", None)
    monkeypatch.setattr(se, "_SLOT_MAPPINGS_DIRTY", False)

    statuses = se.compare_with_server([
        se.SaveFile(
            title_id="GBA_advance_wars_usa",
            path=Path(save_path),
            hash="local-hash",
            mtime=save_path.stat().st_mtime,
            system="GBA",
            game_name="Advance Wars",
            save_exists=True,
            legacy_title_id="GBA_advance_wars",
            canonical_title_id="GBA_advance_wars_usa",
            title_id_source="fuzzy",
            title_id_confidence="low",
        )
    ], "http://example", {"X-API-Key": "x"})

    assert len(statuses) == 1
    assert statuses[0].save.title_id == "GBA_advance_wars_usa"
    assert statuses[0].status == "not_on_server"


def test_compare_with_server_ignores_stale_legacy_mapping_when_server_slot_missing(monkeypatch, tmp_path):
    save_path = tmp_path / "Zelda-Minish Cap.sav"
    save_path.write_bytes(b"local")
    mapping_path = tmp_path / ".slot_mappings.json"
    mapping_path.write_text(
        """
{
  "entries": {
    "%s": {
      "effective_title_id": "GBA_zelda_minish_cap",
      "legacy_title_id": "GBA_zelda_minish_cap",
      "canonical_title_id": "GBA_legend_of_zelda_the_the_minish_cap_usa"
    }
  }
}
""" % str(save_path.resolve()).replace("\\", "\\\\"),
        encoding="utf-8",
    )

    def fake_get(url, headers=None, timeout=None):
        return DummyResponse({"titles": []})

    monkeypatch.setattr(se.requests, "get", fake_get)
    monkeypatch.setattr(se, "_load_state", lambda: {})
    monkeypatch.setattr(se, "SLOT_MAPPING_FILE", mapping_path)
    monkeypatch.setattr(se, "_SLOT_MAPPINGS", None)
    monkeypatch.setattr(se, "_SLOT_MAPPINGS_DIRTY", False)

    statuses = se.compare_with_server([
        se.SaveFile(
            title_id="GBA_legend_of_zelda_the_the_minish_cap_usa",
            path=Path(save_path),
            hash="local-hash",
            mtime=save_path.stat().st_mtime,
            system="GBA",
            game_name="Zelda-Minish Cap",
            save_exists=True,
            legacy_title_id="GBA_zelda_minish_cap",
            canonical_title_id="GBA_legend_of_zelda_the_the_minish_cap_usa",
            title_id_source="fuzzy",
            title_id_confidence="low",
        )
    ], "http://example", {"X-API-Key": "x"})

    assert len(statuses) == 1
    assert statuses[0].save.title_id == "GBA_legend_of_zelda_the_the_minish_cap_usa"


def test_clear_slot_mappings_removes_mapping_file(monkeypatch, tmp_path):
    mapping_path = tmp_path / ".slot_mappings.json"
    mapping_path.write_text('{"entries":{"x":{"effective_title_id":"a"}}}', encoding="utf-8")

    monkeypatch.setattr(se, "SLOT_MAPPING_FILE", mapping_path)
    monkeypatch.setattr(se, "_SLOT_MAPPINGS", {"x": {"effective_title_id": "a"}})
    monkeypatch.setattr(se, "_SLOT_MAPPINGS_DIRTY", False)

    se.clear_slot_mappings()

    assert not mapping_path.exists()
    assert se._load_slot_mappings() == {}


def test_compare_with_server_flags_differing_duplicate_local_saves(monkeypatch, tmp_path):
    primary = tmp_path / "all" / "japan" / "Guru Logic Champ (Japan).sav"
    alternate = tmp_path / "favorites" / "japan" / "Guru Logic Champ (Japan).sav"
    primary.parent.mkdir(parents=True)
    alternate.parent.mkdir(parents=True)
    primary.write_bytes(b"one")
    alternate.write_bytes(b"two")

    def fake_get(url, headers=None, timeout=None):
        return DummyResponse({
            "titles": [{
                "title_id": "GBA_guru_logic_champ_japan",
                "name": "Guru Logic Champ (Japan)",
                "system": "GBA",
                "save_hash": "server-hash",
                "server_timestamp": "2026-03-23T00:00:00Z",
            }]
        })

    monkeypatch.setattr(se.requests, "get", fake_get)
    monkeypatch.setattr(se, "_load_state", lambda: {"GBA_guru_logic_champ_japan": "server-hash"})
    monkeypatch.setattr(se, "SLOT_MAPPING_FILE", tmp_path / ".slot_mappings.json")
    monkeypatch.setattr(se, "_SLOT_MAPPINGS", None)
    monkeypatch.setattr(se, "_SLOT_MAPPINGS_DIRTY", False)

    statuses = se.compare_with_server([
        se.SaveFile(
            title_id="GBA_guru_logic_champ_japan",
            path=primary,
            hash=se._hash_file(primary),
            mtime=primary.stat().st_mtime,
            system="GBA",
            game_name="Guru Logic Champ",
            save_exists=True,
            alternate_paths=[alternate],
        )
    ], "http://example", {"X-API-Key": "x"})

    assert len(statuses) == 1
    assert statuses[0].status == "local_duplicate_conflict"
    assert "Multiple local save copies differ" in statuses[0].mapping_note


def test_compare_with_server_allows_identical_duplicate_local_saves(monkeypatch, tmp_path):
    primary = tmp_path / "all" / "japan" / "Guru Logic Champ (Japan).sav"
    alternate = tmp_path / "favorites" / "japan" / "Guru Logic Champ (Japan).sav"
    primary.parent.mkdir(parents=True)
    alternate.parent.mkdir(parents=True)
    primary.write_bytes(b"same")
    alternate.write_bytes(b"same")

    def fake_get(url, headers=None, timeout=None):
        return DummyResponse({
            "titles": [{
                "title_id": "GBA_guru_logic_champ_japan",
                "name": "Guru Logic Champ (Japan)",
                "system": "GBA",
                "save_hash": "server-hash",
                "server_timestamp": "2026-03-23T00:00:00Z",
            }]
        })

    monkeypatch.setattr(se.requests, "get", fake_get)
    monkeypatch.setattr(se, "_load_state", lambda: {"GBA_guru_logic_champ_japan": "server-hash"})
    monkeypatch.setattr(se, "SLOT_MAPPING_FILE", tmp_path / ".slot_mappings.json")
    monkeypatch.setattr(se, "_SLOT_MAPPINGS", None)
    monkeypatch.setattr(se, "_SLOT_MAPPINGS_DIRTY", False)

    statuses = se.compare_with_server([
        se.SaveFile(
            title_id="GBA_guru_logic_champ_japan",
            path=primary,
            hash=se._hash_file(primary),
            mtime=primary.stat().st_mtime,
            system="GBA",
            game_name="Guru Logic Champ",
            save_exists=True,
            alternate_paths=[alternate],
        )
    ], "http://example", {"X-API-Key": "x"})

    assert len(statuses) == 1
    assert statuses[0].status == "local_newer"


def test_slot_mappings_are_scoped_per_profile(monkeypatch, tmp_path):
    save_path = tmp_path / "Advance Wars.sav"
    save_path.write_bytes(b"local")

    monkeypatch.setattr(se, "SLOT_MAPPING_FILE", tmp_path / ".slot_mappings.json")
    monkeypatch.setattr(se, "_SLOT_MAPPINGS", None)
    monkeypatch.setattr(se, "_SLOT_MAPPINGS_DIRTY", False)

    save_a = se.SaveFile(
        title_id="GBA_advance_wars_usa",
        path=save_path,
        hash="hash-a",
        mtime=save_path.stat().st_mtime,
        system="GBA",
        game_name="Advance Wars",
        profile_scope="profile-a",
    )
    save_b = se.SaveFile(
        title_id="GBA_advance_wars",
        path=save_path,
        hash="hash-b",
        mtime=save_path.stat().st_mtime,
        system="GBA",
        game_name="Advance Wars",
        profile_scope="profile-b",
    )

    se._set_slot_mapping(save_a, "GBA_advance_wars_usa")
    se._set_slot_mapping(save_b, "GBA_advance_wars")

    assert se._get_slot_mapping(save_a)["effective_title_id"] == "GBA_advance_wars_usa"
    assert se._get_slot_mapping(save_b)["effective_title_id"] == "GBA_advance_wars"
