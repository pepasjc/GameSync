#!/usr/bin/env python3
"""GameSync MiSTer framebuffer feasibility spike.

Decides whether a real graphical UI (drawn straight to /dev/fb0 with stdlib
mmap) is fast enough on this hardware, or whether the client has to settle for
a curses TUI.

The plan under test: never touch pixels one at a time from Python. Build each
scanline as a bytes object and assign it into the mmap with a slice, so the
copy happens in C. Text comes from a prebuilt glyph atlas blitted the same way.

Run over SSH - it paints the console, then restores it:
    python3 spike_fb.py
"""

import fcntl
import mmap
import os
import struct
import sys
import time
import traceback

FB_PATH = "/dev/fb0"
REPORT_PATH = "/media/fat/Scripts/.gamesync/fb_report.txt"

# linux/fb.h
FBIOGET_VSCREENINFO = 0x4600
FBIOGET_FSCREENINFO = 0x4602

# linux/kd.h
KDSETMODE = 0x4B3A
KD_TEXT, KD_GRAPHICS = 0x00, 0x01


def get_var_screeninfo(fd):
    """struct fb_var_screeninfo - we only need the leading fields."""
    buf = bytearray(160)
    fcntl.ioctl(fd, FBIOGET_VSCREENINFO, buf, True)
    (xres, yres, xres_virtual, yres_virtual, xoffset, yoffset,
     bits_per_pixel) = struct.unpack_from("7I", buf, 0)
    # bitfields: red, green, blue, transp - each (offset, length, msb_right)
    red = struct.unpack_from("3I", buf, 32)
    green = struct.unpack_from("3I", buf, 44)
    blue = struct.unpack_from("3I", buf, 56)
    transp = struct.unpack_from("3I", buf, 68)
    return {
        "xres": xres, "yres": yres,
        "xres_virtual": xres_virtual, "yres_virtual": yres_virtual,
        "xoffset": xoffset, "yoffset": yoffset, "bpp": bits_per_pixel,
        "red": red, "green": green, "blue": blue, "transp": transp,
    }


def get_line_length(fd):
    """struct fb_fix_screeninfo - line_length lives at offset 44."""
    buf = bytearray(80)
    fcntl.ioctl(fd, FBIOGET_FSCREENINFO, buf, True)
    return struct.unpack_from("I", buf, 44)[0]


def pixel_bytes(info, r, g, b):
    """Pack an RGB triple the way this framebuffer expects it."""
    value = ((r << info["red"][0]) | (g << info["green"][0])
             | (b << info["blue"][0]))
    if info["transp"][1]:
        value |= (0xFF << info["transp"][0])
    return struct.pack("<I", value)


def main():
    lines = []

    def log(text=""):
        lines.append(text)
        print(text)

    log("GameSync MiSTer framebuffer spike")
    log("run at %s" % time.strftime("%Y-%m-%d %H:%M:%S"))

    fd = os.open(FB_PATH, os.O_RDWR)
    tty_fd = None
    fb = None
    try:
        info = get_var_screeninfo(fd)
        stride = get_line_length(fd)
        width, height, bpp = info["xres"], info["yres"], info["bpp"]
        bytes_per_pixel = bpp // 8
        size = stride * height

        log("")
        log("resolution       : %dx%d @ %d bpp" % (width, height, bpp))
        log("virtual          : %dx%d offset %d,%d"
            % (info["xres_virtual"], info["yres_virtual"],
               info["xoffset"], info["yoffset"]))
        log("line_length      : %d bytes (%d px)"
            % (stride, stride // bytes_per_pixel))
        log("mapping size     : %d bytes (%.1f MB)" % (size, size / 1048576.0))
        log("channel offsets  : R%s G%s B%s A%s"
            % (info["red"], info["green"], info["blue"], info["transp"]))

        fb = mmap.mmap(fd, size, mmap.MAP_SHARED,
                       mmap.PROT_READ | mmap.PROT_WRITE)
        log("mmap             : ok")

        saved = fb[:]
        log("saved console    : %d bytes" % len(saved))

        # Take the VT out of text mode so the console stops drawing over us.
        for tty_path in ("/dev/tty2", "/dev/tty1", "/dev/tty0"):
            try:
                tty_fd = os.open(tty_path, os.O_RDWR)
                fcntl.ioctl(tty_fd, KDSETMODE, KD_GRAPHICS)
                log("KD_GRAPHICS      : ok on %s" % tty_path)
                break
            except Exception as exc:  # noqa: BLE001
                if tty_fd is not None:
                    os.close(tty_fd)
                    tty_fd = None
                log("KD_GRAPHICS      : failed on %s (%s)" % (tty_path, exc))

        log("")
        log("=" * 60)
        log("BENCHMARKS  (target: 30 fps = 33 ms per frame)")
        log("=" * 60)

        # 1. full-screen clear, one slice assignment per frame
        row = pixel_bytes(info, 18, 20, 28) * (stride // bytes_per_pixel)
        frame = row * height
        best = bench(lambda: fb.__setitem__(slice(0, size), frame), 20)
        log("full-screen clear (1 memcpy)      : %6.1f ms  -> %5.1f fps"
            % (best * 1000, 1.0 / best))

        # 2. full-screen clear built per row (what a dirty-rect renderer does)
        def clear_rows():
            for y in range(height):
                start = y * stride
                fb[start:start + stride] = row
        best_rows = bench(clear_rows, 10)
        log("full-screen clear (%d row copies) : %6.1f ms  -> %5.1f fps"
            % (height, best_rows * 1000, 1.0 / best_rows))

        # Geometry derived from the real mode, never hardcoded: MiSTer changes
        # the framebuffer resolution at runtime (1920x1080 and 1280x720 both
        # observed on the same box).
        row_count = min(30, max(4, (height - 160) // 24))
        row_height = 20
        row_width = min(1200, width - 120)
        row_span = row_width * bytes_per_pixel
        row_left = 60 * bytes_per_pixel

        panel = pixel_bytes(info, 32, 36, 48) * row_width
        accent = pixel_bytes(info, 80, 170, 255) * row_width

        # 3. a realistic frame: a list of rows, one highlighted
        def realistic_frame():
            for index in range(row_count):
                top = 80 + index * 24
                fill = accent if index == 7 else panel
                for y in range(top, top + row_height):
                    start = y * stride + row_left
                    fb[start:start + row_span] = fill
        best_ui = bench(realistic_frame, 20)
        log("%d list rows (%d row copies)      : %6.1f ms  -> %5.1f fps"
            % (row_count, row_count * row_height, best_ui * 1000,
               1.0 / best_ui))

        # 4. glyph blitting: one slice per glyph scanline
        glyph_w, glyph_h = 14, 24
        glyph_row = pixel_bytes(info, 235, 235, 240) * glyph_w
        cols = max(1, (width - 80) // (glyph_w + 1))
        rows = max(1, (height - 80) // (glyph_h + 2))
        glyph_count = min(2000, cols * rows)

        def glyph_blits():
            for index in range(glyph_count):
                gx = 40 + (index % cols) * (glyph_w + 1)
                gy = 40 + (index // cols) * (glyph_h + 2)
                base = gx * bytes_per_pixel
                for y in range(gy, gy + glyph_h):
                    start = y * stride + base
                    fb[start:start + glyph_w * bytes_per_pixel] = glyph_row
        best_text = bench(glyph_blits, 10)
        log("%d glyphs (%d row copies)       : %6.1f ms  -> %5.1f fps"
            % (glyph_count, glyph_count * glyph_h, best_text * 1000,
               1.0 / best_text))

        # 5. compose in a bytearray, then one memcpy to the fb
        scratch = bytearray(frame)

        def composited():
            for index in range(row_count):
                top = 80 + index * 24
                for y in range(top, top + row_height):
                    start = y * stride + row_left
                    scratch[start:start + row_span] = panel
            fb[:] = scratch
        best_comp = bench(composited, 10)
        log("compose offscreen + 1 memcpy      : %6.1f ms  -> %5.1f fps"
            % (best_comp * 1000, 1.0 / best_comp))

        # 6. alpha blending cost, pure Python, small area
        blend_px = 200 * 40

        def alpha_blend():
            src = bytearray(fb[0:blend_px * bytes_per_pixel])
            for i in range(0, len(src), bytes_per_pixel):
                src[i] = (src[i] + 90) >> 1
                src[i + 1] = (src[i + 1] + 100) >> 1
                src[i + 2] = (src[i + 2] + 120) >> 1
            fb[0:blend_px * bytes_per_pixel] = bytes(src)
        best_blend = bench(alpha_blend, 5)
        log("alpha blend %d px in pure Python : %6.1f ms"
            % (blend_px, best_blend * 1000))

        # visible proof it actually drew something
        draw_test_card(fb, info, stride, width, height, bytes_per_pixel)
        log("")
        log("test card drawn - look at the screen for 4 seconds")
        time.sleep(4)

        fb[:] = saved
        log("console restored : ok")

        probe_sdl(log)

        log("")
        log("=" * 60)
        log("VERDICT")
        log("=" * 60)
        log("A dirty-rect renderer redraws a few hundred row-slices per frame,")
        log("not the whole screen. Compare '40 list rows' against 33 ms.")
        log("Alpha blending in pure Python is the one thing to avoid; use")
        log("precomputed blended colours instead.")

    except Exception:  # noqa: BLE001
        log("")
        log("EXCEPTION:")
        log(traceback.format_exc())
    finally:
        if tty_fd is not None:
            try:
                fcntl.ioctl(tty_fd, KDSETMODE, KD_TEXT)
            except Exception:  # noqa: BLE001
                pass
            os.close(tty_fd)
        if fb is not None:
            fb.close()
        os.close(fd)
        try:
            os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
            with open(REPORT_PATH, "w") as handle:
                handle.write("\n".join(lines) + "\n")
        except Exception:  # noqa: BLE001
            pass


def bench(function, iterations):
    """Return the best wall time of N runs, in seconds."""
    best = None
    for _ in range(iterations):
        start = time.time()
        function()
        elapsed = time.time() - start
        if best is None or elapsed < best:
            best = elapsed
    return best


def draw_test_card(fb, info, stride, width, height, bytes_per_pixel):
    """Paint something recognisably UI-shaped so the result is visible."""
    background = pixel_bytes(info, 18, 20, 28) * (stride // bytes_per_pixel)
    fb[:] = background * height

    header = pixel_bytes(info, 80, 170, 255) * width
    for y in range(40, min(96, height)):
        fb[y * stride:y * stride + width * bytes_per_pixel] = header

    colors = [(232, 93, 117), (247, 181, 56), (86, 196, 134),
              (108, 154, 245), (186, 128, 240)]
    card_width = min(1400, width - 160)
    chip_width = min(90, card_width)
    for index in range(30):
        top = 140 + index * 28
        if top + 22 > height:
            break
        red, green, blue = colors[index % len(colors)]
        chip = pixel_bytes(info, red, green, blue) * chip_width
        rowfill = pixel_bytes(info, 32, 36, 48) * card_width
        for y in range(top, top + 22):
            start = y * stride + 80 * bytes_per_pixel
            fb[start:start + card_width * bytes_per_pixel] = rowfill
            fb[start:start + chip_width * bytes_per_pixel] = chip


def probe_sdl(log):
    """Is libSDL2 usable from ctypes, and which video drivers does it have?"""
    import ctypes
    import ctypes.util

    log("")
    log("=" * 60)
    log("SDL2")
    log("=" * 60)

    handle = None
    for candidate in ("libSDL2-2.0.so.0", "libSDL2.so",
                      "/usr/lib/libSDL2-2.0.so.0"):
        try:
            handle = ctypes.CDLL(candidate)
            log("loaded           : %s" % candidate)
            break
        except OSError as exc:
            log("load failed      : %s (%s)" % (candidate, exc))
    if handle is None:
        log("SDL2 unusable from ctypes")
        return

    try:
        handle.SDL_GetRevision.restype = ctypes.c_char_p
        log("revision         : %s"
            % handle.SDL_GetRevision().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001
        log("revision         : unavailable (%s)" % exc)

    try:
        handle.SDL_GetNumVideoDrivers.restype = ctypes.c_int
        handle.SDL_GetVideoDriver.restype = ctypes.c_char_p
        handle.SDL_GetVideoDriver.argtypes = [ctypes.c_int]
        count = handle.SDL_GetNumVideoDrivers()
        drivers = [handle.SDL_GetVideoDriver(i).decode("utf-8", "replace")
                   for i in range(count)]
        log("video drivers    : %s" % ", ".join(drivers))
    except Exception as exc:  # noqa: BLE001
        log("video drivers    : query failed (%s)" % exc)
        return

    # Which ones actually initialise on this box, with MiSTer running?
    handle.SDL_VideoInit.restype = ctypes.c_int
    handle.SDL_VideoInit.argtypes = [ctypes.c_char_p]
    handle.SDL_GetError.restype = ctypes.c_char_p
    for driver in drivers:
        try:
            result = handle.SDL_VideoInit(driver.encode())
            if result == 0:
                log("  %-12s   : INIT OK" % driver)
                handle.SDL_VideoQuit()
            else:
                log("  %-12s   : failed (%s)"
                    % (driver, handle.SDL_GetError().decode("utf-8", "replace")))
        except Exception as exc:  # noqa: BLE001
            log("  %-12s   : exception (%s)" % (driver, exc))

    for name in ("libSDL2_ttf", "libSDL2_image", "libSDL2_gfx"):
        try:
            ctypes.CDLL(name + "-2.0.so.0")
            log("%-16s : present" % name)
        except OSError:
            log("%-16s : ABSENT" % name)


if __name__ == "__main__":
    sys.exit(main())
