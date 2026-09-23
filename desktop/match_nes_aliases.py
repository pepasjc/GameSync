#!/usr/bin/env python3
"""
Match NES/Famicom translations to their Japanese originals.
Respects region rule: Japan translations -> Japan originals only.
"""
import json
import re
from pathlib import Path
from difflib import SequenceMatcher
from rom_normalizer import load_no_intro_dat, find_dat_for_system, build_name_index

def _to_base_slug(name: str) -> str:
    """Convert name to base slug for comparison."""
    # Remove version info, region, and special markers
    name = re.sub(r'\s*\([^)]*\)$', '', name)  # Remove trailing parens
    name = re.sub(r'\s*\([^)]*\)', '', name)   # Remove all parens
    name = re.sub(r'[^\w\s]', '', name)        # Remove special chars
    name = re.sub(r'\s+', ' ', name).lower()   # Normalize whitespace
    return name.strip()

def _keyword_overlap(trans_name: str, original_name: str) -> float:
    """Score keyword overlap between translation and original."""
    trans_slug = _to_base_slug(trans_name)
    orig_slug = _to_base_slug(original_name)

    trans_words = set(trans_slug.split())
    orig_words = set(orig_slug.split())

    if not trans_words or not orig_words:
        return 0.0

    intersection = trans_words & orig_words
    union = trans_words | orig_words

    return len(intersection) / len(union) if union else 0.0

def find_best_match(trans_name: str, no_intro_dict: dict) -> tuple:
    """
    Find best matching Japan original for a translation.
    Returns (matched_name, score) or (None, 0.0)
    """
    best_match = None
    best_score = 0.0

    for orig_name in no_intro_dict.values():
        # Only match Japan originals
        if "(Japan)" not in orig_name:
            continue

        # Check keyword overlap
        score = _keyword_overlap(trans_name, orig_name)
        if score > best_score:
            best_score = score
            best_match = orig_name

    return best_match, best_score

def main():
    # Load standard NES DAT
    print("Loading standard NES DAT...")
    nes_dat_path = Path("../server/data/dats/Nintendo - Nintendo Entertainment System.dat")
    if not nes_dat_path.exists():
        print(f"ERROR: {nes_dat_path} not found")
        return

    no_intro_dict = load_no_intro_dat(str(nes_dat_path))
    print(f"  Loaded {len(no_intro_dict)} entries from NES DAT")

    # Load current aliases
    print("\nLoading aliases.json...")
    aliases_path = Path("../server/data/dats/EN-Dats/aliases.json")
    with open(aliases_path) as f:
        aliases = json.load(f)

    nes_aliases = aliases.get("NES", {})
    unmatched = {k: v for k, v in nes_aliases.items() if v == ""}
    print(f"  Found {len(unmatched)} unmatched NES entries")

    # Find matches
    print("\nFinding matches...")
    matches = {}
    no_matches = []
    low_confidence = []  # score < 0.5

    for trans_name in sorted(unmatched.keys()):
        best_match, score = find_best_match(trans_name, no_intro_dict)

        if best_match is None:
            no_matches.append(trans_name)
        elif score < 0.5:
            low_confidence.append((trans_name, best_match, score))
        else:
            matches[trans_name] = best_match

    print(f"  High confidence matches: {len(matches)}")
    print(f"  Low confidence (<0.5): {len(low_confidence)}")
    print(f"  No matches found: {len(no_matches)}")

    # Show low confidence for review
    if low_confidence:
        print("\nLow confidence matches (manual review recommended):")
        for trans, orig, score in sorted(low_confidence, key=lambda x: -x[2])[:30]:
            print(f"  [{score:.2f}] {trans}")
            print(f"         -> {orig}")

    # Show no matches
    if no_matches:
        print(f"\nNo Japan matches found ({len(no_matches)} entries):")
        for name in sorted(no_matches)[:20]:
            print(f"  {name}")
        if len(no_matches) > 20:
            print(f"  ... and {len(no_matches) - 20} more")

    # Update aliases
    print("\nUpdating aliases.json...")
    for trans_name, orig_name in matches.items():
        nes_aliases[trans_name] = orig_name

    with open(aliases_path, 'w') as f:
        json.dump(aliases, f, indent=2, ensure_ascii=False)

    remaining = sum(1 for v in nes_aliases.values() if v == "")
    print(f"  Updated {len(matches)} entries")
    print(f"  Remaining unmatched: {remaining}")

if __name__ == "__main__":
    main()
