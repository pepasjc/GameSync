#!/usr/bin/env python3
"""GameSync MiSTer text-rendering feasibility spike.

The framebuffer spike showed rectangle fills are effectively free (23 list rows
in 3.4 ms) but naive per-glyph blitting is not (1920 glyphs in 215 ms), and
per-pixel alpha blending in Python is hopeless (8000 px in 59 ms).

This measures the technique that should fix both: keep antialiased glyph
coverage as an 8-bit mask, colour it with three `bytes.translate` lookups (one
per channel, C speed), and interleave the channels into a BGRA buffer with
strided bytearray slice assignment (also C speed). No Python-level pixel loop
anywhere.

If a full screen of coloured, antialiased text lands in a few milliseconds, the
client gets a real GUI instead of a curses TUI.

    python3 spike_text.py
"""

import mmap
import os
import struct
import sys
import time
import traceback

REPORT_PATH = "/media/fat/Scripts/.gamesync/text_report.txt"


def build_luts(fg, bg):
    """Per-channel 256-byte tables mapping coverage -> blended channel value."""
    luts = []
    for channel in range(3):
        source, dest = bg[channel], fg[channel]
        luts.append(bytes(
            (source + (dest - source) * coverage // 255) & 0xFF
            for coverage in range(256)))
    return luts


def colorize(coverage, luts, width, height):
    """Coverage mask -> BGRA pixels, with no per-pixel Python."""
    out = bytearray(len(coverage) * 4)
    out[0::4] = coverage.translate(luts[2])   # blue
    out[1::4] = coverage.translate(luts[1])   # green
    out[2::4] = coverage.translate(luts[0])   # red
    return out


def colorize_naive(coverage, fg, bg):
    """The obvious implementation, for comparison."""
    out = bytearray(len(coverage) * 4)
    for index, value in enumerate(coverage):
        base = index * 4
        out[base] = bg[2] + (fg[2] - bg[2]) * value // 255
        out[base + 1] = bg[1] + (fg[1] - bg[1]) * value // 255
        out[base + 2] = bg[0] + (fg[0] - bg[0]) * value // 255
    return out


def bench(function, iterations):
    best = None
    for _ in range(iterations):
        start = time.time()
        function()
        elapsed = time.time() - start
        if best is None or elapsed < best:
            best = elapsed
    return best


def main():
    lines = []

    def log(text=""):
        lines.append(text)
        print(text)

    log("GameSync MiSTer text rendering spike")
    log("run at %s" % time.strftime("%Y-%m-%d %H:%M:%S"))

    try:
        # A realistic text run: one list row label, 40 chars at 15x26.
        run_width, run_height = 40 * 15, 26
        run_pixels = run_width * run_height
        coverage = bytes(bytearray(
            (index * 37) & 0xFF for index in range(run_pixels)))

        fg, bg = (235, 235, 240), (32, 36, 48)
        luts = build_luts(fg, bg)

        log("")
        log("text run         : %dx%d = %d px (a 40-char list row)"
            % (run_width, run_height, run_pixels))

        log("")
        log("=" * 62)
        log("COLOURING A TEXT RUN")
        log("=" * 62)

        best_lut = bench(lambda: colorize(coverage, luts, run_width,
                                          run_height), 20)
        log("translate + strided slices : %7.2f ms" % (best_lut * 1000))

        best_naive = bench(lambda: colorize_naive(coverage, fg, bg), 3)
        log("naive per-pixel loop       : %7.2f ms  (%.0fx slower)"
            % (best_naive * 1000, best_naive / best_lut))

        best_luts = bench(lambda: build_luts(fg, bg), 20)
        log("building the LUTs          : %7.2f ms  (once per colour pair)"
            % (best_luts * 1000))

        # Now the real thing: blit coloured runs into the framebuffer.
        fd = os.open("/dev/fb0", os.O_RDWR)
        try:
            buf = bytearray(160)
            import fcntl
            fcntl.ioctl(fd, 0x4600, buf, True)
            xres, yres = struct.unpack_from("2I", buf, 0)
            fix = bytearray(80)
            fcntl.ioctl(fd, 0x4602, fix, True)
            stride = struct.unpack_from("I", fix, 44)[0]
            fb = mmap.mmap(fd, stride * yres, mmap.MAP_SHARED,
                           mmap.PROT_READ | mmap.PROT_WRITE)
            saved = fb[:]

            log("")
            log("=" * 62)
            log("FULL SCREEN OF TEXT  (%dx%d)" % (xres, yres))
            log("=" * 62)

            rows = min(24, (yres - 120) // 28)
            colored = colorize(coverage, luts, run_width, run_height)
            row_bytes = run_width * 4

            def blit_cached():
                """Every row already rendered and cached: pure memcpy."""
                for index in range(rows):
                    top = 60 + index * 28
                    for y in range(run_height):
                        src = y * row_bytes
                        dst = (top + y) * stride + 80 * 4
                        fb[dst:dst + row_bytes] = colored[src:src + row_bytes]

            best_blit = bench(blit_cached, 10)
            log("%d rows, cached strips      : %7.2f ms  -> %5.1f fps"
                % (rows, best_blit * 1000, 1.0 / best_blit))

            def render_and_blit():
                """Cold cache: colour every row, then blit."""
                for index in range(rows):
                    strip = colorize(coverage, luts, run_width, run_height)
                    top = 60 + index * 28
                    for y in range(run_height):
                        src = y * row_bytes
                        dst = (top + y) * stride + 80 * 4
                        fb[dst:dst + row_bytes] = strip[src:src + row_bytes]

            best_cold = bench(render_and_blit, 5)
            log("%d rows, cold cache         : %7.2f ms  -> %5.1f fps"
                % (rows, best_cold * 1000, 1.0 / best_cold))

            # Scrolling one line only dirties the rows that moved.
            def scroll_one_line():
                for index in (0, rows - 1):
                    top = 60 + index * 28
                    for y in range(run_height):
                        src = y * row_bytes
                        dst = (top + y) * stride + 80 * 4
                        fb[dst:dst + row_bytes] = colored[src:src + row_bytes]

            best_scroll = bench(scroll_one_line, 20)
            log("scroll: 2 dirty rows       : %7.2f ms  -> %5.1f fps"
                % (best_scroll * 1000, 1.0 / best_scroll))

            fb[:] = saved
            fb.close()
        finally:
            os.close(fd)

        log("")
        log("=" * 62)
        log("VERDICT")
        log("=" * 62)
        log("A menu redraws on input, not continuously. The numbers that")
        log("matter are 'cold cache' for a fresh screen and 'scroll' for")
        log("moving the selection; both should sit well under 33 ms.")

    except Exception:  # noqa: BLE001
        log("")
        log("EXCEPTION:")
        log(traceback.format_exc())

    try:
        os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
        with open(REPORT_PATH, "w") as handle:
            handle.write("\n".join(lines) + "\n")
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    sys.exit(main())
