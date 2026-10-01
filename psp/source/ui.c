/*
 * PSP Save Sync - screens (see ui.h)
 *
 * Layout (480x272): 24 px header, a list panel on the left (10 rows), a
 * detail panel on the right, 20 px footer with button hints.  Modal
 * cards (confirm, messages, busy) sit over a dimmed snapshot of the last
 * view.  Mirrors the 3DS/DS restyle; the Vita client is the same layout
 * at twice the size.
 */

#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include <pspctrl.h>
#include <pspkernel.h>

#include "gui.h"
#include "ui.h"

/* List panel */
#define LIST_X      6
#define LIST_Y      29
#define LIST_W      282
#define LIST_H      219
#define LIST_HEAD_H 22
#define ROW_H       18
#define ROWS_Y      (LIST_Y + LIST_HEAD_H + 1)
#define ROWS_X      (LIST_X + 4)
#define ROWS_W      (LIST_W - 14)

/* Detail panel */
#define DET_X  (LIST_X + LIST_W + 6)
#define DET_Y  LIST_Y
#define DET_W  (GUI_W - DET_X - 6)
#define DET_H  LIST_H
#define DET_IX (DET_X + 9)
#define DET_IW (DET_W - 18)
#define FIELD_LABEL_W 56   /* label column of label / value rows */

static bool g_online = false;

/* A view frame is on screen and has not been captured for a modal yet */
static bool g_view_shown = false;
static bool g_have_snapshot = false;

static struct {
    char     text[128];
    UiTone   tone;
    uint32_t until;
} g_toast;

/* Smooth selection bar / scrolling, one per view */
typedef struct {
    float bar, first;
    int   total;
    bool  init;
} ListAnim;
static ListAnim g_anim[APP_VIEW_COUNT];

static const char *const VIEW_TITLES[APP_VIEW_COUNT] = {
    "Saves", "Catalog", "Downloads", "Settings"
};

/* ================================================================== */
/* Helpers                                                             */
/* ================================================================== */

static uint32_t tone_hex(UiTone t) {
    switch (t) {
        case UI_TONE_OK:   return HEX_OK;
        case UI_TONE_WARN: return HEX_WARN;
        case UI_TONE_ERR:  return HEX_ERR;
        default:           return HEX_ACCENT;
    }
}

static void format_size(uint64_t bytes, char *out, size_t n) {
    const double kb = 1024.0, mb = kb * 1024, gb = mb * 1024;
    if (bytes >= (uint64_t)gb)
        snprintf(out, n, "%.2f GB", bytes / gb);
    else if (bytes >= (uint64_t)(100 * mb))
        snprintf(out, n, "%.0f MB", bytes / mb);
    else if (bytes >= (uint64_t)mb)
        snprintf(out, n, "%.1f MB", bytes / mb);
    else if (bytes >= 1024)
        snprintf(out, n, "%.0f KB", bytes / kb);
    else
        snprintf(out, n, "%u B", (unsigned)bytes);
}

static void format_bps(uint64_t bps, char *out, size_t n) {
    if (bps == 0) { snprintf(out, n, "--"); return; }
    format_size(bps, out, n);
    strncat(out, "/s", n - strlen(out) - 1);
}

static void format_eta(uint64_t remaining, uint64_t bps, char *out, size_t n) {
    if (bps == 0 || remaining == 0) { snprintf(out, n, "--"); return; }
    unsigned s = (unsigned)(remaining / bps);
    if (s >= 3600)
        snprintf(out, n, "%u:%02u:%02u", s / 3600, (s % 3600) / 60, s % 60);
    else
        snprintf(out, n, "%u:%02u", s / 60, s % 60);
}

/* "2024-01-15T14:30:00..." -> "2024-01-15 14:30" */
static void format_date(const char *iso, char *out, size_t n) {
    if (!iso || !iso[0]) { snprintf(out, n, "--"); return; }
    if (strlen(iso) >= 16 && iso[10] == 'T')
        snprintf(out, n, "%.10s %.5s", iso, iso + 11);
    else
        snprintf(out, n, "%.16s", iso);
}

static int percent(uint64_t off, uint64_t tot) {
    if (tot == 0) return 0;
    int p = (int)((off * 100ULL) / tot);
    return p > 100 ? 100 : p;
}

/* Wait until no buttons are held, so a press from the previous screen
 * doesn't trigger a choice on this one. */
static void drain_buttons(void) {
    SceCtrlData pad;
    do {
        sceCtrlReadBufferPositive(&pad, 1);
        sceKernelDelayThread(16000);
    } while (pad.Buttons != 0);
}

/* Block until one of `mask` is pressed; returns the button. */
static uint32_t wait_buttons(uint32_t mask) {
    drain_buttons();
    SceCtrlData pad;
    uint32_t prev = 0;
    while (1) {
        sceCtrlReadBufferPositive(&pad, 1);
        uint32_t just = pad.Buttons & ~prev;
        prev = pad.Buttons;
        if (just & mask) return just & mask;
        sceKernelDelayThread(16000);
    }
}

/* Button glyph followed by a label; returns the width used. */
static float hint(float x, float cy, const char *button, const char *label, uint32_t hex) {
    float w = gui_button(x, cy, button) + 4;
    w += gui_text_mid(x + w, cy - 8, 16, F_SMALL, gui_rgb(hex), GUI_LEFT, 0, label);
    return w;
}

static float hint_w(const char *button, const char *label) {
    return gui_button_w(button) + 4 + gui_text_w(F_SMALL, label);
}

/* Label / value pair in the detail panel; returns the next y. */
static float field(float y, const char *label, const char *value) {
    gui_text(DET_IX, y, F_SMALL, gui_rgb(HEX_MUTED), GUI_LEFT, label);
    gui_text_fit(DET_IX + FIELD_LABEL_W, y, F_SMALL, gui_rgb(HEX_TEXT), GUI_LEFT, DET_IW - FIELD_LABEL_W, value);
    return y + 14;
}

static float system_pill(float x, float y, const char *system) {
    bool ps1 = system && (!strcmp(system, "PS1") || !strcmp(system, "PSX"));
    return gui_pill(x, y, 12, F_SMALL, gui_rgb(ps1 ? HEX_PS1 : HEX_PSP),
                    gui_rgb(0xFFFFFF), ps1 ? "PS1" : "PSP");
}

/* ================================================================== */
/* Frame chrome                                                        */
/* ================================================================== */

static void draw_toast(void) {
    if (!g_toast.text[0] || gui_ticks() >= g_toast.until) return;
    uint32_t left = g_toast.until - gui_ticks();
    uint8_t a = left < 20 ? (uint8_t)(left * 12) : 0xF0;
    float w = gui_text_w(F_BODY, g_toast.text) + 30;
    if (w > GUI_W - 40) w = GUI_W - 40;
    float x = (GUI_W - w) / 2, y = GUI_FOOTER_Y - 30;
    uint32_t hex = tone_hex(g_toast.tone);
    gui_rrect(x + 1, y + 2, w, 22, 6, gui_rgba(0x000000, a / 2));
    gui_rrect(x, y, w, 22, 6, gui_rgba(HEX_PANEL_HI, a));
    gui_rrect(x + 6, y + 6, 4, 10, 2, gui_rgba(hex, a));
    gui_text_mid(x + 16, y, 22, F_BODY, gui_rgba(HEX_TEXT, a), GUI_LEFT, w - 24, g_toast.text);
}

static void view_begin(AppView view) {
    gui_begin();
    gui_vgrad(0, GUI_HEADER_H, GUI_W, GUI_FOOTER_Y - GUI_HEADER_H,
              gui_rgb(HEX_BG), gui_rgb(0x131D28));
    gui_header(VIEW_TITLES, APP_VIEW_COUNT, (int)view);
}

static void view_end(const GuiHint *hints, int count, bool vsync) {
    draw_toast();
    gui_footer(hints, count);
    gui_end(vsync);
    g_view_shown = true;
}

static void list_panel(const char *title) {
    gui_panel(LIST_X, LIST_Y, LIST_W, LIST_H);
    gui_rect(LIST_X, LIST_Y + LIST_HEAD_H, LIST_W, 1, gui_rgb(HEX_LINE));
    if (title)
        gui_text_mid(LIST_X + 10, LIST_Y, LIST_HEAD_H, F_BOLD, gui_rgb(HEX_TEXT), GUI_LEFT, 0, title);
}

/* Bottom status line inside the list panel */
static void list_status(const char *left, const char *right) {
    float y = ROWS_Y + UI_LIST_ROWS * ROW_H + 2;
    float h = LIST_Y + LIST_H - y;
    if (left)
        gui_text_mid(LIST_X + 10, y, h, F_SMALL, gui_rgb(HEX_DIM), GUI_LEFT, LIST_W - 80, left);
    if (right)
        gui_text_mid(LIST_X + LIST_W - 10, y, h, F_SMALL, gui_rgb(HEX_DIM), GUI_RIGHT, 0, right);
}

static void list_empty(const char *line1, const char *line2, uint32_t hex) {
    float cy = ROWS_Y + UI_LIST_ROWS * ROW_H / 2.0f;
    gui_ring(LIST_X + LIST_W / 2.0f, cy - 26, 11, 2, gui_rgb(hex));
    gui_rect(LIST_X + LIST_W / 2.0f - 1, cy - 32, 2, 7, gui_rgb(hex));
    gui_rect(LIST_X + LIST_W / 2.0f - 1, cy - 23, 2, 2, gui_rgb(hex));
    gui_text(LIST_X + LIST_W / 2.0f, cy - 6, F_BOLD, gui_rgb(HEX_TEXT), GUI_CENTER, line1);
    if (line2) {
        char lines[3][GUI_WRAP_LINE];
        int n = gui_wrap(line2, F_SMALL, LIST_W - 40, lines, 3);
        for (int i = 0; i < n; i++)
            gui_text(LIST_X + LIST_W / 2.0f, cy + 12 + i * 13, F_SMALL, gui_rgb(HEX_DIM),
                     GUI_CENTER, lines[i]);
    }
}

/* Advance the list animation; returns false when the list is empty. */
static bool list_anim(AppView view, int selected, int scroll, int total) {
    ListAnim *a = &g_anim[view];
    if (total <= 0) { a->init = false; return false; }
    float d = a->bar - selected;
    if (!a->init || a->total != total || d > UI_LIST_ROWS || d < -UI_LIST_ROWS) {
        a->bar = (float)selected;
        a->first = (float)scroll;
        a->total = total;
        a->init = true;
    }
    gui_ease(&a->bar, (float)selected, 0.4f);
    gui_ease(&a->first, (float)scroll, 0.4f);
    return true;
}

/* Clip to the rows area and draw the selection bar.  Row i is at
 * row_y(view, i). */
static float row_y(AppView view, int i) {
    return ROWS_Y + (i - g_anim[view].first) * ROW_H;
}

static void rows_begin(AppView view) {
    gui_clip(LIST_X, ROWS_Y, LIST_W, UI_LIST_ROWS * ROW_H);
    float y = ROWS_Y + (g_anim[view].bar - g_anim[view].first) * ROW_H;
    gui_rrect(ROWS_X, y + 1, ROWS_W, ROW_H - 2, 4, gui_rgb(HEX_ACCENT));
}

static void rows_end(AppView view, int total) {
    gui_unclip();
    gui_scrollbar(LIST_X + LIST_W - 6, ROWS_Y + 2, UI_LIST_ROWS * ROW_H - 4,
                  UI_LIST_ROWS, total, g_anim[view].first);
}

/* First and last row index worth drawing this frame */
static void rows_range(AppView view, int total, int *first, int *last) {
    int f = (int)g_anim[view].first - 1;
    if (f < 0) f = 0;
    int l = (int)g_anim[view].first + UI_LIST_ROWS + 1;
    if (l > total) l = total;
    *first = f;
    *last = l;
}

/* Is row i under the (moving) selection bar? */
static bool row_selected(AppView view, int i, int selected) {
    float d = g_anim[view].bar - i;
    return i == selected && d > -0.5f && d < 0.5f;
}

/* ================================================================== */
/* Modal plumbing                                                      */
/* ================================================================== */

static void splash_background(void) {
    gui_vgrad(0, 0, GUI_W, GUI_H, gui_rgb(0x15212E), gui_rgb(HEX_BG));
    float cx = GUI_W / 2.0f;
    gui_rrect(cx - 20, 48, 40, 40, 11, gui_rgb(HEX_ACCENT));
    gui_rrect(cx - 9, 59, 18, 18, 5, gui_rgb(0x15212E));
    gui_text(cx, 98, F_HUGE, gui_rgb(HEX_TEXT), GUI_CENTER, "GameSync");
    gui_text(cx, 134, F_SMALL, gui_rgb(HEX_DIM), GUI_CENTER, "PSP client  \xC2\xB7  v" APP_VERSION);
}

/* Start a modal frame: the last view (dimmed) or the splash behind;
 * cards dim the splash too, the busy line sits on it. */
static void modal_begin(bool card) {
    if (g_view_shown) {
        gui_snapshot();
        g_have_snapshot = true;
        g_view_shown = false;
    }
    gui_begin();
    if (g_have_snapshot) {
        gui_draw_snapshot();
        gui_dim();
    } else {
        splash_background();
        if (card) gui_dim();
    }
}

/* ================================================================== */
/* Public: setup, busy, toast, messages                                */
/* ================================================================== */

void ui_init(void) {
    gui_init();
}

void ui_set_online(bool online) {
    g_online = online;
    gui_set_server_state(online ? 1 : 0);
}

void ui_status(const char *fmt, ...) {
    char buf[192];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);

    modal_begin(false);
    if (!g_have_snapshot) {
        gui_spinner(GUI_W / 2.0f, 186, 9, HEX_ACCENT);
        gui_text_fit(GUI_W / 2.0f, 206, F_BODY, gui_rgb(HEX_DIM), GUI_CENTER, GUI_W - 40, buf);
    } else {
        float w = 300, h = 60, x = (GUI_W - w) / 2, y = (GUI_H - h) / 2;
        gui_card(x, y, w, h, NULL, HEX_ACCENT);
        gui_spinner(x + 28, y + h / 2, 9, HEX_ACCENT);
        char lines[2][GUI_WRAP_LINE];
        int n = gui_wrap(buf, F_BODY, w - 64, lines, 2);
        float ty = y + (h - n * gui_line_h(F_BODY)) / 2;
        for (int i = 0; i < n; i++)
            gui_text(x + 50, ty + i * gui_line_h(F_BODY), F_BODY, gui_rgb(HEX_TEXT), GUI_LEFT, lines[i]);
    }
    gui_end(false);
}

void ui_toast(UiTone tone, const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(g_toast.text, sizeof(g_toast.text), fmt, ap);
    va_end(ap);
    g_toast.tone = tone;
    g_toast.until = gui_ticks() + 150;
}

void ui_message(UiTone tone, const char *title, const char *fmt, ...) {
    char buf[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);

    char lines[8][GUI_WRAP_LINE];
    float w = 340;
    int n = gui_wrap(buf, F_BODY, w - 28, lines, 8);
    int lh = gui_line_h(F_BODY);
    float h = 22 + 12 + n * lh + 34;
    float x = (GUI_W - w) / 2, y = (GUI_H - h) / 2;

    modal_begin(true);
    gui_card(x, y, w, h, title, tone_hex(tone));
    for (int i = 0; i < n; i++)
        gui_text(x + 14, y + 32 + i * lh, F_BODY, gui_rgb(HEX_TEXT), GUI_LEFT, lines[i]);
    float bw = hint_w("CROSS", "OK");
    hint(x + w - 14 - bw, y + h - 16, "CROSS", "OK", HEX_DIM);
    gui_end(true);

    wait_buttons(PSP_CTRL_CROSS | PSP_CTRL_CIRCLE);
}

static uint32_t button_bit(const char *b) {
    if (!strcmp(b, "CROSS"))    return PSP_CTRL_CROSS;
    if (!strcmp(b, "CIRCLE"))   return PSP_CTRL_CIRCLE;
    if (!strcmp(b, "SQUARE"))   return PSP_CTRL_SQUARE;
    if (!strcmp(b, "TRIANGLE")) return PSP_CTRL_TRIANGLE;
    return 0;
}

/* Hints right-aligned at the bottom of a card, Circle last */
static void card_hints(float x, float w, float cy, const char *const *buttons,
                       const char *const *labels, int count, const char *cancel) {
    float hx = x + w - 14;
    if (cancel) {
        hx -= hint_w("CIRCLE", cancel);
        hint(hx, cy, "CIRCLE", cancel, HEX_DIM);
        hx -= 12;
    }
    for (int i = count - 1; i >= 0; i--) {
        hx -= hint_w(buttons[i], labels[i]);
        hint(hx, cy, buttons[i], labels[i], i == 0 ? HEX_TEXT : HEX_DIM);
        hx -= 12;
    }
}

uint32_t ui_ask(UiTone tone, const char *title,
                const char *const *buttons, const char *const *labels, int count,
                const char *fmt, ...) {
    char buf[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);

    char lines[8][GUI_WRAP_LINE];
    float w = 380;
    int n = gui_wrap(buf, F_BODY, w - 28, lines, 8);
    int lh = gui_line_h(F_BODY);
    float h = 22 + 12 + n * lh + 34;
    float x = (GUI_W - w) / 2, y = (GUI_H - h) / 2;

    modal_begin(true);
    gui_card(x, y, w, h, title, tone_hex(tone));
    for (int i = 0; i < n; i++)
        gui_text(x + 14, y + 32 + i * lh, F_BODY, gui_rgb(HEX_TEXT), GUI_LEFT, lines[i]);
    card_hints(x, w, y + h - 16, buttons, labels, count, "Cancel");
    gui_end(true);

    uint32_t mask = PSP_CTRL_CIRCLE;
    for (int i = 0; i < count; i++) mask |= button_bit(buttons[i]);
    return wait_buttons(mask);
}

void ui_details(const char *title, const char *const *labels,
                const char *const *values, int count) {
    if (count > 12) count = 12;
    /* Values may wrap onto a second line */
    char wrapped[12][2][GUI_WRAP_LINE];
    int nl[12];
    float w = 420, vw = w - 28 - 70;
    float h = 22 + 10 + 30;
    for (int i = 0; i < count; i++) {
        nl[i] = gui_wrap(values[i] ? values[i] : "", F_SMALL, vw, wrapped[i], 2);
        if (nl[i] < 1) { nl[i] = 1; wrapped[i][0][0] = '\0'; }
        h += nl[i] * 13 + 3;
    }
    if (h > GUI_H - 8) h = GUI_H - 8;
    float x = (GUI_W - w) / 2, y = (GUI_H - h) / 2;

    modal_begin(true);
    gui_card(x, y, w, h, title, HEX_ACCENT);
    float ry = y + 30;
    for (int i = 0; i < count && ry < y + h - 40; i++) {
        gui_text(x + 14, ry, F_SMALL, gui_rgb(HEX_MUTED), GUI_LEFT, labels[i]);
        for (int k = 0; k < nl[i]; k++)
            gui_text(x + 14 + 70, ry + k * 13, F_SMALL, gui_rgb(HEX_TEXT), GUI_LEFT, wrapped[i][k]);
        ry += nl[i] * 13 + 3;
    }
    float bw = hint_w("CIRCLE", "Close");
    hint(x + w - 14 - bw, y + h - 16, "CIRCLE", "Close", HEX_DIM);
    gui_end(true);

    wait_buttons(PSP_CTRL_CROSS | PSP_CTRL_CIRCLE | PSP_CTRL_TRIANGLE);
}

void ui_fatal(const char *title, const char *fmt, ...) {
    char buf[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);

    gui_begin();
    gui_header(NULL, 0, -1);
    char lines[10][GUI_WRAP_LINE];
    float w = 420;
    int n = gui_wrap(buf, F_BODY, w - 28, lines, 10);
    int lh = gui_line_h(F_BODY);
    float h = 22 + 12 + n * lh + 10;
    float x = (GUI_W - w) / 2, y = GUI_HEADER_H + (GUI_FOOTER_Y - GUI_HEADER_H - h) / 2;
    gui_card(x, y, w, h, title, HEX_ERR);
    for (int i = 0; i < n; i++)
        gui_text(x + 14, y + 32 + i * lh, F_BODY, gui_rgb(HEX_TEXT), GUI_LEFT, lines[i]);
    gui_footer(NULL, 0);
    gui_text_mid(GUI_W / 2.0f, GUI_FOOTER_Y, GUI_FOOTER_H, F_SMALL, gui_rgb(HEX_DIM),
                 GUI_CENTER, 0, "Press HOME to exit");
    gui_end(true);
}

/* ================================================================== */
/* Confirm dialog                                                      */
/* ================================================================== */

/* One side of the comparison: label, big value, small detail */
static void compare_box(float x, float y, float w, float h, const char *label,
                        const char *value, const char *detail, bool present) {
    gui_rrect(x, y, w, h, 5, gui_rgb(HEX_BG2));
    gui_text(x + 9, y + 6, F_SMALL, gui_rgb(HEX_MUTED), GUI_LEFT, label);
    gui_text_fit(x + 9, y + 20, F_TITLE, gui_rgb(present ? HEX_TEXT : HEX_MUTED), GUI_LEFT, w - 18, value);
    gui_text_fit(x + 9, y + h - 17, F_SMALL, gui_rgb(HEX_DIM), GUI_LEFT, w - 18, detail);
}

int ui_confirm(const TitleInfo *title, SyncAction action, bool allow_upload,
               const char *server_hash, uint32_t server_size,
               const char *server_last_sync) {
    const char *heading, *explain;
    uint32_t tone;
    switch (action) {
        case SYNC_UPLOAD:
            heading = "Upload to server";
            explain = "The save on this PSP replaces the server copy.";
            tone = HEX_WARN;
            break;
        case SYNC_DOWNLOAD:
            heading = "Download from server";
            explain = "The server copy replaces the save on this PSP.";
            tone = HEX_INFO;
            break;
        case SYNC_CONFLICT:
            heading = "Conflict";
            explain = allow_upload
                    ? "Both copies changed since the last sync. Pick a side with "
                      "Square (upload) or Triangle (download)."
                    : "Both copies changed since the last sync. Triangle "
                      "downloads the server copy.";
            tone = HEX_ERR;
            break;
        case SYNC_UP_TO_DATE:
            heading = "Up to date";
            explain = "Both copies match. Nothing to do.";
            tone = HEX_OK;
            break;
        default:
            heading = "Can't compare";
            explain = "The save couldn't be read or the server didn't answer.";
            tone = HEX_ERR;
            break;
    }
    bool can_confirm = (action == SYNC_UPLOAD || action == SYNC_DOWNLOAD);

    float w = 384, h = 186, x = (GUI_W - w) / 2, y = (GUI_H - h) / 2;
    modal_begin(true);
    gui_card(x, y, w, h, heading, tone);

    const char *name = title->name[0] ? title->name : title->game_id;
    gui_text_fit(x + 14, y + 30, F_BOLD, gui_rgb(HEX_TEXT), GUI_LEFT, w - 28, name);
    float px = x + 14;
    px += system_pill(px, y + 49, title->is_psx ? "PS1" : "PSP") + 6;
    gui_text_mid(px, y + 49, 12, F_SMALL, gui_rgb(HEX_DIM), GUI_LEFT, 0, title->game_id);

    /* This PSP vs server */
    float by = y + 68, bh = 62, bw = (w - 28 - 34) / 2;
    char local_val[32], local_det[48], srv_val[32], srv_det[48];
    bool local_present = !title->server_only;
    if (local_present) {
        format_size(title->total_size, local_val, sizeof(local_val));
        snprintf(local_det, sizeof(local_det), "%d file%s", title->file_count,
                 title->file_count == 1 ? "" : "s");
    } else {
        snprintf(local_val, sizeof(local_val), "No save");
        snprintf(local_det, sizeof(local_det), "Not on this PSP yet");
    }
    bool srv_present = server_hash && server_hash[0];
    if (srv_present) {
        format_size(server_size, srv_val, sizeof(srv_val));
        format_date(server_last_sync, srv_det, sizeof(srv_det));
    } else {
        snprintf(srv_val, sizeof(srv_val), "No save");
        snprintf(srv_det, sizeof(srv_det), "Nothing uploaded yet");
    }
    compare_box(x + 14, by, bw, bh, "THIS PSP", local_val, local_det, local_present);
    compare_box(x + w - 14 - bw, by, bw, bh, "SERVER", srv_val, srv_det, srv_present);

    /* Direction badge between the boxes */
    float cx = x + w / 2, cy = by + bh / 2;
    gui_circle(cx, cy, 12, gui_rgb(tone));
    uint32_t ink = gui_rgb(HEX_INK);
    if (action == SYNC_UPLOAD) {
        gui_tri(cx + 6, cy, cx - 2, cy - 6, cx - 2, cy + 6, ink);
        gui_rect(cx - 7, cy - 2, 6, 4, ink);
    } else if (action == SYNC_DOWNLOAD) {
        gui_tri(cx - 6, cy, cx + 2, cy - 6, cx + 2, cy + 6, ink);
        gui_rect(cx + 1, cy - 2, 6, 4, ink);
    } else if (action == SYNC_UP_TO_DATE) {
        gui_icon_check(cx, cy, 13, ink);
    } else {
        gui_rect(cx - 1.5f, cy - 7, 3, 9, ink);
        gui_rect(cx - 1.5f, cy + 4, 3, 3, ink);
    }

    gui_text_wrap(x + 14, by + bh + 8, F_SMALL, gui_rgb(HEX_DIM), w - 28, 2, explain);

    /* Cross takes the suggested direction; Square / Triangle force one
     * (the other direction, or either side of a conflict). */
    bool can_force = (action != SYNC_UP_TO_DATE && action != SYNC_FAILED);
    bool offer_up = can_force && allow_upload && action != SYNC_UPLOAD;
    bool offer_down = can_force && action != SYNC_DOWNLOAD;
    const char *buttons[3] = {NULL}, *labels[3] = {NULL};
    int n = 0;
    if (can_confirm) { buttons[n] = "CROSS"; labels[n++] = "Confirm"; }
    if (offer_up)    { buttons[n] = "SQUARE"; labels[n++] = "Upload"; }
    if (offer_down)  { buttons[n] = "TRIANGLE"; labels[n++] = "Download"; }
    card_hints(x, w, y + h - 15, buttons, labels, n, n ? "Cancel" : "Close");
    gui_end(true);

    uint32_t mask = PSP_CTRL_CIRCLE;
    if (can_confirm) mask |= PSP_CTRL_CROSS;
    if (offer_up)    mask |= PSP_CTRL_SQUARE;
    if (offer_down)  mask |= PSP_CTRL_TRIANGLE;
    if (n == 0)      mask |= PSP_CTRL_CROSS;   /* Cross also closes */
    uint32_t b = wait_buttons(mask);
    if ((b & PSP_CTRL_CROSS) && can_confirm) return (int)action;
    if (b & PSP_CTRL_SQUARE)   return SYNC_UPLOAD;
    if (b & PSP_CTRL_TRIANGLE) return SYNC_DOWNLOAD;
    return -1;
}

void ui_sync_summary(const SyncSummary *s) {
    struct { const char *label; int value; uint32_t hex; } tiles[5] = {
        {"Uploaded",   s->uploaded,   HEX_WARN},
        {"Downloaded", s->downloaded, HEX_INFO},
        {"Up to date", s->up_to_date, HEX_OK},
        {"Conflicts",  s->conflicts,  HEX_ERR},
        {"Failed",     s->failed,     HEX_ERR},
    };
    bool clean = s->conflicts == 0 && s->failed == 0;
    float tw = 66, th = 58, gap = 6;
    float w = 5 * tw + 4 * gap + 28, h = 22 + 14 + th + 44;
    float x = (GUI_W - w) / 2, y = (GUI_H - h) / 2;

    modal_begin(true);
    gui_card(x, y, w, h, "Sync all finished", clean ? HEX_OK : HEX_WARN);
    for (int i = 0; i < 5; i++) {
        float tx = x + 14 + i * (tw + gap), ty = y + 34;
        bool lit = tiles[i].value > 0;
        gui_rrect(tx, ty, tw, th, 5, gui_rgb(HEX_BG2));
        gui_rect(tx + 8, ty, tw - 16, 2, gui_rgb(lit ? tiles[i].hex : HEX_LINE));
        gui_textf(tx + tw / 2, ty + 6, F_HUGE, gui_rgb(lit ? tiles[i].hex : HEX_MUTED),
                  GUI_CENTER, "%d", tiles[i].value);
        gui_text(tx + tw / 2, ty + th - 16, F_SMALL, gui_rgb(HEX_DIM), GUI_CENTER, tiles[i].label);
    }
    if (s->conflicts > 0)
        gui_text_fit(x + 14, y + h - 23, F_SMALL, gui_rgb(HEX_DIM), GUI_LEFT, w - 120,
                     "Open a conflict with Cross to choose a side.");
    float bw = hint_w("CROSS", "Continue");
    hint(x + w - 14 - bw, y + h - 15, "CROSS", "Continue", HEX_DIM);
    gui_end(true);

    wait_buttons(PSP_CTRL_CROSS | PSP_CTRL_CIRCLE);
}

/* ================================================================== */
/* Saves view                                                          */
/* ================================================================== */

void ui_draw_saves(const SyncState *state, int selected, int scroll) {
    const AppView V = APP_VIEW_SAVES;
    int total = state->num_titles;
    view_begin(V);

    /* List */
    list_panel("Saves");
    int n_psp = 0, n_ps1 = 0, n_srv = 0;
    for (int i = 0; i < total; i++) {
        if (state->titles[i].is_psx) n_ps1++; else n_psp++;
        if (state->titles[i].server_only) n_srv++;
    }
    char buf[64];
    float tx = LIST_X + LIST_W - 10;
    snprintf(buf, sizeof(buf), "PS1 %d", n_ps1);
    tx -= gui_pill_w(14, F_SMALL, buf);
    gui_pill_outline(tx, LIST_Y + 4, 14, F_SMALL, HEX_PS1, buf);
    snprintf(buf, sizeof(buf), "PSP %d", n_psp);
    tx -= 4 + gui_pill_w(14, F_SMALL, buf);
    gui_pill_outline(tx, LIST_Y + 4, 14, F_SMALL, HEX_PSP, buf);

    if (list_anim(V, selected, scroll, total)) {
        rows_begin(V);
        int first, last;
        rows_range(V, total, &first, &last);
        for (int i = first; i < last; i++) {
            const TitleInfo *t = &state->titles[i];
            float y = row_y(V, i);
            bool sel = row_selected(V, i, selected);
            float x = ROWS_X + 6;
            x += system_pill(x, y + 3, t->is_psx ? "PS1" : "PSP") + 7;
            float right = ROWS_X + ROWS_W - 6;
            if (t->server_only) {
                float pw = gui_pill_w(12, F_SMALL, "SERVER");
                right -= pw;
                if (sel) gui_pill(right, y + 3, 12, F_SMALL, gui_rgb(HEX_INK), gui_rgb(HEX_ACCENT2), "SERVER");
                else gui_pill_outline(right, y + 3, 12, F_SMALL, HEX_INFO, "SERVER");
                right -= 6;
            }
            const char *name = (t->name[0] && strcmp(t->name, t->game_id) != 0) ? t->name : t->game_id;
            gui_text_mid(x, y, ROW_H, F_BODY, gui_rgb(sel ? HEX_INK : HEX_TEXT), GUI_LEFT, right - x, name);
        }
        rows_end(V, total);
    } else {
        list_empty("No saves", "No PSP or PS1 saves on this Memory Stick or the server.", HEX_DIM);
    }
    snprintf(buf, sizeof(buf), "%d saves  \xC2\xB7  %d only on the server", total, n_srv);
    char pos[24];
    snprintf(pos, sizeof(pos), "%d / %d", total ? selected + 1 : 0, total);
    list_status(buf, pos);

    /* Detail */
    gui_panel(DET_X, DET_Y, DET_W, DET_H);
    if (total > 0) {
        const TitleInfo *t = &state->titles[selected];
        const char *name = t->name[0] ? t->name : t->game_id;
        float y = DET_Y + 8;
        int lines = gui_text_wrap(DET_IX, y, F_TITLE, gui_rgb(HEX_TEXT), DET_IW, 2, name);
        y += lines * gui_line_h(F_TITLE) + 4;
        float px = DET_IX;
        px += system_pill(px, y, t->is_psx ? "PS1" : "PSP") + 5;
        if (t->server_only)
            gui_pill(px, y, 12, F_SMALL, gui_rgba(HEX_INFO, 0x50), gui_rgb(HEX_INFO), "Server only");
        else
            gui_pill(px, y, 12, F_SMALL, gui_rgba(HEX_OK, 0x40), gui_rgb(HEX_OK), "On this PSP");
        y += 20;

        char size[32], files[16];
        y = field(y, "Game ID", t->game_id);
        if (t->server_only) {
            y = field(y, "Local", "Not downloaded yet");
        } else {
            format_size(t->total_size, size, sizeof(size));
            snprintf(files, sizeof(files), "%d", t->file_count);
            y = field(y, "Size", size);
            y = field(y, "Files", files);
            const char *slash = strrchr(t->save_dir, '/');
            y = field(y, "Folder", slash ? slash + 1 : t->save_dir);
        }

        /* What Cross will do */
        float by = DET_Y + DET_H - 74;
        gui_rrect(DET_IX - 3, by, DET_IW + 6, 30, 5, gui_rgb(HEX_BG2));
        if (g_online) {
            float hx = DET_IX + 4;
            hx += gui_button(hx, by + 15, "CROSS") + 5;
            gui_text_mid(hx, by, 30, F_SMALL, gui_rgb(HEX_TEXT), GUI_LEFT, DET_IW - 26,
                         t->server_only ? "Download from the server" : "Compare with the server");
        } else {
            gui_text_mid(DET_IX + 4, by, 30, F_SMALL, gui_rgb(HEX_DIM), GUI_LEFT, DET_IW - 8,
                         "Offline \xE2\x80\x94 sync unavailable");
        }
    }
    /* Server / console info */
    float sy = DET_Y + DET_H - 38;
    gui_rect(DET_X + 8, sy - 4, DET_W - 16, 1, gui_rgb(HEX_LINE));
    gui_circle(DET_IX + 3, sy + 7, 3, gui_rgb(g_online ? HEX_OK : HEX_ERR));
    gui_text_fit(DET_IX + 10, sy, F_SMALL, gui_rgb(HEX_DIM), GUI_LEFT, DET_IW - 10, state->server_url);
    snprintf(buf, sizeof(buf), "Console %s  \xC2\xB7  AP %d", state->console_id, state->wifi_ap_index + 1);
    gui_text_fit(DET_IX + 10, sy + 14, F_SMALL, gui_rgb(HEX_MUTED), GUI_LEFT, DET_IW - 10, buf);

    static const GuiHint online_hints[] = {
        {"CROSS", "Sync"}, {"SQUARE", "Sync all"}, {"TRIANGLE", "Details"},
        {"LR", "Page"}, {"START", "Exit"},
    };
    static const GuiHint offline_hints[] = {
        {"UD", "Browse"}, {"LR", "Page"}, {"TRIANGLE", "Details"}, {"START", "Exit"},
    };
    if (g_online) view_end(online_hints, 5, true);
    else view_end(offline_hints, 4, true);
}

/* ================================================================== */
/* ROM catalog view                                                    */
/* ================================================================== */

static const DownloadEntry *find_dl(const DownloadList *list, const char *rom_id) {
    if (!list || !rom_id) return NULL;
    for (int i = 0; i < list->count; i++)
        if (strcmp(list->items[i].rom_id, rom_id) == 0) return &list->items[i];
    return NULL;
}

static const char *dl_status_label(DownloadStatus s) {
    switch (s) {
        case DL_STATUS_QUEUED:    return "Queued";
        case DL_STATUS_ACTIVE:    return "Active";
        case DL_STATUS_PAUSED:    return "Paused";
        case DL_STATUS_COMPLETED: return "Done";
        case DL_STATUS_ERROR:     return "Error";
    }
    return "";
}

static uint32_t dl_status_hex(DownloadStatus s) {
    switch (s) {
        case DL_STATUS_QUEUED:    return HEX_DIM;
        case DL_STATUS_ACTIVE:    return HEX_ACCENT2;
        case DL_STATUS_PAUSED:    return HEX_WARN;
        case DL_STATUS_COMPLETED: return HEX_OK;
        case DL_STATUS_ERROR:     return HEX_ERR;
    }
    return HEX_DIM;
}

/* Small status mark at the start of a catalog row */
static void dl_mark(float cx, float cy, const DownloadEntry *dl, bool sel) {
    if (!dl) {
        gui_circle(cx, cy, 2, gui_rgb(sel ? HEX_INK : HEX_MUTED));
        return;
    }
    uint32_t hex = sel ? HEX_INK : dl_status_hex(dl->status);
    if (dl->status == DL_STATUS_COMPLETED)
        gui_icon_check(cx, cy, 9, gui_rgb(hex));
    else if (dl->status == DL_STATUS_ERROR) {
        gui_line(cx - 3, cy - 3, cx + 3, cy + 3, 1.6f, gui_rgb(hex));
        gui_line(cx - 3, cy + 3, cx + 3, cy - 3, 1.6f, gui_rgb(hex));
    } else if (dl->status == DL_STATUS_PAUSED) {
        gui_rect(cx - 3, cy - 3.5f, 2, 7, gui_rgb(hex));
        gui_rect(cx + 1, cy - 3.5f, 2, 7, gui_rgb(hex));
    } else {
        gui_ring(cx, cy, 4, 1.5f, gui_rgb(hex));
        if (dl->status == DL_STATUS_ACTIVE)
            gui_circle(cx, cy, 1.8f, gui_rgb(hex));
    }
}

void ui_draw_rom_catalog(const RomCatalog *catalog,
                         const DownloadList *downloads,
                         const char *const *systems, int system_count,
                         int system_index,
                         int selected, int scroll_offset,
                         const char *notice) {
    const AppView V = APP_VIEW_ROMS;
    int total = catalog ? catalog->count : 0;
    view_begin(V);

    /* List: system chips, SELECT switches */
    list_panel(NULL);
    float tx = LIST_X + 8;
    tx = gui_tabs(tx, LIST_Y + 3, 16, systems, system_count, system_index) + 4;
    gui_button(tx, LIST_Y + LIST_HEAD_H / 2.0f, "SELECT");
    char buf[96];
    snprintf(buf, sizeof(buf), "%d / %d", total ? selected + 1 : 0, total);
    gui_text_mid(LIST_X + LIST_W - 10, LIST_Y, LIST_HEAD_H, F_SMALL, gui_rgb(HEX_DIM), GUI_RIGHT, 0, buf);

    if (list_anim(V, selected, scroll_offset, total)) {
        rows_begin(V);
        int first, last;
        rows_range(V, total, &first, &last);
        for (int i = first; i < last; i++) {
            const RomEntry *r = &catalog->items[i];
            float y = row_y(V, i);
            bool sel = row_selected(V, i, selected);
            dl_mark(ROWS_X + 11, y + ROW_H / 2.0f, find_dl(downloads, r->rom_id), sel);

            char size[24];
            format_size(r->size, size, sizeof(size));
            float right = ROWS_X + ROWS_W - 6;
            right -= gui_text_mid(right, y, ROW_H, F_SMALL, gui_rgb(sel ? HEX_INK : HEX_DIM),
                                  GUI_RIGHT, 0, size) + 8;
            const char *name = r->name[0] ? r->name : r->filename;
            float x = ROWS_X + 22;
            if (r->disc_total > 1) {
                char discs[24];
                snprintf(discs, sizeof(discs), "%d discs", r->disc_total);
                float pw = gui_pill_w(12, F_SMALL, discs);
                right -= pw;
                gui_pill(right, y + 3, 12, F_SMALL, gui_rgb(sel ? HEX_INK : HEX_PANEL_HI),
                         gui_rgb(sel ? HEX_ACCENT2 : HEX_DIM), discs);
                right -= 6;
            }
            gui_text_mid(x, y, ROW_H, F_BODY, gui_rgb(sel ? HEX_INK : HEX_TEXT), GUI_LEFT, right - x, name);
        }
        rows_end(V, total);
    } else if (catalog && catalog->last_error[0]) {
        list_empty("Couldn't load the catalog", catalog->last_error, HEX_ERR);
    } else if (!g_online) {
        list_empty("Offline", "Connect to the server to browse games.", HEX_MUTED);
    } else {
        list_empty("No games here yet",
                   "Add ROMs to the server, then use Refresh catalog in Settings.", HEX_DIM);
    }
    int queued = downloads ? downloads->count : 0;
    if (notice && notice[0]) {
        snprintf(buf, sizeof(buf), "%d games", total);
        float y = ROWS_Y + UI_LIST_ROWS * ROW_H + 2;
        float h = LIST_Y + LIST_H - y;
        float pw = gui_pill_w(12, F_SMALL, notice);
        gui_pill(LIST_X + 10, y + (h - 12) / 2, 12, F_SMALL, gui_rgba(HEX_WARN, 0x40),
                 gui_rgb(HEX_WARN), notice);
        gui_text_mid(LIST_X + LIST_W - 10, y, h, F_SMALL, gui_rgb(HEX_DIM), GUI_RIGHT,
                     LIST_W - 30 - pw, buf);
    } else {
        snprintf(buf, sizeof(buf), "%d games  \xC2\xB7  %d in the download queue", total, queued);
        list_status(buf, NULL);
    }

    /* Detail */
    gui_panel(DET_X, DET_Y, DET_W, DET_H);
    if (total > 0) {
        const RomEntry *r = &catalog->items[selected];
        const DownloadEntry *dl = find_dl(downloads, r->rom_id);
        float y = DET_Y + 8;
        int lines = gui_text_wrap(DET_IX, y, F_TITLE, gui_rgb(HEX_TEXT), DET_IW, 2,
                                  r->name[0] ? r->name : r->filename);
        y += lines * gui_line_h(F_TITLE) + 4;

        float px = DET_IX;
        px += system_pill(px, y, r->system) + 5;
        char size[24];
        format_size(r->size, size, sizeof(size));
        px += gui_pill(px, y, 12, F_SMALL, gui_rgb(HEX_PANEL_HI), gui_rgb(HEX_DIM), size) + 5;
        if (r->disc_total > 1) {
            snprintf(buf, sizeof(buf), "%d discs", r->disc_total);
            gui_pill(px, y, 12, F_SMALL, gui_rgb(HEX_PANEL_HI), gui_rgb(HEX_DIM), buf);
        }
        y += 20;

        y = field(y, "File", r->filename);
        const char *fmt = roms_preferred_extract_format(r);
        if (fmt && !strcmp(fmt, "cso"))
            y = field(y, "Installs", "CSO (server converts)");
        else if (fmt && !strcmp(fmt, "eboot"))
            y = field(y, "Installs", r->disc_total > 1 ? "Multi-disc EBOOT.PBP" : "EBOOT.PBP");
        else if (fmt && fmt[0])
            y = field(y, "Installs", fmt);
        else
            y = field(y, "Installs", "As-is");

        char path[DOWNLOAD_PATH_LEN];
        if (roms_resolve_target_path(r, path, sizeof(path))) {
            gui_text(DET_IX, y, F_SMALL, gui_rgb(HEX_MUTED), GUI_LEFT, "Goes to");
            int n = gui_text_wrap(DET_IX + FIELD_LABEL_W, y, F_SMALL, gui_rgb(HEX_TEXT), DET_IW - FIELD_LABEL_W, 2, path);
            y += n * gui_line_h(F_SMALL) + 2;
        }

        /* Download state */
        float by = DET_Y + DET_H - 44;
        gui_rrect(DET_IX - 3, by, DET_IW + 6, 36, 5, gui_rgb(HEX_BG2));
        if (!dl) {
            if (g_online) {
                float hx = DET_IX + 4;
                hx += gui_button(hx, by + 18, "CROSS") + 5;
                gui_text_mid(hx, by, 36, F_SMALL, gui_rgb(HEX_TEXT), GUI_LEFT, 0, "Download to this PSP");
            } else {
                gui_text_mid(DET_IX + 4, by, 36, F_SMALL, gui_rgb(HEX_DIM), GUI_LEFT, 0, "Not downloaded");
            }
        } else {
            uint32_t hex = dl_status_hex(dl->status);
            gui_pill(DET_IX + 2, by + 5, 12, F_SMALL, gui_rgba(hex, 0x40), gui_rgb(hex),
                     dl_status_label(dl->status));
            if (dl->status == DL_STATUS_COMPLETED) {
                gui_text_fit(DET_IX + 2, by + 20, F_SMALL, gui_rgb(HEX_DIM), GUI_LEFT, DET_IW - 4,
                             "Installed on the Memory Stick");
            } else {
                int pct = percent(dl->offset, dl->total);
                snprintf(buf, sizeof(buf), "%d%%", pct);
                gui_text(DET_IX + DET_IW - 2, by + 4, F_SMALL, gui_rgb(HEX_DIM), GUI_RIGHT, buf);
                gui_bar(DET_IX + 2, by + 23, DET_IW - 4, 6, dl->total ? pct / 100.0f : 0, hex);
            }
        }
    }

    static const GuiHint online_hints[] = {
        {"CROSS", "Download"}, {"TRIANGLE", "Details"}, {"SELECT", "System"},
        {"LR", "Page"}, {"START", "Exit"},
    };
    static const GuiHint offline_hints[] = {
        {"UD", "Browse"}, {"TRIANGLE", "Details"}, {"SELECT", "System"},
        {"LR", "Page"}, {"START", "Exit"},
    };
    if (g_online) view_end(online_hints, 5, true);
    else view_end(offline_hints, 5, true);
}

/* ================================================================== */
/* Downloads view                                                      */
/* ================================================================== */

static const DownloadEntry *active_entry(const DownloadList *list) {
    for (int i = 0; list && i < list->count; i++)
        if (list->items[i].status == DL_STATUS_ACTIVE) return &list->items[i];
    return NULL;
}

/* Detail panel while a transfer runs */
static void draw_active_transfer(const DownloadEntry *e, uint64_t done, uint64_t total, uint64_t bps) {
    float y = DET_Y + 8;
    gui_spinner(DET_IX + 5, y + 6, 4.5f, HEX_ACCENT2);
    gui_text(DET_IX + 15, y, F_SMALL, gui_rgb(HEX_ACCENT2), GUI_LEFT, "DOWNLOADING");
    y += 16;
    const char *name = (e && e->name[0]) ? e->name : (e ? e->filename : "");
    int lines = gui_text_wrap(DET_IX, y, F_BOLD, gui_rgb(HEX_TEXT), DET_IW, 2, name);
    y += lines * gui_line_h(F_BOLD) + 2;
    const char *file = (e && e->target_path[0]) ? e->target_path : "";
    const char *slash = strrchr(file, '/');
    gui_text_fit(DET_IX, y, F_SMALL, gui_rgb(HEX_DIM), GUI_LEFT, DET_IW, slash ? slash + 1 : file);
    y += 22;

    if (total == 0 && e) total = e->total;
    int pct = percent(done, total);
    gui_bar(DET_IX, y, DET_IW, 9, total ? pct / 100.0f : -1.0f, HEX_ACCENT);
    y += 14;
    char a[24], b[24], line[96];
    format_size(done, a, sizeof(a));
    format_size(total, b, sizeof(b));
    snprintf(line, sizeof(line), "%s / %s", a, b);
    gui_text(DET_IX, y, F_SMALL, gui_rgb(HEX_TEXT), GUI_LEFT, line);
    snprintf(line, sizeof(line), "%d%%", pct);
    gui_text(DET_IX + DET_IW, y, F_BOLD, gui_rgb(HEX_ACCENT2), GUI_RIGHT, line);
    y += 22;

    format_bps(bps, a, sizeof(a));
    format_eta(total > done ? total - done : 0, bps, b, sizeof(b));
    y = field(y, "Speed", a);
    y = field(y, "ETA", b);

    float by = DET_Y + DET_H - 34;
    gui_rrect(DET_IX - 3, by, DET_IW + 6, 26, 5, gui_rgb(HEX_BG2));
    hint(DET_IX + 4, by + 13, "CIRCLE", "Pause (resume later)", HEX_TEXT);
}

/* Detail panel for a queued / paused / finished entry */
static void draw_entry_detail(const DownloadEntry *e) {
    float y = DET_Y + 8;
    uint32_t hex = dl_status_hex(e->status);
    float px = DET_IX;
    px += gui_pill(px, y, 13, F_SMALL, gui_rgba(hex, 0x40), gui_rgb(hex), dl_status_label(e->status)) + 5;
    system_pill(px, y + 0.5f, e->system);
    y += 19;
    int lines = gui_text_wrap(DET_IX, y, F_TITLE, gui_rgb(HEX_TEXT), DET_IW, 3,
                              e->name[0] ? e->name : e->filename);
    y += lines * gui_line_h(F_TITLE) + 6;

    char a[24], b[24], line[96];
    if (e->total > 0) {
        int pct = percent(e->offset, e->total);
        gui_bar(DET_IX, y, DET_IW, 7, pct / 100.0f, hex);
        y += 11;
        format_size(e->offset, a, sizeof(a));
        format_size(e->total, b, sizeof(b));
        snprintf(line, sizeof(line), "%s / %s  (%d%%)", a, b, pct);
        gui_text(DET_IX, y, F_SMALL, gui_rgb(HEX_DIM), GUI_LEFT, line);
        y += 18;
    }
    y = field(y, "File", e->filename);
    gui_text(DET_IX, y, F_SMALL, gui_rgb(HEX_MUTED), GUI_LEFT, "Goes to");
    gui_text_wrap(DET_IX + FIELD_LABEL_W, y, F_SMALL, gui_rgb(HEX_TEXT), DET_IW - FIELD_LABEL_W, 3, e->target_path);

    const char *what = NULL;
    const char *btn = "CROSS";
    switch (e->status) {
        case DL_STATUS_QUEUED: what = "Start download"; break;
        case DL_STATUS_PAUSED: what = "Resume download"; break;
        case DL_STATUS_ERROR:  what = "Retry download"; break;
        case DL_STATUS_COMPLETED: what = "Remove / clear finished"; btn = "SQUARE"; break;
        default: break;
    }
    if (what && (g_online || e->status == DL_STATUS_COMPLETED)) {
        float by = DET_Y + DET_H - 34;
        gui_rrect(DET_IX - 3, by, DET_IW + 6, 26, 5, gui_rgb(HEX_BG2));
        hint(DET_IX + 4, by + 13, btn, what, HEX_TEXT);
    }
}

void ui_draw_downloads(const DownloadList *downloads,
                       int selected, int scroll_offset,
                       bool active_in_progress,
                       uint64_t active_downloaded,
                       uint64_t active_total,
                       uint64_t active_bps,
                       bool vsync) {
    const AppView V = APP_VIEW_DOWNLOADS;
    int total = downloads ? downloads->count : 0;
    const DownloadEntry *active = active_in_progress ? active_entry(downloads) : NULL;
    view_begin(V);

    list_panel("Queue");
    int done = 0, waiting = 0;
    for (int i = 0; i < total; i++) {
        if (downloads->items[i].status == DL_STATUS_COMPLETED) done++;
        else waiting++;
    }
    char buf[64];
    snprintf(buf, sizeof(buf), "%d / %d", total ? selected + 1 : 0, total);
    gui_text_mid(LIST_X + LIST_W - 10, LIST_Y, LIST_HEAD_H, F_SMALL, gui_rgb(HEX_DIM), GUI_RIGHT, 0, buf);

    if (list_anim(V, selected, scroll_offset, total)) {
        rows_begin(V);
        int first, last;
        rows_range(V, total, &first, &last);
        for (int i = first; i < last; i++) {
            const DownloadEntry *e = &downloads->items[i];
            float y = row_y(V, i);
            bool sel = row_selected(V, i, selected);
            uint64_t off = e->offset, tot = e->total;
            if (e == active) {
                off = active_downloaded;
                if (active_total > 0) tot = active_total;
            }
            uint32_t hex = dl_status_hex(e->status);

            /* Status pill, fixed column */
            const char *label = dl_status_label(e->status);
            float pw = 44;
            gui_rrect(ROWS_X + 5, y + 3, pw, 12, 6,
                      sel ? gui_rgb(HEX_INK) : gui_rgba(hex, 0x40));
            gui_text_mid(ROWS_X + 5 + pw / 2, y + 3, 12, F_SMALL, gui_rgb(hex), GUI_CENTER, 0, label);

            /* Right: mini bar + percent for unfinished entries */
            float right = ROWS_X + ROWS_W - 6;
            if (e->status != DL_STATUS_COMPLETED && tot > 0) {
                int pct = percent(off, tot);
                snprintf(buf, sizeof(buf), "%d%%", pct);
                gui_text_mid(right, y, ROW_H, F_SMALL, gui_rgb(sel ? HEX_INK : HEX_DIM), GUI_RIGHT, 0, buf);
                right -= 30;
                gui_bar(right - 36, y + 7, 36, 4, pct / 100.0f, sel ? HEX_INK : hex);
                right -= 44;
            }
            float x = ROWS_X + 5 + pw + 7;
            gui_text_mid(x, y, ROW_H, F_BODY, gui_rgb(sel ? HEX_INK : HEX_TEXT), GUI_LEFT, right - x,
                         e->name[0] ? e->name : e->filename);
        }
        rows_end(V, total);
    } else {
        list_empty("Nothing queued", "Pick a game in the Catalog (L / R) to download it.", HEX_DIM);
    }
    snprintf(buf, sizeof(buf), "%d waiting  \xC2\xB7  %d finished", waiting, done);
    list_status(buf, NULL);

    gui_panel(DET_X, DET_Y, DET_W, DET_H);
    if (active)
        draw_active_transfer(active, active_downloaded, active_total, active_bps);
    else if (total > 0 && selected < total)
        draw_entry_detail(&downloads->items[selected]);

    if (active) {
        static const GuiHint busy_hints[] = {{"CIRCLE", "Pause"}};
        view_end(busy_hints, 1, vsync);
        return;
    }
    static const GuiHint online_hints[] = {
        {"CROSS", "Start / resume"}, {"SQUARE", "Remove"}, {"TRIANGLE", "Details"},
        {"LR", "Page"}, {"START", "Exit"},
    };
    static const GuiHint offline_hints[] = {
        {"UD", "Browse"}, {"SQUARE", "Remove"}, {"TRIANGLE", "Details"},
        {"LR", "Page"}, {"START", "Exit"},
    };
    if (g_online) view_end(online_hints, 5, vsync);
    else view_end(offline_hints, 5, vsync);
}

/* ================================================================== */
/* Settings view                                                       */
/* ================================================================== */

void ui_draw_settings(const UiSettingsRow *rows, int count,
                      int selected, int scroll) {
    const AppView V = APP_VIEW_SETTINGS;
    view_begin(V);

    list_panel("Settings");
    if (list_anim(V, selected, scroll, count)) {
        rows_begin(V);
        int first, last;
        rows_range(V, count, &first, &last);
        for (int i = first; i < last; i++) {
            const UiSettingsRow *r = &rows[i];
            float y = row_y(V, i);
            bool sel = row_selected(V, i, selected);
            float x = ROWS_X + 8;
            if (r->action) {
                /* Action rows: accent label with a chevron */
                uint32_t c = gui_rgb(sel ? HEX_INK : HEX_ACCENT2);
                gui_text_mid(x, y, ROW_H, F_BOLD, c, GUI_LEFT, ROWS_W - 30, r->label);
                float cx = ROWS_X + ROWS_W - 12, cy = y + ROW_H / 2.0f;
                gui_line(cx - 2, cy - 4, cx + 2, cy, 1.6f, c);
                gui_line(cx - 2, cy + 4, cx + 2, cy, 1.6f, c);
            } else {
                gui_text_mid(x, y, ROW_H, F_BODY, gui_rgb(sel ? HEX_INK : HEX_DIM), GUI_LEFT, 92, r->label);
                gui_text_mid(ROWS_X + ROWS_W - 6, y, ROW_H, F_BODY, gui_rgb(sel ? HEX_INK : HEX_TEXT),
                             GUI_RIGHT, ROWS_W - 110, r->value);
            }
        }
        rows_end(V, count);
    }
    list_status("GameSync PSP client", "v" APP_VERSION);

    gui_panel(DET_X, DET_Y, DET_W, DET_H);
    if (count > 0 && selected < count) {
        const UiSettingsRow *r = &rows[selected];
        float y = DET_Y + 8;
        int lines = gui_text_wrap(DET_IX, y, F_TITLE, gui_rgb(HEX_TEXT), DET_IW, 2, r->label);
        y += lines * gui_line_h(F_TITLE) + 6;
        if (!r->action && r->value[0]) {
            lines = gui_text_wrap(DET_IX, y, F_BODY, gui_rgb(HEX_ACCENT2), DET_IW, 3, r->value);
            y += lines * gui_line_h(F_BODY) + 6;
        }
        if (r->help)
            gui_text_wrap(DET_IX, y, F_SMALL, gui_rgb(HEX_DIM), DET_IW, 7, r->help);
        if (r->action) {
            float by = DET_Y + DET_H - 34;
            gui_rrect(DET_IX - 3, by, DET_IW + 6, 26, 5, gui_rgb(HEX_BG2));
            hint(DET_IX + 4, by + 13, "CROSS", r->label, HEX_TEXT);
        }
    }

    static const GuiHint hints[] = {
        {"UD", "Browse"}, {"CROSS", "Select"}, {"START", "Exit"},
    };
    view_end(hints, 3, true);
}
