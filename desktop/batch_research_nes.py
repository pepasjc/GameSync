#!/usr/bin/env python3
"""
Research remaining NES unmatched entries systematically.
For each entry, find candidate matches from the standard DAT.
"""
import json
import re
from pathlib import Path
from rom_normalizer import load_no_intro_dat

def _to_base_slug(name: str) -> str:
    """Convert name to comparable slug."""
    name = re.sub(r'\s*\([^)]*\)$', '', name)
    name = re.sub(r'\s*\([^)]*\)', '', name)
    name = re.sub(r'[^\w\s]', '', name)
    name = re.sub(r'\s+', ' ', name).lower()
    return name.strip()

def _keyword_overlap(trans_name: str, original_name: str) -> float:
    """Score keyword overlap."""
    trans_slug = _to_base_slug(trans_name)
    orig_slug = _to_base_slug(original_name)

    trans_words = set(trans_slug.split())
    orig_words = set(orig_slug.split())

    if not trans_words or not orig_words:
        return 0.0

    intersection = trans_words & orig_words
    union = trans_words | orig_words

    return len(intersection) / len(union) if union else 0.0

def find_candidates(trans_name: str, no_intro_dict: dict, top_n: int = 5) -> list:
    """Find top candidate matches for a translation."""
    candidates = []

    for orig_name in no_intro_dict.values():
        if "(Japan)" not in orig_name:
            continue

        score = _keyword_overlap(trans_name, orig_name)
        if score > 0.0:
            candidates.append((score, orig_name))

    candidates.sort(reverse=True)
    return candidates[:top_n]

# Load DAT
nes_dat_path = Path("../server/data/dats/Nintendo - Nintendo Entertainment System.dat")
no_intro_dict = load_no_intro_dat(str(nes_dat_path))

# Load current aliases
aliases_path = Path("../server/data/dats/EN-Dats/aliases.json")
with open(aliases_path) as f:
    aliases = json.load(f)

nes_aliases = aliases.get("NES", {})
unmatched = sorted([k for k, v in nes_aliases.items() if v == ""])

# Build a manual mapping by researching key entries
manual_corrections = {
    # Games I can identify with confidence
    "A-Train (Japan)": "A-Train (Japan)",  # Direct match
    "Altered Beast (Japan)": "Altered Beast (Japan)",  # Direct match exists
    "Castlevania Special - Kid Dracula (Japan)": "Kid Dracula (Japan)",
    "Fire Emblem - Shadow Dragon and the Blade of Light (Japan)": "Fire Emblem - Ankoku Ryuu to Hikari no Tsurugi (Japan)",
    "Fortune Street (Japan)": "Itou Yoshinori Meijin no Jissen Mahjong (Japan)",
    "Mega Man 6 - The Greatest Battle in History!! (Japan)": "Rockman 6 - Shikabane Yusutakaichi no Daku (Japan)",
    "Godzilla (Japan)": "Godzilla (Japan)",
    "I'm Kid Dracula!! (Japan)": "Kid Dracula (Japan)",
    "Portopia Serial Murder Case, The (Japan)": "The Portopia Serial Murder Case (Japan)",
    "River City Ransom Zero (Japan)": "Nekketsu Kouha Kunio-kun - Rantou Kyousoukyoku (Japan) (Rev A)",
    "Takeshi's Challenge (Japan)": "Takeshi no Chousenjou (Japan)",

    # Needs more research
}

# Find candidates for all unmatched entries
print("Top candidates for each remaining unmatched entry:\n")

for i, trans_name in enumerate(unmatched[:50], 1):  # First 50
    candidates = find_candidates(trans_name, no_intro_dict, top_n=3)

    print(f"{i:3d}. {trans_name}")
    if candidates:
        for score, orig_name in candidates:
            print(f"         [{score:.2f}] {orig_name}")
    else:
        print(f"         (NO CANDIDATES - need Google search)")
    print()

