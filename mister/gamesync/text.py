"""Text rendering for the MiSTer GameSync client.

Glyphs are rasterised once into an 8-bit coverage atlas with FreeType (reached
through ctypes - libfreetype.so.6 is a base-image library that /media/fat/MiSTer
itself links, so it is always present). Colouring a run then costs three
`bytes.translate` lookups plus three strided bytearray slice assignments, all
of which run in C.

Measured on a MiSTer: 1.05 ms to colour a 40-character run, against 195.6 ms
for the equivalent per-pixel Python loop. That 186x gap is the entire reason
this module looks the way it does - never reintroduce a per-pixel loop here.
"""

from __future__ import annotations

import ctypes
from collections import OrderedDict

FT_LOAD_RENDER = 0x4
FT_LOAD_MONOCHROME = 0x1000
FT_LOAD_TARGET_NORMAL = 0x0
#: FT_LOAD_TARGET_(x) is ((x & 15) << 16); FT_RENDER_MODE_MONO is 2.
FT_RENDER_MODE_MONO = 2
FT_LOAD_TARGET_MONO = FT_RENDER_MODE_MONO << 16
FT_PIXEL_MODE_MONO = 1

#: byte -> 8 coverage bytes, MSB first. Built once; expanding a 1bpp glyph is
#: then one table lookup per byte instead of eight shifts per pixel.
_MONO_BYTE = tuple(
    bytes(0xFF if value & (1 << (7 - bit)) else 0x00 for bit in range(8))
    for value in range(256)
)


def _expand_mono(raw: bytes, width: int, rows: int, pitch: int) -> bytes:
    out = bytearray()
    table = _MONO_BYTE
    for row in range(rows):
        start = row * pitch
        line = bytearray()
        for byte in raw[start:start + pitch]:
            line += table[byte]
        out += line[:width]
    return bytes(out)


class FTVector(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class FTBitmap(ctypes.Structure):
    _fields_ = [
        ("rows", ctypes.c_uint),
        ("width", ctypes.c_uint),
        ("pitch", ctypes.c_int),
        ("buffer", ctypes.POINTER(ctypes.c_ubyte)),
        ("num_grays", ctypes.c_ushort),
        ("pixel_mode", ctypes.c_ubyte),
        ("palette_mode", ctypes.c_ubyte),
        ("palette", ctypes.c_void_p),
    ]


class FTGlyphMetrics(ctypes.Structure):
    _fields_ = [("width", ctypes.c_long), ("height", ctypes.c_long),
                ("horiBearingX", ctypes.c_long), ("horiBearingY", ctypes.c_long),
                ("horiAdvance", ctypes.c_long), ("vertBearingX", ctypes.c_long),
                ("vertBearingY", ctypes.c_long), ("vertAdvance", ctypes.c_long)]


class FTGlyphSlot(ctypes.Structure):
    _fields_ = [
        ("library", ctypes.c_void_p),
        ("face", ctypes.c_void_p),
        ("next", ctypes.c_void_p),
        ("glyph_index", ctypes.c_uint),
        ("generic_data", ctypes.c_void_p),
        ("generic_finalizer", ctypes.c_void_p),
        ("metrics", FTGlyphMetrics),
        ("linearHoriAdvance", ctypes.c_long),
        ("linearVertAdvance", ctypes.c_long),
        ("advance", FTVector),
        ("format", ctypes.c_uint),
        ("bitmap", FTBitmap),
        ("bitmap_left", ctypes.c_int),
        ("bitmap_top", ctypes.c_int),
    ]


class FTFaceRec(ctypes.Structure):
    _fields_ = [
        ("num_faces", ctypes.c_long),
        ("face_index", ctypes.c_long),
        ("face_flags", ctypes.c_long),
        ("style_flags", ctypes.c_long),
        ("num_glyphs", ctypes.c_long),
        ("family_name", ctypes.c_char_p),
        ("style_name", ctypes.c_char_p),
        ("num_fixed_sizes", ctypes.c_int),
        ("available_sizes", ctypes.c_void_p),
        ("num_charmaps", ctypes.c_int),
        ("charmaps", ctypes.c_void_p),
        ("generic_data", ctypes.c_void_p),
        ("generic_finalizer", ctypes.c_void_p),
        ("bbox_xmin", ctypes.c_long), ("bbox_ymin", ctypes.c_long),
        ("bbox_xmax", ctypes.c_long), ("bbox_ymax", ctypes.c_long),
        ("units_per_EM", ctypes.c_ushort),
        ("ascender", ctypes.c_short),
        ("descender", ctypes.c_short),
        ("height", ctypes.c_short),
        ("max_advance_width", ctypes.c_short),
        ("max_advance_height", ctypes.c_short),
        ("underline_position", ctypes.c_short),
        ("underline_thickness", ctypes.c_short),
        ("glyph", ctypes.POINTER(FTGlyphSlot)),
    ]


class Glyph:
    __slots__ = ("width", "rows", "left", "top", "advance", "coverage")

    def __init__(self, width, rows, left, top, advance, coverage):
        self.width = width
        self.rows = rows
        self.left = left
        self.top = top
        self.advance = advance
        self.coverage = coverage


class FontError(RuntimeError):
    pass


class Font:
    """One TTF at one pixel size, rasterised into a coverage atlas."""

    #: Latin-1 covers every catalogue name we have seen from the server.
    CHARSET = [chr(c) for c in range(32, 127)] + \
              [chr(c) for c in range(160, 256)]

    def __init__(self, font_bytes: bytes, pixel_size: int, bold: bool = False,
                 pixel_aspect: float = 1.0, mono: bool = False):
        """``pixel_size`` is the height; width follows ``pixel_aspect``.

        A MiSTer driving a 15 kHz monitor reports a 640x240 framebuffer for a
        4:3 picture, so a pixel there is twice as tall as it is wide. Rendering
        square into that makes every glyph half the width it should be, which
        is most of why the UI is unreadable on a cabinet. Dividing the x_ppem
        by the pixel aspect restores the designed proportions.

        ``mono`` drops antialiasing: at 240p the grey fringes are wider than
        the stems they are supposed to smooth, and they read as blur.
        """
        self.pixel_size = pixel_size
        self.pixel_aspect = pixel_aspect if pixel_aspect > 0 else 1.0
        self.x_ppem = max(1, int(round(pixel_size / self.pixel_aspect)))
        self.mono = mono
        self.bold = bold
        self._font_bytes = font_bytes  # must outlive the FT face
        self.glyphs: dict[str, Glyph] = {}
        self.ascent = 0
        self.descent = 0
        self.line_height = 0
        self._build()

    def _build(self) -> None:
        try:
            ft = ctypes.CDLL("libfreetype.so.6")
        except OSError as exc:
            raise FontError("libfreetype.so.6 unavailable: %s" % exc)

        library = ctypes.c_void_p()
        if ft.FT_Init_FreeType(ctypes.byref(library)) != 0:
            raise FontError("FT_Init_FreeType failed")

        try:
            face = ctypes.POINTER(FTFaceRec)()
            buffer = ctypes.create_string_buffer(self._font_bytes,
                                                 len(self._font_bytes))
            self._buffer = buffer  # keep alive for the lifetime of the face
            if ft.FT_New_Memory_Face(library, buffer, len(self._font_bytes), 0,
                                     ctypes.byref(face)) != 0:
                raise FontError("FT_New_Memory_Face failed")

            ft.FT_Set_Pixel_Sizes(face, self.x_ppem, self.pixel_size)
            embolden = getattr(ft, "FT_GlyphSlot_Embolden", None)
            target = FT_LOAD_TARGET_MONO if self.mono else FT_LOAD_TARGET_NORMAL
            load_flags = FT_LOAD_RENDER | target
            if self.mono:
                load_flags |= FT_LOAD_MONOCHROME

            ascent = 0
            descent = 0
            for char in self.CHARSET:
                if ft.FT_Load_Char(face, ord(char), load_flags) != 0:
                    continue
                slot = face.contents.glyph.contents
                if self.bold and embolden is not None:
                    # Re-render emboldened: FT_GlyphSlot_Embolden works on the
                    # outline, so reload without RENDER, embolden, then render.
                    if ft.FT_Load_Char(face, ord(char), 0) == 0:
                        embolden(face.contents.glyph)
                        ft.FT_Render_Glyph(
                            face.contents.glyph,
                            FT_RENDER_MODE_MONO if self.mono else 0)
                        slot = face.contents.glyph.contents

                bitmap = slot.bitmap
                width, rows, pitch = bitmap.width, bitmap.rows, bitmap.pitch
                if width and rows and bitmap.buffer:
                    raw = ctypes.string_at(bitmap.buffer, abs(pitch) * rows)
                    if bitmap.pixel_mode == FT_PIXEL_MODE_MONO:
                        # 1 bit per pixel, MSB first. Expand to the 8-bit
                        # coverage the compositor expects, via a 256-entry
                        # table so the loop stays per-byte rather than
                        # per-pixel.
                        coverage = _expand_mono(raw, width, rows, abs(pitch))
                    elif pitch == width:
                        coverage = raw
                    else:
                        # Normalise away row padding so composition can assume
                        # pitch == width.
                        step = abs(pitch)
                        coverage = b"".join(
                            raw[row * step:row * step + width]
                            for row in range(rows))
                else:
                    width = rows = 0
                    coverage = b""

                glyph = Glyph(width, rows, slot.bitmap_left, slot.bitmap_top,
                              slot.advance.x >> 6, coverage)
                self.glyphs[char] = glyph
                ascent = max(ascent, glyph.top)
                descent = max(descent, glyph.rows - glyph.top)

            if not self.glyphs:
                raise FontError("no glyphs rasterised")

            self.ascent = ascent
            self.descent = descent
            self.line_height = ascent + descent
            self._fallback = self.glyphs.get("?") or next(iter(self.glyphs.values()))
        finally:
            ft.FT_Done_FreeType(library)

    # ------------------------------------------------------------- measuring

    def glyph(self, char: str) -> Glyph:
        return self.glyphs.get(char, self._fallback)

    def measure(self, text: str) -> int:
        total = 0
        for char in text:
            total += self.glyph(char).advance
        return total

    def ellipsize(self, text: str, max_width: int) -> str:
        if self.measure(text) <= max_width:
            return text
        ellipsis = "..."
        budget = max_width - self.measure(ellipsis)
        if budget <= 0:
            return ""
        out = []
        total = 0
        for char in text:
            advance = self.glyph(char).advance
            if total + advance > budget:
                break
            out.append(char)
            total += advance
        return "".join(out) + ellipsis

    # ----------------------------------------------------------- composition

    def coverage_run(self, text: str) -> tuple[int, int, bytearray]:
        """Compose one line of text into an 8-bit coverage mask."""
        width = self.measure(text)
        height = self.line_height
        if width <= 0 or height <= 0:
            return 0, 0, bytearray()

        mask = bytearray(width * height)
        pen = 0
        for char in text:
            glyph = self.glyph(char)
            if glyph.width and glyph.rows:
                left = pen + glyph.left
                top = self.ascent - glyph.top
                for row in range(glyph.rows):
                    dest_y = top + row
                    if dest_y < 0 or dest_y >= height:
                        continue
                    start = dest_y * width + left
                    if left < 0 or left + glyph.width > width:
                        # Clip glyphs that overhang the run box.
                        src_from = max(0, -left)
                        src_to = min(glyph.width, width - left)
                        if src_to <= src_from:
                            continue
                        source = glyph.coverage[row * glyph.width + src_from:
                                                row * glyph.width + src_to]
                        begin = start + src_from
                        mask[begin:begin + len(source)] = source
                    else:
                        source = glyph.coverage[row * glyph.width:
                                                (row + 1) * glyph.width]
                        mask[start:start + glyph.width] = source
            pen += glyph.advance
        return width, height, mask


def build_luts(fg: tuple[int, int, int], bg: tuple[int, int, int]):
    """256-entry per-channel tables mapping coverage to a blended value."""
    luts = []
    for channel in range(3):
        source, dest = bg[channel], fg[channel]
        luts.append(bytes(bytearray(
            (source + (dest - source) * coverage // 255) & 0xFF
            for coverage in range(256))))
    return luts


class TextRenderer:
    """Renders and caches coloured text strips ready to blit."""

    def __init__(self, cache_size: int = 512):
        self._cache: OrderedDict = OrderedDict()
        self._luts: dict = {}
        self._cache_size = cache_size

    def _lut(self, fg, bg):
        key = (fg, bg)
        luts = self._luts.get(key)
        if luts is None:
            luts = build_luts(fg, bg)
            self._luts[key] = luts
        return luts

    def render(self, font: Font, text: str, fg, bg):
        """Return (width, height, BGRA bytes) for one line of text."""
        key = (id(font), text, fg, bg)
        hit = self._cache.get(key)
        if hit is not None:
            self._cache.move_to_end(key)
            return hit

        width, height, mask = font.coverage_run(text)
        if width == 0:
            result = (0, 0, b"")
        else:
            luts = self._lut(fg, bg)
            pixels = bytearray(len(mask) * 4)
            mask_bytes = bytes(mask)
            pixels[0::4] = mask_bytes.translate(luts[2])   # blue
            pixels[1::4] = mask_bytes.translate(luts[1])   # green
            pixels[2::4] = mask_bytes.translate(luts[0])   # red
            result = (width, height, pixels)

        self._cache[key] = result
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return result

    def clear(self) -> None:
        self._cache.clear()
