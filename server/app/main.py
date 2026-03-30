from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.config import settings
from app.middleware.auth import APIKeyMiddleware
from app.routes import normalize, saves, status, sync, titles, update
from app.services import dat_normalizer, db, game_names


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.save_dir.mkdir(parents=True, exist_ok=True)
    db.init_db(settings.save_dir)

    data_dir = Path(__file__).parent.parent / "data"
    dats_dir = data_dir / "dats"

    # Load game names databases
    count_title_ids = game_names.load_database(data_dir / "3dstitledb.txt")
    count_wii = game_names.load_database(data_dir / "wiidb.txt")
    count_wii += game_names.load_libretro_dat_to_dicts(
        dats_dir / "Nintendo - GameCube.dat"
    )
    count_wii += game_names.load_libretro_dat_to_dicts(dats_dir / "Nintendo - Wii.dat")

    # Load libretro DATs (replace legacy .txt files for PS1/PSP/Vita/3DS/DS)
    # Retail DATs are loaded first (psn=False); PSN DATs second (psn=True) so
    # retail entries are never overwritten by their PSN equivalents.
    count_psx = game_names.load_libretro_dat_to_dicts(
        dats_dir / "Sony - PlayStation.dat"
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
        f"{count_psp} PSP + {count_vita} Vita + {count_psx} PSX + {count_wii} GC/Wii game names "
        f"({count_psn_retail} PSN→retail mappings)"
    )

    # Load No-Intro / Redump DAT files for ROM normalization
    dats_dir.mkdir(exist_ok=True)
    dat_normalizer.init(dats_dir)

    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Save Sync", version="1.0.0", lifespan=lifespan)

    app.add_middleware(APIKeyMiddleware)

    app.include_router(status.router, prefix="/api/v1")
    app.include_router(titles.router, prefix="/api/v1")
    app.include_router(saves.router, prefix="/api/v1")
    app.include_router(sync.router, prefix="/api/v1")
    app.include_router(update.router, prefix="/api/v1")
    app.include_router(normalize.router, prefix="/api/v1")

    return app


app = create_app()
