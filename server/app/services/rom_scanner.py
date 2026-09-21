"""ROM catalog scanner service.

On startup the catalog is loaded from the SQLite cache (roms.db) for instant
availability. A filesystem scan is only performed when:

- No cached data exists (first run)
- An explicit rescan is triggered via the API
- The periodic background job fires

Folder layout (EmuDeck / RetroDeck standard):
    <rom_dir>/gba/<rom files>
    <rom_dir>/snes/<rom files>
    <rom_dir>/psx/games/<rom files>

The folder name is mapped to a system code and filenames are matched against
the DAT slug index. CRC32 is skipped by default (ROMs are pre-normalized).

PS3 bundles
-----------
The PS3 system has a special layout because PSN packages typically ship as
multi-file sets (e.g. one ``.pkg`` plus a matching ``.rap`` activation
file).  Anything inside ``<rom_dir>/ps3/<subfolder>/`` containing at least
one ``.pkg`` is collapsed into a *single* catalog entry whose ``name`` is
the subfolder name.  Loose ``.pkg`` files at ``<rom_dir>/ps3/`` are skipped
on purpose — operators must drop PSN content into a per-game folder so the
client knows what to display and where each file should land.

Top-level ``.iso`` files at ``<rom_dir>/ps3/`` continue to scan as
individual entries (one per ISO).

PS1 bundles
-----------
PS1 subfolders are bundled too — webMAN's PS1 emulator on real PS3
hardware expects each game at ``/dev_hdd0/PSXISO/<game>/<files>``, so the
PS3 client downloads them as bundles and lays the files out under that
per-game directory.  Any subfolder of ``<rom_dir>/psx/`` that contains a
PS1 ROM file (``.cue``/``.bin``/``.chd``/``.iso``/``.pbp``) becomes a
single bundle entry whose ``name`` is the subfolder name.  Top-level PS1
files (loose ``.chd`` etc) are still scanned as individual entries —
unlike PS3 we don't reject them, because per-disc-format PS1 layouts at
the top level are common in existing libraries.

Xbox bundles
------------
Xbox releases are stored as per-game subfolders because the disc image
often travels with one or more launcher files.  Any subfolder of
``<rom_dir>/xbox/`` containing a ``.cci``, or a ``default.xbe`` alongside
a ``.iso``, is collapsed into one catalog entry.  By default the bundle is
served as a ZIP; clients can request ``?extract=iso`` to get just the disc
image (converted from CCI or streamed directly if already ISO).  Loose
top-level ``.iso`` files remain single-file entries.

Wii U bundles
-------------
Wii U titles ship either as an installable WUP/NUS folder (``title.tmd`` +
``title.tik`` + numbered ``.app`` contents) or as a decrypted loadiine
layout (``code``/``content``/``meta``).  Both collapse into a single bundle
entry so a console client can fetch one file at a time.  Single-file
emulator images (``.wua``, ``.wud``, ``.wux``) and archives wrapping either
layout stay individual entries.  When the name embeds a Wii U title id —
``Super Mario 3D World [0005000010145C00]`` — that id becomes the catalog
``title_id``, matching the key Wii U *saves* are stored under.

MSU packs (SNES MSU-1, Mega Drive MSU-MD / MD+)
-----------------------------------------------
An enhanced-audio pack is a patched ROM plus the PCM / WAV / raw audio it
streams from beside itself, so it must land on a device as a folder.  Under
``<rom_dir>/snes/`` and ``<rom_dir>/genesis/`` (any depth) two shapes become
one bundle entry each, recognised by *contents* — see ``shared/msu.py``:

* a per-game subfolder holding the pack's files, or
* a ``.zip`` of one (with or without a single wrapping folder).  Only zips
  big enough to plausibly hold audio, or with ``msu`` in the name, are
  opened; the rest keep scanning as ordinary zipped ROMs.

The entry's ``bundle_kind`` (``msu1`` / ``msu-md`` / ``mdplus``) tells the
client what layout the target wants.  A zipped pack is served as the zip it
is (``path`` points at the file, Range works), never re-archived; the
manifest lists members with the wrapping folder stripped and clients hoist
that folder away when extracting.  Its ``title_id`` is resolved from the
pack's name with the tag folded out, so ``ActRaiser (USA) (MSU1)`` shares
the save slot of ``ActRaiser (USA)``.
"""

import binascii
import hashlib
import json
import logging
import re
import zipfile
from collections import Counter
from pathlib import Path
from typing import Optional

from app.services import dat_normalizer, game_names, rom_db
from app.services.rom_id import (
    FOLDER_TO_SYSTEM,
    ROM_EXTENSIONS,
    SYSTEM_CODES,
    normalize_rom_name,
)
from shared.systems import SYSTEM_ALIASES
# rom_id imports `shared` onto sys.path for us, so this stays a plain import.
from shared import msu, wiiu_meta

logger = logging.getLogger(__name__)

SKIP_NAMES = frozenset({"metadata.txt", "systeminfo.txt"})
_ARCHIVE_EXTENSIONS = frozenset({".zip", ".7z", ".rar"})

# Directory names that should never be walked as part of a rom catalog,
# regardless of where they appear under ``rom_dir``.  The conversion cache
# (``_conversion_cache``) is the practical case: when an operator sets
# ``tmp_dir`` to a path nested inside ``rom_dir`` the scanner would otherwise
# index hashed cache outputs (e.g. ``Foo_fda7066b29d259e8.cso``) as legit
# catalog entries, which then leak the cache hash into client-side filenames.
SKIP_DIR_NAMES = frozenset({"_conversion_cache"})


def _is_inside_skipped_dir(file_path: Path) -> bool:
    return any(part in SKIP_DIR_NAMES for part in file_path.parts)

# Extensions that join a PS3 bundle even though they aren't ROM_EXTENSIONS
# in their own right — .rap files are PSN activation tickets the PS3 needs
# alongside the .pkg, and they always travel together.
_PS3_BUNDLE_COMPANION_EXTS = frozenset({".rap", ".edat"})

# Extensions that mark a PS1 subfolder as a bundle target.  Only one of
# these has to be present for the whole subfolder to graduate to a
# catalog entry; companion files (.bin, .sub, .ccd, .toc) come along for
# the ride via the keep-list below.
_PS1_BUNDLE_TRIGGER_EXTS = frozenset({
    ".cue", ".chd", ".iso", ".pbp", ".bin", ".img", ".ccd",
})

# Companion extensions kept inside a PS1 bundle even when not in
# ROM_EXTENSIONS.  Multi-track CD images use .sub/.ccd/.toc/.sbi as
# sidecars; we ship them so the PS1 emulator on the client can mount the
# disc the same way it lives on the server.
_PS1_BUNDLE_COMPANION_EXTS = frozenset({
    ".sub", ".ccd", ".toc", ".sbi", ".m3u",
})

# Xbox bundles preserve their whole per-game directory because attach
# launchers and emulator helper files do not have one stable extension across
# every library.  The scanner still ignores the same metadata sidecars as the
# other bundle paths.
_XBOX_BUNDLE_TRIGGER_EXTS = frozenset({".cci"})
_XBOX_BUNDLE_XBE_NAME = "default.xbe"
_XBOX_FILE_EXTS = frozenset({".iso", ".cci"})

# Wii U titles arrive in three shapes:
#   * WUP / NUS installable set — ``title.tmd`` + ``title.tik`` + ``title.cert``
#     alongside numbered ``.app`` contents (and ``.h3`` hash trees).  The
#     contents are encrypted, but the bundled ticket carries the title key, so
#     this shape is directly usable by BOTH a real console (installed through
#     MCP, as WUP Installer GX2 does) and Cemu 2.x, which decrypts it itself.
#     Nothing here needs a server-side decrypter.
#   * Decrypted "loadiine" layout — ``code/`` + ``content/`` + ``meta/``.
#   * A single emulator image — ``.wua`` (Cemu archive), ``.wud``/``.wux``
#     disc dump, or a ``.zip`` wrapping either folder layout.
# The two folder layouts become bundles so a console client can pull one
# ``.app`` at a time via ``/roms/{id}/file/...`` instead of a multi-GB ZIP.
# Layout reference: https://wiiubrew.org/wiki/Title_metadata
_WIIU_WUP_MARKER = "title.tmd"
_WIIU_LOADIINE_DIRS = ("code", "content", "meta")
_WIIU_FILE_EXTS = frozenset({".wua", ".wud", ".wux", ".iso", ".zip", ".7z", ".rar"})

# Wii U title ids are 16 hex chars in the 0005xxxx space (0005000010… retail
# game, 0005000E… update, 0005000C… DLC).  Requiring the ``0005`` prefix stops
# a stray CRC or hash in a filename from being mistaken for a title id.
_WIIU_TITLE_ID_RE = re.compile(
    r"(?<![0-9A-Fa-f])(0005[0-9A-Fa-f]{12})(?![0-9A-Fa-f])"
)

# How far below ``wiiu/`` to look for bundles.  Covers wiiu/updates/<Game>/
# and one further level of nesting; deep enough for any real layout, shallow
# enough that a stray deep tree can't turn the scan into a full-disk walk.
_WIIU_MAX_SCAN_DEPTH = 3


class RomEntry:
    __slots__ = (
        "rom_id",
        "title_id",
        "system",
        "name",
        "filename",
        "path",
        "size",
        "crc32",
        "source",
        "is_bundle",
        "bundle_files",
        "bundle_kind",
    )

    def __init__(
        self,
        rom_id: str,
        title_id: str,
        system: str,
        name: str,
        filename: str,
        path: str,
        size: int,
        crc32: str = "",
        source: str = "filename",
        is_bundle: bool = False,
        bundle_files: Optional[list[dict]] = None,
        bundle_kind: str = "",
    ):
        self.rom_id = rom_id
        self.title_id = title_id
        self.system = system
        self.name = name
        self.filename = filename
        self.path = path
        self.size = size
        self.crc32 = crc32
        self.source = source
        self.is_bundle = bool(is_bundle)
        # bundle_files is the parsed list of {"name": str, "size": int}
        # dicts.  None and [] are both treated as "not a bundle" so the
        # client doesn't have to handle two empty representations.
        self.bundle_files = bundle_files or []
        # What kind of folder-shaped thing this is, when the client has to
        # lay it out a particular way: an MSU pack kind (``shared/msu.py``),
        # or "" for the ordinary "unzip into a folder" bundle.
        self.bundle_kind = bundle_kind or ""

    def to_dict(self) -> dict:
        d = {
            "rom_id": self.rom_id,
            "title_id": self.title_id,
            "system": self.system,
            "name": self.name,
            "filename": self.filename,
            "path": self.path,
            "size": self.size,
            "crc32": self.crc32,
            "source": self.source,
            "is_bundle": self.is_bundle,
        }
        if self.is_bundle:
            d["file_count"] = len(self.bundle_files)
            d["files"] = self.bundle_files
            if self.bundle_kind:
                d["bundle_kind"] = self.bundle_kind
        return d

    @classmethod
    def from_row(cls, row: dict) -> "RomEntry":
        bundle_files: list[dict] = []
        raw = row.get("bundle_files", "") or ""
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    bundle_files = parsed
            except (TypeError, ValueError):
                # Corrupt JSON — treat as not-a-bundle so the catalog stays
                # serviceable.  A rescan will rewrite the row correctly.
                logger.warning(
                    "[rom_scanner] bundle_files JSON parse failed for %s",
                    row.get("rom_id"),
                )
        return cls(
            rom_id=row.get("rom_id", row["title_id"]),
            title_id=row["title_id"],
            system=row["system"],
            name=row["name"],
            filename=row["filename"],
            path=row["path"],
            size=row["size"],
            crc32=row.get("crc32", ""),
            source=row.get("source", "filename"),
            is_bundle=bool(row.get("is_bundle", 0)),
            bundle_files=bundle_files,
            bundle_kind=row.get("bundle_kind", "") or "",
        )


def _compute_crc32(file_path: Path) -> str:
    h = 0
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h = binascii.crc32(chunk, h) & 0xFFFFFFFF
    return f"{h:08X}"


def _system_for_folder(folder_name: str) -> str | None:
    """Canonical system code for a ROM folder name, else None.

    Always resolves through ``SYSTEM_ALIASES``: ``SYSTEM_CODES`` contains
    aliases too, so a folder named ``SCD`` must still index as ``SEGACD`` —
    otherwise its ROMs and its saves end up under different keys.
    """
    key = folder_name.lower().strip()
    if key in FOLDER_TO_SYSTEM:
        code = FOLDER_TO_SYSTEM[key]
    else:
        upper = key.upper()
        if upper not in SYSTEM_CODES:
            return None
        code = upper
    return SYSTEM_ALIASES.get(code, code)


def _identify_rom_slug(
    system: str, file_path: Path, norm: Optional[object]
) -> tuple[str, str, str]:
    filename = _lookup_filename(file_path)
    stem = Path(filename).stem

    if norm is not None:
        info = norm.normalize(system, filename)
        canonical = info["canonical_name"]
        source = info["source"]

        slug = normalize_rom_name(canonical)
        serial = game_names.lookup_disc_serial(system, canonical)
        if serial:
            return serial, canonical, source
        return f"{system}_{slug}", canonical, source

    slug = normalize_rom_name(stem)
    serial = game_names.lookup_disc_serial(system, stem)
    if serial:
        return serial, stem, "filename"
    return f"{system}_{slug}", stem, "filename"


def _identify_rom_crc32(
    system: str, file_path: Path, norm: Optional[object]
) -> tuple[str, str, str, str]:
    filename = _lookup_filename(file_path)
    # Archive-wrapped ROMs like *.3ds.zip / *.cci.zip should still match on
    # name, but CRC32 of the archive container is not useful for DAT lookups.
    if filename != file_path.name:
        title_id, canonical_name, source = _identify_rom_slug(system, file_path, norm)
        return title_id, canonical_name, source, ""

    crc32 = _compute_crc32(file_path)

    if norm is not None:
        info = norm.normalize(system, filename, crc32)
        canonical = info["canonical_name"]
        source = info["source"]

        slug = normalize_rom_name(canonical)
        serial = game_names.lookup_disc_serial(system, canonical)
        if serial:
            return serial, canonical, source, crc32
        return f"{system}_{slug}", canonical, source, crc32

    slug = normalize_rom_name(Path(filename).stem)
    serial = game_names.lookup_disc_serial(system, Path(filename).stem)
    if serial:
        return serial, Path(filename).stem, "filename", crc32
    return f"{system}_{slug}", Path(filename).stem, "filename", crc32


def _lookup_filename(file_path: Path) -> str:
    """Return the best filename to use for DAT/title-id matching.

    For archive uploads like ``Game.3ds.zip`` / ``Game.cci.zip`` we strip the
    outer archive layer so the normalizer sees the inner cart image name and
    derives the correct stem.
    """
    suffixes = [suffix.lower() for suffix in file_path.suffixes]
    if len(suffixes) >= 2 and suffixes[-1] in _ARCHIVE_EXTENSIONS:
        inner_suffix = suffixes[-2]
        if inner_suffix in ROM_EXTENSIONS and inner_suffix not in _ARCHIVE_EXTENSIONS:
            return file_path.name[: -len(suffixes[-1])]
    return file_path.name


def _wiiu_split_title_id(raw_name: str) -> tuple[str, str]:
    """Split ``Super Mario 3D World [0005000010145C00]`` into (id, name).

    Accepts a bare filename, a folder name, or a stem with archive suffixes
    (``Game [tid].wud.zip``).  Returns ``("", cleaned_name)`` when no Wii U
    title id is embedded.
    """
    stem = raw_name
    while True:
        suffix = Path(stem).suffix.lower()
        if suffix and suffix in ROM_EXTENSIONS:
            stem = Path(stem).stem
            continue
        break

    match = _WIIU_TITLE_ID_RE.search(stem)
    if not match:
        return "", stem.strip()

    cleaned = (stem[: match.start()] + stem[match.end() :]).strip()
    # Drop the now-empty bracket/paren pair the id lived in.
    cleaned = cleaned.replace("[]", "").replace("()", "").strip(" -_.[]()")
    return match.group(1).upper(), cleaned


def _identify_wiiu(raw_name: str, norm: Optional[object]) -> tuple[str, str, str]:
    """Resolve a Wii U folder/file name to (title_id, name, source).

    An embedded title id wins: it is the same identifier Wii U *saves* are
    keyed by (``SYNC_ID_RULES`` uses the ``title_id`` strategy for WIIU), so
    a catalog entry and its save land on the same key.  Without one we fall
    back to the usual DAT-normalized slug.

    Updates (``0005000E…``) and DLC (``0005000C…``) are absent from every DAT,
    but they share their base game's low word — so we name them from the base
    id and label which piece they are.
    """
    title_id, display = _wiiu_split_title_id(raw_name)
    if title_id:
        dat_name = game_names.get_name(title_id)
        if dat_name:
            return title_id, dat_name, "title_id"

        base_id = wiiu_meta.base_title_id(title_id)
        if base_id and base_id != title_id:
            base_name = game_names.get_name(base_id)
            if base_name:
                return (
                    title_id,
                    wiiu_meta.decorate_name(base_name, title_id),
                    "title_id",
                )

        # No DAT entry anywhere — fall back to the folder name, still
        # labelled so the list doesn't show three identical rows.
        return title_id, wiiu_meta.decorate_name(display or title_id, title_id), "filename"

    if norm is not None:
        info = norm.normalize("WIIU", raw_name)
        canonical = info["canonical_name"]
        return f"WIIU_{normalize_rom_name(canonical)}", canonical, info["source"]

    return f"WIIU_{normalize_rom_name(display)}", display, "filename"


def _is_wiiu_bundle_dir(sub: Path) -> bool:
    """True when ``sub`` *itself* is a WUP set or a decrypted loadiine layout.

    Deliberately checks only direct children.  A recursive search would make
    an organiser folder like ``wiiu/updates/`` look like one giant bundle
    because a ``title.tmd`` exists somewhere beneath it — see
    :func:`_wiiu_bundle_dirs`, which descends instead.
    """
    try:
        children = list(sub.iterdir())
    except OSError:
        return False

    if any(
        c.is_file() and c.name.lower() == _WIIU_WUP_MARKER for c in children
    ):
        return True
    child_dirs = {c.name.lower() for c in children if c.is_dir()}
    return all(name in child_dirs for name in _WIIU_LOADIINE_DIRS)


def _wiiu_bundle_dirs(folder: Path, depth: int = 0) -> list[Path]:
    """Every Wii U bundle under ``folder``, descending through organiser dirs.

    Libraries come both ways and both are supported: flat
    (``wiiu/<Game [id]>/``) or sorted (``wiiu/updates/<Game [id]>/``,
    ``wiiu/dlc/...``).  A directory that is itself a bundle ends the walk;
    anything else is assumed to be an organiser folder and descended into.
    That also covers dumps that nest one extra level inside their own folder.
    """
    if depth > _WIIU_MAX_SCAN_DEPTH:
        return []
    found: list[Path] = []
    try:
        children = sorted(folder.iterdir())
    except OSError:
        return found
    for sub in children:
        if not sub.is_dir() or sub.name in SKIP_DIR_NAMES:
            continue
        if _is_wiiu_bundle_dir(sub):
            found.append(sub)
        else:
            found.extend(_wiiu_bundle_dirs(sub, depth + 1))
    return found


def _resolve_msu_identities(scanned: list[dict]) -> None:
    """Settle each MSU pack on the id of the plain ROM it patches.

    Runs once every folder is scanned, so the plain ROMs are all known.
    Candidates come in preference order from ``_msu_row``; the first whose
    id belongs to a non-pack entry of the same system wins.  With no such
    ROM in the catalog the pick is the first DAT-recognised candidate, else
    the pack name without its ``[Hack by …]`` tag — the form a plain ROM
    would most likely be filed under if one turns up later.
    """
    plain: set[tuple[str, str]] = {
        (row["system"], row["title_id"]) for row in scanned
        if not row.get("bundle_kind")
    }
    for row in scanned:
        candidates = row.pop("_msu_candidates", None)
        if not candidates:
            row.pop("_msu_fallback", None)
            continue
        pick = next(
            (c for c in candidates if (row["system"], c[0]) in plain), None
        ) or next((c for c in candidates if c[1] != "filename"), None)
        if pick is None:
            pick = row.get("_msu_fallback") or candidates[0]
        row.pop("_msu_fallback", None)
        row["title_id"], row["source"] = pick


def _peek_msu_zip(system: str, zip_path: Path) -> tuple[str, list[dict]] | None:
    """``(kind, manifest)`` when the zip holds an MSU pack, else None.

    Reads only the central directory plus any cue sheet — a few KB off the
    end of the file, cheap even on a network share.  The manifest is what a
    client will find *after* hoisting the single wrapping folder most packs
    are zipped with, so it matches a loose-folder pack's manifest exactly.
    """
    try:
        with zipfile.ZipFile(zip_path) as zf:
            infos = [i for i in zf.infolist() if not i.is_dir()]
            root, names = msu.strip_common_root(i.filename for i in infos)
            prefix = f"{root}/" if root else ""

            def _read(name: str) -> str:
                return zf.read(prefix + name).decode("utf-8", "replace")

            kind = msu.detect_kind(system, names, _read)
            if not kind:
                return None
            manifest = [
                {"name": name, "size": info.file_size}
                for name, info in zip(names, infos)
                if name.lower() not in SKIP_NAMES and not msu.is_junk(name)
            ]
    except (OSError, zipfile.BadZipFile, RuntimeError, UnicodeDecodeError) as exc:
        logger.debug("[rom_scanner] not an MSU pack: %s (%s)", zip_path.name, exc)
        return None
    return (kind, manifest) if manifest else None


_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_MULTI_UNDERSCORE_RE = re.compile(r"_+")


def _normalize_identifier_part(value: str) -> str:
    lowered = value.lower()
    lowered = _NON_ALNUM_RE.sub("_", lowered)
    return _MULTI_UNDERSCORE_RE.sub("_", lowered).strip("_") or "unknown"


def _make_rom_id(
    entry: dict, title_counts: Counter[str], used_rom_ids: set[str]
) -> str:
    title_id = entry["title_id"]
    if title_counts[title_id] == 1 and title_id not in used_rom_ids:
        return title_id

    system_prefix, _, title_slug = title_id.partition("_")
    stem_suffix = _normalize_identifier_part(Path(entry["filename"]).stem)
    if title_slug and stem_suffix.startswith(f"{title_slug}_"):
        rom_id = f"{system_prefix}_{stem_suffix}"
    else:
        rom_id = f"{title_id}__{stem_suffix}"
    if rom_id not in used_rom_ids:
        return rom_id

    path_suffix = _normalize_identifier_part(entry["path"])
    rom_id = f"{title_id}__{path_suffix}"
    if rom_id not in used_rom_ids:
        return rom_id

    index = 2
    while True:
        candidate = f"{rom_id}_{index}"
        if candidate not in used_rom_ids:
            return candidate
        index += 1


class RomCatalog:
    def __init__(self):
        self._entries: dict[str, RomEntry] = {}
        self._by_system: dict[str, list[RomEntry]] = {}
        # Fingerprints are memoised against this; every mutation bumps it.
        self._generation = 0
        self._fingerprints: tuple[int, dict[str, dict]] | None = None

    def fingerprints(self) -> dict[str, dict]:
        """``{system: {"fingerprint": hex, "count": n}}`` for the catalogue.

        What a client caches the catalogue against. A fingerprint covers the
        fields a client displays and installs from, so a renamed or resized
        file changes it while a rescan that found nothing new does not. Per
        system, so a client refetches only the systems that changed - one
        new SNES ROM does not cost a 20,000-row download.
        """
        if self._fingerprints and self._fingerprints[0] == self._generation:
            return self._fingerprints[1]
        result: dict[str, dict] = {}
        for system, entries in self._by_system.items():
            digest = hashlib.sha1()
            for entry in sorted(entries, key=lambda e: e.rom_id):
                digest.update(("%s%s%d%s%s" % (
                    entry.rom_id, entry.title_id, entry.size, entry.name,
                    entry.filename)).encode("utf-8", "replace"))
            result[system] = {"fingerprint": digest.hexdigest(),
                              "count": len(entries)}
        self._fingerprints = (self._generation, result)
        return result

    @property
    def entries(self) -> dict[str, RomEntry]:
        return self._entries

    def get(self, rom_id: str) -> RomEntry | None:
        return self._entries.get(rom_id)

    def list_all(self) -> list[RomEntry]:
        return list(self._entries.values())

    def list_by_system(self, system: str) -> list[RomEntry]:
        return self._by_system.get(system.upper(), [])

    def systems(self) -> list[str]:
        return sorted(self._by_system.keys())

    def stats(self) -> dict[str, int]:
        return {sys: len(ents) for sys, ents in sorted(self._by_system.items())}

    def _add(self, entry: RomEntry) -> bool:
        if entry.rom_id in self._entries:
            return False
        self._entries[entry.rom_id] = entry
        self._by_system.setdefault(entry.system, []).append(entry)
        self._generation += 1
        return True

    def _rebuild_index(self) -> None:
        self._generation += 1
        self._by_system.clear()
        for entry in self._entries.values():
            self._by_system.setdefault(entry.system, []).append(entry)

    def load_from_db(self) -> int:
        rows = rom_db.list_all()
        self._entries.clear()
        self._by_system.clear()
        for row in rows:
            self._add(RomEntry.from_row(row))
        return len(self._entries)

    def scan(self, rom_dir: Path, use_crc32: bool = False) -> int:
        self._entries.clear()
        self._by_system.clear()

        if not rom_dir or not rom_dir.is_dir():
            return 0

        norm = dat_normalizer.get()
        scanned: list[dict] = []

        for folder in sorted(rom_dir.iterdir()):
            if not folder.is_dir():
                continue
            system = _system_for_folder(folder.name)
            if not system:
                continue

            self._scan_folder(folder, system, norm, rom_dir, use_crc32, scanned)

        _resolve_msu_identities(scanned)

        title_counts: Counter[str] = Counter(entry["title_id"] for entry in scanned)
        used_rom_ids: set[str] = set()
        batch: list[dict] = []

        for raw_entry in scanned:
            rom_id = _make_rom_id(raw_entry, title_counts, used_rom_ids)
            used_rom_ids.add(rom_id)

            bundle_raw = raw_entry.get("bundle_files", "")
            parsed_files: list[dict] = []
            if bundle_raw:
                try:
                    parsed = json.loads(bundle_raw)
                    if isinstance(parsed, list):
                        parsed_files = parsed
                except (TypeError, ValueError):
                    logger.warning(
                        "[rom_scanner] dropped malformed bundle_files for %s",
                        raw_entry.get("title_id"),
                    )

            entry = RomEntry(
                rom_id=rom_id,
                title_id=raw_entry["title_id"],
                system=raw_entry["system"],
                name=raw_entry["name"],
                filename=raw_entry["filename"],
                path=raw_entry["path"],
                size=raw_entry["size"],
                crc32=raw_entry["crc32"],
                source=raw_entry["source"],
                is_bundle=bool(raw_entry.get("is_bundle", 0)),
                bundle_files=parsed_files,
                bundle_kind=raw_entry.get("bundle_kind", ""),
            )
            self._add(entry)
            # Keep the JSON-encoded shape for SQLite (the upsert helper
            # passes ``bundle_files`` straight through to the column).
            db_row = entry.to_dict()
            db_row["is_bundle"] = 1 if entry.is_bundle else 0
            db_row["bundle_files"] = bundle_raw
            db_row["bundle_kind"] = entry.bundle_kind
            db_row["files"] = None  # not a column; .pop avoids strict-keys
            db_row.pop("files", None)
            db_row.pop("file_count", None)
            batch.append(db_row)

        rom_db.upsert(batch)

        logger.info(
            "[rom_scanner] Cataloged %d ROMs across %d systems (crc32=%s)",
            len(self._entries),
            len(self._by_system),
            use_crc32,
        )
        return len(self._entries)

    def _scan_folder(
        self,
        folder: Path,
        system: str,
        norm: Optional[object],
        rom_dir: Path,
        use_crc32: bool,
        scanned: list[dict],
    ) -> None:
        if system.upper() == "PS3":
            self._scan_ps3_folder(folder, norm, rom_dir, use_crc32, scanned)
            return

        if system.upper() == "XBOX":
            self._scan_xbox_folder(folder, norm, rom_dir, use_crc32, scanned)
            return

        if system.upper() == "PS1":
            self._scan_ps1_folder(folder, norm, rom_dir, use_crc32, scanned)
            return

        if system.upper() == "WIIU":
            self._scan_wiiu_folder(folder, norm, rom_dir, use_crc32, scanned)
            return

        claimed: set[Path] = set()
        if system.upper() in msu.MSU_SYSTEMS:
            claimed = self._scan_msu_packs(folder, system, norm, rom_dir, scanned)

        for file_path in sorted(folder.rglob("*")):
            if not file_path.is_file():
                continue
            if _is_inside_skipped_dir(file_path):
                continue
            if file_path.name.lower() in SKIP_NAMES:
                continue
            if file_path.suffix.lower() not in ROM_EXTENSIONS:
                continue
            if claimed and file_path.resolve() in claimed:
                continue

            crc32 = ""
            if use_crc32:
                title_id, canonical_name, source, crc32 = _identify_rom_crc32(
                    system, file_path, norm
                )
            else:
                title_id, canonical_name, source = _identify_rom_slug(
                    system, file_path, norm
                )

            rel_path = str(file_path.relative_to(rom_dir).as_posix())
            size = file_path.stat().st_size
            scanned.append(
                {
                    "title_id": title_id,
                    "system": system,
                    "name": canonical_name,
                    "filename": file_path.name,
                    "path": rel_path,
                    "size": size,
                    "crc32": crc32,
                    "source": source,
                    "is_bundle": 0,
                    "bundle_files": "",
                }
            )

    def _scan_msu_packs(
        self,
        folder: Path,
        system: str,
        norm: Optional[object],
        rom_dir: Path,
        scanned: list[dict],
    ) -> set[Path]:
        """Collapse MSU-1 / MSU-MD / MD+ packs into bundle entries.

        Walks every subfolder and every worthwhile ``.zip`` under ``folder``
        and asks :func:`shared.msu.detect_kind` about its files.  Returns the
        resolved paths the packs own so the plain per-file scan that follows
        leaves them alone — otherwise a pack's ``.sfc`` would show up a
        second time as a bare ROM, and its zip as an 800 MB "zipped ROM".
        """
        claimed: set[Path] = set()

        for sub in sorted(p for p in folder.rglob("*") if p.is_dir()):
            if _is_inside_skipped_dir(sub) or sub.name in SKIP_DIR_NAMES:
                continue
            try:
                files = sorted(f for f in sub.iterdir() if f.is_file())
            except OSError:
                continue
            names = [f.name for f in files]
            kind = msu.detect_kind(
                system, names,
                lambda n, _d=sub: (_d / n).read_text(errors="replace"),
            )
            if not kind:
                continue
            manifest: list[dict] = []
            total = 0
            for f in files:
                claimed.add(f.resolve())
                if f.name.lower() in SKIP_NAMES or msu.is_junk(f.name):
                    continue
                size = f.stat().st_size
                manifest.append({"name": f.name, "size": size})
                total += size
            if not manifest:
                continue
            scanned.append(self._msu_row(
                system, norm, kind, msu.display_name_for(sub.name),
                f"{sub.name}.zip", sub.relative_to(rom_dir).as_posix(),
                total, manifest,
            ))

        for zip_path in sorted(folder.rglob("*.zip")):
            if not zip_path.is_file() or _is_inside_skipped_dir(zip_path):
                continue
            if zip_path.resolve() in claimed:
                continue
            try:
                size = zip_path.stat().st_size
            except OSError:
                continue
            if not msu.archive_worth_peeking(zip_path.name, size):
                continue
            packed = _peek_msu_zip(system, zip_path)
            if packed is None:
                continue
            kind, manifest = packed
            claimed.add(zip_path.resolve())
            scanned.append(self._msu_row(
                system, norm, kind, msu.display_name_for(zip_path.name),
                zip_path.name, zip_path.relative_to(rom_dir).as_posix(),
                sum(m["size"] for m in manifest), manifest,
            ))

        return claimed

    @staticmethod
    def _msu_row(
        system: str,
        norm: Optional[object],
        kind: str,
        display_name: str,
        filename: str,
        rel_path: str,
        total_size: int,
        manifest: list[dict],
    ) -> dict:
        # A pack is a pack *of* some ROM, and it must share that ROM's save
        # slot.  Its name folds the (MSU1) tag away but keeps the MSU
        # author's ``[Hack by …]``, which no plain ROM carries — so several
        # spellings are tried (the name, the name without hack tags, the
        # cart member, likewise stripped) and settled once the whole scan is
        # in: the first that names a ROM actually in the catalog wins.  See
        # :meth:`_resolve_msu_identities`.  The display name keeps every
        # tag so a list shows which pack this is.
        rom = msu.rom_member(kind, [m["name"] for m in manifest])
        candidates = []
        for name in msu.identity_candidates(display_name, rom):
            tid, _canonical, src = _identify_rom_slug(system, Path(f"{name}.zip"), norm)
            if tid not in {c[0] for c in candidates}:
                candidates.append((tid, src))
        title_id, source = candidates[0]
        fallback, _c, fallback_src = _identify_rom_slug(
            system, Path(f"{msu.strip_hack_tags(display_name)}.zip"), norm
        )
        return {
            "_msu_candidates": candidates,
            "_msu_fallback": (fallback, fallback_src),
            "title_id": title_id,
            "system": system,
            "name": display_name,
            "filename": filename,
            "path": rel_path,
            "size": total_size,
            "crc32": "",
            "source": source,
            "is_bundle": 1,
            "bundle_files": json.dumps(manifest),
            "bundle_kind": kind,
        }

    def _scan_xbox_folder(
        self,
        folder: Path,
        norm: Optional[object],
        rom_dir: Path,
        use_crc32: bool,
        scanned: list[dict],
    ) -> None:
        """Xbox-specific scan with bundle detection.

        A subfolder is treated as a bundle when it contains either:
          * a ``.cci`` file (compressed disc image + launcher), OR
          * a ``default.xbe`` alongside a ``.iso`` (extracted disc with
            XBE launcher — functionally identical to CCI for emulators).

        ISO files that are standalone (not inside a bundle directory) are
        indexed as individual catalog entries.
        """
        system = "XBOX"

        bundle_dirs: list[Path] = []
        bundled_paths: set[Path] = set()

        for sub in sorted(folder.iterdir()):
            if not sub.is_dir():
                continue
            if sub.name in SKIP_DIR_NAMES:
                continue
            has_cci = any(
                f.is_file() and f.suffix.lower() in _XBOX_BUNDLE_TRIGGER_EXTS
                for f in sub.rglob("*")
            )
            # Also treat as bundle: subfolder with default.xbe + .iso
            has_xbe_iso = False
            if not has_cci:
                sub_files = [f for f in sub.rglob("*") if f.is_file()]
                has_xbe = any(
                    f.name.lower() == _XBOX_BUNDLE_XBE_NAME for f in sub_files
                )
                has_iso = any(f.suffix.lower() == ".iso" for f in sub_files)
                has_xbe_iso = has_xbe and has_iso

            if has_cci or has_xbe_iso:
                bundle_dirs.append(sub)
                for f in sub.rglob("*"):
                    if f.is_file():
                        bundled_paths.add(f.resolve())

        # ── Pass 1: CCI bundles ───────────────────────────────────────
        for bundle_dir in bundle_dirs:
            files: list[tuple[str, int]] = []
            total_size = 0
            for f in sorted(bundle_dir.rglob("*")):
                if not f.is_file():
                    continue
                if f.name.lower() in SKIP_NAMES:
                    continue
                rel = f.relative_to(bundle_dir).as_posix()
                size = f.stat().st_size
                files.append((rel, size))
                total_size += size

            if not files:
                continue

            display_name = bundle_dir.name
            slug = normalize_rom_name(display_name)
            serial = game_names.lookup_disc_serial(system, display_name)
            title_id = serial if serial else f"XBOX_BUNDLE_{slug}"
            rel_dir = str(bundle_dir.relative_to(rom_dir).as_posix())

            scanned.append(
                {
                    "title_id": title_id,
                    "system": system,
                    "name": display_name,
                    "filename": f"{display_name}.zip",
                    "path": rel_dir,
                    "size": total_size,
                    "crc32": "",
                    "source": "bundle",
                    "is_bundle": 1,
                    "bundle_files": json.dumps(
                        [{"name": rel, "size": sz} for rel, sz in files]
                    ),
                }
            )

        # ── Pass 2: ISO files outside CCI bundles ─────────────────────
        for file_path in sorted(folder.rglob("*")):
            if not file_path.is_file():
                continue
            if _is_inside_skipped_dir(file_path):
                continue
            if file_path.resolve() in bundled_paths:
                continue
            if file_path.name.lower() in SKIP_NAMES:
                continue

            ext = file_path.suffix.lower()
            if ext not in _XBOX_FILE_EXTS:
                continue

            if ext == ".cci":
                logger.info(
                    "[rom_scanner] Xbox: skipping loose .cci outside a "
                    "bundle subfolder: %s", file_path.name
                )
                continue

            crc32 = ""
            if use_crc32:
                title_id, canonical_name, source, crc32 = _identify_rom_crc32(
                    system, file_path, norm
                )
            else:
                title_id, canonical_name, source = _identify_rom_slug(
                    system, file_path, norm
                )

            rel_path = str(file_path.relative_to(rom_dir).as_posix())
            size = file_path.stat().st_size
            scanned.append(
                {
                    "title_id": title_id,
                    "system": system,
                    "name": canonical_name,
                    "filename": file_path.name,
                    "path": rel_path,
                    "size": size,
                    "crc32": crc32,
                    "source": source,
                    "is_bundle": 0,
                    "bundle_files": "",
                }
            )

    def _scan_wiiu_folder(
        self,
        folder: Path,
        norm: Optional[object],
        rom_dir: Path,
        use_crc32: bool,
        scanned: list[dict],
    ) -> None:
        """Wii U scan — WUP / loadiine folders as bundles, images as files.

        Pass 1 collapses every subfolder that looks like a WUP installable
        set (``title.tmd`` present) or a decrypted loadiine layout
        (``code``/``content``/``meta``) into one bundle entry.  Every regular
        file inside is kept: ``.app``, ``.h3``, ``.tmd``, ``.tik`` and
        ``.cert`` are all required to install and none of them are
        ``ROM_EXTENSIONS`` members in their own right.

        Pass 2 picks up single-file images (``.wua``/``.wud``/``.wux``) and
        archives that wrap a folder layout.

        Both passes prefer a title id embedded in the name — see
        :func:`_identify_wiiu`.
        """
        system = "WIIU"

        bundle_dirs = _wiiu_bundle_dirs(folder)
        bundled_paths: set[Path] = set()
        for bundle_dir in bundle_dirs:
            for f in bundle_dir.rglob("*"):
                if f.is_file():
                    bundled_paths.add(f.resolve())

        # ── Pass 1: WUP / loadiine bundles ────────────────────────────
        for bundle_dir in bundle_dirs:
            files: list[tuple[str, int]] = []
            total_size = 0
            for f in sorted(bundle_dir.rglob("*")):
                if not f.is_file():
                    continue
                if f.name.lower() in SKIP_NAMES:
                    continue
                rel = f.relative_to(bundle_dir).as_posix()
                size = f.stat().st_size
                files.append((rel, size))
                total_size += size

            if not files:
                continue

            title_id, display_name, source = _identify_wiiu(bundle_dir.name, norm)
            rel_dir = str(bundle_dir.relative_to(rom_dir).as_posix())

            scanned.append(
                {
                    "title_id": title_id,
                    "system": system,
                    "name": display_name,
                    "filename": f"{display_name}.zip",
                    "path": rel_dir,
                    "size": total_size,
                    "crc32": "",
                    "source": source if source == "title_id" else "bundle",
                    "is_bundle": 1,
                    "bundle_files": json.dumps(
                        [{"name": rel, "size": sz} for rel, sz in files]
                    ),
                }
            )

        # ── Pass 2: single-file images / archives ─────────────────────
        for file_path in sorted(folder.rglob("*")):
            if not file_path.is_file():
                continue
            if _is_inside_skipped_dir(file_path):
                continue
            if file_path.resolve() in bundled_paths:
                continue
            if file_path.name.lower() in SKIP_NAMES:
                continue

            ext = file_path.suffix.lower()
            if ext not in _WIIU_FILE_EXTS:
                continue

            title_id, display_name, source = _identify_wiiu(file_path.name, norm)

            # CRC32 of an archive container tells us nothing about the title
            # inside it, so only fingerprint raw disc images.
            crc32 = ""
            if use_crc32 and ext not in _ARCHIVE_EXTENSIONS:
                crc32 = _compute_crc32(file_path)

            rel_path = str(file_path.relative_to(rom_dir).as_posix())
            scanned.append(
                {
                    "title_id": title_id,
                    "system": system,
                    "name": display_name,
                    "filename": file_path.name,
                    "path": rel_path,
                    "size": file_path.stat().st_size,
                    "crc32": crc32,
                    "source": source,
                    "is_bundle": 0,
                    "bundle_files": "",
                }
            )

    def _scan_ps3_folder(
        self,
        folder: Path,
        norm: Optional[object],
        rom_dir: Path,
        use_crc32: bool,
        scanned: list[dict],
    ) -> None:
        """PS3-specific scan with bundle detection.

        Two passes over the tree:
          1. Walk every immediate subfolder of ``folder``.  If it contains
             at least one ``.pkg``, treat the whole subfolder as a bundle
             entry — name = subfolder, files = every regular file inside
             (recursively).  Each .pkg's neighbours come along for the ride
             so .rap activations and similar companions stay grouped.
          2. Walk the top level for individual ``.iso`` files (the legacy
             path); skip any loose ``.pkg`` because the client has nowhere
             to put it without a containing game name.

        Files inside a recognised bundle subfolder are NOT emitted as
        separate entries — they're owned by the bundle.  Files inside a
        non-PKG subfolder fall through to the legacy per-file behaviour
        (matches the previous rglob-based scanner).
        """
        system = "PS3"

        bundle_dirs: list[Path] = []
        # Subdirectory inventory keyed by the resolved Path so the second
        # pass can answer "is this file inside a bundle dir?" in O(depth).
        bundled_paths: set[Path] = set()

        for sub in sorted(folder.iterdir()):
            if not sub.is_dir():
                continue
            if sub.name in SKIP_DIR_NAMES:
                continue
            # Walk the subfolder; if any descendant is a .pkg the whole
            # subfolder graduates to bundle status.
            has_pkg = any(
                f.is_file() and f.suffix.lower() == ".pkg"
                for f in sub.rglob("*")
            )
            if has_pkg:
                bundle_dirs.append(sub)
                for f in sub.rglob("*"):
                    if f.is_file():
                        bundled_paths.add(f.resolve())

        # ── Pass 1: bundles ───────────────────────────────────────────
        for bundle_dir in bundle_dirs:
            files: list[tuple[str, int]] = []
            total_size = 0
            for f in sorted(bundle_dir.rglob("*")):
                if not f.is_file():
                    continue
                if f.name.lower() in SKIP_NAMES:
                    continue
                ext = f.suffix.lower()
                # Keep .pkg + .rap + any ROM_EXTENSIONS member.  Reject
                # obvious junk (.txt readmes, .nfo) so the bundle size /
                # file count match what the client actually downloads.
                keep = (
                    ext == ".pkg"
                    or ext in _PS3_BUNDLE_COMPANION_EXTS
                    or ext in ROM_EXTENSIONS
                )
                if not keep:
                    continue
                rel = f.relative_to(bundle_dir).as_posix()
                size = f.stat().st_size
                files.append((rel, size))
                total_size += size

            if not files:
                # All-junk subfolder somehow — skip rather than emit an
                # empty bundle.
                continue

            display_name = bundle_dir.name
            slug = normalize_rom_name(display_name)
            # PS3 bundles use a dedicated title_id namespace so they can
            # never collide with file-based PS3 ROMs (which use their
            # disc-serial).  Doing so also lets the desktop / steamdeck
            # clients tell at a glance that an entry is a bundle without
            # having to parse `is_bundle`.
            serial = game_names.lookup_disc_serial(system, display_name)
            title_id = serial if serial else f"PS3_BUNDLE_{slug}"
            rel_dir = str(bundle_dir.relative_to(rom_dir).as_posix())

            scanned.append(
                {
                    "title_id": title_id,
                    "system": system,
                    "name": display_name,
                    # Filename is the .zip the bundle endpoint will serve;
                    # the client uses this to derive the local download
                    # filename when no extract format is requested.
                    "filename": f"{display_name}.zip",
                    "path": rel_dir,
                    "size": total_size,
                    "crc32": "",
                    "source": "bundle",
                    "is_bundle": 1,
                    "bundle_files": json.dumps(
                        [{"name": rel, "size": sz} for rel, sz in files]
                    ),
                }
            )

        # ── Pass 2: top-level files (loose ISOs, etc.) ────────────────
        for file_path in sorted(folder.rglob("*")):
            if not file_path.is_file():
                continue
            if _is_inside_skipped_dir(file_path):
                continue
            if file_path.resolve() in bundled_paths:
                continue
            if file_path.name.lower() in SKIP_NAMES:
                continue

            ext = file_path.suffix.lower()
            if ext not in ROM_EXTENSIONS:
                continue

            # Loose .pkg outside a bundle → skip per the operator spec
            # ("only accept .pkg from subfolders").  Better to drop them
            # silently than confuse the client with an entry that has no
            # game name.
            if ext == ".pkg":
                logger.info(
                    "[rom_scanner] PS3: skipping loose .pkg outside a "
                    "bundle subfolder: %s", file_path.name
                )
                continue

            crc32 = ""
            if use_crc32:
                title_id, canonical_name, source, crc32 = _identify_rom_crc32(
                    system, file_path, norm
                )
            else:
                title_id, canonical_name, source = _identify_rom_slug(
                    system, file_path, norm
                )

            rel_path = str(file_path.relative_to(rom_dir).as_posix())
            size = file_path.stat().st_size
            scanned.append(
                {
                    "title_id": title_id,
                    "system": system,
                    "name": canonical_name,
                    "filename": file_path.name,
                    "path": rel_path,
                    "size": size,
                    "crc32": crc32,
                    "source": source,
                    "is_bundle": 0,
                    "bundle_files": "",
                }
            )

    def _scan_ps1_folder(
        self,
        folder: Path,
        norm: Optional[object],
        rom_dir: Path,
        use_crc32: bool,
        scanned: list[dict],
    ) -> None:
        """PS1-specific scan with subfolder bundle detection.

        Mirror of :meth:`_scan_ps3_folder` with two differences:

          1. The bundle trigger is any PS1 ROM extension
             (``_PS1_BUNDLE_TRIGGER_EXTS``), not just ``.pkg``.
          2. Loose top-level files are NOT rejected — single-disc PS1
             games at ``<rom_dir>/psx/foo.chd`` are common in existing
             libraries and we want to keep cataloging them.

        The bundle path mirrors the layout the webMAN PS1 emulator on
        real PS3 hardware expects (``/dev_hdd0/PSXISO/<game>/<files>``);
        the PS3 client uses the bundle name to derive that target dir.
        """
        system = "PS1"

        bundle_dirs: list[Path] = []
        bundled_paths: set[Path] = set()

        for sub in sorted(folder.iterdir()):
            if not sub.is_dir():
                continue
            if sub.name in SKIP_DIR_NAMES:
                continue
            has_trigger = any(
                f.is_file() and f.suffix.lower() in _PS1_BUNDLE_TRIGGER_EXTS
                for f in sub.rglob("*")
            )
            if has_trigger:
                bundle_dirs.append(sub)
                for f in sub.rglob("*"):
                    if f.is_file():
                        bundled_paths.add(f.resolve())

        # ── Pass 1: bundles ───────────────────────────────────────────
        for bundle_dir in bundle_dirs:
            files: list[tuple[str, int]] = []
            total_size = 0
            for f in sorted(bundle_dir.rglob("*")):
                if not f.is_file():
                    continue
                if f.name.lower() in SKIP_NAMES:
                    continue
                ext = f.suffix.lower()
                keep = (
                    ext in _PS1_BUNDLE_TRIGGER_EXTS
                    or ext in _PS1_BUNDLE_COMPANION_EXTS
                    or ext in ROM_EXTENSIONS
                )
                if not keep:
                    continue
                rel = f.relative_to(bundle_dir).as_posix()
                size = f.stat().st_size
                files.append((rel, size))
                total_size += size

            if not files:
                continue

            display_name = bundle_dir.name
            slug = normalize_rom_name(display_name)
            serial = game_names.lookup_disc_serial(system, display_name)
            title_id = serial if serial else f"PS1_BUNDLE_{slug}"
            rel_dir = str(bundle_dir.relative_to(rom_dir).as_posix())

            scanned.append(
                {
                    "title_id": title_id,
                    "system": system,
                    "name": display_name,
                    "filename": f"{display_name}.zip",
                    "path": rel_dir,
                    "size": total_size,
                    "crc32": "",
                    "source": "bundle",
                    "is_bundle": 1,
                    "bundle_files": json.dumps(
                        [{"name": rel, "size": sz} for rel, sz in files]
                    ),
                }
            )

        # ── Pass 2: top-level files ───────────────────────────────────
        for file_path in sorted(folder.rglob("*")):
            if not file_path.is_file():
                continue
            if _is_inside_skipped_dir(file_path):
                continue
            if file_path.resolve() in bundled_paths:
                continue
            if file_path.name.lower() in SKIP_NAMES:
                continue

            ext = file_path.suffix.lower()
            if ext not in ROM_EXTENSIONS:
                continue

            crc32 = ""
            if use_crc32:
                title_id, canonical_name, source, crc32 = _identify_rom_crc32(
                    system, file_path, norm
                )
            else:
                title_id, canonical_name, source = _identify_rom_slug(
                    system, file_path, norm
                )

            rel_path = str(file_path.relative_to(rom_dir).as_posix())
            size = file_path.stat().st_size
            scanned.append(
                {
                    "title_id": title_id,
                    "system": system,
                    "name": canonical_name,
                    "filename": file_path.name,
                    "path": rel_path,
                    "size": size,
                    "crc32": crc32,
                    "source": source,
                    "is_bundle": 0,
                    "bundle_files": "",
                }
            )


_catalog: Optional[RomCatalog] = None


def init(rom_dir: Path | None) -> Optional[RomCatalog]:
    global _catalog
    if not rom_dir or not rom_dir.is_dir():
        _catalog = None
        return None

    rom_db.init_db(_save_dir())

    _catalog = RomCatalog()
    cached = _catalog.load_from_db()

    if cached > 0:
        logger.info("[rom_scanner] Loaded %d ROMs from cache", cached)
    else:
        _catalog.scan(rom_dir, use_crc32=False)

    return _catalog


def get() -> Optional[RomCatalog]:
    return _catalog


def rescan(use_crc32: bool = False) -> Optional[RomCatalog]:
    from app.config import settings

    global _catalog

    if not settings.rom_dir or not settings.rom_dir.is_dir():
        _catalog = None
        return None

    rom_db.init_db(settings.save_dir)

    if _catalog is None:
        _catalog = RomCatalog()
    _catalog.scan(settings.rom_dir, use_crc32=use_crc32)
    return _catalog


def cleanup_missing() -> int:
    """Drop catalog rows whose backing file is gone from disk.

    Lightweight alternative to a full rescan: walks the in-memory
    catalog (which mirrors ``roms.db``), stats each entry's path under
    ``settings.rom_dir``, and deletes any row whose file no longer
    exists.  Doesn't traverse the ROM tree, doesn't touch DAT lookups,
    doesn't recompute CRC32s — just stat() per row.

    Returns the number of rows removed (0 when everything still
    exists or when there's no catalog yet).
    """
    from app.config import settings

    if _catalog is None:
        return 0
    rom_dir = settings.rom_dir
    if not rom_dir or not rom_dir.is_dir():
        return 0

    to_remove: list[str] = []
    # ``list(...)`` snapshot — we mutate ``_entries`` below, so iterating
    # the live dict would raise.
    for entry in list(_catalog.list_all()):
        full = rom_dir / entry.path
        try:
            # Bundle rows store ``path`` as the bundle *directory*, not a
            # file, so ``is_file()`` would (wrongly) flag every bundle as
            # missing — except a zipped MSU pack, whose bundle *is* a file.
            exists = full.is_dir() or full.is_file() if entry.is_bundle else full.is_file()
            if not exists:
                to_remove.append(entry.rom_id)
        except OSError:
            # Permission/IO issue — leave the row alone rather than
            # nuke entries the OS just can't stat right now.
            continue

    if not to_remove:
        return 0

    for rom_id in to_remove:
        rom_db.delete(rom_id)
        _catalog._entries.pop(rom_id, None)
    _catalog._rebuild_index()

    logger.info("[rom_scanner] cleanup_missing: removed %d row(s)", len(to_remove))
    return len(to_remove)


def _save_dir() -> Path:
    from app.config import settings

    return settings.save_dir
