"""Generic server-only placeholder entries.

The scanner-level modules (``duckstation``, ``pcsx2``, ``retroarch``, ...)
emit one entry per ROM/save found on the local disk.  Saves that only exist
on the server — because the user hasn't yet installed the ROM for that game
on this device — would therefore be invisible in the UI.

``rpcs3``, ``dolphin`` and ``citra`` already solve this for PS3, GameCube
and 3DS by producing per-system placeholders with a real ``save_path``.
This module fills in the gap for every other system: it walks the server's
save list, skips anything already covered by a local scan, and emits a
``GameEntry`` with ``status=SERVER_ONLY`` *plus a predicted save_path* so
the Save Info dialog can offer a direct Download action — no ROM required.

The predicted path matches what the system-specific scanner would produce
once a ROM is installed (DuckStation's ``memcards/<label>_1.mcd``, PPSSPP's
``SAVEDATA/<title_id>/`` slot dir, RetroArch's ``saves/<stem>.srm``, ...).
If the user later downloads the ROM and rescans, the scanner either
matches the same predicted path (save is already in place) or produces an
entry that merges with this one via the normal title_id pipeline.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterable, Optional

_REPO_ROOT = str(Path(__file__).parent.parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from shared.systems import normalize_system_code  # noqa: E402

from .base import find_paths  # noqa: E402
from .models import GameEntry, SyncStatus


# Systems that have their own dedicated builder.  We skip them here so we
# don't clobber the save_path placeholders those builders already compute.
_HANDLED_BY_DEDICATED_BUILDER = {"PS3", "GC", "3DS"}


# Best-effort emulator label per system, used only for display in the Save
# Info dialog.  Unknown systems simply get "Server" so the column stays
# populated.
_SYSTEM_EMULATOR: dict[str, str] = {
    "PS1": "DuckStation",
    "PS2": "PCSX2",
    "PSP": "PPSSPP",
    "VITA": "Vita3K",
    "NDS": "melonDS",
    "3DS": "Citra",
    "SAT": "RetroArch",
    "DC": "RetroArch",
    "MD": "RetroArch",
    "SEGACD": "RetroArch",
    "SMS": "RetroArch",
    "GG": "RetroArch",
    "32X": "RetroArch",
    "GBA": "RetroArch",
    "GB": "RetroArch",
    "GBC": "RetroArch",
    "NES": "RetroArch",
    "SNES": "RetroArch",
    "N64": "RetroArch",
    "WII": "Dolphin",
}

# Systems whose title IDs are bare product codes (no "SYS_" prefix) but are
# still recognisable by their well-known starting letters.  Used only as a
# last-ditch fallback when the server response carries no platform metadata
# — ``_resolve_system`` always prefers ``console_type`` / ``platform`` first,
# which disambiguates prefixes shared between PS1 and PS2.
_CODE_PREFIX_TO_SYSTEM = {
    "SLUS": "PS1",
    "SLES": "PS1",
    "SCUS": "PS1",
    "SCES": "PS1",
    "SLPS": "PS1",
    "SLPM": "PS1",
    "SCPS": "PS1",
    "SCPM": "PS1",
    "NPJH": "PSP",
    "UCUS": "PSP",
    "ULUS": "PSP",
    "UCES": "PSP",
    "ULJS": "PSP",
    "NPUG": "PSP",
    "NPJG": "PSP",
    "BLUS": "PS3",
    "BLES": "PS3",
    "BCUS": "PS3",
    "BCES": "PS3",
}


# ──────────────────────────────────────────────────────────────────────────────
# Save-path helpers
# ──────────────────────────────────────────────────────────────────────────────

# RetroArch systems that write one ``.srm`` per ROM at the top of the saves
# dir (no core-specific subdirectory needed for a first download).  Single
# file so entries stay ``is_multi_file=False``.
_RETROARCH_SRM_SYSTEMS = {
    "GBA", "GB", "GBC", "NES", "SNES", "N64",
    "MD", "SEGACD", "SMS", "GG", "32X",
    "DC", "PCE", "LYNX", "NGPC", "WSWAN", "NEOGEO",
}

# Strip disc / dump-serial tags from display names before using them as a
# filesystem stem, matching DuckStation's card-label cleaning so a server
# save named "Final Fantasy VII (USA) (Disc 1)" lands at the same path a
# local scan of the CHD would predict.
_DISC_TAG_RE = re.compile(
    r"\s*[\(\[]\s*(?:disc|cd|side)\s*\d+(?:\s*of\s*\d+)?\s*[\)\]]",
    re.IGNORECASE,
)
_SERIAL_TAG_RE = re.compile(
    r"\s*[\(\[][A-Z]{4}[-_ ]?\d{5}.*?[\)\]]",
    re.IGNORECASE,
)
# Strip anything that would be invalid in a Windows/POSIX filename so the
# predicted path is always writable.
_UNSAFE_CHARS_RE = re.compile(r'[\\/:*?"<>|]')


def _clean_stem(label: str) -> str:
    """Return a filesystem-safe stem derived from a game's display name."""
    result = _DISC_TAG_RE.sub("", label)
    result = _SERIAL_TAG_RE.sub("", result)
    result = _UNSAFE_CHARS_RE.sub("", result)
    result = re.sub(r"\s+", " ", result).strip()
    return result or label.strip() or "game"


def _ps1_memcards_dir(emulation_path: Path) -> Path:
    emu_saves = emulation_path / "saves" / "duckstation"
    emudeck_saves = Path.home() / "Emulation/saves/duckstation"
    flatpak_saves = Path.home() / ".var/app/org.duckstation.DuckStation/data/duckstation"
    return find_paths(
        emu_saves / "memcards",
        emu_saves / "saves",
        emudeck_saves / "memcards",
        emudeck_saves / "saves",
        flatpak_saves / "memcards",
    ) or (emu_saves / "memcards")


def _ps2_memcards_dir(emulation_path: Path) -> Path:
    emu_saves = emulation_path / "saves" / "pcsx2"
    emudeck_saves = Path.home() / "Emulation/saves/pcsx2"
    flatpak_saves = Path.home() / ".var/app/net.pcsx2.PCSX2/data/PCSX2"
    return find_paths(
        emu_saves / "memcards",
        emu_saves / "saves",
        emudeck_saves / "memcards",
        emudeck_saves / "saves",
        flatpak_saves / "memcards",
    ) or (emu_saves / "memcards")


def _psp_savedata_root(emulation_path: Path) -> Path:
    emu_saves = emulation_path / "saves" / "ppsspp"
    emudeck_saves = Path.home() / "Emulation/saves/ppsspp"
    flatpak_saves = Path.home() / ".var/app/org.ppsspp.PPSSPP/config/ppsspp/PSP"
    return find_paths(
        emu_saves / "saves" / "SAVEDATA",
        emu_saves / "SAVEDATA",
        emudeck_saves / "SAVEDATA",
        flatpak_saves / "SAVEDATA",
    ) or (emu_saves / "SAVEDATA")


def _vita_savedata_root(emulation_path: Path) -> Path:
    # Vita3K keeps per-title savedata under ux0/user/00/savedata/<title_id>.
    emu_saves = emulation_path / "saves" / "vita3k"
    emudeck_saves = Path.home() / "Emulation/saves/vita3k"
    flatpak_saves = Path.home() / ".var/app/org.vita3k.Vita3K/data/Vita3K/Vita3K"
    candidate_roots = [
        emu_saves / "ux0" / "user" / "00" / "savedata",
        emudeck_saves / "ux0" / "user" / "00" / "savedata",
        flatpak_saves / "ux0" / "user" / "00" / "savedata",
    ]
    return find_paths(*candidate_roots) or candidate_roots[0]


def _nds_roms_dir(emulation_path: Path) -> Path:
    # melonDS writes <rom>.sav next to the ROM.  The predicted path uses the
    # canonical roms/nds/ location so a Download-ROM follow-up lands the save
    # in the same folder.
    return emulation_path / "roms" / "nds"


def _retroarch_saves_dir(emulation_path: Path) -> Path:
    emu_saves = emulation_path / "saves" / "retroarch" / "saves"
    emudeck_saves = Path.home() / "Emulation/saves/retroarch/saves"
    flatpak_saves = Path.home() / ".var/app/org.libretro.RetroArch/config/retroarch/saves"
    return find_paths(
        emu_saves,
        emudeck_saves,
        flatpak_saves,
    ) or emu_saves


def _default_save_path(
    system: str,
    title_id: str,
    display_name: str,
    emulation_path: Path,
) -> tuple[Optional[Path], bool, bool]:
    """Predict a save_path for a server-only placeholder.

    Returns ``(path, is_multi_file, is_psp_slot)``.  ``path=None`` means
    this system isn't supported by the generic builder — the UI will keep
    the Download button hidden and the user must install the ROM first.
    """
    stem = _clean_stem(display_name)

    if system == "PS1":
        # DuckStation per-game card: "<clean label>_1.mcd".
        return (_ps1_memcards_dir(emulation_path) / f"{stem}_1.mcd", False, False)

    if system == "PS2":
        # PCSX2 per-game VMP/PS2 card lives alongside the shared memcards.
        return (_ps2_memcards_dir(emulation_path) / f"{stem}.ps2", False, False)

    if system == "PSP":
        # Each PSP title owns a SAVEDATA slot directory.  The server's
        # title_id is the slot name (e.g. ULUS10567DATA), so use it
        # verbatim — matches what PPSSPP's scanner yields.
        return (_psp_savedata_root(emulation_path) / title_id, False, True)

    if system == "VITA":
        return (_vita_savedata_root(emulation_path) / title_id, True, False)

    if system == "NDS":
        return (_nds_roms_dir(emulation_path) / f"{stem}.sav", False, False)

    if system == "SAT":
        # Default to libretro/yabause-style .srm at the saves root.  Users
        # on Beetle Saturn / Yabasanshiro can rescan after installing the
        # ROM to pick up their emulator-specific save layout.
        return (_retroarch_saves_dir(emulation_path) / f"{stem}.srm", False, False)

    if system in _RETROARCH_SRM_SYSTEMS:
        return (_retroarch_saves_dir(emulation_path) / f"{stem}.srm", False, False)

    # Unknown system — leave save_path unset so the UI hides Download.
    return (None, False, False)


def _system_from_title_id(title_id: str) -> str:
    """Best-effort mapping from a canonical title_id to a system code."""
    upper = title_id.upper()
    # SYS_slug style (e.g. "GBA_pokemon_emerald_usa")
    if "_" in upper:
        sys_prefix = upper.split("_", 1)[0]
        if 2 <= len(sys_prefix) <= 8 and sys_prefix.isalnum():
            return sys_prefix
    # Sony bare-code style (SLUS01234, NPJH50001, BLUS30464-SAVE)
    for prefix, sys in _CODE_PREFIX_TO_SYSTEM.items():
        if upper.startswith(prefix):
            return sys
    return ""


def _resolve_system(title_id: str, info: dict) -> str:
    """Pick a system for an arbitrary server save record.

    Server metadata may carry non-canonical labels for the same console
    (``"Genesis"`` vs ``"MD"``, ``"PSX"`` vs ``"PS1"``).  Normalising here
    keeps the system filter from showing duplicate entries for one console.
    """
    for key in ("console_type", "system", "platform"):
        value = info.get(key)
        if value:
            normalized = normalize_system_code(value)
            if normalized:
                return normalized
    return normalize_system_code(_system_from_title_id(title_id))


def build_server_only_entries(
    server_saves: dict[str, dict],
    seen_ids: Iterable[str],
    emulation_path: Path,
) -> list[GameEntry]:
    """
    Emit a placeholder ``GameEntry`` for each server save that is not
    already represented by a local scan and is not handled by a
    system-specific builder (PS3 / GameCube / 3DS).  Each placeholder gets
    a predicted ``save_path`` for supported systems so the user can
    download the save straight away; systems we can't predict for leave
    ``save_path=None`` and still surface so the user can Download ROM.
    """
    seen_set = set(seen_ids)
    results: list[GameEntry] = []

    for title_id, info in server_saves.items():
        if title_id in seen_set:
            continue
        system = _resolve_system(title_id, info)
        if not system:
            continue
        if system in _HANDLED_BY_DEDICATED_BUILDER:
            # Those builders have already run and decided whether to emit a
            # placeholder with a real save_path.  Don't double up.
            continue

        emulator = _SYSTEM_EMULATOR.get(system, "Server")
        display_name = (
            info.get("name") or info.get("game_name") or title_id
        )
        save_path, is_multi_file, is_psp_slot = _default_save_path(
            system, title_id, display_name, emulation_path
        )
        results.append(
            GameEntry(
                title_id=title_id,
                display_name=display_name,
                system=system,
                emulator=emulator,
                status=SyncStatus.SERVER_ONLY,
                server_hash=info.get("save_hash"),
                server_title_id=info.get("title_id") or title_id,
                server_timestamp=info.get("client_timestamp"),
                server_size=info.get("save_size"),
                save_path=save_path,
                is_multi_file=is_multi_file,
                is_psp_slot=is_psp_slot,
            )
        )
    return results
