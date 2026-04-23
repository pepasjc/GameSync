"""Catalog endpoints — the single-source-of-truth resolver.

POST /api/v1/catalog/resolve
    Given whatever a client knows about a save (system + any of
    rom_filename / title_id / gamecode / serial / crc32 / sha1), return the
    canonical ``sync_id`` that every other client would produce for the same
    game.  This is the server-side equivalent of ``shared.sync_id.resolve()``
    with DAT-backed serial lookup wired in.

Why this exists
---------------
Different clients have different information at scan time:

  * The 3DS homebrew client knows the native 64-bit title_id.
  * The NDS homebrew client reads the 4-char gamecode from the ROM header.
  * The Steam Deck scanner has a save filename but may or may not have the
    ROM next to it.
  * The Android client sometimes has only the save filename.

All of them should converge on the same ``sync_id`` so a save uploaded from
one device is found when another device looks.  Rather than duplicate the
"which identifier for which system" logic in every client, each client can
POST whatever it knows to this endpoint and the server returns the canonical
form — backed by the shared ``SYNC_ID_RULES`` table and the server's DAT
corpus.

The resolver never fails; if nothing better is possible it returns a slug
form so the save is still addressable (with ``fallback: true`` so the client
can decide whether to warn the user).
"""

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services import dat_normalizer
from shared.sync_id import ResolveInput, resolve

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class ResolveRequest(BaseModel):
    """All fields except ``system`` are optional; the resolver uses whatever
    is provided and degrades gracefully.  At least one of ``rom_filename``,
    ``title_id``, ``gamecode`` or ``serial`` should be set for a useful
    result — otherwise the server returns a deterministic placeholder
    ``SYSTEM_unknown`` with ``fallback: true``.
    """

    system: str
    rom_filename: Optional[str] = None
    title_id: Optional[str] = None
    gamecode: Optional[str] = None
    serial: Optional[str] = None
    # CRC32 / SHA1 aren't used by the resolver directly yet but are accepted
    # so that the DAT serial lookup can prefer exact hash matches over slug
    # matches for ambiguous filenames.
    crc32: Optional[str] = None
    sha1: Optional[str] = None


class ResolveResponse(BaseModel):
    sync_id: str
    strategy: str = Field(
        ...,
        description=(
            "Which rule produced the sync_id: "
            "'title_id' | 'prefix_hex_serial' | 'serial' | 'slug'."
        ),
    )
    fallback: bool = Field(
        False,
        description=(
            "True when the primary strategy couldn't be applied and the "
            "resolver fell back to slug form.  Clients can use this to "
            "prompt the user to place the ROM next to the save."
        ),
    )
    canonical_name: Optional[str] = Field(
        None,
        description=(
            "The No-Intro / Redump canonical name, when the server could "
            "match the ROM in a DAT.  Useful for UI display even when the "
            "sync_id is a hex or serial form that isn't human-readable."
        ),
    )


class BatchResolveRequest(BaseModel):
    items: list[ResolveRequest]


class BatchResolveResponse(BaseModel):
    results: list[ResolveResponse]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def _resolve_one(req: ResolveRequest) -> ResolveResponse:
    """Turn a single request into a response, wiring the DAT lookup."""
    norm = dat_normalizer.get()

    # Build a DAT-backed serial lookup with CRC32 priority.  We close over
    # the request's CRC32 so the resolver's simple ``(system, filename) -> serial``
    # signature still gets hash-precision matching when available.
    def _serial_lookup(system: str, rom_filename: str) -> Optional[str]:
        if not norm:
            return None
        try:
            return norm.lookup_serial(system, rom_filename, req.crc32)
        except Exception:
            return None

    result = resolve(
        ResolveInput(
            system=req.system,
            rom_filename=req.rom_filename,
            title_id=req.title_id,
            gamecode=req.gamecode,
            serial=req.serial,
        ),
        serial_lookup=_serial_lookup,
    )

    # Enrich with a canonical display name if the DAT knows about this ROM.
    canonical_name: Optional[str] = None
    if norm and req.rom_filename:
        try:
            info = norm.normalize(req.system.upper(), req.rom_filename, req.crc32)
            canonical_name = info.get("canonical_name")
        except Exception:
            canonical_name = None

    return ResolveResponse(
        sync_id=result.sync_id,
        strategy=result.strategy,
        fallback=result.fallback,
        canonical_name=canonical_name,
    )


@router.post("/catalog/resolve", response_model=ResolveResponse)
async def resolve_one(req: ResolveRequest) -> ResolveResponse:
    """Resolve a single (system, hints...) tuple to its canonical sync_id.

    The response's ``strategy`` field tells the caller which rule fired:

      * ``title_id`` — native 16-char hex ID was accepted as-is (3DS).
      * ``prefix_hex_serial`` — NDS-style ``00048000`` + hex(gamecode).
      * ``serial`` — PS1/PS2/PSP/Vita/Saturn disc serial.
      * ``slug`` — ``SYSTEM_slug_region`` fallback.

    ``fallback: true`` means the primary strategy couldn't be applied (e.g.
    NDS save with no matching ROM on the server) and the resolver fell
    back to a slug so the save is still addressable.
    """
    return _resolve_one(req)


@router.post("/catalog/resolve/batch", response_model=BatchResolveResponse)
async def resolve_batch(req: BatchResolveRequest) -> BatchResolveResponse:
    """Resolve many at once — mirrors ``/normalize/batch`` shape so the
    desktop client can resolve every save in a single round-trip when
    scanning a new sync profile.
    """
    return BatchResolveResponse(
        results=[_resolve_one(item) for item in req.items]
    )
