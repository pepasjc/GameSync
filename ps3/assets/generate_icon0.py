from __future__ import annotations

from pathlib import Path
from typing import Iterable

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont


WIDTH = 320
HEIGHT = 176
ROOT = Path(__file__).resolve().parent
OUT_PATH = ROOT / "ICON0.PNG"

FONT_ITALIC = Path(r"C:\Windows\Fonts\arialbi.ttf")
FONT_BLACK = Path(r"C:\Windows\Fonts\ariblk.ttf")
FONT_TAG = Path(r"C:\Windows\Fonts\AGENCYB.TTF")


def clamp(v: float, lo: float = 0.0, hi: float = 255.0) -> int:
    return int(max(lo, min(hi, round(v))))


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def color_lerp(c1: tuple[int, int, int], c2: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return (
        clamp(lerp(c1[0], c2[0], t)),
        clamp(lerp(c1[1], c2[1], t)),
        clamp(lerp(c1[2], c2[2], t)),
    )


def make_linear_gradient(size: tuple[int, int], top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    w, h = size
    img = Image.new("RGBA", size)
    px = img.load()
    for y in range(h):
        t = y / max(1, h - 1)
        color = color_lerp(top, bottom, t)
        for x in range(w):
            px[x, y] = (*color, 255)
    return img


def make_horizontal_gradient(size: tuple[int, int], left: tuple[int, int, int], right: tuple[int, int, int]) -> Image.Image:
    w, h = size
    img = Image.new("RGBA", size)
    px = img.load()
    for x in range(w):
        t = x / max(1, w - 1)
        color = color_lerp(left, right, t)
        for y in range(h):
            px[x, y] = (*color, 255)
    return img


def add_radial_glow(base: Image.Image, center: tuple[int, int], radius: int, color: tuple[int, int, int], strength: float) -> None:
    glow = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(glow)
    x, y = center
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(*color, clamp(255 * strength)))
    glow = glow.filter(ImageFilter.GaussianBlur(radius // 2))
    base.alpha_composite(glow)


def draw_beam(base: Image.Image, points: Iterable[tuple[int, int]], color: tuple[int, int, int], blur: int, alpha: int) -> None:
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.polygon(list(points), fill=(*color, alpha))
    layer = layer.filter(ImageFilter.GaussianBlur(blur))
    base.alpha_composite(layer)


def text_mask(text: str, font: ImageFont.FreeTypeFont) -> tuple[Image.Image, tuple[int, int]]:
    bbox = font.getbbox(text)
    mask = Image.new("L", (bbox[2] - bbox[0] + 8, bbox[3] - bbox[1] + 8), 0)
    draw = ImageDraw.Draw(mask)
    draw.text((4 - bbox[0], 4 - bbox[1]), text, font=font, fill=255)
    return mask, (bbox[0] - 4, bbox[1] - 4)


def gradient_text(
    base: Image.Image,
    text: str,
    position: tuple[int, int],
    font: ImageFont.FreeTypeFont,
    top: tuple[int, int, int],
    bottom: tuple[int, int, int],
    stroke: tuple[int, int, int],
    shadow: tuple[int, int, int],
) -> None:
    mask, offset = text_mask(text, font)
    w, h = mask.size
    x = position[0] + offset[0]
    y = position[1] + offset[1]

    shadow_layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    shadow_mask = Image.new("L", base.size, 0)
    shadow_mask.paste(mask, (x + 4, y + 5))
    shadow_mask = shadow_mask.filter(ImageFilter.GaussianBlur(5))
    shadow_layer.putalpha(shadow_mask)
    shadow_layer = ImageChops.multiply(
        shadow_layer,
        Image.new("RGBA", base.size, (*shadow, 200)),
    )
    base.alpha_composite(shadow_layer)

    stroke_layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    stroke_mask = Image.new("L", base.size, 0)
    for dx in range(-3, 4):
        for dy in range(-3, 4):
            if dx == 0 and dy == 0:
                continue
            stroke_mask.paste(mask, (x + dx, y + dy))
    stroke_mask = stroke_mask.filter(ImageFilter.GaussianBlur(1))
    stroke_layer.putalpha(stroke_mask)
    stroke_layer = ImageChops.multiply(
        stroke_layer,
        Image.new("RGBA", base.size, (*stroke, 255)),
    )
    base.alpha_composite(stroke_layer)

    fill = make_linear_gradient((w, h), top, bottom)
    fill.putalpha(mask)

    gloss = Image.new("RGBA", (w, h), (255, 255, 255, 0))
    gloss_mask = Image.new("L", (w, h), 0)
    gloss_draw = ImageDraw.Draw(gloss_mask)
    gloss_draw.rectangle((0, 0, w, h * 0.4), fill=140)
    gloss_mask = gloss_mask.filter(ImageFilter.GaussianBlur(8))
    gloss.putalpha(gloss_mask)
    fill.alpha_composite(gloss)

    base.alpha_composite(fill, (x, y))


def draw_sync_burst(base: Image.Image, center: tuple[int, int]) -> None:
    x, y = center
    burst = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(burst)
    for r, alpha in ((48, 28), (34, 44), (20, 70)):
        draw.ellipse((x - r, y - r, x + r, y + r), outline=(104, 218, 255, alpha), width=3)
    draw.polygon([(x, y - 18), (x + 5, y - 5), (x + 18, y), (x + 5, y + 5), (x, y + 18), (x - 5, y + 5), (x - 18, y), (x - 5, y - 5)], fill=(255, 255, 255, 220))
    for p1, p2 in (
        ((x - 30, y - 24), (x - 16, y - 10)),
        ((x + 17, y - 10), (x + 31, y - 24)),
        ((x - 30, y + 24), (x - 16, y + 10)),
        ((x + 17, y + 10), (x + 31, y + 24)),
    ):
        draw.line([p1, p2], fill=(126, 234, 255, 180), width=3)
    burst = burst.filter(ImageFilter.GaussianBlur(1))
    base.alpha_composite(burst)


def draw_console(base: Image.Image) -> None:
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    shadow = [(20, 108), (66, 49), (116, 57), (70, 118)]
    draw.polygon(shadow, fill=(0, 0, 0, 110))

    rear = [(24, 100), (66, 46), (112, 54), (70, 108)]
    front = [(33, 109), (75, 55), (121, 63), (79, 117)]
    side = [(70, 108), (112, 54), (121, 63), (79, 117)]

    draw.polygon(rear, fill=(20, 27, 42, 255))
    draw.polygon(front, fill=(36, 47, 70, 255))
    draw.polygon(side, fill=(14, 18, 30, 255))

    draw.line([(42, 96), (87, 39)], fill=(132, 162, 218, 120), width=2)
    draw.line([(47, 101), (92, 44)], fill=(70, 92, 140, 80), width=1)
    draw.line([(42, 101), (89, 106)], fill=(74, 204, 255, 220), width=3)
    draw.line([(44, 104), (89, 108)], fill=(255, 255, 255, 120), width=1)

    # Small chrome edge
    draw.line([(66, 46), (112, 54)], fill=(190, 204, 232, 140), width=2)
    draw.line([(24, 100), (70, 108)], fill=(8, 10, 16, 200), width=2)

    # PS button glow.
    draw.ellipse((64, 77, 77, 90), fill=(32, 38, 60, 255), outline=(120, 224, 255, 220), width=2)
    draw.line([(67, 83), (74, 83)], fill=(182, 241, 255, 220), width=2)
    draw.line([(70, 80), (70, 87)], fill=(182, 241, 255, 220), width=2)

    layer = layer.filter(ImageFilter.GaussianBlur(0.3))
    base.alpha_composite(layer)


def build_icon() -> Image.Image:
    canvas = make_linear_gradient((WIDTH, HEIGHT), (7, 10, 20), (15, 24, 43))

    haze = make_horizontal_gradient((WIDTH, HEIGHT), (12, 18, 32), (4, 7, 16))
    haze.putalpha(90)
    canvas.alpha_composite(haze)

    draw_beam(canvas, [(0, 140), (205, 32), (240, 32), (36, 175)], (28, 64, 112), 12, 78)
    draw_beam(canvas, [(58, 0), (225, 0), (145, 80), (0, 38)], (74, 40, 16), 18, 36)
    add_radial_glow(canvas, (92, 80), 44, (36, 170, 255), 0.48)
    add_radial_glow(canvas, (228, 106), 92, (18, 72, 128), 0.20)

    # Bottom light rail
    rail = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    rail_draw = ImageDraw.Draw(rail)
    rail_draw.rounded_rectangle((38, 138, 300, 147), radius=5, fill=(18, 20, 30, 180))
    rail_draw.rounded_rectangle((44, 141, 294, 143), radius=2, fill=(90, 220, 255, 140))
    rail = rail.filter(ImageFilter.GaussianBlur(2))
    canvas.alpha_composite(rail)

    draw_console(canvas)
    draw_sync_burst(canvas, (92, 79))

    game_font = ImageFont.truetype(str(FONT_ITALIC), 50)
    sync_font = ImageFont.truetype(str(FONT_ITALIC), 50)
    tag_font = ImageFont.truetype(str(FONT_TAG), 24)
    sub_font = ImageFont.truetype(str(FONT_BLACK), 13)

    gradient_text(
        canvas,
        "Game",
        (103, 42),
        game_font,
        top=(255, 200, 116),
        bottom=(229, 82, 56),
        stroke=(42, 10, 14),
        shadow=(10, 3, 4),
    )
    gradient_text(
        canvas,
        "Sync",
        (188, 67),
        sync_font,
        top=(210, 250, 255),
        bottom=(63, 174, 255),
        stroke=(8, 28, 52),
        shadow=(4, 10, 16),
    )

    # Smaller platform tag.
    tag_layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    tag_draw = ImageDraw.Draw(tag_layer)
    tag_draw.rounded_rectangle((222, 120, 292, 147), radius=8, fill=(14, 22, 36, 180), outline=(110, 176, 220, 170), width=2)
    tag_draw.text((240, 122), "PS3", font=tag_font, fill=(225, 233, 245, 255))
    tag_draw.text((104, 122), "CLOUD SAVE SYNC", font=sub_font, fill=(154, 174, 198, 220))
    tag_layer = tag_layer.filter(ImageFilter.GaussianBlur(0.2))
    canvas.alpha_composite(tag_layer)

    # Thin highlight line across the wordmark for a glossy 2000s-console feel.
    streak = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    streak_draw = ImageDraw.Draw(streak)
    streak_draw.polygon([(102, 58), (292, 39), (302, 47), (112, 66)], fill=(255, 255, 255, 80))
    streak = streak.filter(ImageFilter.GaussianBlur(3))
    canvas.alpha_composite(streak)

    return canvas.convert("RGB")


def main() -> None:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    build_icon().save(OUT_PATH, "PNG")
    print(f"wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
