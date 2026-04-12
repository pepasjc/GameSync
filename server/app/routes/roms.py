"""ROM catalog endpoints.

GET  /api/v1/roms              — List all ROMs in catalog (with optional filters)
GET  /api/v1/roms/{title_id}   — Download a ROM file (with HTTP Range support)
                                  ?extract=true  — Convert CHD → CUE/BIN or GDI and return as ZIP
POST /api/v1/roms/scan         — Trigger rescan of ROM directory
GET  /api/v1/roms/systems      — List systems with ROMs and counts
"""

import asyncio
import io
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import Response

from app.config import settings
from app.services import rom_scanner

router = APIRouter()

# CD-ROM systems where CHD can be extracted to CUE/BIN
_CUE_SYSTEMS = frozenset({
    'PSX', 'PS1',         # PlayStation
    'SAT',                # Sega Saturn
    'SCD', 'MEGACD',      # Sega CD / Mega CD
    'PCECD', 'PCENGINECD', 'TG16CD',  # PC Engine CD / TurboGrafx CD
    '3DO',                # 3DO
    'PCFX',               # PC-FX
    'NGCD',               # Neo Geo CD
    'AMIGACD32',          # Amiga CD32
})

# Dreamcast uses GDI format instead
_GDI_SYSTEMS = frozenset({'DC', 'DREAMCAST'})

# All CD-ROM systems that support CHD extraction
_CD_SYSTEMS = _CUE_SYSTEMS | _GDI_SYSTEMS


@router.get("/roms")
async def list_roms(
    system: Optional[str] = Query(None, description="Filter by system code (e.g. GBA, SNES)"),
    search: Optional[str] = Query(None, description="Search ROM name (case-insensitive substring)"),
    has_save: Optional[bool] = Query(None, description="Filter by whether a save exists on server"),
):
    """List all ROMs in the catalog with optional filtering."""
    catalog = rom_scanner.get()
    if not catalog:
        return {"roms": [], "total": 0}

    entries = catalog.list_all()

    if system:
        sys_upper = system.upper()
        entries = [e for e in entries if e.system == sys_upper]

    if search:
        term = search.lower()
        entries = [
            e for e in entries if term in e.name.lower() or term in e.filename.lower()
        ]

    if has_save is not None:
        from app.services import storage

        if has_save:
            entries = [e for e in entries if storage.title_exists(e.title_id)]
        else:
            entries = [e for e in entries if not storage.title_exists(e.title_id)]

    # Annotate each entry with whether CHD extraction is available
    result = []
    for e in entries:
        d = e.to_dict()
        sys_up = (e.system or '').upper()
        is_chd = Path(e.filename).suffix.lower() == '.chd'
        if is_chd and sys_up in _CD_SYSTEMS:
            d['extract_format'] = 'gdi' if sys_up in _GDI_SYSTEMS else 'cue'
        result.append(d)

    return {"roms": result, "total": len(result)}


@router.get("/roms/systems")
async def list_systems():
    """List systems that have ROMs available, with counts."""
    catalog = rom_scanner.get()
    if not catalog:
        return {"systems": [], "stats": {}}
    return {"systems": catalog.systems(), "stats": catalog.stats()}


@router.get("/roms/scan")
async def trigger_scan(
    use_crc32: bool = Query(False, description="Compute CRC32 for accurate DAT matching (slow)"),
):
    """Trigger a rescan of the ROM directory."""
    catalog = rom_scanner.rescan(use_crc32=use_crc32)
    if not catalog:
        return {"status": "no_rom_dir", "count": 0}
    return {"status": "ok", "count": len(catalog.entries)}


@router.get("/roms/{title_id:path}")
async def download_rom(
    title_id: str,
    request: Request,
    extract: bool = Query(False, description="Convert CHD to CUE/BIN or GDI and download as ZIP"),
    range_header: Optional[str] = Header(None, alias="Range"),
):
    """Download a ROM file by title_id.

    Supports HTTP Range requests for regular downloads.
    Pass ?extract=true to convert a CHD to CUE/BIN (or GDI for Dreamcast)
    and receive a ZIP archive containing all extracted files.
    """
    catalog = rom_scanner.get()
    if not catalog:
        return Response(status_code=404, content="No ROM catalog available")

    entry = catalog.get(title_id)
    if not entry:
        return Response(status_code=404, content=f"ROM not found: {title_id}")

    rom_dir = settings.rom_dir
    if not rom_dir:
        return Response(status_code=404, content="ROM directory not configured")

    file_path = rom_dir / entry.path
    if not file_path.is_file():
        return Response(status_code=404, content="ROM file not found on disk")

    if extract:
        return await _extract_chd(file_path, entry.system or '')

    file_size = file_path.stat().st_size
    content_type = _content_type(file_path.name)

    if range_header:
        return _serve_range(file_path, file_size, content_type, range_header)

    return _serve_full(file_path, file_size, content_type)


# ── CHD extraction ──────────────────────────────────────────────────────────

async def _extract_chd(chd_path: Path, system: str) -> Response:
    """Run chdman extractcd in a thread pool and return the result as a ZIP."""
    if chd_path.suffix.lower() != '.chd':
        return Response(status_code=400, content="Only CHD files can be extracted")

    sys_up = system.upper()
    if sys_up not in _CD_SYSTEMS:
        return Response(
            status_code=400,
            content=f"System '{system}' does not support CHD extraction",
        )

    if not shutil.which('chdman'):
        return Response(
            status_code=503,
            content="chdman is not installed. Run: sudo apt install mame-tools",
        )

    out_ext = '.gdi' if sys_up in _GDI_SYSTEMS else '.cue'
    stem = chd_path.stem

    def _run_extraction() -> bytes:
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = Path(tmpdir) / (stem + out_ext)
            result = subprocess.run(
                ['chdman', 'extractcd', '-i', str(chd_path), '-o', str(out_file)],
                capture_output=True,
                text=True,
                timeout=600,  # 10 min max
            )
            if result.returncode != 0:
                msg = result.stderr.strip() or result.stdout.strip() or 'unknown error'
                raise RuntimeError(msg)

            # Pack everything chdman produced into a ZIP (use STORED — bin/raw
            # tracks are binary data that won't compress meaningfully)
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
                for f in sorted(Path(tmpdir).iterdir()):
                    zf.write(f, f.name)
            return buf.getvalue()

    try:
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, _run_extraction)
    except RuntimeError as exc:
        return Response(status_code=500, content=f"Extraction failed: {exc}")
    except subprocess.TimeoutExpired:
        return Response(status_code=504, content="Extraction timed out (>10 min)")

    fmt = 'gdi' if sys_up in _GDI_SYSTEMS else 'cue'
    return Response(
        content=data,
        media_type='application/zip',
        headers={
            'Content-Disposition': f'attachment; filename="{stem}_{fmt}.zip"',
            'Content-Length': str(len(data)),
        },
    )


# ── Regular file serving ────────────────────────────────────────────────────

def _serve_full(file_path: Path, file_size: int, content_type: str) -> Response:
    return Response(
        content=file_path.read_bytes(),
        media_type=content_type,
        headers={
            'Content-Length': str(file_size),
            'Accept-Ranges': 'bytes',
            'Content-Disposition': f'attachment; filename="{file_path.name}"',
        },
    )


def _serve_range(
    file_path: Path, file_size: int, content_type: str, range_header: str
) -> Response:
    start, end = _parse_range(range_header, file_size)
    if start is None:
        return Response(
            status_code=416,
            headers={'Content-Range': f'bytes */{file_size}'},
        )

    length = end - start + 1
    with open(file_path, 'rb') as f:
        f.seek(start)
        data = f.read(length)

    return Response(
        status_code=206,
        content=data,
        media_type=content_type,
        headers={
            'Content-Range': f'bytes {start}-{end}/{file_size}',
            'Content-Length': str(length),
            'Accept-Ranges': 'bytes',
            'Content-Disposition': f'attachment; filename="{file_path.name}"',
        },
    )


def _parse_range(range_header: str, file_size: int) -> tuple[int | None, int | None]:
    """Parse HTTP Range header. Returns (start, end) or (None, None) on error."""
    if not range_header.startswith('bytes='):
        return None, None
    range_spec = range_header[6:]
    try:
        parts = range_spec.split('-', 1)
        if parts[0] == '':
            suffix = int(parts[1])
            return max(0, file_size - suffix), file_size - 1
        elif parts[1] == '':
            start = int(parts[0])
            if start >= file_size:
                return None, None
            return start, file_size - 1
        else:
            start, end = int(parts[0]), int(parts[1])
            if start > end or start >= file_size:
                return None, None
            return start, min(end, file_size - 1)
    except (ValueError, IndexError):
        return None, None


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
    '.cia': 'application/x-3ds-rom',
    '.n64': 'application/x-n64-rom',
    '.z64': 'application/x-n64-rom',
    '.iso': 'application/x-iso9660-image',
    '.chd': 'application/x-chd',
    '.cso': 'application/x-cso',
    '.zip': 'application/zip',
    '.7z':  'application/x-7z-compressed',
    '.rar': 'application/x-rar-compressed',
}


def _content_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    return _CONTENT_TYPES.get(ext, 'application/octet-stream')
