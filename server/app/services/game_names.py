"""Game name lookup service using libretro DAT files and legacy .txt databases."""

import re
from pathlib import Path

from app.services.rom_id import (
    SYSTEM_CODES,
    normalize_rom_name,
    parse_title_id as _parse_emulator_id,
)

# Global cache for game names (loaded once at startup)
_3ds_title_ids: dict[
    str, str
] = {}  # full 16-char hex TitleID -> name (from 3DS DAT title_id lines)
_3ds_names: dict[str, str] = {}  # 4-char game code -> name
_3ds_serial_to_title_id: dict[str, str] = {}  # full product code -> 16-char title ID
_3ds_by_slug: dict[str, str] = {}  # normalized DAT name slug -> preferred 16-char title ID
_3ds_title_ids_by_slug: dict[str, list[str]] = {}  # normalized DAT name slug -> all title IDs
_ds_names: dict[str, str] = {}  # 4-char game code -> name
_psp_names: dict[str, str] = {}  # keyed by full product code e.g. "ULUS10272"
_psx_names: dict[str, str] = {}  # keyed by full product code e.g. "SCUS94163"
_ps2_names: dict[str, str] = {}  # keyed by full product code e.g. "SCUS97203"
_sat_names: dict[str, str] = {}  # keyed by Saturn serial e.g. "T-12705H"
_vita_names: dict[str, str] = {}  # keyed by full product code e.g. "PCSE00082"
_wii_names: dict[
    str, str
] = {}  # 4-char GC/Wii game code -> name e.g. "GALE" -> "Super Smash Bros. Melee"
_ps3_names: dict[str, str] = {}  # keyed by 9-char product code e.g. "BLJM61131"

# Per-dict priority trackers: key → (source_tier, region_rank)
# source_tier: 0 = retail disc, 1 = PSN/digital
# Persists across multiple load_libretro_dat_to_dicts() calls so that retail
# entries loaded first are never overwritten by PSN entries loaded later.
_psp_priority: dict[str, tuple[int, int]] = {}
_psx_priority: dict[str, tuple[int, int]] = {}
_ps2_priority: dict[str, tuple[int, int]] = {}
_sat_priority: dict[str, tuple[int, int]] = {}
_vita_priority: dict[str, tuple[int, int]] = {}
_3ds_priority: dict[str, tuple[int, int]] = {}
_3ds_title_priority: dict[str, tuple[int, int]] = {}
_3ds_title_id_priority: dict[str, tuple[int, int]] = {}
_ds_priority: dict[str, tuple[int, int]] = {}
_wii_priority: dict[str, tuple[int, int]] = {}
_ps3_priority: dict[str, tuple[int, int]] = {}

# Reverse index: normalized game name slug → PS1 retail serial (preferred over PSN codes)
# Rebuilt by build_psx_psn_to_retail() after all databases are loaded.
_psx_by_slug: dict[str, str] = {}
_psx_serials_by_slug: dict[str, list[str]] = {}
_sat_by_slug: dict[str, str] = {}
_sat_serials_by_slug: dict[str, list[str]] = {}
_sat_safe_to_serial: dict[str, str] = {}

# PSN PSone Classic code → original retail disc serial
# e.g. "NPUJ00662" (Parasite Eve Japan PSN) → "SLPM86034" (Parasite Eve Japan retail)
_psx_psn_to_retail: dict[str, str] = {}

_PSN_RE = re.compile(r"^NP")
_RETAIL_RE = re.compile(r"^(SL|SC|PA)")

# Strips parenthesized (USA) / bracketed [Disc1of3] tags from psxdb names before slugifying
_BRACKET_RE = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")
_REGION_RE = re.compile(
    r"\((USA|Europe|Japan|World|France|Germany|Italy|Spain|Australia)\)", re.IGNORECASE
)

_ROMAN_TO_ARABIC: dict[str, str] = {
    "i": "1",
    "ii": "2",
    "iii": "3",
    "iv": "4",
    "v": "5",
    "vi": "6",
    "vii": "7",
    "viii": "8",
    "ix": "9",
    "x": "10",
    "xi": "11",
    "xii": "12",
    "xiii": "13",
    "xiv": "14",
    "xv": "15",
}
_ARABIC_TO_ROMAN: dict[str, str] = {v: k for k, v in _ROMAN_TO_ARABIC.items()}
_AMBIGUOUS_PSX_PREFIXES = {"SLPM", "SLPS", "SCPS"}


def _psx_name_slug(name: str) -> str:
    """Normalize a psxdb game name to a plain slug for reverse lookup.

    "Final Fantasy VII [Disc1of3]" → "final_fantasy_vii"
    "007 - The World Is Not Enough" → "007_the_world_is_not_enough"
    """
    clean = _BRACKET_RE.sub("", name).strip()
    return re.sub(r"[^a-z0-9]+", "_", clean.lower()).strip("_")


def _psx_region_hint(name: str) -> str | None:
    match = _REGION_RE.search(name)
    return match.group(1).upper() if match else None


def _slug_roman_variants(slug: str) -> list[str]:
    """Return roman<->arabic variants for a normalized slug.

    We only use these as a fallback when the exact slug misses, which keeps
    names like "Mega Man X" from colliding with "Mega Man 10" unless one side
    is actually absent from the index.
    """
    parts = slug.split("_")
    variants: list[str] = []
    for idx, part in enumerate(parts):
        replacement = _ROMAN_TO_ARABIC.get(part) or _ARABIC_TO_ROMAN.get(part)
        if not replacement:
            continue
        variants.append("_".join(parts[:idx] + [replacement] + parts[idx + 1 :]))
    return variants


def _psx_serial_region_rank(code: str, region_hint: str | None) -> tuple[int, int, str]:
    """Return a sortable rank for a PS1 serial, honoring the requested region."""
    code = code.upper()
    prefix = code[:4]
    region_groups = {
        "USA": {"SCUS", "SLUS", "PAPX"},
        "EUROPE": {"SCES", "SCED", "SLES"},
        "JAPAN": {"SCPS", "SLPS", "SLPM", "PAPX"},
        "WORLD": set(),
        "FRANCE": {"SLES", "SCES", "SCED"},
        "GERMANY": {"SLES", "SCES", "SCED"},
        "ITALY": {"SLES", "SCES", "SCED"},
        "SPAIN": {"SLES", "SCES", "SCED"},
        "AUSTRALIA": {"SLES", "SCES", "SCED"},
    }
    fallback_order = [
        {"SCUS", "SLUS", "PAPX"},  # USA
        {"SCES", "SCED", "SLES"},  # Europe
        {"SCPS", "SLPS", "SLPM"},  # Japan
    ]

    if region_hint:
        preferred = region_groups.get(region_hint.upper(), set())
        if prefix in preferred:
            return (0, 0 if prefix.startswith("SC") else 1, code)

    for idx, group in enumerate(fallback_order, start=1):
        if prefix in group:
            return (idx, 0 if prefix.startswith("SC") else 1, code)
    return (len(fallback_order) + 1, 1, code)


def _sat_serial_region_rank(code: str, region_hint: str | None) -> tuple[int, str]:
    code = code.upper()
    if region_hint:
        hint = region_hint.upper()
        if hint == "USA" and not code.endswith("-50") and not code.endswith("G"):
            return (0, code)
        if hint == "EUROPE" and code.endswith("-50"):
            return (0, code)
        if hint == "JAPAN" and code.endswith("G"):
            return (0, code)

    if code.endswith("-50"):
        return (2, code)
    if code.endswith("G"):
        return (3, code)
    return (1, code)


def _saturn_safe_id(serial: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "", serial.replace(" ", "_")).upper()


def make_saturn_title_id(serial: str) -> str:
    safe_id = _saturn_safe_id(serial)
    return f"SAT_{safe_id}" if safe_id else "SAT_UNKNOWN"


# Patterns for platform detection
_PSP_CODE_RE = re.compile(r"^[A-Z]{4}\d{5}$")  # ULUS10000, ELES01234, NPUH10001
_PSP_PREFIX_RE = re.compile(r"^[A-Z]{4}\d{5}")  # same but allows slot suffix
_VITA_CODE_RE = re.compile(r"^PCS[A-Z]\d{5}$")  # PCSE00000, PCSB12345, PCSG00001
_PS3_CODE_RE = re.compile(r"^BL[A-Z]{2}\d{5}$")  # BLUS30289, BLES01017, BLJM61131
_PS3_PREFIX_RE = re.compile(r"^[A-Z]{4}\d{5}")  # BLUS30289-SAVE00, NPUB12345, etc.

# 3DS title ID high-word prefixes (first 5 hex chars of the 16-char ID)
_3DS_HIGH_PREFIXES = {
    "00040",
    "00041",
    "00042",
    "00043",
    "00044",
    "00045",
    "00046",
    "00047",
}
_NDS_HIGH_PREFIXES = {"00048"}


def _detect_playstation_platform_heuristic(title_id: str) -> str | None:
    """Best-effort PlayStation platform detection from a serial/save-dir ID.

    Mirrors the PS-family save-kind heuristic used by the native clients:

    - `U***#####`                       -> PSP
    - `PC**#####`                      -> VITA
    - `PB**#####`                      -> PS2
    - `B***#####`                      -> PS3
    - `NP?B#####`                      -> PS3
    - `NP?H#####`, `NP?G#####`         -> PSP
    - `NP?D#####`                      -> PS2
    - `SL**/SC**` ranges               -> PS1 or PS2 depending on serial range

    Returns None when the input does not look like a PlayStation serial.
    """
    tid = title_id.upper().strip()
    if len(tid) < 9 or not _PSP_PREFIX_RE.match(tid):
        return None

    base = tid[:9]

    if base[0] == "U":
        return "PSP"

    if base.startswith("PC"):
        return "VITA"

    if base.startswith("PB"):
        return "PS2"

    if base[0] == "B":
        return "PS3"

    if base.startswith("NP"):
        platform_char = base[3]
        if platform_char == "B":
            return "PS3"
        if platform_char in {"H", "G"}:
            return "PSP"
        if platform_char == "D":
            return "PS2"
        return "PS3"

    if base[0] == "S":
        try:
            serial_num = int(base[4:9])
        except ValueError:
            return "PS1"

        if base.startswith("SLUS") and serial_num >= 20000:
            return "PS2"
        if base.startswith("SCUS") and serial_num >= 97000:
            return "PS2"
        if base.startswith("SLES") and serial_num >= 50000:
            return "PS2"
        if base.startswith("SCES") and serial_num >= 50000:
            return "PS2"
        if base.startswith("SLPS") and serial_num >= 20000:
            return "PS2"
        if base.startswith("SCPS") and serial_num >= 20000:
            return "PS2"
        return "PS1"

    return "PS3"


def detect_platform(title_id: str) -> str:
    """Return the platform string for a title ID.

    Returns one of: "3DS", "NDS", "PSP", "PS1", "PS2", "PS3", "VITA", or an emulator
    system code like "GBA", "SNES", "MD", etc.

    Rules:
      - Emulator SYSTEM_slug format           → system code (e.g. "GBA")
      - 16-char hex, starts with 00040... → "3DS"
      - 16-char hex, starts with 00048... → "NDS"  (DSiWare shown on 3DS)
      - 16-char hex, anything else         → "3DS"  (conservative fallback)
      - Known PlayStation-family serials    → PS1 / PS2 / PS3 / PSP / VITA heuristic
      - Anything else                      → "NDS"  (DS raw endpoint, no product code)
    """
    # Emulator format: SYSTEM_slug (e.g. GBA_zelda_the_minish_cap)
    parsed = _parse_emulator_id(title_id)
    if parsed:
        return parsed[0]  # e.g. "GBA"

    tid = title_id.upper().strip()

    # 16-char hex = 3DS or NDS
    if len(tid) == 16 and all(c in "0123456789ABCDEF" for c in tid):
        if tid[:5] in _NDS_HIGH_PREFIXES:
            return "NDS"
        return "3DS"

    # Vita: PCS[A-Z]#####
    if _VITA_CODE_RE.match(tid) or (len(tid) >= 4 and tid[:3] == "PCS"):
        return "VITA"

    base = tid[:9]

    # Prefer the PS3 DB when the 9-char base is known there, since PS3 PSN IDs
    # overlap with PSP serial families.
    if base in _ps3_names:
        return "PS3"

    if tid in _sat_names:
        return "SAT"

    if tid.startswith("SAT_") and tid[4:] in _sat_safe_to_serial:
        return "SAT"

    heuristic = _detect_playstation_platform_heuristic(tid)
    if heuristic:
        return heuristic

    # Fallback: treat as NDS (DS raw endpoint sends no product code context)
    return "NDS"


_HEX_16_RE = re.compile(r"^[0-9A-F]{16}$")


def load_database(db_path: Path | None = None) -> int:
    """Load a game names database from file into the appropriate cache.

    Automatically detects the target dict based on filename.

    3DS direct TitleID lookups now come from the DAT ``title_id`` lines loaded by
    ``load_libretro_dat_to_dicts()``. This helper remains for the legacy text
    databases that are still in use for other systems:
      - 3dstdb.txt      → _3ds_names  (4-char game code -> name)
      - dstdb.txt       → _ds_names
      - pspdb.txt       → _psp_names
      - vitadb.txt      → _vita_names
      - psxdb.txt       → _psx_names
      - wiidb.txt       → _wii_names  (keyed by first 4 chars of 6-char GC/Wii code)

    Returns the number of entries loaded.
    """
    global \
        _3ds_names, \
        _ds_names, \
        _psp_names, \
        _sat_names, \
        _vita_names, \
        _psx_by_slug, \
        _psx_serials_by_slug, \
        _sat_by_slug, \
        _sat_serials_by_slug, \
        _wii_names

    if db_path is None:
        db_path = Path(__file__).parent.parent.parent / "data" / "3dstdb.txt"

    if not db_path.exists():
        return 0

    fname = db_path.name.lower()
    is_wii = "wii" in fname

    target_dict: dict[str, str] = _3ds_names  # default; overwritten below if not wii
    if not is_wii:
        if "vita" in fname:
            target_dict = _vita_names
        elif "psx" in fname:
            target_dict = _psx_names
        elif "saturn" in fname:
            target_dict = _sat_names
        elif "psp" in fname:
            target_dict = _psp_names
        elif "ds" in fname and "3ds" not in fname:
            target_dict = _ds_names
        else:
            target_dict = _3ds_names

    added = 0
    with open(db_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or "," not in line:
                continue
            parts = line.split(",", 1)
            if len(parts) == 2:
                code = parts[0].strip().upper()
                game_name = parts[1].strip()
                if not code or not game_name:
                    continue
                if is_wii:
                    # wiidb codes are 6 chars (e.g. "GALE01"); index by first 4 (game+region).
                    # Skip header and non-game entries.
                    if len(code) >= 4 and code[:4].isalnum():
                        key = code[:4]
                        if key not in _wii_names:
                            _wii_names[key] = game_name
                            added += 1
                else:
                    target_dict[code] = game_name
                    added += 1

    return added


def load_libretro_dat_to_dicts(dat_path: Path, psn: bool = False) -> int:
    """Parse a libretro clrmamepro DAT file and load serials into the appropriate dict.

    Serial normalization per system (detected from filename):
      - Sony - PlayStation*.dat           → strip hyphens → _psx_names  (SLPS01204)
      - Sony - PlayStation Portable*.dat  → strip hyphens → _psp_names  (ULJM06272)
      - Sony - PlayStation Vita*.dat      → strip hyphens → _vita_names (PCSE00844)
      - Nintendo - Nintendo 3DS*.dat      → extract 4-char code from CTR-P-XXXX → _3ds_names
      - Nintendo - Nintendo DS*.dat       → serial is already 4-char → _ds_names
      - Nintendo - Nintendo DSi*.dat      → serial is already 4-char → _ds_names
      - Nintendo - GameCube*.dat          → extract 4-char code from DL-DOL-XXXX-RGN → _wii_names
      - Nintendo - Wii*.dat               → extract 4-char code from RVL-XXXX-RGN → _wii_names

    psn=True marks this DAT as a lower-priority PSN source so that retail entries
    (loaded with psn=False) are never overwritten by their PSN equivalents.

    Returns the number of entries loaded (new + updated).
    """
    global \
        _psx_names, \
        _ps2_names, \
        _psp_names, \
        _sat_names, \
        _vita_names, \
        _3ds_title_ids, \
        _3ds_names, \
        _3ds_serial_to_title_id, \
        _ds_names, \
        _wii_names, \
        _ps3_names
    global \
        _psx_priority, \
        _ps2_priority, \
        _psp_priority, \
        _sat_priority, \
        _vita_priority, \
        _3ds_priority, \
        _3ds_title_priority, \
        _3ds_title_id_priority, \
        _ds_priority, \
        _wii_priority, \
        _ps3_priority

    if not dat_path.exists():
        return 0

    fname = dat_path.name.lower()

    # Determine target dict, its priority tracker, and serial extraction strategy
    if "playstation vita" in fname:
        target = _vita_names
        priority = _vita_priority
        mode = "strip_hyphens"
    elif "playstation portable" in fname:
        target = _psp_names
        priority = _psp_priority
        mode = "strip_hyphens"
    elif "playstation 3" in fname:
        target = _ps3_names
        priority = _ps3_priority
        mode = "strip_hyphens"
    elif "playstation 2" in fname:
        target = _ps2_names
        priority = _ps2_priority
        mode = "strip_hyphens"
    elif "playstation" in fname:
        target = _psx_names
        priority = _psx_priority
        mode = "strip_hyphens"
    elif "saturn" in fname:
        target = _sat_names
        priority = _sat_priority
        mode = "keep_serial"
    elif "nintendo 3ds" in fname:
        target = _3ds_names
        priority = _3ds_priority
        mode = "3ds_code"  # extract 4-char code from CTR-P-XXXX
    elif "nintendo ds" in fname or "nintendo dsi" in fname:
        target = _ds_names
        priority = _ds_priority
        mode = "ds_code"  # serial is already 4-char (e.g. BKAJ)
    elif "gamecube" in fname:
        target = _wii_names
        priority = _wii_priority
        mode = "gc_code"  # extract 4-char code from DL-DOL-XXXX-RGN (index 2)
    elif "nintendo - wii" in fname:
        target = _wii_names
        priority = _wii_priority
        mode = "wii_code"  # extract 4-char code from RVL-XXXX-RGN (index 1)
    else:
        return 0

    # Parse clrmamepro text format
    # Each game block looks like:
    #   game (
    #       name "Title (Region).ext"
    #       serial "XXXX-YYYYY"
    #       rom ( ... )
    #   )
    # A game block can have multiple serial lines; we use the first.
    # We collect (serial, name) pairs and use region-priority to avoid
    # overwriting a preferred region entry with a less-preferred one.

    # Region priority: lower index = preferred
    _REGION_PRIORITY = ["(USA)", "(Europe)", "(World)", "(Japan)"]

    def _region_rank(name_str: str) -> int:
        for i, tag in enumerate(_REGION_PRIORITY):
            if tag.lower() in name_str.lower():
                return i
        return len(_REGION_PRIORITY)

    # Source tier: retail (0) always beats PSN (1)
    _source_tier = 1 if psn else 0

    added = 0
    current_name: str | None = None
    current_serial: str | None = None
    current_title_id: str | None = None

    _NAME_RE = re.compile(r'^\s*name\s+"(.+?)"')
    _SERIAL_RE = re.compile(r'^\s*serial\s+"(.+?)"')
    _TITLE_ID_RE = re.compile(r'^\s*title_id\s+"([0-9A-Fa-f]{16})"')

    def _extract_key(serial: str) -> str | None:
        if mode == "strip_hyphens":
            key = serial.replace("-", "").upper()
            return (
                key
                if len(key) == 9 and key[:4].isalpha() and key[4:].isdigit()
                else None
            )
        if mode == "keep_serial":
            key = serial.upper().strip()
            return key or None
        if mode == "3ds_code":
            # CTR-P-XXXX or CTR-N-XXXX → XXXX (4-char)
            parts = serial.upper().split("-")
            if len(parts) == 3 and parts[0] == "CTR" and len(parts[2]) == 4:
                return parts[2]
            return None
        if mode == "ds_code":
            # Serial is already 4-char alphanumeric
            s = serial.upper()
            if len(s) == 4 and s.isalnum():
                return s
            # Also handle NTR-XXXX-RGN style
            parts = s.split("-")
            if len(parts) == 3 and len(parts[1]) == 4:
                return parts[1]
            return None
        if mode == "gc_code":
            # DL-DOL-GW7E-USA → GW7E (segment index 2, 4 chars)
            parts = serial.upper().split("-")
            if len(parts) >= 3 and len(parts[2]) == 4 and parts[2].isalnum():
                return parts[2]
            return None
        if mode == "wii_code":
            # RVL-SP3E-USA or RVL-SP3E-USA-B0 → SP3E (segment index 1, 4 chars)
            parts = serial.upper().split("-")
            if len(parts) >= 2 and len(parts[1]) == 4 and parts[1].isalnum():
                return parts[1]
            return None
        return None

    with open(dat_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            m = _NAME_RE.match(line)
            if m:
                current_name = m.group(1)
                current_serial = None
                current_title_id = None
                continue

            m = _SERIAL_RE.match(line)
            if m and current_serial is None:
                current_serial = m.group(1)
                continue

            m = _TITLE_ID_RE.match(line)
            if m and current_title_id is None:
                current_title_id = m.group(1).upper()
                continue

            # End of block — ")" alone on a line at top level
            if line.strip() == ")" and current_name and current_serial:
                key = _extract_key(current_serial)
                if key:
                    rank = (_source_tier, _region_rank(current_name))
                    existing_rank = priority.get(
                        key, (len(_REGION_PRIORITY) + 1, len(_REGION_PRIORITY) + 1)
                    )
                    if rank < existing_rank or key not in target:
                        target[key] = current_name
                        priority[key] = rank
                        added += 1
                    if mode == "3ds_code" and current_title_id:
                        serial_key = current_serial.upper().strip()
                        if serial_key:
                            _3ds_serial_to_title_id[serial_key] = current_title_id
                        existing_title_id_rank = _3ds_title_id_priority.get(
                            current_title_id,
                            (
                                len(_REGION_PRIORITY) + 1,
                                len(_REGION_PRIORITY) + 1,
                            ),
                        )
                        if (
                            rank < existing_title_id_rank
                            or current_title_id not in _3ds_title_ids
                        ):
                            _3ds_title_ids[current_title_id] = current_name
                            _3ds_title_id_priority[current_title_id] = rank

                        slug = normalize_rom_name(current_name)
                        if slug and slug != "unknown":
                            existing_title_rank = _3ds_title_priority.get(
                                slug,
                                (
                                    len(_REGION_PRIORITY) + 1,
                                    len(_REGION_PRIORITY) + 1,
                                ),
                            )
                            if rank < existing_title_rank or slug not in _3ds_by_slug:
                                _3ds_by_slug[slug] = current_title_id
                                _3ds_title_priority[slug] = rank
                            candidates = _3ds_title_ids_by_slug.setdefault(slug, [])
                            if current_title_id not in candidates:
                                candidates.append(current_title_id)
                current_name = None
                current_serial = None
                current_title_id = None

    return added


def lookup_names(product_codes: list[str]) -> dict[str, str]:
    """Look up game names for a list of product codes.

    Returns a dict mapping input codes to their game names.
    Unknown codes are omitted from the result.
    """
    return {code: entry[0] for code, entry in lookup_names_typed(product_codes).items()}


def lookup_names_typed(product_codes: list[str]) -> dict[str, tuple[str, str]]:
    """Look up game names and platform types for a list of product codes.

    Returns a dict mapping input codes to (name, type) tuples.
    Type is one of: "VITA", "PS1", "PS2", "PS3", "PSP", "3DS", "NDS".
    Unknown codes are omitted from the result.
    """
    result = {}

    for code in product_codes:
        code_upper = code.upper().strip()

        # PS Vita product code (PCSX##### format, 9 chars)
        if _VITA_CODE_RE.match(code_upper):
            name = _vita_names.get(code_upper)
            if name:
                result[code] = (name, "VITA")
            continue

        base = code_upper[:9]

        # Prefer PS3 DB hits first because PS3 PSN serials overlap with PSP.
        if base in _ps3_names:
            name = _ps3_names.get(base)
            if name:
                result[code] = (name, "PS3")
            continue

        # For PS1/PS2-style serial families the numeric heuristic is not
        # sufficient for every region prefix (notably Japanese SLPM/SLPS
        # ranges). When one local DAT contains the code and the other does
        # not, trust the explicit DAT membership before falling back to the
        # prefix/number heuristic.
        in_ps1_dat = base in _psx_names
        in_ps2_dat = base in _ps2_names
        if in_ps2_dat and not in_ps1_dat:
            result[code] = (_ps2_names[base], "PS2")
            continue
        if in_ps1_dat and not in_ps2_dat and base[:4] in _AMBIGUOUS_PSX_PREFIXES:
            result[code] = (_psx_names[base], "PS1")
            continue

        platform = _detect_playstation_platform_heuristic(code_upper)
        if platform == "VITA":
            name = _vita_names.get(base)
            if name:
                result[code] = (name, "VITA")
            continue
        if platform == "PS3":
            name = _ps3_names.get(base)
            if name:
                result[code] = (name, "PS3")
            continue
        if platform == "PS1":
            name = _psx_names.get(base)
            if name:
                result[code] = (name, "PS1")
            continue
        if platform == "PS2":
            # Fall back to _psx_names when the PS2 DAT hasn't been loaded
            # yet — legacy data in the wild carries SLUS/SCUS/SLES codes
            # in the PSX dict from before "Sony - PlayStation 2.dat"
            # was routed to its own dict.
            name = _ps2_names.get(base) or _psx_names.get(base)
            if name:
                result[code] = (name, "PS2")
            continue
        if code_upper in _sat_names:
            result[code] = (_sat_names[code_upper], "SAT")
            continue
        if code_upper.startswith("SAT_"):
            sat_serial = _sat_safe_to_serial.get(code_upper[4:])
            if sat_serial and sat_serial in _sat_names:
                result[code] = (_sat_names[sat_serial], "SAT")
                continue
        if platform == "PSP":
            name = _psp_names.get(base)
            if name:
                result[code] = (name, "PSP")
            continue

        # 3DS/DS: full 16-char hex TitleID
        is_3ds_format = code_upper.startswith("CTR-")

        if len(code_upper) == 16 and all(c in "0123456789ABCDEF" for c in code_upper):
            # 1. Direct TitleID lookup populated from 3DS DAT title_id lines.
            name = _3ds_title_ids.get(code_upper)
            if name:
                platform = "NDS" if code_upper[:5] in _NDS_HIGH_PREFIXES else "3DS"
                result[code] = (name, platform)
                continue

            # 2. Fallback: for NDS/DSiWare (00048...) the lower 4 bytes encode
            #    the ASCII game code; look that up in the legacy dstdb.txt.
            low_hex = code_upper[8:16]
            try:
                decoded = bytes.fromhex(low_hex).decode("ascii")
                game_code = (
                    decoded[:4] if decoded.isalnum() and decoded.isupper() else None
                )
            except (ValueError, UnicodeDecodeError):
                game_code = None

            if game_code:
                name = _ds_names.get(game_code) or _3ds_names.get(game_code)
                if name:
                    platform = "NDS" if _ds_names.get(game_code) else "3DS"
                    result[code] = (name, platform)
            continue

        # GC/Wii emulator format: GC_xxxx (e.g. GC_gbze → game code GBZE)
        if code_upper.startswith("GC_") and len(code_upper) >= 6:
            game_code = code_upper[3:7]
            name = _wii_names.get(game_code)
            if name:
                result[code] = (name, "GC")
            continue

        if len(code_upper) >= 10 and "-" in code_upper:
            parts = code_upper.split("-")
            game_code = parts[2][:4] if len(parts) >= 3 else code_upper[-4:]
        elif len(code_upper) == 4:
            game_code = code_upper
        else:
            game_code = code_upper[-4:] if len(code_upper) >= 4 else code_upper

        if is_3ds_format:
            name = _3ds_names.get(game_code) or _ds_names.get(game_code)
            platform = "3DS" if _3ds_names.get(game_code) else "NDS"
        else:
            name = _ds_names.get(game_code) or _3ds_names.get(game_code)
            platform = "NDS" if _ds_names.get(game_code) else "3DS"

        if name:
            result[code] = (name, platform)

    return result


def build_psx_psn_to_retail() -> int:
    """Build the PSN→retail serial mapping and rebuild the slug→serial index.

    Must be called once after all psxdb databases are loaded.

    Groups all PS1 entries by name slug, then:
    - Maps each PSN code (NP*) to the best retail code (SL*/SC*/PA*) for the same game.
    - Rebuilds _psx_by_slug to always prefer retail codes so that name lookups
      (e.g. from the normalize endpoint) return retail serials, never PSN IDs.

    Returns the number of PSN→retail mappings created.
    """
    global _psx_by_slug, _psx_psn_to_retail, _psx_serials_by_slug

    # Group by name slug: slug → {retail: [...], psn: [...]}
    slug_retail: dict[str, list[str]] = {}
    slug_psn: dict[str, list[str]] = {}

    for code, name in _psx_names.items():
        if not _PSP_CODE_RE.match(code):
            continue
        slug = _psx_name_slug(name)
        if not slug:
            continue
        if _RETAIL_RE.match(code):
            slug_retail.setdefault(slug, []).append(code)
        elif _PSN_RE.match(code):
            slug_psn.setdefault(slug, []).append(code)

    # Build PSN → retail mapping (best retail = first alphabetical, biased toward
    # matching region by prefix ordering: SC before SL, PA last).
    _psx_psn_to_retail.clear()
    for slug, psn_codes in slug_psn.items():
        retail_codes = slug_retail.get(slug)
        if not retail_codes:
            continue
        best = sorted(retail_codes)[0]  # simple stable choice; good enough
        for psn_code in psn_codes:
            _psx_psn_to_retail[psn_code] = best

    # Rebuild slug index: retail codes take priority, PSN only if no retail exists
    new_index: dict[str, str] = {}
    slug_serials: dict[str, list[str]] = {}
    for slug, retail_codes in slug_retail.items():
        new_index[slug] = sorted(retail_codes)[0]
        slug_serials[slug] = sorted(retail_codes)
    for slug, psn_codes in slug_psn.items():
        if slug not in new_index:
            new_index[slug] = sorted(psn_codes)[0]
        slug_serials.setdefault(slug, []).extend(sorted(psn_codes))

    _psx_by_slug.clear()
    _psx_by_slug.update(new_index)
    _psx_serials_by_slug.clear()
    _psx_serials_by_slug.update(slug_serials)

    return len(_psx_psn_to_retail)


def get_psx_retail_serial(code: str) -> str | None:
    """If code is a PSN PSone Classic ID (NP*), return the retail disc serial.

    e.g. "NPUJ00662" → "SLPM86034"  (Parasite Eve Japan)
    Returns None for codes that are already retail serials or have no mapping.
    """
    return _psx_psn_to_retail.get(code.upper().strip()[:9])


def build_saturn_slug_index() -> int:
    global _sat_by_slug, _sat_serials_by_slug, _sat_safe_to_serial

    new_index: dict[str, str] = {}
    slug_serials: dict[str, list[str]] = {}

    for code, name in _sat_names.items():
        slug = _psx_name_slug(name)
        if not slug:
            continue
        slug_serials.setdefault(slug, []).append(code)

    for slug, codes in slug_serials.items():
        sorted_codes = sorted(codes)
        new_index[slug] = sorted_codes[0]
        slug_serials[slug] = sorted_codes

    _sat_by_slug.clear()
    _sat_by_slug.update(new_index)
    _sat_serials_by_slug.clear()
    _sat_serials_by_slug.update(slug_serials)
    _sat_safe_to_serial.clear()
    _sat_safe_to_serial.update({_saturn_safe_id(code): code for code in _sat_names})
    return len(_sat_by_slug)


def lookup_psx_serial(name: str) -> str | None:
    """Return the PS1 product code for a game name or ROM filename, or None.

    Strips region/disc tags before matching, so both "Final Fantasy VII (USA)"
    and "Final Fantasy VII [Disc1of3]" resolve to the same serial.
    """
    slug = _psx_name_slug(name)
    candidates = _psx_serials_by_slug.get(slug)
    if not candidates:
        fallback = _psx_by_slug.get(slug)
        if fallback:
            return fallback

        for variant in _slug_roman_variants(slug):
            candidates = _psx_serials_by_slug.get(variant)
            if candidates:
                break
            fallback = _psx_by_slug.get(variant)
            if fallback:
                return fallback

    if not candidates:
        return None
    region_hint = _psx_region_hint(name)
    return min(candidates, key=lambda code: _psx_serial_region_rank(code, region_hint))


def lookup_saturn_serial(name: str) -> str | None:
    slug = _psx_name_slug(name)
    candidates = _sat_serials_by_slug.get(slug)
    if not candidates:
        return _sat_by_slug.get(slug)
    region_hint = _psx_region_hint(name)
    return min(candidates, key=lambda code: _sat_serial_region_rank(code, region_hint))


def lookup_3ds_title_id(name: str) -> str | None:
    slug = normalize_rom_name(name)
    if not slug or slug == "unknown":
        return None
    preferred = _3ds_by_slug.get(slug)
    if preferred:
        return preferred
    candidates = _3ds_title_ids_by_slug.get(slug)
    if candidates:
        return candidates[0]
    return None


def get_3ds_title_id_count() -> int:
    return len(_3ds_title_ids)


def lookup_disc_serial(system: str, name: str) -> str | None:
    sys_upper = system.upper().strip()
    if sys_upper in ("PS1", "PSX"):
        return lookup_psx_serial(name)
    if sys_upper == "3DS":
        return lookup_3ds_title_id(name)
    if sys_upper == "SAT":
        serial = lookup_saturn_serial(name)
        return make_saturn_title_id(serial) if serial else None
    return None


def get_name(product_code: str) -> str | None:
    """Look up a single game name. Returns None if not found."""
    result = lookup_names([product_code])
    return result.get(product_code)


def lookup_name_and_platform(title_id: str) -> tuple[str, str]:
    """Return (game_name, platform) for a title ID.

    game_name falls back to title_id if not found in any DB.
    platform is always one of: "3DS", "NDS", "PSP", "PS1", "VITA".
    """
    platform = detect_platform(title_id)
    typed = lookup_names_typed([title_id])
    if title_id in typed:
        name, _ = typed[title_id]
        return name, platform
    return title_id, platform
