from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.config import settings


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _third_party_dir() -> Path:
    return _repo_root() / "server" / "third_party" / "mcr2vmp"


def _binary_path() -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    return _third_party_dir() / f"mcr2vmp{suffix}"


def _source_paths() -> list[Path]:
    base = _third_party_dir()
    return [
        base / "mcr2vmp.c",
        base / "aes.c",
        base / "sha1.c",
        base / "include" / "aes.h",
        base / "include" / "sha1.h",
    ]


def _needs_rebuild(binary: Path, sources: list[Path]) -> bool:
    if not binary.exists():
        return True
    binary_mtime = binary.stat().st_mtime
    return any(src.stat().st_mtime > binary_mtime for src in sources)


def _find_compiler() -> str:
    for candidate in ("gcc", "cc", "clang"):
        found = shutil.which(candidate)
        if found:
            return found
    raise RuntimeError(
        "mcr2vmp helper is not built and no C compiler was found. "
        "Install gcc/clang or provide a prebuilt mcr2vmp binary."
    )


def ensure_mcr2vmp_binary() -> Path:
    third_party = _third_party_dir()
    binary = _binary_path()
    sources = _source_paths()

    missing = [str(path) for path in sources if not path.exists()]
    if missing:
        raise RuntimeError(f"Missing mcr2vmp source files: {', '.join(missing)}")

    if not _needs_rebuild(binary, sources):
        return binary

    compiler = _find_compiler()
    cmd = [
        compiler,
        "-O2",
        "-I",
        str(third_party / "include"),
        "-o",
        str(binary),
        str(third_party / "mcr2vmp.c"),
        str(third_party / "aes.c"),
        str(third_party / "sha1.c"),
    ]
    subprocess.run(cmd, check=True, cwd=third_party, capture_output=True, text=True)
    if not binary.exists():
        raise RuntimeError("mcr2vmp compilation reported success but no binary was produced")
    return binary


def _conversion_tmp_dir() -> str | None:
    """Same role as ``app.routes.roms._conversion_tmp_dir``: route every
    conversion's working dir to ``settings.tmp_dir`` when configured.
    Returns ``None`` (system default) when unset or unwritable."""
    if settings.tmp_dir is None:
        return None
    try:
        settings.tmp_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return str(settings.tmp_dir)


def convert_raw_card_to_vmp(raw: bytes) -> bytes:
    binary = ensure_mcr2vmp_binary()
    with tempfile.TemporaryDirectory(dir=_conversion_tmp_dir()) as tmpdir:
        tmp = Path(tmpdir)
        input_path = tmp / "card.mcr"
        output_path = tmp / "card.mcr.VMP"
        input_path.write_bytes(raw)
        subprocess.run([str(binary), str(input_path)], check=True, cwd=tmp, capture_output=True, text=True)
        if not output_path.exists():
            raise RuntimeError("mcr2vmp did not produce the expected VMP output file")
        return output_path.read_bytes()
