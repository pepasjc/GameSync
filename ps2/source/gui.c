/*
 * gui.c — gsKit drawing kit for the GameSync PS2 client.
 *
 * Frames are built in gsKit's one-shot queue and flipped between two
 * 640x448 (NTSC) / 640x512 (PAL) 24-bit frame buffers.  No Z buffer: the
 * UI is painted strictly back to front with alpha blending always on, so
 * VRAM holds just the two frame buffers (~2.3-2.6 MB) plus one 512x256
 * glyph texture (512 KB).
 *
 * Text comes from a coverage atlas baked from the Vegur typeface by
 * ps2/tools/gen_font.py (see font_data.h); each glyph is one textured
 * sprite modulated by the text colour.
 */

#include "gui.h"
#include "common.h"

#include <malloc.h>
#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include <kernel.h>
#include <timer.h>

#include <dmaKit.h>
#include <gsKit.h>

static GSGLOBAL  *gs;
static GSTEXTURE  font_tex;
static float      oy;          /* PAL: canvas offset inside the 512-line buffer */

static const char ellipsis[] = "...";

/* ---- Colours ---- */

/* Alpha is GS-scaled: 0x80 = opaque. */
static inline u64 col(uint32_t hex, uint8_t alpha255) {
    return GS_SETREG_RGBAQ((hex >> 16) & 0xFF, (hex >> 8) & 0xFF, hex & 0xFF,
                           (alpha255 + 1) >> 1, 0x00);
}

static uint32_t mix(uint32_t a, uint32_t b, float t) {
    int r = (int)(((a >> 16) & 0xFF) * (1 - t) + ((b >> 16) & 0xFF) * t);
    int g = (int)(((a >> 8) & 0xFF) * (1 - t) + ((b >> 8) & 0xFF) * t);
    int bl = (int)((a & 0xFF) * (1 - t) + (b & 0xFF) * t);
    return ((uint32_t)r << 16) | ((uint32_t)g << 8) | (uint32_t)bl;
}

/* ---- Lifetime and frames ---- */

static void upload_font(void) {
    font_tex.Width  = FONT_ATLAS_W;
    font_tex.Height = FONT_ATLAS_H;
    font_tex.PSM    = GS_PSM_CT32;
    font_tex.Filter = GS_FILTER_NEAREST;
    font_tex.Clut   = NULL;
    font_tex.VramClut = 0;

    u32 size = gsKit_texture_size_ee(font_tex.Width, font_tex.Height, font_tex.PSM);
    u32 *px = memalign(128, size);
    if (!px) return;

    /* 4-bit coverage -> white texel with GS alpha (0x80 = opaque).  RGB is
     * 0x80 too, so MODULATE yields exactly the vertex colour. */
    for (int i = 0; i < FONT_ATLAS_W * FONT_ATLAS_H; i++) {
        uint8_t b = g_font_atlas4[i >> 1];
        uint32_t n = (i & 1) ? (b >> 4) : (b & 0x0F);
        uint32_t a = (n * 128 + 7) / 15;
        px[i] = (a << 24) | 0x808080;
    }
    font_tex.Mem  = px;
    font_tex.Vram = gsKit_vram_alloc(gs, gsKit_texture_size(font_tex.Width, font_tex.Height,
                                                            font_tex.PSM),
                                     GSKIT_ALLOC_USERBUFFER);
    FlushCache(0);
    gsKit_texture_upload(gs, &font_tex);
    /* The texture lives in VRAM for the whole session and is never
     * re-uploaded, so the EE copy can go. */
    free(px);
    font_tex.Mem = NULL;
}

void gui_init(void) {
    if (gs) return;

    /* Smaller persistent queue than the default 256 KB: we only draw
     * one-shot frames. */
    gs = gsKit_init_global_custom(GS_RENDER_QUEUE_OS_POOLSIZE, 16 * 1024);
    gs->PSM             = GS_PSM_CT24;
    gs->ZBuffering      = GS_SETTING_OFF;
    gs->DoubleBuffering = GS_SETTING_ON;
    gs->PrimAlphaEnable = GS_SETTING_ON;

    dmaKit_init(D_CTRL_RELE_OFF, D_CTRL_MFD_OFF, D_CTRL_STS_UNSPEC,
                D_CTRL_STD_OFF, D_CTRL_RCYC_8, 1 << DMA_CHANNEL_GIF);
    dmaKit_chan_init(DMA_CHANNEL_GIF);

    gsKit_init_screen(gs);
    gsKit_mode_switch(gs, GS_ONESHOT);

    oy = (float)((gs->Height - GUI_H) / 2);
    if (oy < 0) oy = 0;

    upload_font();
}

void gui_begin(void) {
    gsKit_set_primalpha(gs, GS_SETREG_ALPHA(0, 1, 0, 1, 0), 0);
    gsKit_set_test(gs, GS_ATEST_OFF);
    gsKit_clear(gs, col(HEX_BG, 0xFF));
}

void gui_end(bool vsync) {
    gsKit_queue_exec(gs);
    if (vsync) {
        gsKit_sync_flip(gs);
        return;
    }
    /* gsKit_sync_flip() minus the vblank wait. */
    gsKit_finish();
    if (gs->DoubleBuffering == GS_SETTING_ON) {
        GS_SET_DISPFB2(gs->ScreenBuffer[gs->ActiveBuffer & 1] / 8192,
                       gs->Width / 64, gs->PSM, 0, 0);
        gs->ActiveBuffer ^= 1;
    }
    gsKit_setactive(gs);
}

uint32_t gui_ms(void) {
    return (uint32_t)(GetTimerSystemTime() / (kBUSCLK / 1000));
}

/* ---- Shapes ---- */

void gui_rect_a(float x, float y, float w, float h, uint32_t hex, uint8_t alpha) {
    if (w <= 0 || h <= 0) return;
    gsKit_prim_sprite(gs, x, y + oy, x + w, y + h + oy, 0, col(hex, alpha));
}

void gui_rect(float x, float y, float w, float h, uint32_t hex) {
    gui_rect_a(x, y, w, h, hex, 0xFF);
}

void gui_vgrad(float x, float y, float w, float h, uint32_t top, uint32_t bottom) {
    u64 t = col(top, 0xFF), b = col(bottom, 0xFF);
    gsKit_prim_quad_gouraud(gs, x, y + oy, x, y + h + oy, x + w, y + oy, x + w, y + h + oy,
                            0, t, b, t, b);
}

void gui_hgrad(float x, float y, float w, float h, uint32_t left, uint32_t right) {
    u64 l = col(left, 0xFF), r = col(right, 0xFF);
    gsKit_prim_quad_gouraud(gs, x, y + oy, x, y + h + oy, x + w, y + oy, x + w, y + h + oy,
                            0, l, l, r, r);
}

void gui_tri(float x1, float y1, float x2, float y2, float x3, float y3, uint32_t hex) {
    gsKit_prim_triangle(gs, x1, y1 + oy, x2, y2 + oy, x3, y3 + oy, 0, col(hex, 0xFF));
}

#define ARC_SEGS 5

/* Quarter-circle fan centred on (cx, cy) covering angles a0..a0+90deg. */
static void arc(float cx, float cy, float r, float a0, u64 c) {
    float v[2 * (ARC_SEGS + 2)];
    v[0] = cx;
    v[1] = cy + oy;
    for (int i = 0; i <= ARC_SEGS; i++) {
        float a = a0 + (float)M_PI / 2 * i / ARC_SEGS;
        v[2 + i * 2]     = cx + cosf(a) * r;
        v[2 + i * 2 + 1] = cy + oy + sinf(a) * r;
    }
    gsKit_prim_triangle_fan(gs, v, ARC_SEGS + 2, 0, c);
}

void gui_rrect_a(float x, float y, float w, float h, float r, uint32_t hex, uint8_t alpha) {
    if (w <= 0 || h <= 0) return;
    x = floorf(x + 0.5f);
    y = floorf(y + 0.5f);
    w = floorf(w + 0.5f);
    h = floorf(h + 0.5f);
    if (r > w / 2) r = w / 2;
    if (r > h / 2) r = h / 2;
    r = floorf(r);
    u64 c = col(hex, alpha);
    if (r < 1) {
        gsKit_prim_sprite(gs, x, y + oy, x + w, y + h + oy, 0, c);
        return;
    }
    /* Centre column full height, side columns between the corners. */
    gsKit_prim_sprite(gs, x + r, y + oy, x + w - r, y + h + oy, 0, c);
    gsKit_prim_sprite(gs, x, y + r + oy, x + r, y + h - r + oy, 0, c);
    gsKit_prim_sprite(gs, x + w - r, y + r + oy, x + w, y + h - r + oy, 0, c);
    arc(x + r,     y + r,     r, (float)M_PI,       c);
    arc(x + w - r, y + r,     r, (float)M_PI * 1.5f, c);
    arc(x + w - r, y + h - r, r, 0,                 c);
    arc(x + r,     y + h - r, r, (float)M_PI / 2,   c);
}

void gui_rrect(float x, float y, float w, float h, float r, uint32_t hex) {
    gui_rrect_a(x, y, w, h, r, hex, 0xFF);
}

void gui_circle(float cx, float cy, float r, uint32_t hex) {
    enum { SEGS = 20 };
    float v[2 * (SEGS + 2)];
    v[0] = cx;
    v[1] = cy + oy;
    for (int i = 0; i <= SEGS; i++) {
        float a = 2 * (float)M_PI * i / SEGS;
        v[2 + i * 2]     = cx + cosf(a) * r;
        v[2 + i * 2 + 1] = cy + oy + sinf(a) * r;
    }
    gsKit_prim_triangle_fan(gs, v, SEGS + 2, 0, col(hex, 0xFF));
}

void gui_ring(float cx, float cy, float r, float thick, uint32_t hex, uint32_t inner) {
    gui_circle(cx, cy, r, hex);
    gui_circle(cx, cy, r - thick, inner);
}

/* Bar of thickness t from (x1,y1) to (x2,y2). */
static void thick_line(float x1, float y1, float x2, float y2, float t, uint32_t hex) {
    float dx = x2 - x1, dy = y2 - y1;
    float len = sqrtf(dx * dx + dy * dy);
    if (len <= 0) return;
    float nx = -dy / len * t / 2, ny = dx / len * t / 2;
    gsKit_prim_quad(gs, x1 + nx, y1 + ny + oy, x1 - nx, y1 - ny + oy,
                    x2 + nx, y2 + ny + oy, x2 - nx, y2 - ny + oy, 0, col(hex, 0xFF));
}

void gui_icon_check(float cx, float cy, float s, uint32_t hex) {
    thick_line(cx - s * 0.45f, cy, cx - s * 0.1f, cy + s * 0.35f, s * 0.22f, hex);
    thick_line(cx - s * 0.1f, cy + s * 0.35f, cx + s * 0.5f, cy - s * 0.35f, s * 0.22f, hex);
}

/* Left/right pointing arrow heads, centred on (cx, cy). */
void gui_icon_arrow(float cx, float cy, float s, bool right, uint32_t hex) {
    float d = right ? 1 : -1;
    gui_tri(cx + d * s / 2, cy, cx - d * s / 2, cy - s / 2, cx - d * s / 2, cy + s / 2, hex);
}

/* ---- Text ---- */

static const FontGlyph *glyph(FontId f, unsigned char ch) {
    if (ch < FONT_FIRST_CHAR || ch >= FONT_FIRST_CHAR + FONT_GLYPH_COUNT) ch = '?';
    return &g_font_faces[f].glyphs[ch - FONT_FIRST_CHAR];
}

int gui_line_h(FontId f) {
    return g_font_faces[f].ascent + g_font_faces[f].descent;
}

float gui_text_w(FontId f, const char *s) {
    int q = 0;
    if (!s) return 0;
    for (; *s && *s != '\n'; s++) q += glyph(f, (unsigned char)*s)->adv_q;
    return q / 4.0f;
}

static void draw_run(float x, float y, FontId f, u64 c, const char *s) {
    const FontFace *face = &g_font_faces[f];
    float base = floorf(y + 0.5f) + face->ascent + oy;
    int pen_q = (int)floorf(x * 4 + 0.5f);
    for (; *s && *s != '\n'; s++) {
        const FontGlyph *g = glyph(f, (unsigned char)*s);
        if (g->w && g->h) {
            float gx = (float)((pen_q + 2) >> 2) + g->xoff;
            float gy = base + g->yoff;
            gsKit_prim_sprite_texture(gs, &font_tex,
                                      gx, gy, (float)g->x, (float)g->y,
                                      gx + g->w, gy + g->h,
                                      (float)(g->x + g->w), (float)(g->y + g->h),
                                      0, c);
        }
        pen_q += g->adv_q;
    }
}

float gui_text(float x, float y, FontId f, uint32_t hex, GuiAlign align, const char *s) {
    if (!s || !*s) return 0;
    float w = gui_text_w(f, s);
    if (align == GUI_CENTER) x -= w / 2;
    else if (align == GUI_RIGHT) x -= w;
    draw_run(x, y, f, col(hex, 0xFF), s);
    return w;
}

float gui_textf(float x, float y, FontId f, uint32_t hex, GuiAlign align, const char *fmt, ...) {
    char buf[256];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    return gui_text(x, y, f, hex, align, buf);
}

/* Copy the longest prefix of s that fits max_w together with an ellipsis;
 * returns true if it had to cut. */
static bool fit(const char *s, FontId f, float max_w, char *out, size_t size) {
    if (gui_text_w(f, s) <= max_w) {
        snprintf(out, size, "%s", s);
        char *nl = strchr(out, '\n');
        if (nl) *nl = '\0';
        return false;
    }
    float limit = max_w - gui_text_w(f, ellipsis);
    float w = 0;
    const char *p = s;
    while (*p && *p != '\n') {
        float gw = glyph(f, (unsigned char)*p)->adv_q / 4.0f;
        if (w + gw > limit) break;
        w += gw;
        p++;
    }
    size_t len = (size_t)(p - s);
    while (len > 0 && s[len - 1] == ' ') len--;
    if (len + sizeof(ellipsis) > size) len = size - sizeof(ellipsis);
    memcpy(out, s, len);
    memcpy(out + len, ellipsis, sizeof(ellipsis));
    return true;
}

float gui_text_fit(float x, float y, FontId f, uint32_t hex, GuiAlign align,
                   float max_w, const char *s) {
    if (!s || !*s) return 0;
    char buf[GUI_WRAP_LINE * 2];
    fit(s, f, max_w, buf, sizeof(buf));
    return gui_text(x, y, f, hex, align, buf);
}

float gui_text_mid(float x, float y, float h, FontId f, uint32_t hex, GuiAlign align,
                   float max_w, const char *s) {
    /* Centre the cap height rather than the full line box: looks right
     * for the mostly-uppercase-and-digits labels in pills and buttons. */
    const FontFace *face = &g_font_faces[f];
    float cap = face->ascent * 0.72f;
    float ty = y + (h - cap) / 2 - (face->ascent - cap);
    if (max_w > 0) return gui_text_fit(x, ty, f, hex, align, max_w, s);
    return gui_text(x, ty, f, hex, align, s);
}

int gui_wrap(const char *s, FontId f, float max_w, char lines[][GUI_WRAP_LINE], int max_lines) {
    int count = 0;
    if (!s || max_lines <= 0) return 0;
    const char *p = s;
    while (*p && count < max_lines) {
        const char *start = p, *cut = NULL, *after_cut = NULL;
        float w = 0;
        while (*p && *p != '\n') {
            float gw = glyph(f, (unsigned char)*p)->adv_q / 4.0f;
            if (w + gw > max_w && p > start) break;
            if (*p == ' ') {
                cut = p;
                after_cut = p + 1;
            }
            w += gw;
            p++;
            if ((size_t)(p - start) >= GUI_WRAP_LINE - 4) break;
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
        char tmp[GUI_WRAP_LINE + 8];
        snprintf(tmp, sizeof(tmp), "%s%s", lines[count - 1], ellipsis);
        fit(tmp, f, max_w, lines[count - 1], GUI_WRAP_LINE);
    }
    return count;
}

int gui_text_wrap(float x, float y, FontId f, uint32_t hex, float max_w,
                  int max_lines, const char *s) {
    char lines[8][GUI_WRAP_LINE];
    if (max_lines > 8) max_lines = 8;
    int n = gui_wrap(s, f, max_w, lines, max_lines);
    int lh = gui_line_h(f);
    for (int i = 0; i < n; i++) gui_text(x, y + i * lh, f, hex, GUI_LEFT, lines[i]);
    return n;
}

/* ---- Widgets ---- */

#define PILL_PAD 8

float gui_pill_w(FontId f, const char *label) {
    return gui_text_w(f, label) + PILL_PAD * 2;
}

float gui_pill(float x, float y, float h, FontId f, uint32_t bg, uint32_t fg, const char *label) {
    float w = gui_pill_w(f, label);
    gui_rrect(x, y, w, h, h / 2, bg);
    gui_text_mid(x + PILL_PAD, y, h, f, fg, GUI_LEFT, 0, label);
    return w;
}

#define BTN_R 9.0f   /* face-button radius */

static bool is_face(const char *b) {
    return b[1] == '\0' && strchr("XOST", b[0]) != NULL;
}

float gui_button_w(const char *b) {
    if (is_face(b)) return BTN_R * 2;
    if (!strcmp(b, "DPAD") || !strcmp(b, "UD") || !strcmp(b, "LR")) return 18;
    return gui_text_w(FONT_TINY, b) + 12;
}

float gui_button(float x, float cy, const char *b) {
    if (is_face(b)) {
        float cx = x + BTN_R;
        uint32_t bg = 0x0B1118;
        gui_circle(cx, cy, BTN_R, 0x3A4B5E);
        gui_circle(cx, cy, BTN_R - 1.5f, bg);
        switch (b[0]) {
            case 'X':
                thick_line(cx - 4.5f, cy - 4.5f, cx + 4.5f, cy + 4.5f, 2.2f, HEX_BTN_CROSS);
                thick_line(cx - 4.5f, cy + 4.5f, cx + 4.5f, cy - 4.5f, 2.2f, HEX_BTN_CROSS);
                break;
            case 'O':
                gui_ring(cx, cy, 5.2f, 2.0f, HEX_BTN_CIRCLE, bg);
                break;
            case 'S':
                gui_rect(cx - 4.5f, cy - 4.5f, 9, 9, HEX_BTN_SQUARE);
                gui_rect(cx - 2.5f, cy - 2.5f, 5, 5, bg);
                break;
            case 'T':
                gui_tri(cx, cy - 5.5f, cx - 5.5f, cy + 4, cx + 5.5f, cy + 4, HEX_BTN_TRIANGLE);
                gui_tri(cx, cy - 2.2f, cx - 2.8f, cy + 2.2f, cx + 2.8f, cy + 2.2f, bg);
                break;
        }
        return BTN_R * 2;
    }
    if (!strcmp(b, "DPAD") || !strcmp(b, "UD") || !strcmp(b, "LR")) {
        uint32_t base = 0x4A5D70, hi = HEX_TEXT;
        bool ud = !strcmp(b, "UD"), lr = !strcmp(b, "LR"), all = !ud && !lr;
        gui_rrect(x + 6, cy - 9, 6, 18, 1.5f, (ud || all) ? hi : base);
        gui_rrect(x, cy - 3, 18, 6, 1.5f, (lr || all) ? hi : base);
        if (lr) {
            gui_rect(x + 6, cy - 9, 6, 6, base);
            gui_rect(x + 6, cy + 3, 6, 6, base);
        }
        if (ud) {
            gui_rect(x, cy - 3, 6, 6, base);
            gui_rect(x + 12, cy - 3, 6, 6, base);
        }
        return 18;
    }
    /* Shoulder buttons get a tab shape, START / SELECT a pill. */
    float w = gui_button_w(b);
    bool shoulder = (b[0] == 'L' || b[0] == 'R') && b[1] && b[2] == '\0';
    gui_rrect(x, cy - 9, w, 18, shoulder ? 4 : 9, 0xAAB8C5);
    gui_text_mid(x + w / 2, cy - 9, 18, FONT_TINY, HEX_INK, GUI_CENTER, 0, b);
    return w;
}

void gui_bar(float x, float y, float w, float h, float frac, uint32_t hex) {
    gui_rrect(x, y, w, h, h / 2, HEX_BG2);
    if (frac < 0) frac = 0;
    if (frac > 1) frac = 1;
    float fw = w * frac;
    if (fw <= 0) return;
    if (fw < h) fw = h;
    gui_rrect(x, y, fw, h, h / 2, hex);
    if (fw > h) gui_rect_a(x + h / 2, y + 1, fw - h, h * 0.35f, 0xFFFFFF, 0x30);
}

void gui_scrollbar(float x, float y, float h, int first, int visible, int total) {
    if (total <= visible || total <= 0) return;
    gui_rrect(x, y, 4, h, 2, HEX_BG2);
    float th = h * visible / total;
    if (th < 16) th = 16;
    float ty = y + (h - th) * ((float)first / (float)(total - visible));
    if (ty < y) ty = y;
    if (ty > y + h - th) ty = y + h - th;
    gui_rrect(x, ty, 4, th, 2, HEX_DIM);
}

void gui_panel(float x, float y, float w, float h) {
    gui_rrect(x - 1, y - 1, w + 2, h + 2, 9, 0x0A1118);
    gui_rrect(x, y, w, h, 8, HEX_PANEL);
}

#define CARD_TITLE_H 30

void gui_card(float x, float y, float w, float h, const char *title, uint32_t tone) {
    gui_rrect_a(x + 3, y + 4, w, h, 9, 0x000000, 0x70);   /* drop shadow */
    gui_rrect(x - 1, y - 1, w + 2, h + 2, 9, mix(HEX_LINE, tone, 0.4f));
    gui_rrect(x, y, w, h, 8, HEX_PANEL);
    if (title) {
        gui_rrect(x, y, w, CARD_TITLE_H, 8, HEX_PANEL_HI);
        gui_rect(x, y + CARD_TITLE_H - 10, w, 10, HEX_PANEL_HI);
        gui_rect(x, y + CARD_TITLE_H, w, 1, HEX_LINE);
        gui_rrect(x + 12, y + 9, 4, 12, 2, tone);
        gui_text_mid(x + 24, y, CARD_TITLE_H, FONT_BODY, HEX_TEXT, GUI_LEFT, w - 36, title);
    }
}

void gui_dim(void) {
    gui_rect_a(0, -oy, GUI_W, GUI_H + 2 * oy, 0x05090D, 0xB4);
}

/* ---- Chrome ---- */

void gui_header(const char *section, const char *right_text, uint32_t dot_hex) {
    gui_vgrad(0, -oy, GUI_W, GUI_HEADER_H + oy, HEX_BG2, 0x18222E);
    gui_rect(0, GUI_HEADER_H - 1, GUI_W, 1, HEX_LINE);

    /* Logo mark: teal rounded square with a notch. */
    float lx = GUI_MARGIN, cy = GUI_HEADER_H / 2.0f + 2;
    gui_rrect(lx, cy - 9, 18, 18, 5, HEX_ACCENT);
    gui_rrect(lx + 5, cy - 4, 8, 8, 2, HEX_BG2);
    float x = lx + 26;
    x += gui_text_mid(x, cy - 12, 24, FONT_TITLE, HEX_TEXT, GUI_LEFT, 0, "GameSync");
    if (section && *section) {
        gui_circle(x + 10, cy, 2.2f, HEX_MUTED);
        gui_text_mid(x + 20, cy - 12, 24, FONT_TITLE, HEX_ACCENT2, GUI_LEFT, 220, section);
    }

    float rx = GUI_W - GUI_MARGIN;
    gui_circle(rx - 5, cy, 6, mix(HEX_BG2, dot_hex, 0.35f));
    gui_circle(rx - 5, cy, 3.5f, dot_hex);
    rx -= 18;
    if (right_text && *right_text)
        rx -= gui_text_mid(rx, cy - 12, 24, FONT_SMALL, HEX_DIM, GUI_RIGHT, 0, right_text) + 12;
    gui_text_mid(rx, cy - 12, 24, FONT_SMALL, HEX_MUTED, GUI_RIGHT, 0, "v" APP_VERSION);
}

void gui_footer(const GuiHint *hints, int count) {
    float y = GUI_FOOTER_Y;
    gui_rect(0, y, GUI_W, GUI_FOOTER_H + oy, HEX_BG2);
    gui_rect(0, y, GUI_W, 1, HEX_LINE);
    if (count <= 0) return;
    float widths[12], total = 0;
    float avail = GUI_W - 2 * GUI_MARGIN;
    FontId font = FONT_SMALL;
    if (count > 12) count = 12;
    for (int pass = 0; pass < 2; pass++) {
        total = 0;
        for (int i = 0; i < count; i++) {
            widths[i] = gui_button_w(hints[i].button) + 5 + gui_text_w(font, hints[i].label);
            total += widths[i];
        }
        if (total + 8 * (count - 1) <= avail) break;
        font = FONT_TINY;   /* crowded view: smaller labels */
    }
    float gap = (avail - total) / (count > 1 ? count - 1 : 1);
    if (gap > 22) gap = 22;
    if (gap < 6) gap = 6;
    float x = GUI_MARGIN, cy = y + 16;
    for (int i = 0; i < count; i++) {
        float bw = gui_button(x, cy, hints[i].button);
        gui_text_mid(x + bw + 5, cy - 12, 24, font, HEX_DIM, GUI_LEFT, 0, hints[i].label);
        x += widths[i] + gap;
    }
}

float gui_tabs(float x, float y, float h, const char *const *labels, int count, int active) {
    float pad = 10, total = 0;
    for (int i = 0; i < count; i++) total += gui_text_w(FONT_SMALL, labels[i]) + pad * 2;
    gui_rrect(x, y, total + 4, h, h / 2, HEX_BG);
    float cx = x + 2;
    for (int i = 0; i < count; i++) {
        float w = gui_text_w(FONT_SMALL, labels[i]) + pad * 2;
        bool on = (i == active);
        if (on) gui_rrect(cx, y + 2, w, h - 4, (h - 4) / 2, HEX_ACCENT);
        gui_text_mid(cx + w / 2, y, h, FONT_SMALL, on ? HEX_INK : HEX_DIM, GUI_CENTER, 0, labels[i]);
        cx += w;
    }
    return x + total + 4;
}
