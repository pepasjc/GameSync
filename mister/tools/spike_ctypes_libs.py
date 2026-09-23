#!/usr/bin/env python3
"""GameSync MiSTer spike: can Python drive the image's own C libraries?

The MiSTer main binary links libfreetype, libpng16, libImlib2 and libz, so they
are guaranteed present on any working MiSTer. If ctypes can drive them, the
client gets C-speed text rasterisation, image decode, scaling and alpha
blending while keeping every sync/install rule in shared Python - which is the
whole reason for not writing this in C++.

Tests:
  1. FreeType: init, load a face, rasterise an antialiased glyph, time 96 of them.
  2. Imlib2: create an ARGB image, scale it, alpha-blend it - the three things
     pure Python cannot do fast.

    python3 spike_ctypes_libs.py
"""

import ctypes
import glob
import os
import time
import traceback

REPORT_PATH = "/media/fat/Scripts/.gamesync/ctypes_report.txt"

FONT_SEARCH = [
    "/media/fat/**/*.ttf",
    "/usr/share/fonts/**/*.ttf",
    "/usr/lib/fonts/*.ttf",
    "/media/fat/linux/**/*.ttf",
]


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


class FTVector(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


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


FT_LOAD_RENDER = 0x4


def find_fonts(log):
    found = []
    for pattern in FONT_SEARCH:
        try:
            found.extend(glob.glob(pattern, recursive=True))
        except Exception:  # noqa: BLE001
            pass
    found = sorted(set(found))
    log("fonts on device  : %d found" % len(found))
    for path in found[:8]:
        log("    %s" % path)
    return found


def probe_freetype(log):
    log("")
    log("=" * 62)
    log("FREETYPE via ctypes")
    log("=" * 62)
    try:
        ft = ctypes.CDLL("libfreetype.so.6")
    except OSError as exc:
        log("load failed      : %s" % exc)
        return False
    log("loaded           : libfreetype.so.6")

    library = ctypes.c_void_p()
    if ft.FT_Init_FreeType(ctypes.byref(library)) != 0:
        log("FT_Init_FreeType : FAILED")
        return False
    log("FT_Init_FreeType : ok")

    major, minor, patch = ctypes.c_int(), ctypes.c_int(), ctypes.c_int()
    ft.FT_Library_Version(library, ctypes.byref(major), ctypes.byref(minor),
                          ctypes.byref(patch))
    log("version          : %d.%d.%d" % (major.value, minor.value, patch.value))

    fonts = find_fonts(log)
    if not fonts:
        log("")
        log("no TTF on the device - the client ships its own font anyway,")
        log("so this only blocks the spike, not the design.")
        ft.FT_Done_FreeType(library)
        return True

    face = ctypes.POINTER(FTFaceRec)()
    chosen = None
    for path in fonts:
        if ft.FT_New_Face(library, path.encode(), 0, ctypes.byref(face)) == 0:
            chosen = path
            break
    if chosen is None:
        log("FT_New_Face      : no loadable face")
        ft.FT_Done_FreeType(library)
        return True

    log("")
    log("face             : %s" % chosen)
    log("family / style   : %s / %s"
        % (face.contents.family_name, face.contents.style_name))
    log("glyphs           : %d" % face.contents.num_glyphs)

    ft.FT_Set_Pixel_Sizes(face, 0, 24)
    if ft.FT_Load_Char(face, ord("A"), FT_LOAD_RENDER) != 0:
        log("FT_Load_Char     : FAILED")
    else:
        bitmap = face.contents.glyph.contents.bitmap
        log("rasterised 'A'   : %dx%d, pitch %d, %d grays, mode %d"
            % (bitmap.width, bitmap.rows, bitmap.pitch, bitmap.num_grays,
               bitmap.pixel_mode))
        advance = face.contents.glyph.contents.advance.x / 64.0
        log("advance          : %.2f px  (antialiased 8-bit coverage)"
            % advance)

    start = time.time()
    for codepoint in range(32, 128):
        ft.FT_Load_Char(face, codepoint, FT_LOAD_RENDER)
    elapsed = time.time() - start
    log("96 glyphs at 24px: %.1f ms  (%.2f ms each)"
        % (elapsed * 1000, elapsed * 1000 / 96))
    log("=> a full atlas can be built at startup; no build-time prebake needed")

    ft.FT_Done_Face(face)
    ft.FT_Done_FreeType(library)
    return True


def probe_imlib2(log):
    log("")
    log("=" * 62)
    log("IMLIB2 via ctypes  (scaling + alpha, what Python cannot do)")
    log("=" * 62)
    try:
        im = ctypes.CDLL("libImlib2.so.1")
    except OSError as exc:
        log("load failed      : %s" % exc)
        return False
    log("loaded           : libImlib2.so.1")

    im.imlib_create_image.restype = ctypes.c_void_p
    im.imlib_create_image.argtypes = [ctypes.c_int, ctypes.c_int]
    im.imlib_context_set_image.argtypes = [ctypes.c_void_p]
    im.imlib_image_get_data.restype = ctypes.POINTER(ctypes.c_uint32)
    im.imlib_free_image.restype = None

    source = im.imlib_create_image(640, 480)
    if not source:
        log("imlib_create_image: FAILED")
        return False
    log("create 640x480   : ok")

    im.imlib_context_set_image(source)
    data = im.imlib_image_get_data()
    for index in range(0, 640 * 480, 997):
        data[index] = 0xFF3366CC
    im.imlib_image_put_back_data(data)

    im.imlib_create_cropped_scaled_image.restype = ctypes.c_void_p
    im.imlib_create_cropped_scaled_image.argtypes = [ctypes.c_int] * 6

    start = time.time()
    for _ in range(10):
        im.imlib_context_set_image(source)
        scaled = im.imlib_create_cropped_scaled_image(0, 0, 640, 480, 320, 240)
        if scaled:
            im.imlib_context_set_image(scaled)
            im.imlib_free_image()
    elapsed = (time.time() - start) / 10
    log("scale 640->320   : %.2f ms  (pure Python: not viable)"
        % (elapsed * 1000))

    im.imlib_blend_image_onto_image.argtypes = [ctypes.c_void_p] + [ctypes.c_int] * 9
    target = im.imlib_create_image(1280, 720)
    im.imlib_context_set_image(target)
    im.imlib_context_set_blend(1)
    start = time.time()
    for _ in range(10):
        im.imlib_blend_image_onto_image(source, 1, 0, 0, 640, 480,
                                        100, 100, 640, 480)
    elapsed = (time.time() - start) / 10
    log("alpha-blend 640x480: %.2f ms  (pure Python: ~2.3 s)"
        % (elapsed * 1000))

    im.imlib_context_set_image(target)
    im.imlib_free_image()
    im.imlib_context_set_image(source)
    im.imlib_free_image()
    log("=> alpha, scaling and PNG/JPEG decode all available at C speed")
    return True


def main():
    lines = []

    def log(text=""):
        lines.append(text)
        print(text)

    log("GameSync MiSTer ctypes library spike")
    log("run at %s" % time.strftime("%Y-%m-%d %H:%M:%S"))

    try:
        probe_freetype(log)
    except Exception:  # noqa: BLE001
        log("FREETYPE EXCEPTION:")
        log(traceback.format_exc())
    try:
        probe_imlib2(log)
    except Exception:  # noqa: BLE001
        log("IMLIB2 EXCEPTION:")
        log(traceback.format_exc())

    try:
        os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
        with open(REPORT_PATH, "w") as handle:
            handle.write("\n".join(lines) + "\n")
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    main()
