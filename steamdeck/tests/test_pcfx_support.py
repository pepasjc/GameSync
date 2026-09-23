"""NEC PC-FX on the Steam Deck: EmuDeck ``roms/pcfx`` + RetroArch Beetle PC-FX.

Beetle PC-FX exposes its backup RAM as one libretro SAVE_RAM block, so
RetroArch writes ``<game>.srm`` (under ``Beetle PC-FX/`` when saves are
sorted by core).  The save keys as ``PCFX_<slug>``, same as the server.
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
STEAMDECK_ROOT = ROOT / "steamdeck"
if str(STEAMDECK_ROOT) not in sys.path:
    sys.path.insert(0, str(STEAMDECK_ROOT))

from scanner import retroarch, server_only  # noqa: E402
from scanner.rom_target import resolve_rom_target_dir  # noqa: E402


def test_pcfx_downloads_land_in_the_emudeck_folder(tmp_path):
    # Linux is case-sensitive: EmuDeck's folder is lowercase "pcfx", and the
    # generic fallback would otherwise create a second "PCFX" folder.
    assert resolve_rom_target_dir(tmp_path, "PCFX") == tmp_path / "pcfx"
    (tmp_path / "PC-FX").mkdir()
    assert resolve_rom_target_dir(tmp_path, "PCFX") == tmp_path / "PC-FX"


def test_beetle_pcfx_core_resolves_to_pcfx_not_pce():
    assert retroarch._core_to_system("NEC - PC-FX (Beetle PC-FX)") == "PCFX"
    assert retroarch._core_to_system("Beetle PC-FX") == "PCFX"
    assert retroarch._folder_to_system("pcfx") == "PCFX"


def test_rom_folder_scan_finds_beetle_pcfx_save(tmp_path):
    rom = tmp_path / "roms" / "pcfx" / "Zenki FX - Vajura Fight (Japan).chd"
    rom.parent.mkdir(parents=True)
    rom.write_bytes(b"chd")
    save = (tmp_path / "saves" / "retroarch" / "saves" / "Beetle PC-FX"
            / "Zenki FX - Vajura Fight (Japan).srm")
    save.parent.mkdir(parents=True)
    save.write_bytes(b"\0" * 0x10000)

    entries = [e for e in retroarch.scan(tmp_path) if e.system == "PCFX"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry.title_id == "PCFX_zenki_fx_vajura_fight_japan"
    assert entry.save_path == save
    assert entry.save_hash


def test_server_only_pcfx_save_predicts_a_retroarch_srm(tmp_path):
    server = {
        "PCFX_zenki_fx_vajura_fight_japan": {
            "console_type": "PCFX",
            "name": "Zenki FX - Vajura Fight (Japan)",
            "save_hash": "h",
        }
    }
    entry = server_only.build_server_only_entries(
        server, set(), tmp_path,
        canonical_names={
            "PCFX_zenki_fx_vajura_fight_japan": "Zenki FX - Vajura Fight (Japan)",
        },
    )[0]
    assert entry.save_path == (
        tmp_path / "saves" / "retroarch" / "saves"
        / "Zenki FX - Vajura Fight (Japan).srm"
    )
