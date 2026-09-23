#!/usr/bin/env python3
"""Push files to a MiSTer over SSH and optionally run a command there.

Development helper for the on-device GameSync client. Credentials come from the
command line or the MISTER_HOST / MISTER_USER / MISTER_PASSWORD environment
variables so nothing sensitive lives in the repository.

    python mister/tools/deploy.py --host 192.168.1.41 --password 1 \
        --put mister/tools/spike_input.py /media/fat/Scripts/GameSync_Spike.sh \
        --run "python3 /media/fat/Scripts/GameSync_Spike.sh --probe"
"""

import argparse
import os
import posixpath
import stat
import sys

try:
    import paramiko
except ImportError:  # pragma: no cover - developer machine only
    sys.exit("paramiko required: pip install paramiko")


def connect(args):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        args.host,
        port=args.port,
        username=args.user,
        password=args.password,
        key_filename=args.key or None,
        timeout=args.timeout,
    )
    return client


def makedirs(sftp, remote_dir):
    parts = [p for p in remote_dir.split("/") if p]
    path = ""
    for part in parts:
        path = path + "/" + part
        try:
            sftp.stat(path)
        except IOError:
            sftp.mkdir(path)


#: Shell scripts run under Linux must be LF. Everything else is uploaded
#: byte-for-byte - normalising a .pyz corrupts the zip archive.
TEXT_SUFFIXES = (".sh", ".py", ".txt", ".cfg", ".conf", ".json", ".md")


def put(sftp, local, remote, executable=True):
    """Upload with an atomic rename, normalising line endings for text only."""
    makedirs(sftp, posixpath.dirname(remote))
    with open(local, "rb") as handle:
        data = handle.read()
    if remote.lower().endswith(TEXT_SUFFIXES):
        data = data.replace(b"\r\n", b"\n")
    tmp = remote + ".part"
    with sftp.open(tmp, "wb") as handle:
        handle.write(data)
    try:
        sftp.posix_rename(tmp, remote)
    except (IOError, AttributeError):
        try:
            sftp.remove(remote)
        except IOError:
            pass
        sftp.rename(tmp, remote)
    if executable:
        try:
            sftp.chmod(remote, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP
                       | stat.S_IROTH | stat.S_IXOTH)
        except IOError:
            pass  # exfat ignores chmod
    print("put %s -> %s (%d bytes)" % (local, remote, len(data)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("MISTER_HOST"))
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--user", default=os.environ.get("MISTER_USER", "root"))
    parser.add_argument("--password",
                        default=os.environ.get("MISTER_PASSWORD"))
    parser.add_argument("--key", default=os.environ.get("MISTER_KEY"))
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--put", nargs=2, action="append", metavar=("LOCAL", "REMOTE"),
                        default=[], help="upload a file (repeatable)")
    parser.add_argument("--get", nargs=2, action="append", metavar=("REMOTE", "LOCAL"),
                        default=[], help="download a file (repeatable)")
    parser.add_argument("--run", action="append", default=[],
                        help="run a command after uploading (repeatable)")
    parser.add_argument("--cat", action="append", default=[],
                        help="print a remote file (repeatable)")
    args = parser.parse_args()

    if not args.host:
        parser.error("--host or MISTER_HOST required")

    client = connect(args)
    try:
        if args.put:
            sftp = client.open_sftp()
            for local, remote in args.put:
                put(sftp, local, remote)
            sftp.close()
        # Uploads, then commands, then downloads - so --get can collect
        # whatever --run produced.
        for command in args.run:
            print("--- run: %s" % command)
            _, stdout, stderr = client.exec_command(command, timeout=600)
            out = stdout.read().decode("utf-8", "replace")
            err = stderr.read().decode("utf-8", "replace")
            if out:
                print(out.rstrip())
            if err:
                print("[stderr] " + err.rstrip())
        for remote in args.cat:
            print("--- cat: %s" % remote)
            _, stdout, _ = client.exec_command("cat %s" % remote, timeout=60)
            print(stdout.read().decode("utf-8", "replace").rstrip())
        if args.get:
            sftp = client.open_sftp()
            for remote, local in args.get:
                sftp.get(remote, local)
                print("got %s -> %s" % (remote, local))
            sftp.close()
    finally:
        client.close()


if __name__ == "__main__":
    main()
