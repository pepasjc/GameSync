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
            "'cia' (3DS decrypted CIA, also installable on CFW hardware), "
            "'decrypted_cci' (3DS decrypted CCI for emulators)"
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
        if fmt in _3DS_EXTRACT_SPECS:
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

    def _run() -> tuple[str, bytes]:
        with tempfile.TemporaryDirectory() as tmpdir:
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

    # Preserve the original ROM stem — the ZIP extension is the only hint the
    # client needs to distinguish a CHD extraction from the raw file.
    filename = f"{stem}.zip"
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
    elif sys_up in _3DS_SYSTEMS and suffix in _3DS_CART_EXTENSIONS.union({'.zip'}):
        return '3ds', list(_3DS_EXTRACT_FORMATS)

    return None, []


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
    '.chd': 'application/x-chd',
    '.cso': 'application/x-cso',
    '.rvz': 'application/x-rvz',
    '.zip': 'application/zip',
    '.7z':  'application/x-7z-compressed',
    '.rar': 'application/x-rar-compressed',
}


def _content_type(filename: str) -> str:
    return _CONTENT_TYPES.get(Path(filename).suffix.lower(), 'application/octet-stream')
