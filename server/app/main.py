from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.config import settings
from app.middleware.auth import APIKeyMiddleware
from app.routes import normalize, roms, saves, status, sync, titles, update, web
from app.services import dat_normalizer, db, game_names, rom_scanner

import asyncio
import logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.save_dir.mkdir(parents=True, exist_ok=True)
    db.init_db(settings.save_dir)

    data_dir = Path(__file__).parent.parent / "data"
    dats_dir = data_dir / "dats"

    # Load game names databases
    # 3dstitledb.txt: full 16-char TitleID→name for 3DS hardware client (not in DATs)
    count_title_ids = game_names.load_database(data_dir / "3dstitledb.txt")
    count_wii = game_names.load_libretro_dat_to_dicts(
        dats_dir / "Nintendo - GameCube.dat"
    )
    count_wii += game_names.load_libretro_dat_to_dicts(dats_dir / "Nintendo - Wii.dat")

    # Load libretro DATs (replace legacy .txt files for PS1/PSP/Vita/3DS/DS)
    # Retail DATs are loaded first (psn=False); PSN DATs second (psn=True) so
    # retail entries are never overwritten by their PSN equivalents.
    count_psx = game_names.load_libretro_dat_to_dicts(
        dats_dir / "Sony - PlayStation.dat"
    )
    count_ps3 = game_names.load_libretro_dat_to_dicts(
        dats_dir / "Sony - PlayStation 3.dat"
    )
    count_psp = game_names.load_libretro_dat_to_dicts(
        dats_dir / "Sony - PlayStation Portable.dat"
    )
    count_psp += game_names.load_libretro_dat_to_dicts(
        dats_dir / "Sony - PlayStation Portable (PSN).dat", psn=True
    )
    count_vita = game_names.load_libretro_dat_to_dicts(
        dats_dir / "Sony - PlayStation Vita.dat"
    )
    count_3ds = game_names.load_libretro_dat_to_dicts(
        dats_dir / "Nintendo - Nintendo 3DS.dat"
    )
    count_3ds += game_names.load_libretro_dat_to_dicts(
        dats_dir / "Nintendo - Nintendo 3DS (Digital).dat"
    )
    count_ds = game_names.load_libretro_dat_to_dicts(
        dats_dir / "Nintendo - Nintendo DS.dat"
    )
    count_ds += game_names.load_libretro_dat_to_dicts(
        dats_dir / "Nintendo - Nintendo DSi.dat"
    )

    count_psn_retail = game_names.build_psx_psn_to_retail()
    print(
        f"Loaded {count_title_ids} 3DS TitleIDs + {count_3ds} 3DS codes + {count_ds} DS + "
        f"{count_psp} PSP + {count_vita} Vita + {count_psx} PSX + {count_ps3} PS3 + {count_wii} GC/Wii game names "
        f"({count_psn_retail} PSN→retail mappings)"
    )

    # Load No-Intro / Redump DAT files for ROM normalization
    dats_dir.mkdir(exist_ok=True)
    dat_normalizer.init(dats_dir)

    # Load ROM catalog from cache (or scan if no cache)
    rom_scan_task = None
    if settings.rom_dir:
        rom_db_path = settings.save_dir / "roms.db"
        from app.services import rom_db

        rom_db.init_db(settings.save_dir)

        rom_catalog = rom_scanner.init(settings.rom_dir)
        if rom_catalog:
            print(
                f"ROM catalog: {len(rom_catalog.entries)} ROMs across {len(rom_catalog.systems())} systems"
            )
        else:
            print("ROM catalog: no ROMs found or directory not accessible")

        if settings.rom_scan_interval > 0:
            rom_scan_task = asyncio.create_task(_periodic_rom_scan())

    yield

    if rom_scan_task:
        rom_scan_task.cancel()
        try:
            await rom_scan_task
        except asyncio.CancelledError:
            pass


async def _periodic_rom_scan():
    interval = settings.rom_scan_interval
    try:
        while True:
            await asyncio.sleep(interval)
            try:
                catalog = rom_scanner.rescan()
                if catalog:
                    logger.info(
                        "[rom_scanner] Periodic scan: %d ROMs", len(catalog.entries)
                    )
            except Exception:
                logger.exception("[rom_scanner] Periodic scan failed")
    except asyncio.CancelledError:
        pass


def create_app() -> FastAPI:
    app = FastAPI(title="Save Sync", version="1.0.0", lifespan=lifespan)

    app.add_middleware(APIKeyMiddleware)

    app.include_router(web.router)  # serves GET / — no auth, key injected server-side
    app.include_router(status.router, prefix="/api/v1")
    app.include_router(titles.router, prefix="/api/v1")
    app.include_router(saves.router, prefix="/api/v1")
    app.include_router(sync.router, prefix="/api/v1")
    app.include_router(update.router, prefix="/api/v1")
    app.include_router(normalize.router, prefix="/api/v1")
    app.include_router(roms.router, prefix="/api/v1")

    return app


app = create_app()
