"""8x8 bitmap font for the dense (CRT-style) layout.

A drop-in for text.Font: same ``glyph``/``measure``/``ellipsize``/
``coverage_run`` surface, so TextRenderer and every draw site take it as is.
The point is density. At 640x240 a FreeType face needs about 20 scanlines a
row to stay legible; an 8x8 cell needs 10, which doubles the rows on screen -
the look of DreamSTer's launcher and of a console's own text mode.

Higher modes scale the cell by whole pixels (``scale_x`` x ``scale_y``), never
by resampling, so the glyphs stay as sharp at 1080p as at 240p.

Font data: font8x8_basic by Daniel Hepper, https://github.com/dhepper/font8x8
(public domain), itself from Marcel Sondaar's font8x8 and the public-domain
IBM PC VGA glyphs. Stored here bit-reversed so the most significant bit is the
leftmost pixel.
"""

from __future__ import annotations

from .text import Font, Glyph

try:
    import unicodedata
except ImportError:  # pragma: no cover - MiSTer's Python ships it
    unicodedata = None

#: 128 glyphs x 8 rows, one byte per row, MSB = leftmost pixel.
FONT8X8 = bytes.fromhex(
    "0000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000183c3c18180018006c6c0000000000006c6cfe6cfe6c6c00"
    "307cc0780cf8300000c6cc183066c600386c3876dccc76006060c00000000000"
    "1830606060301800603018181830600000663cff3c660000003030fc30300000"
    "0000000000303060000000fc000000000000000000303000060c183060c08000"
    "7cc6cedef6e67c00307030303030fc0078cc0c3860ccfc0078cc0c380ccc7800"
    "1c3c6cccfe0c1e00fcc0f80c0ccc78003860c0f8cccc7800fccc0c1830303000"
    "78cccc78cccc780078cccc7c0c18700000303000003030000030300000303060"
    "183060c0603018000000fc0000fc00006030180c1830600078cc0c1830003000"
    "7cc6dededec078003078ccccfccccc00fc66667c6666fc003c66c0c0c0663c00"
    "f86c6666666cf800fe6268786862fe00fe6268786860f0003c66c0c0ce663e00"
    "ccccccfccccccc0078303030303078001e0c0c0ccccc7800e6666c786c66e600"
    "f06060606266fe00c6eefefed6c6c600c6e6f6decec6c600386cc6c6c66c3800"
    "fc66667c6060f00078ccccccdc781c00fc66667c6c66e60078cce0701ccc7800"
    "fcb4303030307800ccccccccccccfc00cccccccccc783000c6c6c6d6feeec600"
    "c6c66c38386cc600cccccc7830307800fec68c183266fe007860606060607800"
    "c06030180c060200781818181818780010386cc60000000000000000000000ff"
    "30301800000000000000780c7ccc7600e060607c6666dc00000078ccc0cc7800"
    "1c0c0c7ccccc7600000078ccfcc07800386c60f06060f000000076cccc7c0cf8"
    "e0606c766666e60030007030303078000c000c0c0ccccc78e060666c786ce600"
    "70303030303078000000ccfefed6c6000000f8cccccccc00000078cccccc7800"
    "0000dc66667c60f0000076cccc7c0c1e0000dc766660f00000007cc0780cf800"
    "10307c30303418000000cccccccc76000000cccccc7830000000c6d6fefe6c00"
    "0000c66c386cc6000000cccccc7c0cf80000fc983064fc001c3030e030301c00"
    "1818180018181800e030301c3030e00076dc0000000000000000000000000000"
)
assert len(FONT8X8) == 128 * 8

#: Typographic punctuation the catalogue uses, folded onto ASCII.
_FOLD = {
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "·": ".", "×": "x",
}


def _ascii_for(char: str) -> str:
    """The ASCII glyph to draw for one character: accents drop to the base
    letter (the font has no Latin-1 half), anything else unknown is '?'."""
    code = ord(char)
    if 0x20 <= code < 0x7F:
        return char
    folded = _FOLD.get(char)
    if folded:
        return folded
    if unicodedata is not None:
        base = unicodedata.normalize("NFKD", char)[:1]
        if base and 0x20 <= ord(base) < 0x7F:
            return base
    return " " if code < 0x20 else "?"


#: byte -> 8 coverage bytes, MSB first.
_DOTS = tuple(
    bytes(0xFF if value & (0x80 >> bit) else 0x00 for bit in range(8))
    for value in range(256)
)


class BitmapFont(Font):
    """The 8x8 font at ``scale_x`` x ``scale_y`` framebuffer pixels per dot.

    ``line_height`` is the bare cell, 8 * scale_y: the blank line above and
    below a row belongs to the layout, not the font, so text centred in a
    row lands where the cell grid expects it.
    """

    def __init__(self, scale_x: int = 1, scale_y: int = 1,
                 bold: bool = False):
        self.scale_x = max(1, int(scale_x))
        self.scale_y = max(1, int(scale_y))
        self.bold = bold
        self.mono = True
        self.pixel_size = 8 * self.scale_y
        self.pixel_aspect = 1.0
        self.x_ppem = 8 * self.scale_x
        self.ascent = self.line_height = 8 * self.scale_y
        self.descent = 0
        self.glyphs: dict[str, Glyph] = {}
        for code in range(0x20, 0x7F):
            self.glyphs[chr(code)] = self._make(code)
        self._fallback = self.glyphs["?"]

    def _make(self, code: int) -> Glyph:
        width = 8 * self.scale_x
        rows = []
        for byte in FONT8X8[code * 8:code * 8 + 8]:
            if self.bold:
                # Smear one dot right, the way a character generator bolds:
                # stems double, counters survive.
                byte |= byte >> 1
            dots = _DOTS[byte]
            if self.scale_x > 1:
                dots = b"".join(bytes((d,)) * self.scale_x for d in dots)
            rows.append(dots * self.scale_y)
        return Glyph(width, 8 * self.scale_y, 0, 8 * self.scale_y, width,
                     b"".join(rows))

    def glyph(self, char: str) -> Glyph:
        glyph = self.glyphs.get(char)
        if glyph is None:
            glyph = self.glyphs.get(_ascii_for(char), self._fallback)
            self.glyphs[char] = glyph
        return glyph
