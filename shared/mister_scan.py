"""Walking a MiSTer's save and games directories, over any transport.

The desktop client reaches a MiSTer over SFTP and the on-device client reads
the same files locally. The *rules* are identical in both cases - which folders
map to which system, which extensions count, where a downloaded save has to be
written so the core finds it - so they live here once, behind a tiny file
provider, rather than being written twice and drifting.

A provider needs four methods: ``listdir``, ``is_dir``, ``stat`` and ``read``.
:class:`LocalProvider` is the on-device one; the desktop supplies an SFTP-backed
equivalent.
"""

from __future__ import annotations

import os
import posixpath
import re
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

from shared.mister import (
    MISTER_CD_SYSTEMS,
    MISTER_FOLDER_ROM_EXTENSIONS,
    MISTER_FOLDER_TO_SYSTEM,
    MISTER_GAMES_ROOTS,
    MISTER_SHARED_SAVE_FOLDERS,
    mister_system_folder_candidates,
    mister_system_save_folder_candidates,
)
from shared.rom_id import make_title_id, normalize_rom_name
from shared.systems import CD_ALL_EXTENSIONS, ROM_EXTENSIONS, SAVE_EXTENSIONS

MISTER_SAVES_DIR = "/media/fat/saves"

#: MiSTer cores always write ``.sav`` regardless of what other emulators use
#: for the system, so a downloaded save must land with this extension or the
#: core will never see it.
MISTER_SAVE_EXT = ".sav"

_ALL_ROM_EXTENSIONS = tuple(sorted(ROM_EXTENSIONS))
_CD_LIKE_EXTENSIONS = tuple(sorted(CD_ALL_EXTENSIONS | {".gdi", ".cdi"}))

__all__ = [
    "LocalProvider",
    "InstalledRom",
    "filter_game_entries",
    "is_game_entry",
    "list_installed_games",
    "rom_extensions_for_folder",
    "system_for_save",
    "MISTER_SAVES_DIR",
    "MiSTerSaveFile",
    "build_save_path",
    "find_installed_rom_stem",
    "installed_game_save_paths",
    "save_folder_for_system",
    "scan_saves",
]


class LocalProvider:
    """Reads the MiSTer's own filesystem, for the on-device client."""

    def listdir(self, path: str) -> List[str]:
        try:
            return os.listdir(path)
        except OSError:
            return []

    def is_dir(self, path: str) -> bool:
        return os.path.isdir(path)

    def stat(self, path: str):
        """Return ``(size, mtime)``, or ``(0, 0.0)`` when unavailable."""
        try:
            info = os.stat(path)
            return int(info.st_size), float(info.st_mtime)
        except OSError:
            return 0, 0.0

    def read(self, path: str) -> bytes:
        with open(path, "rb") as handle:
            return handle.read()


class MiSTerSaveFile:
    """One save file found on the device, before its identity is resolved."""

    __slots__ = ("system", "folder", "filename", "path", "title_id", "size",
                 "mtime")

    def __init__(self, system, folder, filename, path, title_id, size, mtime):
        self.system = system
        self.folder = folder
        self.filename = filename
        self.path = path
        self.title_id = title_id
        self.size = size
        self.mtime = mtime

    @property
    def stem(self) -> str:
        return posixpath.splitext(self.filename)[0]

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "MiSTerSaveFile(%s, %s)" % (self.system, self.filename)


# --------------------------------------------------------------- game filter
#
# A MiSTer games folder is not only games. Every CD core keeps its BIOS beside
# them (``boot.rom``, ``cd_bios.rom``), several cores keep firmware or a
# system disk there, and update scripts drop caches and support folders in.
# ``.rom`` and ``.bin`` are legitimate ROM extensions for some cores, so the
# extension cannot decide - the *name* has to. Without this the Installed tab
# lists a dozen systems that hold nothing but a BIOS, which buries the systems
# that really do have games.

#: Exact filenames that are never a game.
_NON_GAME_FILES = frozenset({
    "000-lo.lo",           # NeoGeo BIOS sprite ROM
    "sfix.sfix",           # NeoGeo BIOS fix layer
    "neogeo.zip",          # NeoGeo BIOS set
    "darksoft.zip",
    "sbi.zip",             # PSX libcrypt patches
    "alt_roms.zip",
    "sdcard.zip",
    "sid_data.bin",
    "kanji.rom",
    "disk.dat",            # X68000 / SharpMZ system disk
    "sram.dat",
    "cheats.zip",
    "menu.rom",
    "mister.ini",
})

#: Stems (extension dropped) that are never a game, as a regex. Covers the
#: numbered variants a core uses for regional BIOS revisions.
_NON_GAME_STEM_RE = re.compile(
    r"(?i)^("
    r"(?:[\w-]+[-_])?boot\d*"   # boot.rom, boot0.rom, mister-boot.nes, dc_boot
    r"|(?:[\w-]+[-_])?flash\d*"  # dc_flash.bin
    r"|cd_?bios\d*"       # cd_bios.rom (MegaCD, TGFX16-CD)
    r"|syscard\d*"        # PC Engine CD system cards
    r"|kick|kick\d.*|kickstart.*"  # Amiga Kickstart ("Kick Off 2" is a game)
    r"|tos\d*"            # Atari ST TOS
    r"|x68000.*"         # X68000 IPL/CGROM
    r"|uni-?bios.*"        # NeoGeo UniBIOS
    r"|neocd.*"          # NeoGeo CD BIOS
    r"|top-sp1"
    r"|firmware.*"
    r"|empty.*|blank.*"     # blank disk/tape images shipped with a core
    r")$"
)

#: A stem calling itself a BIOS, when the extension is one a BIOS uses. The
#: extension matters: ``Bios Fighter.md`` is a Mega Drive game.
_BIOS_NAME_RE = re.compile(r"(?i)(?:^|[ _.\-\[(])bios(?:$|[ _.\-\])])")
_BIOS_EXTENSIONS = (".rom", ".bin", ".dat", ".img", ".lo", ".zip")

#: Support folders cores and scripts keep next to the games.
_NON_GAME_FOLDERS = frozenset({
    "artwork", "cheats", "config", "docs", "filters", "gamma", "hbmame",
    "mame", "mra", "music", "palette", "palettes", "presets", "samples",
    "saves", "savestates", "screenshots", "scripts", "shadow_masks",
    "system", "bios", "overlays", "drv", "old", "media", "shaders", "misc",
    "mister",
})

#: Files that are one track of a multi-track disc image, not a disc.
_TRACK_FILE_RE = re.compile(r"(?i)(?:^|[ _\-(\[])track\s*\d+")

#: A sheet names the disc; the data/audio files it points at are not games.
_SHEET_EXTENSIONS = (".cue", ".gdi", ".ccd", ".m3u", ".toc")

#: Dropped when a sheet of the same stem sits beside them.
_SHEET_COMPANION_EXTENSIONS = (".bin", ".img", ".iso", ".raw", ".sub",
                               ".wav", ".ogg", ".mp3", ".flac")


def is_game_entry(name: str, is_dir: bool = False) -> bool:
    """Is this games-folder entry a game, rather than a BIOS or support file?

    Extension filtering happens in the caller; this only rejects entries that
    look like a ROM but are not one.
    """
    entry = str(name or "").strip()
    if not entry or entry.startswith("."):
        return False
    if is_dir:
        return entry.lower() not in _NON_GAME_FOLDERS
    lowered = entry.lower()
    if lowered in _NON_GAME_FILES:
        return False
    if _TRACK_FILE_RE.search(posixpath.splitext(entry)[0]):
        return False
    stem, extension = posixpath.splitext(lowered)
    if extension in _BIOS_EXTENSIONS and _BIOS_NAME_RE.search(stem):
        return False
    return not _NON_GAME_STEM_RE.match(stem)


def filter_game_entries(
    entries: Iterable[Tuple[str, bool]],
) -> List[Tuple[str, bool]]:
    """Keep the real games out of one games folder's listing.

    ``entries`` is ``(name, is_dir)`` pairs. On top of :func:`is_game_entry`,
    a disc image referenced by a sheet beside it is dropped, so a game shipped
    as ``Game.cue`` + ``Game.bin`` is one row and not two.
    """
    kept = [(name, is_dir) for name, is_dir in entries
            if is_game_entry(name, is_dir)]
    sheet_stems = {
        posixpath.splitext(name)[0].lower()
        for name, is_dir in kept
        if not is_dir and name.lower().endswith(_SHEET_EXTENSIONS)
    }
    if not sheet_stems:
        return kept
    result = []
    for name, is_dir in kept:
        lowered = name.lower()
        if (not is_dir
                and lowered.endswith(_SHEET_COMPANION_EXTENSIONS)
                and posixpath.splitext(lowered)[0] in sheet_stems):
            continue
        result.append((name, is_dir))
    return result


def rom_extensions_for_folder(folder: str) -> Tuple[str, ...]:
    """Extensions the core owning ``folder`` will load, best known.

    Falls back to the global ROM set for a folder we have no list for, so an
    unknown or new core still shows its games.
    """
    known = MISTER_FOLDER_ROM_EXTENSIONS.get(folder)
    if known is None:
        return _ALL_ROM_EXTENSIONS
    return tuple(known)


class InstalledRom:
    """One game found in a MiSTer games folder."""

    __slots__ = ("name", "path", "is_dir", "is_pack")

    def __init__(self, name: str, path: str, is_dir: bool, is_pack: bool = False):
        self.name = name
        self.path = path
        self.is_dir = is_dir
        #: An MSU pack: a cart with its streamed audio beside it (``.msu`` /
        #: ``.cue`` in the folder, or a ``cart.rom`` MegaCD title).  Kept apart
        #: from the plain ROM of the same game, which it is not a copy of.
        self.is_pack = is_pack

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "InstalledRom(%r)" % self.name


def list_installed_games(provider, folder_path: str, folder: str,
                         system: Optional[str] = None) -> List[InstalledRom]:
    """The games in one system's folder - BIOS and support files excluded.

    Three rules do the work, and all three come from how MiSTer lays a card
    out rather than from guesswork:

    * **The core's own extensions decide.** MiSTer ships every core's folder
      whether or not you own games for it, each holding a BIOS with a generic
      extension (``boot.rom``, ``kanji.rom``). Matching the global ROM set made
      every one of those folders look like a system with a game in it.
    * **A subfolder is a game only for a CD core.** CD cores launch a folder of
      discs; cartridge cores never do, so a subfolder there is the user's own
      shelf (``NEOGEO/Fighting``) or the core's (``VECTREX/Overlays``). Those
      are descended into instead, one level, so the games inside still show.
    * **A CD game folder must actually hold a disc.** ``MegaCD/USA`` holds one
      regional BIOS, not a game.
    """
    if system is None:
        system = MISTER_FOLDER_TO_SYSTEM.get(folder, folder.upper())
    extensions = rom_extensions_for_folder(folder)
    is_cd = system in MISTER_CD_SYSTEMS or bool(
        set(extensions) & set(_CD_LIKE_EXTENSIONS))

    entries = [(name, provider.is_dir(posixpath.join(folder_path, name)))
               for name in sorted(provider.listdir(folder_path))]

    found: List[InstalledRom] = []
    for name, is_dir in filter_game_entries(entries):
        path = posixpath.join(folder_path, name)
        if not is_dir:
            if extensions and name.lower().endswith(extensions):
                found.append(InstalledRom(posixpath.splitext(name)[0], path,
                                          False))
            continue
        if is_cd:
            if _holds_a_game(provider, path, extensions):
                children = provider.listdir(path)
                found.append(InstalledRom(
                    name, path, True,
                    is_pack=any(c.lower() == "cart.rom" for c in children)))
            continue
        # A shelf folder on a cartridge core: the games are one level down.
        # A folder that also holds ``.msu`` or ``.cue`` sidecars is an MSU
        # pack rather than a shelf, and its cart is flagged as such.
        children = sorted(provider.listdir(path))
        pack = any(c.lower().endswith((".msu", ".cue")) for c in children)
        for child, child_is_dir in filter_game_entries(
                [(entry, provider.is_dir(posixpath.join(path, entry)))
                 for entry in children]):
            if child_is_dir or not child.lower().endswith(extensions):
                continue
            found.append(InstalledRom(posixpath.splitext(child)[0],
                                      posixpath.join(path, child), False,
                                      is_pack=pack))
    return found


def _holds_a_game(provider, path: str, extensions: Sequence[str]) -> bool:
    """Does this folder contain a disc image, at its top level or one below?"""
    wanted = tuple(extensions) or _ALL_ROM_EXTENSIONS
    for name, is_dir in filter_game_entries(
            [(entry, provider.is_dir(posixpath.join(path, entry)))
             for entry in provider.listdir(path)]):
        if not is_dir:
            if name.lower().endswith(wanted):
                return True
        elif any(child.lower().endswith(wanted)
                 for child in provider.listdir(posixpath.join(path, name))):
            return True
    return False


def scan_saves(provider, saves_root: str = MISTER_SAVES_DIR,
               systems: Optional[set] = None) -> List[MiSTerSaveFile]:
    """Every recognised save under ``saves_root``.

    Folders that map to no known system are skipped rather than guessed at, and
    the full save-extension set is honoured - the legacy shell script only
    looked at three of the twelve.
    """
    found: List[MiSTerSaveFile] = []
    extensions = tuple(SAVE_EXTENSIONS)

    for folder in sorted(provider.listdir(saves_root)):
        system = MISTER_FOLDER_TO_SYSTEM.get(folder)
        if not system:
            continue
        if systems and system not in systems:
            continue
        folder_path = posixpath.join(saves_root, folder)
        if not provider.is_dir(folder_path):
            continue

        for filename in sorted(provider.listdir(folder_path)):
            if not filename.lower().endswith(extensions):
                continue
            path = posixpath.join(folder_path, filename)
            stem = posixpath.splitext(filename)[0]
            # One folder can serve two systems; the installed game decides.
            resolved = system_for_save(provider, folder, system, stem)
            if systems and resolved not in systems and system not in systems:
                continue
            try:
                title_id = make_title_id(resolved, filename)
            except Exception:
                continue
            size, mtime = provider.stat(path)
            found.append(MiSTerSaveFile(resolved, folder, filename, path,
                                        title_id, size, mtime))
    return _prefer_canonical_folder(provider, found, saves_root)


def _prefer_canonical_folder(provider, found, saves_root):
    """Drop a duplicate of the same save sitting in a non-canonical folder.

    A save can end up in two places - ``saves/TGFX16-CD`` was a plausible
    guess for PC Engine CD before it turned out the core writes everything to
    ``saves/TGFX16`` - and the two would then show as two rows fighting over
    one server slot. The core only ever reads one of them, so that is the one
    to keep.
    """
    by_identity = {}
    for item in found:
        by_identity.setdefault((item.system, item.title_id), []).append(item)

    kept = []
    for (system, _title_id), items in by_identity.items():
        if len(items) == 1:
            kept.extend(items)
            continue
        canonical = save_folder_for_system(provider, system, saves_root)
        preferred = [item for item in items if item.folder == canonical]
        kept.extend(preferred or items[:1])

    # Preserve the original folder/filename ordering.
    order = {id(item): index for index, item in enumerate(found)}
    return sorted(kept, key=lambda item: order[id(item)])


def system_for_save(provider, folder: str, system: str, stem: str,
                    games_roots=None) -> str:
    """Which system a save belongs to, when one folder serves two.

    The TurboGrafx-16 core writes HuCard and CD saves into the same
    ``saves/TGFX16``, so the folder no longer identifies the system. The games
    folders are still separate, so the installed game decides: a save whose
    name matches a game in ``games/TGFX16-CD`` is a CD save.

    Falls back to the folder's usual system when nothing matches, which is the
    old behaviour.
    """
    shared = MISTER_SHARED_SAVE_FOLDERS.get(folder)
    if not shared:
        return system
    if games_roots is None:
        games_roots = [MISTER_GAMES_ROOTS["usb"], MISTER_GAMES_ROOTS["sd"]]

    wanted = normalize_rom_name(stem)
    if not wanted or wanted == "unknown":
        return system

    for candidate in shared:
        for root in games_roots:
            for folder_name in mister_system_folder_candidates(candidate):
                directory = posixpath.join(root, folder_name)
                if not provider.is_dir(directory):
                    continue
                for entry in provider.listdir(directory):
                    name = entry
                    if not provider.is_dir(posixpath.join(directory, entry)):
                        name = posixpath.splitext(entry)[0]
                    if normalize_rom_name(name) == wanted:
                        return candidate
    return system


def save_folder_for_system(provider, system: str,
                           saves_root: str = MISTER_SAVES_DIR) -> str:
    """The save folder name a core uses, preferring one that already exists.

    Cores were renamed over the years (``Genesis`` -> ``MegaDrive``,
    ``PCEngine`` -> ``TGFX16``), so an existing folder wins over the modern
    name; otherwise the modern name is created. Some cores also write their
    saves under another system's folder entirely - see
    ``MISTER_SYSTEM_SAVE_FOLDERS``.
    """
    candidates = mister_system_save_folder_candidates(system)
    if not candidates:
        return ""
    for candidate in candidates:
        if provider.is_dir(posixpath.join(saves_root, candidate)):
            return candidate
    return candidates[0]


def find_installed_rom_stem(
    provider,
    system: str,
    title_id: str,
    games_roots: Optional[List[str]] = None,
    catalog_lookup: Optional[Callable[[str, str], Optional[str]]] = None,
) -> Optional[str]:
    """Name of the installed game a save belongs to, or None.

    A MiSTer core names its save after the game file or folder it launched, so
    a downloaded save has to reuse that exact name. Two ordering rules matter:

    * USB is searched before the SD card, because cores look at
      ``/media/usb0`` first and a game there shadows the SD copy.
    * Directories are matched before files, because CD cores name the backup
      RAM after the *folder* holding the discs. A folder name is returned whole
      rather than split into stem and extension, since game folders routinely
      contain dots (``... v1.021+hotfix``).

    This mirrors ``desktop/sync_engine._mister_matching_rom_stem`` deliberately:
    the two clients must agree on which installed game a save belongs to.
    """
    if games_roots is None:
        games_roots = [MISTER_GAMES_ROOTS["usb"], MISTER_GAMES_ROOTS["sd"]]

    target = (title_id or "").upper()
    if not target:
        return None

    for root in games_roots:
        for folder in mister_system_folder_candidates(system):
            system_dir = posixpath.join(root, folder)
            if not provider.is_dir(system_dir):
                continue
            entries = sorted(provider.listdir(system_dir))
            directories, files = [], []
            for entry in entries:
                if provider.is_dir(posixpath.join(system_dir, entry)):
                    directories.append(entry)
                else:
                    files.append(entry)

            for entry in directories + files:
                is_dir = entry in directories
                stem = entry if is_dir else posixpath.splitext(entry)[0]
                if _matches(system, stem, entry, target, catalog_lookup):
                    return stem
    return None


def _matches(system, stem, entry, target, catalog_lookup) -> bool:
    try:
        if make_title_id(system, entry).upper() == target:
            return True
    except Exception:
        pass
    if catalog_lookup is not None:
        try:
            found = catalog_lookup(system, stem)
        except Exception:
            found = None
        if found and found.upper() == target:
            return True
    slug = normalize_rom_name(stem)
    return bool(slug) and slug != "unknown" and slug.upper() in target


def build_save_path(
    provider,
    system: str,
    title_id: str,
    game_name: str = "",
    saves_root: str = MISTER_SAVES_DIR,
    games_roots: Optional[List[str]] = None,
    catalog_lookup: Optional[Callable[[str, str], Optional[str]]] = None,
) -> str:
    """Where a save downloaded from the server has to be written.

    The file name has to be the installed game's name, because that is what the
    core will look for. Falling back to the server's name is a guess that only
    works when the two happen to agree.
    """
    folder = save_folder_for_system(provider, system, saves_root)
    if not folder:
        return ""

    stem = find_installed_rom_stem(provider, system, title_id, games_roots,
                                   catalog_lookup)
    if not stem:
        stem = _sanitize(game_name) or _sanitize(title_id)
    if not stem:
        return ""
    return posixpath.join(saves_root, folder, stem + MISTER_SAVE_EXT)


def installed_game_save_paths(
    provider,
    system: str,
    stem: str,
    saves_root: str = MISTER_SAVES_DIR,
) -> List[str]:
    """Save files a core has written for the installed game ``stem``.

    The inverse of ``build_save_path``: a core names its save after the game
    file's stem or the game folder, so the save for an installed game is
    ``saves/<folder>/<stem>.sav`` in whichever save folder the system has
    used over the years (``Genesis`` and ``MegaDrive`` may both exist).
    Only files that exist are returned, so callers can offer to remove
    them alongside the game - leaving them behind is how two variants of
    one game end up fighting over a server slot after one is uninstalled.
    """
    if not stem:
        return []
    found = []
    for folder in mister_system_save_folder_candidates(system):
        directory = posixpath.join(saves_root, folder)
        name = stem + MISTER_SAVE_EXT
        if name in provider.listdir(directory):
            found.append(posixpath.join(directory, name))
    return found


_UNSAFE = set('<>:"/\\|?*')


def _sanitize(name: str) -> str:
    cleaned = "".join(" " if ch in _UNSAFE else ch for ch in str(name or ""))
    return " ".join(cleaned.split()).strip(". ")
