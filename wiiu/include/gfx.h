#ifndef WIIUSYNC_GFX_H
#define WIIUSYNC_GFX_H

#include "common.h"

/*
 * gfx — a small software renderer straight into the OSScreen framebuffers.
 *
 * Everything is drawn in one logical 1280x720 coordinate space.  The TV is
 * 1280x720 and the GamePad 854x480 — exactly two thirds — so the same frame
 * is rendered twice: 1:1 on the TV, scaled by 2/3 on the GamePad, with text
 * rasterised at each screen's own pixel size (no blurry upscale).
 *
 * Text uses the console's shared system font (CafeStd, handed out by
 * OSGetSharedData — nothing is bundled) through stb_truetype, with a glyph
 * cache so a redraw only blits.  Should the shared font be unavailable the
 * OSScreen bitmap font is used instead, snapped to its fixed grid.
 *
 * Colours are 0xRRGGBB.
 */

#define GFX_W 1280
#define GFX_H 720

typedef enum {
    GFX_TV  = 0,
    GFX_DRC = 1,
} GfxScreen;

typedef enum {
    GFX_LEFT = 0,
    GFX_CENTER,
    GFX_RIGHT,
} GfxAlign;

/* Load the system font.  Safe to call before the screens exist. */
void gfx_init(void);

/* Point the renderer at a screen's back buffer (``buf``/``size`` are what
 * OSScreenSetBufferEx was given).  Returns false when the buffer layout could
 * not be identified — the caller then skips the screen. */
bool gfx_begin(GfxScreen screen, void *buf, uint32_t size);

/* Clip drawing to a logical rectangle (gfx_unclip resets to the screen). */
void gfx_clip(int x, int y, int w, int h);
void gfx_unclip(void);

/* ---- shapes ---- */

void gfx_clear(uint32_t rgb);
void gfx_rect(int x, int y, int w, int h, uint32_t rgb);
void gfx_rect_a(int x, int y, int w, int h, uint32_t rgb, int alpha);
void gfx_vgrad(int x, int y, int w, int h, uint32_t top, uint32_t bottom);
/* Rounded rectangle with anti-aliased corners. */
void gfx_rrect(int x, int y, int w, int h, int r, uint32_t rgb);
void gfx_circle(int cx, int cy, int r, uint32_t rgb);
/* Filled triangle pointing up / down / right, centred on (cx, cy). */
void gfx_tri(int cx, int cy, int s, int dir, uint32_t rgb);   /* 0 up 1 down 2 right */
void gfx_check(int cx, int cy, int s, uint32_t rgb);

/* Opaque blend of two colours, t in 0..256. */
uint32_t gfx_mix(uint32_t a, uint32_t b, int t);

/* ---- text (UTF-8) ---- */

/* ``size`` is the logical pixel height of the line (TV pixels). */
int  gfx_line_h(int size);
int  gfx_text_w(int size, const char *s);
/* Draw with the line box's top at y.  Returns the drawn logical width. */
int  gfx_text(int x, int y, int size, uint32_t rgb, GfxAlign align, const char *s);
/* Cut to ``max_w`` with an ellipsis. */
int  gfx_text_fit(int x, int y, int size, uint32_t rgb, GfxAlign align,
                  int max_w, const char *s);
/* Vertically centred in a box of height h. */
int  gfx_text_mid(int x, int y, int h, int size, uint32_t rgb, GfxAlign align,
                  int max_w, const char *s);
/* Word-wrapped paragraph ('\n' forces a break); returns the lines drawn. */
int  gfx_text_wrap(int x, int y, int size, uint32_t rgb, int max_w,
                   int max_lines, const char *s);
/* Same, aligned on x (GFX_CENTER: x is the centre of every line). */
int  gfx_text_wrap_align(int x, int y, int size, uint32_t rgb, GfxAlign align,
                         int max_w, int max_lines, const char *s);
int  gfx_wrap_count(int size, int max_w, int max_lines, const char *s);

/* True when the shared system font loaded. */
bool gfx_has_font(void);

#endif /* WIIUSYNC_GFX_H */
