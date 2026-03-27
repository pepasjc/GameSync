#!/usr/bin/env python3
import sys
import ftplib
import os
import argparse

def deploy_vpk(host, port, vpk_path):
    if not os.path.exists(vpk_path):
        print(f"Error: {vpk_path} not found. Run ./build.sh first.")
        sys.exit(1)

    print(f"Deploying {vpk_path} to {host}:{port}...")

    try:
        ftp = ftplib.FTP()
        ftp.connect(host, port, timeout=10)
        ftp.login()
        
        # Navigate to packages folder
        try:
            ftp.cwd('/ux0:/packages')
        except ftplib.error_perm:
            # Try alternative path
            try:
                ftp.cwd('/packages')
            except ftplib.error_perm:
                print("Error: Could not find packages folder on Vita. Make sure ftpd is running.")
                sys.exit(1)

        with open(vpk_path, 'rb') as f:
            ftp.storbinary(f'STOR vitasync.vpk', f)

        ftp.quit()
        print("Done! The VPK should appear in LiveArea under 'Packages'.")
        
    except ftplib.error_perm as e:
        print(f"FTP permission error: {e}")
        sys.exit(1)
    except ftplib.error_temp as e:
        print(f"FTP temporary error (is ftpd running on Vita?): {e}")
        sys.exit(1)
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
    parser = argparse.ArgumentParser(description='Deploy Vita Save Sync VPK via FTP')
    parser.add_argument('ip', nargs='?', default='192.168.1.19', help='Vita IP address')
    parser.add_argument('port', nargs='?', type=int, default=1337, help='FTP port')
    parser.add_argument('--build-path', default='build/vitasync.vpk', help='Path to the built VPK file')
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    vpk_path = os.path.join(script_dir, args.build_path)
    
    deploy_vpk(args.ip, args.port, vpk_path)
