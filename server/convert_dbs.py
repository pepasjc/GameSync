#!/usr/bin/env python3
"""
Convert PSP, PS Vita, and PSX game databases to the 3DSS/dstdb format.

Input formats:
    - TSV files (from No-Intro): tab-separated with headers
      Columns: Title ID | Region | Name | PKG link | zRIF | Content ID | ...
    - TXT files with comments: # comments, then ID,Name format

Output format (no comments, no line numbers):
    ID,Game Name

Usage:
    python3 convert_dbs.py [--psp input.tsv] [--psv input.tsv] [--psx input.tsv] [--output-dir DIR]
"""

import argparse
import os
import re
from pathlib import Path


def parse_tsv_file(filepath):
    """Parse a TSV file (like from No-Intro) extracting Title ID and Name."""
    entries = []
    
    if not os.path.exists(filepath):
        return entries
    
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        lines = f.readlines()
    
    if not lines:
        return entries
    
    # Check if first line is a header (contains "Title ID" or similar)
    header = lines[0].lower()
    start_idx = 0
    if 'title id' in header or 'product code' in header:
        start_idx = 1
    
    # Find column indices
    headers = lines[0].split('\t')
    title_id_col = None
    name_col = None
    
    for i, h in enumerate(headers):
        h = h.strip().lower()
        if h == 'title id' or h == 'product code':
            title_id_col = i
        elif h == 'name' or h == 'game name':
            name_col = i
    
    # Fallback: assume column 0 is ID, column 2 is name (common TSV format)
    if title_id_col is None:
        title_id_col = 0
    if name_col is None:
        name_col = 2
    
    for line in lines[start_idx:]:
        line = line.strip()
        if not line:
            continue
        
        cols = line.split('\t')
        if len(cols) > max(title_id_col, name_col):
            game_id = cols[title_id_col].strip()
            name = cols[name_col].strip()
            if game_id and name:
                entries.append((game_id, name))
    
    return entries


def parse_txt_file(filepath):
    """Parse a TXT file with # comments and ID,Name format."""
    entries = []
    
    if not os.path.exists(filepath):
        return entries
    
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            line = line.strip()
            # Skip empty lines and comment lines
            if not line or line.startswith('#'):
                continue
            
            # Parse ID,Name format
            # Handle cases where name contains commas by finding first comma
            idx = line.find(',')
            if idx > 0:
                game_id = line[:idx].strip()
                name = line[idx+1:].strip()
                if game_id and name:
                    entries.append((game_id, name))
    
    return entries


def parse_db_file(filepath):
    """Auto-detect and parse either TSV or TXT format."""
    if not os.path.exists(filepath):
        return []
    
    # Read first line to detect format
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        first_line = f.readline()
    
    if '\t' in first_line:
        return parse_tsv_file(filepath)
    else:
        return parse_txt_file(filepath)


def write_db_file(entries, output_path):
    """Write entries to output file in ID,Name format (no comments)."""
    with open(output_path, 'w', encoding='utf-8') as f:
        for game_id, name in entries:
            f.write(f"{game_id},{name}\n")
    return len(entries)


def generate_psx_db():
    """Generate a basic PSX database from common game IDs.
    
    PSX games on PSP/PS Vita use UMD/ISO disc IDs.
    Common format: SCUS-XXXXXX, SCES-XXXXXX, etc.
    """
    # Common PSOne Classics / PSX games
    # Format: product code, game name
    common_psx = [
        ("SCUS-94153", "Chrono Cross"),
        ("SCUS-94154", "Chrono Trigger"),
        ("SCUS-94227", "Final Fantasy VII"),
        ("SCUS-94228", "Final Fantasy VIII"),
        ("SCUS-94229", "Final Fantasy IX"),
        ("SCUS-94320", "Xenogears"),
        ("SCUS-94126", "Suikoden"),
        ("SCUS-94127", "Suikoden II"),
        ("SCUS-97502", "Metal Gear Solid"),
        ("SCUS-97503", "Metal Gear Solid: Ghost in the Hills"),
        ("SCUS-97426", "Syphon Filter"),
        ("SCUS-97130", "Tekken 3"),
        ("SCUS-94184", "Resident Evil 2"),
        ("SCUS-94187", "Resident Evil 3: Nemesis"),
        ("SCUS-94163", "Castlevania: Symphony of the Night"),
        ("SCUS-94140", "Crash Bandicoot"),
        ("SCUS-94141", "Crash Bandicoot 2: Cortex Strikes Back"),
        ("SCUS-94142", "Crash Bandicoot 3: Warped"),
        ("SCUS-94139", "Spyro the Dragon"),
        ("SCUS-94143", "Spyro 2: Ripto's Rage!"),
        ("SCUS-94144", "Spyro: Year of the Dragon"),
        ("SCUS-94195", "Medal of Honor"),
        ("SCUS-94123", "Parasite Eve"),
        ("SCUS-94125", "Parasite Eve II"),
        ("SCUS-94424", "Star Wars: Jedi Power Battles"),
        ("SCUS-94425", "Star Wars: Rebel Assault II"),
        ("SCUS-94156", "Brave Fencer Musashi"),
        ("SCUS-94157", "Legend of Mana"),
        ("SCUS-94158", "Chrono Cross"),
        ("SCUS-94321", "Vagrant Story"),
        ("SCUS-94421", "Wild Arms"),
        ("SCUS-94422", "Wild Arms II"),
        ("SCUS-94173", "Dino Crisis"),
        ("SCUS-94174", "Dino Crisis 2"),
        ("SCUS-94179", "Silent Hill"),
        ("SCUS-94180", "Silent Hill 2"),
        ("SCUS-97506", "MGS: Special Missions"),
        ("SCUS-94501", "Street Fighter Alpha 2"),
        ("SCUS-94502", "Street Fighter Alpha 3"),
        ("SCUS-94322", "Persona 2: Eternal Punishment"),
        ("SCUS-94323", "Persona 2: Innocent Sin"),
        ("SCUS-94507", "Street Fighter EX2"),
        ("SCUS-94508", "Street Fighter EX3"),
        ("SCES-00176", "Wipeout"),
        ("SCES-00308", "Wipeout XL"),
        ("SCES-01013", "Wipeout Fusion"),
        ("SCUS-98204", "Twisted Metal"),
        ("SCUS-98205", "Twisted Metal 2"),
        ("SCUS-98206", "Twisted Metal 3"),
        ("SCUS-98207", "Twisted Metal: Black"),
        ("SCUS-98115", "Gran Turismo"),
        ("SCUS-98116", "Gran Turismo 2"),
        ("SCUS-98134", "Gran Turismo 3 A-Spec"),
        ("SCUS-94147", "Ape Escape"),
        ("SCUS-94148", "Ape Escape 2"),
        ("SCUS-94328", "Time Crisis"),
        ("SCUS-94329", "Time Crisis II"),
        ("SCUS-94330", "Time Crisis: Razing Storm"),
        ("SCUS-97501", "Oddworld: Abe's Oddysee"),
        ("SCUS-97525", "Oddworld: Abe's Exoddus"),
        ("SCUS-94160", "Rayman"),
        ("SCUS-94161", "Rayman 2: The Great Escape"),
        ("SCUS-94162", "Rayman 3: Hoodlum Havoc"),
        ("SCES-01450", "Grand Theft Auto"),
        ("SCES-01451", "Grand Theft Auto 2"),
        ("SCUS-94200", "Grand Theft Auto III"),
        ("SCUS-94201", "Grand Theft Auto: Vice City"),
        ("SCUS-94202", "Grand Theft Auto: San Andreas"),
        ("SCUS-94181", "Tom Clancy's Rainbow Six"),
        ("SCUS-94182", "Tom Clancy's Rainbow Six: Rogue Spear"),
        ("SCUS-94520", "Sly Cooper"),
        ("SCUS-94521", "Sly 2: Band of Thieves"),
        ("SCUS-94522", "Sly 3: Honor Among Thieves"),
        ("SCUS-94121", "Klonoa"),
        ("SCUS-94122", "Klonoa 2: Dream Champ Tournament"),
        ("SCUS-94149", "Ridge Racer"),
        ("SCUS-94150", "Ridge Racer 2"),
        ("SCUS-94151", "Ridge Racer Type 4"),
        ("SCUS-94325", "Armored Core"),
        ("SCUS-94326", "Armored Core 2"),
        ("SCUS-94327", "Armored Core: Master of Arena"),
        ("SCES-02728", "Tekken"),
        ("SCES-02729", "Tekken 2"),
        ("SCUS-94155", "Destruction Derby Raw"),
        ("SCUS-94338", "Need for Speed III: Hot Pursuit"),
        ("SCUS-94339", "Need for Speed: High Stakes"),
        ("SCUS-94340", "Need for Speed: Porsche Unleashed"),
        ("SCUS-94341", "Need for Speed: Hot Pursuit 2"),
        ("SCUS-94513", "NBA Live 98"),
        ("SCUS-94514", "NBA Live 99"),
        ("SCUS-94515", "NBA Live 2000"),
        ("SCUS-94516", "NBA Live 2001"),
        ("SCUS-94517", "NBA Live 2002"),
        ("SCUS-94518", "NBA Live 2003"),
        ("SCUS-94519", "NBA Live 2004"),
        ("SCUS-94190", "Madden NFL 98"),
        ("SCUS-94191", "Madden NFL 99"),
        ("SCUS-94192", "Madden NFL 2000"),
        ("SCUS-94193", "Madden NFL 2001"),
        ("SCUS-94194", "Madden NFL 2002"),
        ("SCUS-94189", "FIFA Soccer 97"),
        ("SCUS-94186", "FIFA Soccer 98"),
        ("SCUS-94188", "FIFA Soccer 99"),
        ("SCUS-94331", "FIFA Soccer 2000"),
        ("SCUS-94332", "FIFA Soccer 2001"),
        ("SCUS-94333", "FIFA Soccer 2002"),
        ("SCUS-94334", "FIFA Soccer 2003"),
        ("SCUS-94335", "FIFA Soccer 2004"),
        ("SCUS-94336", "FIFA Soccer 2005"),
        ("SCUS-94165", "Tony Hawk's Pro Skater"),
        ("SCUS-94166", "Tony Hawk's Pro Skater 2"),
        ("SCUS-94167", "Tony Hawk's Pro Skater 3"),
        ("SCUS-94168", "Tony Hawk's Pro Skater 4"),
        ("SCUS-94169", "Tony Hawk's Underground"),
        ("SCUS-94170", "Tony Hawk's Underground 2"),
        ("SCUS-94171", "Tony Hawk's American Wasteland"),
        ("SCUS-94426", "Hot Shots! Golf"),
        ("SCUS-94427", "Hot Shots! Golf 2"),
        ("SCUS-94428", "Hot Shots! Golf 3"),
        ("SCUS-94176", "Sonic the Hedgehog"),
        ("SCUS-94177", "Sonic the Hedgehog 2"),
        ("SCUS-94178", "Sonic the Hedgehog 3"),
        ("SCUS-94175", "Sonic Mega Collection"),
        ("SCUS-94172", "Sonic Heroes"),
        ("SCUS-97504", "PaRappa the Rapper"),
        ("SCUS-97505", "Um Jammer Lammy"),
        ("SCUS-94164", "Kula World / Roll Away"),
        ("SCUS-94342", "Cool Boarders"),
        ("SCUS-94343", "Cool Boarders 2"),
        ("SCUS-94344", "Cool Boarders 3"),
        ("SCUS-94345", "Cool Boarders 4"),
        ("SCUS-94346", "Cool Boarders: Burning Edges"),
        ("SCUS-97509", "Lemmings"),
        ("SCUS-97510", "Lemmings 3D"),
        ("SCUS-97511", "The Adventures of Lomax"),
        ("SCUS-97512", "Eye of the Beholder"),
        ("SCES-00001", "Silly Putty"),
        ("SCUS-94101", "Pong"),
    ]
    
    # Add more common PSX IDs
    # Europe
    europe_prefixes = ["SCES", "SCES", "PBPX", "PES", "PEZX"]
    # Japan  
    japan_prefixes = ["SCPM", "SCPS", "SLPM", "SLPS", "PAPX", "PZL2"]
    # USA
    usa_prefixes = ["SCUS", "PBPX", "PES", "PEZX"]
    
    return common_psx


def main():
    parser = argparse.ArgumentParser(
        description='Convert PSP, PS Vita, and PSX game databases to 3DSS format'
    )
    parser.add_argument(
        '--psp', 
        default='data/PSP_GAMES.tsv',
        help='Input PSP database file (default: data/PSP_GAMES.tsv)'
    )
    parser.add_argument(
        '--psv',
        default='data/PSV_GAMES.tsv',
        help='Input PS Vita database file (default: data/PSV_GAMES.tsv)'
    )
    parser.add_argument(
        '--psx',
        default='data/PSX_GAMES.tsv',
        help='Input PSX database file (default: data/PSX_GAMES.tsv)'
    )
    parser.add_argument(
        '--output-dir',
        default='data',
        help='Output directory (default: data)'
    )
    parser.add_argument(
        '--psx-generate',
        action='store_true',
        help='Generate default PSX database with common game IDs (ignores --psx)'
    )
    
    args = parser.parse_args()
    
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Process PSP database
    if os.path.exists(args.psp):
        psp_entries = parse_db_file(args.psp)
        psp_output = output_dir / 'pspdb.txt'
        count = write_db_file(psp_entries, psp_output)
        print(f"PSP: {count} entries -> {psp_output}")
    else:
        print(f"PSP: {args.psp} not found, skipping")
    
    # Process PS Vita database
    if os.path.exists(args.psv):
        psv_entries = parse_db_file(args.psv)
        psv_output = output_dir / 'vsvdb.txt'
        count = write_db_file(psv_entries, psv_output)
        print(f"PS Vita: {count} entries -> {psv_output}")
    else:
        print(f"PS Vita: {args.psv} not found, skipping")
    
    # Process PSX database
    if args.psx_generate:
        psx_entries = generate_psx_db()
        psx_output = output_dir / 'psxdb.txt'
        count = write_db_file(psx_entries, psx_output)
        print(f"PSX: {count} generated entries -> {psx_output}")
    elif args.psx and os.path.exists(args.psx):
        psx_entries = parse_db_file(args.psx)
        psx_output = output_dir / 'psxdb.txt'
        count = write_db_file(psx_entries, psx_output)
        print(f"PSX: {count} entries -> {psx_output}")
    else:
        print("PSX: no input file and --psx-generate not specified, skipping")


if __name__ == '__main__':
    main()
