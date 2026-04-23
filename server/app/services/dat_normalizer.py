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

import json
import re
import xml.etree.ElementTree as ET
import logging
from pathlib import Path
from typing import Optional

from app.services.rom_id import normalize_rom_name

logger = logging.getLogger(__name__)
_ALIAS_BRACKET_RE = re.compile(r"\s*\[[^\]]*\]")


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
    ("neo geo pocket color", "NGPC"),  # before plain neo geo pocket
    ("neo geo pocket", "NGP"),
    ("neogeo pocket", "NGP"),
    ("neo geo cd", "NEOCD"),
    ("neogeo cd", "NEOCD"),
    # NEC
    ("pc engine supergrafx", "PCSG"),
    ("supergrafx", "PCSG"),
    ("pc engine", "PCE"),
    ("turbografx", "PCE"),
    # Bandai
    ("wonderswan color", "WSWANC"),  # before plain wonderswan
    ("wonderswan", "WSWAN"),
    # Atari
    ("atari - 2600", "A2600"),
    ("atari 2600", "A2600"),
    ("atari - 5200", "A5200"),
    ("atari 5200", "A5200"),
    ("atari - 7800", "A7800"),
    ("atari 7800", "A7800"),
    ("atari - 800", "A800"),
    ("atari 800", "A800"),
    ("atari - xe", "ATARIXED"),
    ("atari xe", "ATARIXED"),
    ("atari lynx", "LYNX"),
    ("lynx", "LYNX"),
    ("atari - jaguar cd", "JAGCD"),  # before plain jaguar
    ("jaguar cd", "JAGCD"),
    ("atari - jaguar", "JAGUAR"),
    ("jaguar", "JAGUAR"),
    ("atari - st", "ATARIST"),
    ("atari st", "ATARIST"),
    ("atarist", "ATARIST"),
    # Nintendo misc
    ("family computer disk system", "FDS"),
    ("famicom disk system", "FDS"),
    ("satellaview", "BS"),
    ("virtual boy", "VB"),
    ("pokemon mini", "POKEMINI"),
    # Sega misc
    ("32x", "32X"),
    ("naomi 2", "NAOMI2"),  # before plain naomi
    ("naomi", "NAOMI"),
    # NEC misc
    ("pc-98", "PC98"),
    ("pc98", "PC98"),
    ("pc-fx", "PCFX"),
    ("pcfx", "PCFX"),
    # Sharp
    ("sharp - x1", "X1"),
    ("x68000", "X68K"),
    # 3DO
    ("3do", "3DO"),
    # Arcade
    ("fbneo", "FBNEO"),
    ("final burn neo", "FBNEO"),
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


def _normalize_alias_lookup_name(name: str) -> str:
    """Normalize translated ROM names while ignoring patch-style [] tags."""
    return normalize_rom_name(_ALIAS_BRACKET_RE.sub("", name).strip())


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
        # system → {alias_slug → canonical_name} (translated titles -> DAT titles)
        self._alias_slug_index: dict[str, dict[str, str]] = {}
        # system → {alias_slug → [canonical_name, ...]}
        self._alias_slug_candidates: dict[str, dict[str, list[str]]] = {}
        # system → {canonical_name, ...}
        self._canonical_names: dict[str, set[str]] = {}
        # system → {lowercase_rom_stem → canonical_name}  (arcade set-name lookup)
        self._romfile_index: dict[str, dict[str, str]] = {}
        # system → {canonical_name → serial}  (DAT-declared on-disc/cart serial)
        #   e.g. NDS   → {"Mario Kart DS (USA)": "AMCE", ...}
        #        PS1   → {"Final Fantasy VII (USA) (Disc 1)": "SCUS-94163", ...}
        #        SAT   → {"Panzer Dragoon (USA)": "T-4403H", ...}
        # Used by the sync_id resolver to produce canonical identifiers that
        # match what real hardware and emulators use.
        self._serial_index: dict[str, dict[str, str]] = {}
        # system → {normalized_serial → canonical_name} (reverse lookup)
        self._serial_to_name: dict[str, dict[str, str]] = {}
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
            crc_map, slug_cands, romfile_map, serial_map = _parse_dat(dat_path)
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
                self._canonical_names.setdefault(system, set()).update(names)
            # Merge ROM filename index
            if romfile_map:
                self._romfile_index.setdefault(system, {}).update(romfile_map)
            # Merge serial index (forward and reverse)
            if serial_map:
                self._serial_index.setdefault(system, {}).update(serial_map)
                rev = self._serial_to_name.setdefault(system, {})
                for name, serial in serial_map.items():
                    norm = _normalize_serial(serial)
                    if not norm:
                        continue
                    # Prefer the region-best canonical name when multiple
                    # clones share a serial.
                    current = rev.get(norm)
                    if current is None or _region_score(name) < _region_score(current):
                        rev[norm] = name
            logger.info(
                "[dat_normalizer] %s: +%d CRC32 +%d names +%d romfiles +%d serials  [%s]",
                system,
                len(crc_map),
                sum(len(v) for v in slug_cands.values()),
                len(romfile_map),
                len(serial_map),
                dat_path.name,
            )
        self._load_aliases()

    def _load_aliases(self) -> None:
        aliases_path = self.dats_dir / "EN-Dats" / "aliases.json"
        if not aliases_path.is_file():
            return
        try:
            payload = json.loads(aliases_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.error(
                "[dat_normalizer] Failed to parse alias file %s: %s",
                aliases_path,
                exc,
            )
            return

        for system, mappings in payload.items():
            if not isinstance(mappings, dict):
                continue
            sys_key = str(system).upper().strip()
            known_canonicals = self._canonical_names.get(sys_key, set())
            if not known_canonicals:
                continue
            sys_cands = self._alias_slug_candidates.setdefault(sys_key, {})
            loaded = 0
            skipped = 0
            for alias_name, canonical_name in mappings.items():
                alias = str(alias_name or "").strip()
                canonical = str(canonical_name or "").strip()
                if not alias or not canonical:
                    continue
                # Only trust aliases whose targets exist in the canonical DAT.
                if canonical not in known_canonicals:
                    skipped += 1
                    continue
                alias_slug = _normalize_alias_lookup_name(alias)
                if not alias_slug:
                    continue
                existing = sys_cands.setdefault(alias_slug, [])
                if canonical not in existing:
                    existing.append(canonical)
                    loaded += 1

            sys_idx = self._alias_slug_index.setdefault(sys_key, {})
            for alias_slug, names in sys_cands.items():
                sys_idx[alias_slug] = min(names, key=_region_score)
            logger.info(
                "[dat_normalizer] %s: +%d aliases (%d skipped stale targets)  [%s]",
                sys_key,
                loaded,
                skipped,
                aliases_path.name,
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
          source         — "dat_crc32" | "dat_filename" | "dat_alias" | "filename"
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

        # 2. ROM filename lookup (arcade set names like samsho5 → full name)
        romfile_map = self._romfile_index.get(sys_key, {})
        if stem.lower() in romfile_map:
            canonical = romfile_map[stem.lower()]
            return {
                "canonical_name": canonical,
                "slug": normalize_rom_name(canonical),
                "source": "dat_filename",
            }

        # 3. Slug exact lookup
        query_slug = normalize_rom_name(stem)
        if query_slug in self._slug_index.get(sys_key, {}):
            canonical = self._slug_index[sys_key][query_slug]
            return {
                "canonical_name": canonical,
                "slug": normalize_rom_name(canonical),
                "source": "dat_filename",
            }

        # 4. Alias lookup for translated/patched filenames
        alias_query_slug = _normalize_alias_lookup_name(stem)
        if alias_query_slug in self._alias_slug_index.get(sys_key, {}):
            canonical = self._alias_slug_index[sys_key][alias_query_slug]
            return {
                "canonical_name": canonical,
                "slug": normalize_rom_name(canonical),
                "source": "dat_alias",
            }

        # 5. Fallback — normalize filename as-is
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
        if raw:
            return sorted(set(raw), key=_region_score)
        alias_query_slug = _normalize_alias_lookup_name(Path(filename).stem)
        raw = self._alias_slug_candidates.get(sys_key, {}).get(alias_query_slug, [])
        return sorted(set(raw), key=_region_score)

    def available_systems(self) -> list[str]:
        return sorted(set(self._crc_index) | set(self._slug_index))

    def stats(self) -> dict[str, dict[str, int]]:
        return {
            sys: {
                "crc32_entries": len(self._crc_index.get(sys, {})),
                "name_entries": len(self._slug_index.get(sys, {})),
                "serial_entries": len(self._serial_index.get(sys, {})),
            }
            for sys in self.available_systems()
        }

    # ------------------------------------------------------------------
    # Serial lookup — used by the sync_id resolver
    # ------------------------------------------------------------------

    def serial_for_canonical(self, system: str, canonical_name: str) -> Optional[str]:
        """Return the DAT serial for a canonical game name, if known."""
        return self._serial_index.get(system.upper(), {}).get(canonical_name)

    def canonical_for_serial(self, system: str, serial: str) -> Optional[str]:
        """Reverse lookup: region-best canonical name for a DAT serial."""
        norm = _normalize_serial(serial)
        if not norm:
            return None
        return self._serial_to_name.get(system.upper(), {}).get(norm)

    def lookup_serial(
        self,
        system: str,
        filename: str,
        crc32: Optional[str] = None,
    ) -> Optional[str]:
        """Find the DAT serial for a ROM by filename and/or CRC32.

        Tries CRC32 first (most accurate) then falls back to filename-based
        slug lookup.  Returns None when no entry matches.
        """
        sys_key = system.upper()
        serials = self._serial_index.get(sys_key)
        if not serials:
            return None

        # 1. CRC32 lookup → canonical → serial
        if crc32:
            crc_padded = crc32.upper().zfill(8)
            canonical = self._crc_index.get(sys_key, {}).get(crc_padded)
            if canonical and canonical in serials:
                return serials[canonical]

        # 2. Slug lookup → canonical → serial
        stem = Path(filename).stem
        slug = normalize_rom_name(stem)
        canonical = self._slug_index.get(sys_key, {}).get(slug)
        if canonical and canonical in serials:
            return serials[canonical]

        # 3. ROM filename lookup (arcade-style)
        canonical = self._romfile_index.get(sys_key, {}).get(stem.lower())
        if canonical and canonical in serials:
            return serials[canonical]

        return None


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


def _normalize_serial(serial: str) -> str:
    """Canonicalise a DAT serial for dictionary lookup.

    DATs sometimes list serials with inconsistent casing or punctuation (e.g.
    ``SCUS-94163`` vs ``SCUS94163``).  We uppercase and strip non-alphanumeric
    separators so either form matches the same key.
    """
    if not serial:
        return ""
    return re.sub(r"[^A-Z0-9]", "", serial.upper())


def _parse_dat(
    dat_path: Path,
) -> tuple[dict[str, str], dict[str, list[str]], dict[str, str], dict[str, str]]:
    """Parse a No-Intro/Redump DAT — XML, MAME, or libretro clrmamepro text.

    Auto-detects format by inspecting the file header:
      - Starts with "<"                → No-Intro/Redump XML
      - Contains "machine (" blocks    → MAME clrmamepro variant
      - Otherwise                      → libretro clrmamepro text

    Returns:
      crc_map         — {CRC32_UPPER_8 → canonical_name}
      slug_candidates — {slug → [canonical_name, ...]}  (all variants per slug)
      romfile_map     — {lowercase_rom_stem → canonical_name}  (arcade set names)
      serial_map      — {canonical_name → serial_string}
    """
    try:
        first_line = ""
        has_machine_block = False
        with open(dat_path, "r", encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                stripped = line.strip()
                if not first_line and stripped:
                    first_line = stripped
                    if first_line.startswith("<"):
                        break
                if stripped.startswith("machine (") or stripped == "machine (":
                    has_machine_block = True
                    break
                if stripped.startswith("game (") or stripped == "game (":
                    break
                if i > 500:
                    break

        if first_line.startswith("<"):
            return _parse_xml_dat(dat_path)
        if has_machine_block:
            return _parse_mame_dat(dat_path)
        return _parse_clrmamepro_dat(dat_path)
    except Exception as exc:
        logger.error(
            "[dat_normalizer] Failed to detect format of %s: %s", dat_path.name, exc
        )
        return {}, {}, {}, {}


def _parse_xml_dat(
    dat_path: Path,
) -> tuple[dict[str, str], dict[str, list[str]], dict[str, str], dict[str, str]]:
    """Parse a standard No-Intro/Redump XML DAT.

    Returns:
      crc_map         — {CRC32_UPPER_8 → canonical_name}
      slug_candidates — {slug → [canonical_name, ...]}  (all variants per slug)
      romfile_map     — {lowercase_rom_stem → canonical_name}
      serial_map      — {canonical_name → serial_string}
    """
    crc_map: dict[str, str] = {}
    slug_candidates: dict[str, list[str]] = {}
    romfile_map: dict[str, str] = {}
    serial_map: dict[str, str] = {}
    try:
        tree = ET.parse(dat_path)
        root = tree.getroot()
        for game in root.findall("game"):
            canonical = game.get("name", "").strip()
            if not canonical:
                continue
            slug = normalize_rom_name(canonical)
            slug_candidates.setdefault(slug, []).append(canonical)
            # No-Intro XML puts the serial either on the game element or on
            # the rom element; check both.
            game_serial = game.get("serial", "").strip()
            if game_serial:
                serial_map.setdefault(canonical, game_serial)
            for rom in game.findall("rom"):
                crc = rom.get("crc", "").upper().zfill(8)
                if crc and crc != "00000000":
                    crc_map[crc] = canonical
                rom_name = rom.get("name", "")
                if rom_name:
                    romfile_map[Path(rom_name).stem.lower()] = canonical
                rom_serial = rom.get("serial", "").strip()
                if rom_serial:
                    serial_map.setdefault(canonical, rom_serial)
    except Exception as exc:
        logger.error("[dat_normalizer] Failed to parse XML %s: %s", dat_path.name, exc)
    return crc_map, slug_candidates, romfile_map, serial_map


_ROM_LINE_RE = re.compile(r"\brom\s*\(.*?\bcrc\s+([0-9A-Fa-f]{1,8})\b", re.IGNORECASE)
_ROM_NAME_IN_LINE_RE = re.compile(r"\brom\s*\(.*?\bname\s+(\S+)", re.IGNORECASE)
# game-level: `serial "BKAJ"` — a top-level block attribute
_GAME_SERIAL_RE = re.compile(r'^\s*serial\s+"(.+?)"')
# rom-level: `... serial "BKAJ" )` — same value on the rom line
_ROM_SERIAL_IN_LINE_RE = re.compile(
    r"\brom\s*\(.*?\bserial\s+\"([^\"]+)\"", re.IGNORECASE
)


def _parse_clrmamepro_dat(
    dat_path: Path,
) -> tuple[dict[str, str], dict[str, list[str]], dict[str, str], dict[str, str]]:
    """Parse a libretro clrmamepro text-format DAT.

    Format::

        game (
            name "Canonical Title (Region)"
            region "USA"
            serial "SLUS-01234"
            rom ( name "file.ext" size 12345 crc AABBCCDD md5 ... serial "SLUS-01234" )
        )

    A game block may contain multiple rom lines (multi-disc / multi-track);
    all their CRC32s are indexed to the same canonical name.  When the game
    carries a ``serial`` attribute (NDS, PS1, PS2, PSP, Vita, Saturn DATs all
    do) we capture it for sync_id resolution.

    Returns:
      crc_map         — {CRC32_UPPER_8 → canonical_name}
      slug_candidates — {slug → [canonical_name, ...]}  (all variants per slug)
      romfile_map     — {lowercase_rom_stem → canonical_name}
      serial_map      — {canonical_name → serial_string}
    """
    crc_map: dict[str, str] = {}
    slug_candidates: dict[str, list[str]] = {}
    romfile_map: dict[str, str] = {}
    serial_map: dict[str, str] = {}

    _NAME_RE = re.compile(r'^\s*name\s+"(.+?)"')

    try:
        current_name: str | None = None
        current_serial: str | None = None

        with open(dat_path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = _NAME_RE.match(line)
                if m:
                    current_name = m.group(1).strip()
                    current_serial = None
                    continue

                # game-level `serial "..."` line (before any rom line)
                ms = _GAME_SERIAL_RE.match(line)
                if ms and current_name and current_serial is None:
                    current_serial = ms.group(1).strip()
                    if current_serial:
                        serial_map.setdefault(current_name, current_serial)

                # rom ( ... name filename.zip ... crc XXXXXXXX ... serial "..." )
                if "rom (" in line and current_name:
                    nm = _ROM_NAME_IN_LINE_RE.search(line)
                    if nm:
                        rom_stem = Path(nm.group(1).strip('"')).stem.lower()
                        if rom_stem:
                            romfile_map[rom_stem] = current_name

                    rm = _ROM_LINE_RE.search(line)
                    if rm:
                        crc = rm.group(1).upper().zfill(8)
                        if crc and crc != "00000000":
                            crc_map[crc] = current_name

                    rs = _ROM_SERIAL_IN_LINE_RE.search(line)
                    if rs and current_serial is None:
                        current_serial = rs.group(1).strip()
                        if current_serial:
                            serial_map.setdefault(current_name, current_serial)

                # end of block
                if line.strip() == ")" and current_name:
                    slug = normalize_rom_name(current_name)
                    cands = slug_candidates.setdefault(slug, [])
                    if current_name not in cands:
                        cands.append(current_name)
                    current_name = None
                    current_serial = None

    except Exception as exc:
        logger.error(
            "[dat_normalizer] Failed to parse clrmamepro %s: %s", dat_path.name, exc
        )
    return crc_map, slug_candidates, romfile_map, serial_map


# ---------------------------------------------------------------------------
# MAME clrmamepro text DAT parser
# ---------------------------------------------------------------------------

# Top-level blocks we want to ingest (machine = playable set; resource = BIOS /
# shared device set).  Both follow the same layout so one parser handles both.
_MAME_BLOCK_START_RE = re.compile(r"^(machine|resource)\s*\(")
# Inside a block the set name is on a line by itself and is unquoted, e.g.
# ``\tname 1on1gov``.  The trailing ``\s*$`` anchor ensures we don't confuse it
# with the inner ``rom ( name ... size ... )`` lines (those have extra content
# after the first token, so the anchor fails).
_MAME_SET_NAME_RE = re.compile(r'^\s*name\s+(\S+)\s*$')
# Display title is quoted, e.g. ``\tdescription "1 on 1 Government (Japan)"``.
_MAME_DESC_RE = re.compile(r'^\s*description\s+"(.+?)"')


def _parse_mame_dat(
    dat_path: Path,
) -> tuple[dict[str, str], dict[str, list[str]], dict[str, str], dict[str, str]]:
    """Parse a MAME clrmamepro text DAT (``machine`` / ``resource`` blocks).

    Format::

        machine (
            name 1on1gov                              # unquoted SET NAME
            description "1 on 1 Government (Japan)"   # quoted DISPLAY NAME
            year 2000
            manufacturer "Tecmo"
            rom ( name 1on1.u119 size 1048576 crc 10aecc19 ... )
            rom ( name 1on1.u120 size 1048576 crc eea158bd ... )
            ...
        )

    MAME differs from No-Intro-style clrmamepro in two important ways:

    1. The top-level ``name`` inside each block is the **set name** (unquoted),
       which is the ZIP filename stem on disk (e.g. ``1on1gov.zip``).  The
       human-readable title lives in ``description "..."``.
    2. The ``rom ( name X crc Y )`` lines reference individual chip files
       *inside* the ZIP; their CRC32s don't match the ZIP container's CRC, so
       scanning the ROM folder by file CRC would never match.  We therefore
       skip CRC indexing entirely for MAME and rely on set-name → description
       mapping via ``romfile_map`` (lookup #2 in :meth:`DatNormalizer.normalize`).

    Returns:
      crc_map         — always empty for MAME
      slug_candidates — {slug → [description, ...]}
      romfile_map     — {set_name.lower() → description}  (ZIP stem → title)
      serial_map      — always empty for MAME
    """
    crc_map: dict[str, str] = {}
    slug_candidates: dict[str, list[str]] = {}
    romfile_map: dict[str, str] = {}
    serial_map: dict[str, str] = {}

    try:
        current_set: Optional[str] = None
        current_desc: Optional[str] = None
        in_block = False

        with open(dat_path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                stripped = line.strip()

                if not in_block:
                    if _MAME_BLOCK_START_RE.match(stripped):
                        in_block = True
                        current_set = None
                        current_desc = None
                    continue

                # End of machine/resource block (bare ``)`` on its own line).
                # MAME.dat keeps every inner element on a single line so we
                # never see nested multi-line sub-blocks; a bare ``)`` always
                # closes the outer block.
                if stripped == ")":
                    if current_set and current_desc:
                        slug = normalize_rom_name(current_desc)
                        cands = slug_candidates.setdefault(slug, [])
                        if current_desc not in cands:
                            cands.append(current_desc)
                        # Map the on-disk ZIP stem (set-name) to the display
                        # title.  Earlier winners stay — MAME set names are
                        # unique so collisions shouldn't happen, but guard
                        # just in case.
                        romfile_map.setdefault(current_set.lower(), current_desc)
                    in_block = False
                    current_set = None
                    current_desc = None
                    continue

                if current_set is None:
                    m = _MAME_SET_NAME_RE.match(line)
                    if m:
                        current_set = m.group(1).strip()
                        continue

                if current_desc is None:
                    m = _MAME_DESC_RE.match(line)
                    if m:
                        current_desc = m.group(1).strip()
                        continue

    except Exception as exc:
        logger.error(
            "[dat_normalizer] Failed to parse MAME DAT %s: %s", dat_path.name, exc
        )

    return crc_map, slug_candidates, romfile_map, serial_map
