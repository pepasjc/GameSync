from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.config import settings
from app.middleware.auth import APIKeyMiddleware
from app.routes import saves, status, sync, titles, update
from app.services import game_names


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.save_dir.mkdir(parents=True, exist_ok=True)
    # Load game names databases (3DS, DS, PSP, PS Vita)
    data_dir = Path(__file__).parent.parent / "data"
    count_3ds = game_names.load_database(data_dir / "3dstdb.txt")
    count_ds = game_names.load_database(data_dir / "dstdb.txt")
    count_psp = game_names.load_database(data_dir / "pspdb.txt")
    count_vita = game_names.load_database(data_dir / "vitadb.txt")
    count_psx = game_names.load_database(data_dir / "psxdb.txt")
    count_psx += game_names.load_database(data_dir / "unsorted_psx.txt")
    print(
        f"Loaded {count_3ds} 3DS + {count_ds} DS + "
        f"{count_psp} PSP + {count_vita} Vita + {count_psx} PSX game names from database"
    )
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="3DS Save Sync", version="1.0.0", lifespan=lifespan)

    app.add_middleware(APIKeyMiddleware)

    app.include_router(status.router, prefix="/api/v1")
    app.include_router(titles.router, prefix="/api/v1")
    app.include_router(saves.router, prefix="/api/v1")
    app.include_router(sync.router, prefix="/api/v1")
    app.include_router(update.router, prefix="/api/v1")

    return app


app = create_app()
