import argparse
import os
import subprocess
import sys


def run(cmd):
    print(">", " ".join(cmd))
    subprocess.check_call(cmd)


def main():
    parser = argparse.ArgumentParser(
        description="Push file to DS via Raspberry Pi bridge"
    )
    parser.add_argument("input_file", nargs="?", help="Local file to send")
    parser.add_argument("--pi", default="pi@192.168.1.201", help="Pi SSH target")
    parser.add_argument("--ds-ip", default="192.168.4.137", help="DS IP")
    parser.add_argument("--ds-port", type=int, default=5000, help="DS FTP port")
    parser.add_argument("--ds-path", default="roms/nds", help="Destination path on DS")
    parser.add_argument(
        "--list-dir", action="store_true", help="List files in the DS directory"
    )

    args = parser.parse_args()

    if args.list_dir:
        ftp_script = f"""
from ftplib import FTP

ftp = FTP()
ftp.set_pasv(True)
ftp.connect("{args.ds_ip}", {args.ds_port}, timeout=10)
ftp.login()
ftp.cwd("{args.ds_path}")
print("Files in {args.ds_path}:")
for f in ftp.nlst():
    print(f)
try:
    ftp.quit()
except:
    ftp.close()
"""
        subprocess.run(["ssh", args.pi, "python3 -"], input=ftp_script.encode())
        return

    if not args.input_file:
        print("Error: input_file required unless using --list-dir")
        sys.exit(1)

    filename = os.path.basename(args.input_file)
    remote_tmp = f"/tmp/{filename}"

    # 1️⃣ SCP to Pi
    run(["scp", args.input_file, f"{args.pi}:{remote_tmp}"])

    # 2️⃣ FTP from Pi to DS (piped script)
    ftp_script = f"""
from ftplib import FTP
import os

ftp = FTP()
ftp.set_pasv(True)
ftp.connect("{args.ds_ip}", {args.ds_port}, timeout=10)
ftp.login()
ftp.cwd("{args.ds_path}")

with open("{remote_tmp}", "rb") as f:
    ftp.storbinary("STOR {filename}", f)

try:
    ftp.quit()
except:
    ftp.close()
os.remove("{remote_tmp}")
"""

    print("> ssh", args.pi, "python3 -")
    subprocess.run(["ssh", args.pi, "python3 -"], input=ftp_script.encode(), check=True)

    print("✅ Transfer complete")


if __name__ == "__main__":
    main()
