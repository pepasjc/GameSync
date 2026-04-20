"""Saturn ROM identification — shared between server and all Python clients.

Ports the two mechanisms the Android client already uses (see
``android/app/src/main/kotlin/com/savesync/android/emulators/EmulatorBase.kt``):

1. **IP.BIN parser** — reads the Saturn hardware ID ``"SEGA SEGASATURN "`` at
   sector 0 and extracts the 10-byte product code at offset ``0x20``. Works on
   ``.iso`` (2048-byte sectors) and raw ``.bin`` / ``.img`` / ``.cue`` (2352-byte
   sectors, data at offset 0x10). Does not work on ``.chd`` (compressed).

2. **DAT lookup** — fallback for CHDs and other images we can't read inline.
   Parses the libretro clrmamepro DAT and matches by game name (with the same
   progressive parenthetical-stripping Android uses, so
   ``"Grandia (Japan) (Disc 1) (4M)"`` still resolves to ``T-4507G``).

Both return a canonical ``SAT_<PRODUCT_CODE>`` title ID (e.g. ``SAT_T-4507G``)
matching what the server already stores in ``saturn_archive_names.json`` and
what the Android client produces, so the three clients no longer diverge.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

_SATURN_MAGIC = b"SEGA SEGASATURN "

_ISO_EXTS = {".iso"}
_RAW_EXTS = {".bin", ".img"}
_CUE_EXT = ".cue"

# Single letter + hyphen + single digit at the end of a serial (e.g. "T-4507G-0",
# "GS-9076-2") = per-disc variant of a canonical product code. Skip during DAT
# ingestion so the canonical serial (without the suffix) is what gets stored.
_DISC_INDEX_RE = re.compile(r"[A-Za-z]-\d$")

# Strip [bracket] tags (fan-translation / hack markers).
_BRACKET_TAG_RE = re.compile(r"\s*\[[^\]]*\]")

# Trailing "(...)" group — used to progressively shrink the name until a DAT
# entry matches.
_TRAILING_PAREN_RE = re.compile(r"\s*\([^)]*\)\s*$")

# clrmamepro DAT line matchers. We only care about the first game-level "name"
# and "serial" per game block; per-rom serials are ignored.
_GAME_OPEN = "game ("
_GAME_CLOSE = ")"
_NAME_RE = re.compile(r'^\s+name\s+"(.+)"')
_SERIAL_RE = re.compile(r'^\s+serial\s+"([^"]+)"')


# ---------------------------------------------------------------------------
# Serial → title_id normalization
# ---------------------------------------------------------------------------


def _safe_saturn_id(product_code: str) -> Optional[str]:
    """
    Normalise a raw product code into the form used everywhere else in the
    codebase (uppercase, alphanumeric + ``_`` + ``-``).
    Matches Android's ``lookupSaturnSerial`` / ``readSaturnProductCode``
    normalisation and the server's ``_saturn_safe_id`` behaviour.
    """
    if not product_code:
        return None
    safe = product_code.replace(" ", "_")
    safe = "".join(c for c in safe if c.isalnum() or c in "_-").upper()
    return safe or None


def _format_title_id(product_code: str) -> Optional[str]:
    safe = _safe_saturn_id(product_code)
    return f"SAT_{safe}" if safe else None


# ---------------------------------------------------------------------------
# IP.BIN parser
# ---------------------------------------------------------------------------


def _resolve_cue_to_bin(cue_path: Path) -> Optional[Path]:
    """Parse a .cue file and return the first referenced track (usually .bin)."""
    try:
        for line in cue_path.read_text(errors="ignore").splitlines():
            stripped = line.strip()
            if stripped.upper().startswith("FILE"):
                m = re.search(r'FILE\s+"(.+?)"', stripped, re.IGNORECASE)
                if m:
                    referenced = cue_path.parent / m.group(1)
                    if referenced.exists():
                        return referenced
    except Exception:
        pass
    return None


def read_saturn_product_code(rom_file: Path) -> Optional[str]:
    """
    Parse a Saturn disc image and return a ``"SAT_<PRODUCT_CODE>"`` title ID.

    Returns None if the file isn't a Saturn disc we can read inline (e.g. CHD,
    corrupt image, missing file).  Callers should fall back to the DAT lookup
    in that case.
    """
    ext = rom_file.suffix.lower()
    image_file: Optional[Path]
    if ext == _CUE_EXT:
        image_file = _resolve_cue_to_bin(rom_file)
        if image_file is None:
            return None
        ext = image_file.suffix.lower()
    else:
        image_file = rom_file

    if ext in _ISO_EXTS:
        data_offset = 0
    elif ext in _RAW_EXTS:
        # 12-byte sync pattern + 4-byte header before user data in MODE1/2352
        data_offset = 0x10
    else:
        return None

    try:
        with open(image_file, "rb") as f:
            buf = f.read(data_offset + 0x30)
    except OSError:
        return None

    if len(buf) < data_offset + 0x30:
        return None
    if buf[data_offset : data_offset + len(_SATURN_MAGIC)] != _SATURN_MAGIC:
        return None

    raw = buf[data_offset + 0x20 : data_offset + 0x2A].decode(
        "ascii", errors="ignore"
    )
    # The code field is padded to 10 bytes; the version string may follow after
    # 2+ spaces or a "Vn" suffix.  Drop that so the title ID is stable across
    # firmware revisions of the same game.
    product_code = re.split(r"\s{2,}|(?<=\w)\s*V\d", raw)[0].strip()
    return _format_title_id(product_code)


# ---------------------------------------------------------------------------
# DAT parser + name-based lookup
# ---------------------------------------------------------------------------


def parse_saturn_dat(text: str) -> dict[str, str]:
    """
    Parse a clrmamepro Saturn DAT and return ``{lower_game_name: PRODUCT_CODE}``.

    The game-level ``serial`` field (at one indent) is authoritative; per-rom
    serials inside ``rom ( ... )`` are ignored.  Disc-index variants like
    ``T-4507G-0`` are skipped so the canonical code is what gets stored.
    """
    result: dict[str, str] = {}
    in_game = False
    cur_name = ""
    cur_serial = ""

    for line in text.splitlines():
        stripped = line.strip()
        if stripped == _GAME_OPEN:
            in_game = True
            cur_name = ""
            cur_serial = ""
            continue
        if stripped == _GAME_CLOSE and in_game:
            if cur_name and cur_serial:
                key = cur_name.lower()
                # putIfAbsent: first occurrence (usually primary region) wins.
                result.setdefault(key, cur_serial)
            in_game = False
            continue
        if not in_game:
            continue
        if not cur_name:
            m = _NAME_RE.match(line)
            if m:
                cur_name = m.group(1)
                continue
        if not cur_serial:
            m = _SERIAL_RE.match(line)
            if m:
                serial = m.group(1)
                if not _DISC_INDEX_RE.search(serial):
                    cur_serial = serial

    return result


def _strip_bracket_tags(name: str) -> str:
    return _BRACKET_TAG_RE.sub("", name).strip()


def lookup_saturn_serial_in_dat(
    rom_name: str, name_to_serial: dict[str, str]
) -> Optional[str]:
    """
    Find the product serial for ``rom_name`` in a pre-parsed DAT map.

    Tries exact (case-insensitive) match first, then progressively strips
    trailing ``(...)`` groups so names like
    ``"Grandia (Japan) (Disc 1) (4M) [T-En by TrekkiesUnite118 v0.9.3 RC]"``
    still fall through to ``"Grandia (Japan)"``.
    """
    if not name_to_serial:
        return None

    name = _strip_bracket_tags(rom_name)
    hit = name_to_serial.get(name.lower())
    if hit:
        return hit

    while True:
        stripped = _TRAILING_PAREN_RE.sub("", name).strip()
        if stripped == name or not stripped:
            return None
        name = stripped
        hit = name_to_serial.get(name.lower())
        if hit:
            return hit


# ---------------------------------------------------------------------------
# DAT discovery + caching
# ---------------------------------------------------------------------------

_DAT_CANDIDATE_NAMES = (
    "Sega - Saturn.dat",
    "Sega - Saturn (libretro).dat",
)


def _default_dat_candidates() -> list[Path]:
    """
    Walk up from this file's directory looking for the Saturn DAT.  Covers the
    server layout (``server/data/dats/...``), a future ``shared/data/dats/...``,
    and the Android asset path when the repo is checked out directly.
    """
    start = Path(__file__).resolve().parent
    roots = [start] + list(start.parents)
    candidates: list[Path] = []
    subdirs = (
        Path("data") / "dats",
        Path("server") / "data" / "dats",
        Path("shared") / "data" / "dats",
        Path("android") / "app" / "src" / "main" / "assets",
    )
    for root in roots:
        for sub in subdirs:
            for name in _DAT_CANDIDATE_NAMES:
                candidates.append(root / sub / name)
    return candidates


_dat_cache: dict[str, dict[str, str]] = {}


def _load_dat(dat_path: Optional[Path]) -> dict[str, str]:
    """Load + parse a Saturn DAT, cached by absolute path."""
    if dat_path is None:
        for candidate in _default_dat_candidates():
            if candidate.is_file():
                dat_path = candidate
                break
    if dat_path is None or not dat_path.is_file():
        return {}

    key = str(dat_path.resolve())
    cached = _dat_cache.get(key)
    if cached is not None:
        return cached

    try:
        text = dat_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    parsed = parse_saturn_dat(text)
    _dat_cache[key] = parsed
    return parsed


# ---------------------------------------------------------------------------
# Public resolver
# ---------------------------------------------------------------------------


def resolve_saturn_title_id(
    rom_path: Optional[Path] = None,
    rom_name: Optional[str] = None,
    dat_path: Optional[Path] = None,
) -> Optional[str]:
    """
    Return a canonical ``SAT_<serial>`` title ID for a Saturn game, or None if
    neither the IP.BIN parser nor the DAT lookup can identify it.

    Order of attempts:
      1. If ``rom_path`` is an ``.iso`` / ``.bin`` / ``.cue``, read its IP.BIN.
      2. Otherwise (or on failure) look up ``rom_name`` (or ``rom_path.stem``)
         in the bundled libretro DAT.

    Callers should fall back to their existing filename-slug logic on None so
    unidentified discs still get a usable title ID.
    """
    if rom_path is not None:
        via_ipbin = read_saturn_product_code(rom_path)
        if via_ipbin:
            return via_ipbin

    name = rom_name
    if name is None and rom_path is not None:
        name = rom_path.stem
    if not name:
        return None

    name_to_serial = _load_dat(dat_path)
    serial = lookup_saturn_serial_in_dat(name, name_to_serial)
    return _format_title_id(serial) if serial else None


__all__ = [
    "lookup_saturn_serial_in_dat",
    "parse_saturn_dat",
    "read_saturn_product_code",
    "resolve_saturn_title_id",
]
