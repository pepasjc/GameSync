/*
 * GameSync Vita client - drawing kit on top of vita2d.
 *
 * Everything here is plain geometry built from vita2d_draw_array triangle
 * fans plus the console's own system font (PGF, PVF fallback), so the
 * client ships no font or image assets.  Vertex data lives in vita2d's
 * per-frame pool, which is reset at the start of every frame.
 */

#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include <psp2/sysmodule.h>
#include <vita2d.h>

#include "common.h"
#include "gui.h"

#define POOL_SIZE (2 * 1024 * 1024)

/* Line height, in pixels, of text at scale 1.0 (GUI_S_BODY) when the font
 * has to be scaled (PVF); PGF is drawn at its native size so it stays sharp. */
#define FONT_LINE_PX 20.0f
/* Baseline position as a fraction of the line height */
#define FONT_ASCENT  0.78f

/* The system font: PGF (what VitaShell and most homebrew use), or the
 * scalable PVF if PGF won't load.  Only one of the two is ever set. */
static vita2d_pvf *g_pvf;
static vita2d_pgf *g_pgf;
static float g_k = 1.0f;            /* vita2d scale for our scale 1.0 */
static float g_line = FONT_LINE_PX; /* line height at scale 1.0 */
static float g_ascii_w[128];        /* advance at scale 1.0, in pixels */
static uint32_t g_ticks;
static const char *g_ellipsis = "...";

static int font_draw(int x, int y, unsigned int color, float scale, const char *s) {
    if (g_pvf) return vita2d_pvf_draw_text(g_pvf, x, y, color, scale, s);
    if (g_pgf) return vita2d_pgf_draw_text(g_pgf, x, y, color, scale, s);
    return 0;
}

static int font_width(float scale, const char *s) {
    if (g_pvf) return vita2d_pvf_text_width(g_pvf, scale, s);
    if (g_pgf) return vita2d_pgf_text_width(g_pgf, scale, s);
    return 0;
}

static int font_height(float scale, const char *s) {
    if (g_pvf) return vita2d_pvf_text_height(g_pvf, scale, s);
    if (g_pgf) return vita2d_pgf_text_height(g_pgf, scale, s);
    return 0;
}

uint32_t gui_mix(uint32_t a, uint32_t b, float t) {
    if (t < 0) t = 0;
    if (t > 1) t = 1;
    uint32_t r  = (uint32_t)(((a >> 16) & 0xFF) * (1 - t) + ((b >> 16) & 0xFF) * t);
    uint32_t g  = (uint32_t)(((a >> 8) & 0xFF) * (1 - t) + ((b >> 8) & 0xFF) * t);
    uint32_t bl = (uint32_t)((a & 0xFF) * (1 - t) + (b & 0xFF) * t);
    return (r << 16) | (g << 8) | bl;
}

/* ------------------------------------------------------------------ */
/* Lifetime and frames                                                 */
/* ------------------------------------------------------------------ */

void gui_init(void) {
    vita2d_init_advanced(POOL_SIZE);
    vita2d_set_clear_color(gui_rgb(HEX_BG));
    sceSysmoduleLoadModule(SCE_SYSMODULE_PGF);
    g_pgf = vita2d_load_default_pgf();
    if (!g_pgf) g_pvf = vita2d_load_default_pvf();

    /* PGF glyphs are bitmaps: draw them at native size (scaling blurs them
     * and bleeds neighbouring atlas glyphs in).  PVF is normalised to a
     * FONT_LINE_PX line.  Then cache ASCII advances - ten copies of a glyph
     * at once, because vita2d rounds the pen to whole pixels. */
    int h = font_height(1.0f, "A");
    if (h > 0) {
        if (g_pgf) g_line = (float)h;
        else g_k = FONT_LINE_PX / (float)h;
    }
    char run[11];
    run[10] = '\0';
    for (int c = 32; c < 127; c++) {
        memset(run, c, 10);
        g_ascii_w[c] = (float)font_width(g_k, run) / 10.0f;
    }
}

void gui_exit(void) {
    vita2d_wait_rendering_done();
    if (g_pvf) vita2d_free_pvf(g_pvf);
    if (g_pgf) vita2d_free_pgf(g_pgf);
    g_pvf = NULL;
    g_pgf = NULL;
    vita2d_fini();
}

void gui_begin(bool vsync) {
    vita2d_set_vblank_wait(vsync ? 1 : 0);
    vita2d_start_drawing();
    vita2d_clear_screen();
    g_ticks++;
    gui_vgrad(0, 0, GUI_W, GUI_H, gui_rgb(0x131D29), gui_rgb(HEX_BG));
}

void gui_end(void) {
    vita2d_end_drawing();
    vita2d_swap_buffers();
}

uint32_t gui_ticks(void) { return g_ticks; }

void gui_ease(float *value, float target, float speed) {
    float d = target - *value;
    if (fabsf(d) < 0.02f) *value = target;
    else *value += d * speed;
}

void gui_clip(float x, float y, float w, float h) {
    vita2d_enable_clipping();
    vita2d_set_clip_rectangle((int)x, (int)y, (int)(x + w), (int)(y + h));
}

void gui_unclip(void) {
    vita2d_disable_clipping();
}

/* ------------------------------------------------------------------ */
/* Shapes                                                              */
/* ------------------------------------------------------------------ */

static vita2d_color_vertex *verts(int n) {
    return (vita2d_color_vertex *)vita2d_pool_memalign(
        (unsigned int)(n * sizeof(vita2d_color_vertex)), sizeof(vita2d_color_vertex));
}

static void vset(vita2d_color_vertex *v, float x, float y, unsigned int color) {
    v->x = x;
    v->y = y;
    v->z = 0.5f;
    v->color = color;
}

void gui_rect(float x, float y, float w, float h, unsigned int color) {
    if (w <= 0 || h <= 0) return;
    vita2d_draw_rectangle(x, y, w, h, color);
}

void gui_vgrad(float x, float y, float w, float h, unsigned int top, unsigned int bottom) {
    vita2d_color_vertex *v = verts(4);
    if (!v) return;
    vset(&v[0], x, y, top);
    vset(&v[1], x + w, y, top);
    vset(&v[2], x, y + h, bottom);
    vset(&v[3], x + w, y + h, bottom);
    vita2d_draw_array(SCE_GXM_PRIMITIVE_TRIANGLE_STRIP, v, 4);
}

static int arc_segments(float r) {
    int n = (int)(r * 0.6f) + 3;
    if (n > 12) n = 12;
    return n;
}

void gui_rrect(float x, float y, float w, float h, float r, unsigned int color) {
    if (w <= 0 || h <= 0) return;
    if (r * 2 > h) r = h / 2;
    if (r * 2 > w) r = w / 2;
    if (r < 1) {
        gui_rect(x, y, w, h, color);
        return;
    }
    /* One triangle fan: centre, then the four corner arcs */
    int seg = arc_segments(r);
    int n = 1 + 4 * (seg + 1) + 1;
    vita2d_color_vertex *v = verts(n);
    if (!v) return;
    vset(&v[0], x + w / 2, y + h / 2, color);
    const float cx[4] = { x + w - r, x + r, x + r, x + w - r };
    const float cy[4] = { y + h - r, y + h - r, y + r, y + r };
    int k = 1;
    for (int c = 0; c < 4; c++) {
        float a0 = c * (float)M_PI / 2;
        for (int i = 0; i <= seg; i++) {
            float a = a0 + (float)M_PI / 2 * i / seg;
            vset(&v[k++], cx[c] + cosf(a) * r, cy[c] + sinf(a) * r, color);
        }
    }
    v[k] = v[1];
    vita2d_draw_array(SCE_GXM_PRIMITIVE_TRIANGLE_FAN, v, n);
}

void gui_circle(float cx, float cy, float r, unsigned int color) {
    int seg = (int)(r * 1.2f) + 8;
    if (seg > 48) seg = 48;
    int n = seg + 2;
    vita2d_color_vertex *v = verts(n);
    if (!v) return;
    vset(&v[0], cx, cy, color);
    for (int i = 0; i <= seg; i++) {
        float a = 2 * (float)M_PI * i / seg;
        vset(&v[i + 1], cx + cosf(a) * r, cy + sinf(a) * r, color);
    }
    vita2d_draw_array(SCE_GXM_PRIMITIVE_TRIANGLE_FAN, v, n);
}

void gui_triangle(float x0, float y0, float x1, float y1, float x2, float y2,
                  unsigned int color) {
    vita2d_color_vertex *v = verts(3);
    if (!v) return;
    vset(&v[0], x0, y0, color);
    vset(&v[1], x1, y1, color);
    vset(&v[2], x2, y2, color);
    vita2d_draw_array(SCE_GXM_PRIMITIVE_TRIANGLES, v, 3);
}

void gui_line(float x0, float y0, float x1, float y1, float thick, unsigned int color) {
    float dx = x1 - x0, dy = y1 - y0;
    float len = sqrtf(dx * dx + dy * dy);
    if (len <= 0) return;
    float nx = -dy / len * thick / 2, ny = dx / len * thick / 2;
    vita2d_color_vertex *v = verts(4);
    if (!v) return;
    vset(&v[0], x0 + nx, y0 + ny, color);
    vset(&v[1], x0 - nx, y0 - ny, color);
    vset(&v[2], x1 + nx, y1 + ny, color);
    vset(&v[3], x1 - nx, y1 - ny, color);
    vita2d_draw_array(SCE_GXM_PRIMITIVE_TRIANGLE_STRIP, v, 4);
}

void gui_icon_up(float cx, float cy, float s, unsigned int color) {
    float h = s / 2;
    gui_triangle(cx, cy - h, cx - h, cy, cx + h, cy, color);
    gui_rect(cx - s * 0.17f, cy - 0.5f, s * 0.34f, h + 0.5f, color);
}

void gui_icon_down(float cx, float cy, float s, unsigned int color) {
    float h = s / 2;
    gui_triangle(cx, cy + h, cx - h, cy, cx + h, cy, color);
    gui_rect(cx - s * 0.17f, cy - h, s * 0.34f, h + 0.5f, color);
}

void gui_icon_check(float cx, float cy, float s, unsigned int color) {
    float t = s * 0.16f;
    if (t < 2) t = 2;
    gui_line(cx - s * 0.42f, cy, cx - s * 0.12f, cy + s * 0.3f, t, color);
    gui_line(cx - s * 0.17f, cy + s * 0.3f, cx + s * 0.45f, cy - s * 0.32f, t, color);
}

void gui_icon_cross(float cx, float cy, float s, unsigned int color) {
    float t = s * 0.16f, h = s * 0.36f;
    if (t < 2) t = 2;
    gui_line(cx - h, cy - h, cx + h, cy + h, t, color);
    gui_line(cx - h, cy + h, cx + h, cy - h, t, color);
}

/* ------------------------------------------------------------------ */
/* Text                                                                */
/* ------------------------------------------------------------------ */

/* Length in bytes of the UTF-8 sequence starting at p (>= 1) */
static int utf8_len(const char *p) {
    unsigned char c = (unsigned char)*p;
    int n = (c < 0x80) ? 1 : (c >> 5) == 0x6 ? 2 : (c >> 4) == 0xE ? 3 : (c >> 3) == 0x1E ? 4 : 1;
    for (int i = 1; i < n; i++)
        if (!p[i]) return i;
    return n;
}

static float char_w(const char *p, int n, float scale) {
    unsigned char c = (unsigned char)*p;
    if (n == 1) return (c < 128) ? g_ascii_w[c] * scale : 0;
    char buf[8];
    memcpy(buf, p, (size_t)n);
    buf[n] = '\0';
    return (float)font_width(scale * g_k, buf);
}

float gui_line_h(float scale) { return g_line * scale; }

float gui_text_w(float scale, const char *s) {
    float w = 0;
    if (!s) return 0;
    while (*s && *s != '\n') {
        int n = utf8_len(s);
        w += char_w(s, n, scale);
        s += n;
    }
    return w;
}

float gui_text(float x, float y, float scale, unsigned int color, GuiAlign align, const char *s) {
    if (!s || !*s) return 0;
    float w = gui_text_w(scale, s);
    if (align == GUI_CENTER) x -= w / 2;
    else if (align == GUI_RIGHT) x -= w;
    font_draw((int)(x + 0.5f), (int)(y + g_line * FONT_ASCENT * scale + 0.5f),
              color, scale * g_k, s);
    return w;
}

float gui_textf(float x, float y, float scale, unsigned int color, GuiAlign align,
                const char *fmt, ...) {
    char buf[256];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    return gui_text(x, y, scale, color, align, buf);
}

/* Copy the longest prefix of `s` that fits `max_w` together with an
 * ellipsis; a newline ends the text. */
static void fit(const char *s, float scale, float max_w, char *out, size_t size) {
    if (gui_text_w(scale, s) <= max_w) {
        snprintf(out, size, "%s", s);
        char *nl = strchr(out, '\n');
        if (nl) *nl = '\0';
        return;
    }
    float limit = max_w - gui_text_w(scale, g_ellipsis);
    float w = 0;
    const char *p = s;
    while (*p && *p != '\n') {
        int n = utf8_len(p);
        float cw = char_w(p, n, scale);
        if (w + cw > limit) break;
        w += cw;
        p += n;
    }
    size_t len = (size_t)(p - s);
    while (len > 0 && s[len - 1] == ' ') len--;
    size_t el = strlen(g_ellipsis);
    if (len + el + 1 > size) len = size - el - 1;
    memcpy(out, s, len);
    memcpy(out + len, g_ellipsis, el + 1);
}

float gui_text_fit(float x, float y, float scale, unsigned int color, GuiAlign align,
                   float max_w, const char *s) {
    if (!s || !*s) return 0;
    char buf[GUI_WRAP_LINE * 2];
    fit(s, scale, max_w, buf, sizeof(buf));
    return gui_text(x, y, scale, color, align, buf);
}

float gui_text_mid(float x, float y, float h, float scale, unsigned int color,
                   GuiAlign align, float max_w, const char *s) {
    float ty = y + (h - gui_line_h(scale)) / 2;
    if (max_w > 0) return gui_text_fit(x, ty, scale, color, align, max_w, s);
    return gui_text(x, ty, scale, color, align, s);
}

int gui_wrap(const char *s, float scale, float max_w, char lines[][GUI_WRAP_LINE], int max_lines) {
    int count = 0;
    if (!s || max_lines <= 0) return 0;
    const char *p = s;
    while (*p && count < max_lines) {
        const char *start = p, *cut = NULL, *after_cut = NULL;
        float w = 0;
        while (*p && *p != '\n') {
            int n = utf8_len(p);
            float cw = char_w(p, n, scale);
            if (w + cw > max_w && p > start) break;
            if (*p == ' ' || *p == '/') {
                /* Break after a slash so long paths wrap at a separator */
                cut = (*p == ' ') ? p : p + 1;
                after_cut = p + 1;
            }
            w += cw;
            p += n;
            if ((size_t)(p - start) >= GUI_WRAP_LINE - 8) break;
        }
        const char *end = p;
        if (*p && *p != '\n' && cut && cut > start) {
            end = cut;
            p = after_cut;
        }
        size_t len = (size_t)(end - start);
        if (len > GUI_WRAP_LINE - 1) len = GUI_WRAP_LINE - 1;
        memcpy(lines[count], start, len);
        lines[count][len] = '\0';
        count++;
        if (*p == '\n') p++;
        else while (*p == ' ') p++;
    }
    if (*p && count > 0) {
        /* Text left over: ellipsis on the last line */
        char tmp[GUI_WRAP_LINE + 8];
        snprintf(tmp, sizeof(tmp), "%s%s", lines[count - 1], g_ellipsis);
        fit(tmp, scale, max_w, lines[count - 1], GUI_WRAP_LINE);
    }
    return count;
}

int gui_text_wrap(float x, float y, float scale, unsigned int color, float max_w,
                  int max_lines, const char *s) {
    char lines[8][GUI_WRAP_LINE];
    if (max_lines > 8) max_lines = 8;
    int n = gui_wrap(s, scale, max_w, lines, max_lines);
    float lh = gui_line_h(scale) * 1.12f;
    for (int i = 0; i < n; i++)
        gui_text(x, y + i * lh, scale, color, GUI_LEFT, lines[i]);
    return n;
}

/* ------------------------------------------------------------------ */
/* Widgets                                                             */
/* ------------------------------------------------------------------ */

float gui_pill_w(float h, float scale, const char *label) {
    return gui_text_w(scale, label) + h * 0.9f;
}

float gui_pill(float x, float y, float h, float scale, unsigned int bg, unsigned int fg,
               const char *label) {
    float pad = h * 0.45f;
    float w = gui_text_w(scale, label) + pad * 2;
    gui_rrect(x, y, w, h, h / 2, bg);
    gui_text_mid(x + pad, y, h, scale, fg, GUI_LEFT, 0, label);
    return w;
}

float gui_pill_outline(float x, float y, float h, float scale, uint32_t hex,
                       uint32_t fill_hex, const char *label) {
    float pad = h * 0.45f;
    float w = gui_text_w(scale, label) + pad * 2;
    gui_rrect(x, y, w, h, h / 2, gui_rgb(hex));
    gui_rrect(x + 1.5f, y + 1.5f, w - 3, h - 3, (h - 3) / 2, gui_rgb(fill_hex));
    gui_text_mid(x + pad, y, h, scale, gui_rgb(hex), GUI_LEFT, 0, label);
    return w;
}

/* PlayStation face-button colours */
#define HEX_PS_CROSS    0x7FA8E8
#define HEX_PS_CIRCLE   0xF06A6A
#define HEX_PS_SQUARE   0xE58ACB
#define HEX_PS_TRIANGLE 0x3CCB9E
#define BTN_R 12.0f      /* face button radius */

static bool is(const char *a, const char *b) { return strcmp(a, b) == 0; }

float gui_button_w(const char *b) {
    if (is(b, "CROSS") || is(b, "CIRCLE") || is(b, "SQUARE") || is(b, "TRIANGLE"))
        return BTN_R * 2;
    if (is(b, "L") || is(b, "R")) return 30;
    if (is(b, "DPAD") || is(b, "UD") || is(b, "LR")) return 22;
    return gui_text_w(GUI_S_TINY, b) + 16;
}

float gui_button(float x, float cy, const char *b) {
    unsigned int ink = gui_rgb(HEX_INK);
    if (is(b, "CROSS") || is(b, "CIRCLE") || is(b, "SQUARE") || is(b, "TRIANGLE")) {
        float cx = x + BTN_R;
        unsigned int face = gui_rgb(0x0A1118), rim = gui_rgb(0x3A4B5E);
        gui_circle(cx, cy, BTN_R, rim);
        gui_circle(cx, cy, BTN_R - 1.5f, face);
        if (is(b, "CROSS")) {
            unsigned int c = gui_rgb(HEX_PS_CROSS);
            gui_line(cx - 5, cy - 5, cx + 5, cy + 5, 2.4f, c);
            gui_line(cx - 5, cy + 5, cx + 5, cy - 5, 2.4f, c);
        } else if (is(b, "CIRCLE")) {
            gui_circle(cx, cy, 6.2f, gui_rgb(HEX_PS_CIRCLE));
            gui_circle(cx, cy, 3.9f, face);
        } else if (is(b, "SQUARE")) {
            unsigned int c = gui_rgb(HEX_PS_SQUARE);
            gui_rect(cx - 5.5f, cy - 5.5f, 11, 11, c);
            gui_rect(cx - 3.2f, cy - 3.2f, 6.4f, 6.4f, face);
        } else {
            unsigned int c = gui_rgb(HEX_PS_TRIANGLE);
            gui_triangle(cx, cy - 6.8f, cx - 6.6f, cy + 4.8f, cx + 6.6f, cy + 4.8f, c);
            gui_triangle(cx, cy - 2.6f, cx - 3.0f, cy + 2.6f, cx + 3.0f, cy + 2.6f, face);
        }
        return BTN_R * 2;
    }
    if (is(b, "L") || is(b, "R")) {
        /* Shoulder button: a tab with the outer top corner rounded */
        unsigned int c = gui_rgb(0xAAB8C5);
        gui_rrect(x, cy - 10, 30, 20, 7, c);
        if (is(b, "L")) gui_rect(x + 15, cy - 10, 15, 8, c);
        else gui_rect(x, cy - 10, 15, 8, c);
        gui_text_mid(x + 15, cy - 10, 20, GUI_S_SMALL, ink, GUI_CENTER, 0, b);
        return 30;
    }
    if (is(b, "DPAD") || is(b, "UD") || is(b, "LR")) {
        unsigned int base = gui_rgb(0x6F8295), hi = gui_rgb(HEX_TEXT);
        bool ud = is(b, "UD"), lr = is(b, "LR"), all = !ud && !lr;
        gui_rrect(x + 7.5f, cy - 11, 7, 22, 2, (ud || all) ? hi : base);
        gui_rrect(x, cy - 3.5f, 22, 7, 2, (lr || all) ? hi : base);
        if (lr) {
            gui_rrect(x + 7.5f, cy - 11, 7, 7.5f, 2, base);
            gui_rrect(x + 7.5f, cy + 3.5f, 7, 7.5f, 2, base);
        }
        if (ud) {
            gui_rrect(x, cy - 3.5f, 7.5f, 7, 2, base);
            gui_rrect(x + 14.5f, cy - 3.5f, 7.5f, 7, 2, base);
        }
        return 22;
    }
    /* START / SELECT: small capsule */
    float w = gui_text_w(GUI_S_TINY, b) + 16;
    gui_rrect(x, cy - 9, w, 18, 9, gui_rgb(0xAAB8C5));
    gui_text_mid(x + w / 2, cy - 9, 18, GUI_S_TINY, ink, GUI_CENTER, 0, b);
    return w;
}

void gui_bar(float x, float y, float w, float h, float frac, unsigned int color) {
    gui_bar_track(x, y, w, h, frac, color, gui_rgb(HEX_BG2));
}

void gui_bar_track(float x, float y, float w, float h, float frac, unsigned int color,
                   unsigned int track) {
    gui_rrect(x, y, w, h, h / 2, track);
    if (frac < 0) {
        float seg = w * 0.3f;
        float t = (sinf(g_ticks * 0.05f) + 1) / 2;
        gui_rrect(x + (w - seg) * t, y, seg, h, h / 2, color);
        return;
    }
    if (frac > 1) frac = 1;
    float fw = w * frac;
    if (fw <= 0) return;
    if (fw < h) fw = h;
    gui_rrect(x, y, fw, h, h / 2, color);
    if (fw > h) gui_rect(x + h / 2, y + 2, fw - h, h * 0.30f, gui_rgba(0xFFFFFF, 0x30));
}

void gui_spinner(float cx, float cy, float r, unsigned int color) {
    const int dots = 10;
    int head = (int)(g_ticks / 4) % dots;
    for (int i = 0; i < dots; i++) {
        float a = (float)i / dots * 2 * (float)M_PI;
        int age = (head - i + dots) % dots;
        uint8_t alpha = (uint8_t)(255 - age * 22);
        unsigned int c = (color & 0x00FFFFFF) | ((unsigned int)alpha << 24);
        gui_circle(cx + cosf(a) * r, cy + sinf(a) * r, r * 0.2f, c);
    }
}

void gui_scrollbar(float x, float y, float h, int visible, int total, float smooth_first) {
    if (total <= visible || total <= 0) return;
    gui_rrect(x, y, 5, h, 2.5f, gui_rgb(HEX_BG2));
    float th = h * visible / total;
    if (th < 24) th = 24;
    float ty = y + (h - th) * (smooth_first / (float)(total - visible));
    if (ty < y) ty = y;
    if (ty > y + h - th) ty = y + h - th;
    gui_rrect(x, ty, 5, th, 2.5f, gui_rgb(HEX_DIM));
}

void gui_panel(float x, float y, float w, float h) {
    gui_rrect(x - 1, y - 1, w + 2, h + 2, 11, gui_rgb(0x0A1118));
    gui_rrect(x, y, w, h, 10, gui_rgb(HEX_PANEL));
}

void gui_card(float x, float y, float w, float h, const char *title, uint32_t tone) {
    gui_rrect(x + 3, y + 5, w, h, 12, gui_rgba(0x000000, 0x70));
    gui_rrect(x - 1.5f, y - 1.5f, w + 3, h + 3, 12, gui_rgb(gui_mix(HEX_LINE, tone, 0.45f)));
    gui_rrect(x, y, w, h, 11, gui_rgb(HEX_PANEL));
    if (title) {
        /* Title strip: rounded on top, square at the bottom */
        gui_rrect(x, y, w, 40, 11, gui_rgb(HEX_PANEL_HI));
        gui_rect(x, y + 20, w, 20, gui_rgb(HEX_PANEL_HI));
        gui_rect(x, y + 40, w, 1, gui_rgb(HEX_LINE));
        gui_rrect(x + 16, y + 12, 6, 16, 3, gui_rgb(tone));
        gui_text_mid(x + 32, y, 40, GUI_S_BODY, gui_rgb(HEX_TEXT), GUI_LEFT, w - 48, title);
    }
}

void gui_dim(void) {
    gui_rect(0, 0, GUI_W, GUI_H, gui_rgba(0x05090D, 0xB8));
}

void gui_banner(float x, float y, float w, float h, uint32_t tone, const char *text) {
    gui_rrect(x, y, w, h, 8, gui_rgb(gui_mix(HEX_BG2, tone, 0.10f)));
    gui_rrect(x, y, 5, h, 2.5f, gui_rgb(tone));
    gui_text_mid(x + 16, y, h, GUI_S_SMALL, gui_rgb(HEX_TEXT), GUI_LEFT, w - 26, text);
}

void gui_header(const char *section, int online) {
    gui_rect(0, 0, GUI_W, GUI_HEADER_H, gui_rgb(HEX_BG2));
    gui_rect(0, GUI_HEADER_H - 1, GUI_W, 1, gui_rgb(HEX_LINE));

    /* Logo mark: teal rounded square with a notch */
    gui_rrect(18, 12, 24, 24, 7, gui_rgb(HEX_ACCENT));
    gui_rrect(25, 19, 10, 10, 3, gui_rgb(HEX_BG2));
    float x = 52;
    x += gui_text_mid(x, 0, GUI_HEADER_H, GUI_S_TITLE, gui_rgb(HEX_TEXT), GUI_LEFT, 0, "GameSync");
    if (section && *section) {
        gui_circle(x + 13, GUI_HEADER_H / 2.0f, 3, gui_rgb(HEX_MUTED));
        gui_text_mid(x + 26, 0, GUI_HEADER_H, GUI_S_BODY, gui_rgb(HEX_ACCENT2), GUI_LEFT,
                     420, section);
    }

    /* Right side: server status chip, then the version */
    float cy = GUI_HEADER_H / 2.0f;
    uint32_t dot = online > 0 ? HEX_OK : (online == 0 ? HEX_ERR : HEX_MUTED);
    const char *label = online > 0 ? "Online" : (online == 0 ? "Offline" : "Connecting");
    float lw = gui_text_w(GUI_S_SMALL, label);
    float chip_w = lw + 40;
    float chip_x = GUI_W - 16 - chip_w;
    gui_rrect(chip_x, cy - 14, chip_w, 28, 14, gui_rgb(HEX_BG));
    gui_circle(chip_x + 17, cy, 7.5f, gui_rgba(dot, 0x50));
    gui_circle(chip_x + 17, cy, 4.5f, gui_rgb(dot));
    gui_text_mid(chip_x + 30, cy - 14, 28, GUI_S_SMALL, gui_rgb(HEX_TEXT), GUI_LEFT, 0, label);
    gui_text_mid(chip_x - 14, 0, GUI_HEADER_H, GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_RIGHT, 0,
                 "v" APP_VERSION);
}

/* A hint with an empty label hugs the next one ("L R  Switch system") */
static float hint_w(const GuiHint *h) {
    if (!h->label[0]) return gui_button_w(h->button);
    return gui_button_w(h->button) + 7 + gui_text_w(GUI_S_SMALL, h->label);
}

static float hint_gap(const GuiHint *prev) {
    return prev->label[0] ? 22 : 5;
}

float gui_hints(float x, float cy, const GuiHint *hints, int count, bool right) {
    if (right) {
        float total = 0;
        for (int i = 0; i < count; i++)
            total += hint_w(&hints[i]) + (i ? hint_gap(&hints[i - 1]) : 0);
        x -= total;
    }
    for (int i = 0; i < count; i++) {
        if (i) x += hint_gap(&hints[i - 1]);
        x += gui_button(x, cy, hints[i].button);
        if (!hints[i].label[0]) continue;
        x += 7;
        x += gui_text_mid(x, cy - 15, 30, GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_LEFT, 0,
                          hints[i].label);
    }
    return x;
}

void gui_footer(const GuiHint *hints, int count) {
    float y = GUI_FOOTER_Y;
    gui_rect(0, y, GUI_W, GUI_FOOTER_H, gui_rgb(HEX_BG2));
    gui_rect(0, y, GUI_W, 1, gui_rgb(HEX_LINE));
    if (count > 0) gui_hints(18, y + GUI_FOOTER_H / 2.0f, hints, count, false);
}

float gui_tabs(float x, float y, float h, float scale, const char *const *labels,
               int count, int active) {
    float pad = 16, total = 0;
    for (int i = 0; i < count; i++) total += gui_text_w(scale, labels[i]) + pad * 2;
    gui_rrect(x, y, total + 6, h, h / 2, gui_rgb(HEX_BG));
    float cx = x + 3;
    for (int i = 0; i < count; i++) {
        float w = gui_text_w(scale, labels[i]) + pad * 2;
        bool on = (i == active);
        if (on) gui_rrect(cx, y + 3, w, h - 6, (h - 6) / 2, gui_rgb(HEX_ACCENT));
        gui_text_mid(cx + w / 2, y, h, scale, gui_rgb(on ? HEX_INK : HEX_DIM), GUI_CENTER, 0,
                     labels[i]);
        cx += w;
    }
    return x + total + 6;
}
