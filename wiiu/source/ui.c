/*
 * ui.c — GameSync UI kit for the Wii U client, mirrored on TV + GamePad.
 * Drawing goes through gfx.c (software rendering into the OSScreen buffers);
 * this file owns the framebuffers, the ProcUI foreground dance, and every
 * shared widget — header, tabs, footer hints, list rows, detail panel,
 * dialogs, progress and boot cards.  The look follows the 3DS / DS restyle:
 * deep slate background, lighter rounded cards, teal accent, green / amber /
 * blue / red status colours.
 *
 * Framebuffers live in MEM1, which ProcUI reclaims whenever the app drops to
 * the background (HOME menu).  ui_acquire_foreground / ui_release_foreground
 * are registered as ProcUI callbacks so the buffers are torn down and rebuilt
 * around every foreground transition — without this the app hangs on HOME.
 */

#include "ui.h"

#include <stdio.h>
#include <stdarg.h>
#include <string.h>

#include <coreinit/cache.h>
#include <coreinit/memdefaultheap.h>
#include <coreinit/memfrmheap.h>
#include <coreinit/memheap.h>
#include <coreinit/screen.h>
#include <coreinit/time.h>
#include <proc_ui/procui.h>

#define FRM_HEAP_TAG 0x33445353   /* "3DSS" */

/* OSScreenClearBufferEx takes 0xRRGGBBAA. */
#define CLEAR_RGBA ((HEX_BG << 8) | 0xFF)

/* List geometry. */
#define LIST_HEAD_H  50
#define LIST_ROWS_Y  (UI_CONTENT_Y + LIST_HEAD_H)
#define ROW_H        42
#define LIST_ROWS    ((UI_CONTENT_B - 8 - LIST_ROWS_Y) / ROW_H)

static char  g_status[200];
static bool  g_status_err = false;

static void    *g_buf_tv = NULL, *g_buf_drc = NULL;
static uint32_t g_size_tv = 0, g_size_drc = 0;
static bool     g_foreground = false;
static volatile bool g_repaint_needed = false;

bool ui_consume_repaint_request(void) {
    if (!g_repaint_needed) return false;
    g_repaint_needed = false;
    return true;
}

int ui_list_visible(void) { return LIST_ROWS; }

static uint32_t anim_ms(void) {
    return (uint32_t)OSTicksToMilliseconds(OSGetTime());
}

/* ---- ProcUI foreground handling ---- */

uint32_t ui_acquire_foreground(void *ctx) {
    (void)ctx;
    if (g_foreground) return 0;

    MEMHeapHandle heap = MEMGetBaseHeapHandle(MEM_BASE_HEAP_MEM1);
    if (!heap) return 0;
    MEMRecordStateForFrmHeap(heap, FRM_HEAP_TAG);

    OSScreenInit();
    g_size_tv  = OSScreenGetBufferSizeEx(SCREEN_TV);
    g_size_drc = OSScreenGetBufferSizeEx(SCREEN_DRC);

    /* OSScreenSetBufferEx wants 0x100-aligned memory. */
    g_buf_tv  = MEMAllocFromFrmHeapEx(heap, g_size_tv,  0x100);
    g_buf_drc = MEMAllocFromFrmHeapEx(heap, g_size_drc, 0x100);
    if (!g_buf_tv || !g_buf_drc) {
        MEMFreeByStateToFrmHeap(heap, FRM_HEAP_TAG);
        g_buf_tv = g_buf_drc = NULL;
        return 0;
    }

    OSScreenSetBufferEx(SCREEN_TV,  g_buf_tv);
    OSScreenSetBufferEx(SCREEN_DRC, g_buf_drc);
    OSScreenEnableEx(SCREEN_TV,  TRUE);
    OSScreenEnableEx(SCREEN_DRC, TRUE);

    /* Both buffers of each screen are freshly allocated MEM1 holding whatever
     * the previous owner left behind.  Clear and flip twice so neither the
     * visible nor the work buffer can show garbage before the first repaint. */
    for (int i = 0; i < 2; i++) {
        OSScreenClearBufferEx(SCREEN_TV,  CLEAR_RGBA);
        OSScreenClearBufferEx(SCREEN_DRC, CLEAR_RGBA);
        DCFlushRange(g_buf_tv,  g_size_tv);
        DCFlushRange(g_buf_drc, g_size_drc);
        OSScreenFlipBuffersEx(SCREEN_TV);
        OSScreenFlipBuffersEx(SCREEN_DRC);
    }

    g_foreground = true;
    g_repaint_needed = true;
    return 0;
}

uint32_t ui_release_foreground(void *ctx) {
    (void)ctx;
    if (!g_foreground) return 0;

    OSScreenShutdown();
    MEMHeapHandle heap = MEMGetBaseHeapHandle(MEM_BASE_HEAP_MEM1);
    if (heap) MEMFreeByStateToFrmHeap(heap, FRM_HEAP_TAG);
    g_buf_tv = g_buf_drc = NULL;
    g_foreground = false;
    return 0;
}

void ui_init(void) {
    gfx_init();

    ProcUIRegisterCallback(PROCUI_CALLBACK_ACQUIRE, ui_acquire_foreground, NULL, 100);
    ProcUIRegisterCallback(PROCUI_CALLBACK_RELEASE, ui_release_foreground, NULL, 100);
    /* EXIT too: MEM1 must go back before ProcUIShutdown() resets the heap,
     * and the exiting transition does not necessarily fire RELEASE first. */
    ProcUIRegisterCallback(PROCUI_CALLBACK_EXIT, ui_release_foreground, NULL, 100);

    /* ProcUIInit leaves us already in the foreground and does not replay the
     * ACQUIRE callback for that initial state — do it by hand. */
    ui_acquire_foreground(NULL);
}

void ui_shutdown(void) {
    ui_release_foreground(NULL);
}

/* ---- frames ---- */

static void render_screen(GfxScreen s, void *buf, uint32_t size,
                          UiDrawFn fn, void *ctx) {
    if (gfx_begin(s, buf, size)) {
        fn(ctx);
        return;
    }
    /* Unrecognised framebuffer layout: say so with OSScreen's own font
     * rather than leaving the screen black. */
    OSScreenID id = s == GFX_TV ? SCREEN_TV : SCREEN_DRC;
    OSScreenClearBufferEx(id, CLEAR_RGBA);
    OSScreenPutFontEx(id, 0, 0, "GameSync: framebuffer layout not recognised");
    OSScreenPutFontEx(id, 0, 1, "Status:");
    OSScreenPutFontEx(id, 0, 2, g_status);
}

void ui_render(UiDrawFn fn, void *ctx) {
    if (!g_foreground || !fn) return;
    render_screen(GFX_TV,  g_buf_tv,  g_size_tv,  fn, ctx);
    render_screen(GFX_DRC, g_buf_drc, g_size_drc, fn, ctx);
    DCFlushRange(g_buf_tv,  g_size_tv);
    DCFlushRange(g_buf_drc, g_size_drc);
    OSScreenFlipBuffersEx(SCREEN_TV);
    OSScreenFlipBuffersEx(SCREEN_DRC);
}

/* ---- status line ---- */

void ui_status(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(g_status, sizeof(g_status), fmt, ap);
    va_end(ap);
    g_status_err = false;
}

void ui_error(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(g_status, sizeof(g_status), fmt, ap);
    va_end(ap);
    g_status_err = true;
}

const char *ui_status_text(void) { return g_status; }
bool ui_status_is_error(void) { return g_status_err; }

/* ---- small helpers ---- */

void ui_human_size(uint64_t b, char *o, size_t n) {
    if (b >= (1ULL << 30))
        snprintf(o, n, "%llu.%llu GB", (unsigned long long)(b >> 30),
                 (unsigned long long)(((b & ((1ULL << 30) - 1)) * 10) >> 30));
    else if (b >= (1ULL << 20))
        snprintf(o, n, "%llu.%llu MB", (unsigned long long)(b >> 20),
                 (unsigned long long)(((b & ((1ULL << 20) - 1)) * 10) >> 20));
    else if (b >= (1ULL << 10))
        snprintf(o, n, "%llu KB", (unsigned long long)(b >> 10));
    else
        snprintf(o, n, "%llu B", (unsigned long long)b);
}

const char *ui_view_name(AppView view) {
    switch (view) {
        case APP_VIEW_ROMS:      return "Game Catalog";
        case APP_VIEW_LOCAL:     return "Installed Games";
        case APP_VIEW_DOWNLOADS: return "Downloads";
        case APP_VIEW_GCCARDS:   return "GC Memory Cards";
        case APP_VIEW_SERVER:    return "Server GC Saves";
        case APP_VIEW_VWII:      return "vWii Saves";
        case APP_VIEW_WIIU:      return "Wii U Saves";
        case APP_VIEW_CONFIG:    return "Settings";
        default:                 return "?";
    }
}

static const char *view_tab(AppView view) {
    switch (view) {
        case APP_VIEW_ROMS:      return "Catalog";
        case APP_VIEW_LOCAL:     return "Installed";
        case APP_VIEW_DOWNLOADS: return "Downloads";
        case APP_VIEW_GCCARDS:   return "GC Cards";
        case APP_VIEW_SERVER:    return "GC Server";
        case APP_VIEW_VWII:      return "vWii";
        case APP_VIEW_WIIU:      return "Wii U";
        case APP_VIEW_CONFIG:    return "Settings";
        default:                 return "?";
    }
}

/* ---- widgets ---- */

int ui_pill_w(int h, int size, const char *label) {
    return gfx_text_w(size, label) + h;   /* h/2 padding each side */
}

int ui_pill(int x, int y, int h, int size, uint32_t bg, uint32_t fg, const char *label) {
    int w = ui_pill_w(h, size, label);
    gfx_rrect(x, y, w, h, h / 2, bg);
    gfx_text_mid(x + h / 2, y, h, size, fg, GFX_LEFT, 0, label);
    return w;
}

/* Face buttons keep the SNES-style colours the other clients use. */
static uint32_t face_color(const char *b) {
    if (!strcmp(b, "A")) return HEX_ERR;
    if (!strcmp(b, "B")) return HEX_WARN;
    if (!strcmp(b, "X")) return HEX_INFO;
    if (!strcmp(b, "Y")) return HEX_OK;
    return 0;
}

#define BTN_R 14

int ui_button_w(const char *b) {
    if (face_color(b) || !strcmp(b, "+") || !strcmp(b, "-")) return BTN_R * 2;
    if (!strcmp(b, "DPAD") || !strcmp(b, "UD") || !strcmp(b, "LR")) return 28;
    return gfx_text_w(UI_S_TINY, b) + 20;
}

static void dpad(int x, int cy, const char *b) {
    int c = x + 14;
    gfx_rrect(c - 4, cy - 13, 9, 27, 2, HEX_MUTED);
    gfx_rrect(c - 13, cy - 4, 27, 9, 2, HEX_MUTED);
    if (!strcmp(b, "UD")) {
        gfx_tri(c, cy - 8, 8, 0, HEX_TEXT);
        gfx_tri(c, cy + 8, 8, 1, HEX_TEXT);
    } else if (!strcmp(b, "LR")) {
        gfx_rect(c - 12, cy - 2, 6, 5, HEX_TEXT);
        gfx_rect(c + 7, cy - 2, 6, 5, HEX_TEXT);
    }
}

int ui_button(int x, int cy, const char *b) {
    uint32_t fc = face_color(b);
    if (fc) {
        gfx_circle(x + BTN_R, cy, BTN_R, fc);
        gfx_text_mid(x + BTN_R, cy - BTN_R, BTN_R * 2, UI_S_TINY, HEX_INK,
                     GFX_CENTER, 0, b);
        return BTN_R * 2;
    }
    if (!strcmp(b, "+") || !strcmp(b, "-")) {
        gfx_circle(x + BTN_R, cy, BTN_R, HEX_MUTED);
        gfx_rect(x + BTN_R - 7, cy - 1, 15, 3, HEX_TEXT);
        if (b[0] == '+') gfx_rect(x + BTN_R - 1, cy - 7, 3, 15, HEX_TEXT);
        return BTN_R * 2;
    }
    if (!strcmp(b, "DPAD") || !strcmp(b, "UD") || !strcmp(b, "LR")) {
        dpad(x, cy, b);
        return 28;
    }
    /* Shoulder / named buttons: rounded tab with the label. */
    int w = ui_button_w(b);
    gfx_rrect(x, cy - 13, w, 26, 8, HEX_MUTED);
    gfx_text_mid(x + w / 2, cy - 13, 26, UI_S_TINY, HEX_TEXT, GFX_CENTER, 0, b);
    return w;
}

void ui_bar(int x, int y, int w, int h, int permille, uint32_t color) {
    gfx_rrect(x, y, w, h, h / 2, HEX_LINE);
    if (permille < 0) {
        /* Indeterminate: a segment sliding across the trough. */
        int seg = w / 4;
        int span = w + seg;
        int pos = (int)(anim_ms() / 3 % (uint32_t)span) - seg;
        int a = pos < 0 ? 0 : pos;
        int b = pos + seg > w ? w : pos + seg;
        if (b - a > 0) gfx_rrect(x + a, y, b - a, h, h / 2, color);
        return;
    }
    if (permille > 1000) permille = 1000;
    int fw = (int)((int64_t)w * permille / 1000);
    if (fw > 0 && fw < h) fw = h;   /* keep the rounded ends visible */
    if (fw > 0) gfx_rrect(x, y, fw, h, h / 2, color);
}

void ui_icon(int cx, int cy, int r, UiIcon icon, uint32_t color) {
    switch (icon) {
        case UI_ICON_NONE: return;
        case UI_ICON_DOT:
            gfx_circle(cx, cy, r / 2, color);
            return;
        case UI_ICON_UNKNOWN:
            gfx_circle(cx, cy, r, color);
            gfx_circle(cx, cy, r - 2, HEX_BG2);
            gfx_text_mid(cx, cy - r, r * 2, UI_S_TINY, color, GFX_CENTER, 0, "?");
            return;
        default: break;
    }
    gfx_circle(cx, cy, r, color);
    int s = r;
    switch (icon) {
        case UI_ICON_OK:    gfx_check(cx, cy - 1, s, HEX_INK); break;
        case UI_ICON_UP:
            gfx_tri(cx, cy - 2, s, 0, HEX_INK);
            gfx_rect(cx - 2, cy, 5, s / 2, HEX_INK);
            break;
        case UI_ICON_DOWN:
            gfx_tri(cx, cy + 2, s, 1, HEX_INK);
            gfx_rect(cx - 2, cy - s / 2, 5, s / 2, HEX_INK);
            break;
        case UI_ICON_ALERT:
            gfx_rect(cx - 2, cy - s / 2 - 1, 5, s * 2 / 3, HEX_INK);
            gfx_rect(cx - 2, cy + s / 3, 5, 4, HEX_INK);
            break;
        case UI_ICON_PLUS:
            gfx_rect(cx - s / 2, cy - 2, s, 5, HEX_INK);
            gfx_rect(cx - 2, cy - s / 2, 5, s, HEX_INK);
            break;
        default: break;
    }
}

/* ---- chrome ---- */

void ui_draw_background(void) {
    gfx_vgrad(0, 0, GFX_W, GFX_H, HEX_BG, 0x141F2B);
}

/* Dot + label chip ending at x_right; returns its left edge. */
static int status_chip(int x_right, int cy, uint32_t dot, const char *label) {
    int tw = gfx_text_w(UI_S_SMALL, label);
    int x = x_right - tw;
    gfx_text_mid(x, cy - 15, 30, UI_S_SMALL, HEX_DIM, GFX_LEFT, 0, label);
    gfx_circle(x - 12, cy, 6, dot);
    return x - 24;
}

static void header_bar(const SyncState *st, const char *section) {
    gfx_vgrad(0, 0, GFX_W, UI_HEADER_H, HEX_BG2, 0x18222E);

    /* Logo: teal rounded square with a dark core, like the other clients. */
    gfx_rrect(24, 16, 32, 32, 9, HEX_ACCENT);
    gfx_rrect(33, 25, 14, 14, 4, HEX_BG2);

    int x = 70;
    int tw = gfx_text_mid(x, 0, UI_HEADER_H, UI_S_TITLE, HEX_TEXT, GFX_LEFT, 0, "GameSync");
    gfx_text_mid(x + 1, 0, UI_HEADER_H, UI_S_TITLE, HEX_TEXT, GFX_LEFT, 0, "GameSync");
    x += tw + 18;
    gfx_circle(x, UI_HEADER_H / 2, 4, HEX_ACCENT);
    x += 16;
    gfx_text_mid(x, 0, UI_HEADER_H, UI_S_HEAD, HEX_ACCENT2, GFX_LEFT, 420, section);

    int cy = UI_HEADER_H / 2;
    int xr = GFX_W - 24;
    xr -= gfx_text_mid(xr, 0, UI_HEADER_H, UI_S_SMALL, HEX_MUTED, GFX_RIGHT, 0,
                       "v" APP_VERSION) + 26;
    if (st) {
        xr = status_chip(xr, cy, st->mocha_ok ? HEX_OK : HEX_MUTED, "NAND") - 10;
        xr = status_chip(xr, cy, st->sd_ready ? HEX_OK : HEX_ERR, "SD") - 10;
        status_chip(xr, cy, st->net_ready ? HEX_OK : HEX_ERR,
                    st->net_ready ? st->ip : "Offline");
    }
}

void ui_draw_header_plain(const SyncState *st, const char *section) {
    header_bar(st, section);
    gfx_rect(0, UI_HEADER_H, GFX_W, 1, HEX_LINE);
}

void ui_draw_header(const SyncState *st, AppView view) {
    header_bar(st, ui_view_name(view));

    /* View tabs: ZL ... ZR around the eight views. */
    int y = UI_HEADER_H;
    gfx_rect(0, y, GFX_W, UI_TABS_H, 0x152029);
    gfx_rect(0, y + UI_TABS_H - 1, GFX_W, 1, HEX_LINE);
    int cy = y + UI_TABS_H / 2;

    int pad = 18, gap = 6;
    int total = 0;
    for (int v = 0; v < APP_VIEW_COUNT; v++)
        total += gfx_text_w(UI_S_SMALL, view_tab((AppView)v)) + pad * 2 + gap;
    total -= gap;
    int zl = ui_button_w("ZL"), zr = ui_button_w("ZR");
    int x = (GFX_W - total) / 2;
    ui_button(x - zl - 16, cy, "ZL");
    ui_button(x + total + 16, cy, "ZR");
    (void)zr;

    for (int v = 0; v < APP_VIEW_COUNT; v++) {
        const char *lab = view_tab((AppView)v);
        int w = gfx_text_w(UI_S_SMALL, lab) + pad * 2;
        if (v == (int)view) {
            gfx_rrect(x, cy - 16, w, 32, 16, HEX_ACCENT);
            gfx_text_mid(x + w / 2, cy - 16, 32, UI_S_SMALL, HEX_INK, GFX_CENTER, 0, lab);
        } else {
            gfx_text_mid(x + w / 2, cy - 16, 32, UI_S_SMALL, HEX_DIM, GFX_CENTER, 0, lab);
        }
        x += w + gap;
    }
}

static void footer_bar(const UiHint *hints, int count);

void ui_draw_footer(const UiHint *hints, int count) {
    /* Status banner. */
    if (g_status[0]) {
        uint32_t tone = g_status_err ? HEX_ERR : HEX_ACCENT;
        gfx_rrect(UI_LIST_X, UI_BANNER_Y, GFX_W - UI_LIST_X * 2, UI_BANNER_H, 10,
                  g_status_err ? 0x3A2329 : HEX_PANEL);
        gfx_rrect(UI_LIST_X, UI_BANNER_Y, 6, UI_BANNER_H, 3, tone);
        ui_icon(UI_LIST_X + 30, UI_BANNER_Y + UI_BANNER_H / 2, 11,
                g_status_err ? UI_ICON_ALERT : UI_ICON_DOT, tone);
        gfx_text_mid(UI_LIST_X + 52, UI_BANNER_Y, UI_BANNER_H, UI_S_SMALL,
                     g_status_err ? 0xFFB4AE : HEX_TEXT, GFX_LEFT,
                     GFX_W - UI_LIST_X * 2 - 70, g_status);
    }
    footer_bar(hints, count);
}

/* Footer bar with button hints. */
static void footer_bar(const UiHint *hints, int count) {
    gfx_rect(0, UI_FOOTER_Y, GFX_W, UI_FOOTER_H, HEX_BG2);
    gfx_rect(0, UI_FOOTER_Y, GFX_W, 1, HEX_LINE);
    int cy = UI_FOOTER_Y + UI_FOOTER_H / 2;
    int x = 24;
    for (int i = 0; i < count; i++) {
        if (!hints[i].button) continue;
        x += ui_button(x, cy, hints[i].button) + 8;
        x += gfx_text_mid(x, UI_FOOTER_Y, UI_FOOTER_H, UI_S_SMALL, HEX_TEXT,
                          GFX_LEFT, 0, hints[i].label) + 26;
    }
}

/* ---- list panel ---- */

static int list_panel_base(void) {
    int h = UI_CONTENT_B - UI_CONTENT_Y;
    gfx_rrect(UI_LIST_X, UI_CONTENT_Y, UI_LIST_W, h, 14, HEX_BG2);
    gfx_rect(UI_LIST_X + 14, UI_CONTENT_Y + LIST_HEAD_H - 4, UI_LIST_W - 28, 1, HEX_LINE);
    return UI_CONTENT_Y;
}

void ui_list_panel(const char *title, const char *right) {
    int y = list_panel_base();
    int rw = 0;
    if (right && right[0])
        rw = gfx_text_mid(UI_LIST_X + UI_LIST_W - 18, y, LIST_HEAD_H - 4, UI_S_SMALL,
                          HEX_DIM, GFX_RIGHT, 0, right);
    if (title && title[0])
        gfx_text_mid(UI_LIST_X + 18, y, LIST_HEAD_H - 4, UI_S_SMALL, HEX_TEXT,
                     GFX_LEFT, UI_LIST_W - rw - 56, title);
}

void ui_list_panel_tabs(const char *const *tabs, int count, int active,
                        const char *right) {
    int y = list_panel_base();
    int cy = y + (LIST_HEAD_H - 4) / 2;
    int x = UI_LIST_X + 14;
    for (int i = 0; i < count; i++) {
        int w = ui_pill_w(30, UI_S_SMALL, tabs[i]) + 8;
        if (i == active) {
            gfx_rrect(x, cy - 15, w, 30, 15, HEX_ACCENT);
            gfx_text_mid(x + w / 2, cy - 15, 30, UI_S_SMALL, HEX_INK, GFX_CENTER, 0, tabs[i]);
        } else {
            gfx_text_mid(x + w / 2, cy - 15, 30, UI_S_SMALL, HEX_DIM, GFX_CENTER, 0, tabs[i]);
        }
        x += w + 4;
    }
    if (right && right[0])
        gfx_text_mid(UI_LIST_X + UI_LIST_W - 18, y, LIST_HEAD_H - 4, UI_S_SMALL,
                     HEX_DIM, GFX_RIGHT, UI_LIST_X + UI_LIST_W - 18 - x - 10, right);
}

void ui_list_row(int slot, bool sel, const UiRow *r) {
    if (slot < 0 || slot >= LIST_ROWS) return;
    int y = LIST_ROWS_Y + slot * ROW_H;
    int x0 = UI_LIST_X + 10;
    int w = UI_LIST_W - 20 - 16;      /* room for the scrollbar */
    if (sel) gfx_rrect(x0, y + 2, w, ROW_H - 4, 10, HEX_ACCENT);

    uint32_t fg = sel ? HEX_INK : (r->text_color ? r->text_color : HEX_TEXT);
    int x = x0 + 12;

    if (r->icon != UI_ICON_NONE) {
        uint32_t ic = r->icon_color ? r->icon_color : HEX_DIM;
        if (sel && r->icon == UI_ICON_DOT) ic = HEX_INK;
        ui_icon(x + 11, y + ROW_H / 2, 11, r->icon, ic);
        x += 32;
    }
    if (r->tag && r->tag[0]) {
        uint32_t tc = r->tag_color ? r->tag_color : HEX_DIM;
        x += ui_pill(x, y + (ROW_H - 26) / 2, 26, UI_S_TINY,
                     sel ? HEX_INK : gfx_mix(HEX_BG2, tc, 70),
                     sel ? tc : gfx_mix(tc, HEX_TEXT, 60), r->tag) + 10;
    }

    int xr = x0 + w - 12;
    if (r->badge && r->badge[0]) {
        uint32_t bc = r->badge_color ? r->badge_color : HEX_DIM;
        int bw = ui_pill_w(26, UI_S_TINY, r->badge);
        xr -= bw;
        ui_pill(xr, y + (ROW_H - 26) / 2, 26, UI_S_TINY,
                sel ? HEX_INK : gfx_mix(HEX_BG2, bc, 64),
                sel ? bc : gfx_mix(bc, HEX_TEXT, 40), r->badge);
        xr -= 12;
    }
    if (r->right && r->right[0]) {
        int rw = gfx_text_mid(xr, y, ROW_H, UI_S_SMALL, sel ? HEX_INK : HEX_DIM,
                              GFX_RIGHT, 0, r->right);
        xr -= rw + 16;
    }

    int text_w = xr - x;
    gfx_text_mid(x, y, ROW_H, UI_S_BODY, fg, GFX_LEFT, text_w,
                 r->text ? r->text : "");

    if (r->permille >= 0) {
        int bx = x, bw = (x0 + w - 12) - x;
        ui_bar(bx, y + ROW_H - 7, bw, 5, r->permille,
               sel ? HEX_INK : HEX_ACCENT);
    }
}

void ui_list_empty(const char *title, const char *hint) {
    int cy = LIST_ROWS_Y + (LIST_ROWS * ROW_H) / 2 - 40;
    int cx = UI_LIST_X + UI_LIST_W / 2;
    gfx_circle(cx, cy - 10, 26, HEX_PANEL);
    gfx_circle(cx, cy - 10, 9, HEX_MUTED);
    if (title)
        gfx_text_fit(cx, cy + 32, UI_S_BODY, HEX_TEXT, GFX_CENTER, UI_LIST_W - 60, title);
    if (hint)
        gfx_text_wrap_align(cx, cy + 72, UI_S_SMALL, HEX_DIM, GFX_CENTER,
                            UI_LIST_W - 120, 3, hint);
}

void ui_list_scrollbar(int first, int visible, int total) {
    if (total <= visible || total <= 0) return;
    int x = UI_LIST_X + UI_LIST_W - 18;
    int y = LIST_ROWS_Y + 4;
    int h = LIST_ROWS * ROW_H - 8;
    gfx_rrect(x, y, 6, h, 3, HEX_LINE);
    int th = h * visible / total;
    if (th < 24) th = 24;
    int ty = y + (int)((int64_t)(h - th) * first / (total - visible));
    gfx_rrect(x, ty, 6, th, 3, HEX_ACCENT);
}

/* ---- detail panel ---- */

static int g_dy = 0;         /* next free y inside the detail panel */
static int g_pill_x = 0;     /* > 0 while a pill line is open */

#define DET_PAD   22
#define DET_IN_X  (UI_DETAIL_X + DET_PAD)
#define DET_IN_W  (UI_DETAIL_W - DET_PAD * 2)
#define DET_LABEL 128

static void close_pills(void) {
    if (g_pill_x > 0) { g_dy += 30 + 14; g_pill_x = 0; }
}

static bool det_room(int h) {
    return g_dy + h <= UI_CONTENT_B - 14;
}

void ui_detail_begin(const char *title) {
    int h = UI_CONTENT_B - UI_CONTENT_Y;
    gfx_rrect(UI_DETAIL_X, UI_CONTENT_Y, UI_DETAIL_W, h, 14, HEX_PANEL);
    g_dy = UI_CONTENT_Y + 18;
    g_pill_x = 0;
    if (title && title[0]) {
        int n = gfx_text_wrap(DET_IN_X, g_dy, UI_S_HEAD, HEX_TEXT, DET_IN_W, 3, title);
        g_dy += n * (UI_S_HEAD + UI_S_HEAD / 4) + 10;
    }
}

void ui_detail_pill(const char *label, uint32_t color) {
    if (!label || !label[0]) return;
    int w = ui_pill_w(30, UI_S_TINY, label);
    if (g_pill_x == 0) g_pill_x = DET_IN_X;
    else if (g_pill_x + w > DET_IN_X + DET_IN_W) { close_pills(); g_pill_x = DET_IN_X; }
    if (!det_room(30)) return;
    ui_pill(g_pill_x, g_dy, 30, UI_S_TINY, gfx_mix(HEX_PANEL, color, 72),
            gfx_mix(color, HEX_TEXT, 50), label);
    g_pill_x += w + 8;
}

void ui_detail_field(const char *label, const char *fmt, ...) {
    close_pills();
    char val[256];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(val, sizeof(val), fmt, ap);
    va_end(ap);
    int vw = DET_IN_W - DET_LABEL;
    int lines = gfx_wrap_count(UI_S_SMALL, vw, 2, val);
    int lh = UI_S_SMALL + UI_S_SMALL / 4;
    if (!det_room(lines * lh)) return;
    gfx_text_fit(DET_IN_X, g_dy, UI_S_SMALL, HEX_DIM, GFX_LEFT, DET_LABEL - 10, label);
    gfx_text_wrap(DET_IN_X + DET_LABEL, g_dy, UI_S_SMALL, HEX_TEXT, vw, 2, val);
    g_dy += lines * lh + 6;
}

void ui_detail_note(uint32_t color, const char *fmt, ...) {
    close_pills();
    char val[320];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(val, sizeof(val), fmt, ap);
    va_end(ap);
    int lh = UI_S_SMALL + UI_S_SMALL / 4;
    int room = (UI_CONTENT_B - 14 - g_dy) / lh;
    if (room <= 0) return;
    if (room > 5) room = 5;
    int n = gfx_text_wrap(DET_IN_X, g_dy, UI_S_SMALL, color ? color : HEX_DIM,
                          DET_IN_W, room, val);
    g_dy += n * lh + 8;
}

void ui_detail_gap(int px) {
    close_pills();
    g_dy += px;
}

void ui_detail_progress(uint64_t done, uint64_t total, uint32_t color) {
    close_pills();
    if (!det_room(16 + 34)) return;
    int pm = total ? (int)(done * 1000 / total) : -1;
    ui_bar(DET_IN_X, g_dy, DET_IN_W, 14, pm, color);
    g_dy += 22;
    char a[24], b[24], line[64];
    ui_human_size(done, a, sizeof(a));
    ui_human_size(total, b, sizeof(b));
    snprintf(line, sizeof(line), "%s / %s", a, b);
    gfx_text(DET_IN_X, g_dy, UI_S_SMALL, HEX_TEXT, GFX_LEFT, total ? line : a);
    if (total) {
        char pct[8];
        snprintf(pct, sizeof(pct), "%d%%", pm / 10);
        gfx_text(DET_IN_X + DET_IN_W, g_dy, UI_S_SMALL, color, GFX_RIGHT, pct);
    }
    g_dy += UI_S_SMALL + 14;
}

void ui_detail_status(UiIcon icon, uint32_t color, const char *title, const char *sub) {
    close_pills();
    int h = (sub && sub[0]) ? 72 : 50;
    if (!det_room(h)) return;
    gfx_rrect(DET_IN_X, g_dy, DET_IN_W, h, 10, HEX_PANEL_HI);
    gfx_rrect(DET_IN_X, g_dy, 6, h, 3, color);
    ui_icon(DET_IN_X + 34, g_dy + h / 2, 15, icon, color);
    int tx = DET_IN_X + 62, tw = DET_IN_W - 74;
    if (sub && sub[0]) {
        gfx_text_fit(tx, g_dy + 10, UI_S_BODY, color, GFX_LEFT, tw, title);
        gfx_text_fit(tx, g_dy + 42, UI_S_TINY, HEX_DIM, GFX_LEFT, tw, sub);
    } else {
        gfx_text_mid(tx, g_dy, h, UI_S_BODY, color, GFX_LEFT, tw, title);
    }
    g_dy += h + 14;
}

void ui_detail_action(const char *button, const char *label) {
    close_pills();
    if (!det_room(32)) return;
    int x = DET_IN_X;
    x += ui_button(x, g_dy + 15, button) + 10;
    gfx_text_mid(x, g_dy, 30, UI_S_SMALL, HEX_TEXT, GFX_LEFT,
                 DET_IN_X + DET_IN_W - x, label);
    g_dy += 38;
}

/* ---- modal cards ---- */

void ui_dim(void) {
    gfx_rect_a(0, 0, GFX_W, GFX_H, 0x060A0F, 170);
}

/* Card frame with a title strip; returns the y below the strip. */
static int card(int x, int y, int w, int h, const char *title, uint32_t tone) {
    gfx_rrect(x - 2, y - 2, w + 4, h + 4, 18, HEX_LINE);
    gfx_rrect(x, y, w, h, 16, HEX_PANEL);
    gfx_rrect(x + 22, y + 19, 6, 26, 3, tone);
    gfx_text_mid(x + 40, y, 64, UI_S_BODY, HEX_TEXT, GFX_LEFT, w - 64, title);
    gfx_rect(x + 18, y + 63, w - 36, 1, HEX_LINE);
    return y + 64;
}

void ui_dialog(const char *title, uint32_t tone, const char *message,
               const UiHint *hints, int count) {
    ui_dim();
    int w = 780;
    int lh = UI_S_BODY + UI_S_BODY / 4;
    int lines = gfx_wrap_count(UI_S_BODY, w - 64, 9, message);
    int h = 64 + 24 + lines * lh + 20 + 58;
    int x = (GFX_W - w) / 2, y = (GFX_H - h) / 2;
    int by = card(x, y, w, h, title, tone);
    gfx_text_wrap(x + 32, by + 22, UI_S_BODY, HEX_TEXT, w - 64, 9, message);

    /* Hints right-aligned along the bottom. */
    int total = 0;
    for (int i = 0; i < count; i++)
        total += ui_button_w(hints[i].button) + 8 +
                 gfx_text_w(UI_S_SMALL, hints[i].label) + 26;
    int hx = x + w - 24 - total + 26;
    int cy = y + h - 32;
    for (int i = 0; i < count; i++) {
        hx += ui_button(hx, cy, hints[i].button) + 8;
        hx += gfx_text_mid(hx, cy - 15, 30, UI_S_SMALL, HEX_TEXT, GFX_LEFT, 0,
                           hints[i].label) + 26;
    }
}

void ui_progress_card(const UiProgress *p) {
    ui_dim();
    int w = 820, h = 330;
    int x = (GFX_W - w) / 2, y = (GFX_H - h) / 2;
    int by = card(x, y, w, h, p->title ? p->title : "Working", HEX_ACCENT);
    int ix = x + 32, iw = w - 64;
    int cy = by + 18;

    gfx_text_fit(ix, cy, UI_S_HEAD, HEX_TEXT, GFX_LEFT, iw, p->name ? p->name : "");
    cy += UI_S_HEAD + 10;
    if (p->detail && p->detail[0])
        gfx_text_fit(ix, cy, UI_S_SMALL, HEX_DIM, GFX_LEFT, iw, p->detail);
    cy += UI_S_SMALL + 18;

    int pm = p->total ? (int)(p->done * 1000 / p->total) : -1;
    ui_bar(ix, cy, iw, 18, pm, HEX_ACCENT);
    cy += 30;

    char left[64] = "", right[16] = "";
    if (p->total) {
        if (p->bytes) {
            char a[24], b[24];
            ui_human_size(p->done, a, sizeof(a));
            ui_human_size(p->total, b, sizeof(b));
            snprintf(left, sizeof(left), "%s / %s", a, b);
        } else {
            snprintf(left, sizeof(left), "%llu / %llu",
                     (unsigned long long)p->done, (unsigned long long)p->total);
        }
        snprintf(right, sizeof(right), "%d%%", pm / 10);
    } else if (p->bytes && p->done) {
        ui_human_size(p->done, left, sizeof(left));
    }
    gfx_text(ix, cy, UI_S_BODY, HEX_TEXT, GFX_LEFT, left);
    gfx_text(ix + iw, cy, UI_S_BODY, HEX_ACCENT2, GFX_RIGHT, right);
    cy += UI_S_BODY + 12;

    if (p->bps) {
        char spd[24], line[96];
        ui_human_size(p->bps, spd, sizeof(spd));
        uint64_t eta = (p->total > p->done) ? (p->total - p->done) / p->bps : 0;
        if (p->total)
            snprintf(line, sizeof(line), "Speed %s/s     Remaining %llu:%02llu",
                     spd, (unsigned long long)(eta / 60), (unsigned long long)(eta % 60));
        else
            snprintf(line, sizeof(line), "Speed %s/s", spd);
        gfx_text(ix, cy, UI_S_SMALL, HEX_DIM, GFX_LEFT, line);
    }

    if (p->hint_button) {
        int hy = y + h - 32;
        int hx = ix;
        gfx_text_mid(hx, hy - 15, 30, UI_S_SMALL, HEX_DIM, GFX_LEFT, 0, "Press");
        hx += gfx_text_w(UI_S_SMALL, "Press") + 10;
        hx += ui_button(hx, hy, p->hint_button) + 10;
        gfx_text_mid(hx, hy - 15, 30, UI_S_SMALL, HEX_DIM, GFX_LEFT, 0,
                     p->hint_label ? p->hint_label : "");
    }
}

void ui_draw_boot(const char *const *steps, int count, int current, const char *note) {
    ui_draw_background();
    ui_draw_header_plain(NULL, "Starting");

    int w = 700;
    int lh = 40;
    int note_lines = (note && note[0]) ? gfx_wrap_count(UI_S_SMALL, w - 64, 4, note) : 0;
    int h = 64 + 20 + count * lh + 16 + note_lines * (UI_S_SMALL + 6) + 24;
    int x = (GFX_W - w) / 2, y = UI_HEADER_H + (UI_FOOTER_Y - UI_HEADER_H - h) / 2;
    int by = card(x, y, w, h, "Starting GameSync " APP_VERSION, HEX_ACCENT);

    int cy = by + 20;
    for (int i = 0; i < count; i++) {
        int mid = cy + lh / 2;
        uint32_t col;
        if (i < current) {
            ui_icon(x + 48, mid, 12, UI_ICON_OK, HEX_OK);
            col = HEX_DIM;
        } else if (i == current) {
            ui_icon(x + 48, mid, 12, UI_ICON_DOT, HEX_ACCENT);
            gfx_circle(x + 48, mid, 12, gfx_mix(HEX_PANEL, HEX_ACCENT, 80));
            gfx_circle(x + 48, mid, 6, HEX_ACCENT);
            col = HEX_TEXT;
        } else {
            gfx_circle(x + 48, mid, 5, HEX_MUTED);
            col = HEX_MUTED;
        }
        gfx_text_mid(x + 76, cy, lh, UI_S_BODY, col, GFX_LEFT, w - 110, steps[i]);
        cy += lh;
    }
    if (note_lines)
        gfx_text_wrap(x + 32, cy + 16, UI_S_SMALL, HEX_WARN, w - 64, 4, note);

    /* No status banner here: the card itself is the status. */
    static const UiHint hints[] = { { "HOME", "Exit" } };
    footer_bar(hints, 1);
    gfx_text_mid(GFX_W - 24, UI_FOOTER_Y, UI_FOOTER_H, UI_S_TINY, HEX_MUTED,
                 GFX_RIGHT, 0, "UDP log on port 4405");
}

void ui_draw_editor(const char *label, const char *buf, int cur, char up, char down) {
    int w = 980, h = 300;
    int x = (GFX_W - w) / 2, y = UI_CONTENT_Y + 30;
    int by = card(x, y, w, h, label, HEX_ACCENT);

    /* Input box. */
    int fx = x + 32, fw = w - 64, fy = by + 70, fh = 64;
    gfx_rrect(fx, fy, fw, fh, 10, HEX_BG);
    gfx_rrect(fx, fy + fh - 3, fw, 3, 1, HEX_ACCENT);

    int len = (int)strlen(buf);
    if (cur > len) cur = len;
    if (cur < 0) cur = 0;

    /* Scroll horizontally so the caret stays inside the box. */
    int start = 0;
    char tmp[256];
    for (;;) {
        int n = cur - start;
        if (n < 0) n = 0;
        if (n > (int)sizeof(tmp) - 1) n = (int)sizeof(tmp) - 1;
        memcpy(tmp, buf + start, (size_t)n);
        tmp[n] = '\0';
        if (gfx_text_w(UI_S_HEAD, tmp) < fw - 80 || start >= cur) break;
        start++;
    }
    gfx_clip(fx + 12, fy, fw - 24, fh);
    int tx = fx + 16;
    int ty = fy + (fh - UI_S_HEAD) / 2;
    tx += gfx_text(tx, ty, UI_S_HEAD, HEX_TEXT, GFX_LEFT, tmp);

    /* Caret cell: the character under the cursor (or a blank at the end). */
    char ch[2] = { cur < len ? buf[cur] : ' ', '\0' };
    int cw = gfx_text_w(UI_S_HEAD, ch[0] == ' ' ? "M" : ch) + 8;
    gfx_rrect(tx, fy + 10, cw, fh - 20, 6, HEX_ACCENT);
    gfx_text(tx + 4, ty, UI_S_HEAD, HEX_INK, GFX_LEFT, ch);
    int caret_x = tx + cw / 2;
    if (cur < len)
        gfx_text(tx + cw, ty, UI_S_HEAD, HEX_TEXT, GFX_LEFT, buf + cur + 1);
    gfx_unclip();

    /* Picker: what Up / Down would put in the caret cell. */
    char u[2] = { up, '\0' }, d[2] = { down, '\0' };
    gfx_tri(caret_x - 30, fy - 24, 14, 0, HEX_ACCENT);
    gfx_text(caret_x, fy - 38, UI_S_SMALL, HEX_DIM, GFX_CENTER, up == ' ' ? "space" : u);
    gfx_tri(caret_x - 30, fy + fh + 22, 14, 1, HEX_ACCENT);
    gfx_text(caret_x, fy + fh + 8, UI_S_SMALL, HEX_DIM, GFX_CENTER, down == ' ' ? "space" : d);

    char info[48];
    snprintf(info, sizeof(info), "%d characters", len);
    gfx_text(x + w - 32, y + h - 44, UI_S_TINY, HEX_MUTED, GFX_RIGHT, info);
}
