"""ROM filename normalization and emulator-style title IDs.

This is the single source of truth for converting a ROM filename like
``"Legend of Zelda, The - Minish Cap (USA).gba"`` into a stable slug
(``legend_of_zelda_the_minish_cap_usa``) and, combined with a system
code, a canonical title_id (``GBA_legend_of_zelda_the_minish_cap_usa``).

Why a separate module
---------------------
Both the server and every Python client need to agree on the same slug
rules, or they end up with different storage keys for the same save.
This file stays small, pure, and dependency-light so it can be imported
from anywhere — including tests that shouldn't need the full server
stack.
"""

from __future__ import annotations

import re

from shared.systems import SYSTEM_CODES


# Regex for emulator title_id format: SYSTEM_slug.
# The slug may be lowercase (ROM-name style: ``GBA_zelda_the_minish_cap``)
# or uppercase with hyphens (product-code style: ``SAT_GS-9188``,
# ``SAT_T-14410G`` for Saroo Saturn saves).
_EMULATOR_TITLE_ID_RE = re.compile(
    r"^([A-Z0-9]{2,8})_([A-Za-z0-9][A-Za-z0-9_-]{0,99})$"
)

# Parenthetical tags stripped from ROM filenames before slugification.  They
# are split across three regexes so we can handle them in distinct passes:
# the region is extracted first (and moved to the slug suffix so regional
# releases get distinct title_ids), then revision/disc/misc tags are
# removed outright.
_REGION_RE = re.compile(
    r"\s*\("
    r"(?:USA|Europe|Japan|World|Germany|France|Italy|Spain|Australia|"
    r"Brazil|Korea|China|Netherlands|Sweden|Denmark|Norway|Finland|Asia|"
    r"En|Ja|Fr|De|Es|It|Nl|Pt|Sv|No|Da|Fi|Ko|Zh|[A-Z][a-z,\s]+)"
    r"\)",
    re.IGNORECASE,
)
_REV_RE = re.compile(
    r"\s*\((?:Rev\s*\w+|v\d[\d.]*|Version\s*\w+|Beta\s*\d*|Proto\s*\d*|Demo|Sample|Unl)\)",
    re.IGNORECASE,
)
_DISC_RE = re.compile(r"\s*\((?:Disc|Disk|CD)\s*\d+\)", re.IGNORECASE)
_EXTRA_RE = re.compile(r"\s*\([^)]+\)")

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_MULTI_UNDERSCORE_RE = re.compile(r"_+")


def normalize_rom_name(filename: str) -> str:
    """Strip extension and revision/disc tags; append region to the slug.

    Region tags are moved to the end so that different regional releases of
    the same game get distinct title_ids while still grouping cleanly by
    game name.

    Examples
    --------
    ``"Castlevania - Dracula X (USA).sfc"``           → ``"castlevania_dracula_x_usa"``
    ``"Super Mario World (USA).sfc"``                 → ``"super_mario_world_usa"``
    ``"Sonic the Hedgehog (USA, Europe).md"``         → ``"sonic_the_hedgehog_usa_europe"``
    ``"Final Fantasy VII (Rev 1) (USA).bin"``         → ``"final_fantasy_vii_usa"``
    ``"Legend of Zelda, The - Minish Cap (USA).gba"`` → ``"legend_of_zelda_the_minish_cap_usa"``
    ``"Homebrew Game.sfc"``                           → ``"homebrew_game"``
    """
    name = _strip_extension(filename)

    # Extract region before stripping all parenthetical tags.
    region_match = _REGION_RE.search(name)
    region_parts = ""
    if region_match:
        region_text = region_match.group(0).strip(" ()")
        region_parts = "_".join(region_text.lower().replace(",", " ").split())

    # Strip revision, disc, and all remaining parentheticals (including region).
    name = _REV_RE.sub("", name)
    name = _DISC_RE.sub("", name)
    name = _EXTRA_RE.sub("", name)

    name = name.lower()
    name = _NON_ALNUM_RE.sub("_", name)
    name = _MULTI_UNDERSCORE_RE.sub("_", name).strip("_")

    if region_parts:
        name = f"{name}_{region_parts}"

    return name or "unknown"


def make_title_id(system: str, rom_filename: str) -> str:
    """Return the canonical title_id, e.g. ``GBA_legend_of_zelda_the_minish_cap_usa``.

    Raises ``ValueError`` when ``system`` isn't in the shared registry —
    callers should either pass a canonicalised code or fall back to a
    free-form composition.
    """
    system = system.upper().strip()
    if system not in SYSTEM_CODES:
        raise ValueError(
            f"Unknown system code: {system!r}. Valid codes: {sorted(SYSTEM_CODES)}"
        )
    return f"{system}_{normalize_rom_name(rom_filename)}"


def parse_title_id(title_id: str) -> tuple[str, str] | None:
    """Return ``(system, slug)`` if this is an emulator-format title_id.

    Returns ``None`` for native formats (16-char hex, PS/Vita product codes
    like ``SLUS-01234``, etc.) — those aren't slug-form and there's nothing
    to parse.
    """
    match = _EMULATOR_TITLE_ID_RE.match(title_id)
    if match and match.group(1) in SYSTEM_CODES:
        return (match.group(1), match.group(2))
    return None


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _strip_extension(filename: str) -> str:
    """Drop up to three trailing ``.ext`` tokens, e.g. ``foo.nds`` or
    ``foo.cue.gz``.  Each extension is bounded to 1–5 alphanumeric chars so
    that filenames containing dots in the stem (``"Final Fantasy VII.bin"``)
    still lose only the extension."""
    name = filename
    for _ in range(3):
        dot_idx = name.rfind(".")
        if dot_idx <= 0:
            break
        suffix = name[dot_idx + 1:]
        if 1 <= len(suffix) <= 5 and suffix.isalnum():
            name = name[:dot_idx]
        else:
            break
    return name
