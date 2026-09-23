#!/usr/bin/env python3
"""Receive wiiusync's WHBLogUdp output.

The Wii U broadcasts every WHBLogPrintf line to UDP port 4405, so this is the
only way to see where boot stops when the screen itself never updates.

    python wiiu/tools/udplog.py

Leave it running, then launch the app on the console. The PC and the Wii U
must be on the same LAN segment (broadcast does not cross subnets or most
guest/AP-isolated Wi-Fi).
"""

import socket
import sys

PORT = 4405


def main() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("", PORT))
    except OSError as exc:
        print(f"cannot bind UDP {PORT}: {exc}", file=sys.stderr)
        return 1

    print(f"listening on UDP {PORT} — launch wiiusync on the console (Ctrl-C to stop)")
    while True:
        data, addr = sock.recvfrom(2048)
        text = data.decode("utf-8", errors="replace").rstrip("\r\n\0")
        if text:
            print(f"[{addr[0]}] {text}", flush=True)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        pass
