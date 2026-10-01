/*
 * PSP Save Sync - GU drawing kit (see gui.h)
 *
 * VRAM layout (2 MB): two 512x272 RGBA8888 frame buffers, one more for
 * the modal-background snapshot, and a depth buffer the GE wants
 * configured even though depth testing stays off.  Everything else
 * (font atlas, CLUT, display list) lives in main RAM.
 *
 * Shapes are untextured 2D primitives with 32-bit float vertices; text is
 * one tinted sprite per glyph sampled from the T8 font atlas.
 */

#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include <pspdisplay.h>
#include <pspge.h>
#include <pspgu.h>
#include <pspkernel.h>
#include <pspnet_apctl.h>

#include "common.h"
#include "gui.h"

#define BUF_W       512
#define FRAME_BYTES (BUF_W * GUI_H * 4)
#define FB0_OFF     0
#define FB1_OFF     FRAME_BYTES
#define SNAP_OFF    (FRAME_BYTES * 2)
#define DEPTH_OFF   (FRAME_BYTES * 3)

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

static unsigned int __attribute__((aligned(16))) g_list[96 * 1024];
static uint32_t __attribute__((aligned(16))) g_clut[256];

static uint32_t g_draw_off = FB0_OFF;
static bool     g_snap_valid = false;
static uint32_t g_ticks = 0;

/* Texture state cache, reset every frame */
enum { TEX_NONE, TEX_FONT, TEX_SNAP };
static int g_tex_on = -1;
static int g_tex_bound = TEX_NONE;

static int g_server_state = -1;
static int g_wifi_strength = -1;

typedef struct { uint32_t c; float x, y, z; } CV;
typedef struct { uint16_t u, v; uint32_t c; int16_t x, y, z; } TV;

#define CV_FMT (GU_COLOR_8888 | GU_VERTEX_32BITF | GU_TRANSFORM_2D)
#define TV_FMT (GU_TEXTURE_16BIT | GU_COLOR_8888 | GU_VERTEX_16BIT | GU_TRANSFORM_2D)

static void *vram_abs(uint32_t off) {
    return (void *)((uintptr_t)sceGeEdramGetAddr() + off);
}

/* ================================================================== */
/* Lifetime and frames                                                 */
/* ================================================================== */

void gui_init(void) {
    for (int i = 0; i < 256; i++)
        g_clut[i] = ((uint32_t)i << 24) | 0x00FFFFFF;
    sceKernelDcacheWritebackAll();

    sceGuInit();
    sceGuStart(GU_DIRECT, g_list);
    sceGuDrawBuffer(GU_PSM_8888, (void *)FB0_OFF, BUF_W);
    sceGuDispBuffer(GUI_W, GUI_H, (void *)FB1_OFF, BUF_W);
    sceGuDepthBuffer((void *)DEPTH_OFF, BUF_W);
    sceGuOffset(2048 - GUI_W / 2, 2048 - GUI_H / 2);
    sceGuViewport(2048, 2048, GUI_W, GUI_H);
    sceGuScissor(0, 0, GUI_W, GUI_H);
    sceGuEnable(GU_SCISSOR_TEST);
    sceGuDisable(GU_DEPTH_TEST);
    sceGuDisable(GU_CULL_FACE);
    sceGuShadeModel(GU_SMOOTH);
    sceGuEnable(GU_BLEND);
    sceGuBlendFunc(GU_ADD, GU_SRC_ALPHA, GU_ONE_MINUS_SRC_ALPHA, 0, 0);
    sceGuClearColor(gui_rgb(HEX_BG));
    sceGuClear(GU_COLOR_BUFFER_BIT);
    sceGuFinish();
    sceGuSync(0, 0);
    sceDisplayWaitVblankStart();
    sceGuDisplay(GU_TRUE);
    g_draw_off = FB0_OFF;
}

void gui_term(void) {
    sceGuTerm();
}

void gui_begin(void) {
    sceGuStart(GU_DIRECT, g_list);
    g_tex_on = -1;
    g_tex_bound = TEX_NONE;
    sceGuScissor(0, 0, GUI_W, GUI_H);
    sceGuClearColor(gui_rgb(HEX_BG));
    sceGuClear(GU_COLOR_BUFFER_BIT);
}

void gui_end(bool vsync) {
    sceGuFinish();
    sceGuSync(0, 0);
    if (vsync) sceDisplayWaitVblankStart();
    sceGuSwapBuffers();
    g_draw_off = (g_draw_off == FB0_OFF) ? FB1_OFF : FB0_OFF;
    g_ticks++;
}

uint32_t gui_ticks(void) { return g_ticks; }

void gui_ease(float *value, float target, float speed) {
    float d = target - *value;
    if (fabsf(d) < 0.01f) *value = target;
    else *value += d * speed;
}

void gui_snapshot(void) {
    uint32_t shown = (g_draw_off == FB0_OFF) ? FB1_OFF : FB0_OFF;
    sceGuStart(GU_DIRECT, g_list);
    sceGuCopyImage(GU_PSM_8888, 0, 0, GUI_W, GUI_H, BUF_W, vram_abs(shown),
                   0, 0, BUF_W, vram_abs(SNAP_OFF));
    sceGuTexSync();
    sceGuFinish();
    sceGuSync(0, 0);
    g_snap_valid = true;
}

static void tex_off(void) {
    if (g_tex_on != 0) {
        sceGuDisable(GU_TEXTURE_2D);
        g_tex_on = 0;
    }
}

static void tex_on(void) {
    if (g_tex_on != 1) {
        sceGuEnable(GU_TEXTURE_2D);
        g_tex_on = 1;
    }
}

static void tex_font(void) {
    tex_on();
    if (g_tex_bound == TEX_FONT) return;
    sceGuClutMode(GU_PSM_8888, 0, 0xFF, 0);
    sceGuClutLoad(256 / 8, g_clut);
    sceGuTexMode(GU_PSM_T8, 0, 0, 0);
    sceGuTexImage(0, font_atlas_w, font_atlas_h, font_atlas_w, font_atlas);
    sceGuTexFunc(GU_TFX_MODULATE, GU_TCC_RGBA);
    sceGuTexFilter(GU_NEAREST, GU_NEAREST);
    sceGuTexWrap(GU_CLAMP, GU_CLAMP);
    g_tex_bound = TEX_FONT;
}

void gui_draw_snapshot(void) {
    if (!g_snap_valid) return;
    tex_on();
    sceGuTexMode(GU_PSM_8888, 0, 0, 0);
    sceGuTexImage(0, 512, 512, BUF_W, vram_abs(SNAP_OFF));
    sceGuTexFunc(GU_TFX_REPLACE, GU_TCC_RGB);
    sceGuTexFilter(GU_NEAREST, GU_NEAREST);
    sceGuTexWrap(GU_CLAMP, GU_CLAMP);
    g_tex_bound = TEX_SNAP;
    /* 64 px strips keep the texture cache happy */
    for (int x = 0; x < GUI_W; x += 64) {
        int w = (GUI_W - x < 64) ? GUI_W - x : 64;
        TV *v = (TV *)sceGuGetMemory(2 * sizeof(TV));
        v[0] = (TV){(uint16_t)x, 0, 0xFFFFFFFF, (int16_t)x, 0, 0};
        v[1] = (TV){(uint16_t)(x + w), GUI_H, 0xFFFFFFFF, (int16_t)(x + w), GUI_H, 0};
        sceGuDrawArray(GU_SPRITES, TV_FMT, 2, 0, v);
    }
}

void gui_clip(float x, float y, float w, float h) {
    sceGuScissor((int)x, (int)y, (int)w, (int)h);
}

void gui_unclip(void) {
    sceGuScissor(0, 0, GUI_W, GUI_H);
}

uint32_t gui_mix(uint32_t a, uint32_t b, float t) {
    int ar = (a >> 16) & 0xFF, ag = (a >> 8) & 0xFF, ab = a & 0xFF;
    int br = (b >> 16) & 0xFF, bg = (b >> 8) & 0xFF, bb = b & 0xFF;
    uint32_t r = (uint32_t)(ar + (br - ar) * t);
    uint32_t g = (uint32_t)(ag + (bg - ag) * t);
    uint32_t bl = (uint32_t)(ab + (bb - ab) * t);
    return gui_rgb((r << 16) | (g << 8) | bl);
}

/* ================================================================== */
/* Shapes                                                              */
/* ================================================================== */

static CV *cv_alloc(int n) {
    return (CV *)sceGuGetMemory(n * sizeof(CV));
}

static inline CV cv(uint32_t c, float x, float y) {
    return (CV){c, x, y, 0.0f};
}

void gui_rect(float x, float y, float w, float h, uint32_t color) {
    if (w <= 0 || h <= 0) return;
    tex_off();
    CV *v = cv_alloc(2);
    v[0] = cv(color, x, y);
    v[1] = cv(color, x + w, y + h);
    sceGuDrawArray(GU_SPRITES, CV_FMT, 2, 0, v);
}

void gui_vgrad(float x, float y, float w, float h, uint32_t top, uint32_t bottom) {
    if (w <= 0 || h <= 0) return;
    tex_off();
    CV *v = cv_alloc(4);
    v[0] = cv(top, x, y);
    v[1] = cv(top, x + w, y);
    v[2] = cv(bottom, x, y + h);
    v[3] = cv(bottom, x + w, y + h);
    sceGuDrawArray(GU_TRIANGLE_STRIP, CV_FMT, 4, 0, v);
}

void gui_rrect(float x, float y, float w, float h, float r, uint32_t color) {
    if (w <= 0 || h <= 0) return;
    if (r > w / 2) r = w / 2;
    if (r > h / 2) r = h / 2;
    if (r < 1) { gui_rect(x, y, w, h, color); return; }
    tex_off();
    int seg = r < 3 ? 2 : (r < 7 ? 4 : 6);
    int n = 1 + 4 * (seg + 1) + 1;
    CV *v = cv_alloc(n);
    int k = 0;
    v[k++] = cv(color, x + w / 2, y + h / 2);
    const float cxs[4] = {x + w - r, x + w - r, x + r, x + r};
    const float cys[4] = {y + r, y + h - r, y + h - r, y + r};
    for (int c = 0; c < 4; c++) {
        float a0 = -(float)M_PI / 2 + c * (float)M_PI / 2;
        for (int i = 0; i <= seg; i++) {
            float a = a0 + (float)M_PI / 2 * i / seg;
            v[k++] = cv(color, cxs[c] + cosf(a) * r, cys[c] + sinf(a) * r);
        }
    }
    v[k++] = v[1];
    sceGuDrawArray(GU_TRIANGLE_FAN, CV_FMT, k, 0, v);
}

static int circle_segs(float r) {
    return r < 4 ? 12 : (r < 10 ? 20 : 32);
}

void gui_circle(float cx, float cy, float r, uint32_t color) {
    tex_off();
    int segs = circle_segs(r);
    CV *v = cv_alloc(segs + 2);
    v[0] = cv(color, cx, cy);
    for (int i = 0; i <= segs; i++) {
        float a = 2 * (float)M_PI * i / segs;
        v[i + 1] = cv(color, cx + cosf(a) * r, cy + sinf(a) * r);
    }
    sceGuDrawArray(GU_TRIANGLE_FAN, CV_FMT, segs + 2, 0, v);
}

void gui_ring(float cx, float cy, float r, float thick, uint32_t color) {
    tex_off();
    int segs = circle_segs(r);
    CV *v = cv_alloc(2 * (segs + 1));
    float ri = r - thick;
    for (int i = 0; i <= segs; i++) {
        float a = 2 * (float)M_PI * i / segs;
        float c = cosf(a), s = sinf(a);
        v[2 * i]     = cv(color, cx + c * r, cy + s * r);
        v[2 * i + 1] = cv(color, cx + c * ri, cy + s * ri);
    }
    sceGuDrawArray(GU_TRIANGLE_STRIP, CV_FMT, 2 * (segs + 1), 0, v);
}

void gui_line(float x0, float y0, float x1, float y1, float thick, uint32_t color) {
    float dx = x1 - x0, dy = y1 - y0;
    float len = sqrtf(dx * dx + dy * dy);
    if (len <= 0) return;
    float nx = -dy / len * thick / 2, ny = dx / len * thick / 2;
    tex_off();
    CV *v = cv_alloc(4);
    v[0] = cv(color, x0 + nx, y0 + ny);
    v[1] = cv(color, x0 - nx, y0 - ny);
    v[2] = cv(color, x1 + nx, y1 + ny);
    v[3] = cv(color, x1 - nx, y1 - ny);
    sceGuDrawArray(GU_TRIANGLE_STRIP, CV_FMT, 4, 0, v);
}

void gui_tri(float x0, float y0, float x1, float y1, float x2, float y2, uint32_t color) {
    tex_off();
    CV *v = cv_alloc(3);
    v[0] = cv(color, x0, y0);
    v[1] = cv(color, x1, y1);
    v[2] = cv(color, x2, y2);
    sceGuDrawArray(GU_TRIANGLES, CV_FMT, 3, 0, v);
}

void gui_icon_check(float cx, float cy, float s, uint32_t color) {
    float t = s * 0.22f;
    gui_line(cx - s * 0.45f, cy, cx - s * 0.12f, cy + s * 0.32f, t, color);
    gui_line(cx - s * 0.16f, cy + s * 0.32f, cx + s * 0.45f, cy - s * 0.35f, t, color);
}

/* ================================================================== */
/* Text                                                                */
/* ================================================================== */

/* Next code point of a UTF-8 string; stray bytes are read as Latin-1. */
static uint32_t utf8_next(const char **ps) {
    const unsigned char *s = (const unsigned char *)*ps;
    uint32_t c = s[0];
    int n = 0;
    if (c >= 0xF0 && c < 0xF8) { c &= 0x07; n = 3; }
    else if (c >= 0xE0) { c &= 0x0F; n = 2; }
    else if (c >= 0xC0) { c &= 0x1F; n = 1; }
    for (int i = 1; i <= n; i++) {
        if ((s[i] & 0xC0) != 0x80) {   /* malformed: take the byte as-is */
            *ps += 1;
            return s[0];
        }
        c = (c << 6) | (s[i] & 0x3F);
    }
    *ps += 1 + n;
    return c;
}

static int glyph_index(uint32_t cp) {
    if (cp >= 32 && cp < 127) return (int)cp - 32;
    int lo = 95, hi = font_glyph_count - 1;
    while (lo <= hi) {
        int mid = (lo + hi) / 2;
        if (font_codepoints[mid] == cp) return mid;
        if (font_codepoints[mid] < cp) lo = mid + 1;
        else hi = mid - 1;
    }
    /* Common fallbacks: non-breaking space, unknown -> '?' */
    if (cp == 0xA0) return 0;
    return '?' - 32;
}

int gui_line_h(const FontFace *f) { return f->line_h; }

float gui_text_w(const FontFace *f, const char *s) {
    float w = 0;
    while (s && *s) {
        uint32_t cp = utf8_next(&s);
        if (cp == '\n') break;
        w += f->glyphs[glyph_index(cp)].adv;
    }
    return w;
}

/* Draw at most `max_bytes` bytes of s from a pen on the baseline. */
static void draw_run(int pen_x, int base_y, const FontFace *f, uint32_t color,
                     const char *s, size_t max_bytes) {
    const char *end = s + max_bytes;
    int count = 0;
    for (const char *p = s; p < end && *p;) {
        uint32_t cp = utf8_next(&p);
        if (f->glyphs[glyph_index(cp)].w) count++;
    }
    if (count == 0) return;
    tex_font();
    TV *v = (TV *)sceGuGetMemory(count * 2 * sizeof(TV));
    int k = 0;
    for (const char *p = s; p < end && *p;) {
        uint32_t cp = utf8_next(&p);
        const FontGlyph *g = &f->glyphs[glyph_index(cp)];
        if (g->w) {
            int x = pen_x + g->ox, y = base_y + g->oy;
            v[k++] = (TV){g->x, g->y, color, (int16_t)x, (int16_t)y, 0};
            v[k++] = (TV){(uint16_t)(g->x + g->w), (uint16_t)(g->y + g->h), color,
                          (int16_t)(x + g->w), (int16_t)(y + g->h), 0};
        }
        pen_x += g->adv;
    }
    sceGuDrawArray(GU_SPRITES, TV_FMT, k, 0, v);
}

static float align_x(float x, float w, GuiAlign align) {
    if (align == GUI_CENTER) return x - w / 2;
    if (align == GUI_RIGHT) return x - w;
    return x;
}

float gui_text(float x, float y, const FontFace *f, uint32_t color, GuiAlign align, const char *s) {
    return gui_text_fit(x, y, f, color, align, 0, s);
}

float gui_textf(float x, float y, const FontFace *f, uint32_t color, GuiAlign align, const char *fmt, ...) {
    char buf[256];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    return gui_text_fit(x, y, f, color, align, 0, buf);
}

float gui_text_fit(float x, float y, const FontFace *f, uint32_t color, GuiAlign align, float max_w, const char *s) {
    if (!s || !*s) return 0;
    size_t len = strcspn(s, "\n");
    float w = 0;
    for (const char *p = s; p < s + len;) {
        uint32_t cp = utf8_next(&p);
        w += f->glyphs[glyph_index(cp)].adv;
    }
    int base_y = (int)(y + 0.5f) + f->ascent;
    if (max_w <= 0 || w <= max_w) {
        draw_run((int)(align_x(x, w, align) + 0.5f), base_y, f, color, s, len);
        return w;
    }
    /* Cut at the last code point that leaves room for the ellipsis */
    const FontGlyph *ell = &f->glyphs[glyph_index(0x2026)];
    float room = max_w - ell->adv, acc = 0, cut_w = 0;
    const char *p = s, *cut = s;
    while (p < s + len) {
        const char *q = p;
        uint32_t cp = utf8_next(&q);
        float a = f->glyphs[glyph_index(cp)].adv;
        if (acc + a > room) break;
        acc += a;
        p = q;
        if (cp != ' ') { cut = p; cut_w = acc; }
    }
    float total = cut_w + ell->adv;
    int px = (int)(align_x(x, total, align) + 0.5f);
    draw_run(px, base_y, f, color, s, (size_t)(cut - s));
    draw_run(px + (int)cut_w, base_y, f, color, "\xE2\x80\xA6", 3);
    return total;
}

float gui_text_mid(float x, float y, float h, const FontFace *f, uint32_t color, GuiAlign align, float max_w, const char *s) {
    /* Centre the cap height rather than the full line box: looks right
     * for the mostly-capital labels in pills and buttons. */
    float top = y + (h - f->line_h) / 2.0f + 0.5f;
    return gui_text_fit(x, top, f, color, align, max_w, s);
}

int gui_wrap(const char *s, const FontFace *f, float max_w, char lines[][GUI_WRAP_LINE], int max_lines) {
    int n = 0;
    const char *p = s ? s : "";
    while (*p && n < max_lines) {
        /* Take as many words as fit on this line */
        const char *line_start = p, *last_break = NULL;
        float w = 0;
        const char *q = p;
        while (*q && *q != '\n') {
            const char *r = q;
            uint32_t cp = utf8_next(&r);
            float a = f->glyphs[glyph_index(cp)].adv;
            if (w + a > max_w && q > line_start) break;
            if (cp == ' ') last_break = q;
            w += a;
            q = r;
        }
        const char *end = q, *next = q;
        if (*q && *q != '\n' && last_break) {
            end = last_break;
            next = last_break + 1;
        } else if (*q == '\n') {
            next = q + 1;
        }
        size_t len = (size_t)(end - line_start);
        if (len >= GUI_WRAP_LINE) len = GUI_WRAP_LINE - 1;
        memcpy(lines[n], line_start, len);
        lines[n][len] = '\0';
        n++;
        p = next;
        if (n == max_lines && *p) {
            /* Text remains: mark the last line as cut */
            size_t l = strlen(lines[n - 1]);
            if (l + 4 < GUI_WRAP_LINE) {
                while (l > 0 && gui_text_w(f, lines[n - 1]) +
                       f->glyphs[glyph_index(0x2026)].adv > max_w) {
                    /* drop one code point */
                    do { l--; } while (l > 0 && ((unsigned char)lines[n - 1][l] & 0xC0) == 0x80);
                    lines[n - 1][l] = '\0';
                }
                memcpy(lines[n - 1] + l, "\xE2\x80\xA6", 4);
            }
        }
    }
    return n;
}

int gui_text_wrap(float x, float y, const FontFace *f, uint32_t color, float max_w, int max_lines, const char *s) {
    char lines[8][GUI_WRAP_LINE];
    if (max_lines > 8) max_lines = 8;
    int n = gui_wrap(s, f, max_w, lines, max_lines);
    for (int i = 0; i < n; i++)
        gui_text(x, y + i * f->line_h, f, color, GUI_LEFT, lines[i]);
    return n;
}

/* ================================================================== */
/* Widgets                                                             */
/* ================================================================== */

float gui_pill_w(float h, const FontFace *f, const char *label) {
    return gui_text_w(f, label) + h * 0.9f;
}

float gui_pill(float x, float y, float h, const FontFace *f, uint32_t bg, uint32_t fg, const char *label) {
    float w = gui_pill_w(h, f, label);
    gui_rrect(x, y, w, h, h / 2, bg);
    gui_text_mid(x + w / 2, y, h, f, fg, GUI_CENTER, 0, label);
    return w;
}

float gui_pill_outline(float x, float y, float h, const FontFace *f, uint32_t hex, const char *label) {
    float w = gui_pill_w(h, f, label);
    gui_rrect(x, y, w, h, h / 2, gui_rgba(hex, 0x40));
    gui_text_mid(x + w / 2, y, h, f, gui_mix(hex, HEX_TEXT, 0.3f), GUI_CENTER, 0, label);
    return w;
}

/* PlayStation face-button symbol colours */
#define HEX_BTN_CROSS    0x8DB4F2
#define HEX_BTN_CIRCLE   0xF27C7C
#define HEX_BTN_SQUARE   0xE79BD3
#define HEX_BTN_TRIANGLE 0x4DD3A8
#define HEX_BTN_BASE     0x2E3F53

float gui_button_w(const char *b) {
    if (!strcmp(b, "CROSS") || !strcmp(b, "CIRCLE") ||
        !strcmp(b, "SQUARE") || !strcmp(b, "TRIANGLE"))
        return 14;
    if (!strcmp(b, "DPAD") || !strcmp(b, "UD") || !strcmp(b, "LR")) return 13;
    if (!strcmp(b, "L") || !strcmp(b, "R")) return 18;
    if (!strcmp(b, "L/R")) return 39;
    return gui_text_w(F_SMALL, b) + 10;
}

float gui_button(float x, float cy, const char *b) {
    float r = 7;
    float cx = x + r;
    if (!strcmp(b, "CROSS")) {
        gui_circle(cx, cy, r, gui_rgb(HEX_BTN_BASE));
        uint32_t c = gui_rgb(HEX_BTN_CROSS);
        gui_line(cx - 3.2f, cy - 3.2f, cx + 3.2f, cy + 3.2f, 1.6f, c);
        gui_line(cx - 3.2f, cy + 3.2f, cx + 3.2f, cy - 3.2f, 1.6f, c);
        return 14;
    }
    if (!strcmp(b, "CIRCLE")) {
        gui_circle(cx, cy, r, gui_rgb(HEX_BTN_BASE));
        gui_ring(cx, cy, 3.9f, 1.5f, gui_rgb(HEX_BTN_CIRCLE));
        return 14;
    }
    if (!strcmp(b, "SQUARE")) {
        gui_circle(cx, cy, r, gui_rgb(HEX_BTN_BASE));
        uint32_t c = gui_rgb(HEX_BTN_SQUARE);
        float s = 3.3f, t = 1.4f;
        gui_rect(cx - s, cy - s, 2 * s, t, c);
        gui_rect(cx - s, cy + s - t, 2 * s, t, c);
        gui_rect(cx - s, cy - s, t, 2 * s, c);
        gui_rect(cx + s - t, cy - s, t, 2 * s, c);
        return 14;
    }
    if (!strcmp(b, "TRIANGLE")) {
        gui_circle(cx, cy, r, gui_rgb(HEX_BTN_BASE));
        uint32_t c = gui_rgb(HEX_BTN_TRIANGLE);
        float ty = cy - 3.6f, by = cy + 2.6f, hw = 3.8f;
        gui_line(cx, ty, cx - hw, by, 1.4f, c);
        gui_line(cx, ty, cx + hw, by, 1.4f, c);
        gui_line(cx - hw - 0.4f, by, cx + hw + 0.4f, by, 1.4f, c);
        return 14;
    }
    if (!strcmp(b, "DPAD") || !strcmp(b, "UD") || !strcmp(b, "LR")) {
        uint32_t base = gui_rgb(0x6F8295), hi = gui_rgb(HEX_TEXT);
        bool ud = !strcmp(b, "UD"), lr = !strcmp(b, "LR"), all = !ud && !lr;
        gui_rect(x + 4.5f, cy - 6.5f, 4, 13, (ud || all) ? hi : base);
        gui_rect(x, cy - 2, 13, 4, (lr || all) ? hi : base);
        if (lr) {
            gui_rect(x + 4.5f, cy - 6.5f, 4, 4.5f, base);
            gui_rect(x + 4.5f, cy + 2, 4, 4.5f, base);
        }
        if (ud) {
            gui_rect(x, cy - 2, 4.5f, 4, base);
            gui_rect(x + 8.5f, cy - 2, 4.5f, 4, base);
        }
        return 13;
    }
    if (!strcmp(b, "L") || !strcmp(b, "R")) {
        /* Shoulder button: tab rounded on the outer side */
        gui_rrect(x, cy - 6.5f, 18, 13, 4, gui_rgb(0xAAB8C5));
        gui_text_mid(x + 9, cy - 6.5f, 13, F_SMALL, gui_rgb(HEX_INK), GUI_CENTER, 0, b);
        return 18;
    }
    if (!strcmp(b, "L/R")) {
        gui_button(x, cy, "L");
        gui_button(x + 21, cy, "R");
        return 39;
    }
    /* START / SELECT: flat capsule like the PSP's own buttons */
    float w = gui_text_w(F_SMALL, b) + 10;
    gui_rrect(x, cy - 6.5f, w, 13, 6.5f, gui_rgb(0xAAB8C5));
    gui_text_mid(x + w / 2, cy - 6.5f, 13, F_SMALL, gui_rgb(HEX_INK), GUI_CENTER, 0, b);
    return w;
}

void gui_bar(float x, float y, float w, float h, float frac, uint32_t hex) {
    gui_rrect(x, y, w, h, h / 2, gui_rgb(HEX_BG2));
    if (frac < 0) {
        float seg = w * 0.3f;
        float t = (sinf(g_ticks * 0.06f) + 1) / 2;
        gui_rrect(x + (w - seg) * t, y, seg, h, h / 2, gui_rgb(hex));
        return;
    }
    if (frac > 1) frac = 1;
    float fw = w * frac;
    if (fw <= 0) return;
    if (fw < h) fw = h;
    gui_rrect(x, y, fw, h, h / 2, gui_rgb(hex));
    if (fw > h) gui_rect(x + h / 2, y + 1, fw - h, h * 0.35f, gui_rgba(0xFFFFFF, 0x30));
}

void gui_spinner(float cx, float cy, float r, uint32_t hex) {
    const int dots = 8;
    int head = (g_ticks / 4) % dots;
    for (int i = 0; i < dots; i++) {
        float a = (float)i / dots * 2 * (float)M_PI;
        int age = (head - i + dots) % dots;
        gui_circle(cx + cosf(a) * r, cy + sinf(a) * r, r * 0.24f,
                   gui_rgba(hex, (uint8_t)(255 - age * 28)));
    }
}

void gui_scrollbar(float x, float y, float h, int visible, int total, float smooth_first) {
    if (total <= visible || total <= 0) return;
    gui_rrect(x, y, 3, h, 1.5f, gui_rgb(HEX_BG2));
    float th = h * visible / total;
    if (th < 12) th = 12;
    float ty = y + (h - th) * (smooth_first / (float)(total - visible));
    if (ty < y) ty = y;
    if (ty > y + h - th) ty = y + h - th;
    gui_rrect(x, ty, 3, th, 1.5f, gui_rgb(HEX_DIM));
}

void gui_panel(float x, float y, float w, float h) {
    gui_rrect(x - 1, y - 1, w + 2, h + 2, 7, gui_rgb(0x0A1118));
    gui_rrect(x, y, w, h, 6, gui_rgb(HEX_PANEL));
}

void gui_card(float x, float y, float w, float h, const char *title, uint32_t tone) {
    gui_rrect(x + 2, y + 3, w, h, 7, gui_rgba(0x000000, 0x90));
    gui_rrect(x - 1, y - 1, w + 2, h + 2, 7, gui_mix(HEX_LINE, tone, 0.45f));
    gui_rrect(x, y, w, h, 6, gui_rgb(HEX_PANEL));
    if (title) {
        gui_rrect(x, y, w, 22, 6, gui_rgb(HEX_PANEL_HI));
        gui_rect(x, y + 12, w, 10, gui_rgb(HEX_PANEL_HI));
        gui_rect(x, y + 22, w, 1, gui_rgb(HEX_LINE));
        gui_rrect(x + 8, y + 7, 4, 9, 2, gui_rgb(tone));
        gui_text_mid(x + 18, y, 22, F_BOLD, gui_rgb(HEX_TEXT), GUI_LEFT, w - 26, title);
    }
}

void gui_dim(void) {
    gui_rect(0, 0, GUI_W, GUI_H, gui_rgba(0x05090D, 0xB4));
}

void gui_set_server_state(int state) {
    g_server_state = state;
}

static void status_icons(float x_right, float cy) {
    /* Server dot: green reachable, red offline, grey unknown */
    uint32_t dot = g_server_state > 0 ? HEX_OK : (g_server_state == 0 ? HEX_ERR : HEX_MUTED);
    gui_circle(x_right - 4, cy, 5, gui_rgba(dot, 0x50));
    gui_circle(x_right - 4, cy, 3.2f, gui_rgb(dot));

    /* WiFi bars, refreshed about once a second */
    if (g_server_state >= 0 && (g_ticks % 60) == 0) {
        union SceNetApctlInfo info;
        if (sceNetApctlGetInfo(PSP_NET_APCTL_INFO_STRENGTH, &info) == 0)
            g_wifi_strength = info.strength;
        else
            g_wifi_strength = -1;
    }
    int bars = g_wifi_strength < 0 ? 0 :
               g_wifi_strength > 66 ? 3 : g_wifi_strength > 33 ? 2 : 1;
    float bx = x_right - 26;
    for (int i = 0; i < 3; i++) {
        float bh = 4 + i * 3;
        gui_rect(bx + i * 5, cy + 5 - bh, 3, bh, gui_rgb(i < bars ? HEX_TEXT : HEX_MUTED));
    }
}

void gui_header(const char *section, int page, int pages) {
    gui_vgrad(0, 0, GUI_W, GUI_HEADER_H, gui_rgb(0x223041), gui_rgb(HEX_BG2));
    gui_rect(0, GUI_HEADER_H - 1, GUI_W, 1, gui_rgb(HEX_LINE));

    /* Logo mark: teal rounded square with a notch */
    gui_rrect(8, 5, 14, 14, 4, gui_rgb(HEX_ACCENT));
    gui_rrect(12, 9, 6, 6, 2, gui_rgb(HEX_BG2));
    float x = 28;
    x += gui_text_mid(x, 0, GUI_HEADER_H, F_BOLD, gui_rgb(HEX_TEXT), GUI_LEFT, 0, "GameSync");
    if (section && *section) {
        gui_circle(x + 7, GUI_HEADER_H / 2.0f, 1.6f, gui_rgb(HEX_MUTED));
        x += 14;
        x += gui_text_mid(x, 0, GUI_HEADER_H, F_BOLD, gui_rgb(HEX_ACCENT2), GUI_LEFT, 200, section);
    }

    float right = GUI_W - 8;
    status_icons(right, GUI_HEADER_H / 2.0f);
    right -= 34;
    right -= gui_text_mid(right, 0, GUI_HEADER_H, F_SMALL, gui_rgb(HEX_DIM), GUI_RIGHT, 0, "v" APP_VERSION);

    /* Pager dots for the START view cycle */
    if (pages > 0) {
        float px = right - 12 - (pages - 1) * 10;
        for (int i = 0; i < pages; i++) {
            if (i == page)
                gui_rrect(px + i * 10 - 5, GUI_HEADER_H / 2.0f - 2.5f, 10, 5, 2.5f, gui_rgb(HEX_ACCENT));
            else
                gui_circle(px + i * 10, GUI_HEADER_H / 2.0f, 2.2f, gui_rgb(HEX_MUTED));
        }
    }
}

void gui_footer(const GuiHint *hints, int count) {
    float y = GUI_FOOTER_Y;
    gui_rect(0, y, GUI_W, GUI_FOOTER_H, gui_rgb(HEX_BG2));
    gui_rect(0, y, GUI_W, 1, gui_rgb(HEX_LINE));
    if (count <= 0) return;
    float widths[12], total = 0;
    if (count > 12) count = 12;
    for (int i = 0; i < count; i++) {
        widths[i] = gui_button_w(hints[i].button) + 4 + gui_text_w(F_SMALL, hints[i].label);
        total += widths[i];
    }
    float gap = (GUI_W - 16 - total) / (count > 1 ? count - 1 : 1);
    if (gap > 16) gap = 16;
    if (gap < 4) gap = 4;
    float x = 8, cy = y + GUI_FOOTER_H / 2.0f + 0.5f;
    for (int i = 0; i < count; i++) {
        float bw = gui_button(x, cy, hints[i].button);
        gui_text_mid(x + bw + 4, y, GUI_FOOTER_H, F_SMALL, gui_rgb(HEX_DIM), GUI_LEFT, 0, hints[i].label);
        x += widths[i] + gap;
    }
}

float gui_tabs(float x, float y, float h, const char *const *labels, int count, int active) {
    float pad = 8, total = 0;
    for (int i = 0; i < count; i++) total += gui_text_w(F_SMALL, labels[i]) + pad * 2;
    gui_rrect(x, y, total + 4, h, h / 2, gui_rgb(HEX_BG));
    float cx = x + 2;
    for (int i = 0; i < count; i++) {
        float w = gui_text_w(F_SMALL, labels[i]) + pad * 2;
        bool on = (i == active);
        if (on) gui_rrect(cx, y + 2, w, h - 4, (h - 4) / 2, gui_rgb(HEX_ACCENT));
        gui_text_mid(cx + w / 2, y, h, F_SMALL, gui_rgb(on ? HEX_INK : HEX_DIM), GUI_CENTER, 0, labels[i]);
        cx += w;
    }
    return x + total + 4;
}
