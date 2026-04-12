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
"""

import binascii
import logging
import re
from collections import Counter
from pathlib import Path
from typing import Optional

from app.services import dat_normalizer, game_names, rom_db
from app.services.rom_id import (
    SYSTEM_CODES,
    normalize_rom_name,
)

logger = logging.getLogger(__name__)

FOLDER_TO_SYSTEM: dict[str, str] = {
    "3do": "3DO",
    "ags": "ARCADE",
    "amiga": "ARCADE",
    "amiga1200": "ARCADE",
    "amiga600": "ARCADE",
    "amigacd32": "ARCADE",
    "amstradcpc": "ARCADE",
    "atari2600": "A2600",
    "atari5200": "ARCADE",
    "atari7800": "A7800",
    "atari800": "ARCADE",
    "atarijaguar": "ARCADE",
    "atarijaguarcd": "ARCADE",
    "atarilynx": "LYNX",
    "atarist": "ARCADE",
    "atarixe": "ARCADE",
    "atomiswave": "ARCADE",
    "arcade": "ARCADE",
    "fba": "FBA",
    "fbneo": "FBA",
    "c64": "ARCADE",
    "cavestory": "ARCADE",
    "colecovision": "ARCADE",
    "cps": "CPS1",
    "cps1": "CPS1",
    "cps2": "CPS2",
    "cps3": "CPS3",
    "daphne": "ARCADE",
    "dreamcast": "DC",
    "famicom": "NES",
    "fds": "FDS",
    "gameandwatch": "ARCADE",
    "gamegear": "GG",
    "gb": "GB",
    "gba": "GBA",
    "gbc": "GBC",
    "gc": "GC",
    "genesis": "MD",
    "megadrive": "MD",
    "megadrivejp": "MD",
    "mastersystem": "SMS",
    "megacd": "SCD",
    "megacdjp": "SCD",
    "sega32x": "32X",
    "sega32xjp": "32X",
    "sega32xna": "32X",
    "segacd": "SCD",
    "model2": "ARCADE",
    "model3": "ARCADE",
    "naomi": "ARCADE",
    "naomigd": "ARCADE",
    "n3ds": "3DS",
    "n64": "N64",
    "n64dd": "N64DD",
    "nds": "NDS",
    "nes": "NES",
    "neogeo": "NEOGEO",
    "neogeocd": "NEOCD",
    "neogeocdjp": "NEOCD",
    "ngp": "NGP",
    "ngpc": "NGPC",
    "pcengine": "PCE",
    "pcenginecd": "PCECD",
    "pcfx": "PCE",
    "psx": "PS1",
    "ps1": "PS1",
    "ps2": "PS2",
    "ps3": "PS3",
    "psp": "PSP",
    "psvita": "VITA",
    "saturn": "SAT",
    "saturnjp": "SAT",
    "sfc": "SNES",
    "sgb": "SNES",
    "snes": "SNES",
    "snesna": "SNES",
    "sneshd": "SNES",
    "satellaview": "SNES",
    "sufami": "SNES",
    "tg16": "TG16",
    "tg-cd": "PCECD",
    "virtualboy": "VB",
    "wii": "WII",
    "wonderswan": "WSWAN",
    "wonderswancolor": "WSWANC",
    "mame": "MAME",
    "mame-advmame": "MAME",
    "mame-mame4all": "MAME",
}

ROM_EXTENSIONS = frozenset(
    {
        ".gba",
        ".gbc",
        ".gb",
        ".nes",
        ".fds",
        ".sfc",
        ".smc",
        ".sgb",
        ".nds",
        ".3ds",
        ".cia",
        ".n64",
        ".z64",
        ".v64",
        ".ndd",
        ".d64",
        ".iso",
        ".cso",
        ".chd",
        ".elf",
        ".gcm",
        ".gci",
        ".md",
        ".smd",
        ".gen",
        ".32x",
        ".gg",
        ".sms",
        ".vb",
        ".pce",
        ".tg16",
        ".cue",
        ".bin",
        ".ccd",
        ".img",
        ".mds",
        ".mdf",
        ".ecm",
        ".dax",
        ".pbp",
        ".zip",
        ".7z",
        ".rar",
        ".ngp",
        ".ngc",
        ".ws",
        ".wsc",
        ".pc2",
        ".lnx",
        ".a26",
        ".a78",
        ".vec",
        ".sat",
        ".col",
        ".neo",
    }
)

SKIP_NAMES = frozenset({"metadata.txt", "systeminfo.txt"})


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

    def to_dict(self) -> dict:
        return {
            "rom_id": self.rom_id,
            "title_id": self.title_id,
            "system": self.system,
            "name": self.name,
            "filename": self.filename,
            "path": self.path,
            "size": self.size,
            "crc32": self.crc32,
            "source": self.source,
        }

    @classmethod
    def from_row(cls, row: dict) -> "RomEntry":
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
    key = folder_name.lower().strip()
    if key in FOLDER_TO_SYSTEM:
        return FOLDER_TO_SYSTEM[key]
    upper = key.upper()
    if upper in SYSTEM_CODES:
        return upper
    return None


def _identify_rom_slug(
    system: str, file_path: Path, norm: Optional[object]
) -> tuple[str, str, str]:
    filename = file_path.name
    stem = file_path.stem

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
    filename = file_path.name
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
        return True

    def _rebuild_index(self) -> None:
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

        title_counts: Counter[str] = Counter(entry["title_id"] for entry in scanned)
        used_rom_ids: set[str] = set()
        batch: list[dict] = []

        for raw_entry in scanned:
            rom_id = _make_rom_id(raw_entry, title_counts, used_rom_ids)
            used_rom_ids.add(rom_id)

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
            )
            self._add(entry)
            batch.append(entry.to_dict())

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
        for file_path in sorted(folder.rglob("*")):
            if not file_path.is_file():
                continue
            if file_path.name.lower() in SKIP_NAMES:
                continue
            if file_path.suffix.lower() not in ROM_EXTENSIONS:
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


def _save_dir() -> Path:
    from app.config import settings

    return settings.save_dir
