"""
RetroAchievements support checker.

Answers "does RetroAchievements know this ROM?" for a folder of files —
aimed at ROM hacks and fan translations, which No-Intro DATs can't vouch
for.  Each file is hashed with RA's own per-system rules
(:mod:`shared.ra_hash`) and looked up in RA's public hash library.

Network access is two unauthenticated ``dorequest.php`` calls per console
(``r=hashlibrary`` → md5 → game id, ``r=gameslist`` → game id → title), the
same endpoints RAIntegration uses, cached on disk for a day.  No account or
API key is needed.

With a web API key (Tools → Config) the ``API_GetGameList.php`` endpoint is
used instead: one call per console that also carries each game's achievement
count, so a hash RA knows but has no set for can be told apart from one that
actually earns achievements.
"""

from __future__ import annotations

import io
import re
import sys
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import rom_normalizer as rn

_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from shared.ra_hash import RA_CONSOLE_IDS, RaHash, ra_console_ids, ra_hash_stream  # noqa: E402
from shared.systems import SYSTEM_CODES, normalize_system_code  # noqa: E402

try:
    import py7zr
except ImportError:  # pragma: no cover - exercised when desktop deps are stale
    py7zr = None


from shared.ra_api import (  # noqa: E402  (re-exported for callers and tests)
    CACHE_DIR,
    CACHE_MAX_AGE,
    RA_BASE_URL,
    RA_GAME_URL,
    RA_WEB_API_URL,
    RaAuthError,
    RaLibrary,
    clear_cache,
    download_library,
    download_library_web,
    fetch_library,
    load_cached_library,
)



# ---------------------------------------------------------------------------
# Hack / translation detection
# ---------------------------------------------------------------------------

KIND_TRANSLATION = "translation"
KIND_HACK = "hack"
KIND_UNMATCHED = "unmatched"   # no hack tag, but CRC not in the No-Intro DAT
KIND_RETAIL = "retail"         # CRC found in the No-Intro DAT
KIND_UNKNOWN = "unknown"       # no tag and no DAT to check against

HACK_KINDS = frozenset({KIND_TRANSLATION, KIND_HACK, KIND_UNMATCHED})

# GoodTools / No-Intro / RHDN style markers.  Translation is checked first
# because "[T-En by X] [Hack]" is still primarily a translation.
_TRANSLATION_RE = re.compile(
    r"\[t[-+][a-z]|\[(?:fan\s*)?translat|\((?:fan\s*)?translat|\(t-[a-z]{2}",
    re.IGNORECASE,
)
_HACK_RE = re.compile(
    r"\[h(?:\d+|\]|ack)|\(hack|\[hack|\bhack\b|\bromhack\b",
    re.IGNORECASE,
)


def classify_name(name: str) -> str | None:
    """``translation`` / ``hack`` from filename tags, else ``None``."""
    if _TRANSLATION_RE.search(name):
        return KIND_TRANSLATION
    if _HACK_RE.search(name):
        return KIND_HACK
    return None


def classify(name: str, crc: str | None, dat: dict[str, str] | None) -> str:
    """Full classification: tag first, then DAT membership when a DAT exists."""
    tagged = classify_name(name)
    if tagged:
        return tagged
    if not dat:
        return KIND_UNKNOWN
    if crc and crc.upper() in dat:
        return KIND_RETAIL
    return KIND_UNMATCHED


# ---------------------------------------------------------------------------
# Folder scan
# ---------------------------------------------------------------------------

STATUS_SUPPORTED = "supported"
STATUS_REGISTERED = "registered"     # RA knows the hash but the set has 0 achievements
STATUS_NOT_FOUND = "not_found"
STATUS_UNSUPPORTED = "unsupported"   # system RA can't hash here (disc etc.)
STATUS_NO_SYSTEM = "no_system"
STATUS_ERROR = "error"

_ARCHIVE_EXTENSIONS = {".zip", ".7z"}


@dataclass
class RaScanEntry:
    path: Path
    system: str
    kind: str
    member: str | None = None
    md5: str | None = None
    status: str = STATUS_ERROR
    game_id: int | None = None
    title: str | None = None
    achievements: int | None = None   # None = unknown (public endpoints)
    detail: str = ""

    @property
    def label(self) -> str:
        return f"{self.path.name}/{self.member}" if self.member else self.path.name

    @property
    def game_url(self) -> str | None:
        return RA_GAME_URL.format(game_id=self.game_id) if self.game_id else None

    @property
    def is_hack(self) -> bool:
        return self.kind in HACK_KINDS


def detect_system(path: Path, root: Path) -> str | None:
    """System code from the nearest ancestor folder name under ``root``.

    ``roms/snes/hacks/foo.sfc`` → ``SNES``; ``root`` itself counts, so a
    folder named ``genesis`` scanned directly resolves too.
    """
    folder = path.parent
    while True:
        code = normalize_system_code(folder.name)
        if code in SYSTEM_CODES:
            return code
        if folder == root or folder.parent == folder:
            return None
        folder = folder.parent


def _is_arcade(system: str) -> bool:
    return system in {"ARCADE", "MAME", "FBNEO", "FBA", "NEOGEO", "CPS1", "CPS2", "CPS3"}


def _rom_extension(name: str) -> bool:
    return Path(name).suffix.lower() in rn.ROM_EXTENSIONS


def iter_rom_files(root: Path) -> list[Path]:
    return sorted(
        p for p in root.rglob("*")
        if p.is_file()
        and (p.suffix.lower() in rn.ROM_EXTENSIONS or p.suffix.lower() in _ARCHIVE_EXTENSIONS)
    )


def _crc_of_bytes(data: bytes) -> str:
    return f"{zlib.crc32(data) & 0xFFFFFFFF:08X}"


def _iter_archive_members(path: Path) -> Iterable[tuple[str, bytes]]:
    """Yield ``(member_name, bytes)`` for every ROM-looking member."""
    ext = path.suffix.lower()
    if ext == ".zip":
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                if info.is_dir() or not _rom_extension(info.filename):
                    continue
                yield info.filename, zf.read(info)
    elif ext == ".7z":
        if py7zr is None:
            raise RuntimeError("py7zr is not installed; cannot read .7z archives")
        with py7zr.SevenZipFile(path, "r") as sz:
            names = [n for n in sz.getnames() if _rom_extension(n)]
            if not names:
                return
            for name, fh in (sz.read(names) or {}).items():
                yield name, fh.read()


class DatCache:
    """Lazily loads one No-Intro DAT per system for the retail/unmatched split."""

    def __init__(self) -> None:
        self._dats: dict[str, dict[str, str] | None] = {}

    def get(self, system: str) -> dict[str, str] | None:
        if system not in self._dats:
            dat_path = rn.find_dat_for_system(system)
            self._dats[system] = rn.load_no_intro_dat(dat_path) if dat_path else None
        return self._dats[system]


LibraryProvider = Callable[[int], RaLibrary]
ProgressCallback = Callable[[str], None]


def _lookup(
    md5: str, system: str, libraries: LibraryProvider
) -> tuple[int | None, str | None, int | None]:
    """Search every RA console mapped to ``system``; returns
    ``(game_id, title, achievement_count)`` — count is None when unknown."""
    for console_id in ra_console_ids(system):
        library = libraries(console_id)
        game_id, title = library.lookup(md5)
        if game_id:
            return game_id, title, library.achievement_count(game_id)
    return None, None, None


def _finish(entry: RaScanEntry, hashed: RaHash, libraries: LibraryProvider) -> RaScanEntry:
    if not hashed.ok:
        entry.status = STATUS_ERROR
        entry.detail = hashed.reason or "hash failed"
        return entry
    entry.md5 = hashed.md5
    game_id, title, achievements = _lookup(hashed.md5, entry.system, libraries)
    if game_id:
        entry.game_id = game_id
        entry.title = title
        entry.achievements = achievements
        entry.status = STATUS_REGISTERED if achievements == 0 else STATUS_SUPPORTED
    else:
        entry.status = STATUS_NOT_FOUND
    return entry


def scan_folder(
    root: Path,
    libraries: LibraryProvider,
    system: str | None = None,
    only_hacks: bool = True,
    progress: ProgressCallback | None = None,
    should_stop: Callable[[], bool] | None = None,
    dats: DatCache | None = None,
) -> list[RaScanEntry]:
    """Hash every ROM under ``root`` and look it up on RetroAchievements.

    ``system`` forces one system for everything; otherwise each file's system
    comes from its folder name.  With ``only_hacks`` the result keeps just
    translations, tagged hacks and ROMs absent from the No-Intro DAT.
    """
    dats = dats or DatCache()
    results: list[RaScanEntry] = []
    files = iter_rom_files(root)
    total = len(files)

    for index, path in enumerate(files, 1):
        if should_stop and should_stop():
            break
        if progress:
            progress(f"[{index}/{total}] {path.name}")

        sysc = system or detect_system(path, root)
        if not sysc:
            if not only_hacks or classify_name(path.name):
                results.append(RaScanEntry(path, "", classify_name(path.name) or KIND_UNKNOWN,
                                           status=STATUS_NO_SYSTEM, detail="system not recognised"))
            continue

        if sysc not in RA_CONSOLE_IDS:
            kind = classify_name(path.name) or KIND_UNKNOWN
            if only_hacks and kind not in HACK_KINDS:
                continue
            results.append(RaScanEntry(path, sysc, kind, status=STATUS_UNSUPPORTED,
                                       detail=f"{sysc}: RetroAchievements hashing not supported here"))
            continue

        try:
            results.extend(_scan_one(path, sysc, only_hacks, libraries, dats))
        except Exception as exc:  # noqa: BLE001 - one bad file must not kill the scan
            results.append(RaScanEntry(path, sysc, classify_name(path.name) or KIND_UNKNOWN,
                                       status=STATUS_ERROR, detail=str(exc)))
    return results


def _scan_one(
    path: Path,
    sysc: str,
    only_hacks: bool,
    libraries: LibraryProvider,
    dats: DatCache,
) -> list[RaScanEntry]:
    ext = path.suffix.lower()

    # Arcade: the zip *is* the romset, and RA hashes its name.
    if _is_arcade(sysc):
        kind = classify_name(path.name) or KIND_UNKNOWN
        if only_hacks and kind not in HACK_KINDS:
            return []
        entry = RaScanEntry(path, sysc, kind)
        return [_finish(entry, ra_hash_stream(None, sysc, str(path)), libraries)]  # type: ignore[arg-type]

    dat = dats.get(sysc)
    out: list[RaScanEntry] = []

    if ext in _ARCHIVE_EXTENSIONS:
        for member, data in _iter_archive_members(path):
            kind = classify(f"{path.name} {member}", _crc_of_bytes(data), dat)
            if only_hacks and kind not in HACK_KINDS:
                continue
            entry = RaScanEntry(path, sysc, kind, member=member)
            out.append(_finish(entry, ra_hash_stream(io.BytesIO(data), sysc, member), libraries))
        return out

    crc = rn._crc32_file(path) if dat else None
    kind = classify(path.name, crc, dat)
    if only_hacks and kind not in HACK_KINDS:
        return out
    entry = RaScanEntry(path, sysc, kind)
    with open(path, "rb") as fh:
        out.append(_finish(entry, ra_hash_stream(fh, sysc, str(path)), libraries))
    return out


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

STATUS_LABELS = {
    STATUS_SUPPORTED: "Supported",
    STATUS_REGISTERED: "Registered, no achievements",
    STATUS_NOT_FOUND: "Not on RA",
    STATUS_UNSUPPORTED: "Can't hash",
    STATUS_NO_SYSTEM: "Unknown system",
    STATUS_ERROR: "Error",
}

KIND_LABELS = {
    KIND_TRANSLATION: "Translation",
    KIND_HACK: "Hack",
    KIND_UNMATCHED: "Not in DAT",
    KIND_RETAIL: "Retail",
    KIND_UNKNOWN: "Unknown",
}

CSV_COLUMNS = (
    "file", "system", "kind", "status", "ra_game_id", "ra_title", "achievements", "ra_url", "md5", "detail",
)


def entry_to_row(entry: RaScanEntry) -> dict[str, str]:
    return {
        "file": str(entry.path) + (f"/{entry.member}" if entry.member else ""),
        "system": entry.system,
        "kind": KIND_LABELS.get(entry.kind, entry.kind),
        "status": STATUS_LABELS.get(entry.status, entry.status),
        "ra_game_id": str(entry.game_id or ""),
        "ra_title": entry.title or "",
        "achievements": "" if entry.achievements is None else str(entry.achievements),
        "ra_url": entry.game_url or "",
        "md5": entry.md5 or "",
        "detail": entry.detail,
    }


def write_csv(entries: list[RaScanEntry], path: Path) -> None:
    import csv

    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for entry in entries:
            writer.writerow(entry_to_row(entry))


def summarize(entries: list[RaScanEntry]) -> str:
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry.status] = counts.get(entry.status, 0) + 1
    parts = [f"{len(entries)} ROM(s)"]
    for status in (STATUS_SUPPORTED, STATUS_REGISTERED, STATUS_NOT_FOUND,
                   STATUS_UNSUPPORTED, STATUS_NO_SYSTEM, STATUS_ERROR):
        if counts.get(status):
            parts.append(f"{STATUS_LABELS[status]}: {counts[status]}")
    return " — ".join(parts)
