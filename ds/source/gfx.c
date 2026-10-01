#include "gfx.h"
#include <string.h>

// ---------------------------------------------------------------------------
// Surfaces and clipping
// ---------------------------------------------------------------------------

void gfx_surface_init(Surface *s, Color *px, int w, int h) {
    s->px = px;
    s->w = w;
    s->h = h;
    gfx_unclip(s);
}

void gfx_unclip(Surface *s) {
    s->cx0 = 0;
    s->cy0 = 0;
    s->cx1 = s->w;
    s->cy1 = s->h;
}

void gfx_clip(Surface *s, int x, int y, int w, int h) {
    s->cx0 = x < 0 ? 0 : x;
    s->cy0 = y < 0 ? 0 : y;
    s->cx1 = x + w > s->w ? s->w : x + w;
    s->cy1 = y + h > s->h ? s->h : y + h;
}

// Clip a rectangle; false if nothing is left
static bool clip_rect(const Surface *s, int *x, int *y, int *w, int *h) {
    int x0 = *x, y0 = *y, x1 = *x + *w, y1 = *y + *h;
    if (x0 < s->cx0) x0 = s->cx0;
    if (y0 < s->cy0) y0 = s->cy0;
    if (x1 > s->cx1) x1 = s->cx1;
    if (y1 > s->cy1) y1 = s->cy1;
    if (x1 <= x0 || y1 <= y0) return false;
    *x = x0;
    *y = y0;
    *w = x1 - x0;
    *h = y1 - y0;
    return true;
}

static inline bool in_clip(const Surface *s, int x, int y) {
    return x >= s->cx0 && x < s->cx1 && y >= s->cy0 && y < s->cy1;
}

// ---------------------------------------------------------------------------
// Colour
// ---------------------------------------------------------------------------

Color gfx_blend(Color under, Color over, int alpha) {
    if (alpha >= 256) return over;
    if (alpha <= 0) return under;
    int ur = under & 31, ug = (under >> 5) & 31, ub = (under >> 10) & 31;
    int or_ = over & 31, og = (over >> 5) & 31, ob = (over >> 10) & 31;
    int r = ur + (((or_ - ur) * alpha) >> 8);
    int g = ug + (((og - ug) * alpha) >> 8);
    int b = ub + (((ob - ub) * alpha) >> 8);
    return (Color)(0x8000 | (b << 10) | (g << 5) | r);
}

Color gfx_mix(Color a, Color b, int alpha) {
    return gfx_blend(a, b, alpha);
}

// ---------------------------------------------------------------------------
// Rectangles
// ---------------------------------------------------------------------------

void gfx_fill(Surface *s, int x, int y, int w, int h, Color c) {
    if (!clip_rect(s, &x, &y, &w, &h)) return;
    uint32_t pair = (uint32_t)c | ((uint32_t)c << 16);
    for (int row = 0; row < h; row++) {
        Color *p = s->px + (y + row) * s->w + x;
        int n = w;
        if (((uintptr_t)p & 2) && n > 0) {
            *p++ = c;
            n--;
        }
        uint32_t *p32 = (uint32_t *)p;
        for (; n >= 2; n -= 2) *p32++ = pair;
        if (n) *(Color *)p32 = c;
    }
}

void gfx_fill_alpha(Surface *s, int x, int y, int w, int h, Color c, int alpha) {
    if (alpha >= 256) {
        gfx_fill(s, x, y, w, h, c);
        return;
    }
    if (alpha <= 0 || !clip_rect(s, &x, &y, &w, &h)) return;
    for (int row = 0; row < h; row++) {
        Color *p = s->px + (y + row) * s->w + x;
        for (int i = 0; i < w; i++) p[i] = gfx_blend(p[i], c, alpha);
    }
}

void gfx_hline(Surface *s, int x, int y, int w, Color c) {
    gfx_fill(s, x, y, w, 1, c);
}

void gfx_vline(Surface *s, int x, int y, int h, Color c) {
    gfx_fill(s, x, y, 1, h, c);
}

static const uint8_t bayer4[4][4] = {
    { 0, 8, 2, 10 },
    { 12, 4, 14, 6 },
    { 3, 11, 1, 9 },
    { 15, 7, 13, 5 },
};

static inline int dither5(int v8, int t) {
    int v = (v8 * 2 + t) >> 4;
    return v > 31 ? 31 : v;
}

void gfx_vgradient(Surface *s, int x, int y, int w, int h, uint32_t top, uint32_t bottom) {
    int x0 = x, y0 = y, cw = w, ch = h;
    if (h <= 0 || !clip_rect(s, &x0, &y0, &cw, &ch)) return;
    int tr = (top >> 16) & 0xFF, tg = (top >> 8) & 0xFF, tb = top & 0xFF;
    int br = (bottom >> 16) & 0xFF, bg = (bottom >> 8) & 0xFF, bb = bottom & 0xFF;
    int span = h > 1 ? h - 1 : 1;
    for (int row = y0; row < y0 + ch; row++) {
        int k = row - y;
        int r = tr + (br - tr) * k / span;
        int g = tg + (bg - tg) * k / span;
        int b = tb + (bb - tb) * k / span;
        Color *p = s->px + row * s->w;
        const uint8_t *th = bayer4[row & 3];
        Color pat[4];
        for (int i = 0; i < 4; i++) {
            int t = th[i];
            pat[i] = (Color)(0x8000 | (dither5(b, t) << 10) | (dither5(g, t) << 5) | dither5(r, t));
        }
        for (int col = x0; col < x0 + cw; col++) p[col] = pat[col & 3];
    }
}

// Coverage (0..16) of pixel (i, j) in the top-left corner square of a
// rounded rectangle with radius r, sampled 4x4
static int corner_coverage(int i, int j, int r) {
    int c8 = r * 8, r2 = c8 * c8, n = 0;
    for (int sy = 0; sy < 4; sy++) {
        int dy = j * 8 + sy * 2 + 1 - c8;
        for (int sx = 0; sx < 4; sx++) {
            int dx = i * 8 + sx * 2 + 1 - c8;
            if (dx * dx + dy * dy <= r2) n++;
        }
    }
    return n;
}

static inline void plot(Surface *s, int x, int y, Color c, int alpha) {
    if (!in_clip(s, x, y) || alpha <= 0) return;
    Color *p = s->px + y * s->w + x;
    *p = gfx_blend(*p, c, alpha);
}

#define MAX_RADIUS 16

void gfx_round_rect(Surface *s, int x, int y, int w, int h, int r, Color c, int alpha) {
    if (w <= 0 || h <= 0) return;
    if (r > w / 2) r = w / 2;
    if (r > h / 2) r = h / 2;
    if (r > MAX_RADIUS) r = MAX_RADIUS;
    if (r <= 0) {
        gfx_fill_alpha(s, x, y, w, h, c, alpha);
        return;
    }
    // Straight parts
    gfx_fill_alpha(s, x + r, y, w - 2 * r, r, c, alpha);
    gfx_fill_alpha(s, x, y + r, w, h - 2 * r, c, alpha);
    gfx_fill_alpha(s, x + r, y + h - r, w - 2 * r, r, c, alpha);
    // Corners
    for (int j = 0; j < r; j++) {
        for (int i = 0; i < r; i++) {
            int cov = corner_coverage(i, j, r);
            if (!cov) continue;
            int a = alpha * cov / 16;
            plot(s, x + i, y + j, c, a);
            plot(s, x + w - 1 - i, y + j, c, a);
            plot(s, x + i, y + h - 1 - j, c, a);
            plot(s, x + w - 1 - i, y + h - 1 - j, c, a);
        }
    }
}

void gfx_round_frame(Surface *s, int x, int y, int w, int h, int r, Color border, Color fill) {
    gfx_round_rect(s, x, y, w, h, r, border, 256);
    gfx_round_rect(s, x + 1, y + 1, w - 2, h - 2, r > 1 ? r - 1 : 0, fill, 256);
}

void gfx_circle(Surface *s, int cx, int cy, int r, Color c) {
    gfx_round_rect(s, cx - r, cy - r, 2 * r, 2 * r, r, c, 256);
}

// ---------------------------------------------------------------------------
// Icons
// ---------------------------------------------------------------------------

void gfx_icon(Surface *s, const Icon *icon, int x, int y, Color c) {
    gfx_icon_scaled(s, icon, x, y, 1, c);
}

void gfx_icon_scaled(Surface *s, const Icon *icon, int x, int y, int scale, Color c) {
    for (int j = 0; j < icon->h; j++) {
        uint16_t bits = icon->rows[j];
        for (int i = 0; bits && i < icon->w; i++, bits <<= 1) {
            if (!(bits & 0x8000)) continue;
            if (scale == 1) {
                if (in_clip(s, x + i, y + j)) s->px[(y + j) * s->w + x + i] = c;
            } else {
                gfx_fill(s, x + i * scale, y + j * scale, scale, scale, c);
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Text
// ---------------------------------------------------------------------------

// Latin-1 letters U+00C0..U+00FF folded to ASCII, so "Pokémon" reads right
static const char latin1_fold[65] =
    "AAAAAAACEEEEIIII"
    "DNOOOOOxOUUUUYPs"
    "aaaaaaaceeeeiiii"
    "dnooooo/ouuuuypy";

// Next character as a font code (ASCII), advancing *p over UTF-8
int gfx_next_char(const char **p) {
    const unsigned char *s = (const unsigned char *)*p;
    unsigned char c = s[0];
    if (c < 0x80) {
        *p += 1;
        return c;
    }
    int len = (c >= 0xF0) ? 4 : (c >= 0xE0) ? 3 : (c >= 0xC0) ? 2 : 1;
    for (int i = 1; i < len; i++) {
        if ((s[i] & 0xC0) != 0x80) {  // broken sequence: one byte
            *p += 1;
            return '?';
        }
    }
    *p += len;
    if (len == 2) {
        unsigned cp = ((c & 0x1F) << 6) | (s[1] & 0x3F);
        if (cp >= 0xC0 && cp <= 0xFF) return latin1_fold[cp - 0xC0];
        if (cp == 0xA0) return ' ';
    } else if (len == 3) {
        unsigned cp = ((c & 0x0F) << 12) | ((s[1] & 0x3F) << 6) | (s[2] & 0x3F);
        if (cp == 0x2019 || cp == 0x2018) return '\'';
        if (cp == 0x201C || cp == 0x201D) return '"';
        if (cp == 0x2013 || cp == 0x2014) return '-';
    }
    return '?';
}

static inline int glyph_index(const Font *f, int ch) {
    if (ch < f->first || ch >= f->first + f->count) ch = '?';
    return ch - f->first;
}

static inline int char_adv(const Font *f, int ch) {
    return f->adv[glyph_index(f, ch)];
}

static void draw_glyph(Surface *s, const Font *f, int x, int y, int ch, Color c) {
    const uint16_t *rows = f->rows + glyph_index(f, ch) * f->height;
    for (int j = 0; j < f->height; j++) {
        uint16_t bits = rows[j];
        int py = y + j;
        if (!bits || py < s->cy0 || py >= s->cy1) continue;
        Color *line = s->px + py * s->w;
        for (int i = 0; bits; i++, bits <<= 1) {
            if ((bits & 0x8000) && x + i >= s->cx0 && x + i < s->cx1) line[x + i] = c;
        }
    }
}

int gfx_text_width_n(const Font *f, const char *text, int n) {
    int w = 0;
    const char *p = text;
    while (*p && (n < 0 || p - text < n)) w += char_adv(f, gfx_next_char(&p));
    return w;
}

int gfx_text_width(const Font *f, const char *text) {
    return gfx_text_width_n(f, text, -1);
}

int gfx_text_n(Surface *s, const Font *f, int x, int y, Color c, const char *text, int n) {
    const char *p = text;
    while (*p && (n < 0 || p - text < n)) {
        int ch = gfx_next_char(&p);
        if (ch != ' ') draw_glyph(s, f, x, y, ch, c);
        x += char_adv(f, ch);
    }
    return x;
}

int gfx_text(Surface *s, const Font *f, int x, int y, Color c, const char *text) {
    return gfx_text_n(s, f, x, y, c, text, -1);
}

int gfx_text_scaled(Surface *s, const Font *f, int x, int y, int scale, Color c, const char *text) {
    const char *p = text;
    while (*p) {
        int ch = gfx_next_char(&p);
        Icon glyph = { 16, f->height, f->rows + glyph_index(f, ch) * f->height };
        gfx_icon_scaled(s, &glyph, x, y, scale, c);
        x += char_adv(f, ch) * scale;
    }
    return x;
}

// "..." with the dots one pixel apart: narrower than three '.' glyphs
static int ellipsis_width(const Font *f) {
    return f == &font_mono ? 5 : 7;
}

static void draw_ellipsis(Surface *s, const Font *f, int x, int y, Color c) {
    int by = y + f->baseline - 1;
    if (f == &font_mono) {
        for (int i = 0; i < 3; i++) gfx_fill(s, x + i * 2, by, 1, 1, c);
    } else {
        int d = (f == &font_bold) ? 2 : 1;
        for (int i = 0; i < 3; i++) gfx_fill(s, x + i * (d + 1), by - d + 1, d, d, c);
    }
}

int gfx_text_fit(Surface *s, const Font *f, int x, int y, int max_w, Color c, const char *text) {
    if (gfx_text_width(f, text) <= max_w) return gfx_text(s, f, x, y, c, text);
    int room = max_w - ellipsis_width(f), w = 0;
    const char *p = text, *end = text;
    while (*p) {
        const char *q = p;
        int adv = char_adv(f, gfx_next_char(&q));
        if (w + adv > room) break;
        w += adv;
        p = q;
        end = p;
    }
    // Don't leave a space before the dots
    int n = (int)(end - text);
    while (n > 0 && text[n - 1] == ' ') {
        n--;
        w -= char_adv(f, ' ');
    }
    gfx_text_n(s, f, x, y, c, text, n);
    draw_ellipsis(s, f, x + w + 1, y, c);
    return x + w + ellipsis_width(f);
}

int gfx_text_fit_left(Surface *s, const Font *f, int x, int y, int max_w, Color c, const char *text) {
    int total = gfx_text_width(f, text);
    if (total <= max_w) return gfx_text(s, f, x, y, c, text);
    int room = max_w - ellipsis_width(f);
    const char *p = text;
    while (*p && total > room) {
        total -= char_adv(f, gfx_next_char(&p));
    }
    draw_ellipsis(s, f, x, y, c);
    return gfx_text(s, f, x + ellipsis_width(f), y, c, p);
}

void gfx_text_right(Surface *s, const Font *f, int right_x, int y, Color c, const char *text) {
    gfx_text(s, f, right_x - gfx_text_width(f, text), y, c, text);
}

void gfx_text_center(Surface *s, const Font *f, int cx, int y, Color c, const char *text) {
    gfx_text(s, f, cx - gfx_text_width(f, text) / 2, y, c, text);
}

int gfx_text_wrap(Surface *s, const Font *f, int x, int y, int max_w, int line_h, int max_lines,
                  Color c, const char *text) {
    const char *p = text;
    int lines = 0;
    while (*p && lines < max_lines) {
        // Longest run that fits, preferring to break after a space
        const char *q = p, *brk = NULL, *fit = p;
        int w = 0;
        while (*q && *q != '\n') {
            const char *n = q;
            int adv = char_adv(f, gfx_next_char(&n));
            if (w + adv > max_w) break;
            w += adv;
            if (*q == ' ') brk = q;
            q = n;
            fit = q;
        }
        const char *end;
        if (!*q || *q == '\n') end = q;
        else if (brk && brk > p) end = brk;
        else end = fit > p ? fit : q + 1;  // at least one character

        bool last = (lines == max_lines - 1);
        bool more = *end && !(*end == '\n' && !end[1]);
        if (s) {
            if (last && more) {
                // Out of lines: ellipsise what is left of this line
                char buf[160];
                size_t len = strcspn(p, "\n");
                if (len >= sizeof(buf)) len = sizeof(buf) - 1;
                memcpy(buf, p, len);
                buf[len] = '\0';
                if ((size_t)(end - p) == len) {
                    // This line itself fits: add the dots after it
                    int ew = ellipsis_width(f);
                    int lw = gfx_text_width_n(f, p, (int)(end - p));
                    if (lw + ew + 1 <= max_w) {
                        gfx_text_n(s, f, x, y + lines * line_h, c, p, (int)(end - p));
                        draw_ellipsis(s, f, x + lw + 1, y + lines * line_h, c);
                    } else {
                        gfx_text_fit(s, f, x, y + lines * line_h, max_w, c, buf);
                    }
                } else {
                    gfx_text_fit(s, f, x, y + lines * line_h, max_w, c, buf);
                }
            } else {
                gfx_text_n(s, f, x, y + lines * line_h, c, p, (int)(end - p));
            }
        }
        lines++;
        p = end;
        if (*p == ' ' || *p == '\n') p++;
    }
    return lines ? lines : 1;
}
