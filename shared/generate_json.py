#!/usr/bin/env python3
"""Generate shared/systems.json from shared/systems.py.

Run from the repo root:
    python shared/generate_json.py

The JSON file is consumed by non-Python clients such as the MiSTer sync
script (mister/sync_saves.sh) and any future shell tooling.
"""
from __future__ import annotations

import json
from pathlib import Path

import mister
import systems  # relative import when run from this directory

OUTPUTS = [
    Path(__file__).parent / "systems.json",
    Path(__file__).parent.parent / "mister" / "systems.json",
]


def main() -> None:
    data = {
        "system_choices": systems.SYSTEM_CHOICES,
        "all_console_types": systems.ALL_CONSOLE_TYPES,
        "system_aliases": systems.SYSTEM_ALIASES,
        "system_codes": sorted(systems.SYSTEM_CODES),
        "rom_extensions": sorted(systems.ROM_EXTENSIONS),
        "cd_data_extensions": sorted(systems.CD_DATA_EXTENSIONS),
        "cd_all_extensions": sorted(systems.CD_ALL_EXTENSIONS),
        "cd_folder_systems": sorted(systems.CD_FOLDER_SYSTEMS),
        "mega_everdrive_cd_systems": sorted(systems.MEGA_EVERDRIVE_CD_SYSTEMS),
        "save_extensions": sorted(systems.SAVE_EXTENSIONS),
        "companion_extensions": sorted(systems.COMPANION_EXTENSIONS),
        "save_ext_choices": systems.SAVE_EXT_CHOICES,
        "system_default_save_ext": systems.SYSTEM_DEFAULT_SAVE_EXT,
        "system_dat_keywords": systems.SYSTEM_DAT_KEYWORDS,
        "folder_to_system": systems.FOLDER_TO_SYSTEM,
        "system_color": systems.SYSTEM_COLOR,
        "default_system_color": systems.DEFAULT_SYSTEM_COLOR,
        "psx_retail_prefixes": sorted(systems.PSX_RETAIL_PREFIXES),
        "mister_folder_to_system": mister.MISTER_FOLDER_TO_SYSTEM,
        "mister_system_to_folder": mister.MISTER_SYSTEM_TO_FOLDER,
    }
    payload = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    for output in OUTPUTS:
        output.write_text(payload, encoding="utf-8")
        print(f"Written: {output}")


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    main()
