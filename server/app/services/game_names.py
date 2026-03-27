"""Game name lookup service using 3dstdb.txt, dstdb.txt, psptdb.txt, vitatdb.txt."""

import re
from pathlib import Path

from app.services.rom_id import SYSTEM_CODES, parse_title_id as _parse_emulator_id

# Global cache for game names (loaded once at startup)
_3ds_title_ids: dict[str, str] = {}  # full 16-char hex TitleID -> name (from 3dstitledb.txt)
_3ds_names: dict[str, str] = {}      # 4-char game code -> name (legacy, from 3dstdb.txt)
_ds_names: dict[str, str] = {}       # 4-char game code -> name
_psp_names: dict[str, str] = {}      # keyed by full product code e.g. "ULUS10272"
_psx_names: dict[str, str] = {}      # keyed by full product code e.g. "SCUS94163"
_vita_names: dict[str, str] = {}     # keyed by full product code e.g. "PCSE00082"

# Reverse index: normalized game name slug → PS1 retail serial (preferred over PSN codes)
# Rebuilt by build_psx_psn_to_retail() after all databases are loaded.
_psx_by_slug: dict[str, str] = {}

# PSN PSone Classic code → original retail disc serial
# e.g. "NPUJ00662" (Parasite Eve Japan PSN) → "SLPM86034" (Parasite Eve Japan retail)
_psx_psn_to_retail: dict[str, str] = {}

_PSN_RE    = re.compile(r"^NP")
_RETAIL_RE = re.compile(r"^(SL|SC|PA)")

# Strips parenthesized (USA) / bracketed [Disc1of3] tags from psxdb names before slugifying
_BRACKET_RE = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")


def _psx_name_slug(name: str) -> str:
    """Normalize a psxdb game name to a plain slug for reverse lookup.

    "Final Fantasy VII [Disc1of3]" → "final_fantasy_vii"
    "007 - The World Is Not Enough" → "007_the_world_is_not_enough"
    """
    clean = _BRACKET_RE.sub("", name).strip()
    return re.sub(r"[^a-z0-9]+", "_", clean.lower()).strip("_")

# Patterns for platform detection
_PSP_CODE_RE   = re.compile(r"^[A-Z]{4}\d{5}$")   # ULUS10000, ELES01234, NPUH10001
_PSP_PREFIX_RE = re.compile(r"^[A-Z]{4}\d{5}")    # same but allows slot suffix
_VITA_CODE_RE  = re.compile(r"^PCS[A-Z]\d{5}$")   # PCSE00000, PCSB12345, PCSG00001

# 3DS title ID high-word prefixes (first 5 hex chars of the 16-char ID)
_3DS_HIGH_PREFIXES = {"00040", "00041", "00042", "00043", "00044", "00045", "00046", "00047"}
_NDS_HIGH_PREFIXES = {"00048"}


def detect_platform(title_id: str) -> str:
    """Return the platform string for a title ID.

    Returns one of: "3DS", "NDS", "PSP", "PSX", "VITA", or an emulator
    system code like "GBA", "SNES", "MD", etc.

    Rules:
      - Emulator SYSTEM_slug format           → system code (e.g. "GBA")
      - 16-char hex, starts with 00040... → "3DS"
      - 16-char hex, starts with 00048... → "NDS"  (DSiWare shown on 3DS)
      - 16-char hex, anything else         → "3DS"  (conservative fallback)
      - Starts with PCS (PCSA/PCSB/etc.)  → "VITA"
      - 4-letter + 5-digit code, found in psx_names → "PS1"
      - 4-letter + 5-digit code otherwise  → "PSP"
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

    # PSP / PSX: 4 letters + 5 digits (optionally with slot suffix)
    if _PSP_PREFIX_RE.match(tid):
        base = tid[:9]
        if base in _psx_names:
            return "PS1"
        return "PSP"

    # Fallback: treat as NDS (DS raw endpoint sends no product code context)
    return "NDS"


_HEX_16_RE = re.compile(r"^[0-9A-F]{16}$")


def load_database(db_path: Path | None = None) -> int:
    """Load a game names database from file into the appropriate cache.

    Automatically detects the target dict based on filename:
      - 3dstitledb.txt  → _3ds_title_ids (full 16-char hex TitleID -> name)
      - 3dstdb.txt      → _3ds_names  (4-char game code -> name)
      - dstdb.txt       → _ds_names
      - pspdb.txt       → _psp_names
      - vitadb.txt      → _vita_names
      - psxdb.txt       → _psx_names

    Returns the number of entries loaded.
    """
    global _3ds_title_ids, _3ds_names, _ds_names, _psp_names, _vita_names, _psx_by_slug

    if db_path is None:
        db_path = Path(__file__).parent.parent.parent / "data" / "3dstdb.txt"

    if not db_path.exists():
        return 0

    fname = db_path.name.lower()
    if "3dstitledb" in fname:
        target_dict = _3ds_title_ids
    elif "vita" in fname:
        target_dict = _vita_names
    elif "psx" in fname:
        target_dict = _psx_names
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
                if code and game_name:
                    target_dict[code] = game_name
                    added += 1

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
    Type is one of: "VITA", "PS1", "PSP", "3DS", "NDS".
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

        # PSX/PSP product code — may have a slot suffix (e.g. ULUS10272DATA00).
        # Always look up by the 9-char base; return result keyed by original code.
        if _PSP_PREFIX_RE.match(code_upper):
            base = code_upper[:9]
            name = _psx_names.get(base)
            if name:
                result[code] = (name, "PS1")
                continue
            name = _psp_names.get(base)
            if name:
                result[code] = (name, "PSP")
            continue

        # 3DS/DS: full 16-char hex TitleID
        is_3ds_format = code_upper.startswith("CTR-")

        if len(code_upper) == 16 and all(c in "0123456789ABCDEF" for c in code_upper):
            # 1. Direct TitleID lookup (3dstitledb.txt — most accurate)
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
                game_code = decoded[:4] if decoded.isalnum() and decoded.isupper() else None
            except (ValueError, UnicodeDecodeError):
                game_code = None

            if game_code:
                name = _ds_names.get(game_code) or _3ds_names.get(game_code)
                if name:
                    platform = "NDS" if _ds_names.get(game_code) else "3DS"
                    result[code] = (name, platform)
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
    global _psx_by_slug, _psx_psn_to_retail

    # Group by name slug: slug → {retail: [...], psn: [...]}
    slug_retail: dict[str, list[str]] = {}
    slug_psn:    dict[str, list[str]] = {}

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
        best = sorted(retail_codes)[0]   # simple stable choice; good enough
        for psn_code in psn_codes:
            _psx_psn_to_retail[psn_code] = best

    # Rebuild slug index: retail codes take priority, PSN only if no retail exists
    new_index: dict[str, str] = {}
    for slug, retail_codes in slug_retail.items():
        new_index[slug] = sorted(retail_codes)[0]
    for slug, psn_codes in slug_psn.items():
        if slug not in new_index:
            new_index[slug] = sorted(psn_codes)[0]

    _psx_by_slug.clear()
    _psx_by_slug.update(new_index)

    return len(_psx_psn_to_retail)


def get_psx_retail_serial(code: str) -> str | None:
    """If code is a PSN PSone Classic ID (NP*), return the retail disc serial.

    e.g. "NPUJ00662" → "SLPM86034"  (Parasite Eve Japan)
    Returns None for codes that are already retail serials or have no mapping.
    """
    return _psx_psn_to_retail.get(code.upper().strip()[:9])


def lookup_psx_serial(name: str) -> str | None:
    """Return the PS1 product code for a game name or ROM filename, or None.

    Strips region/disc tags before matching, so both "Final Fantasy VII (USA)"
    and "Final Fantasy VII [Disc1of3]" resolve to the same serial.
    """
    return _psx_by_slug.get(_psx_name_slug(name))


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
