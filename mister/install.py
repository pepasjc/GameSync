#!/usr/bin/env python3
"""Install the GameSync client onto a MiSTer over the network.

    python mister/install.py 192.168.1.41

That is the whole happy path: it builds the zipapp, checks the target really is
a MiSTer with a usable Python, uploads the launcher and payload, and then runs
the client's own selftest on the device so a reported success is a verified one.

MiSTer's stock credentials (root/1) are the default; override with --user and
--password, or use --key for an SSH key.

Optionally seed the config at the same time:

    python mister/install.py 192.168.1.41 \
        --server-url http://192.168.1.10:8000 --api-key SECRET --rom-target usb

Uninstall with --uninstall.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import posixpath
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(REPO_ROOT, "mister", "tools")

REMOTE_SCRIPTS = "/media/fat/Scripts"
REMOTE_DIR = REMOTE_SCRIPTS + "/.gamesync"
REMOTE_PYZ = REMOTE_DIR + "/gamesync.pyz"
REMOTE_LAUNCHER = REMOTE_SCRIPTS + "/GameSync.sh"

# Config, state and log follow the MiSTer script convention:
# /media/fat/Scripts/.config/<script>/ , the same place the stock downloader
# keeps its data. Defined in shared/mister.py because the desktop client writes
# the state file there too.
REMOTE_CONFIG_DIR = REMOTE_SCRIPTS + "/.config/gamesync"
REMOTE_CONFIG = REMOTE_CONFIG_DIR + "/gamesync.cfg"
LEGACY_REMOTE_CONFIG = "/media/fat/3dssync.cfg"
LEGACY_REMOTE_STATE = "/media/fat/3dssync_state.json"
REMOTE_STATE = REMOTE_CONFIG_DIR + "/state.json"

#: MiSTer ships with these; they are the documented defaults, not a secret.
DEFAULT_USER = "root"
DEFAULT_PASSWORD = "1"

MIN_PYTHON = (3, 7)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fail(message):
    print("error: %s" % message, file=sys.stderr)
    return 1


def run(client, command, timeout=120):
    _, stdout, stderr = client.exec_command(command, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace").strip()
    err = stderr.read().decode("utf-8", "replace").strip()
    status = stdout.channel.recv_exit_status()
    return status, out, err


def verify_target(client):
    """Refuse to scatter files over a machine that is not a MiSTer."""
    status, out, _ = run(client, "test -d /media/fat && echo yes || echo no")
    if status != 0 or out != "yes":
        return "/media/fat not found - this does not look like a MiSTer"

    status, out, err = run(client, "python3 -c 'import sys;print(\"%d.%d.%d\" % sys.version_info[:3])'")
    if status != 0:
        return "python3 missing or not runnable on the device (%s)" % (err or out)
    try:
        version = tuple(int(part) for part in out.split("."))
    except ValueError:
        return "could not read the device python version (%r)" % out
    if version < MIN_PYTHON:
        return "device python %s is older than %d.%d" % (
            out, MIN_PYTHON[0], MIN_PYTHON[1])
    print("  device python        %s" % out)

    status, out, _ = run(
        client,
        "touch %s/.gamesync_write_test && rm -f %s/.gamesync_write_test "
        "&& echo yes || echo no" % (REMOTE_SCRIPTS, REMOTE_SCRIPTS),
    )
    if out != "yes":
        return "%s is not writable" % REMOTE_SCRIPTS
    return None


def _exists(sftp, path):
    try:
        sftp.stat(path)
        return True
    except IOError:
        return False


def _write_remote(sftp, path, body):
    """Write via a .part file and rename, so a cut connection cannot truncate."""
    with sftp.open(path + ".part", "wb") as handle:
        handle.write(body.encode("utf-8"))
    try:
        sftp.posix_rename(path + ".part", path)
    except (IOError, AttributeError):
        try:
            sftp.remove(path)
        except IOError:
            pass
        sftp.rename(path + ".part", path)


def migrate_legacy(client, sftp):
    """Move a pre-0.5.4 config and state into the script-convention directory.

    The state file matters most: losing it makes every save look like a
    conflict on the next sync.
    """
    for legacy, current, label in (
        (LEGACY_REMOTE_CONFIG, REMOTE_CONFIG, "config"),
        (LEGACY_REMOTE_STATE, REMOTE_STATE, "sync state"),
    ):
        if not _exists(sftp, legacy) or _exists(sftp, current):
            continue
        status, _, err = run(client, "cp %s %s" % (legacy, current))
        if status == 0:
            print("  migrated %-12s %s -> %s" % (label, legacy, current))
        else:
            print("  migrate failed       %s (%s)" % (legacy, err))


CONFIG_TEMPLATE = """# GameSync configuration for MiSTer
#
#   SERVER_URL   the GameSync server, e.g. http://192.168.1.10:8000
#   API_KEY      must match the server's SYNC_API_KEY
#   ROM_TARGET   sd | usb - where downloaded ROMs are installed
#
# MiSTer is standardised enough that these three keys are all there is; save
# folders, games roots and system names all come from shared/mister.py.

SERVER_URL=%s
API_KEY=%s
ROM_TARGET=%s
"""


def write_config(client, sftp, args):
    """Always leave a complete, commented config on the device.

    Every key is written even when its value is unknown, so the file shows what
    can be set rather than looking like there is nowhere to put the server.
    """
    if _exists(sftp, REMOTE_CONFIG) and not args.force_config:
        if args.server_url or args.api_key or args.rom_target:
            print("  config               kept (%s already exists; "
                  "use --force-config to replace)" % REMOTE_CONFIG)
        return

    body = CONFIG_TEMPLATE % (
        (args.server_url or "").rstrip("/"),
        args.api_key or "",
        args.rom_target or "sd",
    )
    _write_remote(sftp, REMOTE_CONFIG, body)
    if args.server_url and args.api_key:
        print("  config               written to %s" % REMOTE_CONFIG)
    else:
        print("  config               template written to %s" % REMOTE_CONFIG)


def uninstall(client, sftp):
    for path in (REMOTE_PYZ, REMOTE_LAUNCHER):
        try:
            sftp.remove(path)
            print("  removed              %s" % path)
        except IOError:
            print("  not present          %s" % path)
    run(client, "rmdir %s 2>/dev/null" % REMOTE_DIR)
    print("\nGameSync removed. %s was left alone." % REMOTE_CONFIG)
    return 0


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("host", help="MiSTer IP address or hostname")
    parser.add_argument("--user", default=DEFAULT_USER)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    parser.add_argument("--key", help="SSH private key file instead of a password")
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--server-url", help="seed SERVER_URL in the device config")
    parser.add_argument("--api-key", help="seed API_KEY in the device config")
    parser.add_argument("--rom-target", choices=("sd", "usb"),
                        help="where downloaded ROMs are installed (default sd)")
    parser.add_argument("--force-config", action="store_true",
                        help="overwrite an existing device config")
    parser.add_argument("--no-build", action="store_true",
                        help="use the existing build_output artifacts")
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()

    try:
        import paramiko
    except ImportError:
        return fail("paramiko is required on this machine: pip install paramiko")

    build_pyz = _load("gamesync_build_pyz",
                      os.path.join(TOOLS_DIR, "build_pyz.py"))
    deploy = _load("gamesync_deploy", os.path.join(TOOLS_DIR, "deploy.py"))

    pyz = os.path.join(build_pyz.OUT_DIR, "gamesync.pyz")
    launcher = os.path.join(build_pyz.OUT_DIR, "GameSync.sh")

    if not args.uninstall:
        if args.no_build:
            missing = [p for p in (pyz, launcher) if not os.path.exists(p)]
            if missing:
                return fail("--no-build but missing: %s"
                            % ", ".join(missing))
        else:
            print("Building the client...")
            status = build_pyz.build()
            if status:
                return fail("build failed")

    print("\nConnecting to %s@%s:%d..." % (args.user, args.host, args.port))
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            args.host,
            port=args.port,
            username=args.user,
            password=None if args.key else args.password,
            key_filename=args.key or None,
            timeout=args.timeout,
            allow_agent=bool(args.key),
            look_for_keys=bool(args.key),
        )
    except Exception as exc:  # noqa: BLE001
        return fail("could not connect: %s\n"
                    "       MiSTer needs SSH enabled and reachable on the "
                    "network." % exc)

    try:
        sftp = client.open_sftp()
        if args.uninstall:
            return uninstall(client, sftp)

        problem = verify_target(client)
        if problem:
            return fail(problem)

        print("\nInstalling...")
        run(client, "mkdir -p %s" % REMOTE_CONFIG_DIR)
        deploy.put(sftp, pyz, REMOTE_PYZ)
        deploy.put(sftp, launcher, REMOTE_LAUNCHER)
        migrate_legacy(client, sftp)
        write_config(client, sftp, args)
        sftp.close()

        print("\nVerifying on the device...")
        status, out, err = run(client, "python3 %s --selftest" % REMOTE_PYZ,
                               timeout=180)
        print(out or err)
        if status != 0:
            return fail("the client did not pass its selftest on the device")

        print("\nInstalled. On the MiSTer: OSD -> Scripts -> GameSync")
        status, out, _ = run(
            client,
            "grep -c '^SERVER_URL=.\\+' %s 2>/dev/null || echo 0" % REMOTE_CONFIG,
        )
        if out.strip() != "1":
            print("\nNot connected to a server yet. Edit %s:" % REMOTE_CONFIG)
            print("    SERVER_URL=http://<your-server>:8000")
            print("    API_KEY=<the server's SYNC_API_KEY>")
            print("or re-run with --server-url and --api-key.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
