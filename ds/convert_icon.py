#!/usr/bin/env python3
"""Convert a PNG icon to 32x32 16-color BMP for NDS homebrew (ndstool)."""

import struct
import sys

try:
    from PIL import Image
except ImportError:
    print("Pillow is required: pip install Pillow")
    sys.exit(1)


def convert_icon(input_path, output_path="icon.bmp"):
    img = Image.open(input_path).convert("RGBA")

    # Resize to 32x32
    img = img.resize((32, 32), Image.LANCZOS)

    # Quantize to 16 colors (4-bit)
    # Convert to RGB first (drop alpha), then quantize
    img_rgb = Image.new("RGB", img.size, (0, 0, 0))
    img_rgb.paste(img, mask=img.split()[3] if img.mode == "RGBA" else None)
    img_indexed = img_rgb.quantize(colors=16, method=Image.MEDIANCUT)

    palette_data = img_indexed.getpalette()  # flat list: [R,G,B, R,G,B, ...]
    pixels = list(img_indexed.getdata())

    width, height = 32, 32

    # Build 16-color BMP palette (BGRA format, 64 bytes)
    palette = bytearray()
    for i in range(16):
        r = palette_data[i * 3]
        g = palette_data[i * 3 + 1]
        b = palette_data[i * 3 + 2]
        palette.extend([b, g, r, 0])

    # Build pixel data (4-bit, bottom-up row order)
    pixel_bytes = bytearray()
    for y in range(height - 1, -1, -1):  # BMP is bottom-up
        for x in range(0, width, 2):
            idx = y * width + x
            p1 = pixels[idx] & 0x0F
            p2 = pixels[idx + 1] & 0x0F
            pixel_bytes.append((p1 << 4) | p2)

    # BMP header: offset = 14 + 40 + 64 = 118
    data_offset = 118
    file_size = data_offset + len(pixel_bytes)

    bmp_header = struct.pack('<2sIHHI',
        b'BM', file_size, 0, 0, data_offset)

    dib_header = struct.pack('<IIIHHIIIIII',
        40, width, height, 1, 4, 0,
        len(pixel_bytes), 0, 0, 16, 0)

    with open(output_path, 'wb') as f:
        f.write(bmp_header)
        f.write(dib_header)
        f.write(palette)
        f.write(pixel_bytes)

    print(f"Converted {input_path} -> {output_path}")
    print(f"  {width}x{height}, 16 colors, {file_size} bytes")


if __name__ == '__main__':
    src = sys.argv[1] if len(sys.argv) > 1 else "../client/icon.png"
    dst = sys.argv[2] if len(sys.argv) > 2 else "icon.bmp"
    convert_icon(src, dst)
