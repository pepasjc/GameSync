"""Where a downloaded ROM has to land on a MiSTer, and under what name.

These are the rules the desktop installer already follows; they live here so
the on-device client cannot drift from them. Every one of them exists because
of how MiSTer cores actually behave:

* A CD core names its backup RAM after the *folder* holding the discs, so CD
  games install into a per-game subfolder and every disc of a game shares it.
  That is also what makes disc swapping work and what keeps a multi-disc game
  on one memory card.
* Core folder names drifted across MiSTer releases (``Genesis`` ->
  ``MegaDrive``, ``PCEngine`` -> ``TGFX16``), so an existing folder is reused
  and the modern name is only created when nothing is there.
* Cores search ``/media/usb0`` before ``/media/fat``. The moment a
  ``games/<Core>`` folder exists on USB, the core stops looking at the SD card
  entirely - including for ``boot.rom``. Creating one without seeding the BIOS
  silently breaks a working CD core, which is why :func:`bios_seed_sources`
  exists.
* CD cores read CHD natively, so a CHD is installed byte-for-byte with no
  server-side conversion.
* An MSU pack is a folder, and which core plays it depends on its audio
  container: MSU-1 and MD+ packs go under their own system's folder and are
  loaded as the ROM they contain, while an MSU-MD pack is a *MegaCD* core
  title - it lives under ``games/MegaCD/<Game>/`` with the cart renamed
  ``cart.rom`` beside its cue/bin, as the core documents.
"""

from __future__ import annotations

import posixpath
import re
from typing import List, Optional

from shared import msu
from shared.mister import (
    MISTER_CD_SYSTEMS,
    MISTER_GAMES_ROOTS,
    mister_system_folder_candidates,
)

__all__ = [
    "bios_seed_sources",
    "group_discs",
    "games_root",
    "install_target",
    "is_cd_system",
    "msu_pack_layout",
    "msu_pack_target",
    "needs_extract",
    "safe_file_name",
    "safe_folder_name",
    "strip_disc_tag",
    "system_games_dir",
]

_DISC_TAG_RE = re.compile(
    r"\s*[\(\[]\s*Dis[ck]\s*\d+(?:\s*of\s*\d+)?\s*[\)\]]", re.IGNORECASE
)
_UNSAFE_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

#: Extensions dropped when turning a filename into a folder name. A name ending
#: in a version tag ("... v1.1") must keep its final segment, so only
#: recognised ROM/disc extensions are stripped.
_FOLDER_NAME_STRIP_EXTS = frozenset({
    ".chd", ".cue", ".bin", ".iso", ".img", ".mdf", ".gdi", ".ccd", ".sub",
    ".zip", ".7z", ".rar", ".rvz", ".wbfs", ".cso", ".wua", ".wux",
    ".nes", ".sfc", ".smc", ".md", ".gen", ".gg", ".sms", ".pce", ".gba",
    ".gb", ".gbc", ".n64", ".z64", ".v64", ".nds", ".3ds", ".cci", ".cia",
})

#: The BIOS files a CD core expects beside its games.
BIOS_PATTERNS = ("boot.rom", "boot0.rom", "boot1.rom", "boot2.rom", "boot3.rom")


def strip_disc_tag(name: str) -> str:
    """Drop ``(Disc N)`` / ``(Disk N of M)`` so every disc shares one folder."""
    cleaned = _DISC_TAG_RE.sub("", str(name or ""))
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def safe_folder_name(value: str) -> str:
    """Sanitise a game name for use as a folder name."""
    text = str(value or "download").strip()
    lowered = text.lower()
    for suffix in _FOLDER_NAME_STRIP_EXTS:
        if lowered.endswith(suffix):
            text = text[: -len(suffix)]
            break
    text = _UNSAFE_RE.sub("_", text.strip())
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text or "download"


def safe_file_name(value: str) -> str:
    """Reduce a server-supplied filename to a safe basename, keeping the extension.

    Directory components are stripped so a crafted catalogue ``filename``
    (``../../etc/passwd``) cannot escape the resolved target folder.
    """
    base = str(value or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    base = _UNSAFE_RE.sub("_", base).strip(" .")
    return base or "download.rom"


def is_cd_system(system: str) -> bool:
    return (system or "").upper() in MISTER_CD_SYSTEMS


def games_root(rom_target: str = "sd") -> str:
    """``/media/fat/games`` or ``/media/usb0/games``."""
    key = (rom_target or "sd").lower()
    return MISTER_GAMES_ROOTS.get(key, MISTER_GAMES_ROOTS["sd"])


def system_games_dir(provider, system: str, rom_target: str = "sd") -> str:
    """The games folder for a system, preferring one that already exists.

    Returns ``""`` when the system has no known MiSTer folder, so the caller
    refuses the install rather than dumping files in the games root.
    """
    candidates = mister_system_folder_candidates(system)
    if not candidates:
        return ""
    root = games_root(rom_target)
    for candidate in candidates:
        path = posixpath.join(root, candidate)
        if provider.is_dir(path):
            return path
    return posixpath.join(root, candidates[0])


def install_target(provider, system: str, filename: str, display_name: str = "",
                   rom_target: str = "sd"):
    """Where one ROM file should be written.

    Returns ``(directory, filename)``, or ``("", "")`` when the system has no
    MiSTer folder. CD systems get a per-game subfolder with the disc tag
    stripped, so ``Game (Disc 1).chd`` and ``Game (Disc 2).chd`` land together
    and the core keeps one memory card for both.
    """
    system_dir = system_games_dir(provider, system, rom_target)
    if not system_dir:
        return "", ""

    safe_name = safe_file_name(filename)
    if not is_cd_system(system):
        return system_dir, safe_name

    base = display_name or filename
    folder = safe_folder_name(strip_disc_tag(base))
    return posixpath.join(system_dir, folder), safe_name


#: The MegaCD core insists on this name for the cartridge of an MSU-MD title.
MEGACD_CART_NAME = "cart.rom"


def msu_pack_layout(kind: str, system: str):
    """``(system whose folder the pack lives in, cart rename or None)``.

    Returns ``None`` when the kind is not one this MiSTer knows how to play.
    """
    kind = (kind or "").lower()
    system = (system or "").upper()
    if kind == msu.MSU1:
        return system or "SNES", None
    if kind == msu.MD_PLUS:
        return system or "MD", None
    if kind == msu.MSU_MD:
        return "SEGACD", MEGACD_CART_NAME
    return None


def msu_pack_target(provider, system: str, kind: str, display_name: str,
                    rom_target: str = "sd"):
    """Folder an MSU pack unpacks into, and the cart rename it needs.

    Returns ``(directory, cart_rename)``, or ``("", None)`` when the pack
    kind is unknown or its core has no folder here.  Every pack gets its own
    subfolder - the audio has to sit beside the ROM, and a MegaCD title's
    backup RAM is named after that folder.
    """
    layout = msu_pack_layout(kind, system)
    if layout is None:
        return "", None
    target_system, rename = layout
    system_dir = system_games_dir(provider, target_system, rom_target)
    if not system_dir:
        return "", None
    folder = safe_folder_name(display_name or "pack")
    return posixpath.join(system_dir, folder), rename


def needs_extract(system: str, filename: str) -> Optional[str]:
    """The ``?extract=`` format to request, or None to take the file as-is.

    MiSTer CD cores read CHD natively, so a CHD is never converted. Nothing
    else on a MiSTer wants a server-side conversion either.
    """
    return None


class DiscGroup:
    """One game in the catalogue, with every disc it is made of."""

    __slots__ = ("system", "name", "rows")

    def __init__(self, system, name, rows):
        self.system = system
        self.name = name
        self.rows = rows

    @property
    def size(self) -> int:
        return sum(int(row.get("size") or 0) for row in self.rows)

    @property
    def disc_count(self) -> int:
        return len(self.rows)

    def __repr__(self):  # pragma: no cover - debugging aid
        return "DiscGroup(%s, %r, %d discs)" % (self.system, self.name,
                                                self.disc_count)


def group_discs(rows) -> List["DiscGroup"]:
    """Fold a multi-disc game's catalogue rows into one entry.

    Every disc of a CD game installs into the same folder - that is what lets
    the core keep one memory card for the whole game - so presenting the discs
    separately invites installing half a game.

    Grouping is by that **folder**, not by the server's ``primary_rom_id``.
    The folder is the thing that decides which saves a disc shares, and the
    server's id can span more than one: it grouped a vanilla release, a fan
    translation of it and a bonus "Making of" disc under one id, and those
    install into three different folders. Keying on the folder keeps the
    invariant that one entry installs into exactly one place.
    """
    groups = []
    by_key = {}

    for row in rows or ():
        system = str(row.get("system") or "").upper()
        name = str(row.get("name") or row.get("filename") or "")
        if not is_cd_system(system):
            groups.append(DiscGroup(system, name, [row]))
            continue

        base = safe_folder_name(strip_disc_tag(name))
        key = (system, base)

        group = by_key.get(key)
        if group is None:
            group = DiscGroup(system, base or name, [])
            by_key[key] = group
            groups.append(group)
        group.rows.append(row)

    for group in groups:
        # Keep the discs in order, so disc 1 installs first.
        group.rows.sort(key=lambda row: (int(row.get("disc_index") or 0),
                                         str(row.get("filename") or "")))
    return groups


def bios_seed_sources(provider, system: str, rom_target: str) -> List[str]:
    """BIOS files to copy into a newly created USB games folder.

    Only relevant when installing to USB: once ``/media/usb0/games/<Core>``
    exists the core ignores the SD folder completely, so a CD core that used to
    find ``boot.rom`` on the SD card would stop booting. Returns the existing SD
    paths worth copying; the caller must not overwrite anything already there.
    """
    if (rom_target or "sd").lower() != "usb":
        return []
    candidates = mister_system_folder_candidates(system)
    if not candidates:
        return []

    sd_root = MISTER_GAMES_ROOTS["sd"]
    for candidate in candidates:
        sd_dir = posixpath.join(sd_root, candidate)
        if not provider.is_dir(sd_dir):
            continue
        present = set(provider.listdir(sd_dir))
        found = [posixpath.join(sd_dir, name)
                 for name in BIOS_PATTERNS if name in present]
        if found:
            return found
    return []
