#!/usr/bin/env python3
"""Run a command on the MiSTer over SSH and print stdout/stderr.

Usage: python tools/mister_ssh.py "<command>"
Host/credentials come from MISTER_HOST / MISTER_USER / MISTER_PASS env vars,
defaulting to the LAN device at 192.168.1.16.
"""
import os
import sys

import paramiko

HOST = os.environ.get("MISTER_HOST", "192.168.1.16")
USER = os.environ.get("MISTER_USER", "root")
PASS = os.environ.get("MISTER_PASS", "1")


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "uname -a"
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=15,
                   look_for_keys=False, allow_agent=False)
    timeout = float(os.environ.get("MISTER_TIMEOUT", "120"))
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    rc = stdout.channel.recv_exit_status()
    sys.stdout.write(out)
    if err:
        sys.stdout.write("--- stderr ---\n" + err)
    client.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
