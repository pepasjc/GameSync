#!/usr/bin/env python3
"""Build the MiSTer GameSync client into a single executable zipapp.

MiSTer has Python 3.9.6 but no pip, so everything ships inside one .pyz:
the gamesync package, the vendored shared/ modules and the bundled font.

    python mister/tools/build_pyz.py
    python mister/tools/build_pyz.py --deploy --host 192.168.1.41 --password 1

Output: build_output/mister/GameSync.sh and gamesync.pyz
"""

import argparse
import ast
import os
import shutil
import subprocess
import sys
import tempfile
import zipapp

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_DIR = os.path.join(ROOT, "build_output", "mister")

#: Only the shared modules the client actually needs, so the payload stays small.
SHARED_MODULES = [
    "__init__.py",
    "systems.py",
    "systems.json",
    "sync_id.py",
    "mister.py",
    "mister_saves.py",
    "mister_scan.py",
    "mister_install.py",
    "msu.py",
    "title_match.py",
    "saturn_format.py",
    "rom_id/__init__.py",
    "rom_id/normalizer.py",
    "rom_id/saturn.py",
    # Imported unconditionally by rom_id/__init__.py; without it the whole
    # shared package fails to import on the device.
    "rom_id/dreamcast.py",
]

LAUNCHER = """#!/usr/bin/env bash
# GameSync for MiSTer - launcher for the Scripts menu.
#
# MiSTer runs Scripts with stdin/stdout on a real VT but without TERM set, and
# /media/usb0 is mounted noexec, so the payload always lives on the SD card.
set -u
export TERM="${TERM:-linux}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "${DIR}/.gamesync/gamesync.pyz" "$@"
status=$?

# When a script ends, MiSTer leaves its console on screen until a key is
# pressed. The app has already drawn its own goodbye, so on a clean exit
# ask MiSTer to go straight back to the menu instead. A crash (non-zero)
# keeps the console so the traceback can be read, and so do the text
# subcommands. GAMESYNC_STAY=1 opts out for the SSH dev loop.
case " $* " in *" --selftest "*) stay=1 ;; *) stay="${GAMESYNC_STAY:-0}" ;; esac
if [ "$status" -eq 0 ] && [ "$stay" != 1 ] && [ -w /dev/MiSTer_cmd ]; then
    echo "load_core /media/fat/menu.rbf" > /dev/MiSTer_cmd
fi
exit "$status"
"""

MAIN = """import sys

from gamesync.__main__ import main

sys.exit(main())
"""


def check_python39(path):
    """Every shipped file must parse under the device's Python 3.9.6."""
    problems = []
    for base, _dirs, files in os.walk(path):
        for name in files:
            if not name.endswith(".py"):
                continue
            full = os.path.join(base, name)
            with open(full, "r", encoding="utf-8") as handle:
                source = handle.read()
            try:
                ast.parse(source, feature_version=(3, 9))
            except SyntaxError as exc:
                problems.append("%s:%s %s" % (full, exc.lineno, exc.msg))
    return problems


def build(deploy=False, host=None, password=None, user="root", port=22):
    os.makedirs(OUT_DIR, exist_ok=True)
    stage = tempfile.mkdtemp(prefix="gamesync-build-")
    try:
        shutil.copytree(os.path.join(ROOT, "mister", "gamesync"),
                        os.path.join(stage, "gamesync"),
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

        shared_dst = os.path.join(stage, "shared")
        for relative in SHARED_MODULES:
            source = os.path.join(ROOT, "shared", relative)
            if not os.path.exists(source):
                print("warning: missing %s" % source)
                continue
            target = os.path.join(shared_dst, relative)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(source, target)

        with open(os.path.join(stage, "__main__.py"), "w", newline="\n") as handle:
            handle.write(MAIN)

        problems = check_python39(stage)
        if problems:
            print("Python 3.9 syntax check FAILED:")
            for problem in problems:
                print("  " + problem)
            return 1
        print("Python 3.9 syntax check: ok")

        # Import the staged tree in a clean interpreter: a shared module
        # missing from SHARED_MODULES parses fine and only fails on the
        # device, where it takes the whole app down before it draws a pixel.
        result = subprocess.run(
            [sys.executable, "-c",
             "import gamesync.app, gamesync.sync, gamesync.downloads"],
            cwd=stage, capture_output=True, text=True,
            env={**os.environ, "PYTHONPATH": stage})
        if result.returncode:
            print("staged import check FAILED:")
            print(result.stderr.strip())
            return 1
        print("staged import check: ok")

        pyz = os.path.join(OUT_DIR, "gamesync.pyz")
        if os.path.exists(pyz):
            os.remove(pyz)
        zipapp.create_archive(stage, pyz, interpreter="/usr/bin/env python3")

        launcher = os.path.join(OUT_DIR, "GameSync.sh")
        with open(launcher, "w", newline="\n") as handle:
            handle.write(LAUNCHER)

        print("built %s (%.1f KB)" % (pyz, os.path.getsize(pyz) / 1024.0))
        print("built %s" % launcher)

        if deploy:
            if not host:
                print("--deploy needs --host")
                return 1
            deploy_script = os.path.join(ROOT, "mister", "tools", "deploy.py")
            command = [
                sys.executable, deploy_script,
                "--host", host, "--user", user, "--port", str(port),
                "--put", pyz, "/media/fat/Scripts/.gamesync/gamesync.pyz",
                "--put", launcher, "/media/fat/Scripts/GameSync.sh",
            ]
            if password:
                command += ["--password", password]
            return subprocess.call(command)
        return 0
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deploy", action="store_true")
    parser.add_argument("--host", default=os.environ.get("MISTER_HOST"))
    parser.add_argument("--user", default=os.environ.get("MISTER_USER", "root"))
    parser.add_argument("--password", default=os.environ.get("MISTER_PASSWORD"))
    parser.add_argument("--port", type=int, default=22)
    args = parser.parse_args()
    return build(args.deploy, args.host, args.password, args.user, args.port)


if __name__ == "__main__":
    sys.exit(main())
