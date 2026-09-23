#!/usr/bin/env python3
"""Create a simple 32x32 BMP icon for NDS homebrew."""

import struct

def create_simple_icon(filename):
    """Create a 32x32 16-color BMP icon."""
    width, height = 32, 32
    
    # BMP file header (14 bytes)
    # Offset = 14 (BMP hdr) + 40 (DIB hdr) + 16*4 (palette) = 118
    bmp_header = struct.pack('<2sIHHI',
        b'BM',          # Signature
        118 + 512,      # File size (header + palette + data) = 630
        0,              # Reserved
        0,              # Reserved
        118             # Offset to pixel data
    )
    
    # DIB header (40 bytes)
    dib_header = struct.pack('<IIIHHIIIIII',
        40,             # Header size
        width,          # Width
        height,         # Height
        1,              # Color planes
        4,              # Bits per pixel (16 colors)
        0,              # No compression
        512,            # Image size (32*32/2)
        0, 0, 16, 0     # Resolution, colors
    )
    
    # 16-color palette (64 bytes)
    palette = []
    for i in range(16):
        if i == 0:  # Background - blue
            palette.extend([255, 0, 0, 0])  # BGRA
        elif i == 15:  # Foreground - white
            palette.extend([255, 255, 255, 0])
        else:  # Gradient
            val = i * 16
            palette.extend([val, val, val, 0])
    
    # Create pixel data (bottom-up, 4-bit)
    pixels = []
    for y in range(height):
        row = []
        for x in range(height // 2):  # 2 pixels per byte
            # Simple pattern: border and "DS" text
            px1 = x * 2
            px2 = x * 2 + 1
            
            # Draw a simple border and fill
            if y < 2 or y >= height - 2 or px1 < 2 or px2 >= width - 2:
                c1 = c2 = 15  # White border
            elif (8 <= y < 24) and (8 <= px1 < 24):
                # Draw simple "DS" letters
                if (10 <= y < 22):
                    if (10 <= px1 < 13) or (px1 == 13 and y in [10, 21]):
                        c1 = 15  # D
                    elif (19 <= px1 < 22) or (px1 in [18, 22] and y in [10, 15, 21]):
                        c1 = 15  # S
                    else:
                        c1 = 0
                    
                    if (10 <= px2 < 13) or (px2 == 13 and y in [10, 21]):
                        c2 = 15  # D
                    elif (19 <= px2 < 22) or (px2 in [18, 22] and y in [10, 15, 21]):
                        c2 = 15  # S
                    else:
                        c2 = 0
                else:
                    c1 = c2 = 0
            else:
                c1 = c2 = 0  # Blue background
            
            byte = (c1 << 4) | c2
            row.append(byte)
        pixels.extend(row)
    
    # Write file
    with open(filename, 'wb') as f:
        f.write(bmp_header)
        f.write(dib_header)
        f.write(bytes(palette))
        f.write(bytes(pixels))
    
    print(f"Created {filename}")

if __name__ == '__main__':
    create_simple_icon('icon.bmp')
