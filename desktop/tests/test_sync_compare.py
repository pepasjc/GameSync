import hashlib
import os
from pathlib import Path

import sync_engine as se
from saroo_format import (
    ArchiveEntry,
    GameSlot,
    SLOT_SIZE,
    build_ss_save_bin,
    saroo_slot_to_mednafen,
)


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


def test_scan_memcard_pro_ps1_uses_slot_1_only(tmp_path):
    root = tmp_path / "MemoryCards"
    game_dir = root / "SLUS-00594"
    game_dir.mkdir(parents=True)
    (game_dir / "SLUS-00594.txt").write_text("Dino Crisis 2", encoding="utf-8")
    (game_dir / "SLUS-00594-1.mcd").write_bytes(b"slot1")
    (game_dir / "SLUS-00594-2.mcd").write_bytes(b"slot2")

    results = se._scan_memcard_pro(root)

    assert len(results) == 1
    assert results[0].title_id == "SLUS00594"
    assert results[0].path == game_dir / "SLUS-00594-1.mcd"
    assert results[0].system == "PS1"


def test_scan_memcard_pro_accepts_card_root_and_skips_shared_cards(tmp_path):
    root = tmp_path
    memcards = root / "MemoryCards"
    shared_dir = memcards / "MemoryCard1"
    game_dir = memcards / "SCUS-94455"
    shared_dir.mkdir(parents=True)
    game_dir.mkdir(parents=True)

    (shared_dir / "MemoryCard1-1.mcd").write_bytes(b"shared")
    (game_dir / "SCUS-94455-1.mcd").write_bytes(b"game")

    results = se._scan_memcard_pro(root)

    assert len(results) == 1
    assert results[0].title_id == "SCUS94455"
    assert results[0].path == game_dir / "SCUS-94455-1.mcd"


def test_scan_memcard_pro_ps2_uses_name_txt_and_slot_1(tmp_path):
    root = tmp_path / "PS2"
    game_dir = root / "SLUS-20002"
    game_dir.mkdir(parents=True)
    (game_dir / "name.txt").write_text("Dynasty Warriors", encoding="utf-8")
    (game_dir / "SLUS-20002-1.mc2").write_bytes(b"slot1")
    (game_dir / "SLUS-20002-2.mc2").write_bytes(b"slot2")

    results = se._scan_memcard_pro(root, "PS2")

    assert len(results) == 1
    assert results[0].title_id == "SLUS20002"
    assert results[0].path == game_dir / "SLUS-20002-1.mc2"
    assert results[0].system == "PS2"
    assert results[0].game_name == "Dynasty Warriors"


def test_upload_save_uses_ps1_card_endpoint_for_ps1_titles(monkeypatch, tmp_path):
    card = tmp_path / "SLUS-00594-1.mcd"
    card.write_bytes(b"ps1-card")
    calls = []

    class PostResponse:
        def raise_for_status(self):
            return None

    def fake_post(url, headers=None, params=None, data=None, timeout=None):
        calls.append((url, params, data))
        return PostResponse()

    monkeypatch.setattr(se.requests, "post", fake_post)
    monkeypatch.setattr(se, "_update_state", lambda title_id, hash_val: None)

    se.upload_save("SLUS00594", card, "http://example", {"X-API-Key": "x"})

    assert calls == [
        ("http://example/api/v1/saves/SLUS00594/ps1-card", {}, b"ps1-card")
    ]


def test_download_save_uses_ps1_card_endpoint_for_ps1_titles(monkeypatch, tmp_path):
    dest = tmp_path / "SLUS-00594-1.mcd"
    calls = []

    class GetResponse:
        content = b"downloaded-card"
        headers = {"X-Save-Hash": "server-ps1-hash"}

        def raise_for_status(self):
            return None

    def fake_get(url, headers=None, params=None, timeout=None):
        calls.append((url, params))
        return GetResponse()

    monkeypatch.setattr(se.requests, "get", fake_get)
    monkeypatch.setattr(se, "_update_state", lambda title_id, hash_val: None)

    server_hash = se.download_save("SLUS00594", dest, "http://example", {"X-API-Key": "x"})

    assert calls == [
        ("http://example/api/v1/saves/SLUS00594/ps1-card", {"slot": 0})
    ]
    assert dest.read_bytes() == b"downloaded-card"
    assert server_hash == "server-ps1-hash"


def test_upload_save_uses_raw_endpoint_for_ps2_titles(monkeypatch, tmp_path):
    card = tmp_path / "SLUS-20002-1.mc2"
    card.write_bytes(b"ps2-card")
    calls = []

    class PostResponse:
        def raise_for_status(self):
            return None

    def fake_post(url, headers=None, params=None, data=None, timeout=None):
        calls.append((url, params, data))
        return PostResponse()

    monkeypatch.setattr(se.requests, "post", fake_post)
    monkeypatch.setattr(se, "_update_state", lambda title_id, hash_val: None)

    se.upload_save("SLUS20002", card, "http://example", {"X-API-Key": "x"}, system="PS2")

    assert calls == [
        ("http://example/api/v1/saves/SLUS20002/ps2-card", {}, b"ps2-card")
    ]


def test_upload_save_uses_saroo_per_game_payload_not_full_container(monkeypatch, tmp_path):
    ss_save = tmp_path / "SS_SAVE.BIN"
    ss_save.write_bytes(b"x" * (8 * 1024 * 1024))
    calls = []

    class PostResponse:
        def raise_for_status(self):
            return None

    def fake_post(url, headers=None, params=None, data=None, timeout=None):
        calls.append((url, params, data))
        return PostResponse()

    monkeypatch.setattr(se.requests, "post", fake_post)
    monkeypatch.setattr(se, "_update_state", lambda title_id, hash_val: None)
    monkeypatch.setattr(
        se,
        "_SAROO_META",
        {
            "SAT_T12705H": {
                "game_id": "T-12705H",
                "slot_index": 0x10000,
                "native_bytes": b"per-game-save",
                "bkr_path": "",
            }
        },
    )

    se.upload_save(
        "SAT_T12705H",
        ss_save,
        "http://example",
        {"X-API-Key": "x"},
        system="SAT",
    )

    assert calls == [
        ("http://example/api/v1/saves/SAT_T12705H/raw", {}, b"per-game-save")
    ]


def test_upload_save_prefers_newer_saroo_bkr_over_container(monkeypatch, tmp_path):
    ss_save = tmp_path / "SS_SAVE.BIN"
    ss_save.write_bytes(b"x" * (8 * 1024 * 1024))
    bkr = tmp_path / "T12705H.bkr"
    bkr.write_bytes(b"newer-bkr")
    os.utime(ss_save, (1000, 1000))
    os.utime(bkr, (2000, 2000))
    calls = []

    class PostResponse:
        def raise_for_status(self):
            return None

    def fake_post(url, headers=None, params=None, data=None, timeout=None):
        calls.append((url, params, data))
        return PostResponse()

    monkeypatch.setattr(se.requests, "post", fake_post)
    monkeypatch.setattr(se, "_update_state", lambda title_id, hash_val: None)
    monkeypatch.setattr(
        se,
        "_SAROO_META",
        {
            "SAT_T12705H": {
                "game_id": "T-12705H",
                "slot_index": 0x10000,
                "native_bytes": b"older-slot",
                "bkr_path": str(bkr),
            }
        },
    )

    se.upload_save(
        "SAT_T12705H",
        ss_save,
        "http://example",
        {"X-API-Key": "x"},
        system="SAT",
    )

    assert calls == [
        ("http://example/api/v1/saves/SAT_T12705H/raw", {}, b"newer-bkr")
    ]


def test_scan_saroo_preserves_physical_slot_offsets_when_invalid_slots_are_skipped(
    monkeypatch, tmp_path
):
    saroo_root = tmp_path / "saroo"
    saroo_root.mkdir()

    def make_slot(game_id, save_name, payload, comment="SAVE"):
        return GameSlot(
            game_id=game_id,
            saves=[
                ArchiveEntry(
                    name=save_name,
                    comment=comment,
                    language_code=0,
                    date_code=1,
                    raw_data=payload,
                )
            ],
        )

    first_slot = make_slot("T-0001G   V1.000", "FIRSTSAVE_1", b"A" * 256)
    invalid_reserved_slot = make_slot("T-0002G   V1.000", "BROKEN___01", b"B" * 256)
    dracula_slot = make_slot("T-9527G   V1.400", "DRACULAX_01", b"D" * 4388)
    grandia_slot = make_slot("T-4507G   V1.002", "GRANDIA_001", b"G" * 3040)

    ss_data = bytearray(
        build_ss_save_bin(
            [first_slot, invalid_reserved_slot, dracula_slot, grandia_slot]
        )
    )
    ss_data[2 * SLOT_SIZE : 3 * SLOT_SIZE] = b"\x00" * SLOT_SIZE
    ss_save = saroo_root / "SS_SAVE.BIN"
    ss_save.write_bytes(ss_data)

    monkeypatch.setattr(se, "_SAROO_META", {})

    results = se._scan_saroo(saroo_root, None)

    grandia_result = next(result for result in results if result.title_id == "SAT_T-4507G")
    grandia_meta = se._SAROO_META["SAT_T-4507G"]

    expected_grandia = saroo_slot_to_mednafen(
        bytes(ss_data[4 * SLOT_SIZE : 5 * SLOT_SIZE])
    )
    wrong_dracula = saroo_slot_to_mednafen(bytes(ss_data[3 * SLOT_SIZE : 4 * SLOT_SIZE]))

    assert grandia_result.hash == hashlib.sha256(expected_grandia).hexdigest()
    assert grandia_meta["slot_index"] == 4 * SLOT_SIZE
    assert grandia_meta["native_bytes"] == expected_grandia
    assert grandia_meta["native_bytes"] != wrong_dracula


def test_download_save_uses_raw_endpoint_for_ps2_titles(monkeypatch, tmp_path):
    dest = tmp_path / "SLUS-20002-1.mc2"
    calls = []

    class GetResponse:
        content = b"downloaded-ps2-card"
        headers = {"X-Save-Hash": "server-ps2-hash"}

        def raise_for_status(self):
            return None

    def fake_get(url, headers=None, params=None, timeout=None):
        calls.append((url, params))
        return GetResponse()

    monkeypatch.setattr(se.requests, "get", fake_get)
    monkeypatch.setattr(se, "_update_state", lambda title_id, hash_val: None)

    server_hash = se.download_save("SLUS20002", dest, "http://example", {"X-API-Key": "x"}, system="PS2")

    assert calls == [
        ("http://example/api/v1/saves/SLUS20002/ps2-card", None)
    ]
    assert dest.read_bytes() == b"downloaded-ps2-card"
    assert server_hash == "server-ps2-hash"


def test_compare_with_server_uses_ps1_card_meta_for_ps1_titles(monkeypatch, tmp_path):
    card = tmp_path / "SLUS-00594-1.mcd"
    card.write_bytes(b"ps1-card")
    local_hash = se._hash_file(card)
    calls = []

    def fake_get(url, headers=None, params=None, timeout=None):
        calls.append((url, params))
        if url.endswith("/api/v1/titles"):
            return DummyResponse({
                "titles": [{
                    "title_id": "SLUS00594",
                    "name": "Dino Crisis 2 (USA)",
                    "system": "PS1",
                    "save_hash": "generic-psp-visible-hash",
                    "server_timestamp": "2026-03-23T00:00:00Z",
                }]
            })
        if url.endswith("/api/v1/saves/SLUS00594/ps1-card/meta"):
            return DummyResponse({
                "title_id": "SLUS00594",
                "save_hash": local_hash,
                "server_timestamp": "2026-03-24T00:00:00Z",
            })
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(se.requests, "get", fake_get)
    monkeypatch.setattr(se, "_load_state", lambda: {"SLUS00594": local_hash})
    monkeypatch.setattr(se, "SLOT_MAPPING_FILE", tmp_path / ".slot_mappings.json")
    monkeypatch.setattr(se, "_SLOT_MAPPINGS", None)
    monkeypatch.setattr(se, "_SLOT_MAPPINGS_DIRTY", False)

    statuses = se.compare_with_server([
        se.SaveFile(
            title_id="SLUS00594",
            path=card,
            hash=local_hash,
            mtime=card.stat().st_mtime,
            system="PS1",
            game_name="Dino Crisis 2",
            save_exists=True,
        )
    ], "http://example", {"X-API-Key": "x"})

    assert len(statuses) == 1
    assert statuses[0].status == "up_to_date"
    assert statuses[0].server_hash == local_hash
    assert ("http://example/api/v1/saves/SLUS00594/ps1-card/meta", {"slot": 0}) in calls


def test_compare_with_server_remaps_saturn_slug_title_id_by_unique_hash_match(
    monkeypatch, tmp_path
):
    save_path = tmp_path / "Grandia (Japan).bkr"
    save_path.write_bytes(b"grandia")
    local_hash = se._hash_file(save_path)

    def fake_get(url, headers=None, params=None, timeout=None):
        if url.endswith("/api/v1/titles"):
            return DummyResponse(
                {
                    "titles": [
                        {
                            "title_id": "SAT_T-4507G",
                            "name": "Grandia (Japan) (Disc 1) (4M)",
                            "system": "SAT",
                            "save_hash": local_hash,
                            "server_timestamp": "2026-04-13T00:00:00Z",
                        }
                    ]
                }
            )
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(se.requests, "get", fake_get)
    monkeypatch.setattr(se, "_load_state", lambda: {})
    monkeypatch.setattr(se, "SLOT_MAPPING_FILE", tmp_path / ".slot_mappings.json")
    monkeypatch.setattr(se, "_SLOT_MAPPINGS", None)
    monkeypatch.setattr(se, "_SLOT_MAPPINGS_DIRTY", False)
    monkeypatch.setattr(
        se,
        "_canonical_saturn_payload",
        lambda *args, **kwargs: (save_path.read_bytes(), []),
    )

    statuses = se.compare_with_server(
        [
            se.SaveFile(
                title_id="SAT_grandia_japan",
                path=save_path,
                hash=local_hash,
                mtime=save_path.stat().st_mtime,
                system="SAT",
                game_name="Grandia (Japan)",
                save_exists=True,
            )
        ],
        "http://example",
        {"X-API-Key": "x"},
    )

    assert len(statuses) == 1
    assert statuses[0].save.title_id == "SAT_T-4507G"
    assert statuses[0].status == "up_to_date"
    assert statuses[0].server_hash == local_hash


def test_scan_emudeck_rpcs3_uses_full_folder_name_as_title_id(tmp_path):
    root = tmp_path / "Emulation"
    save_dir = root / "rpcs3" / "saves" / "BLUS30464-AUTOSAVE-01"
    save_dir.mkdir(parents=True)
    (save_dir / "PARAM.SFO").write_bytes(b"param")
    (save_dir / "GAME.DAT").write_bytes(b"game")

    results = se._scan_emudeck(root)

    assert len(results) == 1
    assert results[0].title_id == "BLUS30464-AUTOSAVE-01"
    assert results[0].path == save_dir
    assert results[0].system == "PS3"
    assert results[0].hash == se._hash_ps3_dir_files(save_dir)


def test_upload_save_uses_bundle_endpoint_for_ps3_directories(monkeypatch, tmp_path):
    save_dir = tmp_path / "BLUS30464-AUTOSAVE-01"
    save_dir.mkdir()
    (save_dir / "PARAM.SFO").write_bytes(b"param")
    (save_dir / "GAME.DAT").write_bytes(b"game")
    calls = []

    class PostResponse:
        def raise_for_status(self):
            return None

    def fake_post(url, headers=None, params=None, data=None, timeout=None):
        calls.append((url, params, data))
        return PostResponse()

    monkeypatch.setattr(se.requests, "post", fake_post)
    monkeypatch.setattr(se, "_update_state", lambda title_id, hash_val: None)

    se.upload_save(
        "BLUS30464-AUTOSAVE-01",
        save_dir,
        "http://example",
        {"X-API-Key": "x"},
        system="PS3",
    )

    assert calls[0][0] == "http://example/api/v1/saves/BLUS30464-AUTOSAVE-01"
    assert calls[0][1] == {}
    assert calls[0][2][:4] == b"3DSS"


def test_upload_save_skips_param_pfd_for_ps3_directories(monkeypatch, tmp_path):
    save_dir = tmp_path / "BLJS10001GAME"
    save_dir.mkdir()
    (save_dir / "PARAM.SFO").write_bytes(b"param")
    (save_dir / "GAME").write_bytes(b"game")
    (save_dir / "PARAM.PFD").write_bytes(b"pfd")
    calls = []

    class PostResponse:
        def raise_for_status(self):
            return None

    def fake_post(url, headers=None, params=None, data=None, timeout=None):
        calls.append((url, params, data))
        return PostResponse()

    monkeypatch.setattr(se.requests, "post", fake_post)
    monkeypatch.setattr(se, "_update_state", lambda title_id, hash_val: None)

    se.upload_save(
        "BLJS10001GAME",
        save_dir,
        "http://example",
        {"X-API-Key": "x"},
        system="PS3",
    )

    names = [name for name, _data in se._parse_dir_bundle(calls[0][2])]
    assert names == ["GAME", "PARAM.SFO"]


def test_hash_ps3_dir_files_ignores_ps3_metadata_and_pngs(tmp_path):
    save_dir = tmp_path / "BLJS10001GAME"
    save_dir.mkdir()
    (save_dir / "GAME").write_bytes(b"game")
    (save_dir / "PARAM.SFO").write_bytes(b"param")
    (save_dir / "PARAM.PFD").write_bytes(b"pfd")
    (save_dir / "ICON0.PNG").write_bytes(b"icon")
    (save_dir / "PIC1.PNG").write_bytes(b"pic")

    assert se._hash_ps3_dir_files(save_dir) == hashlib.sha256(b"game").hexdigest()


def test_download_save_extracts_ps3_bundle_into_missing_directory(monkeypatch, tmp_path):
    dest = tmp_path / "BLUS30464-AUTOSAVE-01"
    source = tmp_path / "server-copy"
    source.mkdir()
    (source / "PARAM.SFO").write_bytes(b"param")
    (source / "GAME.DAT").write_bytes(b"game")
    calls = []

    class GetResponse:
        content = se._create_dir_bundle("BLUS30464-AUTOSAVE-01", source)
        headers = {"X-Save-Hash": "server-ps3-hash"}

        def raise_for_status(self):
            return None

    def fake_get(url, headers=None, params=None, timeout=None):
        calls.append((url, params))
        return GetResponse()

    monkeypatch.setattr(se.requests, "get", fake_get)
    monkeypatch.setattr(se, "_update_state", lambda title_id, hash_val: None)

    server_hash = se.download_save(
        "BLUS30464-AUTOSAVE-01",
        dest,
        "http://example",
        {"X-API-Key": "x"},
        system="PS3",
    )

    assert calls == [("http://example/api/v1/saves/BLUS30464-AUTOSAVE-01", None)]
    assert dest.is_dir()
    assert (dest / "PARAM.SFO").read_bytes() == b"param"
    assert (dest / "GAME.DAT").read_bytes() == b"game"
    assert server_hash == "server-ps3-hash"
