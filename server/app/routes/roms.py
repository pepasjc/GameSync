"""ROM catalog endpoints.

GET  /api/v1/roms              — List all ROMs in catalog (with optional filters)
GET  /api/v1/roms/{title_id}   — Download a ROM file (with HTTP Range support)
                                  ?extract=cue  — CHD → CUE/BIN ZIP (PS1, Saturn, etc.)
                                  ?extract=gdi  — CHD → GDI ZIP (Dreamcast)
                                  ?extract=iso  — CHD → ISO (PSP)
                                  ?extract=cso  — CHD → CSO compressed image (PSP)
                                  ?extract=rvz  — RVZ → ISO (GameCube / Wii via DolphinTool)
                                  ?extract=cia  — 3DS cart image → decrypted CIA
                                                 (installable on CFW 3DS AND usable in emulators)
                                  ?extract=decrypted_cci
                                                 3DS cart image → decrypted CCI for emulators
                                  PS3 .iso files: streamed raw (RPCS3 mounts ISO directly).
                                  PS3 bundle (subfolder containing .pkg files): streamed as
                                  ZIP_STORED archive of every file in the subfolder.  Loose
                                  .pkg at <rom_dir>/ps3/ root are skipped — operators must
                                  drop PSN content into a per-game subfolder so the catalog
                                  can derive the game name + group .pkg + .rap files.
                                  Xbox .iso files: streamed raw or converted to CCI ZIP.
                                  Xbox CCI bundles: subfolder ZIP by default, or converted
                                  to ISO on demand.
GET  /api/v1/roms/{rom_id}/manifest
                              — Bundle file list (returns single-element list for non-bundle)
GET  /api/v1/roms/{rom_id}/file/{rel_path}
                              — Stream a single file out of a bundle (Range support).
                                Used by the PS3 client to route .pkg → /dev_hdd0/packages
                                and .rap → /dev_hdd0/exdata.
POST /api/v1/roms/scan         — Trigger rescan of ROM directory
GET  /api/v1/roms/systems      — List systems with ROMs and counts
"""

import asyncio
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from app.config import settings


def _conversion_tmp_dir() -> str | None:
    """Resolve where ``tempfile.mkdtemp`` should put conversion workdirs.

    Returns the configured ``settings.tmp_dir`` (creating it if needed) when
    set, otherwise ``None`` so ``mkdtemp`` falls back to its system default.

    Centralised here because (a) every conversion path needs the same
    treatment and (b) we can't rely on the ``TMPDIR`` environment variable —
    uv's bundled python-build-standalone interpreter strips ``TMPDIR`` on
    startup while leaving every other env var alone.
    """
    if settings.tmp_dir is None:
        return None
    try:
        settings.tmp_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        # If the configured dir can't be created (permissions, missing mount,
        # etc.), fall back to the system default rather than failing the
        # whole request — the server log already records the OSError.
        return None
    return str(settings.tmp_dir)


# ── Conversion output cache ─────────────────────────────────────────────────
#
# 3DS / GameCube / PSP / CHD conversions are slow (multi-minute CPU-bound
# decryption + decompression).  The same source ROM converted to the same
# format always produces the same output, so caching the result by
# (source_path, mtime, size, format) is a free 100% speedup on every
# repeat download.  On a Pi serving a developer who's testing the same
# game over and over, this is the difference between "instant" and
# "seven minutes per attempt."
#
# Cache layout:
#   <settings.tmp_dir>/_conversion_cache/<sha-prefix>_<stem><output_ext>
#
# The cache lives under ``settings.tmp_dir`` (auto-disabled when that's
# unset, mirroring tmpfs-fallback behaviour).  We never evict anything
# automatically — on a sane host with the tmp_dir on a multi-TB volume
# this is fine for years; if disk pressure ever becomes a concern, a
# cron/systemd-timer can prune oldest-mtime entries.

_CACHE_DIR_NAME = "_conversion_cache"


def _conversion_cache_dir() -> Path | None:
    """Persistent cache directory under ``settings.tmp_dir``.  Returns
    ``None`` when no tmp_dir is configured (caching disabled — every
    request goes through the full pipeline)."""
    tmp = _conversion_tmp_dir()
    if tmp is None:
        return None
    cache = Path(tmp) / _CACHE_DIR_NAME
    try:
        cache.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return cache


def _conversion_cache_key(source_path: Path, fmt: str) -> str:
    """Stable 16-char SHA-256 prefix of (absolute path, mtime_ns, size,
    format).  Including mtime + size means the cache invalidates
    automatically when the source ROM is replaced (e.g. user re-downloads
    a fresher dump), without any explicit cache-bust step."""
    st = source_path.stat()
    payload = f"{source_path.absolute()}|{st.st_mtime_ns}|{st.st_size}|{fmt}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _cached_output_path(source_path: Path, fmt: str, output_ext: str) -> Path | None:
    """Predicted cache path for this (source, fmt) — does NOT check
    existence.  Returns ``None`` when caching is disabled."""
    cache = _conversion_cache_dir()
    if cache is None:
        return None
    key = _conversion_cache_key(source_path, fmt)
    return cache / f"{source_path.stem}_{key}{output_ext}"


def _lookup_cached_output(source_path: Path, fmt: str, output_ext: str) -> Path | None:
    """Return path to a pre-computed conversion output if one exists,
    else ``None``.  Callers that get a hit can skip the entire
    converter pipeline and stream straight from the cached file."""
    candidate = _cached_output_path(source_path, fmt, output_ext)
    if candidate is None:
        return None
    return candidate if candidate.is_file() else None


def _save_to_cache(temp_output: Path, source_path: Path, fmt: str, output_ext: str) -> Path:
    """Move a fresh conversion output into the cache and return the new
    path.  When caching is disabled, returns the original path
    unchanged so callers always get a usable Path back.

    Move (not copy) avoids paying for a duplicate write on the slow
    (Pi + USB HDD) tier — ``shutil.move`` is a rename when source +
    destination are on the same filesystem, which is the common case
    when ``settings.tmp_dir`` is set.
    """
    cached_path = _cached_output_path(source_path, fmt, output_ext)
    if cached_path is None:
        return temp_output
    try:
        shutil.move(str(temp_output), str(cached_path))
        return cached_path
    except OSError:
        # If the move fails (cross-fs without permission, dest exists and
        # is busy, etc.), keep using the temp output — the response still
        # works, we just don't get the cache benefit on this request.
        return temp_output


def _conversion_cache_key_multi(source_paths: list[Path], fmt: str) -> str:
    """Cache key over an ordered set of source files (multi-disc PS1).

    Hashes each path's (absolute, mtime_ns, size) so the cache invalidates
    when any disc is replaced.  Discs are hashed in the order supplied
    because order matters for pop-fe (Disc 1 must be argv[0]) — passing
    the same set in a different order would legitimately produce a
    different PBP and so should miss the cache.
    """
    parts: list[str] = []
    for p in source_paths:
        st = p.stat()
        parts.append(f"{p.absolute()}|{st.st_mtime_ns}|{st.st_size}")
    payload = '\n'.join(parts) + f"|{fmt}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _cached_output_by_key(label: str, key: str, output_ext: str) -> Path | None:
    cache = _conversion_cache_dir()
    if cache is None:
        return None
    return cache / f"{label}_{key}{output_ext}"


def _lookup_cached_by_key(label: str, key: str, output_ext: str) -> Path | None:
    candidate = _cached_output_by_key(label, key, output_ext)
    if candidate is None:
        return None
    return candidate if candidate.is_file() else None


def _save_to_cache_by_key(temp_output: Path, label: str, key: str, output_ext: str) -> Path:
    cached = _cached_output_by_key(label, key, output_ext)
    if cached is None:
        return temp_output
    try:
        shutil.move(str(temp_output), str(cached))
        return cached
    except OSError:
        return temp_output


# Chunk size for streamed Range responses. 1 MiB is a good balance between
# syscall overhead and keeping memory bounded — a single in-flight request
# never holds more than this much in RAM at a time, so 4GB ROMs over slow
# WAN links cost ~1MB of process memory regardless of file size.
_STREAM_CHUNK = 1 << 20
from app.services import rom_scanner

router = APIRouter()

# ── System classification ────────────────────────────────────────────────────

# CD-ROM systems extracted to CUE/BIN zip
_CUE_SYSTEMS = frozenset({
    'PSX', 'PS1',
    'SAT',
    'SCD', 'MEGACD',
    'PCECD', 'PCENGINECD', 'TG16CD',
    '3DO',
    'PCFX',
    # Neo Geo CD: scanner emits ``NEOCD`` (canonical), some older
    # configs / external clients use ``NGCD`` or ``NEOGEOCD`` — list
    # all three so the extract path matches regardless of which alias
    # arrived.  Same pattern for Atari Jaguar CD below.
    'NEOCD', 'NGCD', 'NEOGEOCD',
    'AMIGACD32',
    'JAGCD', 'JAGUARCD',
    # PS2 is dual-media — DVDs go through the ISO extract path, CDs
    # through this CUE/BIN path.  Listing PS2 here gates the CD extract
    # handler; the actual disc-vs-DVD decision per game is made in
    # ``_extract_formats_for_entry`` using the DAT.
    'PS2',
})

# Dreamcast uses GDI format
_GDI_SYSTEMS = frozenset({'DC', 'DREAMCAST'})

# PSP uses its own ISO/CSO pipeline
_PSP_SYSTEMS = frozenset({'PSP'})

# PS2 uses chdman's ``extractdvd`` subcommand for DVD CHDs (single ISO output).
# PS2 CDs continue to flow through ``_extract_cd`` like other CD-ROM systems.
_PS2_SYSTEMS = frozenset({'PS2'})

# GameCube / Wii use RVZ (Dolphin compressed) — convert with DolphinTool
_GC_SYSTEMS = frozenset({'GC', 'WII'})

# Xbox / Xbox 360 disc images.  The server exposes exactly two user-facing
# variants for Xbox catalog rows: CCI and ISO.  CCI source folders are served
# as ZIPs because the .cci image can travel with attach launchers; ISO source
# files are streamed raw unless the user requests CCI conversion.
_XBOX_SYSTEMS = frozenset({'XBOX', 'X360', 'XBOX360'})
_XBOX_DISC_EXTENSIONS = frozenset({'.cci', '.iso'})

# 3DS cartridge images can be converted to CIA variants
_3DS_SYSTEMS = frozenset({'3DS'})
_3DS_CART_EXTENSIONS = frozenset({'.3ds', '.cci'})
# Output filenames preserve the original ROM stem — only the extension changes.
# Two formats only: a decrypted CIA (which is also installable on CFW 3DS
# hardware, so one button covers both use-cases) and a decrypted CCI for
# emulators that prefer that format.
_3DS_EXTRACT_SPECS = {
    'cia': {
        'setting': 'rom_3ds_cia_command',
        'env': 'SYNC_ROM_3DS_CIA_COMMAND',
        'label': 'CIA',
        'output_ext': '.cia',
    },
    'decrypted_cci': {
        'setting': 'rom_3ds_decrypted_cci_command',
        'env': 'SYNC_ROM_3DS_DECRYPTED_CCI_COMMAND',
        'label': 'decrypted CCI',
        'output_ext': '.cci',
    },
}
_3DS_EXTRACT_FORMATS = list(_3DS_EXTRACT_SPECS.keys())

# Xbox conversion specs — same shape as the 3DS table so the shared
# ``_expand_command_template`` / cache machinery handles them without
# special-casing.  XGDTool's CLI can create ``--xiso`` and ``--cci`` outputs
# from the opposite source format; the server wraps CCI outputs in a ZIP for
# consistent WebUI downloads.
_XBOX_EXTRACT_SPECS = {
    'cci': {
        'setting': 'rom_xbox_cci_command',
        'env': 'SYNC_ROM_XBOX_CCI_COMMAND',
        'label': 'CCI ZIP',
        'output_ext': '.zip',
        'tool_output_ext': '.cci',
        'mime': 'application/zip',
    },
    'iso': {
        'setting': 'rom_xbox_iso_command',
        'env': 'SYNC_ROM_XBOX_ISO_COMMAND',
        'label': 'ISO',
        'output_ext': '.iso',
        'tool_output_ext': '.iso',
        'mime': 'application/x-iso9660-image',
    },
}
_XBOX_EXTRACT_FORMATS = list(_XBOX_EXTRACT_SPECS.keys())

# PS1 → PSP EBOOT.PBP conversion (popstation-style).  Used by the PSP
# client's ROM Catalog so PS1 games convert into a PBP installable
# under ms0:/PSP/GAME/<id>/.  Spec mirrors the 3DS/Xbox layout so the
# templated command runner + cache code paths handle it without special
# casing.  Output is a single .pbp file served raw (Content-Type
# application/octet-stream) — no zip, no archive, the client writes it
# directly as EBOOT.PBP at the target path.
_PS1_EBOOT_SYSTEMS = frozenset({'PS1', 'PSX'})
_PS1_EBOOT_SPEC = {
    'setting': 'rom_ps1_eboot_command',
    'env': 'SYNC_ROM_PS1_EBOOT_COMMAND',
    'label': 'PSP EBOOT.PBP',
    'output_ext': '.pbp',
    'mime': 'application/octet-stream',
}

# All systems that support any CHD extraction
_CD_SYSTEMS   = _CUE_SYSTEMS | _GDI_SYSTEMS
_ALL_EXTRACT  = _CD_SYSTEMS | _PSP_SYSTEMS


# ── List endpoint ────────────────────────────────────────────────────────────

@router.get("/roms")
async def list_roms(
    system: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    has_save: Optional[bool] = Query(None),
    limit: Optional[int] = Query(None, ge=1, le=20000),
    offset: int = Query(0, ge=0),
):
    catalog = rom_scanner.get()
    if not catalog:
        return {"roms": [], "total": 0, "offset": 0, "limit": limit, "has_more": False}

    entries = catalog.list_all()

    if system:
        sys_upper = system.upper()
        entries = [e for e in entries if e.system == sys_upper]

    if search:
        term = search.lower()
        entries = [e for e in entries if term in e.name.lower() or term in e.filename.lower()]

    if has_save is not None:
        from app.services import storage
        if has_save:
            entries = [e for e in entries if storage.title_exists(e.title_id)]
        else:
            entries = [e for e in entries if not storage.title_exists(e.title_id)]

    # `total` is always the full filtered count — essential for the client to
    # know whether to page further or show a "showing X of Y" hint.
    total = len(entries)

    # Multi-disc PS1 grouping must be computed over the full filtered set
    # before pagination — otherwise a multi-disc game split across page
    # boundaries would report wrong disc_total values.  Maps rom_id →
    # (disc_index, disc_total, primary_rom_id).  Single-disc games get
    # the trivial (1, 1, rom_id) tuple.
    ps1_disc_meta = _ps1_compute_disc_groups(entries)

    if limit is not None:
        page = entries[offset : offset + limit]
        has_more = (offset + len(page)) < total
    else:
        page = entries[offset:] if offset else entries
        has_more = False

    result = []
    for e in page:
        d = e.to_dict()
        extract_format, extract_formats = _extract_formats_for_entry(e)
        if extract_format:
            d['extract_format'] = extract_format
        if extract_formats:
            d['extract_formats'] = extract_formats
        meta = ps1_disc_meta.get(e.rom_id)
        if meta is not None:
            d['disc_index'], d['disc_total'], d['primary_rom_id'] = meta
        result.append(d)

    return {
        "roms": result,
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": has_more,
    }


def _ps1_compute_disc_groups(entries) -> dict[str, tuple[int, int, str]]:
    """Group PS1 entries by ``title_id`` to surface multi-disc info to
    clients.  Returns a map keyed on ``rom_id``; PS1 entries get
    ``(disc_index, disc_total, primary_rom_id)``, non-PS1 entries are
    omitted (clients only branch on PS1).

    Single-disc PS1 games still get an entry with ``(1, 1, rom_id)`` so
    the PSP client can use the field's presence as a "this is a PS1
    game" flag without an extra system check.
    """
    groups: dict[str, list] = {}
    for e in entries:
        if (e.system or '').upper() not in _PS1_EBOOT_SYSTEMS:
            continue
        if not e.title_id:
            continue
        groups.setdefault(e.title_id, []).append(e)

    meta: dict[str, tuple[int, int, str]] = {}
    for group in groups.values():
        group.sort(key=lambda e: (_ps1_disc_number(e.filename), e.filename))
        total = len(group)
        primary_rom_id = group[0].rom_id
        for idx, e in enumerate(group, 1):
            meta[e.rom_id] = (idx, total, primary_rom_id)
    return meta


# ── Misc endpoints ───────────────────────────────────────────────────────────

@router.get("/roms/systems")
async def list_systems():
    catalog = rom_scanner.get()
    if not catalog:
        return {"systems": [], "stats": {}}
    return {"systems": catalog.systems(), "stats": catalog.stats()}


@router.get("/roms/share-link")
async def create_share_link(
    path: str = Query(..., description="Path to share, e.g. /api/v1/roms/SLUS00922"),
    ttl_days: int = Query(7, ge=1, le=30),
):
    """Mint an HMAC-signed share URL for a download path.

    The returned URL embeds a short-lived token (default 7 days) and
    can be pasted into a browser by anyone — no API key needed.  The
    token is bound to the exact path, so it can't be replayed against
    another ROM / save.  Rotating ``settings.api_key`` invalidates
    every previously-issued share link.

    Allow-listed prefixes only (``/api/v1/roms/<id>``,
    ``/api/v1/saves/<id>``); admin-shaped sub-routes (scan / systems)
    are rejected so a leaked token can't escalate.
    """
    from app.services import share_token

    # Strip any pre-existing query string the caller might have left on
    # the path (the WebUI sometimes builds these from a download URL
    # that already had ``?api_key=...``).  We sign the bare path only.
    bare_path = path.split("?", 1)[0]

    if not share_token.is_shareable_path(bare_path):
        return JSONResponse(
            status_code=400,
            content={
                "detail": (
                    "Path is not shareable.  Only individual ROM and save "
                    "download paths can be shared."
                )
            },
        )

    token, expires = share_token.make(bare_path, ttl_seconds=ttl_days * 86400)
    return {
        "path": bare_path,
        "token": token,
        "expires_at": expires,
        "url": f"{bare_path}?token={token}",
    }


@router.get("/roms/scan")
async def trigger_scan(request: Request, use_crc32: bool = Query(False)):
    # Only admin users may trigger a rescan
    from app.config import settings as _settings
    remote_user = request.headers.get("X-Remote-User", "")
    is_admin = (not remote_user) or (remote_user in _settings.admin_users_set)
    if not is_admin:
        return JSONResponse(status_code=403, content={"detail": "Admin access required"})

    catalog = rom_scanner.rescan(use_crc32=use_crc32)
    if not catalog:
        return {"status": "no_rom_dir", "count": 0}
    return {"status": "ok", "count": len(catalog.entries)}


# ── ROM bundle helpers ───────────────────────────────────────────────────────
#
# A bundle entry corresponds to ``<rom_dir>/<system>/<subfolder>/`` containing
# multiple files that must travel together (PS3 .pkg + .rap, Xbox .cci +
# launchers, PS1 cue/bin folders, etc.).  The catalog row's ``path`` field
# stores the relative subfolder so the routes below can find the on-disk
# source without a second DB lookup.


def _bundle_dir_for(entry, rom_dir: Path) -> Path | None:
    if not getattr(entry, 'is_bundle', False):
        return None
    bundle_dir = (rom_dir / entry.path).resolve()
    rom_dir_resolved = rom_dir.resolve()
    # Guard against a malicious or stale catalog row pointing outside the
    # rom_dir tree (e.g. an absolute path or "../").  rom_dir_resolved must
    # be a strict prefix of bundle_dir.
    try:
        bundle_dir.relative_to(rom_dir_resolved)
    except ValueError:
        return None
    if not bundle_dir.is_dir():
        return None
    return bundle_dir


def _bundle_manifest_files(entry) -> list[dict]:
    """Return the [{name, size}] list stored in the catalog row.

    Falls through to a live filesystem walk when the row didn't carry the
    list (older catalog rows or a manual rescan glitch).  Filtering matches
    the scanner's keep-list so we never advertise a file the bundle ZIP
    wouldn't ship.
    """
    files = getattr(entry, 'bundle_files', None) or []
    if files:
        return list(files)

    rom_dir = settings.rom_dir
    if rom_dir is None:
        return []
    bundle_dir = _bundle_dir_for(entry, rom_dir)
    if bundle_dir is None:
        return []

    sys_up = (getattr(entry, 'system', '') or '').upper()
    out: list[dict] = []
    for f in sorted(bundle_dir.rglob('*')):
        if not f.is_file():
            continue
        if f.name.lower() in {'metadata.txt', 'systeminfo.txt'}:
            continue
        if sys_up in _XBOX_SYSTEMS:
            keep = True
        else:
            ext = f.suffix.lower()
            keep = (
                ext == '.pkg'
                or ext in {'.rap', '.edat'}
                or ext in {'.iso', '.pbp', '.cci', '.xbe'}
            )
            if not keep:
                continue
        out.append({
            'name': f.relative_to(bundle_dir).as_posix(),
            'size': f.stat().st_size,
        })
    return out


def _safe_archive_stem(value: str, fallback: str = "bundle") -> str:
    return re.sub(r'[^A-Za-z0-9_.\- ]+', '_', value).strip('_ ') or fallback


def _xbox_bundle_cci_path(entry, bundle_dir: Path) -> Path | None:
    files = _bundle_manifest_files(entry)
    cci_files = [
        bundle_dir / f['name']
        for f in files
        if Path(str(f.get('name', ''))).suffix.lower() == '.cci'
    ]
    cci_files = [p for p in cci_files if p.is_file()]
    if not cci_files:
        # Stale manifest fallback: scan the directory directly.
        cci_files = sorted(p for p in bundle_dir.rglob('*.cci') if p.is_file())
    if not cci_files:
        return None
    # Libraries should have one CCI per game folder. If an operator drops
    # multiple parts in there, choosing the largest is the least surprising
    # deterministic fallback for the ISO conversion path.
    return max(cci_files, key=lambda p: p.stat().st_size)


async def _serve_single_file_zip(
    source_path: Path,
    zip_stem: str,
    arcname: str | None = None,
    cache_source: Path | None = None,
    cache_fmt: str | None = None,
) -> Response:
    """Wrap a single file in a ZIP_STORED archive and stream it."""
    output_ext = '.zip'
    if cache_source is not None and cache_fmt:
        cached = _lookup_cached_output(cache_source, cache_fmt, output_ext)
        if cached is not None:
            return _stream_file_response(cached, 'application/zip')

    tmpdir = tempfile.mkdtemp(prefix='single_file_zip_', dir=_conversion_tmp_dir())
    safe_stem = _safe_archive_stem(zip_stem)
    zip_path = Path(tmpdir) / f'{safe_stem}.zip'
    member_name = arcname or source_path.name

    def _build_zip() -> Path:
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_STORED, allowZip64=True) as zf:
            zf.write(source_path, arcname=member_name)
        return zip_path

    try:
        out_path = await asyncio.get_event_loop().run_in_executor(None, _build_zip)
    except Exception as exc:  # pragma: no cover — disk error path
        _cleanup_dir(tmpdir)
        return Response(status_code=500, content=f"ZIP failed: {exc}")

    if cache_source is not None and cache_fmt:
        out_path = _save_to_cache(out_path, cache_source, cache_fmt, output_ext)
    return _stream_file_response(out_path, 'application/zip', cleanup_dir=tmpdir)


async def _serve_bundle_zip(entry, bundle_dir: Path) -> Response:
    """Return a ZIP_STORED archive of every file in the bundle.

    PSN packages are already incompressible (encrypted blobs), so deflate
    just wastes CPU.  ZIP_STORED also lets us advertise an exact
    Content-Length without re-archiving every request: the ZIP overhead is
    fixed (30 byte local header + 46 byte central directory entry per
    member, plus 22 byte EOCD).  We materialise the ZIP to a tempfile so
    Starlette can stream it with proper Range support.
    """
    files = _bundle_manifest_files(entry)
    if not files:
        return Response(status_code=404, content="Bundle is empty on disk")

    tmpdir = tempfile.mkdtemp(prefix='rom_bundle_', dir=_conversion_tmp_dir())
    safe_stem = _safe_archive_stem(entry.name)
    zip_path = Path(tmpdir) / f'{safe_stem}.zip'

    def _build_zip() -> Path:
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_STORED, allowZip64=True) as zf:
            for f in files:
                src = bundle_dir / f['name']
                if not src.is_file():
                    continue
                zf.write(src, arcname=f['name'])
        return zip_path

    try:
        out_path = await asyncio.get_event_loop().run_in_executor(None, _build_zip)
    except Exception as exc:  # pragma: no cover — disk error path
        _cleanup_dir(tmpdir)
        return Response(status_code=500, content=f"Bundle ZIP failed: {exc}")

    return _stream_file_response(out_path, 'application/zip', cleanup_dir=tmpdir)


# ── Bundle-specific endpoints ───────────────────────────────────────────────
#
# Both routes are mounted BEFORE the catch-all download route below so the
# FastAPI matcher resolves them first.  ``rom_key`` here is always a single
# segment because ``download_rom`` already URL-escapes it on the way out;
# bundle ids never contain slashes.


@router.get("/roms/{rom_key}/manifest")
async def rom_manifest(rom_key: str):
    """Return the file list for a bundle entry.

    Single-file ROMs return a 1-element manifest with the on-disk name +
    size, so the desktop / steamdeck / PS3 client can use one code path
    regardless of bundle-ness.
    """
    catalog = rom_scanner.get()
    if not catalog:
        return JSONResponse(status_code=404, content={"detail": "no catalog"})

    entry = catalog.get(rom_key)
    if not entry:
        for e in catalog.list_all():
            if e.title_id == rom_key:
                entry = e
                break
    if not entry:
        return JSONResponse(status_code=404,
                            content={"detail": f"ROM not found: {rom_key}"})

    rom_dir = settings.rom_dir
    if rom_dir is None:
        return JSONResponse(status_code=404,
                            content={"detail": "ROM directory not configured"})

    if getattr(entry, 'is_bundle', False):
        files = _bundle_manifest_files(entry)
        return {
            "rom_id": entry.rom_id,
            "is_bundle": True,
            "name": entry.name,
            "system": entry.system,
            "total_size": entry.size,
            "files": files,
        }

    file_path = rom_dir / entry.path
    if not file_path.is_file():
        return JSONResponse(status_code=404,
                            content={"detail": "ROM file missing on disk"})
    return {
        "rom_id": entry.rom_id,
        "is_bundle": False,
        "name": entry.name,
        "system": entry.system,
        "total_size": file_path.stat().st_size,
        "files": [{"name": entry.filename, "size": file_path.stat().st_size}],
    }


@router.api_route("/roms/{rom_key}/file/{rel_path:path}",
                  methods=["GET", "HEAD"])
async def download_bundle_file(
    rom_key: str,
    rel_path: str,
    range_header: Optional[str] = Header(None, alias="Range"),
):
    """Serve a single file out of a PS3 bundle.

    Used by the PS3 client so it can route .pkg files to /dev_hdd0/packages
    and .rap files to /dev_hdd0/exdata without downloading a ZIP first.
    Each file uses Range so very large packages can resume across sessions
    on the slow PS3 connection.
    """
    catalog = rom_scanner.get()
    if not catalog:
        return Response(status_code=404, content="No ROM catalog available")

    entry = catalog.get(rom_key)
    if not entry:
        for e in catalog.list_all():
            if e.title_id == rom_key:
                entry = e
                break
    if not entry:
        return Response(status_code=404, content=f"ROM not found: {rom_key}")
    if not getattr(entry, 'is_bundle', False):
        return Response(status_code=400,
                        content="ROM is not a bundle; use /roms/<rom_id>")

    rom_dir = settings.rom_dir
    if rom_dir is None:
        return Response(status_code=404, content="ROM directory not configured")

    bundle_dir = _bundle_dir_for(entry, rom_dir)
    if bundle_dir is None:
        return Response(status_code=404,
                        content="Bundle directory missing on disk")

    # Path traversal guard: refuse anything that escapes the bundle dir
    # after resolution (PSL1GHT / curl / our own clients all send normal
    # POSIX paths so we don't need URL-decode magic here).
    requested = (bundle_dir / rel_path).resolve()
    try:
        requested.relative_to(bundle_dir.resolve())
    except ValueError:
        return Response(status_code=400, content="Bad bundle file path")

    if not requested.is_file():
        return Response(status_code=404,
                        content=f"Bundle file not found: {rel_path}")

    file_size = requested.stat().st_size
    content_type = _content_type(requested.name)

    if range_header:
        return _serve_range(requested, file_size, content_type, range_header)
    return _serve_full(requested, file_size, content_type)


# ── Download endpoint ────────────────────────────────────────────────────────

@router.api_route("/roms/{rom_key:path}", methods=["GET", "HEAD"])
async def download_rom(
    rom_key: str,
    request: Request,
    extract: Optional[str] = Query(
        None,
        description=(
            "Extract format: 'cue' (CUE/BIN zip), 'gdi' (GDI zip), "
            "'iso' (PSP ISO), 'cso' (PSP compressed ISO), "
            "'cia' (3DS decrypted CIA, also installable on CFW hardware), "
            "'decrypted_cci' (3DS decrypted CCI for emulators), "
            "'cci' (Xbox CCI ZIP), 'iso' (Xbox ISO)"
        ),
    ),
    range_header: Optional[str] = Header(None, alias="Range"),
):
    catalog = rom_scanner.get()
    if not catalog:
        return Response(status_code=404, content="No ROM catalog available")

    # The catalog is keyed by `rom_id` (always unique). For most ROMs
    # rom_id == title_id, so plain title_id lookups still resolve. But
    # multi-variant titles (e.g. Saturn ROM hacks sharing a Saturn product
    # code, or multi-disc games whose disc index is stripped from the
    # serial) collide on title_id and only the rom_id is unique. Older
    # clients / deep links may still send the title_id; fall back to the
    # first entry whose title_id matches so they keep working, while new
    # clients should send rom_id directly.
    entry = catalog.get(rom_key)
    if not entry:
        for e in catalog.list_all():
            if e.title_id == rom_key:
                entry = e
                break
    if not entry:
        return Response(status_code=404, content=f"ROM not found: {rom_key}")

    rom_dir = settings.rom_dir
    if not rom_dir:
        return Response(status_code=404, content="ROM directory not configured")

    sys_up = (entry.system or '').upper()

    # Bundle entry → /<system>/<subfolder> on disk holds many files.  Serve
    # the whole subfolder as a ZIP by default.  Xbox bundles additionally
    # support ``?extract=iso`` by converting the source .cci inside the
    # bundle, while ``?extract=cci`` remains the bundle ZIP.
    if getattr(entry, 'is_bundle', False):
        bundle_dir = _bundle_dir_for(entry, rom_dir)
        if bundle_dir is None:
            return Response(status_code=404,
                            content="Bundle directory not found on disk")
        if extract:
            fmt = extract.lower()
            if sys_up in _XBOX_SYSTEMS and fmt in _XBOX_EXTRACT_SPECS:
                if fmt == 'cci':
                    return await _serve_bundle_zip(entry, bundle_dir)
                cci_path = _xbox_bundle_cci_path(entry, bundle_dir)
                if cci_path is None:
                    return Response(
                        status_code=404,
                        content="Xbox CCI bundle has no .cci file",
                    )
                return await _extract_xbox(
                    cci_path, sys_up, fmt, display_stem=entry.name
                )
        return await _serve_bundle_zip(entry, bundle_dir)

    file_path = rom_dir / entry.path
    if not file_path.is_file():
        return Response(status_code=404, content="ROM file not found on disk")

    if extract:
        fmt = extract.lower()
        if sys_up in _XBOX_SYSTEMS and fmt in _XBOX_EXTRACT_SPECS:
            # Xbox CCI/ISO conversions go through the templated command
            # runner or a no-op direct stream when the source already
            # matches the requested target.
            return await _extract_xbox(file_path, sys_up, fmt)
        elif fmt in _3DS_EXTRACT_SPECS:
            return await _extract_3ds(file_path, sys_up, fmt)
        elif fmt == 'eboot' and sys_up in _PS1_EBOOT_SYSTEMS:
            # PS1 → PSP EBOOT.PBP via pop-fe; check before the generic
            # CUE/BIN ``_extract_cd`` branch so PS1 doesn't silently
            # fall through to the wrong handler.  ``entry`` is required
            # so the handler can look up sibling discs (multi-disc
            # games) by shared ``title_id``.
            return await _extract_ps1_eboot(file_path, sys_up, entry)
        elif fmt == 'rvz' or (fmt == 'iso' and file_path.suffix.lower() == '.rvz'):
            return await _extract_rvz(file_path, file_path.stem)
        elif fmt == 'iso' and sys_up in _PS2_SYSTEMS:
            # PS2 DVD path — chdman ``extractdvd`` produces a raw ISO.
            return await _extract_ps2_iso(file_path, file_path.stem)
        elif fmt in ('iso', 'cso'):
            return await _extract_psp(file_path, sys_up, file_path.stem, fmt)
        else:
            # 'cue', 'gdi', or legacy 'true'
            return await _extract_cd(file_path, sys_up)

    file_size = file_path.stat().st_size
    content_type = _content_type(file_path.name)

    if range_header:
        return _serve_range(file_path, file_size, content_type, range_header)
    return _serve_full(file_path, file_size, content_type)


# ── Extract helpers — common cleanup + streaming ────────────────────────────
#
# All extract endpoints used to call `output.read_bytes()` and return the
# whole result as `Response(content=bytes)`. For a 4GB Wii ISO that means
# 4GB of Python heap allocation on a Raspberry Pi — guaranteed OOM.
#
# The new pattern:
#   1. Create a tempdir *manually* (not a context manager) so it survives
#      past the request handler.
#   2. Run the conversion blocking in an executor as before.
#   3. Return FileResponse(path=...) so Starlette streams from disk.
#   4. Attach a BackgroundTask that wipes the tempdir after the response
#      body has been fully sent.
#
# This keeps RAM bounded to ~1 MB per request regardless of output size,
# and lets nginx pass bytes through as fast as the client can consume them.

def _cleanup_dir(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _stream_file_response(
    file_path: Path,
    media_type: str,
    cleanup_dir: str | None = None,
) -> FileResponse:
    """Return a streaming FileResponse, with optional tempdir cleanup."""
    background = BackgroundTask(_cleanup_dir, cleanup_dir) if cleanup_dir else None
    return FileResponse(
        path=file_path,
        media_type=media_type,
        filename=file_path.name,
        headers={'Content-Disposition': f'attachment; filename="{file_path.name}"'},
        background=background,
    )


# ── GameCube / Wii RVZ → ISO ─────────────────────────────────────────────────

async def _extract_rvz(rvz_path: Path, stem: str) -> Response:
    """Convert a Dolphin RVZ compressed disc image to a plain ISO."""
    if rvz_path.suffix.lower() != '.rvz':
        return Response(status_code=400, content="Only RVZ files can be converted with this endpoint")

    dolphin_tool = (
        shutil.which('DolphinTool')
        or shutil.which('dolphin-tool')
        or (Path('/usr/games/dolphin-tool').is_file() and '/usr/games/dolphin-tool')
    )
    if not dolphin_tool:
        return Response(
            status_code=503,
            content=(
                "DolphinTool not found. Install Dolphin emulator and ensure DolphinTool "
                "is on PATH (Linux: dolphin-tool, Windows: DolphinTool.exe)."
            ),
        )

    # Cache fast-path
    cached = _lookup_cached_output(rvz_path, 'rvz', '.iso')
    if cached is not None:
        return _stream_file_response(cached, 'application/x-iso9660-image')

    tmpdir = tempfile.mkdtemp(prefix='rvz_extract_', dir=_conversion_tmp_dir())
    iso_path = Path(tmpdir) / (stem + '.iso')

    def _run() -> None:
        r = subprocess.run(
            [dolphin_tool, 'convert', '-f', 'iso', '-i', str(rvz_path), '-o', str(iso_path)],
            capture_output=True, text=True, timeout=600,
        )
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip() or r.stdout.strip() or 'DolphinTool failed')

    try:
        await asyncio.get_event_loop().run_in_executor(None, _run)
    except RuntimeError as exc:
        _cleanup_dir(tmpdir)
        return Response(status_code=500, content=f"Conversion failed: {exc}")
    except subprocess.TimeoutExpired:
        _cleanup_dir(tmpdir)
        return Response(status_code=504, content="Conversion timed out (>10 min)")

    if not iso_path.is_file():
        _cleanup_dir(tmpdir)
        return Response(status_code=500, content="Conversion completed but produced no ISO")

    cached_path = _save_to_cache(iso_path, rvz_path, 'rvz', '.iso')
    return _stream_file_response(cached_path, 'application/x-iso9660-image', cleanup_dir=tmpdir)


# ── Nintendo 3DS cart image → CIA / CCI variants ────────────────────────────

async def _extract_3ds(source_path: Path, system: str, fmt: str) -> Response:
    """Convert a 3DS cart image (optionally wrapped in ZIP) to another format."""
    if system not in _3DS_SYSTEMS:
        return Response(
            status_code=400,
            content=f"{fmt} extraction is only supported for Nintendo 3DS ROMs (got {system})",
        )

    spec = _3DS_EXTRACT_SPECS.get(fmt)
    if spec is None:
        return Response(status_code=400, content=f"Unsupported 3DS extract format: {fmt}")

    # Cache fast-path: an identical conversion completed before for this
    # exact source ROM (matched on path + mtime + size + format) — stream
    # the cached output directly without re-running the slow converter.
    cached = _lookup_cached_output(source_path, fmt, spec['output_ext'])
    if cached is not None:
        return _stream_file_response(cached, 'application/x-3ds-rom')

    command_template = getattr(settings, spec['setting'])
    if not command_template:
        return Response(
            status_code=503,
            content=(
                f"3DS {spec['label']} conversion is not configured on the server.\n"
                f"\n"
                f"To enable this conversion, set {spec['env']} to a command template that "
                f"reads {{input}} (a .3ds / .cci cart image) and writes the output to {{output}} "
                f"(a {spec['output_ext']} file).\n"
                f"\n"
                f"On Raspberry Pi / Linux, run the bundled installer from the repo root to set up "
                f"the full 3DS conversion toolchain (CIA, decrypted CCI):\n"
                f"    ./install-3ds-rom-tools-rpi.sh\n"
                f"It prints the exact SYNC_ROM_3DS_* lines to paste into your server/.env file."
            ),
        )

    tmpdir = tempfile.mkdtemp(prefix='3ds_extract_', dir=_conversion_tmp_dir())

    def _run() -> Path:
        tmp = Path(tmpdir)
        input_path, stem = _materialize_3ds_source(source_path, tmp)
        # Preserve the original ROM stem verbatim — only the extension changes.
        output_name = f"{stem}{spec['output_ext']}"
        output_path = tmp / output_name

        cmd = _expand_command_template(
            command_template,
            input=str(input_path),
            output=str(output_path),
            output_dir=str(tmp),
            stem=stem,
        )
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=1800,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or '3DS conversion failed')

        final_path = output_path if output_path.is_file() else _find_single_output(tmp, spec['output_ext'])
        if final_path is None or not final_path.is_file():
            raise RuntimeError(
                f"converter completed but did not produce a {spec['output_ext']} file"
            )
        return final_path

    try:
        final_path = await asyncio.get_event_loop().run_in_executor(None, _run)
    except RuntimeError as exc:
        _cleanup_dir(tmpdir)
        return Response(status_code=500, content=f"Conversion failed: {exc}")
    except subprocess.TimeoutExpired:
        _cleanup_dir(tmpdir)
        return Response(status_code=504, content="Conversion timed out (>30 min)")
    except zipfile.BadZipFile:
        _cleanup_dir(tmpdir)
        return Response(status_code=400, content="Invalid ZIP archive")
    except ValueError as exc:
        _cleanup_dir(tmpdir)
        return Response(status_code=400, content=str(exc))

    # Promote the converted output into the persistent cache so the next
    # request for this exact source+format gets the instant fast-path.
    # On the same filesystem, ``shutil.move`` is just a rename — zero
    # extra I/O cost on top of the conversion we already did.
    cached_path = _save_to_cache(final_path, source_path, fmt, spec['output_ext'])
    return _stream_file_response(cached_path, 'application/x-3ds-rom', cleanup_dir=tmpdir)


# ── Xbox CCI / ISO conversion ────────────────────────────────────────────────

async def _extract_xbox(
    source_path: Path,
    system: str,
    fmt: str,
    display_stem: str | None = None,
) -> Response:
    """Return an Xbox disc image in the requested CCI or ISO form.

    ``fmt`` is one of:
      * ``'iso'`` — direct stream for .iso input, or CCI → ISO conversion.
      * ``'cci'`` — direct ZIP for .cci input, or ISO → CCI conversion
                    followed by a ZIP wrapper.

    Conversions shell out to configurable command templates.  XGDTool is the
    intended backend, but the route only depends on the template producing the
    requested output file somewhere under ``{output_dir}``.
    """
    if system not in _XBOX_SYSTEMS:
        return Response(
            status_code=400,
            content=f"{fmt} extraction is only supported for Xbox / Xbox 360 ROMs (got {system})",
        )

    spec = _XBOX_EXTRACT_SPECS.get(fmt)
    if spec is None:
        return Response(status_code=400, content=f"Unsupported Xbox extract format: {fmt}")

    if source_path.suffix.lower() not in _XBOX_DISC_EXTENSIONS:
        return Response(
            status_code=400,
            content=(
                f"Xbox extract expects a {sorted(_XBOX_DISC_EXTENSIONS)} input; "
                f"got {source_path.suffix}"
            ),
        )

    source_ext = source_path.suffix.lower()
    stem = display_stem or source_path.stem

    if fmt == 'iso' and source_ext == '.iso':
        return _stream_file_response(source_path, spec['mime'])

    if fmt == 'cci' and source_ext == '.cci':
        return await _serve_single_file_zip(
            source_path,
            zip_stem=stem,
            arcname=f"{stem}.cci",
            cache_source=source_path,
            cache_fmt='xbox_cci_direct',
        )

    if fmt == 'iso' and source_ext != '.cci':
        return Response(status_code=400, content="Xbox ISO output expects a .cci or .iso source")
    if fmt == 'cci' and source_ext != '.iso':
        return Response(status_code=400, content="Xbox CCI output expects a .iso or .cci source")

    cached = _lookup_cached_output(source_path, f'xbox_{fmt}', spec['output_ext'])
    if cached is not None:
        return _stream_file_response(cached, spec['mime'])

    command_template = getattr(settings, spec['setting'])
    if not command_template:
        return Response(
            status_code=503,
            content=(
                f"Xbox {spec['label']} conversion is not configured on the server.\n"
                f"\n"
                f"To enable this conversion, set {spec['env']} to a command template that "
                f"reads {{input}} and writes a {spec['tool_output_ext']} output file. "
                f"The server will look at {{output}} first, then scan {{output_dir}} for "
                f"the produced {spec['tool_output_ext']} file(s). ``{{stem}}`` is the "
                f"input filename without extension.\n"
                f"\n"
                f"XGDTool CLI supports format targets like --xiso and --cci; point this "
                f"template at your XGDTool executable and include {{input}} + {{output_dir}}."
            ),
        )

    tmpdir = tempfile.mkdtemp(prefix='xbox_extract_', dir=_conversion_tmp_dir())

    def _run() -> Path:
        tmp = Path(tmpdir)
        tool_output_ext = spec['tool_output_ext']
        output_name = f"{stem}{tool_output_ext}"
        output_path = tmp / output_name

        cmd = _expand_command_template(
            command_template,
            input=str(source_path),
            output=str(output_path),
            output_dir=str(tmp),
            stem=stem,
        )
        # Xbox 360 disc images can hit ~7 GB; leave generous room for
        # compression/decompression on a slow Pi or USB drive.
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3600,
        )
        if result.returncode != 0:
            raise RuntimeError(
                result.stderr.strip() or result.stdout.strip() or 'Xbox conversion failed'
            )

        if fmt == 'cci':
            if output_path.is_file():
                cci_outputs = [output_path]
            else:
                cci_outputs = sorted(
                    p for p in tmp.rglob(f'*{tool_output_ext}')
                    if p.is_file() and p.suffix.lower() == tool_output_ext
                )
            if not cci_outputs:
                raise RuntimeError(
                    f"converter completed but did not produce a {tool_output_ext} file"
                )
            zip_path = tmp / f"{stem}.zip"
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_STORED, allowZip64=True) as zf:
                for out in cci_outputs:
                    arcname = f"{stem}.cci" if len(cci_outputs) == 1 else out.name
                    zf.write(out, arcname=arcname)
            return zip_path

        final_path = (
            output_path
            if output_path.is_file()
            else _find_single_output(tmp, tool_output_ext)
        )
        if final_path is None or not final_path.is_file():
            raise RuntimeError(
                f"converter completed but did not produce a {tool_output_ext} file"
            )
        return final_path

    try:
        final_path = await asyncio.get_event_loop().run_in_executor(None, _run)
    except RuntimeError as exc:
        _cleanup_dir(tmpdir)
        return Response(status_code=500, content=f"Conversion failed: {exc}")
    except subprocess.TimeoutExpired:
        _cleanup_dir(tmpdir)
        return Response(status_code=504, content="Conversion timed out (>60 min)")

    cached_path = _save_to_cache(final_path, source_path, f'xbox_{fmt}', spec['output_ext'])
    return _stream_file_response(cached_path, spec['mime'], cleanup_dir=tmpdir)


# ── PS1 → PSP EBOOT.PBP ──────────────────────────────────────────────────────

_PS1_SERIAL_RE = re.compile(
    r'(?:^|[^A-Za-z0-9])'
    r'((?:SLUS|SCUS|SLES|SCES|SLPS|SLPM|SCPS|SCPM|SLPN|'
    r'SCAJ|SLAJ|PAPX|PBPX|SLED|SCED)[-_ ]?\d{3}[._\- ]?\d{2})'
    r'(?=$|[^0-9A-Za-z])',
    re.IGNORECASE,
)


def _extract_ps1_serial_from_filename(name: str) -> str | None:
    """Pull a Sony PS1 disc serial out of a filename like
    ``Crash Bandicoot [SCUS-94900].chd`` → ``SCUS-94900``.

    Mirrors the desktop client's ``extract_ps_serial`` regex but limited
    to the PS1-only prefixes — the goal is to feed popstation_md a
    ``main_gamecode`` argument it accepts.
    """
    stem = Path(name).stem
    m = _PS1_SERIAL_RE.search(stem)
    if not m:
        return None
    raw = m.group(1).upper().replace('_', '-').replace(' ', '-').replace('.', '-')
    # Normalise to PREFIX-12345 (5 contiguous digits, dash separator).
    parts = re.findall(r'[A-Z]+|\d+', raw)
    letters = ''.join(p for p in parts if p.isalpha())
    digits = ''.join(p for p in parts if p.isdigit())
    if len(letters) >= 4 and len(digits) >= 5:
        return f"{letters[:4]}-{digits[:5]}"
    return None


def _ps1_clean_title(name: str) -> str:
    """Strip parentheticals + bracketed serials so the EBOOT title field
    reads naturally on the PSP XMB.
    """
    stem = Path(name).stem
    cleaned = re.sub(r'\s*[\[\(][^\]\)]*[\]\)]', '', stem)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned or stem


# ``Foo (Disc 2) [ENG].chd`` → 2.  Falls back to 1 for files without a
# disc tag (single-disc games or non-standard naming).
_PS1_DISC_NUM_RE = re.compile(
    r'\(\s*[Dd]is[ck]\s+(\d+)\s*(?:of\s+\d+)?\s*\)',
)


def _ps1_disc_number(filename: str) -> int:
    m = _PS1_DISC_NUM_RE.search(filename)
    return int(m.group(1)) if m else 1


def _ps1_normalize_gamecode(value: str | None) -> str:
    """Strip non-alphanumerics and uppercase.  pop-fe + popstation expect
    the canonical PS1 product code form (``SCUS94503``, 9 chars, no dash);
    the catalog's ``title_id`` is already in that form, but a fallback
    via ``_extract_ps1_serial_from_filename`` returns the dashed form."""
    if not value:
        return ''
    return re.sub(r'[^A-Za-z0-9]', '', value).upper()


def _ps1_disc_siblings(entry) -> list:
    """Return all PS1 catalog entries sharing ``entry.title_id`` (i.e. the
    full multi-disc set), sorted by disc number derived from filename.

    Multi-disc PS1 games end up as N RomEntry rows with the same
    ``title_id`` (the disc serial — same for every disc) but distinct
    ``rom_id`` (disambiguated by stem suffix).  pop-fe needs all discs
    in disc-1-first order to produce a single multi-disc EBOOT.PBP that
    the PSP firmware (POPS) can disc-swap between via the home menu.

    Returns ``[entry]`` for single-disc games or when the catalog isn't
    populated yet (e.g. a request mid-rescan).
    """
    if (entry.system or '').upper() not in _PS1_EBOOT_SYSTEMS:
        return [entry]
    title_id = entry.title_id
    if not title_id:
        return [entry]
    catalog = rom_scanner.get()
    if catalog is None:
        return [entry]
    siblings = [
        e for e in catalog.list_all()
        if (e.system or '').upper() in _PS1_EBOOT_SYSTEMS
        and e.title_id == title_id
    ]
    if len(siblings) <= 1:
        return [entry]
    siblings.sort(key=lambda e: (_ps1_disc_number(e.filename), e.filename))
    return siblings


async def _extract_ps1_eboot(source_path: Path, system: str, entry) -> Response:
    """Convert a PS1 disc image (or multi-disc set) to a PSP EBOOT.PBP.

    Used by the PSP client's ROM Catalog so PS1 games convert into a PBP
    installable under ``ms0:/PSP/GAME/<id>/`` and play on real PSP
    hardware.  Output is the raw .pbp — no zip wrapper — so the client
    can stream it straight to the target path without an extra extraction
    step.

    Multi-disc games (Final Fantasy VII, Ace Combat 3, etc.) are
    detected via shared ``title_id`` across catalog entries.  The full
    set of disc files is passed to the converter in disc-1-first order
    in a single invocation, producing one multi-disc PBP that POPS can
    swap discs in.  The cache key is over the entire disc set, so a
    request for any disc hits the same cached PBP.

    Until the operator wires up ``SYNC_ROM_PS1_EBOOT_COMMAND``, the
    route returns 503 with a pop-fe install hint.
    """
    if system not in _PS1_EBOOT_SYSTEMS:
        return Response(
            status_code=400,
            content=(
                "EBOOT extraction is only supported for PS1 ROMs "
                f"(got {system})"
            ),
        )

    spec = _PS1_EBOOT_SPEC

    # Resolve the full multi-disc set (or [entry] for single-disc).
    siblings = _ps1_disc_siblings(entry)
    rom_dir = settings.rom_dir
    if rom_dir is None:
        return Response(status_code=503, content="ROM directory not configured")
    disc_paths = [rom_dir / s.path for s in siblings]
    disc_paths = [p for p in disc_paths if p.is_file()]
    if not disc_paths:
        return Response(status_code=404, content="No disc files found on disk")

    # Cache key spans every disc — disc 1 and disc 2 of the same game
    # map to the same cached PBP.
    cache_key = _conversion_cache_key_multi(disc_paths, 'eboot')
    cached = _lookup_cached_by_key('ps1_eboot', cache_key, spec['output_ext'])
    if cached is not None:
        return _stream_file_response(cached, spec['mime'])

    command_template = getattr(settings, spec['setting'])
    if not command_template:
        return Response(
            status_code=503,
            content=(
                f"PS1 → {spec['label']} conversion is not configured on the server.\n"
                f"\n"
                f"Recommended setup with pop-fe (https://github.com/sahlberg/pop-fe):\n"
                f"  1. git clone https://github.com/sahlberg/pop-fe.git /home/pi/pop-fe\n"
                f"  2. cd /home/pi/pop-fe && git submodule update --init\n"
                f"  3. (cd atracdenc/src && cmake . && make)\n"
                f"  4. pip3 install --user --break-system-packages \\\n"
                f"       pycdlib ecdsa PyPDF2 pycryptodome rarfile opencv-contrib-python\n"
                f"  5. Add to server/.env:\n"
                f"     SYNC_ROM_PS1_EBOOT_CWD=/home/pi/pop-fe\n"
                f"     SYNC_ROM_PS1_EBOOT_COMMAND=[\"python3\",\"/home/pi/pop-fe/pop-fe.py\","
                f"\"--psp-dir\",\"{{output_dir}}\",\"--title\",\"{{title}}\","
                f"\"--game_id\",\"{{gamecode}}\",\"--no-libcrypt\",\"{{inputs}}\"]\n"
                f"\n"
                f"Available placeholders:\n"
                f"  {{inputs}}      — list-valued; expands to N argv entries, one per disc\n"
                f"                    (multi-disc games pass all discs in disc-1-first order)\n"
                f"  {{input}}       — primary disc path only\n"
                f"  {{title}}       — game name (catalog ``name``, falls back to filename)\n"
                f"  {{gamecode}}    — PS1 product code (catalog ``title_id``, e.g. SCUS94503)\n"
                f"  {{output_dir}}  — fresh scratch dir; converter must put EBOOT.PBP under it\n"
                f"  {{stem}}        — primary disc filename without extension\n"
                f"  {{compression}} — fixed at 9 (kept for popstation-flavoured templates)\n"
            ),
        )

    cwd = settings.rom_ps1_eboot_cwd or None
    tmpdir = tempfile.mkdtemp(prefix='ps1_eboot_', dir=_conversion_tmp_dir())

    title = entry.name or _ps1_clean_title(disc_paths[0].name)
    gamecode = (
        _ps1_normalize_gamecode(entry.title_id)
        or _ps1_normalize_gamecode(_extract_ps1_serial_from_filename(disc_paths[0].name))
        or 'SLUS00000'  # last-resort placeholder; pop-fe still emits a PBP
    )
    primary = disc_paths[0]

    def _run() -> Path:
        tmp = Path(tmpdir)
        stage = tmp / 'stage'
        stage.mkdir()

        cmd = _expand_command_template(
            command_template,
            inputs=[str(p) for p in disc_paths],
            input=str(primary),
            output=str(stage / 'EBOOT.PBP'),
            output_dir=str(stage),
            stem=primary.stem,
            title=title,
            gamecode=gamecode,
            compression='9',
        )
        # pop-fe + ATRAC3 encoding + asset fetching can take 5-15 min
        # per disc on a Pi — give multi-disc runs a full hour.  Run with
        # ``cwd`` set to the configured pop-fe source tree so its
        # relative-path lookups for binmerge / cue2cu2.py / atracdenc
        # resolve correctly; falls back to the per-request scratch dir
        # for self-contained converters that don't care.
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3600,
            cwd=cwd or str(tmp),
        )
        if result.returncode != 0:
            raise RuntimeError(
                result.stderr.strip()
                or result.stdout.strip()
                or 'PS1 → EBOOT conversion failed'
            )

        # pop-fe writes ``<psp-dir>/<gameid>/EBOOT.PBP`` (or
        # ``<psp-dir>/PSP/GAME/<gameid>/EBOOT.PBP`` if that subtree
        # already existed in the staging dir).  Other converters may
        # emit it directly into ``output_dir``.  Recover by globbing,
        # picking the largest match (filters out empty stub PBPs).
        candidates = list(stage.rglob('EBOOT.PBP'))
        if not candidates:
            raise RuntimeError(
                "converter completed but did not produce an EBOOT.PBP\n"
                + (result.stdout[-2000:] if result.stdout else '')
            )
        candidates.sort(key=lambda p: p.stat().st_size, reverse=True)
        return candidates[0]

    try:
        final_path = await asyncio.get_event_loop().run_in_executor(None, _run)
    except RuntimeError as exc:
        _cleanup_dir(tmpdir)
        return Response(status_code=500, content=f"Conversion failed: {exc}")
    except subprocess.TimeoutExpired:
        _cleanup_dir(tmpdir)
        return Response(status_code=504, content="Conversion timed out (>1 hour)")

    cached_path = _save_to_cache_by_key(final_path, 'ps1_eboot', cache_key, spec['output_ext'])
    return _stream_file_response(cached_path, spec['mime'], cleanup_dir=tmpdir)


# ── PSP CHD → ISO / CSO ──────────────────────────────────────────────────────

async def _extract_psp(chd_path: Path, system: str, stem: str, fmt: str) -> Response:
    """Extract a PSP CHD to ISO or CSO."""
    if chd_path.suffix.lower() != '.chd':
        return Response(status_code=400, content="Only CHD files can be extracted")

    if system not in _PSP_SYSTEMS:
        return Response(status_code=400, content=f"ISO/CSO extraction is only for PSP (got {system})")

    if not shutil.which('chdman'):
        return Response(status_code=503,
                        content="chdman not installed. Run: sudo apt install mame-tools")

    cso_tool: Optional[str] = None
    if fmt == 'cso':
        cso_tool = shutil.which('maxcso') or shutil.which('ciso')
        if not cso_tool:
            return Response(
                status_code=503,
                content="No CSO tool found. Install one: sudo apt install ciso  OR  compile maxcso",
            )

    # Cache fast-path
    output_ext = '.iso' if fmt == 'iso' else '.cso'
    cached = _lookup_cached_output(chd_path, fmt, output_ext)
    if cached is not None:
        mime = 'application/x-iso9660-image' if fmt == 'iso' else 'application/x-cso'
        return _stream_file_response(cached, mime)

    tmpdir = tempfile.mkdtemp(prefix='psp_extract_', dir=_conversion_tmp_dir())
    tmp = Path(tmpdir)
    iso_path = tmp / (stem + '.iso')
    cso_path = tmp / (stem + '.cso')

    def _run() -> Path:
        # PSP CHDs are hard-disk images (createhd), not CD-ROM images (createcd).
        # Use extracthd which outputs a raw ISO directly.
        r = subprocess.run(
            ['chdman', 'extracthd', '-i', str(chd_path), '-o', str(iso_path)],
            capture_output=True, text=True, timeout=600,
        )
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip() or r.stdout.strip() or 'chdman failed')

        if fmt == 'iso':
            return iso_path

        # Step 2 — ISO → CSO
        if 'maxcso' in (cso_tool or ''):
            cmd = [cso_tool, str(iso_path), '--output', str(cso_path)]
        else:
            # ciso: ciso <level 1-9> <input> <output>
            cmd = [cso_tool, '9', str(iso_path), str(cso_path)]

        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip() or r.stdout.strip() or 'CSO conversion failed')

        # ISO is no longer needed; remove early so the tempdir doesn't double in size.
        try:
            iso_path.unlink()
        except OSError:
            pass
        return cso_path

    try:
        out_path = await asyncio.get_event_loop().run_in_executor(None, _run)
    except RuntimeError as exc:
        _cleanup_dir(tmpdir)
        return Response(status_code=500, content=f"Conversion failed: {exc}")
    except subprocess.TimeoutExpired:
        _cleanup_dir(tmpdir)
        return Response(status_code=504, content="Conversion timed out (>10 min)")

    mime = 'application/x-iso9660-image' if fmt == 'iso' else 'application/x-cso'
    cached_path = _save_to_cache(out_path, chd_path, fmt, output_ext)
    return _stream_file_response(cached_path, mime, cleanup_dir=tmpdir)


# ── PS2 CHD → ISO (DVD images) ──────────────────────────────────────────────

async def _extract_ps2_iso(chd_path: Path, stem: str) -> Response:
    """Extract a PS2 DVD CHD to a single .iso via ``chdman extractdvd``.

    PS2 ships both DVDs and CDs.  CD images flow through ``_extract_cd``
    (CUE/BIN zip); only DVD images take this code path, and the caller is
    responsible for figuring out which it is via the DAT lookup in
    :func:`_extract_formats_for_entry`.

    Output is a raw ISO image — no further compression — because PS2
    emulators (PCSX2, AetherSX2) read .iso natively and the CHD already
    sat in a compressed form on the server, so re-compressing at this
    stage would just slow things down.
    """
    if chd_path.suffix.lower() != '.chd':
        return Response(status_code=400, content="Only CHD files can be extracted")

    if not shutil.which('chdman'):
        return Response(
            status_code=503,
            content="chdman not installed. Run: sudo apt install mame-tools",
        )

    # Cache fast-path — same convention as PSP / CD extracts.
    cached = _lookup_cached_output(chd_path, 'iso', '.iso')
    if cached is not None:
        return _stream_file_response(cached, 'application/x-iso9660-image')

    tmpdir = tempfile.mkdtemp(prefix='ps2_extract_', dir=_conversion_tmp_dir())
    tmp = Path(tmpdir)
    iso_path = tmp / (stem + '.iso')

    def _run() -> Path:
        # PS2 DVDs were created with ``chdman createdvd``; ``extractdvd``
        # is the matching reverse operation.  Some older chdman builds
        # (pre-0.227) only ship ``extractraw`` — fall back to that on a
        # "subcommand unknown" failure so older Pi images still work.
        r = subprocess.run(
            ['chdman', 'extractdvd', '-i', str(chd_path), '-o', str(iso_path)],
            capture_output=True, text=True, timeout=900,
        )
        if r.returncode != 0:
            stderr = (r.stderr or '').strip()
            if 'unknown command' in stderr.lower() or 'usage:' in stderr.lower():
                # Older chdman — try extractraw as a fallback.
                r2 = subprocess.run(
                    ['chdman', 'extractraw', '-i', str(chd_path), '-o', str(iso_path)],
                    capture_output=True, text=True, timeout=900,
                )
                if r2.returncode != 0:
                    raise RuntimeError(
                        (r2.stderr or r2.stdout or 'chdman extractraw failed').strip()
                    )
            else:
                raise RuntimeError(stderr or (r.stdout or '').strip() or 'chdman failed')
        return iso_path

    try:
        out_path = await asyncio.get_event_loop().run_in_executor(None, _run)
    except RuntimeError as exc:
        _cleanup_dir(tmpdir)
        return Response(status_code=500, content=f"Conversion failed: {exc}")
    except subprocess.TimeoutExpired:
        _cleanup_dir(tmpdir)
        return Response(status_code=504, content="Conversion timed out (>15 min)")

    cached_path = _save_to_cache(out_path, chd_path, 'iso', '.iso')
    return _stream_file_response(
        cached_path, 'application/x-iso9660-image', cleanup_dir=tmpdir
    )


# ── CD-ROM CHD → CUE/BIN or GDI zip ─────────────────────────────────────────

async def _extract_cd(chd_path: Path, system: str) -> Response:
    """Run chdman extractcd and return a ZIP of all output files."""
    if chd_path.suffix.lower() != '.chd':
        return Response(status_code=400, content="Only CHD files can be extracted")

    sys_up = system.upper()
    if sys_up not in _CD_SYSTEMS:
        return Response(status_code=400,
                        content=f"System '{system}' does not support CD extraction")

    if not shutil.which('chdman'):
        return Response(status_code=503,
                        content="chdman not installed. Run: sudo apt install mame-tools")

    out_ext = '.gdi' if sys_up in _GDI_SYSTEMS else '.cue'
    stem    = chd_path.stem

    # Cache fast-path: the CD extraction is deterministic (chdman is
    # bit-exact for a given CHD), so caching the final ZIP is safe.
    # Format key is a fixed literal because there's only one CHD-CD output
    # variant per system; we use ``out_ext`` to pick a stable cache key.
    cd_fmt = f'cd_{out_ext.lstrip(".")}'  # e.g. "cd_cue" / "cd_gdi"
    cached = _lookup_cached_output(chd_path, cd_fmt, '.zip')
    if cached is not None:
        return _stream_file_response(cached, 'application/zip')

    # Two-phase tempdir layout:
    #   <tmpdir>/extract/   — chdman writes its raw output here
    #   <tmpdir>/<stem>.zip — final archive we stream to the client
    # We zip with ZIP_STORED (no compression) because BIN/RAW track data is
    # already incompressible and the client can extract instantly. This also
    # lets us avoid CPU spike on the Pi during the zip step.
    tmpdir = tempfile.mkdtemp(prefix='cd_extract_', dir=_conversion_tmp_dir())
    extract_dir = Path(tmpdir) / 'extract'
    extract_dir.mkdir()
    zip_path = Path(tmpdir) / f'{stem}.zip'

    def _run_extraction() -> Path:
        out_file = extract_dir / (stem + out_ext)
        r = subprocess.run(
            ['chdman', 'extractcd', '-i', str(chd_path), '-o', str(out_file)],
            capture_output=True, text=True, timeout=600,
        )
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip() or r.stdout.strip() or 'unknown error')

        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_STORED) as zf:
            for f in sorted(extract_dir.iterdir()):
                zf.write(f, f.name)

        # Free the raw extracted files now that they're inside the zip — keeps
        # disk usage from doubling on the Pi.
        for f in extract_dir.iterdir():
            try: f.unlink()
            except OSError: pass
        try: extract_dir.rmdir()
        except OSError: pass

        return zip_path

    try:
        out_path = await asyncio.get_event_loop().run_in_executor(None, _run_extraction)
    except RuntimeError as exc:
        _cleanup_dir(tmpdir)
        return Response(status_code=500, content=f"Extraction failed: {exc}")
    except subprocess.TimeoutExpired:
        _cleanup_dir(tmpdir)
        return Response(status_code=504, content="Extraction timed out (>10 min)")

    cached_path = _save_to_cache(out_path, chd_path, cd_fmt, '.zip')
    return _stream_file_response(cached_path, 'application/zip', cleanup_dir=tmpdir)


# ── Regular file serving ─────────────────────────────────────────────────────
#
# Both helpers below stream from disk in chunks instead of loading the whole
# file into RAM. The previous implementation called `file_path.read_bytes()`
# which on a Pi with 2-4 GB RAM made multi-GB ROM downloads OOM-kill the
# process — and even when it didn't, the long blocking read held the event
# loop and tripped nginx's `proxy_read_timeout`, severing the connection.

def _serve_full(file_path: Path, file_size: int, content_type: str) -> Response:
    # FileResponse uses the platform's zero-copy sendfile() under the hood
    # when possible, otherwise falls back to chunked async reads. Either way
    # the request handler never holds the whole file in memory.
    return FileResponse(
        path=file_path,
        media_type=content_type,
        filename=file_path.name,
        headers={
            'Accept-Ranges': 'bytes',
            'Content-Length': str(file_size),
        },
    )


def _serve_range(
    file_path: Path, file_size: int, content_type: str, range_header: str
) -> Response:
    start, end = _parse_range(range_header, file_size)
    if start is None:
        return Response(status_code=416, headers={'Content-Range': f'bytes */{file_size}'})

    length = end - start + 1

    def _iter() -> bytes:
        # Open inside the generator so the file handle's lifetime is tied to
        # the response stream, not the request handler.
        with open(file_path, 'rb') as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                buf = f.read(min(_STREAM_CHUNK, remaining))
                if not buf:
                    break
                remaining -= len(buf)
                yield buf

    return StreamingResponse(
        _iter(),
        status_code=206,
        media_type=content_type,
        headers={
            'Content-Range': f'bytes {start}-{end}/{file_size}',
            'Content-Length': str(length),
            'Accept-Ranges': 'bytes',
            'Content-Disposition': f'attachment; filename="{file_path.name}"',
        },
    )


def _parse_range(range_header: str, file_size: int) -> tuple[int | None, int | None]:
    if not range_header.startswith('bytes='):
        return None, None
    try:
        parts = range_header[6:].split('-', 1)
        if parts[0] == '':
            suffix = int(parts[1])
            return max(0, file_size - suffix), file_size - 1
        elif parts[1] == '':
            start = int(parts[0])
            return (None, None) if start >= file_size else (start, file_size - 1)
        else:
            start, end = int(parts[0]), int(parts[1])
            if start > end or start >= file_size:
                return None, None
            return start, min(end, file_size - 1)
    except (ValueError, IndexError):
        return None, None


def _extract_formats_for_entry(entry) -> tuple[str | None, list[str]]:
    system = getattr(entry, 'system', '') or ''
    filename = getattr(entry, 'filename', '') or ''
    sys_up = system.upper()
    suffix = Path(filename).suffix.lower()

    if sys_up in _XBOX_SYSTEMS and getattr(entry, 'is_bundle', False):
        return 'xbox', list(_XBOX_EXTRACT_FORMATS)

    if suffix == '.chd':
        if sys_up in _PSP_SYSTEMS:
            return 'psp', ['iso', 'cso']
        if sys_up in _PS2_SYSTEMS:
            # PS2 is split media: DVD CHDs extract to a single ISO,
            # CD CHDs extract to a CUE/BIN zip.  Ask the DAT what the
            # original disc was.  When the DAT can't answer (game not
            # in any loaded DAT, or DAT only listed cart entries) we
            # fall back to no extract option — the user still gets the
            # raw CHD download.
            normalizer = _dat_normalizer_get()
            disc_ext = (
                normalizer.lookup_disc_format('PS2', filename) if normalizer else None
            )
            if disc_ext == 'iso':
                return 'iso', ['iso']
            if disc_ext in ('bin', 'cue'):
                return 'cue', ['cue']
            return None, []
        if sys_up in _GDI_SYSTEMS:
            return 'gdi', ['gdi']
        if sys_up in _PS1_EBOOT_SYSTEMS:
            # PS1 CHD: PS3 client wants CUE/BIN, PSP client wants
            # EBOOT.PBP.  Advertise both so each client picks its
            # native format from extract_formats[].
            return 'cue', ['cue', 'eboot']
        if sys_up in _CUE_SYSTEMS:
            return 'cue', ['cue']
    elif sys_up in _PS1_EBOOT_SYSTEMS and suffix in {'.cue', '.bin', '.iso', '.img'}:
        # PS1 native disc images (no CHD) — no extract needed for the
        # PS3 client (raw CUE/BIN is fine), but the PSP client needs
        # an EBOOT, so advertise that as the only option.
        return None, ['eboot']
    elif suffix == '.rvz' and sys_up in _GC_SYSTEMS:
        return 'rvz', ['iso']
    elif sys_up in _3DS_SYSTEMS and suffix in _3DS_CART_EXTENSIONS.union({'.zip'}):
        return '3ds', list(_3DS_EXTRACT_FORMATS)
    elif sys_up in _XBOX_SYSTEMS and suffix in _XBOX_DISC_EXTENSIONS:
        return 'xbox', list(_XBOX_EXTRACT_FORMATS)

    return None, []


def _dat_normalizer_get():
    """Lazy DatNormalizer accessor — avoids a hard import cycle at module load."""
    from app.services import dat_normalizer
    return dat_normalizer.get()


def _materialize_3ds_source(source_path: Path, tmp_dir: Path) -> tuple[Path, str]:
    suffix = source_path.suffix.lower()
    if suffix in _3DS_CART_EXTENSIONS:
        return source_path, source_path.stem

    if suffix != '.zip':
        raise ValueError(
            "3DS conversion currently supports raw .3ds/.cci files or .zip archives containing one .3ds/.cci file"
        )

    with zipfile.ZipFile(source_path) as zf:
        members = [
            info for info in zf.infolist()
            if not info.is_dir() and Path(info.filename).suffix.lower() in _3DS_CART_EXTENSIONS
        ]
        if not members:
            raise ValueError("ZIP archive does not contain a .3ds or .cci file")
        if len(members) > 1:
            raise ValueError("ZIP archive must contain exactly one .3ds or .cci file")

        member = members[0]
        member_name = Path(member.filename).name
        extracted = tmp_dir / member_name
        with zf.open(member) as src, open(extracted, 'wb') as dst:
            # Default copyfileobj buffer is 8 KiB.  For multi-GB ROMs that
            # means hundreds of thousands of read/write syscalls; bumping to
            # 8 MiB shaves syscall overhead and lets the kernel pipeline I/O
            # alongside zlib decompression more efficiently.  Decompression
            # itself stays single-threaded (Python's zipfile module limit),
            # but every megabyte of avoided syscall churn helps.
            shutil.copyfileobj(src, dst, length=8 * 1024 * 1024)
        return extracted, extracted.stem


def _expand_command_template(template: str, **values) -> list[str]:
    payload = template.strip()
    if not payload:
        raise RuntimeError("empty command template")

    if payload.startswith('['):
        parsed = json.loads(payload)
        if not isinstance(parsed, list) or not all(isinstance(part, str) for part in parsed):
            raise RuntimeError("command template JSON must be an array of strings")
        parts = parsed
    else:
        parts = shlex.split(payload, posix=os.name != 'nt')

    # A list-valued placeholder (e.g. ``{inputs}`` for multi-disc PS1)
    # expands to N argv entries when it appears as a token by itself.
    # Embedding it inside a larger token like ``--files={inputs}`` is a
    # template authoring mistake — flag it loudly rather than silently
    # str(list)-ing.
    list_values = {k: list(v) for k, v in values.items() if isinstance(v, (list, tuple))}
    scalar_values = {k: v for k, v in values.items() if not isinstance(v, (list, tuple))}

    expanded: list[str] = []
    for part in parts:
        list_token_match = next(
            (k for k in list_values if part == f'{{{k}}}'),
            None,
        )
        if list_token_match is not None:
            expanded.extend(list_values[list_token_match])
            continue
        for key in list_values:
            if f'{{{key}}}' in part:
                raise RuntimeError(
                    f"placeholder {{{key}}} is list-valued and must appear "
                    f"as a standalone token, not embedded in {part!r}"
                )
        updated = part
        for key, value in scalar_values.items():
            updated = updated.replace(f'{{{key}}}', value)
        expanded.append(updated)
    return expanded


def _find_single_output(tmp_dir: Path, extension: str) -> Path | None:
    outputs = sorted(
        p for p in tmp_dir.rglob(f'*{extension}') if p.is_file() and p.suffix.lower() == extension
    )
    if len(outputs) == 1:
        return outputs[0]
    return None


_CONTENT_TYPES = {
    '.gba': 'application/x-gba-rom',
    '.gbc': 'application/x-gbc-rom',
    '.gb':  'application/x-gb-rom',
    '.nes': 'application/x-nes-rom',
    '.fds': 'application/x-nes-rom',
    '.sfc': 'application/x-snes-rom',
    '.smc': 'application/x-snes-rom',
    '.nds': 'application/x-nds-rom',
    '.3ds': 'application/x-3ds-rom',
    '.cci': 'application/x-3ds-rom',
    '.cia': 'application/x-3ds-rom',
    '.n64': 'application/x-n64-rom',
    '.z64': 'application/x-n64-rom',
    '.iso': 'application/x-iso9660-image',
    '.pkg': 'application/octet-stream',  # PS3 / PSP PSN package — no standard MIME
    '.chd': 'application/x-chd',
    '.cso': 'application/x-cso',
    '.rvz': 'application/x-rvz',
    '.zip': 'application/zip',
    '.7z':  'application/x-7z-compressed',
    '.rar': 'application/x-rar-compressed',
}


def _content_type(filename: str) -> str:
    return _CONTENT_TYPES.get(Path(filename).suffix.lower(), 'application/octet-stream')
