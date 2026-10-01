#ifndef GFX_H
#define GFX_H

// Software 2D drawing into 16-bit (RGB555) surfaces: the DS client's UI is
// drawn into a RAM back buffer per screen and copied to a bitmap background
// (see video.c). Plain C with no libnds dependency, so the same code renders
// on a PC for the host screenshot test (tests/render_screens.c).

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

#define SCREEN_W 256
#define SCREEN_H 192

// DS bitmap colour: 5 bits per channel, red in the low bits, bit 15 = opaque
typedef uint16_t Color;
#define RGB(r, g, b) ((Color)(0x8000 | (((b) >> 3) << 10) | (((g) >> 3) << 5) | ((r) >> 3)))
// 0xRRGGBB
#define HEX(c) RGB(((c) >> 16) & 0xFF, ((c) >> 8) & 0xFF, (c) & 0xFF)

typedef struct {
    Color *px;
    int w, h;
    int cx0, cy0, cx1, cy1;  // clip rectangle, x1/y1 exclusive
} Surface;

// Bitmap font: printable ASCII from `first`, cells `height` rows tall, one
// uint16_t per row (bit 15 = leftmost pixel)
typedef struct {
    uint8_t height;
    uint8_t baseline;  // row of the baseline within the cell
    uint8_t first, count;
    const uint8_t *adv;
    const uint16_t *rows;
} Font;

extern const Font font_regular;  // Pixel Operator, proportional, 13 px cell
extern const Font font_bold;     // Pixel Operator Bold
extern const Font font_mono;     // Spleen 5x8, 5 px fixed

void gfx_surface_init(Surface *s, Color *px, int w, int h);
void gfx_clip(Surface *s, int x, int y, int w, int h);  // intersect with the screen
void gfx_unclip(Surface *s);

// alpha 0..256 (256 = opaque)
Color gfx_blend(Color under, Color over, int alpha);
Color gfx_mix(Color a, Color b, int alpha);  // same, for computing colours

void gfx_fill(Surface *s, int x, int y, int w, int h, Color c);
void gfx_fill_alpha(Surface *s, int x, int y, int w, int h, Color c, int alpha);
void gfx_hline(Surface *s, int x, int y, int w, Color c);
void gfx_vline(Surface *s, int x, int y, int h, Color c);
// Vertical gradient, ordered-dithered so 5-bit channels don't band
void gfx_vgradient(Surface *s, int x, int y, int w, int h, uint32_t top_rgb, uint32_t bottom_rgb);
// Rounded rectangle with anti-aliased corners
void gfx_round_rect(Surface *s, int x, int y, int w, int h, int r, Color c, int alpha);
void gfx_round_frame(Surface *s, int x, int y, int w, int h, int r, Color border, Color fill);
void gfx_circle(Surface *s, int cx, int cy, int r, Color c);  // centre at cx+0.5,cy+0.5 for even sizes

// 1-bit icon: `rows` of `w` bits (bit 15 = leftmost)
typedef struct {
    uint8_t w, h;
    const uint16_t *rows;
} Icon;
void gfx_icon(Surface *s, const Icon *icon, int x, int y, Color c);
void gfx_icon_scaled(Surface *s, const Icon *icon, int x, int y, int scale, Color c);

// Next character of UTF-8 text as a font code (ASCII; accented Latin-1
// letters folded to their base letter, anything else '?'), advancing *p
int gfx_next_char(const char **p);

// Text. y is the top of the font cell. Characters outside the font draw as '?'.
int gfx_text_width(const Font *f, const char *text);
int gfx_text_width_n(const Font *f, const char *text, int n);
int gfx_text(Surface *s, const Font *f, int x, int y, Color c, const char *text);  // returns end x
int gfx_text_n(Surface *s, const Font *f, int x, int y, Color c, const char *text, int n);
int gfx_text_scaled(Surface *s, const Font *f, int x, int y, int scale, Color c, const char *text);
// Cut to max_w pixels with "..." if needed; returns end x
int gfx_text_fit(Surface *s, const Font *f, int x, int y, int max_w, Color c, const char *text);
// Cut from the left ("...end of a long path")
int gfx_text_fit_left(Surface *s, const Font *f, int x, int y, int max_w, Color c, const char *text);
void gfx_text_right(Surface *s, const Font *f, int right_x, int y, Color c, const char *text);
void gfx_text_center(Surface *s, const Font *f, int cx, int y, Color c, const char *text);
// Word-wrapped into max_w; returns the number of lines drawn (s may be NULL
// to only count)
int gfx_text_wrap(Surface *s, const Font *f, int x, int y, int max_w, int line_h, int max_lines,
                  Color c, const char *text);

#endif
