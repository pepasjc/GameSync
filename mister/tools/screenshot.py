#!/usr/bin/env python3
"""Screenshot the GameSync UI off a real MiSTer.

Layout on a CRT cannot be checked by reasoning about it. This runs the client
against the cabinet's own video tty, dumps /dev/fb0 while it is drawing, pulls
the raw bytes back and writes a PNG - corrected for pixel aspect, so what you
look at is what is on the glass rather than what is in memory.

    python mister/tools/screenshot.py --host 192.168.1.40 --out shot.png
    python mister/tools/screenshot.py --host 192.168.1.40 --tab 4 --delay 8

Why it works this way:

* The client must run on the machine's console, not over the SSH channel, so
  stdin/stdout are redirected to /dev/tty2 - the VT MiSTer gives to Scripts.
* Launched over SSH nobody has switched the active VT, so the getty on tty1
  keeps the console and our KD_GRAPHICS lands on the wrong terminal. `chvt 2`
  first, or the capture comes back showing a login prompt.
* A second SSH connection does the capture: the first is blocked running the
  client, and backgrounding it means the framebuffer is restored before the
  dump happens.
* --timeout hands the console back on its own, so an aborted run never leaves
  the cabinet in graphics mode.
"""

import argparse
import os
import struct
import sys
import threading
import time

try:
    import paramiko
except ImportError:  # pragma: no cover - developer machine only
    sys.exit("paramiko required: pip install paramiko")

try:
    from PIL import Image
except ImportError:  # pragma: no cover - developer machine only
    Image = None

LAUNCHER = "/media/fat/Scripts/GameSync.sh"
REMOTE_RAW = "/tmp/gamesync_fb.raw"

#: fb_var_screeninfo: xres, yres, ..., bits_per_pixel at offset 24.
FBIOGET_VSCREENINFO = 0x4600
FBIOGET_FSCREENINFO = 0x4602

GEOMETRY = r"""
import fcntl, os, struct
fd = os.open("/dev/fb0", os.O_RDWR)
var = bytearray(160); fcntl.ioctl(fd, 0x4600, var, True)
xres, yres = struct.unpack_from("2I", var, 0)
bpp = struct.unpack_from("I", var, 24)[0]
fix = bytearray(80); fcntl.ioctl(fd, 0x4602, fix, True)
stride = struct.unpack_from("I", fix, 44)[0]
smem_start = struct.unpack_from("I", fix, 16)[0]
print("%d %d %d %d %d" % (xres, yres, bpp, stride, smem_start))
os.close(fd)
"""


MEM_DUMP = r"""
import mmap, os
start, size, path = %d, %d, %r
fd = os.open("/dev/mem", os.O_RDONLY | os.O_SYNC)
view = mmap.mmap(fd, size, mmap.MAP_SHARED, mmap.PROT_READ, offset=start)
with open(path, "wb") as out:
    out.write(view[:size])
view.close(); os.close(fd)
"""


def connect(args):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(args.host, port=args.port, username=args.user,
                   password=args.password, key_filename=args.key or None,
                   timeout=args.timeout, allow_agent=False,
                   look_for_keys=bool(args.key))
    return client


def run(client, command, timeout=120):
    _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    return out, err


def geometry(client):
    out, err = run(client, "python3 - <<'PYEOF'\n%s\nPYEOF" % GEOMETRY)
    parts = out.split()
    if len(parts) != 5:
        raise SystemExit("could not read framebuffer geometry: %s%s" % (out, err))
    return tuple(int(p) for p in parts)


def capture(client, stride, height, smem_start):
    """Dump the framebuffer to REMOTE_RAW.

    Kernel 6.18's fbdev refuses read() on /dev/fb0 (the MiSTer_fb driver has
    no mmap/read hooks), so a short dump falls back to /dev/mem at the
    physical address the driver reports - the same route the client itself
    draws through.
    """
    size = stride * height
    run(client, "dd if=/dev/fb0 bs=%d count=%d of=%s 2>/dev/null"
        % (stride, height, REMOTE_RAW))
    out, _ = run(client, "stat -c %%s %s 2>/dev/null" % REMOTE_RAW)
    if out.strip().isdigit() and int(out.strip()) >= size:
        return
    # read() on /dev/mem faults outside System RAM; mmap does not.
    run(client, "python3 - <<'PYEOF'\n%s\nPYEOF"
        % (MEM_DUMP % (smem_start, size, REMOTE_RAW)))


def to_png(raw, width, height, bpp, stride, path, correct_aspect=True):
    if Image is None:
        raise SystemExit("Pillow required for PNG output: pip install pillow")
    if bpp != 32:
        raise SystemExit("only 32 bpp is supported, got %d" % bpp)

    # Drop any stride padding so the buffer is tightly packed.
    row_bytes = width * 4
    if stride != row_bytes:
        raw = b"".join(raw[y * stride:y * stride + row_bytes]
                       for y in range(height))

    image = Image.frombytes("RGB", (width, height), raw, "raw", "BGRX")
    if correct_aspect and height <= 288:
        # A 240p mode is a 4:3 picture, so each pixel is wider than it is tall
        # on screen. Scale to square pixels with NEAREST: the point is to see
        # the real pixel grid, not a prettier resampled version of it.
        image = image.resize((width, height * 2), Image.NEAREST)
    image.save(path)
    return image.size


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default=os.environ.get("MISTER_HOST"),
                        required=not os.environ.get("MISTER_HOST"))
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--user", default=os.environ.get("MISTER_USER", "root"))
    parser.add_argument("--password",
                        default=os.environ.get("MISTER_PASSWORD", "1"))
    parser.add_argument("--key", default=os.environ.get("MISTER_KEY", ""))
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--out", default="mister-shot.png")
    parser.add_argument("--tab", type=int, default=0,
                        help="tab to open: 0 saves, 1 catalog, 2 installed, "
                             "3 downloads, 4 settings")
    parser.add_argument("--delay", type=float, default=7.0,
                        help="seconds to wait before capturing; the first scan "
                             "and the server round-trip both have to finish")
    parser.add_argument("--hold", type=float, default=16.0,
                        help="--timeout passed to the client; must exceed "
                             "--delay or the console is handed back first")
    parser.add_argument("--raw", default="",
                        help="also write the unconverted framebuffer bytes")
    parser.add_argument("--no-aspect", action="store_true",
                        help="skip pixel-aspect correction")
    parser.add_argument("--show-conflict", action="store_true")
    parser.add_argument("--calibrate", action="store_true",
                        help="open the screen adjustment screen directly")
    parser.add_argument("--demo-confirm", action="store_true",
                        help="open a sample confirmation dialog")
    parser.add_argument("--demo-search", action="store_true",
                        help="open the catalog search keyboard")
    parser.add_argument("--demo-choose", action="store_true",
                        help="open the sample install picker")
    args = parser.parse_args(argv)

    if args.hold <= args.delay:
        parser.error("--hold must be greater than --delay")

    runner, shooter = connect(args), connect(args)
    try:
        width, height, bpp, stride, smem_start = geometry(shooter)
        print("framebuffer %dx%d @ %d bpp, stride %d" % (width, height, bpp, stride))

        extra = ""
        if args.show_conflict:
            extra += " --show-conflict"
        if args.calibrate:
            extra += " --calibrate"
        if args.demo_confirm:
            extra += " --demo-confirm"
        if args.demo_choose:
            extra += " --demo-choose"
        if args.demo_search:
            extra += " --demo-search"
        command = ("chvt 2 2>/dev/null; bash %s --timeout %g --tab %d%s "
                   "< /dev/tty2 > /dev/tty2 2>&1" %
                   (LAUNCHER, args.hold, args.tab, extra))
        output = {}

        def drive():
            output["text"] = run(runner, command, timeout=args.hold + 60)

        thread = threading.Thread(target=drive)
        thread.start()
        time.sleep(args.delay)

        capture(shooter, stride, height, smem_start)
        sftp = shooter.open_sftp()
        local_raw = args.raw or (args.out + ".raw")
        sftp.get(REMOTE_RAW, local_raw)
        sftp.close()

        with open(local_raw, "rb") as handle:
            raw = handle.read()
        if not args.raw:
            os.unlink(local_raw)

        size = to_png(raw, width, height, bpp, stride, args.out,
                      correct_aspect=not args.no_aspect)
        print("wrote %s (%dx%d)" % (args.out, size[0], size[1]))

        thread.join(timeout=args.hold + 60)
        text, err = output.get("text", ("", ""))
        if text.strip():
            print("--- client output ---\n%s" % text.strip())
        if err.strip():
            print("--- stderr ---\n%s" % err.strip())
    finally:
        runner.close()
        shooter.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
