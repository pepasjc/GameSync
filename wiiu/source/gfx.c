/*
 * gfx.c — software renderer for the OSScreen framebuffers.  See gfx.h.
 *
 * OSScreen gives every screen one allocation holding two framebuffers and
 * flips between them; OSScreenPutPixelEx always writes the current *work*
 * buffer.  Rather than hard-coding where that buffer sits and how wide its
 * rows are (the GamePad's 854-pixel rows are padded, and emulators have been
 * known to disagree with hardware), each frame plants a marker pixel through
 * OSScreenPutPixelEx and looks for it: that yields the work buffer's address
 * and row pitch, after which every pixel is written directly — orders of
 * magnitude faster than one OSScreenPutPixelEx call per pixel.  Pixels are
 * 0xRRGGBBAA words, the same format OSScreenPutPixelEx takes.
 *
 * Text: stb_truetype (Sean Barrett, public domain / MIT,
 * https://github.com/nothings/stb — vendored under third_party/stb/)
 * rasterises the console's shared CafeStd font into an 8-bit coverage cache.
 */

#include "gfx.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

#include <coreinit/memory.h>
#include <coreinit/screen.h>

#define STBTT_STATIC
#define STB_TRUETYPE_IMPLEMENTATION
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wunused-function"
#pragma GCC diagnostic ignored "-Wsign-compare"
#include "stb_truetype.h"
#pragma GCC diagnostic pop

/* ---- target surface ---- */

static uint32_t *g_px = NULL;        /* work buffer, pixel (0,0) */
static int       g_pitch = 0;        /* words per row */
static int       g_dw = 0, g_dh = 0; /* device size */
static int       g_num = 1, g_den = 1;
static GfxScreen g_scr = GFX_TV;
static int       g_cx0, g_cy0, g_cx1, g_cy1;   /* device clip, exclusive max */

/* Work-buffer offsets (in words) seen per screen, so the full marker search
 * runs only the first time each of the two buffers comes round. */
#define MAX_SEEN 4
static uint32_t g_seen_off[2][MAX_SEEN];
static int      g_seen_pitch[2];
static int      g_seen_count[2];

/* Logical -> device, flooring (also for negative values). */
static inline int dv(int v) {
    int64_t t = (int64_t)v * g_num;
    return (int)(t >= 0 ? t / g_den : -((-t + g_den - 1) / g_den));
}
/* Device -> logical, rounding up (widths). */
static inline int lv(int v) {
    return (int)(((int64_t)v * g_den + g_num - 1) / g_num);
}

static inline uint32_t px_of(uint32_t rgb) { return (rgb << 8) | 0xFF; }

/* Rounded v / 255 without a divide (exact for v <= 255 * 255). */
static inline uint32_t div255(uint32_t v) {
    v += 128;
    return (v + (v >> 8)) >> 8;
}

static inline uint32_t blend_px(uint32_t dst, uint32_t rgb, int a) {
    if (a >= 255) return px_of(rgb);
    if (a <= 0) return dst;
    uint32_t ia = 255 - (uint32_t)a;
    uint32_t r = div255(((rgb >> 16) & 0xFF) * (uint32_t)a + ((dst >> 24) & 0xFF) * ia);
    uint32_t g = div255(((rgb >> 8) & 0xFF) * (uint32_t)a + ((dst >> 16) & 0xFF) * ia);
    uint32_t b = div255((rgb & 0xFF) * (uint32_t)a + ((dst >> 8) & 0xFF) * ia);
    return (r << 24) | (g << 16) | (b << 8) | 0xFF;
}

uint32_t gfx_mix(uint32_t a, uint32_t b, int t) {
    if (t < 0) t = 0;
    if (t > 256) t = 256;
    int it = 256 - t;
    uint32_t r = (((a >> 16) & 0xFF) * it + ((b >> 16) & 0xFF) * t) >> 8;
    uint32_t g = (((a >> 8) & 0xFF) * it + ((b >> 8) & 0xFF) * t) >> 8;
    uint32_t bl = ((a & 0xFF) * it + (b & 0xFF) * t) >> 8;
    return (r << 16) | (g << 8) | bl;
}

#define PROBE_A 0x13579BDFu
#define PROBE_B 0x2468ACE1u

bool gfx_begin(GfxScreen screen, void *buf, uint32_t size) {
    OSScreenID id = screen == GFX_TV ? SCREEN_TV : SCREEN_DRC;
    int w = screen == GFX_TV ? 1280 : 854;
    int h = screen == GFX_TV ? 720 : 480;
    uint32_t *base = (uint32_t *)buf;
    uint32_t words = size / 4;
    int s = (int)screen;

    g_px = NULL;
    if (!buf || size == 0) return false;

    /* Marker at (1,1): found at off + pitch + 1. */
    OSScreenPutPixelEx(id, 1, 1, PROBE_A);
    for (int i = 0; i < g_seen_count[s]; i++) {
        uint32_t idx = g_seen_off[s][i] + (uint32_t)g_seen_pitch[s] + 1;
        if (idx < words && base[idx] == PROBE_A) {
            g_px = base + g_seen_off[s][i];
            g_pitch = g_seen_pitch[s];
            break;
        }
    }

    if (!g_px) {
        /* First sight of this buffer: plant a second marker at (0,2) and find
         * both.  idx(1,1) = off + P + 1, idx(0,2) = off + 2P. */
        OSScreenPutPixelEx(id, 0, 2, PROBE_B);
        int64_t ia = -1, ib = -1;
        for (uint32_t i = 0; i < words && (ia < 0 || ib < 0); i++) {
            if (base[i] == PROBE_A && ia < 0) ia = i;
            else if (base[i] == PROBE_B && ib < 0) ib = i;
        }
        if (ia < 0 || ib < 0) return false;
        int64_t pitch = ib - ia + 1;
        int64_t off = ia - pitch - 1;
        if (pitch < w || pitch > 4096 || off < 0 ||
            (uint64_t)(off + pitch * h) > words)
            return false;
        g_pitch = (int)pitch;
        g_px = base + off;
        if (g_seen_pitch[s] != g_pitch) g_seen_count[s] = 0;
        g_seen_pitch[s] = g_pitch;
        if (g_seen_count[s] < MAX_SEEN)
            g_seen_off[s][g_seen_count[s]++] = (uint32_t)off;
    }

    g_scr = screen;
    g_dw = w;
    g_dh = h;
    if (screen == GFX_TV) { g_num = 1; g_den = 1; }
    else                  { g_num = 2; g_den = 3; }
    gfx_unclip();
    return true;
}

void gfx_clip(int x, int y, int w, int h) {
    g_cx0 = dv(x);       g_cy0 = dv(y);
    g_cx1 = dv(x + w);   g_cy1 = dv(y + h);
    if (g_cx0 < 0) g_cx0 = 0;
    if (g_cy0 < 0) g_cy0 = 0;
    if (g_cx1 > g_dw) g_cx1 = g_dw;
    if (g_cy1 > g_dh) g_cy1 = g_dh;
}

void gfx_unclip(void) {
    g_cx0 = 0; g_cy0 = 0; g_cx1 = g_dw; g_cy1 = g_dh;
}

/* ---- device-space primitives ---- */

static void d_span(int x0, int x1, int y, uint32_t p) {
    if (y < g_cy0 || y >= g_cy1) return;
    if (x0 < g_cx0) x0 = g_cx0;
    if (x1 > g_cx1) x1 = g_cx1;
    uint32_t *row = g_px + y * g_pitch;
    for (int x = x0; x < x1; x++) row[x] = p;
}

static void d_span_a(int x0, int x1, int y, uint32_t rgb, int a) {
    if (y < g_cy0 || y >= g_cy1) return;
    if (x0 < g_cx0) x0 = g_cx0;
    if (x1 > g_cx1) x1 = g_cx1;
    uint32_t *row = g_px + y * g_pitch;
    for (int x = x0; x < x1; x++) row[x] = blend_px(row[x], rgb, a);
}

static inline void d_plot_a(int x, int y, uint32_t rgb, int a) {
    if (x < g_cx0 || x >= g_cx1 || y < g_cy0 || y >= g_cy1) return;
    uint32_t *p = g_px + y * g_pitch + x;
    *p = blend_px(*p, rgb, a);
}

/* ---- shapes ---- */

void gfx_clear(uint32_t rgb) {
    if (!g_px) return;
    uint32_t p = px_of(rgb);
    for (int y = 0; y < g_dh; y++) {
        uint32_t *row = g_px + y * g_pitch;
        for (int x = 0; x < g_dw; x++) row[x] = p;
    }
}

void gfx_rect(int x, int y, int w, int h, uint32_t rgb) {
    if (!g_px || w <= 0 || h <= 0) return;
    int x0 = dv(x), x1 = dv(x + w), y0 = dv(y), y1 = dv(y + h);
    uint32_t p = px_of(rgb);
    for (int yy = y0; yy < y1; yy++) d_span(x0, x1, yy, p);
}

void gfx_rect_a(int x, int y, int w, int h, uint32_t rgb, int alpha) {
    if (!g_px || w <= 0 || h <= 0) return;
    int x0 = dv(x), x1 = dv(x + w), y0 = dv(y), y1 = dv(y + h);
    for (int yy = y0; yy < y1; yy++) d_span_a(x0, x1, yy, rgb, alpha);
}

void gfx_vgrad(int x, int y, int w, int h, uint32_t top, uint32_t bottom) {
    if (!g_px || w <= 0 || h <= 0) return;
    int x0 = dv(x), x1 = dv(x + w), y0 = dv(y), y1 = dv(y + h);
    int n = y1 - y0;
    for (int yy = y0; yy < y1; yy++) {
        int t = n > 1 ? (yy - y0) * 256 / (n - 1) : 0;
        d_span(x0, x1, yy, px_of(gfx_mix(top, bottom, t)));
    }
}

/* Coverage of a pixel against a circle of radius r centred at (cx, cy),
 * all in device pixels (pixel centres at +0.5). */
static int coverage(float px, float py, float cx, float cy, float r) {
    float dx = px - cx, dy = py - cy;
    float d = sqrtf(dx * dx + dy * dy);
    float c = r - d + 0.5f;
    if (c <= 0.f) return 0;
    if (c >= 1.f) return 255;
    return (int)(c * 255.f);
}

void gfx_rrect(int x, int y, int w, int h, int r, uint32_t rgb) {
    if (!g_px || w <= 0 || h <= 0) return;
    int x0 = dv(x), x1 = dv(x + w), y0 = dv(y), y1 = dv(y + h);
    int rd = dv(r);
    int dw = x1 - x0, dh = y1 - y0;
    if (rd * 2 > dw) rd = dw / 2;
    if (rd * 2 > dh) rd = dh / 2;
    if (rd <= 0) { gfx_rect(x, y, w, h, rgb); return; }

    uint32_t p = px_of(rgb);
    float lcx = (float)(x0 + rd), rcx = (float)(x1 - rd);
    for (int yy = y0; yy < y1; yy++) {
        float cy;
        if (yy < y0 + rd)       cy = (float)(y0 + rd);
        else if (yy >= y1 - rd) cy = (float)(y1 - rd);
        else { d_span(x0, x1, yy, p); continue; }

        /* Corner row: solid middle, anti-aliased ends. */
        float py = yy + 0.5f;
        d_span(x0 + rd, x1 - rd, yy, p);
        for (int xx = x0; xx < x0 + rd; xx++) {
            int a = coverage(xx + 0.5f, py, lcx, cy, (float)rd);
            if (a) d_plot_a(xx, yy, rgb, a);
        }
        for (int xx = x1 - rd; xx < x1; xx++) {
            int a = coverage(xx + 0.5f, py, rcx, cy, (float)rd);
            if (a) d_plot_a(xx, yy, rgb, a);
        }
    }
}

void gfx_circle(int cx, int cy, int r, uint32_t rgb) {
    if (!g_px || r <= 0) return;
    float fcx = dv(cx * 2) / 2.0f, fcy = dv(cy * 2) / 2.0f;
    float fr = r * (float)g_num / (float)g_den;
    int x0 = (int)floorf(fcx - fr - 1), x1 = (int)ceilf(fcx + fr + 1);
    int y0 = (int)floorf(fcy - fr - 1), y1 = (int)ceilf(fcy + fr + 1);
    for (int yy = y0; yy < y1; yy++)
        for (int xx = x0; xx < x1; xx++) {
            int a = coverage(xx + 0.5f, yy + 0.5f, fcx, fcy, fr);
            if (a) d_plot_a(xx, yy, rgb, a);
        }
}

void gfx_tri(int cx, int cy, int s, int dir, uint32_t rgb) {
    if (!g_px || s <= 0) return;
    int ds = dv(s), dcx = dv(cx), dcy = dv(cy);
    int half = ds / 2;
    uint32_t p = px_of(rgb);
    for (int i = 0; i <= half; i++) {
        int span = i;
        switch (dir) {
            case 0:  d_span(dcx - span, dcx + span + 1, dcy - half / 2 + i, p); break;
            case 1:  d_span(dcx - span, dcx + span + 1, dcy + half / 2 - i, p); break;
            default:
                for (int k = -span; k <= span; k++)
                    d_span(dcx - half / 2 + i, dcx - half / 2 + i + 1, dcy + k, p);
                break;
        }
    }
}

void gfx_check(int cx, int cy, int s, uint32_t rgb) {
    if (!g_px) return;
    /* Two thick strokes: short down-right, long up-right. */
    int t = s / 5 < 2 ? 2 : s / 5;
    for (int i = 0; i <= s / 3; i++)
        gfx_rect(cx - s / 2 + i, cy + i - t / 2, t, t, rgb);
    for (int i = 0; i <= (s * 2) / 3; i++)
        gfx_rect(cx - s / 2 + s / 3 + i, cy + s / 3 - i - t / 2, t, t, rgb);
}

/* ---- font ---- */

static stbtt_fontinfo g_font;
static bool  g_font_ok = false;
static int   g_ascent = 0, g_descent = 0;
static bool  g_has_ellipsis = false;

#define GLYPH_SLOTS 4096                 /* power of two */
#define GLYPH_POOL  (3u << 20)           /* coverage bytes */

typedef struct {
    uint32_t cp;
    uint16_t px;
    uint8_t  used;
    int16_t  xoff, yoff;
    uint16_t w, h;
    float    adv;
    uint32_t off;
} Glyph;

static Glyph   *g_glyphs = NULL;
static uint8_t *g_pool = NULL;
static uint32_t g_pool_used = 0;
static int      g_glyph_count = 0;

void gfx_init(void) {
    void *font = NULL;
    uint32_t size = 0;
    if (!OSGetSharedData(OS_SHAREDDATATYPE_FONT_STANDARD, 0, &font, &size) ||
        !font || size == 0)
        return;
    if (!stbtt_InitFont(&g_font, (const unsigned char *)font,
                        stbtt_GetFontOffsetForIndex((const unsigned char *)font, 0)))
        return;
    g_glyphs = calloc(GLYPH_SLOTS, sizeof(Glyph));
    g_pool = malloc(GLYPH_POOL);
    if (!g_glyphs || !g_pool) {
        free(g_glyphs); free(g_pool);
        g_glyphs = NULL; g_pool = NULL;
        return;
    }
    int gap;
    stbtt_GetFontVMetrics(&g_font, &g_ascent, &g_descent, &gap);
    g_has_ellipsis = stbtt_FindGlyphIndex(&g_font, 0x2026) != 0;
    g_font_ok = true;
}

bool gfx_has_font(void) { return g_font_ok; }

static Glyph *glyph_get(uint32_t cp, int px) {
    uint32_t hsh = (cp * 2654435761u) ^ ((uint32_t)px * 40503u);
    uint32_t i = hsh & (GLYPH_SLOTS - 1);
    for (;;) {
        Glyph *g = &g_glyphs[i];
        if (!g->used) break;
        if (g->cp == cp && g->px == px) return g;
        i = (i + 1) & (GLYPH_SLOTS - 1);
    }

    float sc = stbtt_ScaleForPixelHeight(&g_font, (float)px);
    int gi = stbtt_FindGlyphIndex(&g_font, (int)cp);
    if (!gi && cp > ' ') gi = stbtt_FindGlyphIndex(&g_font, '?');
    int x0 = 0, y0 = 0, x1 = 0, y1 = 0;
    stbtt_GetGlyphBitmapBox(&g_font, gi, sc, sc, &x0, &y0, &x1, &y1);
    uint32_t need = (uint32_t)((x1 - x0) * (y1 - y0));

    /* Full: drop everything and start over (cheap — a frame re-rasterises
     * only the glyphs it actually draws). */
    if (g_pool_used + need > GLYPH_POOL || g_glyph_count >= GLYPH_SLOTS * 3 / 4) {
        memset(g_glyphs, 0, GLYPH_SLOTS * sizeof(Glyph));
        g_pool_used = 0;
        g_glyph_count = 0;
        i = hsh & (GLYPH_SLOTS - 1);
    }

    Glyph *g = &g_glyphs[i];
    int adv = 0, lsb = 0;
    stbtt_GetGlyphHMetrics(&g_font, gi, &adv, &lsb);
    g->cp = cp;
    g->px = (uint16_t)px;
    g->used = 1;
    g->xoff = (int16_t)x0;
    g->yoff = (int16_t)y0;
    g->w = (uint16_t)(x1 - x0);
    g->h = (uint16_t)(y1 - y0);
    g->adv = adv * sc;
    g->off = g_pool_used;
    if (need) {
        stbtt_MakeGlyphBitmap(&g_font, g_pool + g_pool_used, g->w, g->h, g->w,
                              sc, sc, gi);
        g_pool_used += need;
    }
    g_glyph_count++;
    return g;
}

/* Decode one UTF-8 sequence; malformed bytes come back as '?'. */
static uint32_t utf8_next(const char **ps) {
    const unsigned char *s = (const unsigned char *)*ps;
    uint32_t c = s[0];
    int n = 0;
    if (c < 0x80)            { *ps += 1; return c; }
    else if ((c & 0xE0) == 0xC0) { c &= 0x1F; n = 1; }
    else if ((c & 0xF0) == 0xE0) { c &= 0x0F; n = 2; }
    else if ((c & 0xF8) == 0xF0) { c &= 0x07; n = 3; }
    else { *ps += 1; return '?'; }
    for (int i = 1; i <= n; i++) {
        if ((s[i] & 0xC0) != 0x80) { *ps += i; return '?'; }
        c = (c << 6) | (s[i] & 0x3F);
    }
    *ps += n + 1;
    return c;
}

static int dev_px(int size) {
    int p = dv(size);
    return p < 10 ? 10 : p;
}

/* OSScreen's own font: fixed 12x24 cells from a (50,32) origin, device px. */
#define OSF_CELL_W 12

/* Width of ``n`` bytes of ``s`` in device pixels. */
static float dev_width_n(int px, const char *s, size_t n) {
    if (!g_font_ok) {
        float w = 0;
        const char *e = s + n;
        while (s < e && *s) { utf8_next(&s); w += OSF_CELL_W; }
        return w;
    }
    float w = 0;
    const char *e = s + n;
    while (s < e && *s) {
        uint32_t cp = utf8_next(&s);
        w += glyph_get(cp, px)->adv;
    }
    return w;
}

static float dev_width(int px, const char *s) {
    return dev_width_n(px, s, strlen(s));
}

int gfx_line_h(int size) { return size; }

int gfx_text_w(int size, const char *s) {
    if (!s) return 0;
    if (g_dw == 0) { g_num = 1; g_den = 1; }   /* before the first frame */
    return lv((int)ceilf(dev_width(dev_px(size), s)));
}

static void draw_glyph(const Glyph *g, int pen_x, int base_y, uint32_t rgb) {
    const uint8_t *src = g_pool + g->off;
    for (int r = 0; r < g->h; r++) {
        int yy = base_y + g->yoff + r;
        if (yy < g_cy0 || yy >= g_cy1) continue;
        uint32_t *row = g_px + yy * g_pitch;
        for (int c = 0; c < g->w; c++) {
            int a = src[r * g->w + c];
            if (!a) continue;
            int xx = pen_x + g->xoff + c;
            if (xx < g_cx0 || xx >= g_cx1) continue;
            row[xx] = blend_px(row[xx], rgb, a);
        }
    }
}

/* Draw ``n`` bytes at device (dx, dy = line top).  Returns device width. */
static float dev_draw_n(int dx, int dy, int px, uint32_t rgb, const char *s, size_t n) {
    if (!g_font_ok) {
        /* Bitmap fallback: snap to the OSScreen character grid. */
        char buf[160];
        size_t k = 0;
        const char *e = s + n;
        while (s < e && *s && k < sizeof(buf) - 1) {
            uint32_t cp = utf8_next(&s);
            buf[k++] = (cp >= 0x20 && cp < 0x7F) ? (char)cp : '?';
        }
        buf[k] = '\0';
        int col = (dx - 50) / OSF_CELL_W, row = (dy - 32 + 12) / 24;
        if (col < 0) col = 0;
        if (row < 0) row = 0;
        OSScreenPutFontEx(g_scr == GFX_TV ? SCREEN_TV : SCREEN_DRC,
                          (uint32_t)col, (uint32_t)row, buf);
        return (float)k * OSF_CELL_W;
    }

    float sc = stbtt_ScaleForPixelHeight(&g_font, (float)px);
    int base_y = dy + (int)lroundf(g_ascent * sc);
    float pen = (float)dx;
    const char *e = s + n;
    while (s < e && *s) {
        uint32_t cp = utf8_next(&s);
        const Glyph *g = glyph_get(cp, px);
        if (g->w && g->h) draw_glyph(g, (int)lroundf(pen), base_y, rgb);
        pen += g->adv;
    }
    return pen - (float)dx;
}

int gfx_text(int x, int y, int size, uint32_t rgb, GfxAlign align, const char *s) {
    if (!g_px || !s) return 0;
    int px = dev_px(size);
    float w = dev_width(px, s);
    int dx = dv(x);
    if (align == GFX_CENTER)     dx -= (int)(w / 2);
    else if (align == GFX_RIGHT) dx -= (int)ceilf(w);
    dev_draw_n(dx, dv(y), px, rgb, s, strlen(s));
    return lv((int)ceilf(w));
}

int gfx_text_fit(int x, int y, int size, uint32_t rgb, GfxAlign align,
                 int max_w, const char *s) {
    if (!g_px || !s) return 0;
    int px = dev_px(size);
    float maxd = (float)dv(max_w);
    float w = dev_width(px, s);
    if (w <= maxd) return gfx_text(x, y, size, rgb, align, s);

    const char *ell = (g_font_ok && g_has_ellipsis) ? "\xE2\x80\xA6" : "...";
    float ew = dev_width(px, ell);
    /* Longest prefix (on a character boundary) that fits with the ellipsis. */
    size_t keep = 0;
    float acc = 0;
    const char *p = s;
    while (*p) {
        const char *q = p;
        uint32_t cp = utf8_next(&q);
        float cw = g_font_ok ? glyph_get(cp, px)->adv : OSF_CELL_W;
        if (acc + cw + ew > maxd) break;
        acc += cw;
        p = q;
        keep = (size_t)(p - s);
    }
    while (keep > 0 && s[keep - 1] == ' ') keep--;

    float tw = dev_width_n(px, s, keep) + ew;
    int dx = dv(x);
    if (align == GFX_CENTER)     dx -= (int)(tw / 2);
    else if (align == GFX_RIGHT) dx -= (int)ceilf(tw);
    float d = dev_draw_n(dx, dv(y), px, rgb, s, keep);
    dev_draw_n(dx + (int)lroundf(d), dv(y), px, rgb, ell, strlen(ell));
    return lv((int)ceilf(tw));
}

int gfx_text_mid(int x, int y, int h, int size, uint32_t rgb, GfxAlign align,
                 int max_w, const char *s) {
    int ty = y + (h - gfx_line_h(size)) / 2;
    if (max_w > 0) return gfx_text_fit(x, ty, size, rgb, align, max_w, s);
    return gfx_text(x, ty, size, rgb, align, s);
}

/* Greedy word wrap.  Calls ``emit`` per line with a byte range; returns the
 * number of lines.  The last allowed line takes whatever is left (and gets
 * cut with an ellipsis by the drawer). */
typedef void (*LineFn)(const char *s, size_t n, int line, bool last, void *ctx);

static int wrap_lines(int size, int max_w, int max_lines, const char *s,
                      LineFn emit, void *ctx) {
    if (!s) return 0;
    int px = dev_px(size);
    float maxd = (float)dv(max_w);
    int lines = 0;
    const char *p = s;
    while (*p && lines < max_lines) {
        /* Last permitted line: hand over the rest up to the next newline. */
        const char *nl = strchr(p, '\n');
        size_t rest = nl ? (size_t)(nl - p) : strlen(p);
        if (lines == max_lines - 1) {
            if (emit) emit(p, rest, lines, true, ctx);
            lines++;
            break;
        }

        size_t best = 0;          /* bytes that fit, ending on a word */
        size_t i = 0;
        while (i < rest) {
            size_t j = i;
            while (j < rest && p[j] != ' ') j++;          /* word end */
            if (dev_width_n(px, p, j) > maxd) break;
            best = j;
            while (j < rest && p[j] == ' ') j++;
            i = j;
        }
        if (best == 0 && rest > 0) {
            /* A single word wider than the line: break it by characters. */
            const char *q = p;
            while ((size_t)(q - p) < rest) {
                const char *r = q;
                utf8_next(&r);
                if (dev_width_n(px, p, (size_t)(r - p)) > maxd && q > p) break;
                q = r;
            }
            best = (size_t)(q - p);
        }
        if (best >= rest) best = rest;
        if (emit) emit(p, best, lines, false, ctx);
        lines++;
        p += best;
        if (*p == '\n') p++;
        else while (*p == ' ') p++;
    }
    return lines;
}

typedef struct {
    int x, y, size, max_w;
    uint32_t rgb;
    GfxAlign align;
} WrapDraw;

static void wrap_emit(const char *s, size_t n, int line, bool last, void *ctx) {
    WrapDraw *w = (WrapDraw *)ctx;
    char buf[512];
    if (n >= sizeof(buf)) n = sizeof(buf) - 1;
    memcpy(buf, s, n);
    buf[n] = '\0';
    int y = w->y + line * (gfx_line_h(w->size) + w->size / 4);
    if (last) gfx_text_fit(w->x, y, w->size, w->rgb, w->align, w->max_w, buf);
    else      gfx_text(w->x, y, w->size, w->rgb, w->align, buf);
}

int gfx_text_wrap_align(int x, int y, int size, uint32_t rgb, GfxAlign align,
                        int max_w, int max_lines, const char *s) {
    if (!g_px) return 0;
    WrapDraw w = { x, y, size, max_w, rgb, align };
    return wrap_lines(size, max_w, max_lines, s, wrap_emit, &w);
}

int gfx_text_wrap(int x, int y, int size, uint32_t rgb, int max_w,
                  int max_lines, const char *s) {
    return gfx_text_wrap_align(x, y, size, rgb, GFX_LEFT, max_w, max_lines, s);
}

int gfx_wrap_count(int size, int max_w, int max_lines, const char *s) {
    return wrap_lines(size, max_w, max_lines, s, NULL, NULL);
}
