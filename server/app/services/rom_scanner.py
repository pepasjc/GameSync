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
from pathlib import Path
from typing import Optional

from app.services import dat_normalizer, game_names, rom_db
from app.services.rom_id import SYSTEM_CODES, normalize_rom_name

logger = logging.getLogger(__name__)

FOLDER_TO_SYSTEM: dict[str, str] = {
    "3do": "ARCADE",
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
    "ngpc": "NGP",
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
    "virtualboy": "ARCADE",
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
        title_id: str,
        system: str,
        name: str,
        filename: str,
        path: str,
        size: int,
        crc32: str = "",
        source: str = "filename",
    ):
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
        slug = info["slug"]
        source = info["source"]

        if system in ("PS1", "PSX"):
            serial = game_names.lookup_psx_serial(canonical)
            if serial:
                return serial, canonical, source
        return f"{system}_{slug}", canonical, source

    slug = normalize_rom_name(stem)
    if system in ("PS1", "PSX"):
        serial = game_names.lookup_psx_serial(stem)
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
        slug = info["slug"]
        source = info["source"]

        if system in ("PS1", "PSX"):
            serial = game_names.lookup_psx_serial(canonical)
            if serial:
                return serial, canonical, source, crc32
        return f"{system}_{slug}", canonical, source, crc32

    slug = normalize_rom_name(Path(filename).stem)
    if system in ("PS1", "PSX"):
        serial = game_names.lookup_psx_serial(Path(filename).stem)
        if serial:
            return serial, Path(filename).stem, "filename", crc32
    return f"{system}_{slug}", Path(filename).stem, "filename", crc32


class RomCatalog:
    def __init__(self):
        self._entries: dict[str, RomEntry] = {}
        self._by_system: dict[str, list[RomEntry]] = {}

    @property
    def entries(self) -> dict[str, RomEntry]:
        return self._entries

    def get(self, title_id: str) -> RomEntry | None:
        return self._entries.get(title_id)

    def list_all(self) -> list[RomEntry]:
        return list(self._entries.values())

    def list_by_system(self, system: str) -> list[RomEntry]:
        return self._by_system.get(system.upper(), [])

    def systems(self) -> list[str]:
        return sorted(self._by_system.keys())

    def stats(self) -> dict[str, int]:
        return {sys: len(ents) for sys, ents in sorted(self._by_system.items())}

    def _add(self, entry: RomEntry) -> None:
        self._entries[entry.title_id] = entry
        self._by_system.setdefault(entry.system, []).append(entry)

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
        batch: list[dict] = []

        for folder in sorted(rom_dir.iterdir()):
            if not folder.is_dir():
                continue
            system = _system_for_folder(folder.name)
            if not system:
                continue

            self._scan_folder(folder, system, norm, rom_dir, use_crc32, batch)

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
        batch: list[dict],
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

            entry = RomEntry(
                title_id=title_id,
                system=system,
                name=canonical_name,
                filename=file_path.name,
                path=rel_path,
                size=size,
                crc32=crc32,
                source=source,
            )
            self._add(entry)
            batch.append(entry.to_dict())


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
