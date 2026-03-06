"""Game name lookup service using 3dstdb.txt, dstdb.txt, psptdb.txt, vitatdb.txt."""

import re
from pathlib import Path

from app.services.rom_id import SYSTEM_CODES, parse_title_id as _parse_emulator_id

# Global cache for game names (loaded once at startup)
_3ds_names: dict[str, str] = {}
_ds_names: dict[str, str] = {}
_psp_names: dict[str, str] = {}   # keyed by full product code e.g. "ULUS10272"
_psx_names: dict[str, str] = {}   # keyed by full product code e.g. "NPUF30001"
_vita_names: dict[str, str] = {}  # keyed by full product code e.g. "PCSE00082"

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
      - 4-letter + 5-digit code, found in psx_names → "PSX"
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
            return "PSX"
        return "PSP"

    # Fallback: treat as NDS (DS raw endpoint sends no product code context)
    return "NDS"


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
    elif "psx" in name:
        target_dict = _psx_names
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

    Returns a dict mapping input codes to their game names.
    Unknown codes are omitted from the result.
    """
    return {code: entry[0] for code, entry in lookup_names_typed(product_codes).items()}


def lookup_names_typed(product_codes: list[str]) -> dict[str, tuple[str, str]]:
    """Look up game names and platform types for a list of product codes.

    Returns a dict mapping input codes to (name, type) tuples.
    Type is one of: "VITA", "PSX", "PSP", "3DS", "NDS".
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
                result[code] = (name, "PSX")
                continue
            name = _psp_names.get(base)
            if name:
                result[code] = (name, "PSP")
            continue

        # 3DS/DS: extract 4-char game code
        is_3ds_format = code_upper.startswith("CTR-")

        if len(code_upper) == 16 and all(c in "0123456789ABCDEF" for c in code_upper):
            # 16-char hex title ID. For NDS/DSiWare (00048...), the lower 4 bytes
            # encode the ASCII game code. For 3DS retail (00040000...) the lower
            # bytes are a sequential ID — no ASCII code to decode.
            low_hex = code_upper[8:16]
            try:
                decoded = bytes.fromhex(low_hex).decode("ascii")
                if decoded.isalnum() and decoded.isupper():
                    game_code = decoded[:4]
                else:
                    game_code = None
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


def get_name(product_code: str) -> str | None:
    """Look up a single game name. Returns None if not found."""
    result = lookup_names([product_code])
    return result.get(product_code)


def lookup_name_and_platform(title_id: str) -> tuple[str, str]:
    """Return (game_name, platform) for a title ID.

    game_name falls back to title_id if not found in any DB.
    platform is always one of: "3DS", "NDS", "PSP", "PSX", "VITA".
    """
    platform = detect_platform(title_id)
    typed = lookup_names_typed([title_id])
    if title_id in typed:
        name, _ = typed[title_id]
        return name, platform
    return title_id, platform
