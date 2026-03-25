"""ROM normalization endpoints.

POST /api/v1/normalize/batch
    Given a list of {system, filename, crc32?}, returns canonical No-Intro
    names and the correct title_id for each ROM.

GET  /api/v1/normalize/systems
    Lists which systems have DAT data loaded and their entry counts.
"""

from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from app.services import dat_normalizer
from app.services.rom_id import normalize_rom_name

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class RomEntry(BaseModel):
    system: str
    filename: str
    crc32: Optional[str] = None  # hex string, with or without padding


class NormalizeRequest(BaseModel):
    roms: list[RomEntry]


class NormalizeResult(BaseModel):
    system: str
    original_filename: str
    canonical_name: str
    title_id: str
    source: str  # "dat_crc32" | "dat_filename" | "filename"


class NormalizeResponse(BaseModel):
    results: list[NormalizeResult]


class SystemsResponse(BaseModel):
    systems: list[str]
    stats: dict


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/normalize/batch", response_model=NormalizeResponse)
async def normalize_batch(req: NormalizeRequest) -> NormalizeResponse:
    """Normalize ROM filenames to canonical No-Intro names.

    For each entry the server tries, in order:
      1. CRC32 exact lookup in the DAT (most accurate)
      2. Filename slug fuzzy match against DAT name index
      3. Plain filename normalization (strips region/revision tags)

    The returned `title_id` is ready to use for save sync — identical to what
    other clients (3DS, NDS, desktop) would generate for the same ROM.
    """
    norm = dat_normalizer.get()
    results: list[NormalizeResult] = []

    for entry in req.roms:
        sys_upper = entry.system.upper()

        if norm:
            info = norm.normalize(sys_upper, entry.filename, entry.crc32)
        else:
            # No DATs loaded — fall back to simple normalization
            stem = Path(entry.filename).stem
            slug = normalize_rom_name(stem)
            info = {"canonical_name": stem, "slug": slug, "source": "filename"}

        title_id = f"{sys_upper}_{info['slug']}"
        results.append(NormalizeResult(
            system=sys_upper,
            original_filename=entry.filename,
            canonical_name=info["canonical_name"],
            title_id=title_id,
            source=info["source"],
        ))

    return NormalizeResponse(results=results)


@router.get("/normalize/systems", response_model=SystemsResponse)
async def list_systems() -> SystemsResponse:
    """Return systems that have DAT data loaded, with entry counts."""
    norm = dat_normalizer.get()
    if not norm:
        return SystemsResponse(systems=[], stats={})
    return SystemsResponse(
        systems=norm.available_systems(),
        stats=norm.stats(),
    )
