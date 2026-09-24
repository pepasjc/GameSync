from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.config import settings
from app.middleware.auth import APIKeyMiddleware
from app.routes import catalog, normalize, roms, saves, status, sync, titles, update, web
from app.services import dat_normalizer, db, game_names, rom_scanner, saturn_archives

import asyncio
import logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail fast on an insecure config (weak/placeholder API key) before we
    # bind a socket and start serving.
    settings.validate_security()

    settings.save_dir.mkdir(parents=True, exist_ok=True)
    db.init_db(settings.save_dir)

    data_dir = Path(__file__).parent.parent / "data"
    dats_dir = data_dir / "dats"

    # Load game name databases.
    count_wii = game_names.load_libretro_dat_to_dicts(
        dats_dir / "Nintendo - GameCube.dat"
    )
    count_wii += game_names.load_libretro_dat_to_dicts(dats_dir / "Nintendo - Wii.dat")
    count_wiiu = game_names.load_libretro_dat_to_dicts(
        dats_dir / "Nintendo - Wii U.dat"
    )

    # Load libretro DATs (replace legacy .txt files for PS1/PSP/Vita/3DS/DS)
    # Retail DATs are loaded first (psn=False); PSN DATs second (psn=True) so
    # retail entries are never overwritten by their PSN equivalents.
    count_psx = game_names.load_libretro_dat_to_dicts(
        dats_dir / "Sony - PlayStation.dat"
    )
    count_ps2 = game_names.load_libretro_dat_to_dicts(
        dats_dir / "Sony - PlayStation 2.dat"
    )
    count_sat = game_names.load_libretro_dat_to_dicts(dats_dir / "Sega - Saturn.dat")
    count_dc = game_names.load_libretro_dat_to_dicts(
        dats_dir / "Sega - Dreamcast.dat"
    )
    count_ps3 = game_names.load_libretro_dat_to_dicts(
        dats_dir / "Sony - PlayStation 3.dat"
    )
    count_ps3 += game_names.load_libretro_dat_to_dicts(
        dats_dir / "Sony - PlayStation 3 (PSN).dat", psn=True
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
    count_3ds_title_ids = game_names.get_3ds_title_id_count()
    count_ds = game_names.load_libretro_dat_to_dicts(
        dats_dir / "Nintendo - Nintendo DS.dat"
    )
    count_ds += game_names.load_libretro_dat_to_dicts(
        dats_dir / "Nintendo - Nintendo DSi.dat"
    )
    count_xbox = game_names.load_libretro_dat_to_dicts(
        dats_dir / "Microsoft - Xbox.dat"
    )

    count_psn_retail = game_names.build_psx_psn_to_retail()
    count_sat_slugs = game_names.build_saturn_slug_index()
    count_dc_slugs = game_names.build_dreamcast_slug_index()
    count_sat_archives = saturn_archives.load_seed(
        data_dir / "saturn_archive_names.json"
    )
    print(
        f"Loaded {count_3ds_title_ids} 3DS TitleIDs + {count_3ds} 3DS codes + {count_ds} DS + "
        f"{count_psp} PSP + {count_vita} Vita + {count_psx} PSX + {count_ps2} PS2 + {count_sat} Saturn + {count_dc} Dreamcast + {count_ps3} PS3 + {count_wii} GC/Wii + {count_wiiu} Wii U + {count_xbox} Xbox game names "
        f"({count_psn_retail} PSN→retail mappings, {count_sat_slugs} Saturn slug mappings, "
        f"{count_dc_slugs} Dreamcast slug mappings, {count_sat_archives} Saturn archive mappings)"
    )

    # Once per DB: bring PS3 rows to the current hash scheme, so /titles and
    # /sync can stop re-hashing every PS3 save per request. In a worker
    # thread; until it finishes, those requests still re-hash as before.
    ps3_hash_task = asyncio.create_task(_migrate_ps3_hashes())

    # Load No-Intro / Redump DAT files for ROM normalization
    dats_dir.mkdir(exist_ok=True)
    dat_normalizer.init(dats_dir)

    # Load ROM catalog from cache (or scan if no cache)
    rom_scan_task = None
    rom_cleanup_task = None
    ra_index_task = None
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

        # Drop any roms.db rows that point at files which have since
        # disappeared from disk (user moved a ROM, renamed a folder, etc).
        # Fast — just stat() per row, no full filesystem walk.  Runs
        # once at startup and again every 24h via ``_periodic_rom_cleanup``.
        try:
            removed = rom_scanner.cleanup_missing()
            if removed:
                print(f"ROM catalog: removed {removed} stale row(s) (files gone)")
        except Exception:
            logger.exception("[rom_scanner] startup cleanup_missing failed")

        if settings.rom_scan_interval > 0:
            rom_scan_task = asyncio.create_task(_periodic_rom_scan())
        rom_cleanup_task = asyncio.create_task(_periodic_rom_cleanup())
        # Housekeeping after the catalog is up, never during the scan: the
        # RA pass reads every cartridge ROM once and would otherwise hold up
        # startup for minutes on a large library.
        ra_index_task = asyncio.create_task(library_maintenance(rom_catalog))

    yield

    _ra_stop.set()

    if rom_scan_task:
        rom_scan_task.cancel()
        try:
            await rom_scan_task
        except asyncio.CancelledError:
            pass
    if rom_cleanup_task:
        rom_cleanup_task.cancel()
        try:
            await rom_cleanup_task
        except asyncio.CancelledError:
            pass
    try:
        # Short: it is DB rows and small files, and a half-done pass is
        # finished by the next start (the flag is only set at the end).
        await asyncio.wait_for(asyncio.shield(ps3_hash_task), timeout=5)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        pass
    if ra_index_task:
        # Not cancelled: the pass runs in a worker thread and checks
        # ``_ra_stop`` between ROMs, so it exits on its own with a
        # consistent cache rather than mid-write.
        try:
            await asyncio.wait_for(ra_index_task, timeout=10)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass


_ra_stop = asyncio.Event()
_maintenance_lock = asyncio.Lock()


async def _migrate_ps3_hashes() -> None:
    from app.services import storage

    try:
        changed = await asyncio.to_thread(storage.migrate_ps3_hashes)
        if changed:
            logger.info("[storage] PS3 hash scheme: updated %d row(s)", changed)
    except Exception:
        logger.exception("[storage] PS3 hash migration failed; will retry next start")


async def library_maintenance(catalog) -> None:
    """After a scan: fix zstd CHDs, then refresh the RetroAchievements index.

    Serialised - a scan that lands while a pass is running waits for it
    rather than starting a second one over the same files.
    """
    if catalog is None:
        return
    async with _maintenance_lock:
        if settings.chd_recompress_zstd:
            from app.services import chd_normalize

            try:
                counts = await asyncio.to_thread(
                    chd_normalize.normalize,
                    chd_normalize.catalog_chds(catalog.list_all(), settings.rom_dir),
                    _ra_stop.is_set,
                )
                if counts["recompressed"]:
                    logger.info("[chd_normalize] re-compressed %d zstd CHD(s)", counts["recompressed"])
                    # Sizes changed on disk; let the catalog catch up
                    # before the RA pass reads it.
                    catalog = await asyncio.to_thread(rom_scanner.rescan) or catalog
            except Exception:
                logger.exception("[chd_normalize] pass failed")
        if settings.ra_enabled:
            await _ra_index_pass(catalog)


async def _ra_index_pass(catalog) -> None:
    """Hash the catalog for RetroAchievements in a worker thread.

    Everything in here is best-effort: no network, a bad key or an
    unreadable ROM all leave the catalog serving fine, just unbadged.
    """
    if catalog is None:
        return
    from app.services import ra_index

    try:
        result = await asyncio.to_thread(
            ra_index.refresh,
            catalog.list_all(),
            settings.save_dir / ".ra_cache",
            settings.ra_api_key,
            settings.ra_username,
            _ra_stop.is_set,
            200,
            settings.rom_dir,
        )
        if result.get("hashed"):
            logger.info(
                "[ra_index] %d ROM(s) hashed, %d known to RetroAchievements",
                result["hashed"], result["known"],
            )
    except Exception:
        logger.exception("[ra_index] indexing pass failed")


async def _periodic_rom_scan():
    interval = settings.rom_scan_interval
    try:
        # Scan immediately on startup so new files added while the server was
        # down are picked up without waiting for the first interval to elapse.
        try:
            catalog = rom_scanner.rescan()
            if catalog:
                logger.info(
                    "[rom_scanner] Startup scan: %d ROMs", len(catalog.entries)
                )
        except Exception:
            logger.exception("[rom_scanner] Startup scan failed")

        while True:
            await asyncio.sleep(interval)
            try:
                catalog = rom_scanner.rescan()
                if catalog:
                    logger.info(
                        "[rom_scanner] Periodic scan: %d ROMs", len(catalog.entries)
                    )
                    await library_maintenance(catalog)
            except Exception:
                logger.exception("[rom_scanner] Periodic scan failed")
    except asyncio.CancelledError:
        pass


async def _periodic_rom_cleanup():
    """Drop roms.db rows whose backing file vanished from disk.

    Cheap stat-only sweep; intentionally separate from the heavier
    ``_periodic_rom_scan`` so we can run it on a slow cadence (24h)
    without dragging the scan interval up.  Scans miss deletions only
    until their next interval anyway, but a daily targeted cleanup
    keeps the DB tidy even if the operator turned off ``rom_scan_interval``
    or set it very high.
    """
    DAY_SECONDS = 86400
    try:
        while True:
            await asyncio.sleep(DAY_SECONDS)
            try:
                removed = rom_scanner.cleanup_missing()
                if removed:
                    logger.info(
                        "[rom_scanner] Daily cleanup: removed %d row(s)", removed
                    )
            except Exception:
                logger.exception("[rom_scanner] Daily cleanup failed")
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
    app.include_router(catalog.router, prefix="/api/v1")

    return app


app = create_app()
