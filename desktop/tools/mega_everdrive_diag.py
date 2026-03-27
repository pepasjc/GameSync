"""Standalone diagnostic for MEGA EverDrive save folder matching.

Run from the desktop/ directory:
    python tools/mega_everdrive_diag.py <rom_folder> <gamedata_folder>

Example:
    python tools/mega_everdrive_diag.py J:/MD J:/MEGA/gamedata
"""
import sys
from pathlib import Path
import rom_normalizer as rn

ROM_EXTENSIONS = {
    ".md", ".smd", ".gen", ".bin", ".rom",   # Mega Drive
    ".sfc", ".smc", ".gba", ".gb", ".gbc", ".nes",
}

def main():
    if len(sys.argv) < 3:
        print("Usage: python test_mega_everdrive.py <rom_folder> <gamedata_folder>")
        sys.exit(1)

    rom_folder   = Path(sys.argv[1])
    gamedata_dir = Path(sys.argv[2])

    if not rom_folder.exists():
        print(f"ERROR: ROM folder not found: {rom_folder}")
        sys.exit(1)
    if not gamedata_dir.exists():
        print(f"ERROR: Gamedata folder not found: {gamedata_dir}")
        sys.exit(1)

    # Show first 10 gamedata subfolders so we know the naming convention
    print("=== First 10 gamedata subfolders ===")
    subdirs = sorted(d.name for d in gamedata_dir.iterdir() if d.is_dir())
    for d in subdirs[:10]:
        has_bram = (gamedata_dir / d / "bram.srm").exists()
        print(f"  {'[bram.srm]' if has_bram else '[no save] '} {d}")
    print(f"  ... ({len(subdirs)} total)\n")

    # Scan ROMs and check matches
    roms = sorted(
        f for f in rom_folder.rglob("*")
        if f.is_file() and f.suffix.lower() in ROM_EXTENSIONS
    )
    print(f"=== ROM scan: {len(roms)} ROMs found in {rom_folder} ===")

    matched = 0
    no_save = 0
    for rom in roms:
        game_dir = gamedata_dir / rom.name   # EverDrive uses full filename incl. extension
        if game_dir.is_dir():
            bram = game_dir / "bram.srm"
            new_stem = rn.normalize_name(rom.name)
            new_game_dir = gamedata_dir / (new_stem + rom.suffix)
            status = "MATCH+BRAM" if bram.exists() else "MATCH-no-bram"
            rename_needed = new_game_dir != game_dir
            print(f"  [{status}] {rom.name}")
            if rename_needed:
                print(f"            folder rename: {game_dir.name}/ -> {new_game_dir.name}/")
            matched += 1
        else:
            no_save += 1

    print(f"\nSummary: {matched} ROMs have a gamedata subfolder, {no_save} do not.")
    if matched == 0:
        print("\nPossible causes:")
        print("  1. The gamedata folder names don't exactly match the ROM filenames (minus extension)")
        print("  2. No ROMs have been played yet on the EverDrive")
        print("\nCompare the ROM filenames above with the gamedata subfolder names shown at the top.")

if __name__ == "__main__":
    main()
