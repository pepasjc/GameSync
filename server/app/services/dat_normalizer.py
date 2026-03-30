"""
No-Intro / Redump DAT file normalizer service.

Drop any No-Intro/Redump XML **or** libretro clrmamepro text DAT file into
server/data/dats/.  Both formats are auto-detected by inspecting the first
non-empty line of the file.

The filename is used to auto-detect the system, e.g.:
  "Nintendo - Game Boy Advance (20240101-123456).dat"  → GBA
  "Sega - Mega Drive - Genesis.dat"                    → MD
  "Sony - PlayStation.dat"                             → PS1

Lookup order for each ROM:
  1. CRC32  (exact match — most accurate)
  2. Filename slug  (normalized against DAT name index)
  3. Fallback — just normalize the filename string
"""

import re
import xml.etree.ElementTree as ET
import logging
from pathlib import Path
from typing import Optional

from app.services.rom_id import normalize_rom_name

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DAT filename → system code mapping
# Ordered so that more-specific strings appear before shorter ones.
# ---------------------------------------------------------------------------
_DAT_SYSTEM_MAP: list[tuple[str, str]] = [
    # Nintendo handhelds
    ("game boy advance", "GBA"),
    ("game boy color", "GBC"),
    ("game boy", "GB"),  # must come after GBC / GBA
    ("super nintendo entertainment system", "SNES"),  # must come before NES catch-alls
    ("nintendo - nes", "NES"),
    ("nintendo entertainment", "NES"),
    ("famicom", "NES"),
    ("super nintendo", "SNES"),
    ("snes", "SNES"),
    ("sfc", "SNES"),
    ("nintendo 64", "N64"),
    ("nintendo - nintendo 3ds (digital)", "3DS"),  # before plain 3DS
    ("nintendo - nintendo 3ds", "3DS"),
    ("nintendo - ds", "NDS"),
    ("nintendo ds", "NDS"),
    ("nintendo dsi", "NDS"),
    # Nintendo home
    ("gamecube", "GC"),
    ("nintendo - gamecube", "GC"),
    ("nintendo - wii", "WII"),
    # Sony
    ("playstation2", "PS2"),
    ("playstation 2", "PS2"),
    ("playstation 3", "PS3"),
    ("playstation portable (psn)", "PSP"),  # before plain PSP
    ("playstation portable", "PSP"),
    ("psp", "PSP"),
    ("playstation vita", "VITA"),
    ("playstation", "PS1"),  # catch-all — after PS2 / PS3 / PSP / Vita
    # Sega
    ("mega-cd", "SEGACD"),
    ("mega cd", "SEGACD"),
    ("sega cd", "SEGACD"),
    ("mega drive", "MD"),
    ("genesis", "MD"),
    ("sega master system", "SMS"),
    ("master system", "SMS"),
    ("mark iii", "SMS"),
    ("game gear", "GG"),
    ("saturn", "SAT"),
    ("dreamcast", "DC"),
    # SNK
    ("neo geo pocket color", "NGP"),  # before plain neo geo pocket
    ("neo geo pocket", "NGP"),
    ("neogeo pocket", "NGP"),
    ("neo geo cd", "NEOCD"),
    ("neogeo cd", "NEOCD"),
    # NEC
    ("pc engine", "PCE"),
    ("turbografx", "PCE"),
    # Bandai
    ("wonderswan color", "WSWAN"),  # before plain wonderswan
    ("wonderswan", "WSWAN"),
    # Atari
    ("atari - 2600", "A2600"),
    ("atari 2600", "A2600"),
    ("atari - 7800", "A7800"),
    ("atari 7800", "A7800"),
    ("atari lynx", "LYNX"),
    ("lynx", "LYNX"),
    # Arcade
    ("mame", "MAME"),
    ("arcade", "ARCADE"),
]


# ---------------------------------------------------------------------------
# Region priority for ranking candidates (lower = better / higher priority)
# ---------------------------------------------------------------------------


def _region_score(canonical: str) -> tuple[int, int]:
    """Score a canonical name by region preference.

    Returns a tuple (base_score, extra_paren_penalty) so that within the same
    region, cleaner titles (fewer extra parentheticals) sort before augmented
    releases like Virtual Console, Collector's Edition, etc.
    Lower tuple = more preferred when sorting.
    """
    n = canonical.lower()

    # Detect unwanted release types — these lose to any proper regional release
    is_junk = any(
        x in n for x in ["(demo)", "(kiosk", "(proto", "(beta", "(sample)", "(preview)"]
    )

    if is_junk:
        base = 99
    elif "(usa)" in n:
        base = 0
    elif "(world)" in n:
        base = 1
    elif "(europe)" in n:
        base = 2
    elif "(japan)" in n:
        base = 3
    else:
        base = 10  # other / unknown region

    # Penalize extra parenthetical groups beyond the first (the region tag).
    # "(USA)" → extra=0, "(USA) (Virtual Console)" → extra=1, etc.
    paren_count = canonical.count("(")
    extra = max(0, paren_count - 1)

    return (base, extra)


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
                logger.warning(
                    "[dat_normalizer] Skipped (unrecognized system): %s", dat_path.name
                )
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
            logger.info(
                "[dat_normalizer] %s: +%d CRC32 +%d names  [%s]",
                system,
                len(crc_map),
                sum(len(v) for v in slug_cands.values()),
                dat_path.name,
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
    """Parse a No-Intro/Redump DAT — XML or libretro clrmamepro text format.

    Auto-detects format by inspecting the first non-empty line:
      - Starts with "<"       → No-Intro/Redump XML
      - Starts with "clrmame" → libretro clrmamepro text

    Returns:
      crc_map         — {CRC32_UPPER_8 → canonical_name}
      slug_candidates — {slug → [canonical_name, ...]}  (all variants per slug)
    """
    try:
        with open(dat_path, "r", encoding="utf-8", errors="replace") as fh:
            for first_line in fh:
                stripped = first_line.strip()
                if stripped:
                    break
            else:
                stripped = ""

        if stripped.startswith("<"):
            return _parse_xml_dat(dat_path)
        else:
            return _parse_clrmamepro_dat(dat_path)
    except Exception as exc:
        logger.error(
            "[dat_normalizer] Failed to detect format of %s: %s", dat_path.name, exc
        )
        return {}, {}


def _parse_xml_dat(dat_path: Path) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Parse a standard No-Intro/Redump XML DAT.

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
        logger.error("[dat_normalizer] Failed to parse XML %s: %s", dat_path.name, exc)
    return crc_map, slug_candidates


_ROM_LINE_RE = re.compile(r"\brom\s*\(.*?\bcrc\s+([0-9A-Fa-f]{1,8})\b", re.IGNORECASE)


def _parse_clrmamepro_dat(
    dat_path: Path,
) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Parse a libretro clrmamepro text-format DAT.

    Format::

        game (
            name "Canonical Title (Region)"
            region "USA"
            rom ( name "file.ext" size 12345 crc AABBCCDD md5 ... )
        )

    A game block may contain multiple rom lines (multi-disc / multi-track);
    all their CRC32s are indexed to the same canonical name.

    Returns:
      crc_map         — {CRC32_UPPER_8 → canonical_name}
      slug_candidates — {slug → [canonical_name, ...]}  (all variants per slug)
    """
    crc_map: dict[str, str] = {}
    slug_candidates: dict[str, list[str]] = {}

    _NAME_RE = re.compile(r'^\s*name\s+"(.+?)"')

    try:
        current_name: str | None = None

        with open(dat_path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = _NAME_RE.match(line)
                if m:
                    current_name = m.group(1).strip()
                    continue

                # rom ( ... crc XXXXXXXX ... )
                rm = _ROM_LINE_RE.search(line)
                if rm and current_name:
                    crc = rm.group(1).upper().zfill(8)
                    if crc and crc != "00000000":
                        crc_map[crc] = current_name

                # end of block
                if line.strip() == ")" and current_name:
                    slug = normalize_rom_name(current_name)
                    cands = slug_candidates.setdefault(slug, [])
                    if current_name not in cands:
                        cands.append(current_name)
                    current_name = None

    except Exception as exc:
        logger.error(
            "[dat_normalizer] Failed to parse clrmamepro %s: %s", dat_path.name, exc
        )
    return crc_map, slug_candidates
