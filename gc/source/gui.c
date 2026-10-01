/*
 * gui.c — 2D drawing kit on libogc GX (see gui.h).
 *
 * Everything is drawn as vertex-coloured triangles; text is textured quads
 * sampled from the console's own IPL ROM font (SYS_InitFont), so no font is
 * bundled.  If the ROM font can't be read, libogc's built-in 8x16 console
 * font (console_font_8x16, part of libogc) is converted into an I4 sheet and
 * used instead.
 *
 * Frames are synchronous: draw, GX_DrawDone, copy to the back XFB, flip at
 * the next retrace.  There is never a frame queued behind one in flight.
 */

#include "gui.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <malloc.h>
#include <math.h>

#include <gccore.h>

/* ------------------------------------------------------------------------ */
/* Video / GX state                                                          */
/* ------------------------------------------------------------------------ */

#define GX_FIFO_SIZE (256 * 1024)

static GXRModeObj *g_rmode;
static void       *g_xfb[2];
static int         g_fb;
static void       *g_fifo;

typedef enum { MODE_NONE, MODE_SOLID, MODE_TEX } DrawMode;
static DrawMode g_mode = MODE_NONE;
static int      g_bound_sheet = -1;

/* ------------------------------------------------------------------------ */
/* Font                                                                      */
/* ------------------------------------------------------------------------ */

#define FONT_MAX_SHEETS 8

typedef struct {
    u8  sheet;
    u16 u, v;      /* top-left texel of the cell */
    u8  adv;       /* advance in texels */
} Glyph;

static Glyph     g_glyph[256];
static GXTexObj  g_sheet[FONT_MAX_SHEETS];
static int       g_sheets;
static float     g_sheet_w, g_sheet_h;
static float     g_cell_w, g_cell_h;
static float     g_fk = 1.0f;         /* texels -> logical px at scale 1 */
static bool      g_font_ok;

/* libogc's console font: 256 glyphs, 8x16, 1 bpp, MSB = leftmost pixel. */
extern const u8 console_font_8x16[];

static void font_from_rom(void) {
    u32 size = SYS_GetFontEncoding() == 1 ? SYS_FONTSIZE_SJIS : SYS_FONTSIZE_ANSI;
    sys_fontheader *f = (sys_fontheader *)memalign(32, size);
    if (!f) return;
    if (!SYS_InitFont(f) || f->cell_height == 0 || f->sheet_width == 0) {
        free(f);
        return;
    }
    DCFlushRange(f, size);

    g_cell_w  = f->cell_width;
    g_cell_h  = f->cell_height;
    g_sheet_w = f->sheet_width;
    g_sheet_h = f->sheet_height;

    void *sheets[FONT_MAX_SHEETS];
    g_sheets = 0;
    for (int c = 0; c < 256; c++) {
        void *img = NULL;
        s32 x = 0, y = 0, w = 0;
        int ch = c < 0x20 ? '?' : c;
        SYS_GetFontTexture(ch, &img, &x, &y, &w);
        if (!img) { g_glyph[c] = g_glyph['?']; continue; }
        int s;
        for (s = 0; s < g_sheets; s++) if (sheets[s] == img) break;
        if (s == g_sheets) {
            if (g_sheets == FONT_MAX_SHEETS) { g_glyph[c] = g_glyph['?']; continue; }
            sheets[g_sheets] = img;
            GX_InitTexObj(&g_sheet[g_sheets], img, f->sheet_width, f->sheet_height,
                          GX_TF_I4, GX_CLAMP, GX_CLAMP, GX_FALSE);
            GX_InitTexObjLOD(&g_sheet[g_sheets], GX_LINEAR, GX_LINEAR,
                             0, 0, 0, GX_FALSE, GX_FALSE, GX_ANISO_1);
            g_sheets++;
        }
        g_glyph[c].sheet = (u8)s;
        g_glyph[c].u = (u16)x;
        g_glyph[c].v = (u16)y;
        g_glyph[c].adv = (u8)w;
    }
    g_font_ok = g_sheets > 0;
}

static void font_from_console(void) {
    /* 16 x 16 cells of 8x16 -> a 128x256 I4 texture (8x8 texel tiles). */
    const int W = 128, H = 256;
    u8 *tex = (u8 *)memalign(32, W * H / 2);
    if (!tex) return;
    memset(tex, 0, W * H / 2);
    for (int c = 0; c < 256; c++) {
        int cx = (c % 16) * 8, cy = (c / 16) * 16;
        for (int row = 0; row < 16; row++) {
            u8 bits = console_font_8x16[c * 16 + row];
            for (int col = 0; col < 8; col++) {
                if (!(bits & (0x80 >> col))) continue;
                int x = cx + col, y = cy + row;
                int tile = (y / 8) * (W / 8) + (x / 8);
                int off = tile * 32 + ((y % 8) * 8 + (x % 8)) / 2;
                tex[off] |= (x & 1) ? 0x0F : 0xF0;
            }
        }
        g_glyph[c].sheet = 0;
        g_glyph[c].u = (u16)cx;
        g_glyph[c].v = (u16)cy;
        g_glyph[c].adv = 8;
    }
    DCFlushRange(tex, W * H / 2);
    GX_InitTexObj(&g_sheet[0], tex, W, H, GX_TF_I4, GX_CLAMP, GX_CLAMP, GX_FALSE);
    GX_InitTexObjLOD(&g_sheet[0], GX_NEAR, GX_NEAR, 0, 0, 0, GX_FALSE, GX_FALSE, GX_ANISO_1);
    g_sheets = 1;
    g_cell_w = 8; g_cell_h = 16;
    g_sheet_w = W; g_sheet_h = H;
    g_font_ok = true;
}

/* ------------------------------------------------------------------------ */
/* Init / frames                                                             */
/* ------------------------------------------------------------------------ */

void gui_init(void) {
    VIDEO_Init();
    g_rmode = VIDEO_GetPreferredMode(NULL);
    g_xfb[0] = MEM_K0_TO_K1(SYS_AllocateFramebuffer(g_rmode));
    g_xfb[1] = MEM_K0_TO_K1(SYS_AllocateFramebuffer(g_rmode));
    VIDEO_Configure(g_rmode);
    VIDEO_ClearFrameBuffer(g_rmode, g_xfb[0], COLOR_BLACK);
    VIDEO_ClearFrameBuffer(g_rmode, g_xfb[1], COLOR_BLACK);
    VIDEO_SetNextFramebuffer(g_xfb[0]);
    VIDEO_SetBlack(FALSE);
    VIDEO_Flush();
    VIDEO_WaitVSync();
    if (g_rmode->viTVMode & VI_NON_INTERLACE) VIDEO_WaitVSync();
    g_fb = 1;

    g_fifo = memalign(32, GX_FIFO_SIZE);
    memset(g_fifo, 0, GX_FIFO_SIZE);
    GX_Init(g_fifo, GX_FIFO_SIZE);

    GXColor bg = { (HEX_BG >> 16) & 0xFF, (HEX_BG >> 8) & 0xFF, HEX_BG & 0xFF, 0xFF };
    GX_SetCopyClear(bg, GX_MAX_Z24);

    f32 yscale = GX_GetYScaleFactor(g_rmode->efbHeight, g_rmode->xfbHeight);
    u32 xfb_h = GX_SetDispCopyYScale(yscale);
    GX_SetScissor(0, 0, g_rmode->fbWidth, g_rmode->efbHeight);
    GX_SetDispCopySrc(0, 0, g_rmode->fbWidth, g_rmode->efbHeight);
    GX_SetDispCopyDst(g_rmode->fbWidth, xfb_h);
    /* vfilter softens 1-px horizontal edges on interlaced CRTs (less flicker) */
    GX_SetCopyFilter(g_rmode->aa, g_rmode->sample_pattern, GX_TRUE, g_rmode->vfilter);
    GX_SetFieldMode(g_rmode->field_rendering,
                    (g_rmode->viHeight == 2 * g_rmode->xfbHeight) ? GX_ENABLE : GX_DISABLE);
    GX_SetPixelFmt(g_rmode->aa ? GX_PF_RGB565_Z16 : GX_PF_RGB8_Z24, GX_ZC_LINEAR);
    GX_SetDispCopyGamma(GX_GM_1_0);
    GX_SetViewport(0, 0, g_rmode->fbWidth, g_rmode->efbHeight, 0, 1);
    GX_SetCullMode(GX_CULL_NONE);
    GX_SetClipMode(GX_CLIP_DISABLE);
    GX_SetZMode(GX_FALSE, GX_ALWAYS, GX_FALSE);
    GX_SetBlendMode(GX_BM_BLEND, GX_BL_SRCALPHA, GX_BL_INVSRCALPHA, GX_LO_CLEAR);
    GX_SetAlphaUpdate(GX_TRUE);
    GX_SetColorUpdate(GX_TRUE);

    Mtx44 proj;
    guOrtho(proj, 0, GUI_H, 0, GUI_W, 0, 1000);
    GX_LoadProjectionMtx(proj, GX_ORTHOGRAPHIC);
    Mtx mv;
    guMtxIdentity(mv);
    guMtxTransApply(mv, mv, 0, 0, -100.0f);
    GX_LoadPosMtxImm(mv, GX_PNMTX0);

    GX_ClearVtxDesc();
    GX_SetVtxAttrFmt(GX_VTXFMT0, GX_VA_POS, GX_POS_XY, GX_F32, 0);
    GX_SetVtxAttrFmt(GX_VTXFMT0, GX_VA_CLR0, GX_CLR_RGBA, GX_RGBA8, 0);
    GX_SetVtxAttrFmt(GX_VTXFMT0, GX_VA_TEX0, GX_TEX_ST, GX_F32, 0);
    GX_SetNumChans(1);
    GX_SetChanCtrl(GX_COLOR0A0, GX_DISABLE, GX_SRC_REG, GX_SRC_VTX,
                   GX_LIGHTNULL, GX_DF_NONE, GX_AF_NONE);
    GX_SetTexCoordGen(GX_TEXCOORD0, GX_TG_MTX2x4, GX_TG_TEX0, GX_IDENTITY);
    GX_SetNumTevStages(1);
    GX_InvalidateTexAll();

    font_from_rom();
    if (!g_font_ok) font_from_console();
    g_fk = 24.0f / g_cell_h;
}

void gui_begin(void) {
    g_mode = MODE_NONE;
    g_bound_sheet = -1;
    GX_InvVtxCache();
    GX_InvalidateTexAll();
}

void gui_end(bool vsync) {
    GX_DrawDone();
    GX_CopyDisp(g_xfb[g_fb], GX_TRUE);
    GX_DrawDone();
    VIDEO_SetNextFramebuffer(g_xfb[g_fb]);
    VIDEO_Flush();
    if (vsync) VIDEO_WaitVSync();
    g_fb ^= 1;
}

static void set_mode(DrawMode m) {
    if (g_mode == m) return;
    g_mode = m;
    GX_ClearVtxDesc();
    GX_SetVtxDesc(GX_VA_POS, GX_DIRECT);
    GX_SetVtxDesc(GX_VA_CLR0, GX_DIRECT);
    if (m == MODE_TEX) {
        GX_SetVtxDesc(GX_VA_TEX0, GX_DIRECT);
        GX_SetNumTexGens(1);
        GX_SetTevOrder(GX_TEVSTAGE0, GX_TEXCOORD0, GX_TEXMAP0, GX_COLOR0A0);
        /* colour = vertex colour; alpha = font intensity * vertex alpha */
        GX_SetTevColorIn(GX_TEVSTAGE0, GX_CC_ZERO, GX_CC_ZERO, GX_CC_ZERO, GX_CC_RASC);
        GX_SetTevColorOp(GX_TEVSTAGE0, GX_TEV_ADD, GX_TB_ZERO, GX_CS_SCALE_1, GX_TRUE, GX_TEVPREV);
        GX_SetTevAlphaIn(GX_TEVSTAGE0, GX_CA_ZERO, GX_CA_TEXA, GX_CA_RASA, GX_CA_ZERO);
        GX_SetTevAlphaOp(GX_TEVSTAGE0, GX_TEV_ADD, GX_TB_ZERO, GX_CS_SCALE_1, GX_TRUE, GX_TEVPREV);
    } else {
        GX_SetNumTexGens(0);
        GX_SetTevOrder(GX_TEVSTAGE0, GX_TEXCOORDNULL, GX_TEXMAP_NULL, GX_COLOR0A0);
        GX_SetTevOp(GX_TEVSTAGE0, GX_PASSCLR);
    }
}

/* ------------------------------------------------------------------------ */
/* Shapes                                                                    */
/* ------------------------------------------------------------------------ */

u32 gui_mix(u32 a, u32 b, float t) {
    if (t < 0) t = 0;
    if (t > 1) t = 1;
    int r = (int)(((a >> 16) & 0xFF) * (1 - t) + ((b >> 16) & 0xFF) * t);
    int g = (int)(((a >> 8) & 0xFF) * (1 - t) + ((b >> 8) & 0xFF) * t);
    int bl = (int)((a & 0xFF) * (1 - t) + (b & 0xFF) * t);
    return gui_rgb(((u32)r << 16) | ((u32)g << 8) | (u32)bl);
}

static inline void vtx(float x, float y, u32 c) {
    GX_Position2f32(x, y);
    GX_Color1u32(c);
}

void gui_rect(float x, float y, float w, float h, u32 color) {
    if (w <= 0 || h <= 0) return;
    set_mode(MODE_SOLID);
    GX_Begin(GX_QUADS, GX_VTXFMT0, 4);
    vtx(x, y, color);
    vtx(x + w, y, color);
    vtx(x + w, y + h, color);
    vtx(x, y + h, color);
    GX_End();
}

void gui_vgrad(float x, float y, float w, float h, u32 top, u32 bottom) {
    if (w <= 0 || h <= 0) return;
    set_mode(MODE_SOLID);
    GX_Begin(GX_QUADS, GX_VTXFMT0, 4);
    vtx(x, y, top);
    vtx(x + w, y, top);
    vtx(x + w, y + h, bottom);
    vtx(x, y + h, bottom);
    GX_End();
}

void gui_tri(float x0, float y0, float x1, float y1, float x2, float y2, u32 color) {
    set_mode(MODE_SOLID);
    GX_Begin(GX_TRIANGLES, GX_VTXFMT0, 3);
    vtx(x0, y0, color);
    vtx(x1, y1, color);
    vtx(x2, y2, color);
    GX_End();
}

#define CORNER_SEG 5
static float g_cos[CORNER_SEG + 1], g_sin[CORNER_SEG + 1];
static bool  g_trig_ready;

static void trig_init(void) {
    if (g_trig_ready) return;
    for (int i = 0; i <= CORNER_SEG; i++) {
        float a = (float)i / CORNER_SEG * (float)M_PI / 2;
        g_cos[i] = cosf(a);
        g_sin[i] = sinf(a);
    }
    g_trig_ready = true;
}

void gui_rrect(float x, float y, float w, float h, float r, u32 color) {
    if (w <= 0 || h <= 0) return;
    if (r > w / 2) r = w / 2;
    if (r > h / 2) r = h / 2;
    if (r < 1) { gui_rect(x, y, w, h, color); return; }
    trig_init();
    set_mode(MODE_SOLID);
    /* Convex outline -> one fan from the centre. */
    int n = 4 * (CORNER_SEG + 1) + 2;
    GX_Begin(GX_TRIANGLEFAN, GX_VTXFMT0, n);
    vtx(x + w / 2, y + h / 2, color);
    /* top-right, bottom-right, bottom-left, top-left; clockwise on screen */
    float cx[4] = { x + w - r, x + w - r, x + r, x + r };
    float cy[4] = { y + r, y + h - r, y + h - r, y + r };
    float first_x = 0, first_y = 0;
    for (int c = 0; c < 4; c++) {
        for (int i = 0; i <= CORNER_SEG; i++) {
            float dx, dy;
            switch (c) {
                case 0:  dx =  g_sin[i]; dy = -g_cos[i]; break;   /* up -> right */
                case 1:  dx =  g_cos[i]; dy =  g_sin[i]; break;   /* right -> down */
                case 2:  dx = -g_sin[i]; dy =  g_cos[i]; break;   /* down -> left */
                default: dx = -g_cos[i]; dy = -g_sin[i]; break;   /* left -> up */
            }
            float px = cx[c] + dx * r, py = cy[c] + dy * r;
            if (c == 0 && i == 0) { first_x = px; first_y = py; }
            vtx(px, py, color);
        }
    }
    vtx(first_x, first_y, color);
    GX_End();
}

void gui_circle(float cx, float cy, float r, u32 color) {
    gui_rrect(cx - r, cy - r, r * 2, r * 2, r, color);
}

/* ------------------------------------------------------------------------ */
/* Text                                                                      */
/* ------------------------------------------------------------------------ */

static const char *ellipsis = "...";

/* Decode one UTF-8 sequence to a Latin-1 glyph index ('?' outside). */
static int next_ch(const char *s, int *ch) {
    const u8 *p = (const u8 *)s;
    if (p[0] < 0x80) { *ch = p[0]; return 1; }
    if ((p[0] & 0xE0) == 0xC0 && (p[1] & 0xC0) == 0x80) {
        int cp = ((p[0] & 0x1F) << 6) | (p[1] & 0x3F);
        *ch = cp <= 0xFF ? cp : '?';
        return 2;
    }
    if ((p[0] & 0xF0) == 0xE0 && (p[1] & 0xC0) == 0x80 && (p[2] & 0xC0) == 0x80) {
        int cp = ((p[0] & 0x0F) << 12) | ((p[1] & 0x3F) << 6) | (p[2] & 0x3F);
        /* typographic quotes/dashes the catalog names like to carry */
        if (cp == 0x2018 || cp == 0x2019) *ch = '\'';
        else if (cp == 0x201C || cp == 0x201D) *ch = '"';
        else if (cp == 0x2013 || cp == 0x2014) *ch = '-';
        else if (cp == 0x2022) *ch = 0xB7;
        else *ch = '?';
        return 3;
    }
    if ((p[0] & 0xF8) == 0xF0 && p[1] && p[2] && p[3]) { *ch = '?'; return 4; }
    /* Stray byte: treat as Latin-1 */
    *ch = p[0];
    return 1;
}

static inline float glyph_adv(int ch, float scale) {
    return g_glyph[ch & 0xFF].adv * g_fk * scale;
}

float gui_line_h(float scale) { return 24.0f * scale; }

float gui_text_w(float scale, const char *s) {
    float w = 0;
    if (!s || !g_font_ok) return 0;
    while (*s && *s != '\n') {
        int ch;
        s += next_ch(s, &ch);
        w += glyph_adv(ch, scale);
    }
    return w;
}

float gui_text(float x, float y, float scale, u32 color, GuiAlign align, const char *s) {
    if (!s || !*s || !g_font_ok) return 0;
    float tw = gui_text_w(scale, s);
    if (align == GUI_CENTER) x -= tw / 2;
    else if (align == GUI_RIGHT) x -= tw;
    /* Snap to whole pixels: sub-pixel glyph quads go blurry. */
    x = floorf(x + 0.5f);
    y = floorf(y + 0.5f);

    set_mode(MODE_TEX);
    float k = g_fk * scale, ch_h = g_cell_h * k;
    float pen = x;
    while (*s && *s != '\n') {
        int ch;
        s += next_ch(s, &ch);
        const Glyph *gl = &g_glyph[ch & 0xFF];
        if (ch != ' ') {
            if (g_bound_sheet != gl->sheet) {
                GX_LoadTexObj(&g_sheet[gl->sheet], GX_TEXMAP0);
                g_bound_sheet = gl->sheet;
            }
            /* Sample only the glyph's own columns (advance + 1, inside its
             * cell) and stay half a texel off the cell edges, or bilinear
             * filtering picks up a sliver of the neighbouring glyph. */
            float tw_tex = gl->adv + 1.0f;
            if (tw_tex > g_cell_w) tw_tex = g_cell_w;
            float s0 = gl->u / g_sheet_w, t0 = (gl->v + 0.5f) / g_sheet_h;
            float s1 = (gl->u + tw_tex - 0.5f) / g_sheet_w;
            float t1 = (gl->v + g_cell_h - 0.5f) / g_sheet_h;
            float qw = tw_tex * k;
            GX_Begin(GX_QUADS, GX_VTXFMT0, 4);
            GX_Position2f32(pen, y);             GX_Color1u32(color); GX_TexCoord2f32(s0, t0);
            GX_Position2f32(pen + qw, y);        GX_Color1u32(color); GX_TexCoord2f32(s1, t0);
            GX_Position2f32(pen + qw, y + ch_h); GX_Color1u32(color); GX_TexCoord2f32(s1, t1);
            GX_Position2f32(pen, y + ch_h);      GX_Color1u32(color); GX_TexCoord2f32(s0, t1);
            GX_End();
        }
        pen += glyph_adv(ch, scale);
    }
    return tw;
}

float gui_textf(float x, float y, float scale, u32 color, GuiAlign align, const char *fmt, ...) {
    char buf[256];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    return gui_text(x, y, scale, color, align, buf);
}

/* Longest prefix of s that fits max_w together with an ellipsis. */
static bool fit(const char *s, float scale, float max_w, char *out, size_t size) {
    if (gui_text_w(scale, s) <= max_w) {
        snprintf(out, size, "%s", s);
        char *nl = strchr(out, '\n');
        if (nl) *nl = '\0';
        return false;
    }
    float limit = max_w - gui_text_w(scale, ellipsis);
    float w = 0;
    const char *p = s;
    while (*p && *p != '\n') {
        int ch;
        int n = next_ch(p, &ch);
        float gw = glyph_adv(ch, scale);
        if (w + gw > limit) break;
        w += gw;
        p += n;
    }
    size_t len = (size_t)(p - s);
    while (len > 0 && s[len - 1] == ' ') len--;
    if (len + strlen(ellipsis) + 1 > size) len = size - strlen(ellipsis) - 1;
    memcpy(out, s, len);
    strcpy(out + len, ellipsis);
    return true;
}

float gui_text_fit(float x, float y, float scale, u32 color, GuiAlign align, float max_w, const char *s) {
    if (!s || !*s) return 0;
    if (max_w <= 0) return gui_text(x, y, scale, color, align, s);
    char buf[GUI_WRAP_LINE * 2];
    fit(s, scale, max_w, buf, sizeof(buf));
    return gui_text(x, y, scale, color, align, buf);
}

float gui_text_mid(float x, float y, float h, float scale, u32 color, GuiAlign align, float max_w, const char *s) {
    float ty = y + (h - gui_line_h(scale)) / 2 + scale * 1.5f;
    return gui_text_fit(x, ty, scale, color, align, max_w, s);
}

int gui_wrap(const char *s, float scale, float max_w, char lines[][GUI_WRAP_LINE], int max_lines) {
    int count = 0;
    if (!s || max_lines <= 0) return 0;
    const char *p = s;
    while (*p && count < max_lines) {
        const char *start = p, *cut = NULL, *after_cut = NULL;
        float w = 0;
        while (*p && *p != '\n') {
            int ch;
            int n = next_ch(p, &ch);
            float gw = glyph_adv(ch, scale);
            if (w + gw > max_w && p > start) break;
            if (ch == ' ') { cut = p; after_cut = p + n; }
            w += gw;
            p += n;
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
        fit(tmp, scale, max_w, lines[count - 1], GUI_WRAP_LINE);
    }
    return count;
}

int gui_text_wrap(float x, float y, float scale, u32 color, float max_w, int max_lines, const char *s) {
    char lines[8][GUI_WRAP_LINE];
    if (max_lines > 8) max_lines = 8;
    int n = gui_wrap(s, scale, max_w, lines, max_lines);
    float lh = gui_line_h(scale);
    for (int i = 0; i < n; i++) gui_text(x, y + i * lh, scale, color, GUI_LEFT, lines[i]);
    return n;
}

/* ------------------------------------------------------------------------ */
/* Widgets                                                                   */
/* ------------------------------------------------------------------------ */

float gui_pill_w(float h, float scale, const char *label) {
    return gui_text_w(scale, label) + h * 0.9f;
}

float gui_pill(float x, float y, float h, float scale, u32 bg, u32 fg, const char *label) {
    float pad = h * 0.45f;
    float w = gui_text_w(scale, label) + pad * 2;
    gui_rrect(x, y, w, h, h / 2, bg);
    gui_text_mid(x + pad, y, h, scale, fg, GUI_LEFT, 0, label);
    return w;
}

float gui_pill_outline(float x, float y, float h, float scale, u32 color, u32 fill, const char *label) {
    float pad = h * 0.45f;
    float w = gui_text_w(scale, label) + pad * 2;
    gui_rrect(x, y, w, h, h / 2, color);
    gui_rrect(x + 1.5f, y + 1.5f, w - 3, h - 3, (h - 3) / 2, fill);
    gui_text_mid(x + pad, y, h, scale, color, GUI_LEFT, 0, label);
    return w;
}

/* GameCube controller colours */
#define BTN_A     0x2FB67C   /* big green A */
#define BTN_B     0xE0464E   /* red B */
#define BTN_XY    0xC9D1D9   /* grey kidney buttons */
#define BTN_Z     0x7B61D1   /* purple Z */
#define BTN_SHLD  0x8B9AAB   /* L / R */
#define BTN_START 0xC9D1D9

#define BTN_D 18.0f   /* glyph diameter */

float gui_button_w(const char *b) {
    if (!strcmp(b, "L") || !strcmp(b, "R") || !strcmp(b, "Z")) return 24;
    if (strlen(b) == 1) return BTN_D;
    if (!strcmp(b, "DPAD") || !strcmp(b, "UD") || !strcmp(b, "LR")) return BTN_D;
    return gui_text_w(GUI_S_TINY, b) + 12;
}

float gui_button(float x, float cy, const char *b) {
    u32 ink = gui_rgb(HEX_INK);
    if (!strcmp(b, "L") || !strcmp(b, "R") || !strcmp(b, "Z")) {
        u32 c = gui_rgb(*b == 'Z' ? BTN_Z : BTN_SHLD);
        u32 fg = *b == 'Z' ? gui_rgb(0xFFFFFF) : ink;
        gui_rrect(x, cy - 8, 24, 16, 5, c);
        gui_text_mid(x + 12, cy - 8, 16, GUI_S_TINY, fg, GUI_CENTER, 0, b);
        return 24;
    }
    if (strlen(b) == 1) {
        u32 hex = *b == 'A' ? BTN_A : *b == 'B' ? BTN_B : BTN_XY;
        u32 fg = (*b == 'A' || *b == 'B') ? gui_rgb(0xFFFFFF) : ink;
        float r = BTN_D / 2;
        gui_circle(x + r, cy, r, gui_rgb(hex));
        gui_text_mid(x + r + 0.5f, cy - r, BTN_D, GUI_S_TINY, fg, GUI_CENTER, 0, b);
        return BTN_D;
    }
    if (!strcmp(b, "DPAD") || !strcmp(b, "UD") || !strcmp(b, "LR")) {
        u32 base = gui_rgb(0x4A5B6D), hi = gui_rgb(HEX_TEXT);
        bool ud = !strcmp(b, "UD"), lr = !strcmp(b, "LR"), all = !ud && !lr;
        float s = BTN_D, t = 6;
        /* Base cross, then the arms that matter highlighted on top */
        gui_rect(x + (s - t) / 2, cy - s / 2, t, s, base);
        gui_rect(x, cy - t / 2, s, t, base);
        if (ud || all) {
            gui_rect(x + (s - t) / 2, cy - s / 2, t, (s - t) / 2, hi);
            gui_rect(x + (s - t) / 2, cy + t / 2, t, (s - t) / 2, hi);
        }
        if (lr || all) {
            gui_rect(x, cy - t / 2, (s - t) / 2, t, hi);
            gui_rect(x + (s + t) / 2, cy - t / 2, (s - t) / 2, t, hi);
        }
        return s;
    }
    /* START (and other words) */
    float w = gui_text_w(GUI_S_TINY, b) + 12;
    gui_rrect(x, cy - 8, w, 16, 8, gui_rgb(BTN_START));
    gui_text_mid(x + w / 2, cy - 8, 16, GUI_S_TINY, ink, GUI_CENTER, 0, b);
    return w;
}

void gui_bar(float x, float y, float w, float h, float frac, u32 color) {
    gui_rrect(x, y, w, h, h / 2, gui_rgb(HEX_BG));
    if (frac < 0) {
        /* Busy: soft diagonal stripes over the whole track */
        gui_rrect(x, y, w, h, h / 2, (color & 0xFFFFFF00) | 0x50);
        for (float sx = x + h; sx < x + w - h; sx += h * 1.6f)
            gui_rect(sx, y + 2, h * 0.6f, h - 4, color);
        return;
    }
    if (frac > 1) frac = 1;
    float fw = w * frac;
    if (fw <= 0) return;
    if (fw < h) fw = h;
    gui_rrect(x, y, fw, h, h / 2, color);
    if (fw > h) gui_rect(x + h / 2, y + 2, fw - h, h * 0.3f, gui_rgba(0xFFFFFF, 0x38));
}

void gui_scrollbar(float x, float y, float h, int first, int visible, int total) {
    if (total <= visible || total <= 0) return;
    gui_rrect(x, y, 4, h, 2, gui_rgb(HEX_BG2));
    float th = h * visible / total;
    if (th < 16) th = 16;
    float ty = y + (h - th) * ((float)first / (float)(total - visible));
    if (ty < y) ty = y;
    if (ty > y + h - th) ty = y + h - th;
    gui_rrect(x, ty, 4, th, 2, gui_rgb(HEX_DIM));
}

void gui_panel(float x, float y, float w, float h) {
    gui_rrect(x - 1, y - 1, w + 2, h + 2, 9, gui_rgb(0x0A1118));
    gui_rrect(x, y, w, h, 8, gui_rgb(HEX_PANEL));
}

void gui_card(float x, float y, float w, float h, const char *title, u32 tone) {
    gui_rrect(x + 3, y + 5, w, h, 10, gui_rgba(0x03060A, 0xB0));
    gui_rrect(x - 2, y - 2, w + 4, h + 4, 11, gui_mix(HEX_LINE, tone, 0.45f));
    gui_rrect(x, y, w, h, 9, gui_rgb(HEX_PANEL));
    if (title) {
        gui_rrect(x, y, w, 34, 9, gui_rgb(HEX_PANEL_HI));
        gui_rect(x, y + 20, w, 14, gui_rgb(HEX_PANEL_HI));
        gui_rect(x, y + 34, w, 2, gui_rgb(HEX_LINE));
        gui_rrect(x + 12, y + 10, 5, 14, 2, gui_rgb(tone));
        gui_text_mid(x + 26, y, 34, GUI_S_BODY, gui_rgb(HEX_TEXT), GUI_LEFT, w - 36, title);
    }
}

void gui_dim(void) {
    gui_rect(0, 0, GUI_W, GUI_H, gui_rgba(0x05090D, 0xB8));
}

/* ------------------------------------------------------------------------ */
/* Chrome                                                                    */
/* ------------------------------------------------------------------------ */

void gui_header(const char *section, const SyncState *st) {
    gui_vgrad(0, 0, GUI_W, GUI_HEADER_H, gui_rgb(0x162130), gui_rgb(HEX_BG2));
    gui_rect(0, GUI_HEADER_H - 2, GUI_W, 2, gui_rgb(HEX_LINE));

    float band_y = GUI_SAFE_Y, band_h = GUI_HEADER_H - GUI_SAFE_Y - 2;
    float cy = band_y + band_h / 2;

    /* Logo mark: teal rounded square with a notch */
    gui_rrect(GUI_SAFE_X, cy - 10, 20, 20, 6, gui_rgb(HEX_ACCENT));
    gui_rrect(GUI_SAFE_X + 6, cy - 4, 8, 8, 3, gui_rgb(HEX_BG2));
    float x = GUI_SAFE_X + 28;
    x += gui_text_mid(x, band_y, band_h, 0.86f, gui_rgb(HEX_TEXT), GUI_LEFT, 0, "GameSync");
    if (section && *section) {
        gui_circle(x + 10, cy, 2.5f, gui_rgb(HEX_ACCENT));
        gui_text_mid(x + 20, band_y, band_h, 0.78f, gui_rgb(HEX_ACCENT2), GUI_LEFT, 200, section);
    }

    /* Right: SD pill, network dot + IP, version */
    float r = GUI_SAFE_R;
    char sd[24];
    bool sd_ok = st && st->sd_ready;
    if (sd_ok) snprintf(sd, sizeof(sd), "SD %s", sd_device_to_str(st->sd_device));
    else snprintf(sd, sizeof(sd), "No SD");
    float pw = gui_pill_w(20, GUI_S_TINY, sd);
    gui_pill_outline(r - pw, cy - 10, 20, GUI_S_TINY, gui_rgb(sd_ok ? HEX_OK : HEX_ERR),
                     gui_rgb(HEX_BG2), sd);
    r -= pw + 12;

    bool net_ok = st && st->net_ready && st->dhcp_ok;
    bool link = st && st->net_ready;
    const char *ip = net_ok ? st->ip : link ? "no lease" : "offline";
    u32 dot = net_ok ? HEX_OK : link ? HEX_WARN : HEX_ERR;
    float iw = gui_text_w(GUI_S_SMALL, ip);
    gui_text_mid(r, band_y, band_h, GUI_S_SMALL, gui_rgb(HEX_TEXT), GUI_RIGHT, 0, ip);
    r -= iw + 10;
    gui_circle(r, cy, 6, gui_rgba(dot, 0x50));
    gui_circle(r, cy, 4, gui_rgb(dot));
    r -= 16;
    gui_text_mid(r, band_y, band_h, GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_RIGHT, 0, "v" APP_VERSION);
}

void gui_tabs(const char *const *labels, int count, int active) {
    float y = GUI_TABS_Y, h = GUI_TABS_H;
    float lw = gui_button_w("L"), rw = gui_button_w("R");
    float x0 = GUI_SAFE_X + lw + 6, x1 = GUI_SAFE_R - rw - 6;
    gui_button(GUI_SAFE_X, y + h / 2, "L");
    gui_button(GUI_SAFE_R - rw, y + h / 2, "R");

    float widths[12], total = 0;
    if (count > 12) count = 12;
    for (int i = 0; i < count; i++) {
        widths[i] = gui_text_w(GUI_S_TINY, labels[i]) + 10;
        total += widths[i];
    }
    float extra = (x1 - x0 - 4 - total) / count;
    if (extra < 0) extra = 0;
    gui_rrect(x0, y, x1 - x0, h, h / 2, gui_rgb(HEX_BG2));
    float cx = x0 + 2;
    for (int i = 0; i < count; i++) {
        float w = widths[i] + extra;
        bool on = i == active;
        if (on) gui_rrect(cx, y + 2, w, h - 4, (h - 4) / 2, gui_rgb(HEX_ACCENT));
        gui_text_mid(cx + w / 2, y, h, GUI_S_TINY, gui_rgb(on ? HEX_INK : HEX_DIM),
                     GUI_CENTER, 0, labels[i]);
        cx += w;
    }
}

void gui_footer(const GuiHint *hints, int count) {
    float y = GUI_FOOTER_Y;
    gui_vgrad(0, y, GUI_W, GUI_H - y, gui_rgb(HEX_BG2), gui_rgb(0x141D28));
    gui_rect(0, y, GUI_W, 2, gui_rgb(HEX_LINE));
    if (count <= 0) return;
    float widths[12], total = 0;
    if (count > 12) count = 12;
    for (int i = 0; i < count; i++) {
        widths[i] = gui_button_w(hints[i].button) + 5 + gui_text_w(GUI_S_SMALL, hints[i].label);
        total += widths[i];
    }
    float avail = GUI_SAFE_R - GUI_SAFE_X;
    float gap = (avail - total) / (count > 1 ? count - 1 : 1);
    if (gap > 22) gap = 22;
    if (gap < 6) gap = 6;
    float x = GUI_SAFE_X, band_h = 28, cy = y + 4 + band_h / 2;
    for (int i = 0; i < count; i++) {
        float bw = gui_button(x, cy, hints[i].button);
        gui_text_mid(x + bw + 5, y + 4, band_h, GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_LEFT, 0,
                     hints[i].label);
        x += widths[i] + gap;
    }
}

void gui_banner(float y, const char *text, u32 tone) {
    float x = GUI_SAFE_X, w = GUI_SAFE_R - GUI_SAFE_X, h = GUI_BANNER_H;
    gui_rrect(x, y, w, h, 6, gui_mix(HEX_PANEL, tone, 0.14f));
    gui_rrect(x, y, 5, h, 2, gui_rgb(tone));
    gui_text_mid(x + 14, y, h, GUI_S_SMALL, gui_rgb(HEX_TEXT), GUI_LEFT, w - 24, text);
}
