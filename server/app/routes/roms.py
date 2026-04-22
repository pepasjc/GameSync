"""ROM catalog endpoints.

GET  /api/v1/roms              — List all ROMs in catalog (with optional filters)
GET  /api/v1/roms/{title_id}   — Download a ROM file (with HTTP Range support)
                                  ?extract=cue  — CHD → CUE/BIN ZIP (PS1, Saturn, etc.)
                                  ?extract=gdi  — CHD → GDI ZIP (Dreamcast)
                                  ?extract=iso  — CHD → ISO (PSP)
                                  ?extract=cso  — CHD → CSO compressed image (PSP)
                                  ?extract=rvz  — RVZ → ISO (GameCube / Wii via DolphinTool)
                                  ?extract=cia  — 3DS cart image → installable CIA
                                  ?extract=decrypted_cia
                                                 3DS cart image → decrypted CIA for emulators
POST /api/v1/roms/scan         — Trigger rescan of ROM directory
GET  /api/v1/roms/systems      — List systems with ROMs and counts
"""

import asyncio
import io
import json
import os
import shlex
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse, Response

from app.config import settings
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
    'NGCD',
    'AMIGACD32',
    'JAGCD',
})

# Dreamcast uses GDI format
_GDI_SYSTEMS = frozenset({'DC', 'DREAMCAST'})

# PSP uses its own ISO/CSO pipeline
_PSP_SYSTEMS = frozenset({'PSP'})

# GameCube / Wii use RVZ (Dolphin compressed) — convert with DolphinTool
_GC_SYSTEMS = frozenset({'GC', 'WII'})

# 3DS cartridge images can be converted to CIA variants
_3DS_SYSTEMS = frozenset({'3DS'})

# All systems that support any CHD extraction
_CD_SYSTEMS   = _CUE_SYSTEMS | _GDI_SYSTEMS
_ALL_EXTRACT  = _CD_SYSTEMS | _PSP_SYSTEMS


# ── List endpoint ────────────────────────────────────────────────────────────

@router.get("/roms")
async def list_roms(
    system: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    has_save: Optional[bool] = Query(None),
):
    catalog = rom_scanner.get()
    if not catalog:
        return {"roms": [], "total": 0}

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

    result = []
    for e in entries:
        d = e.to_dict()
        extract_format, extract_formats = _extract_formats_for_entry(e.system or '', e.filename)
        if extract_format:
            d['extract_format'] = extract_format
        if extract_formats:
            d['extract_formats'] = extract_formats
        result.append(d)

    return {"roms": result, "total": len(result)}


# ── Misc endpoints ───────────────────────────────────────────────────────────

@router.get("/roms/systems")
async def list_systems():
    catalog = rom_scanner.get()
    if not catalog:
        return {"systems": [], "stats": {}}
    return {"systems": catalog.systems(), "stats": catalog.stats()}


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


# ── Download endpoint ────────────────────────────────────────────────────────

@router.get("/roms/{title_id:path}")
async def download_rom(
    title_id: str,
    request: Request,
    extract: Optional[str] = Query(
        None,
        description=(
            "Extract format: 'cue' (CUE/BIN zip), 'gdi' (GDI zip), "
            "'iso' (PSP ISO), 'cso' (PSP compressed ISO), "
            "'cia' (3DS installable CIA), 'decrypted_cia' (3DS emulator CIA)"
        ),
    ),
    range_header: Optional[str] = Header(None, alias="Range"),
):
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
        fmt = extract.lower()
        sys_up = (entry.system or '').upper()
        if fmt in ('cia', 'decrypted_cia'):
            return await _extract_3ds(file_path, sys_up, fmt)
        elif fmt == 'rvz' or (fmt == 'iso' and file_path.suffix.lower() == '.rvz'):
            return await _extract_rvz(file_path, file_path.stem)
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

    def _run() -> bytes:
        with tempfile.TemporaryDirectory() as tmpdir:
            iso_path = Path(tmpdir) / (stem + '.iso')
            r = subprocess.run(
                [dolphin_tool, 'convert', '-f', 'iso', '-i', str(rvz_path), '-o', str(iso_path)],
                capture_output=True, text=True, timeout=600,
            )
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip() or r.stdout.strip() or 'DolphinTool failed')
            return iso_path.read_bytes()

    try:
        data = await asyncio.get_event_loop().run_in_executor(None, _run)
    except RuntimeError as exc:
        return Response(status_code=500, content=f"Conversion failed: {exc}")
    except subprocess.TimeoutExpired:
        return Response(status_code=504, content="Conversion timed out (>10 min)")

    filename = stem + '.iso'
    return Response(
        content=data,
        media_type='application/x-iso9660-image',
        headers={
            'Content-Disposition': f'attachment; filename="{filename}"',
            'Content-Length': str(len(data)),
        },
    )


# ── Nintendo 3DS cart image → CIA variants ──────────────────────────────────

async def _extract_3ds(source_path: Path, system: str, fmt: str) -> Response:
    """Convert a 3DS cart image (optionally wrapped in ZIP) to CIA."""
    if system not in _3DS_SYSTEMS:
        return Response(
            status_code=400,
            content=f"{fmt} extraction is only supported for Nintendo 3DS ROMs (got {system})",
        )

    command_template = (
        settings.rom_3ds_cia_command
        if fmt == 'cia'
        else settings.rom_3ds_decrypted_cia_command
    )
    if not command_template:
        env_name = (
            'SYNC_ROM_3DS_CIA_COMMAND'
            if fmt == 'cia'
            else 'SYNC_ROM_3DS_DECRYPTED_CIA_COMMAND'
        )
        label = 'CIA' if fmt == 'cia' else 'decrypted CIA'
        return Response(
            status_code=503,
            content=(
                f"3DS {label} conversion is not configured. "
                f"Set {env_name} to a command template that writes the output CIA file."
            ),
        )

    def _run() -> tuple[str, bytes]:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            input_path, stem = _materialize_3ds_source(source_path, tmp)
            output_name = f"{stem}{'_decrypted' if fmt == 'decrypted_cia' else ''}.cia"
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

            final_path = output_path if output_path.is_file() else _find_single_output(tmp)
            if final_path is None or not final_path.is_file():
                raise RuntimeError("converter completed but did not produce a .cia file")

            return final_path.name, final_path.read_bytes()

    try:
        filename, data = await asyncio.get_event_loop().run_in_executor(None, _run)
    except RuntimeError as exc:
        return Response(status_code=500, content=f"Conversion failed: {exc}")
    except subprocess.TimeoutExpired:
        return Response(status_code=504, content="Conversion timed out (>30 min)")
    except zipfile.BadZipFile:
        return Response(status_code=400, content="Invalid ZIP archive")
    except ValueError as exc:
        return Response(status_code=400, content=str(exc))

    return Response(
        content=data,
        media_type='application/x-3ds-rom',
        headers={
            'Content-Disposition': f'attachment; filename="{filename}"',
            'Content-Length': str(len(data)),
        },
    )


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

    def _run() -> bytes:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            iso_path = tmp / (stem + '.iso')

            # PSP CHDs are hard-disk images (createhd), not CD-ROM images (createcd).
            # Use extracthd which outputs a raw ISO directly.
            r = subprocess.run(
                ['chdman', 'extracthd', '-i', str(chd_path), '-o', str(iso_path)],
                capture_output=True, text=True, timeout=600,
            )
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip() or r.stdout.strip() or 'chdman failed')

            if fmt == 'iso':
                return iso_path.read_bytes()

            # Step 2 — ISO → CSO
            cso_path = tmp / (stem + '.cso')
            if 'maxcso' in (cso_tool or ''):
                cmd = [cso_tool, str(iso_path), '--output', str(cso_path)]
            else:
                # ciso: ciso <level 1-9> <input> <output>
                cmd = [cso_tool, '9', str(iso_path), str(cso_path)]

            r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip() or r.stdout.strip() or 'CSO conversion failed')

            return cso_path.read_bytes()

    try:
        data = await asyncio.get_event_loop().run_in_executor(None, _run)
    except RuntimeError as exc:
        return Response(status_code=500, content=f"Conversion failed: {exc}")
    except subprocess.TimeoutExpired:
        return Response(status_code=504, content="Conversion timed out (>10 min)")

    ext       = '.' + fmt   # .iso or .cso
    mime      = 'application/x-iso9660-image' if fmt == 'iso' else 'application/x-cso'
    filename  = stem + ext
    return Response(
        content=data,
        media_type=mime,
        headers={
            'Content-Disposition': f'attachment; filename="{filename}"',
            'Content-Length': str(len(data)),
        },
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

    def _run_extraction() -> bytes:
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = Path(tmpdir) / (stem + out_ext)
            r = subprocess.run(
                ['chdman', 'extractcd', '-i', str(chd_path), '-o', str(out_file)],
                capture_output=True, text=True, timeout=600,
            )
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip() or r.stdout.strip() or 'unknown error')

            buf = io.BytesIO()
            with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
                for f in sorted(Path(tmpdir).iterdir()):
                    zf.write(f, f.name)
            return buf.getvalue()

    try:
        data = await asyncio.get_event_loop().run_in_executor(None, _run_extraction)
    except RuntimeError as exc:
        return Response(status_code=500, content=f"Extraction failed: {exc}")
    except subprocess.TimeoutExpired:
        return Response(status_code=504, content="Extraction timed out (>10 min)")

    fmt      = 'gdi' if sys_up in _GDI_SYSTEMS else 'cue'
    filename = f"{stem}_{fmt}.zip"
    return Response(
        content=data,
        media_type='application/zip',
        headers={
            'Content-Disposition': f'attachment; filename="{filename}"',
            'Content-Length': str(len(data)),
        },
    )


# ── Regular file serving ─────────────────────────────────────────────────────

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
        return Response(status_code=416, headers={'Content-Range': f'bytes */{file_size}'})

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


def _extract_formats_for_entry(system: str, filename: str) -> tuple[str | None, list[str]]:
    sys_up = system.upper()
    suffix = Path(filename).suffix.lower()

    if suffix == '.chd':
        if sys_up in _PSP_SYSTEMS:
            return 'psp', ['iso', 'cso']
        if sys_up in _GDI_SYSTEMS:
            return 'gdi', ['gdi']
        if sys_up in _CUE_SYSTEMS:
            return 'cue', ['cue']
    elif suffix == '.rvz' and sys_up in _GC_SYSTEMS:
        return 'rvz', ['iso']
    elif sys_up in _3DS_SYSTEMS and suffix in {'.3ds', '.zip'}:
        return '3ds', ['cia', 'decrypted_cia']

    return None, []


def _materialize_3ds_source(source_path: Path, tmp_dir: Path) -> tuple[Path, str]:
    suffix = source_path.suffix.lower()
    if suffix == '.3ds':
        return source_path, source_path.stem

    if suffix != '.zip':
        raise ValueError(
            "3DS conversion currently supports raw .3ds files or .zip archives containing one .3ds file"
        )

    with zipfile.ZipFile(source_path) as zf:
        members = [
            info for info in zf.infolist()
            if not info.is_dir() and Path(info.filename).suffix.lower() == '.3ds'
        ]
        if not members:
            raise ValueError("ZIP archive does not contain a .3ds file")
        if len(members) > 1:
            raise ValueError("ZIP archive must contain exactly one .3ds file")

        member = members[0]
        member_name = Path(member.filename).name
        extracted = tmp_dir / member_name
        with zf.open(member) as src, open(extracted, 'wb') as dst:
            shutil.copyfileobj(src, dst)
        return extracted, extracted.stem


def _expand_command_template(template: str, **values: str) -> list[str]:
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

    expanded: list[str] = []
    for part in parts:
        updated = part
        for key, value in values.items():
            updated = updated.replace(f'{{{key}}}', value)
        expanded.append(updated)
    return expanded


def _find_single_output(tmp_dir: Path) -> Path | None:
    outputs = sorted(p for p in tmp_dir.rglob('*.cia') if p.is_file())
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
    '.cia': 'application/x-3ds-rom',
    '.n64': 'application/x-n64-rom',
    '.z64': 'application/x-n64-rom',
    '.iso': 'application/x-iso9660-image',
    '.chd': 'application/x-chd',
    '.cso': 'application/x-cso',
    '.rvz': 'application/x-rvz',
    '.zip': 'application/zip',
    '.7z':  'application/x-7z-compressed',
    '.rar': 'application/x-rar-compressed',
}


def _content_type(filename: str) -> str:
    return _CONTENT_TYPES.get(Path(filename).suffix.lower(), 'application/octet-stream')
