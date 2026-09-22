"""Linux framebuffer surface for the MiSTer GameSync client.

Everything is drawn by copying whole scanlines into an mmap of /dev/fb0 with
slice assignment, so the copies happen in C. There is deliberately no
per-pixel Python anywhere in this module - see mister/PHASE0_FINDINGS.md for
the measurements that forced that rule.

The framebuffer mode changes at runtime on MiSTer (1920x1080, 1280x720 and
640x240 have all been observed), so geometry is always read from the device
and never assumed.

A viewport can be set to inset every draw inside a CRT's safe area. Arcade and
consumer tubes overscan, and on a JAMMA cabinet at 640x240 the outer few
percent of the picture is simply not on the glass. Doing the inset here rather
than in the layout means no drawing code has to know about it: `width` and
`height` report the viewport, so a caller that fills `0, 0, width, height`
fills the safe area and nothing else.
"""

from __future__ import annotations

try:
    import fcntl
except ImportError:  # pragma: no cover
    # Linux-only. Guarded so the UI logic can be imported and unit-tested on a
    # development machine; nothing here works without a real MiSTer anyway.
    fcntl = None
import errno
import mmap
import os
import struct

FBIOGET_VSCREENINFO = 0x4600
FBIOGET_FSCREENINFO = 0x4602

KDSETMODE = 0x4B3A
KD_TEXT = 0x00
KD_GRAPHICS = 0x01

#: At or below this many scanlines the output is a 240p-class CRT mode, where
#: pixels are not square. Kept in step with theme.LOWRES_MAX_HEIGHT.
LOWRES_MAX_HEIGHT = 288

ACTIVE_VT_PATH = "/sys/class/tty/tty0/active"


class FramebufferError(RuntimeError):
    pass


class Framebuffer:
    """An mmap'd /dev/fb0 with scanline-oriented drawing primitives."""

    def __init__(self, path: str = "/dev/fb0", take_over_console: bool = True):
        self.path = path
        self._take_over_console = take_over_console
        self._fd = -1
        self._mem_fd = -1
        self._tty_fd = -1
        self._map: mmap.mmap | None = None
        self._saved: bytes | None = None
        self._row_cache: dict[tuple[int, int], bytes] = {}

        #: Viewport (the safe area). Drawing coordinates are relative to it.
        self.width = 0
        self.height = 0
        #: The real panel, which only the viewport maths and clear() see.
        self.phys_width = 0
        self.phys_height = 0
        self._origin_x = 0
        self._origin_y = 0

        self.stride = 0
        self.bytes_per_pixel = 0
        self._offsets = (16, 8, 0)
        self._alpha = (0, 0)

    # ------------------------------------------------------------- lifecycle

    def open(self) -> "Framebuffer":
        if fcntl is None:
            raise FramebufferError("no fcntl: not running on Linux")
        self._fd = os.open(self.path, os.O_RDWR)
        try:
            var = bytearray(160)
            fcntl.ioctl(self._fd, FBIOGET_VSCREENINFO, var, True)
            (xres, yres, _xv, _yv, _xo, _yo, bpp) = struct.unpack_from("7I", var, 0)
            red = struct.unpack_from("3I", var, 32)
            green = struct.unpack_from("3I", var, 44)
            blue = struct.unpack_from("3I", var, 56)
            transp = struct.unpack_from("3I", var, 68)

            fix = bytearray(80)
            fcntl.ioctl(self._fd, FBIOGET_FSCREENINFO, fix, True)
            smem_start, smem_len = struct.unpack_from("II", fix, 16)
            stride = struct.unpack_from("I", fix, 44)[0]

            if bpp != 32:
                raise FramebufferError(
                    "only 32 bpp is supported, framebuffer reports %d" % bpp)

            self.phys_width, self.phys_height = xres, yres
            self.width, self.height, self.stride = xres, yres, stride
            self.bytes_per_pixel = bpp // 8
            self._offsets = (red[0], green[0], blue[0])
            self._alpha = (transp[0], transp[1])

            # smem_len reads back as 0 on MiSTer, so the map is sized from the
            # stride and height rather than from what the driver claims.
            self._map = self._map_pixels(smem_start, smem_len,
                                         self.stride * self.phys_height)
            self._saved = self._map[:]
            if self._take_over_console:
                self._set_console_mode(KD_GRAPHICS)
        except Exception:
            self.close()
            raise
        return self

    def _map_pixels(self, smem_start: int, smem_len: int, needed: int):
        """Map the pixel memory, through the device or around it.

        MiSTer's Linux image moved to a 6.x kernel (6.18 as of September
        2026) whose fbdev core refuses ``mmap`` on a driver that does not
        supply its own ``fb_mmap``. The out-of-tree ``MiSTer_fb`` driver
        does not, so the map that had worked for years began failing with
        ``ENODEV`` - and ``write()`` is gone the same way. The driver still
        reports where the pixels are (``smem_start``, a reserved region
        outside System RAM), so map that through ``/dev/mem`` instead; the
        image ships without ``STRICT_DEVMEM`` and scripts run as root.
        """
        try:
            return mmap.mmap(self._fd, needed, mmap.MAP_SHARED,
                             mmap.PROT_READ | mmap.PROT_WRITE)
        except OSError as exc:
            if exc.errno != errno.ENODEV or not smem_start:
                raise
        length = max(needed, smem_len)
        self._mem_fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
        try:
            return mmap.mmap(self._mem_fd, length, mmap.MAP_SHARED,
                             mmap.PROT_READ | mmap.PROT_WRITE,
                             offset=smem_start)
        except OSError as exc:
            raise FramebufferError(
                "framebuffer refuses mmap and /dev/mem at 0x%x failed: %s"
                % (smem_start, exc))

    def close(self) -> None:
        if self._map is not None:
            try:
                if self._saved is not None:
                    self._map[:] = self._saved
            except Exception:
                pass
            try:
                self._map.close()
            except Exception:
                pass
            self._map = None
        if self._tty_fd >= 0:
            try:
                fcntl.ioctl(self._tty_fd, KDSETMODE, KD_TEXT)
            except Exception:
                pass
            try:
                os.close(self._tty_fd)
            except Exception:
                pass
            self._tty_fd = -1
        for attr in ("_fd", "_mem_fd"):
            fd = getattr(self, attr)
            if fd >= 0:
                try:
                    os.close(fd)
                except Exception:
                    pass
                setattr(self, attr, -1)

    def __enter__(self) -> "Framebuffer":
        return self.open()

    def __exit__(self, *_exc) -> None:
        self.close()

    def _set_console_mode(self, mode: int) -> None:
        """Stop the text console drawing over us (and put it back later)."""
        candidates = []
        try:
            with open(ACTIVE_VT_PATH) as handle:
                candidates.append("/dev/" + handle.read().strip())
        except Exception:
            pass
        candidates += ["/dev/tty2", "/dev/tty1", "/dev/tty0", "/dev/console"]

        for tty_path in candidates:
            try:
                fd = os.open(tty_path, os.O_RDWR)
            except Exception:
                continue
            try:
                fcntl.ioctl(fd, KDSETMODE, mode)
            except Exception:
                os.close(fd)
                continue
            self._tty_fd = fd
            return

    # -------------------------------------------------------------- viewport

    def set_viewport(self, left: int, top: int, width: int, height: int) -> None:
        """Inset all drawing into a safe area, in physical pixels."""
        left = max(0, min(left, self.phys_width - 1))
        top = max(0, min(top, self.phys_height - 1))
        self._origin_x = left
        self._origin_y = top
        self.width = max(1, min(width, self.phys_width - left))
        self.height = max(1, min(height, self.phys_height - top))
        self._row_cache.clear()

    def set_overscan(self, percent_x: float, percent_y: float) -> None:
        """Inset by a percentage of each edge - what a calibration UI sets.

        Percentages rather than pixels because the mode changes underneath us:
        the same cabinet reports 640x240 for one core and 640x480 for another,
        and an inset in pixels would mean two different things.
        """
        margin_x = int(self.phys_width * max(0.0, min(percent_x, 25.0)) / 100.0)
        margin_y = int(self.phys_height * max(0.0, min(percent_y, 25.0)) / 100.0)
        self.set_viewport(margin_x, margin_y,
                          self.phys_width - margin_x * 2,
                          self.phys_height - margin_y * 2)

    @property
    def pixel_aspect(self) -> float:
        """Pixel width divided by pixel height.

        640x240 on a 4:3 tube gives 0.5 - each pixel is twice as tall as it is
        wide - which is why text has to be rendered with a wider x_ppem than
        y_ppem to come out with the proportions the font designer intended.

        Only 240p-class modes are treated as stretched. Every mode MiSTer
        outputs to a monitor (640x480, 1280x720, 1920x1080) has square pixels,
        and assuming 4:3 for all of them would squash 16:9 text instead.
        The driver reports pixclock 0, so there is no timing to check; the
        scanline count is the only signal there is.
        """
        if not self.phys_width or not self.phys_height:
            return 1.0
        if self.phys_height > LOWRES_MAX_HEIGHT:
            return 1.0
        return (4.0 / 3.0) / (float(self.phys_width) / float(self.phys_height))

    # ------------------------------------------------------------- primitives

    def pack(self, rgb: tuple[int, int, int]) -> bytes:
        """Pack an RGB triple the way this framebuffer orders its channels."""
        red, green, blue = rgb
        value = ((red << self._offsets[0]) | (green << self._offsets[1])
                 | (blue << self._offsets[2]))
        if self._alpha[1]:
            value |= 0xFF << self._alpha[0]
        return struct.pack("<I", value)

    def _row(self, rgb: tuple[int, int, int], width: int) -> bytes:
        key = (rgb[0] << 16 | rgb[1] << 8 | rgb[2], width)
        row = self._row_cache.get(key)
        if row is None:
            if len(self._row_cache) > 256:
                self._row_cache.clear()
            row = self.pack(rgb) * width
            self._row_cache[key] = row
        return row

    def clear(self, rgb: tuple[int, int, int]) -> None:
        """Fill the viewport, and black out whatever the inset left over.

        The border has to be painted too: without it the console text that was
        on screen before we took over survives in the overscan margin.
        """
        assert self._map is not None
        if (self.width, self.height) == (self.phys_width, self.phys_height):
            row = self.pack(rgb) * (self.stride // self.bytes_per_pixel)
            self._map[:] = row * self.phys_height
            return

        black = self.pack((0, 0, 0)) * (self.stride // self.bytes_per_pixel)
        self._map[:] = black * self.phys_height
        self.fill_rect(0, 0, self.width, self.height, rgb)

    def fill_rect(self, x: int, y: int, width: int, height: int,
                  rgb: tuple[int, int, int]) -> None:
        assert self._map is not None
        x, y, width, height = self._clip(x, y, width, height)
        if width <= 0 or height <= 0:
            return
        row = self._row(rgb, width)
        span = width * self.bytes_per_pixel
        left = (x + self._origin_x) * self.bytes_per_pixel
        fb = self._map
        for row_y in range(y + self._origin_y, y + self._origin_y + height):
            start = row_y * self.stride + left
            fb[start:start + span] = row

    def blit(self, x: int, y: int, width: int, height: int,
             pixels: bytes | bytearray,
             crop_x: int = 0, crop_w: int | None = None) -> None:
        """Copy a tightly packed BGRA image into the framebuffer.

        ``crop_x`` / ``crop_w`` take a window out of the *source* before it
        lands. That is what lets a marquee slide a long name inside its own
        column: clipping alone only bounds things at the screen edge, so a
        name scrolled left would otherwise run over the system chip.
        """
        assert self._map is not None
        if width <= 0 or height <= 0:
            return
        src_stride = width * self.bytes_per_pixel

        if crop_x or crop_w is not None:
            crop_x = max(0, min(crop_x, width))
            width = width - crop_x if crop_w is None else min(crop_w, width - crop_x)
            if width <= 0:
                return

        # Clip against the screen, adjusting the source window to match.
        src_x = crop_x if x >= 0 else crop_x - x
        src_y = 0 if y >= 0 else -y
        dst_x = max(0, x)
        dst_y = max(0, y)
        visible_w = min(width - (src_x - crop_x), self.width - dst_x)
        visible_h = min(height - src_y, self.height - dst_y)
        if visible_w <= 0 or visible_h <= 0:
            return

        span = visible_w * self.bytes_per_pixel
        left = (dst_x + self._origin_x) * self.bytes_per_pixel
        src_left = src_x * self.bytes_per_pixel
        fb = self._map
        for line in range(visible_h):
            src = (src_y + line) * src_stride + src_left
            dst = (dst_y + self._origin_y + line) * self.stride + left
            fb[dst:dst + span] = pixels[src:src + span]

    def _clip(self, x: int, y: int, width: int, height: int):
        if x < 0:
            width += x
            x = 0
        if y < 0:
            height += y
            y = 0
        width = min(width, self.width - x)
        height = min(height, self.height - y)
        return x, y, width, height
