#!/usr/bin/env python3
"""
Build PSP EBOOT.PBP on Raspberry Pi and copy to Windows.
Connects via SSH key auth, runs make clean && make, then copies EBOOT.PBP.
"""

import argparse
import os
import sys
import paramiko

HOST = "192.168.1.201"
USER = "pi"
REMOTE_DIR = "/home/pi/Documents/GameSync/psp"
REMOTE_FILE = f"{REMOTE_DIR}/EBOOT.PBP"
LOCAL_DEST_DEFAULT = r"K:\PSP\GAME\pspsync\EBOOT.PBP"
LOCAL_FALLBACK = "..\\psp\\EBOOT.PBP"

ENV_SETUP = (
    'export PSPDEV="/home/pi/pspdev" && '
    'export PSPSDK="$PSPDEV/psp/sdk" && '
    'export PATH="$PATH:$PSPDEV/bin"'
)


def parse_args():
    parser = argparse.ArgumentParser(description="Build PSP EBOOT.PBP on Raspberry Pi")
    parser.add_argument(
        "--clean", action="store_true", help="Run 'make clean' before make"
    )
    parser.add_argument(
        "-o", "--output", default=LOCAL_DEST_DEFAULT, help="Local destination path"
    )
    return parser.parse_args()


def get_local_dest(preferred: str, fallback: str) -> str:
    if os.path.exists(os.path.dirname(preferred)):
        return preferred
    script_dir = os.path.dirname(os.path.abspath(__file__))
    fallback_path = os.path.join(script_dir, fallback)
    fallback_dir = os.path.dirname(fallback_path)
    if os.path.exists(fallback_dir):
        return fallback_path
    return preferred


def run_command(ssh: paramiko.SSHClient, command: str) -> int:
    print(f"$ {command}")
    stdin, stdout, stderr = ssh.exec_command(command, get_pty=True)
    for line in iter(stdout.readline, ""):
        print(line, end="")
    exit_code = stdout.channel.recv_exit_status()
    if exit_code != 0:
        err = stderr.read().decode()
        if err:
            print(err, file=sys.stderr)
    return exit_code


def main():
    args = parse_args()

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    print(f"Connecting to {USER}@{HOST}...")
    ssh.connect(HOST, username=USER, look_for_keys=True, allow_agent=True)
    print("Connected.\n")

    try:
        make_cmd = "make"
        if args.clean:
            make_cmd = "make clean && make"

        rc = run_command(ssh, f"{ENV_SETUP} && cd {REMOTE_DIR} && {make_cmd}")
        if rc != 0:
            print(f"\nBuild failed with exit code {rc}.", file=sys.stderr)
            sys.exit(rc)

        local_dest = get_local_dest(args.output, LOCAL_FALLBACK)
        print(f"\nCopying {REMOTE_FILE} -> {local_dest} ...")
        sftp = ssh.open_sftp()
        sftp.get(REMOTE_FILE, local_dest)
        sftp.close()
        print("Done.")
    finally:
        ssh.close()


if __name__ == "__main__":
    main()
