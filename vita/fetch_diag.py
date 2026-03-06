#!/usr/bin/env python3
import sys
import ftplib
import os
import argparse

REMOTE_PATH_SCAN = '/ux0:/data/vitasync/diag.txt'
REMOTE_PATH_SYNC = '/ux0:/data/vitasync/sync_diag.txt'

def fetch_diag(host, port, output_path, sync_log=False):
    remote_path = REMOTE_PATH_SYNC if sync_log else REMOTE_PATH_SCAN
    print(f"Fetching {remote_path} from {host}:{port}...")

    try:
        ftp = ftplib.FTP()
        ftp.connect(host, port, timeout=10)
        ftp.login()

        chunks = []
        try:
            ftp.retrbinary(f'RETR {remote_path}', chunks.append)
        except ftplib.error_perm as e:
            print(f"Error: could not retrieve file: {e}")
            print("Has the app been run at least once? (diag.txt is created on first scan)")
            sys.exit(1)

        ftp.quit()

        data = b''.join(chunks).decode('utf-8', errors='replace')

        if output_path:
            with open(output_path, 'w') as f:
                f.write(data)
            print(f"Saved to {output_path}")
        else:
            print("--- diag.txt ---")
            print(data)
            print("--- end ---")

    except ConnectionRefusedError:
        print(f"Error: Connection refused. Is Vita ftpd running on {host}:{port}?")
        sys.exit(1)
    except TimeoutError:
        print(f"Error: Connection timed out. Is Vita reachable at {host}?")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Fetch vitasync diagnostic log from Vita via FTP')
    parser.add_argument('ip', nargs='?', default='192.168.1.17', help='Vita IP address')
    parser.add_argument('port', nargs='?', type=int, default=1337, help='FTP port')
    parser.add_argument('-o', '--output', help='Save output to file instead of printing to stdout')
    parser.add_argument('--sync', action='store_true', help='Fetch sync_diag.txt instead of diag.txt')
    args = parser.parse_args()

    fetch_diag(args.ip, args.port, args.output, sync_log=args.sync)
