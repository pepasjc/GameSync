#!/usr/bin/env python3
"""
Parse unsorted.txt and split into PSP, PS Vita, and PS3 databases.
- If "[PSP]" in title -> pspdb.txt
- If "[PSVita]" in title -> vsvdb.txt  
- Otherwise -> ps3db.txt

Also clips the [PSP] / [PSVita] suffix and removes non-ASCII characters.
"""

import re
import unicodedata


def remove_non_ascii(text):
    """Remove non-ASCII characters from text, replacing them with closest ASCII equivalents or removing."""
    # Normalize unicode characters
    normalized = unicodedata.normalize('NFKD', text)
    # Encode to ASCII, ignoring errors (removes non-ASCII)
    ascii_text = normalized.encode('ascii', 'ignore').decode('ascii')
    return ascii_text


def process_line(line):
    """Process a single line, returning (game_id, name, platform) or None."""
    line = line.strip()
    if not line:
        return None
    
    # Split by first comma to get ID and rest
    idx = line.find(',')
    if idx <= 0:
        return None
    
    game_id = line[:idx].strip()
    rest = line[idx+1:].strip()
    
    # Determine platform and clip suffix
    platform = None
    name = rest
    
    if '[PSP]' in rest:
        platform = 'psp'
        name = rest.replace('[PSP]', '').strip()
    elif '[PSVita]' in rest:
        platform = 'psvita'
        name = rest.replace('[PSVita]', '').strip()
    elif '[PS3]' in rest:
        platform = 'ps3'
        name = rest.replace('[PS3]', '').strip()
    elif '[PSOne]' in rest:
        platform = 'psp'  # PSOne Classics are PSP games
        name = rest.replace('[PSOne]', '').strip()
    elif '[MINI]' in rest:
        # MINI games are usually PSP
        platform = 'psp'
        name = rest.replace('[MINI]', '').strip()
    elif '[Demo]' in rest:
        # Demo versions - keep them with the base platform
        # Try to infer from game ID prefix
        if game_id.startswith('UL') or game_id.startswith('UC') or game_id.startswith('NP'):
            # Could be PSP or PS3, let's check common prefixes
            if game_id.startswith('ULJS') or game_id.startswith('ULUS') or game_id.startswith('UCJS'):
                platform = 'psp'
            else:
                platform = 'ps3'
        else:
            platform = 'ps3'
        name = rest.replace('[Demo]', '').strip()
    else:
        # Assume PS3
        platform = 'ps3'
    
    # Remove non-ASCII characters
    name = remove_non_ascii(name)
    
    # Remove remaining tags like [MINI], [PSOne], [Demo]
    name = re.sub(r'\[(?:MINI|PSOne|Demo)\]', '', name)
    
    # Clean up any extra whitespace
    name = ' '.join(name.split())
    
    return (game_id, name, platform)


def main():
    input_file = 'data/unsorted.txt'
    output_dir = 'data'
    
    psp_entries = []
    psv_entries = []
    ps3_entries = []
    
    with open(input_file, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            result = process_line(line)
            if result:
                game_id, name, platform = result
                if platform == 'psp':
                    psp_entries.append((game_id, name))
                elif platform == 'psvita':
                    psv_entries.append((game_id, name))
                elif platform == 'ps3':
                    ps3_entries.append((game_id, name))
    
    # Write output files
    with open(f'{output_dir}/pspdb.txt', 'w', encoding='utf-8') as f:
        for game_id, name in psp_entries:
            f.write(f"{game_id},{name}\n")
    
    with open(f'{output_dir}/vsvdb.txt', 'w', encoding='utf-8') as f:
        for game_id, name in psv_entries:
            f.write(f"{game_id},{name}\n")
    
    with open(f'{output_dir}/ps3db.txt', 'w', encoding='utf-8') as f:
        for game_id, name in ps3_entries:
            f.write(f"{game_id},{name}\n")
    
    print(f"PSP: {len(psp_entries)} entries")
    print(f"PS Vita: {len(psv_entries)} entries")
    print(f"PS3: {len(ps3_entries)} entries")
    print(f"Total: {len(psp_entries) + len(psv_entries) + len(ps3_entries)}")


if __name__ == '__main__':
    main()
