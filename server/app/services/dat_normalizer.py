"""
No-Intro / Redump DAT file normalizer service.

Drop any No-Intro or Redump XML DAT file into  server/data/dats/
The filename is used to auto-detect the system, e.g.:
  "Nintendo - Game Boy Advance (20240101-123456).dat"  → GBA
  "Sega - Mega Drive - Genesis.dat"                    → GEN
  "Sony - PlayStation.dat"                             → PS1

Lookup order for each ROM:
  1. CRC32  (exact match — most accurate)
  2. Filename slug  (normalized against DAT name index)
  3. Fallback — just normalize the filename string
"""

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from app.services.rom_id import normalize_rom_name


# ---------------------------------------------------------------------------
# DAT filename → system code mapping
# Ordered so that more-specific strings appear before shorter ones.
# ---------------------------------------------------------------------------
_DAT_SYSTEM_MAP: list[tuple[str, str]] = [
    # Nintendo handhelds
    ("game boy advance",        "GBA"),
    ("game boy color",          "GBC"),
    ("game boy",                "GB"),    # must come after GBC / GBA
    ("nintendo - nes",          "NES"),
    ("nintendo entertainment",  "NES"),
    ("famicom",                 "NES"),
    ("super nintendo",          "SNES"),
    ("snes",                    "SNES"),
    ("sfc",                     "SNES"),
    ("nintendo 64",             "N64"),
    ("nintendo - ds",           "NDS"),
    ("nintendo ds",             "NDS"),
    # Nintendo home
    ("gamecube",                "GC"),
    ("nintendo - wii",          "WII"),
    # Sony
    ("playstation2",            "PS2"),
    ("playstation 2",           "PS2"),
    ("playstation portable",    "PSP"),
    ("psp",                     "PSP"),
    ("playstation",             "PS1"),   # catch-all — after PS2 / PSP
    # Sega
    ("mega-cd",                 "SCD"),
    ("mega cd",                 "SCD"),
    ("sega cd",                 "SCD"),
    ("mega drive",              "GEN"),
    ("genesis",                 "GEN"),
    ("sega master system",      "SMS"),
    ("master system",           "SMS"),
    ("game gear",               "GG"),
    ("saturn",                  "SAT"),
    ("dreamcast",               "DC"),
    # SNK
    ("neo geo pocket",          "NGP"),
    ("neogeo pocket",           "NGP"),
    ("neo geo cd",              "NEOCD"),
    ("neogeo cd",               "NEOCD"),
    # NEC
    ("pc engine",               "PCE"),
    ("turbografx",              "PCE"),
    # Bandai
    ("wonderswan",              "WS"),
    # Atari
    ("atari 2600",              "A2600"),
    ("atari 7800",              "A7800"),
    ("atari lynx",              "LYNX"),
    ("lynx",                    "LYNX"),
    # Arcade
    ("mame",                    "MAME"),
    ("arcade",                  "ARCADE"),
]


# ---------------------------------------------------------------------------
# Region priority for ranking candidates (lower = better / higher priority)
# ---------------------------------------------------------------------------

def _region_score(canonical: str) -> int:
    """Score a canonical name by region preference. Lower = more preferred."""
    n = canonical.lower()
    if "(usa)" in n:    return 0
    if "(world)" in n:  return 1
    if "(europe)" in n: return 2
    if "(japan)" in n:  return 3
    # Demo / kiosk / proto / beta should lose to any real release
    if any(x in n for x in ["(demo)", "(kiosk", "(proto", "(beta", "(sample)"]):
        return 99
    return 10  # other / unknown region


def _system_from_dat_stem(stem: str) -> Optional[str]:
    """Return system code for a DAT file stem, or None if unrecognized."""
    lower = stem.lower()
    for keyword, code in _DAT_SYSTEM_MAP:
        if keyword in lower:
            return code
    return None


# ---------------------------------------------------------------------------
# DatNormalizer class
# ---------------------------------------------------------------------------

class DatNormalizer:
    """Loads No-Intro/Redump DAT files and provides ROM name lookups."""

    def __init__(self, dats_dir: Path):
        self.dats_dir = dats_dir
        # system → {CRC32_UPPER_8CHARS → canonical_name}
        self._crc_index: dict[str, dict[str, str]] = {}
        # system → {slug → canonical_name}  (best by region priority)
        self._slug_index: dict[str, dict[str, str]] = {}
        # system → {slug → [canonical_name, ...]}  (all candidates, for the picker)
        self._slug_candidates: dict[str, dict[str, list[str]]] = {}
        self._load_all()

    def _load_all(self) -> None:
        if not self.dats_dir.is_dir():
            return
        for dat_path in sorted(self.dats_dir.glob("*.dat")):
            system = _system_from_dat_stem(dat_path.stem)
            if not system:
                print(f"[dat_normalizer] Skipped (unrecognized system): {dat_path.name}")
                continue
            crc_map, slug_cands = _parse_dat(dat_path)
            # Merge CRC index
            self._crc_index.setdefault(system, {}).update(crc_map)
            # Merge slug candidates
            sys_cands = self._slug_candidates.setdefault(system, {})
            for slug, names in slug_cands.items():
                existing = sys_cands.setdefault(slug, [])
                for n in names:
                    if n not in existing:
                        existing.append(n)
            # Rebuild best-per-slug index using region priority
            sys_idx = self._slug_index.setdefault(system, {})
            for slug, names in sys_cands.items():
                sys_idx[slug] = min(names, key=_region_score)
            print(
                f"[dat_normalizer] {system}: +{len(crc_map)} CRC32 "
                f"+{sum(len(v) for v in slug_cands.values())} names  [{dat_path.name}]"
            )

    def normalize(
        self,
        system: str,
        filename: str,
        crc32: Optional[str] = None,
    ) -> dict:
        """Return normalization for one ROM.

        Result dict keys:
          canonical_name — best human-readable name
          slug           — lowercase_underscore slug used in title_id
          source         — "dat_crc32" | "dat_filename" | "filename"
        """
        sys_key = system.upper()
        stem = Path(filename).stem

        # 1. CRC32 exact lookup
        if crc32:
            crc_padded = crc32.upper().zfill(8)
            if crc_padded in self._crc_index.get(sys_key, {}):
                canonical = self._crc_index[sys_key][crc_padded]
                return {
                    "canonical_name": canonical,
                    "slug": normalize_rom_name(canonical),
                    "source": "dat_crc32",
                }

        # 2. Slug fuzzy lookup
        query_slug = normalize_rom_name(stem)
        if query_slug in self._slug_index.get(sys_key, {}):
            canonical = self._slug_index[sys_key][query_slug]
            return {
                "canonical_name": canonical,
                "slug": normalize_rom_name(canonical),
                "source": "dat_filename",
            }

        # 3. Fallback — normalize filename as-is
        return {
            "canonical_name": stem,
            "slug": query_slug,
            "source": "filename",
        }

    def search_candidates(self, system: str, filename: str) -> list[str]:
        """Return all canonical names matching filename, sorted by region priority.

        Returns the empty list when no DAT entry matches the slug.
        The first entry is the recommended (USA-first) choice.
        """
        sys_key = system.upper()
        query_slug = normalize_rom_name(Path(filename).stem)
        raw = self._slug_candidates.get(sys_key, {}).get(query_slug, [])
        return sorted(set(raw), key=_region_score)

    def available_systems(self) -> list[str]:
        return sorted(set(self._crc_index) | set(self._slug_index))

    def stats(self) -> dict[str, dict[str, int]]:
        return {
            sys: {
                "crc32_entries": len(self._crc_index.get(sys, {})),
                "name_entries": len(self._slug_index.get(sys, {})),
            }
            for sys in self.available_systems()
        }


# ---------------------------------------------------------------------------
# Module-level singleton — initialized in FastAPI lifespan
# ---------------------------------------------------------------------------

_normalizer: Optional[DatNormalizer] = None


def init(dats_dir: Path) -> None:
    global _normalizer
    _normalizer = DatNormalizer(dats_dir)


def get() -> Optional[DatNormalizer]:
    return _normalizer


# ---------------------------------------------------------------------------
# Internal DAT XML parser
# ---------------------------------------------------------------------------

def _parse_dat(dat_path: Path) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Parse a No-Intro/Redump XML DAT.

    Returns:
      crc_map        — {CRC32_UPPER_8 → canonical_name}
      slug_candidates — {slug → [canonical_name, ...]}  (all variants per slug)
    """
    crc_map: dict[str, str] = {}
    slug_candidates: dict[str, list[str]] = {}
    try:
        tree = ET.parse(dat_path)
        root = tree.getroot()
        for game in root.findall("game"):
            canonical = game.get("name", "").strip()
            if not canonical:
                continue
            slug = normalize_rom_name(canonical)
            slug_candidates.setdefault(slug, []).append(canonical)
            for rom in game.findall("rom"):
                crc = rom.get("crc", "").upper().zfill(8)
                if crc and crc != "00000000":
                    crc_map[crc] = canonical
    except Exception as exc:
        print(f"[dat_normalizer] Failed to parse {dat_path.name}: {exc}")
    return crc_map, slug_candidates
