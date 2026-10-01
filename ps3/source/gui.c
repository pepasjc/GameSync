/*
 * GameSync PS3 — drawing kit (see gui.h).
 *
 * Shapes go through SDL_gfx; text is drawn from a per-font glyph cache of
 * 8-bit coverage masks, alpha-blended straight into the 32-bit screen
 * surface.  Glyphs come from the console's system font via SDL_ttf, or
 * from SDL_gfx's 8x8 bitmap font (scaled up) when that is unavailable.
 */

#include "gui.h"

#include <SDL/SDL.h>
#include <SDL/SDL_gfxPrimitives.h>
#include <SDL/SDL_gfxPrimitives_font.h>
#include <SDL/SDL_ttf.h>

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* PS3 system fonts (Rodin = the XMB font).  Read-only firmware files on
 * every console; RPCS3 installs them with the firmware. */
#define FONT_REGULAR "/dev_flash/data/font/SCE-PS3-RD-R-LATIN.TTF"
#define FONT_BOLD    "/dev_flash/data/font/SCE-PS3-RD-B-LATIN.TTF"

#define FALLBACK_W 1280
#define FALLBACK_H 720

typedef struct {
    uint8_t *alpha;      /* w*h coverage, NULL for blank glyphs */
    short    w, h;
    short    xoff, yoff; /* from pen x / line top, physical px */
    short    adv;
    bool     loaded;
} Glyph;

typedef struct {
    TTF_Font *ttf;       /* NULL = bitmap fallback */
    int       px;        /* nominal size, physical px */
    int       line_h;    /* physical px */
    int       ascent;
    int       bmp_k;     /* bitmap scale factor */
    Glyph     glyphs[256];
} FontSlot;

static SDL_Surface *g_screen = NULL;
static float g_sx = 1.0f, g_sy = 1.0f;
static FontSlot g_fonts[GUI_F_COUNT];
static bool g_ttf_ok = false;

static Uint32 *g_backdrop = NULL;     /* last full screen */
static Uint32 *g_backdrop_dim = NULL; /* dimmed copy, built lazily */
static bool g_backdrop_valid = false;
static bool g_backdrop_dim_valid = false;
static unsigned g_frame_id = 0;

static const int k_font_sizes[GUI_F_COUNT] = { 15, 18, 18, 22, 28 };
static const bool k_font_bold[GUI_F_COUNT] = { false, false, true, true, true };

/* ------------------------------------------------------------------ */
/* Coordinates and colours                                             */
/* ------------------------------------------------------------------ */

static inline int SX(int x) { return (int)((float)x * g_sx + 0.5f); }
static inline int SY(int y) { return (int)((float)y * g_sy + 0.5f); }
static inline int to_logical_w(int px) { return (int)((float)px / g_sx + 0.5f); }

static inline Uint8 hr(uint32_t c) { return (Uint8)((c >> 16) & 0xFF); }
static inline Uint8 hg(uint32_t c) { return (Uint8)((c >> 8) & 0xFF); }
static inline Uint8 hb(uint32_t c) { return (Uint8)(c & 0xFF); }

uint32_t gui_mix(uint32_t a, uint32_t b, float t) {
    if (t < 0.0f) t = 0.0f;
    if (t > 1.0f) t = 1.0f;
    int r = (int)(hr(a) + (hr(b) - hr(a)) * t);
    int g = (int)(hg(a) + (hg(b) - hg(a)) * t);
    int bl = (int)(hb(a) + (hb(b) - hb(a)) * t);
    return ((uint32_t)r << 16) | ((uint32_t)g << 8) | (uint32_t)bl;
}

static Uint32 map_hex(uint32_t c) {
    return SDL_MapRGB(g_screen->format, hr(c), hg(c), hb(c));
}

/* ------------------------------------------------------------------ */
/* Fonts                                                               */
/* ------------------------------------------------------------------ */

static void font_free(FontSlot *f) {
    for (int i = 0; i < 256; i++) {
        free(f->glyphs[i].alpha);
        f->glyphs[i].alpha = NULL;
        f->glyphs[i].loaded = false;
    }
    if (f->ttf) {
        TTF_CloseFont(f->ttf);
        f->ttf = NULL;
    }
}

static void fonts_open(void) {
    g_ttf_ok = (TTF_Init() == 0);
    for (int i = 0; i < GUI_F_COUNT; i++) {
        FontSlot *f = &g_fonts[i];
        memset(f, 0, sizeof(*f));
        f->px = (int)((float)k_font_sizes[i] * g_sy + 0.5f);
        if (f->px < 8) f->px = 8;

        if (g_ttf_ok) {
            f->ttf = TTF_OpenFont(k_font_bold[i] ? FONT_BOLD : FONT_REGULAR, f->px);
            if (!f->ttf && k_font_bold[i]) {
                f->ttf = TTF_OpenFont(FONT_REGULAR, f->px);
                if (f->ttf) TTF_SetFontStyle(f->ttf, TTF_STYLE_BOLD);
            }
        }
        if (f->ttf) {
            f->ascent = TTF_FontAscent(f->ttf);
            f->line_h = TTF_FontHeight(f->ttf);
        } else {
            f->bmp_k = (f->px + 4) / 9;
            if (f->bmp_k < 1) f->bmp_k = 1;
            f->line_h = 10 * f->bmp_k;
            f->ascent = 9 * f->bmp_k;
        }
    }
}

bool gui_has_system_font(void) {
    return g_fonts[GUI_F_BODY].ttf != NULL;
}

static void glyph_load_bitmap(FontSlot *f, Glyph *g, int cp) {
    int k = f->bmp_k;
    const unsigned char *rows = &gfxPrimitivesFontdata[(cp & 0xFF) * 8];
    g->w = (short)(8 * k);
    g->h = (short)(8 * k);
    g->xoff = 0;
    g->yoff = (short)k;
    g->adv = (short)(7 * k);   /* the 8x8 cells carry their own gap */
    g->alpha = (uint8_t *)calloc((size_t)g->w * (size_t)g->h, 1);
    if (!g->alpha) return;
    for (int ry = 0; ry < 8; ry++) {
        for (int rx = 0; rx < 8; rx++) {
            if (!(rows[ry] & (0x80 >> rx))) continue;
            for (int dy = 0; dy < k; dy++)
                memset(&g->alpha[(ry * k + dy) * g->w + rx * k], 0xFF, (size_t)k);
        }
    }
}

static void glyph_load_ttf(FontSlot *f, Glyph *g, int cp) {
    int minx = 0, maxx = 0, miny = 0, maxy = 0, adv = 0;
    SDL_Color white = { 255, 255, 255, 0 };

    if (TTF_GlyphMetrics(f->ttf, (Uint16)cp, &minx, &maxx, &miny, &maxy, &adv) != 0) {
        if (cp != '?') {
            Glyph *q = &f->glyphs['?'];
            if (!q->loaded) glyph_load_ttf(f, q, '?');
            g->adv = q->adv;
        }
        return;
    }
    g->adv = (short)adv;
    g->xoff = (short)minx;
    g->yoff = (short)(f->ascent - maxy);

    SDL_Surface *s = TTF_RenderGlyph_Blended(f->ttf, (Uint16)cp, white);
    if (!s) return;
    if (s->w > 0 && s->h > 0) {
        g->w = (short)s->w;
        g->h = (short)s->h;
        g->alpha = (uint8_t *)malloc((size_t)s->w * (size_t)s->h);
        if (g->alpha) {
            if (SDL_MUSTLOCK(s)) SDL_LockSurface(s);
            for (int y = 0; y < s->h; y++) {
                const Uint32 *src = (const Uint32 *)((const Uint8 *)s->pixels + y * s->pitch);
                for (int x = 0; x < s->w; x++)
                    g->alpha[y * s->w + x] = (uint8_t)((src[x] & s->format->Amask) >> s->format->Ashift);
            }
            if (SDL_MUSTLOCK(s)) SDL_UnlockSurface(s);
        }
    }
    SDL_FreeSurface(s);
}

static const Glyph *glyph_get(FontSlot *f, int cp) {
    if (cp < 32 || cp > 255 || (cp >= 127 && cp < 160)) cp = '?';
    if (!f->ttf && cp > 126) cp = '?';
    Glyph *g = &f->glyphs[cp];
    if (!g->loaded) {
        g->loaded = true;
        if (f->ttf) glyph_load_ttf(f, g, cp);
        else        glyph_load_bitmap(f, g, cp);
    }
    return g;
}

/* Decode one UTF-8 code point; folds common punctuation outside Latin-1. */
static int utf8_next(const char **ps) {
    const unsigned char *s = (const unsigned char *)*ps;
    int cp, n;
    if (s[0] < 0x80)              { cp = s[0]; n = 1; }
    else if ((s[0] & 0xE0) == 0xC0 && (s[1] & 0xC0) == 0x80) {
        cp = ((s[0] & 0x1F) << 6) | (s[1] & 0x3F); n = 2;
    } else if ((s[0] & 0xF0) == 0xE0 && (s[1] & 0xC0) == 0x80 && (s[2] & 0xC0) == 0x80) {
        cp = ((s[0] & 0x0F) << 12) | ((s[1] & 0x3F) << 6) | (s[2] & 0x3F); n = 3;
    } else if ((s[0] & 0xF8) == 0xF0 && s[1] && s[2] && s[3]) {
        cp = '?'; n = 4;
    } else {
        cp = s[0] >= 0xA0 ? s[0] : '?';   /* stray Latin-1 byte */
        n = 1;
    }
    *ps += n;
    switch (cp) {
        case 0x2018: case 0x2019: return '\'';
        case 0x201C: case 0x201D: return '"';
        case 0x2013: case 0x2014: return '-';
        case 0x2022: return 0xB7;          /* bullet -> middle dot */
        case 0x2122: return 0xAE;          /* (tm) -> (R), close enough */
        default: break;
    }
    return cp;
}

static int text_px_w(GuiFont font, const char *s) {
    FontSlot *f = &g_fonts[font];
    int w = 0;
    if (!s) return 0;
    while (*s) {
        int cp = utf8_next(&s);
        w += glyph_get(f, cp)->adv;
    }
    return w;
}

/* Blend a coverage mask into the screen at physical (px, py). */
static void blit_glyph(int px, int py, const Glyph *g, uint32_t color) {
    if (!g->alpha) return;
    const SDL_PixelFormat *fmt = g_screen->format;
    int x0 = px < 0 ? -px : 0;
    int y0 = py < 0 ? -py : 0;
    int x1 = g->w, y1 = g->h;
    if (px + x1 > g_screen->w) x1 = g_screen->w - px;
    if (py + y1 > g_screen->h) y1 = g_screen->h - py;
    if (x0 >= x1 || y0 >= y1) return;

    Uint32 cr = hr(color), cg = hg(color), cb = hb(color);
    for (int y = y0; y < y1; y++) {
        Uint32 *row = (Uint32 *)((Uint8 *)g_screen->pixels + (py + y) * g_screen->pitch) + px;
        const uint8_t *a = g->alpha + y * g->w;
        for (int x = x0; x < x1; x++) {
            Uint32 al = a[x];
            if (al == 0) continue;
            Uint32 d = row[x];
            Uint32 dr = (d & fmt->Rmask) >> fmt->Rshift;
            Uint32 dg = (d & fmt->Gmask) >> fmt->Gshift;
            Uint32 db = (d & fmt->Bmask) >> fmt->Bshift;
            if (al == 255) {
                dr = cr; dg = cg; db = cb;
            } else {
                dr = dr + (((cr - dr) * al) >> 8);
                dg = dg + (((cg - dg) * al) >> 8);
                db = db + (((cb - db) * al) >> 8);
            }
            row[x] = (dr << fmt->Rshift) | (dg << fmt->Gshift) | (db << fmt->Bshift) | fmt->Amask;
        }
    }
}

/* Draw at physical coordinates, returns width in physical px. */
static int draw_px(int px, int py, GuiFont font, uint32_t color, const char *s) {
    FontSlot *f = &g_fonts[font];
    int pen = px;
    if (!s) return 0;
    if (SDL_MUSTLOCK(g_screen)) SDL_LockSurface(g_screen);
    while (*s) {
        int cp = utf8_next(&s);
        const Glyph *g = glyph_get(f, cp);
        blit_glyph(pen + g->xoff, py + g->yoff, g, color);
        pen += g->adv;
    }
    if (SDL_MUSTLOCK(g_screen)) SDL_UnlockSurface(g_screen);
    return pen - px;
}

int gui_line_h(GuiFont font) {
    return (int)((float)g_fonts[font].line_h / g_sy + 0.5f);
}

int gui_text_w(GuiFont font, const char *s) {
    return to_logical_w(text_px_w(font, s));
}

int gui_text(int x, int y, GuiFont font, uint32_t color, GuiAlign align, const char *s) {
    if (!g_screen || !s) return 0;
    int w = text_px_w(font, s);
    int px = SX(x);
    if (align == GUI_CENTER) px -= w / 2;
    else if (align == GUI_RIGHT) px -= w;
    draw_px(px, SY(y), font, color, s);
    return to_logical_w(w);
}

int gui_textf(int x, int y, GuiFont font, uint32_t color, GuiAlign align, const char *fmt, ...) {
    char buf[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    return gui_text(x, y, font, color, align, buf);
}

/* Copy the longest prefix of s that fits max_px (with an ellipsis when cut). */
static void fit_into(GuiFont font, const char *s, int max_px, char *out, size_t out_size) {
    FontSlot *f = &g_fonts[font];
    if (text_px_w(font, s) <= max_px) {
        snprintf(out, out_size, "%s", s);
        return;
    }
    int ell = text_px_w(font, "...");
    int w = 0;
    const char *p = s;
    const char *cut = s;
    while (*p) {
        const char *before = p;
        int cp = utf8_next(&p);
        int adv = glyph_get(f, cp)->adv;
        if (w + adv + ell > max_px) { cut = before; break; }
        w += adv;
        cut = p;
    }
    size_t n = (size_t)(cut - s);
    if (n + 4 > out_size) n = out_size - 4;
    memcpy(out, s, n);
    /* drop trailing spaces before the ellipsis */
    while (n > 0 && out[n - 1] == ' ') n--;
    memcpy(out + n, "...", 4);
}

int gui_text_fit(int x, int y, GuiFont font, uint32_t color, GuiAlign align, int max_w, const char *s) {
    char buf[512];
    if (!s) return 0;
    fit_into(font, s, (int)((float)max_w * g_sx), buf, sizeof(buf));
    return gui_text(x, y, font, color, align, buf);
}

int gui_text_mid(int x, int y, int h, GuiFont font, uint32_t color, GuiAlign align, int max_w, const char *s) {
    int ty = y + (h - gui_line_h(font)) / 2;
    if (max_w > 0) return gui_text_fit(x, ty, font, color, align, max_w, s);
    return gui_text(x, ty, font, color, align, s);
}

int gui_wrap(const char *s, GuiFont font, int max_w, char lines[][GUI_WRAP_LINE], int max_lines) {
    FontSlot *f = &g_fonts[font];
    int max_px = (int)((float)max_w * g_sx);
    int n = 0;
    const char *p = s ? s : "";

    while (*p && n < max_lines) {
        char *line = lines[n];
        size_t len = 0;
        int w = 0;
        size_t space_len = 0;
        const char *space_next = NULL;
        const char *q = p;

        while (*q && *q != '\n') {
            const char *before = q;
            int cp = utf8_next(&q);
            int adv = glyph_get(f, cp)->adv;
            size_t bytes = (size_t)(q - before);
            if ((w + adv > max_px && len > 0) || len + bytes >= GUI_WRAP_LINE - 4) {
                q = before;
                break;
            }
            if (cp == ' ') {
                space_len = len;
                space_next = q;
            }
            memcpy(line + len, before, bytes);
            len += bytes;
            w += adv;
        }
        if (*q && *q != '\n' && space_next) {
            len = space_len;
            q = space_next;
        }
        line[len] = '\0';
        n++;

        if (*q == '\n') q++;
        else while (*q == ' ') q++;
        p = q;
    }

    if (*p && n > 0) {
        char tmp[GUI_WRAP_LINE + 8];
        snprintf(tmp, sizeof(tmp), "%s ...", lines[n - 1]);
        /* Either fits as "line ..." or gets cut with an ellipsis. */
        fit_into(font, tmp, max_px, lines[n - 1], GUI_WRAP_LINE);
    }
    return n;
}

int gui_text_wrap(int x, int y, GuiFont font, uint32_t color, int max_w, int max_lines, const char *s) {
    char lines[12][GUI_WRAP_LINE];
    if (max_lines > 12) max_lines = 12;
    int n = gui_wrap(s, font, max_w, lines, max_lines);
    int lh = gui_line_h(font);
    for (int i = 0; i < n; i++)
        gui_text(x, y + i * lh, font, color, GUI_LEFT, lines[i]);
    return n;
}

/* ------------------------------------------------------------------ */
/* Lifetime and frames                                                 */
/* ------------------------------------------------------------------ */

bool gui_init(char *error_buf, size_t error_buf_size) {
    if (SDL_Init(SDL_INIT_VIDEO | SDL_INIT_JOYSTICK) != 0) {
        snprintf(error_buf, error_buf_size, "SDL_Init failed: %s", SDL_GetError());
        return false;
    }

    /* 0x0 = the console's current output mode (720p / 1080p / ...). */
    g_screen = SDL_SetVideoMode(0, 0, 32, SDL_SWSURFACE);
    if (!g_screen || g_screen->w < 320 || g_screen->h < 240 ||
        g_screen->format->BitsPerPixel != 32) {
        g_screen = SDL_SetVideoMode(FALLBACK_W, FALLBACK_H, 32, SDL_SWSURFACE);
    }
    if (!g_screen) {
        snprintf(error_buf, error_buf_size, "SDL_SetVideoMode failed: %s", SDL_GetError());
        SDL_Quit();
        return false;
    }
    SDL_ShowCursor(SDL_DISABLE);

    g_sx = (float)g_screen->w / (float)GUI_W;
    g_sy = (float)g_screen->h / (float)GUI_H;

    size_t bytes = (size_t)g_screen->pitch * (size_t)g_screen->h;
    g_backdrop = (Uint32 *)malloc(bytes);
    g_backdrop_dim = (Uint32 *)malloc(bytes);

    fonts_open();
    return true;
}

void gui_shutdown(void) {
    for (int i = 0; i < GUI_F_COUNT; i++) font_free(&g_fonts[i]);
    if (g_ttf_ok) TTF_Quit();
    g_ttf_ok = false;
    free(g_backdrop);
    free(g_backdrop_dim);
    g_backdrop = g_backdrop_dim = NULL;
    g_screen = NULL;
    SDL_Quit();
}

void gui_clear(void) {
    if (!g_screen) return;
    /* Vertical gradient BG -> BG2, in bands so it stays cheap. */
    int h = g_screen->h;
    const int bands = 48;
    for (int i = 0; i < bands; i++) {
        SDL_Rect r;
        r.x = 0;
        r.y = (Sint16)(h * i / bands);
        r.w = (Uint16)g_screen->w;
        r.h = (Uint16)(h * (i + 1) / bands - r.y);
        SDL_FillRect(g_screen, &r, map_hex(gui_mix(HEX_BG, HEX_BG2, (float)i / (bands - 1))));
    }
}

unsigned gui_frame_id(void) {
    return g_frame_id;
}

void gui_present(bool remember) {
    if (!g_screen) return;
    g_frame_id++;
    if (remember && g_backdrop) {
        memcpy(g_backdrop, g_screen->pixels, (size_t)g_screen->pitch * (size_t)g_screen->h);
        g_backdrop_valid = true;
        g_backdrop_dim_valid = false;
    }
    SDL_PumpEvents();
    SDL_Flip(g_screen);
}

void gui_backdrop(void) {
    if (!g_screen) return;
    size_t bytes = (size_t)g_screen->pitch * (size_t)g_screen->h;
    if (!g_backdrop_valid || !g_backdrop_dim) {
        gui_clear();
        return;
    }
    if (!g_backdrop_dim_valid) {
        /* ~37% brightness plus a little navy so the dim reads as "behind". */
        const SDL_PixelFormat *fmt = g_screen->format;
        Uint32 tint = SDL_MapRGB(g_screen->format, 6, 9, 13) & ~fmt->Amask;
        size_t n = bytes / 4;
        for (size_t i = 0; i < n; i++) {
            Uint32 p = g_backdrop[i];
            g_backdrop_dim[i] = (((p >> 2) & 0x3F3F3F3F) + ((p >> 3) & 0x1F1F1F1F) + tint)
                              | fmt->Amask;
        }
        g_backdrop_dim_valid = true;
    }
    memcpy(g_screen->pixels, g_backdrop_dim, bytes);
}

/* ------------------------------------------------------------------ */
/* Shapes                                                              */
/* ------------------------------------------------------------------ */

void gui_rect(int x, int y, int w, int h, uint32_t color) {
    if (!g_screen || w <= 0 || h <= 0) return;
    SDL_Rect r;
    r.x = (Sint16)SX(x);
    r.y = (Sint16)SY(y);
    r.w = (Uint16)(SX(x + w) - r.x);
    r.h = (Uint16)(SY(y + h) - r.y);
    if (r.w == 0) r.w = 1;
    if (r.h == 0) r.h = 1;
    SDL_FillRect(g_screen, &r, map_hex(color));
}

void gui_rect_a(int x, int y, int w, int h, uint32_t color, int alpha) {
    if (!g_screen || w <= 0 || h <= 0) return;
    boxRGBA(g_screen, (Sint16)SX(x), (Sint16)SY(y),
            (Sint16)(SX(x + w) - 1), (Sint16)(SY(y + h) - 1),
            hr(color), hg(color), hb(color), (Uint8)alpha);
}

static int clamp_radius(int r, int w, int h) {
    int pr = SY(r);
    int pw = SX(w), ph = SY(h);
    if (pr * 2 > pw) pr = pw / 2;
    if (pr * 2 > ph) pr = ph / 2;
    return pr < 1 ? 1 : pr;
}

void gui_rrect(int x, int y, int w, int h, int r, uint32_t color) {
    if (!g_screen || w <= 0 || h <= 0) return;
    if (r <= 0) { gui_rect(x, y, w, h, color); return; }
    roundedBoxRGBA(g_screen, (Sint16)SX(x), (Sint16)SY(y),
                   (Sint16)(SX(x + w) - 1), (Sint16)(SY(y + h) - 1),
                   (Sint16)clamp_radius(r, w, h), hr(color), hg(color), hb(color), 255);
}

void gui_rrect_outline(int x, int y, int w, int h, int r, uint32_t color) {
    if (!g_screen || w <= 0 || h <= 0) return;
    roundedRectangleRGBA(g_screen, (Sint16)SX(x), (Sint16)SY(y),
                         (Sint16)(SX(x + w) - 1), (Sint16)(SY(y + h) - 1),
                         (Sint16)clamp_radius(r, w, h), hr(color), hg(color), hb(color), 255);
}

void gui_circle(int cx, int cy, int r, uint32_t color) {
    if (!g_screen) return;
    int pr = SY(r);
    filledCircleRGBA(g_screen, (Sint16)SX(cx), (Sint16)SY(cy), (Sint16)pr,
                     hr(color), hg(color), hb(color), 255);
    aacircleRGBA(g_screen, (Sint16)SX(cx), (Sint16)SY(cy), (Sint16)pr,
                 hr(color), hg(color), hb(color), 255);
}

void gui_line(int x1, int y1, int x2, int y2, int thick, uint32_t color) {
    if (!g_screen) return;
    int t = SY(thick);
    if (t < 1) t = 1;
    thickLineRGBA(g_screen, (Sint16)SX(x1), (Sint16)SY(y1), (Sint16)SX(x2), (Sint16)SY(y2),
                  (Uint8)t, hr(color), hg(color), hb(color), 255);
}

void gui_tri(int x1, int y1, int x2, int y2, int x3, int y3, uint32_t color) {
    if (!g_screen) return;
    filledTrigonRGBA(g_screen, (Sint16)SX(x1), (Sint16)SY(y1), (Sint16)SX(x2), (Sint16)SY(y2),
                     (Sint16)SX(x3), (Sint16)SY(y3), hr(color), hg(color), hb(color), 255);
    aatrigonRGBA(g_screen, (Sint16)SX(x1), (Sint16)SY(y1), (Sint16)SX(x2), (Sint16)SY(y2),
                 (Sint16)SX(x3), (Sint16)SY(y3), hr(color), hg(color), hb(color), 255);
}

/* ------------------------------------------------------------------ */
/* Widgets                                                             */
/* ------------------------------------------------------------------ */

int gui_pill_w(int h, GuiFont font, const char *label) {
    return gui_text_w(font, label) + h;   /* h/2 padding each side */
}

int gui_pill(int x, int y, int h, GuiFont font, uint32_t bg, uint32_t fg, const char *label) {
    int w = gui_pill_w(h, font, label);
    gui_rrect(x, y, w, h, h / 2, bg);
    gui_text_mid(x + h / 2, y, h, font, fg, GUI_LEFT, 0, label);
    return w;
}

int gui_tag(int x, int y, int h, uint32_t color, const char *label) {
    return gui_pill(x, y, h, GUI_F_SMALL, gui_mix(color, HEX_BG, 0.72f), color, label);
}

#define BTN_FACE 22

static bool is_face(const char *b) {
    return !strcmp(b, "CROSS") || !strcmp(b, "CIRCLE") ||
           !strcmp(b, "SQUARE") || !strcmp(b, "TRIANGLE");
}

static bool is_dpad(const char *b) {
    return !strcmp(b, "DPAD") || !strcmp(b, "UD") || !strcmp(b, "LR");
}

int gui_button_w(const char *b) {
    if (!b) return 0;
    if (is_face(b) || is_dpad(b)) return BTN_FACE;
    return gui_pill_w(20, GUI_F_SMALL, b);
}

int gui_button(int x, int cy, const char *b) {
    if (!b) return 0;
    if (is_face(b)) {
        int cx = x + BTN_FACE / 2;
        gui_circle(cx, cy, BTN_FACE / 2, 0x0B1118);
        if (!strcmp(b, "CROSS")) {
            uint32_t c = 0x7CB2E8;
            gui_line(cx - 5, cy - 5, cx + 5, cy + 5, 2, c);
            gui_line(cx - 5, cy + 5, cx + 5, cy - 5, 2, c);
        } else if (!strcmp(b, "CIRCLE")) {
            uint32_t c = 0xFF6B6B;
            gui_circle(cx, cy, 6, c);
            gui_circle(cx, cy, 4, 0x0B1118);
        } else if (!strcmp(b, "SQUARE")) {
            uint32_t c = 0xE88BCB;
            gui_rect(cx - 5, cy - 5, 11, 11, c);
            gui_rect(cx - 3, cy - 3, 7, 7, 0x0B1118);
        } else {
            gui_tri(cx, cy - 7, cx - 7, cy + 5, cx + 7, cy + 5, 0x3DDBA8);
            gui_tri(cx, cy - 3, cx - 4, cy + 3, cx + 4, cy + 3, 0x0B1118);
        }
        return BTN_FACE;
    }
    if (is_dpad(b)) {
        int cx = x + BTN_FACE / 2;
        bool ud = !strcmp(b, "UD"), lr = !strcmp(b, "LR"), all = !strcmp(b, "DPAD");
        gui_circle(cx, cy, BTN_FACE / 2, 0x0B1118);
        gui_rect(cx - 3, cy - 8, 6, 16, (ud || all) ? HEX_TEXT : HEX_LINE);
        gui_rect(cx - 8, cy - 3, 16, 6, (lr || all) ? HEX_TEXT : HEX_LINE);
        if (ud && !all) gui_rect(cx - 3, cy - 3, 6, 6, HEX_TEXT);
        return BTN_FACE;
    }
    return gui_pill(x, cy - 10, 20, GUI_F_SMALL, HEX_PANEL_HI, HEX_TEXT, b);
}

void gui_bar(int x, int y, int w, int h, float frac, uint32_t color) {
    gui_bar_ex(x, y, w, h, frac, HEX_BG, color);
}

void gui_bar_ex(int x, int y, int w, int h, float frac, uint32_t track, uint32_t color) {
    if (frac < 0.0f) frac = 0.0f;
    if (frac > 1.0f) frac = 1.0f;
    gui_rrect(x, y, w, h, h / 2, track);
    int fw = (int)((float)w * frac + 0.5f);
    if (fw > 0) {
        if (fw < h) fw = h;   /* keep the rounded cap visible */
        gui_rrect(x, y, fw, h, h / 2, color);
    }
}

void gui_scrollbar(int x, int y, int h, int first, int visible, int total) {
    if (total <= visible || total <= 0) return;
    gui_rrect(x, y, 4, h, 2, HEX_LINE);
    int th = h * visible / total;
    if (th < 24) th = 24;
    int max_first = total - visible;
    int ty = y + (max_first > 0 ? (h - th) * first / max_first : 0);
    gui_rrect(x, ty, 4, th, 2, HEX_ACCENT);
}

void gui_panel(int x, int y, int w, int h) {
    gui_rrect(x, y, w, h, 12, HEX_PANEL);
}

void gui_card(int x, int y, int w, int h, const char *title, uint32_t tone) {
    gui_rrect(x - 1, y - 1, w + 2, h + 2, 14, HEX_LINE);
    gui_rrect(x, y, w, h, 14, HEX_PANEL);
    if (title && title[0]) {
        gui_rrect(x + 18, y + 16, 5, 22, 2, tone);
        gui_text_mid(x + 32, y + 12, 30, GUI_F_TITLE, HEX_TEXT, GUI_LEFT, w - 50, title);
        gui_rect(x + 18, y + 52, w - 36, 1, HEX_LINE);
    }
}

void gui_banner(int x, int y, int w, int h, uint32_t tone, const char *text) {
    gui_rrect(x, y, w, h, 10, HEX_PANEL);
    gui_rrect(x, y, 6, h, 3, tone);
    if (text) gui_text_mid(x + 20, y, h, GUI_F_BODY, HEX_TEXT, GUI_LEFT, w - 32, text);
}

int gui_header(const char *section) {
    int y = GUI_HEADER_Y;
    int h = GUI_HEADER_H;
    gui_rect(0, 0, GUI_W, y + h, HEX_BG2);
    gui_rect(0, y + h, GUI_W, 1, HEX_LINE);

    int lx = GUI_MARGIN_X;
    int ly = y + (h - 26) / 2;
    gui_rrect(lx, ly, 26, 26, 7, HEX_ACCENT);
    gui_rrect(lx + 7, ly + 7, 12, 12, 3, HEX_BG2);

    int x = lx + 38;
    x += gui_text_mid(x, y, h, GUI_F_TITLE, HEX_TEXT, GUI_LEFT, 0, "GameSync");
    if (section && section[0]) {
        gui_circle(x + 14, y + h / 2, 3, HEX_ACCENT);
        x += 26;
        x += gui_text_mid(x, y, h, GUI_F_TITLE, HEX_ACCENT2, GUI_LEFT, 0, section);
    }
    return x;
}

void gui_header_status(bool online) {
    int y = GUI_HEADER_Y;
    int h = GUI_HEADER_H;
    const char *label = online ? "Online" : "Offline";
    uint32_t tone = online ? HEX_OK : HEX_ERR;
    int pw = gui_text_w(GUI_F_SMALL, label) + 40;
    int px = GUI_W - GUI_MARGIN_X - pw;
    int py = y + (h - 26) / 2;
    gui_rrect(px, py, pw, 26, 13, gui_mix(tone, HEX_BG2, 0.78f));
    gui_circle(px + 15, py + 13, 5, tone);
    gui_text_mid(px + 27, py, 26, GUI_F_SMALL, tone, GUI_LEFT, 0, label);
    gui_textf(px - 14, y + (h - gui_line_h(GUI_F_SMALL)) / 2, GUI_F_SMALL, HEX_DIM,
              GUI_RIGHT, "v%s", APP_VERSION);
}

int gui_tabs(int x, int y, int h, const char *const *labels, int count, int active) {
    int pad = 16;
    int total = 4;
    for (int i = 0; i < count; i++) total += gui_text_w(GUI_F_SMALL, labels[i]) + pad * 2;
    gui_rrect(x, y, total, h, h / 2, HEX_PANEL);
    int cx = x + 2;
    for (int i = 0; i < count; i++) {
        int w = gui_text_w(GUI_F_SMALL, labels[i]) + pad * 2;
        if (i == active) gui_rrect(cx, y + 2, w, h - 4, (h - 4) / 2, HEX_ACCENT);
        gui_text_mid(cx + pad, y, h, GUI_F_SMALL, i == active ? HEX_INK : HEX_DIM,
                     GUI_LEFT, 0, labels[i]);
        cx += w;
    }
    return x + total;
}

int gui_hints(int x, int cy, const GuiHint *hints, int count, int gap) {
    for (int i = 0; i < count; i++) {
        if (!hints[i].button) continue;
        x += gui_button(x, cy, hints[i].button) + 7;
        x += gui_text(x, cy - gui_line_h(GUI_F_SMALL) / 2, GUI_F_SMALL, HEX_DIM,
                      GUI_LEFT, hints[i].label);
        x += gap;
    }
    return x;
}

void gui_footer(const GuiHint *hints, int count) {
    int y = GUI_FOOTER_Y;
    gui_rect(0, y, GUI_W, GUI_H - y, HEX_BG2);
    gui_rect(0, y, GUI_W, 1, HEX_LINE);
    /* Tighten the spacing when there are many hints. */
    int natural = 0;
    for (int i = 0; i < count; i++)
        if (hints[i].button)
            natural += gui_button_w(hints[i].button) + 7 + gui_text_w(GUI_F_SMALL, hints[i].label);
    int avail = GUI_W - GUI_MARGIN_X * 2 - natural;
    int gap = count > 1 ? avail / (count - 1) : 0;
    if (gap > 28) gap = 28;
    if (gap < 10) gap = 10;
    gui_hints(GUI_MARGIN_X, y + GUI_FOOTER_H / 2, hints, count, gap);
}
