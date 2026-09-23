"""MiSTer-over-SSH sync/install plumbing (no network — SSH is monkeypatched)."""

import posixpath
from pathlib import Path

import sync_engine as se
import rom_installer as ri
from rom_installer import build_install_plan, mister_ssh_config


def _ssh_profile(**extra) -> dict:
    return {
        "name": "MiSTer",
        "device_type": "MiSTer",
        "path": "",
        "mister_target": "usb",
        "ssh_host": "192.168.1.41",
        "ssh_port": 22,
        "ssh_username": "root",
        "ssh_password": "1",
        "ssh_key_path": "",
        "systems": [{"system": "GBA", "enabled": True}],
        **extra,
    }


def test_mister_profile_uses_ssh():
    assert se.mister_profile_uses_ssh(_ssh_profile()) is True
    assert se.mister_profile_uses_ssh(_ssh_profile(ssh_host="")) is False
    assert (
        se.mister_profile_uses_ssh({"device_type": "Generic", "ssh_host": "x"}) is False
    )


def test_ssh_save_path_pathlike_api():
    p = se.SshSavePath(
        host="192.168.1.41",
        port=22,
        username="root",
        password="1",
        key_path="",
        remote_path="/media/fat/saves/GBA/Zelda (USA).sav",
    )
    assert p.name == "Zelda (USA).sav"
    assert p.stem == "Zelda (USA)"
    assert p.suffix == ".sav"
    assert str(p) == "ssh://192.168.1.41/media/fat/saves/GBA/Zelda (USA).sav"
    assert p.sync_key() == str(p)
    assert p.exists() is True  # assume_exists default — no network round-trip
    assert p.is_dir() is False


def test_scan_profile_dispatches_to_ssh_scan(monkeypatch):
    called = {}

    def fake_scan(profile, systems_config, progress_callback=None, profile_scope=""):
        called["profile"] = profile
        return []

    monkeypatch.setattr(se, "_scan_mister_ssh", fake_scan)
    result = se.scan_profile(_ssh_profile())
    assert result == []
    assert called["profile"]["ssh_host"] == "192.168.1.41"


def test_scan_profile_without_ssh_stays_local(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise AssertionError("SSH scan must not run for local profiles")

    monkeypatch.setattr(se, "_scan_mister_ssh", boom)
    profile = _ssh_profile(ssh_host="", path=str(tmp_path))
    assert se.scan_profile(profile) == []


def test_mister_ssh_config_prefers_profile_over_global(monkeypatch):
    monkeypatch.setattr(
        ri, "load_config", lambda: {"mister_ssh": {"host": "10.0.0.9", "port": 2222}}
    )
    cfg = mister_ssh_config(_ssh_profile())
    assert cfg["host"] == "192.168.1.41"
    assert cfg["port"] == 22
    # Legacy profile without ssh fields falls back to the global config key.
    legacy = mister_ssh_config({"device_type": "MiSTer"})
    assert legacy["host"] == "10.0.0.9"


def test_build_install_plan_carries_profile_ssh():
    rom = {
        "rom_id": "GBA_game",
        "system": "GBA",
        "name": "Game",
        "filename": "Game (USA).gba",
    }
    plan = build_install_plan(_ssh_profile(), rom, "GBA")
    assert plan.mister_remote == "usb"
    assert plan.mister_ssh["host"] == "192.168.1.41"
    assert plan.mister_ssh["password"] == "1"
    assert str(plan.target_path) == "/media/usb0/games/GBA/Game (USA).gba"


def _fake_ps1_card(incard_name: bytes = b"BASLUS-01324DRACULA-1") -> bytes:
    card = bytearray(131072)
    card[0:2] = b"MC"
    frame = 128  # directory frame 1
    card[frame] = 0x51  # in-use, first link
    card[frame + 0x0A : frame + 0x0A + len(incard_name)] = incard_name
    return bytes(card)


def test_ps1_card_serial_from_directory_frame():
    assert se._ps1_card_serial(_fake_ps1_card()) == "SLUS01324"
    assert se._ps1_card_serial(_fake_ps1_card(b"BESCES-01444SAVE")) == "SCES01444"
    # Formatted but empty card → no serial.
    empty = bytearray(131072)
    empty[0:2] = b"MC"
    assert se._ps1_card_serial(bytes(empty)) is None
    assert se._ps1_card_serial(b"junk") is None


def test_scan_mister_ssh_rekeys_ps1_to_incard_serial(monkeypatch):
    import mister_ssh as ms

    card = _fake_ps1_card()

    class FakeSSH:
        host = "h"
        port = 22
        username = "root"
        password = "1"
        key_path = ""

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def scan_saves(self, progress_cb=None):
            return [
                ms.MiSTerSave(
                    system="PS1",
                    folder="PSX",
                    filename="Breath of Fire IV (USA).sav",
                    remote_path="/media/fat/saves/PSX/Breath of Fire IV (USA).sav",
                    title_id="PS1_breath_of_fire_iv_usa",
                    size=len(card),
                    mtime=1000.0,
                ),
                ms.MiSTerSave(
                    system="GBA",
                    folder="GBA",
                    filename="Zelda (USA).sav",
                    remote_path="/media/fat/saves/GBA/Zelda (USA).sav",
                    title_id="GBA_zelda_usa",
                    size=8,
                    mtime=1000.0,
                ),
            ]

        def read_file(self, path):
            return card

        def hash_file(self, path):
            return "gbahash"

    monkeypatch.setattr(se, "_mister_ssh_from_profile", lambda p: FakeSSH())
    monkeypatch.setattr(se, "_get_cached_hash_for_key", lambda *a: None)
    monkeypatch.setattr(se, "_set_cached_hash_for_key", lambda *a: None)

    results = se._scan_mister_ssh(_ssh_profile(), {})
    by_system = {sv.system: sv for sv in results}
    assert by_system["PS1"].title_id == "SLUS01324"  # in-card code, not slug
    assert by_system["PS1"].hash  # hashed from the same read
    assert by_system["GBA"].title_id == "GBA_zelda_usa"  # non-PS1 untouched


def test_ps1_serial_from_filename_accepts_cd_written_names():
    f = se._ps1_serial_from_filename
    assert f("SLPM-86219") == "SLPM86219"
    assert f("SLUS_012.34") == "SLUS01234"
    assert f("scus94163") == "SCUS94163"
    assert f("SLES 12345") == "SLES12345"
    # Ordinary game names and unknown prefixes must never look like serials.
    assert f("Breath of Fire IV (USA)") is None
    assert f("Final Fantasy VII (Disc 1) (USA)") is None
    assert f("ABCD-12345") is None
    assert f("") is None


def test_segacd_uploads_raw_and_unconverted(monkeypatch):
    """Sega CD backup RAM is identical on both sides — it must not be
    converted like Saturn or Mega Drive, and must use the plain /raw
    endpoint rather than a card endpoint."""
    card = _segacd_bram(b"SNATCHER\x01")
    sent = {}

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            pass

    def fake_post(url, **kw):
        sent["url"] = url
        sent["data"] = kw.get("data")
        return FakeResp()

    monkeypatch.setattr(se.requests, "post", fake_post)
    monkeypatch.setattr(se, "_update_state", lambda *a: None)
    monkeypatch.setattr(se.SshSavePath, "read_bytes", lambda self: card)

    se.upload_save(
        "SEGACD_snatcher_usa",
        _ssh_path("/media/fat/saves/MegaCD/Snatcher (USA).sav"),
        "http://s",
        {},
        system="SEGACD",
    )
    assert sent["url"].endswith("/saves/SEGACD_snatcher_usa/raw")
    assert sent["data"] == card  # byte-for-byte, no conversion


def test_segacd_download_writes_the_card_unchanged(monkeypatch):
    card = _segacd_bram(b"SNATCHER\x01")
    written = {}

    class FakeResp:
        status_code = 200
        content = card
        headers = {"X-Save-Hash": "abc"}

        def raise_for_status(self):
            pass

    monkeypatch.setattr(se.requests, "get", lambda *a, **k: FakeResp())
    monkeypatch.setattr(se.SshSavePath, "write_bytes", lambda self, d: written.update(d=d))
    monkeypatch.setattr(se, "_update_state", lambda *a: None)

    se.download_save(
        "SEGACD_snatcher_usa",
        _ssh_path("/media/fat/saves/MegaCD/Snatcher (USA).sav"),
        "http://s",
        {},
        system="SEGACD",
    )
    assert written["d"] == card


def test_blank_ps1_cards_stay_visible_but_cannot_upload(monkeypatch):
    """A blank card is 'no save data yet' — listed, downloadable, not uploadable."""
    import mister_ssh as ms

    blank = bytearray(131072)
    blank[0:2] = b"MC"
    for frame in range(1, 16):
        blank[frame * 128] = 0xA0  # free block
    blank = bytes(blank)
    assert se._ps1_card_is_empty(blank) is True

    class FakeSSH:
        host = "h"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def scan_saves(self, progress_cb=None):
            return [
                ms.MiSTerSave(
                    system="PS1",
                    folder="PSX",
                    filename=name,
                    remote_path=f"/media/fat/saves/PSX/{name}",
                    title_id="PS1_x",
                    size=131072,
                    mtime=1000.0,
                )
                for name in ("Final Fantasy IX (USA).sav", "SLPM-86219.sav")
            ]

        def read_file(self, path):
            return blank

    monkeypatch.setattr(se, "_mister_ssh_from_profile", lambda p: FakeSSH())
    monkeypatch.setattr(se, "_get_cached_hash_for_key", lambda *a: None)
    monkeypatch.setattr(se, "_set_cached_hash_for_key", lambda *a: None)
    # Blank PS1 cards now consult the ROM catalog, so stub it out: this test is
    # about what happens when nothing else identifies the card, and it must not
    # depend on whatever server the developer has configured.
    import rom_installer

    monkeypatch.setattr(rom_installer, "fetch_rom_catalog", lambda system: [])
    se.clear_mister_catalog_cache()

    saves = se._scan_mister_ssh(_ssh_profile(), {})
    # Both cards are listed — hiding a real file on the device would look
    # like the tool lost the save.
    assert len(saves) == 2
    assert [s.title_id for s in saves] == ["PS1_x", "SLPM86219"]
    # No save data: empty hash + save_exists False, so the sync tab offers a
    # download into the card but never an upload of an empty one.
    assert all(s.hash == "" and s.save_exists is False for s in saves)


class _FakeAttr:
    def __init__(self, filename, is_dir=False):
        self.filename = filename
        self.st_mode = 0o040755 if is_dir else 0o100644


class _RomListingSSH:
    """Serves a fake games/<folder> listing over the SFTP surface we use."""

    host = "h"

    def __init__(self, listings: dict):
        self.listings = listings
        self._sftp = self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def listdir(self, path):
        if path == "/media/fat/saves":
            return ["SNES", "PSX"]
        raise FileNotFoundError(path)

    def listdir_attr(self, path):
        if path not in self.listings:
            raise FileNotFoundError(path)
        return self.listings[path]


def test_download_path_uses_the_installed_rom_name(monkeypatch):
    """Server display names differ from on-device ROM names; the core only
    finds ``<rom stem>.sav``."""
    rom = "Ganbare Goemon 2 - Kiteretsu Shougun McGuiness (Japan).sfc"
    ssh = _RomListingSSH({"/media/usb0/games/SNES": [_FakeAttr(rom)]})
    monkeypatch.setattr(se, "_mister_ssh_from_profile", lambda p: ssh)

    path = se.build_mister_ssh_save_path(
        _ssh_profile(),
        "SNES_ganbare_goemon_2_kiteretsu_shougun_mcguiness_japan",
        "SNES",
        "Ganbare Goemon 2 Kiteretsu Shougun Mcguiness Japan",  # server's name
    )
    assert path.remote_path == (
        "/media/fat/saves/SNES/"
        "Ganbare Goemon 2 - Kiteretsu Shougun McGuiness (Japan).sav"
    )


def test_download_path_falls_back_to_the_server_name_when_not_installed(monkeypatch):
    ssh = _RomListingSSH({"/media/usb0/games/SNES": [_FakeAttr("Other Game.sfc")]})
    monkeypatch.setattr(se, "_mister_ssh_from_profile", lambda p: ssh)

    path = se.build_mister_ssh_save_path(
        _ssh_profile(), "SNES_some_game", "SNES", "Some Game"
    )
    assert path.remote_path == "/media/fat/saves/SNES/Some Game.sav"


def test_download_path_prefers_a_cd_game_folder(monkeypatch):
    """CD cores name the card after the folder, so directories win."""
    ssh = _RomListingSSH(
        {
            "/media/usb0/games/PSX": [
                _FakeAttr("Final Fantasy VII (USA)", is_dir=True),
                _FakeAttr("Final Fantasy VII (Disc 1) (USA).chd"),
            ]
        }
    )
    monkeypatch.setattr(se, "_mister_ssh_from_profile", lambda p: ssh)

    path = se.build_mister_ssh_save_path(
        _ssh_profile(), "SLUS00868", "PS1", "Final Fantasy VII (USA)"
    )
    assert path.remote_path == "/media/fat/saves/PSX/Final Fantasy VII (USA).sav"


def test_blank_cd_card_offers_a_download_when_the_server_has_the_save(monkeypatch):
    """Boot the CD once to make the card, then pull the save down into it."""
    blank = se.SaveFile(
        title_id="SLPM86219",
        path=se.SshSavePath(
            host="h", port=22, username="root", password="1", key_path="",
            remote_path="/media/fat/saves/PSX/SLPM-86219.sav",
        ),
        hash="",
        mtime=1000.0,
        system="PS1",
        game_name="SLPM-86219",
        save_exists=False,
    )

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {
                "titles": [
                    {"title_id": "SLPM86219", "save_hash": "deadbeef", "name": "A Game"}
                ]
            }

    monkeypatch.setattr(se.requests, "get", lambda *a, **k: FakeResp())
    monkeypatch.setattr(se, "_load_state", lambda: {})
    monkeypatch.setattr(se, "_load_ps1_card_meta", lambda *a: None)
    monkeypatch.setattr(se, "_load_gc_card_meta", lambda *a: None)

    statuses = se.compare_with_server([blank], "http://s", {})
    local = [s for s in statuses if s.save.path is not None]
    assert len(local) == 1
    assert local[0].status == "server_newer"
    # Download targets the card the core already created, not a new file.
    assert local[0].save.path.remote_path == "/media/fat/saves/PSX/SLPM-86219.sav"


def _segacd_bram(entry: bytes | None = None) -> bytes:
    """8 KB Sega CD internal backup RAM with the standard format footer."""
    data = bytearray(8192)
    footer = bytes.fromhex(
        "5f5f5f5f5f5f5f5f5f5f5f00000000400"
        "07d007d007d007d0000000000000000"
    )
    data[-0x40 : -0x40 + len(footer)] = footer
    data[-0x20:] = b"SEGA_CD_ROM\x00\x01\x00\x00\x00RAM_CARTRIDGE___"
    if entry:
        data[-0x60:-0x40] = entry.ljust(0x20, b"\x00")
    return bytes(data)


def _md_packed(payload: bytes, sram: int = 8192) -> bytes:
    """A game's SRAM contents as the MegaDrive core stores them."""
    packed = bytearray(b"\x00" * sram)
    packed[64 : 64 + len(payload)] = payload
    return bytes(packed)


def _md_core_image(payload: bytes, sram: int = 8192) -> bytes:
    """What the core writes: packed SRAM padded with 0xFF to 64 KB."""
    packed = _md_packed(payload, sram)
    return packed + b"\xff" * (65536 - len(packed))


def _md_emulator_image(payload: bytes, sram: int = 8192) -> bytes:
    """What emulators and the server keep: one SRAM byte per odd offset."""
    return se._md_packed_to_expanded(_md_packed(payload, sram))


def test_md_layouts_convert_both_ways():
    """MD SRAM lives on the odd byte of the 68000 bus, so emulators store it
    expanded while the MiSTer core stores it packed and padded to 64 KB."""
    emu = _md_emulator_image(b"BOWIE")
    core = _md_core_image(b"BOWIE")

    # Server -> MiSTer: packed, 0xFF-padded, and exactly 64 KB.
    to_core = se._md_to_mister(emu)
    assert len(to_core) == 65536
    assert to_core == core
    assert to_core[8192:] == b"\xff" * (65536 - 8192)
    assert b"BOWIE" in to_core[:8192]

    # MiSTer -> server: back to the expanded layout at the server's size.
    back = se._md_from_mister(core, target_size=len(emu))
    assert back == emu
    assert bytes(back[1::2]) == core[: len(emu) // 2]


def test_md_conversion_round_trips():
    emu = _md_emulator_image(b"SARAH", sram=4096)
    assert se._md_from_mister(se._md_to_mister(emu), target_size=len(emu)) == emu

    core = _md_core_image(b"SARAH", sram=4096)
    assert se._md_to_mister(se._md_from_mister(core, target_size=8192)) == core


def test_md_sram_size_inferred_from_used_region():
    """Without a server counterpart, size comes from the used bytes."""
    assert len(se._md_from_mister(_md_core_image(b"x" * 10, sram=4096))) == 8192
    assert len(se._md_from_mister(_md_core_image(b"x" * 6000, sram=8192))) == 16384
    # A server counterpart always wins over the guess.
    assert (
        len(se._md_from_mister(_md_core_image(b"x", sram=8192), target_size=8192))
        == 8192
    )


def test_md_passthrough_when_already_in_the_right_shape():
    core = _md_core_image(b"x")
    assert se._md_to_mister(core) is core or se._md_to_mister(core) == core
    # Not a core image — leave it alone rather than mangling it.
    odd_sized = b"\x01\x02\x03"
    assert se._md_from_mister(odd_sized) == odd_sized


def test_segacd_blank_bram_detection():
    blank = _segacd_bram()
    assert len(blank) == 8192
    assert b"SEGA_CD_ROM" in blank[-0x40:]
    assert se._segacd_bram_is_empty(blank) is True
    # One directory entry present -> real save data.
    assert se._segacd_bram_is_empty(_segacd_bram(b"SNATCHER___\x00\x01")) is False
    # Not a Sega CD image at all.
    assert se._segacd_bram_is_empty(b"\x00" * 8192) is False
    assert se._segacd_bram_is_empty(b"") is False


def test_blank_segacd_card_cannot_upload_over_a_real_save(monkeypatch):
    """Regression: a freshly formatted BRAM compared as if it held a save."""
    import mister_ssh as ms

    blank = _segacd_bram()

    class FakeSSH:
        host = "h"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def scan_saves(self, progress_cb=None):
            return [
                ms.MiSTerSave(
                    system="SEGACD",
                    folder="MegaCD",
                    filename="Snatcher (USA).sav",
                    remote_path="/media/fat/saves/MegaCD/Snatcher (USA).sav",
                    title_id="SEGACD_snatcher_usa",
                    size=len(blank),
                    mtime=1000.0,
                )
            ]

        def read_file(self, path):
            return blank

    monkeypatch.setattr(se, "_mister_ssh_from_profile", lambda p: FakeSSH())
    monkeypatch.setattr(se, "_get_cached_hash_for_key", lambda *a: None)
    monkeypatch.setattr(se, "_set_cached_hash_for_key", lambda *a: None)

    (save,) = se._scan_mister_ssh(_ssh_profile(), {})
    assert save.title_id == "SEGACD_snatcher_usa"  # matches the server's key
    assert save.save_exists is False
    assert save.hash == ""


def test_scan_mister_ssh_keys_cd_card_by_filename_serial(monkeypatch):
    """A CD-booted card with no save block yet is keyed by its filename."""
    import mister_ssh as ms

    empty_card = bytearray(131072)
    empty_card[0:2] = b"MC"  # formatted, no save blocks
    empty_card = bytes(empty_card)

    class FakeSSH:
        host = "h"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def scan_saves(self, progress_cb=None):
            return [
                ms.MiSTerSave(
                    system="PS1",
                    folder="PSX",
                    filename="SLPM-86219.sav",
                    remote_path="/media/fat/saves/PSX/SLPM-86219.sav",
                    title_id="PS1_slpm_86219",
                    size=len(empty_card),
                    mtime=1000.0,
                )
            ]

        def read_file(self, path):
            return empty_card

    monkeypatch.setattr(se, "_mister_ssh_from_profile", lambda p: FakeSSH())
    monkeypatch.setattr(se, "_get_cached_hash_for_key", lambda *a: None)
    monkeypatch.setattr(se, "_set_cached_hash_for_key", lambda *a: None)

    (save,) = se._scan_mister_ssh(_ssh_profile(), {})
    assert save.title_id == "SLPM86219"


def test_scan_mister_ssh_incard_code_beats_filename_serial(monkeypatch):
    """Variant discs boot one serial but write another — in-card code wins."""
    import mister_ssh as ms

    card = bytearray(131072)
    card[0:2] = b"MC"
    card[128] = 0x51
    name = b"BISLPS-00555SOULEDGE"
    card[128 + 0x0A : 128 + 0x0A + len(name)] = name
    card = bytes(card)

    class FakeSSH:
        host = "h"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def scan_saves(self, progress_cb=None):
            return [
                ms.MiSTerSave(
                    system="PS1",
                    folder="PSX",
                    filename="SLPS-00545.sav",  # disc serial differs
                    remote_path="/media/fat/saves/PSX/SLPS-00545.sav",
                    title_id="PS1_slps_00545",
                    size=len(card),
                    mtime=1000.0,
                )
            ]

        def read_file(self, path):
            return card

    monkeypatch.setattr(se, "_mister_ssh_from_profile", lambda p: FakeSSH())
    monkeypatch.setattr(se, "_get_cached_hash_for_key", lambda *a: None)
    monkeypatch.setattr(se, "_set_cached_hash_for_key", lambda *a: None)

    (save,) = se._scan_mister_ssh(_ssh_profile(), {})
    assert save.title_id == "SLPS00555"


def test_folder_and_cd_cards_for_one_game_stay_separate_rows(monkeypatch):
    """Both cards resolve to the same server slot but remain syncable alone."""
    import mister_ssh as ms

    def card_with(name: bytes) -> bytes:
        buf = bytearray(131072)
        buf[0:2] = b"MC"
        buf[128] = 0x51
        buf[128 + 0x0A : 128 + 0x0A + len(name)] = name
        return bytes(buf)

    folder_card = card_with(b"BASLUS-01324DRACULA")
    cd_card = card_with(b"BASLUS-01324DRACULA2")
    by_path = {
        "/media/fat/saves/PSX/Breath of Fire IV (USA).sav": folder_card,
        "/media/fat/saves/PSX/SLUS-01324.sav": cd_card,
    }

    class FakeSSH:
        host = "h"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def scan_saves(self, progress_cb=None):
            return [
                ms.MiSTerSave(
                    system="PS1",
                    folder="PSX",
                    filename=posixpath.basename(path),
                    remote_path=path,
                    title_id="PS1_x",
                    size=131072,
                    mtime=1000.0,
                )
                for path in by_path
            ]

        def read_file(self, path):
            return by_path[path]

    monkeypatch.setattr(se, "_mister_ssh_from_profile", lambda p: FakeSSH())
    monkeypatch.setattr(se, "_get_cached_hash_for_key", lambda *a: None)
    monkeypatch.setattr(se, "_set_cached_hash_for_key", lambda *a: None)
    monkeypatch.setattr(se, "_flush_scan_cache", lambda: None)
    monkeypatch.setattr(se, "_flush_remote_hash_cache", lambda: None)

    profile = _ssh_profile(systems=[{"system": "PS1", "enabled": True}])
    saves = se.scan_profile(profile)
    assert len(saves) == 2  # not merged by _dedup_saves
    assert {s.title_id for s in saves} == {"SLUS01324"}
    assert {str(s.path) for s in saves} == {
        "ssh://192.168.1.41/media/fat/saves/PSX/Breath of Fire IV (USA).sav",
        "ssh://192.168.1.41/media/fat/saves/PSX/SLUS-01324.sav",
    }
    # Each row carries only its own file — no shared alternate_paths that
    # would make a download overwrite the other card.
    assert all(not s.alternate_paths for s in saves)


def _ssh_path(remote: str) -> "se.SshSavePath":
    return se.SshSavePath(
        host="h", port=22, username="root", password="1", key_path="",
        remote_path=remote,
    )


def _mister_saturn_card(payload: bytes | None = None) -> bytes:
    """A 64 KB MiSTer Saturn backup-RAM image (0xFF pad at even offsets)."""
    from saroo_format import _expand_byte_padded_saturn, SAT_INTERNAL_SIZE

    inner = bytearray(b"\xff" * SAT_INTERNAL_SIZE)
    inner[0:16] = b"BackUpRam Format"
    if payload:
        inner[0x40 : 0x40 + len(payload)] = payload
    return _expand_byte_padded_saturn(bytes(inner))


def test_mister_saturn_card_matches_our_yabause_layout():
    card = _mister_saturn_card()
    assert len(card) == 65536
    # This is the exact shape read off the device: 0xFF at even offsets,
    # data at odd offsets, magic in the collapsed bytes.
    assert {card[i] for i in range(0, len(card), 2)} == {0xFF}
    assert bytes(card[1::2])[:16] == b"BackUpRam Format"
    assert se._saturn_format_for_path(_ssh_path("/x/Game.sav")) == "yabause"
    # Non-MiSTer paths keep their existing mapping.
    assert se._saturn_format_for_path(Path("Game.srm")) == "yabause"
    assert se._saturn_format_for_path(Path("Game.bkr")) == "mednafen"


def test_saturn_title_id_comes_from_the_rom_catalog(monkeypatch):
    """A translation patch's filename bears no resemblance to the server title."""
    catalog = [
        {
            "title_id": "SAT_T-9527G",
            "filename": "Castlevania - Symphony of the Night (Japan) (2M) "
            "[T-En by Knight0fDragon v1.021+hotfix] [n].chd",
            "name": "Akumajou Dracula X - Gekka no Yasoukyoku (Japan) (2M)",
        },
        {"title_id": "SAT_T-19905G", "filename": "Bubble Symphony (Japan).chd"},
    ]
    import rom_installer

    monkeypatch.setattr(rom_installer, "fetch_rom_catalog", lambda system: catalog)
    se.clear_mister_catalog_cache()

    assert (
        se._mister_catalog_title_id(
            "SAT",
            "Castlevania - Symphony of the Night (Japan) (2M) "
            "[T-En by Knight0fDragon v1.021+hotfix] [n]",
        )
        == "SAT_T-9527G"
    )
    # The canonical Japanese name resolves to the same slot.
    assert (
        se._mister_catalog_title_id(
            "SAT", "Akumajou Dracula X - Gekka no Yasoukyoku (Japan) (2M)"
        )
        == "SAT_T-9527G"
    )
    assert se._mister_catalog_title_id("SAT", "Some Unknown Game") is None
    se.clear_mister_catalog_cache()


def test_saturn_upload_sends_canonical_32k(monkeypatch):
    """The server keeps one payload shared with every other Saturn client."""
    from saroo_format import normalize_saturn_save

    card = _mister_saturn_card(b"payload")
    sent = {}

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            pass

    def fake_post(url, **kw):
        sent["url"] = url
        sent["data"] = kw.get("data")
        return FakeResp()

    monkeypatch.setattr(se.requests, "post", fake_post)
    monkeypatch.setattr(se, "_update_state", lambda *a: None)
    monkeypatch.setattr(se.SshSavePath, "read_bytes", lambda self: card)

    se.upload_save("SAT_T-9527G", _ssh_path("/x/Game.sav"), "http://s", {}, system="SAT")
    assert sent["data"] == normalize_saturn_save(card)
    assert len(sent["data"]) == 32768


def test_saturn_download_expands_to_the_64k_the_core_reads(monkeypatch):
    from saroo_format import normalize_saturn_save

    canonical = normalize_saturn_save(_mister_saturn_card(b"payload"))
    written = {}

    class FakeResp:
        status_code = 200
        content = canonical
        headers = {"X-Save-Hash": "abc"}

        def raise_for_status(self):
            pass

    monkeypatch.setattr(se.requests, "get", lambda *a, **k: FakeResp())
    monkeypatch.setattr(se.SshSavePath, "write_bytes", lambda self, d: written.update(d=d))
    monkeypatch.setattr(se, "_update_state", lambda *a: None)

    se.download_save("SAT_T-9527G", _ssh_path("/x/Game.sav"), "http://s", {}, system="SAT")
    out = written["d"]
    assert len(out) == 65536
    assert {out[i] for i in range(0, len(out), 2)} == {0xFF}
    # What lands on the MiSTer collapses back to exactly what the server holds.
    assert normalize_saturn_save(out) == canonical


def test_download_save_writes_to_ssh_path(monkeypatch):
    written = {}

    class FakeResp:
        status_code = 200
        content = b"savedata"
        headers = {"X-Save-Hash": "abc123"}

        def raise_for_status(self):
            pass

    monkeypatch.setattr(se.requests, "get", lambda *a, **k: FakeResp())
    monkeypatch.setattr(
        se.SshSavePath, "write_bytes", lambda self, data: written.update(d=data)
    )
    monkeypatch.setattr(se, "_update_state", lambda *a: None)
    dest = se.SshSavePath(
        host="h", port=22, username="root", password="1", key_path="",
        remote_path="/media/fat/saves/GBA/x.sav", assume_exists=False,
    )
    server_hash = se.download_save("GBA_x", dest, "http://s", {})
    assert written["d"] == b"savedata"
    assert server_hash == "abc123"
