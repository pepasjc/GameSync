#!/usr/bin/env python3
"""
Match NES/Famicom translations to their Japanese originals.
Improved algorithm: uses Japanese title chunks for better matching.
"""
import json
import re
from pathlib import Path
from rom_normalizer import load_no_intro_dat

def _to_base_slug(name: str) -> str:
    """Convert name to base slug for comparison."""
    name = re.sub(r'\s*\([^)]*\)$', '', name)
    name = re.sub(r'\s*\([^)]*\)', '', name)
    name = re.sub(r'[^\w\s]', '', name)
    name = re.sub(r'\s+', ' ', name).lower()
    return name.strip()

def _extract_japanese_slug(name: str) -> str:
    """Extract the Japanese part before any English translation."""
    # Most Japanese games in NES DAT are: "Kanji Hiragana - English Title (Japan)"
    # We want to extract the Japanese part
    if ' - ' in name:
        parts = name.split(' - ')
        jp_part = parts[0].strip()
        if jp_part and any(ord(c) > 127 for c in jp_part):
            return jp_part
    return None

def _keyword_overlap_lenient(trans_name: str, original_name: str) -> float:
    """
    Improved keyword overlap scoring.
    Gives bonus for matching Japanese content and full-substring matches.
    """
    trans_slug = _to_base_slug(trans_name)
    orig_slug = _to_base_slug(original_name)

    # Extract Japanese part of original
    jp_slug = _extract_japanese_slug(original_name)
    if jp_slug:
        jp_slug = _to_base_slug(jp_slug)

    # Full slug match = auto match
    if trans_slug == orig_slug:
        return 1.0

    # If translation contains the Japanese title portion, bonus
    if jp_slug and jp_slug in trans_slug:
        return 0.9

    # If translation is contained in original, that's good
    if trans_slug in orig_slug or orig_slug in trans_slug:
        return 0.85

    # Otherwise use word overlap
    trans_words = set(trans_slug.split())
    orig_words = set(orig_slug.split())

    if not trans_words or not orig_words:
        return 0.0

    intersection = trans_words & orig_words
    union = trans_words | orig_words

    return len(intersection) / len(union) if union else 0.0

def find_best_match(trans_name: str, no_intro_dict: dict, min_score: float = 0.3) -> tuple:
    """
    Find best matching Japan original for a translation.
    Returns (matched_name, score) or (None, 0.0)
    """
    best_match = None
    best_score = min_score

    for orig_name in no_intro_dict.values():
        # Only match Japan originals
        if "(Japan)" not in orig_name:
            continue

        # Check keyword overlap
        score = _keyword_overlap_lenient(trans_name, orig_name)
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
    print("\nFinding matches (with improved algorithm)...")
    matches = {}
    low_confidence = []
    no_matches = []

    for trans_name in sorted(unmatched.keys()):
        best_match, score = find_best_match(trans_name, no_intro_dict, min_score=0.0)

        if best_match is None:
            no_matches.append(trans_name)
        elif score < 0.65:
            low_confidence.append((trans_name, best_match, score))
        else:
            matches[trans_name] = best_match

    print(f"  High confidence matches (>=0.65): {len(matches)}")
    print(f"  Medium/Low confidence (<0.65): {len(low_confidence)}")
    print(f"  No matches found: {len(no_matches)}")

    # Show results for manual review
    if low_confidence:
        print("\nMedium/Low confidence matches (score < 0.65):")
        for trans, orig, score in sorted(low_confidence, key=lambda x: -x[2])[:50]:
            print(f"  [{score:.2f}] {trans}")
            print(f"         -> {orig}")

    # Show no matches
    if no_matches:
        print(f"\nNo Japan matches found ({len(no_matches)} entries):")
        for name in sorted(no_matches):
            print(f"  {name}")

    # Update aliases with high confidence only
    print("\nUpdating aliases.json...")
    for trans_name, orig_name in matches.items():
        nes_aliases[trans_name] = orig_name

    with open(aliases_path, 'w') as f:
        json.dump(aliases, f, indent=2, ensure_ascii=False)

    remaining = sum(1 for v in nes_aliases.values() if v == "")
    print(f"  Updated {len(matches)} entries")
    print(f"  Remaining unmatched: {remaining}")
    print(f"\nManual review needed:")
    print(f"  - {len(low_confidence)} medium/low confidence matches")
    print(f"  - {len(no_matches)} entries with no matches (try Google)")

if __name__ == "__main__":
    main()
