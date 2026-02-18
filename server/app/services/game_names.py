"""Game name lookup service using 3dstdb.txt, dstdb.txt, psptdb.txt, vitatdb.txt."""

import re
from pathlib import Path

# Global cache for game names (loaded once at startup)
_3ds_names: dict[str, str] = {}
_ds_names: dict[str, str] = {}
_psp_names: dict[str, str] = {}   # keyed by full product code e.g. "ULUS10272"
_vita_names: dict[str, str] = {}  # keyed by full product code e.g. "PCSE00082"

# Patterns for platform detection
_PSP_CODE_RE = re.compile(r"^[A-Z]{4}\d{5}$")   # ULUS10000, ELES01234, NPUH10001
_VITA_CODE_RE = re.compile(r"^PCS[A-Z]\d{5}$")   # PCSE00000, PCSB12345, PCSG00001


def load_database(db_path: Path | None = None) -> int:
    """Load a game names database from file into the appropriate cache.

    Automatically detects whether it's 3DS, DS, PSP or Vita based on filename.
    Returns the number of entries loaded.
    """
    global _3ds_names, _ds_names, _psp_names, _vita_names

    if db_path is None:
        db_path = Path(__file__).parent.parent.parent / "data" / "3dstdb.txt"

    if not db_path.exists():
        return 0

    name = db_path.name.lower()
    if "vita" in name:
        target_dict = _vita_names
    elif "psp" in name:
        target_dict = _psp_names
    elif "ds" in name and "3ds" not in name:
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

    Handles:
    - 3DS: full format CTR-P-XXXX or short 4-char code
    - DS:  short 4-char code (prioritized over 3DS for ambiguous codes)
    - PSP: 9-char product code like ULUS10000, ELES01234, NPUH10001
    - Vita: 9-char product code like PCSE00082, PCSB12345

    Returns a dict mapping input codes to their game names.
    Unknown codes are omitted from the result.
    """
    result = {}

    for code in product_codes:
        code_upper = code.upper().strip()

        # PSP product code (XYYY##### format, 9 chars)
        if _PSP_CODE_RE.match(code_upper):
            name = _psp_names.get(code_upper)
            if name:
                result[code] = name
            continue

        # PS Vita product code (PCSX##### format, 9 chars)
        if _VITA_CODE_RE.match(code_upper):
            name = _vita_names.get(code_upper)
            if name:
                result[code] = name
            continue

        # 3DS/DS: extract 4-char game code
        is_3ds_format = code_upper.startswith("CTR-")

        if len(code_upper) >= 10 and "-" in code_upper:
            parts = code_upper.split("-")
            if len(parts) >= 3:
                game_code = parts[2][:4]
            else:
                game_code = code_upper[-4:]
        elif len(code_upper) == 4:
            game_code = code_upper
        else:
            game_code = code_upper[-4:] if len(code_upper) >= 4 else code_upper

        name = None
        if is_3ds_format:
            name = _3ds_names.get(game_code) or _ds_names.get(game_code)
        else:
            name = _ds_names.get(game_code) or _3ds_names.get(game_code)

        if name:
            result[code] = name

    return result


def get_name(product_code: str) -> str | None:
    """Look up a single game name. Returns None if not found."""
    result = lookup_names([product_code])
    return result.get(product_code)
