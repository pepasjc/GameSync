"""Bytecode baked into the zipapp must load, and must not break the archive."""

import subprocess
import sys
import zipapp
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for candidate in (ROOT, ROOT / "mister"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from gamesync import precompile  # noqa: E402


def _make_app(tmp_path):
    src = tmp_path / "src"
    (src / "pkg").mkdir(parents=True)
    (src / "pkg" / "__init__.py").write_text("")
    (src / "pkg" / "mod.py").write_text("VALUE = 42\n")
    (src / "__main__.py").write_text(
        "import pkg.mod, sys\n"
        "print(pkg.mod.VALUE, pkg.mod.__spec__.origin.endswith('.pyc') or "
        "type(pkg.mod.__loader__).__name__)\n")
    target = tmp_path / "app.pyz"
    zipapp.create_archive(str(src), str(target),
                          interpreter="/usr/bin/env python3")
    return target


def test_every_module_but_main_gets_bytecode(tmp_path):
    target = _make_app(tmp_path)
    assert precompile.compile_archive(str(target)) == 2
    names = set(zipfile.ZipFile(target).namelist())
    assert {"pkg/__init__.pyc", "pkg/mod.pyc"} <= names
    assert "__main__.pyc" not in names


def test_shebang_survives_and_the_app_still_runs(tmp_path):
    target = _make_app(tmp_path)
    precompile.compile_archive(str(target))
    assert target.read_bytes().startswith(b"#!/usr/bin/env python3\n")
    result = subprocess.run([sys.executable, str(target)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("42")


def test_running_it_twice_does_not_duplicate_entries(tmp_path):
    target = _make_app(tmp_path)
    precompile.compile_archive(str(target))
    precompile.compile_archive(str(target))
    names = zipfile.ZipFile(target).namelist()
    assert len(names) == len(set(names))
