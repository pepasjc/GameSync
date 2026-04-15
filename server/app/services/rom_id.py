"""ROM-based game ID utilities for emulator devices (MiSTer, RetroArch, Analogue Pocket, etc.).

Title ID format for ROM-based games: SYSTEM_slug
Examples:
    GBA_zelda_the_minish_cap
    SNES_super_mario_world
    MD_sonic_the_hedgehog
"""

import sys
from pathlib import Path
import re

# Make the repo root importable so 'shared' can be found.
_REPO_ROOT = str(Path(__file__).parent.parent.parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from shared.systems import SYSTEM_CODES  # noqa: E402

# Regex for emulator title_id format: SYSTEM_slug
# Slug may be lowercase (ROM-name style: GBA_zelda_the_minish_cap) or uppercase with
# hyphens (product-code style: SAT_GS-9188, SAT_T-14410G for Saroo Saturn saves).
_EMULATOR_TITLE_ID_RE = re.compile(
    r"^([A-Z0-9]{2,8})_([A-Za-z0-9][A-Za-z0-9_-]{0,99})$"
)

# Tags to strip from ROM filenames
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

    Region tags are moved to the end so that different regional releases of the
    same game get distinct title_ids while still grouping cleanly by game name.

    Examples:
        "Castlevania - Dracula X (USA).sfc"           -> "castlevania_dracula_x_usa"
        "Super Mario World (USA).sfc"                 -> "super_mario_world_usa"
        "Sonic the Hedgehog (USA, Europe).md"         -> "sonic_the_hedgehog_usa_europe"
        "Final Fantasy VII (Rev 1) (USA).bin"         -> "final_fantasy_vii_usa"
        "Legend of Zelda, The - Minish Cap (USA).gba" -> "legend_of_zelda_the_minish_cap_usa"
        "Homebrew Game.sfc"                           -> "homebrew_game"
    """
    name = filename
    for _ in range(3):
        dot_idx = name.rfind(".")
        if dot_idx <= 0:
            break
        suffix = name[dot_idx + 1 :]
        if 1 <= len(suffix) <= 5 and suffix.isalnum():
            name = name[:dot_idx]
        else:
            break

    # Extract region before stripping all parenthetical tags
    region_match = _REGION_RE.search(name)
    region_parts = ""
    if region_match:
        region_text = region_match.group(0).strip(" ()")
        region_parts = "_".join(region_text.lower().replace(",", " ").split())

    # Strip revision, disc, and all remaining parentheticals (including region)
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
    """Return canonical title_id e.g. GBA_legend_of_zelda_the_minish_cap_usa."""
    system = system.upper().strip()
    if system not in SYSTEM_CODES:
        raise ValueError(
            f"Unknown system code: {system!r}. Valid codes: {sorted(SYSTEM_CODES)}"
        )
    return f"{system}_{normalize_rom_name(rom_filename)}"


def parse_title_id(title_id: str) -> tuple[str, str] | None:
    """Return (system, slug) if this is an emulator-format title_id, else None."""
    m = _EMULATOR_TITLE_ID_RE.match(title_id)
    if m and m.group(1) in SYSTEM_CODES:
        return (m.group(1), m.group(2))
    return None
