/*
 * ui.c — GameSync screen furniture (see ui.h) on top of the gui.c kit.
 */

#include "ui.h"

#include <stdio.h>
#include <string.h>
#include <stdarg.h>

static char g_status[200];
static bool g_status_err = false;

void ui_init(void) {
    gui_init();
}

int ui_list_visible(void) {
    return (int)((UI_LIST_H - UI_LIST_HEAD - 6) / UI_ROW_H);
}

/* ---- Status line ---- */

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

/* ---- Chrome ---- */

const char *ui_view_name(AppView view) {
    switch (view) {
        case APP_VIEW_ROMS:      return "Game Catalog";
        case APP_VIEW_LOCAL:     return "Installed Games";
        case APP_VIEW_DOWNLOADS: return "Downloads";
        case APP_VIEW_SAVES:     return "Card Images (SD)";
        case APP_VIEW_CARDS:     return "Memory Cards";
        case APP_VIEW_SERVER:    return "Server Saves";
        case APP_VIEW_CONFIG:    return "Settings";
        default:                 return "?";
    }
}

static const char *const k_tabs[APP_VIEW_COUNT] = {
    "Catalog", "Installed", "Queue", "VMC", "Cards", "Server", "Settings",
};

void ui_draw_chrome(const SyncState *state, AppView view, const GuiHint *hints, int nhints) {
    gui_header(ui_view_name(view), state);
    gui_tabs(k_tabs, APP_VIEW_COUNT, (int)view);
    if (g_status[0])
        gui_banner(GUI_BANNER_Y, g_status, g_status_err ? HEX_ERR : HEX_ACCENT);
    gui_footer(hints, nhints);
}

/* ---- List panel ---- */

void ui_list_ex(const char *title, int count, int sel, int scroll, int rows,
                float skip, UiRowFn row, const char *empty) {
    float x = UI_LIST_X, y = UI_LIST_Y, w = UI_LIST_W, h = UI_LIST_H;
    gui_panel(x, y, w, h);

    /* Title strip: name left, position right */
    gui_text_mid(x + UI_PAD, y + 2, UI_LIST_HEAD, GUI_S_SMALL, gui_rgb(HEX_TEXT), GUI_LEFT,
                 w - 100, title);
    if (count > 0)
        gui_textf(x + w - UI_PAD, y + 2 + (UI_LIST_HEAD - gui_line_h(GUI_S_TINY)) / 2 + 1,
                  GUI_S_TINY, gui_rgb(HEX_DIM), GUI_RIGHT, "%d / %d", sel + 1, count);
    gui_rect(x + 8, y + UI_LIST_HEAD + 1, w - 16, 2, gui_rgb(HEX_LINE));

    float top = y + UI_LIST_HEAD + 5 + skip;
    if (count <= 0) {
        float ey = top + 30;
        gui_text_wrap(x + 24, ey, GUI_S_SMALL, gui_rgb(HEX_DIM), w - 48, 4,
                      empty ? empty : "Nothing here yet.");
        return;
    }
    float rw = w - 22;   /* leave room for the scrollbar */
    for (int i = 0; i < rows; i++) {
        int idx = scroll + i;
        if (idx >= count) break;
        float ry = top + i * UI_ROW_H;
        bool on = idx == sel;
        if (on) gui_rrect(x + 6, ry, rw, UI_ROW_H - 2, 6, gui_rgb(HEX_ACCENT));
        row(idx, x + 6, ry, rw, UI_ROW_H - 2, on);
    }
    gui_scrollbar(x + w - 11, top, rows * UI_ROW_H - 2, scroll, rows, count);
}

void ui_list(const char *title, int count, int sel, int scroll, UiRowFn row, const char *empty) {
    ui_list_ex(title, count, sel, scroll, ui_list_visible(), 0, row, empty);
}

void ui_row_text(float x, float y, float w, float h, bool sel, u32 hex,
                 const char *text, const char *right) {
    u32 fg = gui_rgb(sel ? HEX_INK : hex);
    float rw = 0;
    if (right && *right) {
        rw = gui_text_w(GUI_S_TINY, right) + 10;
        gui_text_mid(x + w - 8, y, h, GUI_S_TINY, gui_rgb(sel ? HEX_INK : HEX_DIM),
                     GUI_RIGHT, 0, right);
    }
    gui_text_mid(x, y, h, GUI_S_SMALL, fg, GUI_LEFT, w - rw - 4, text);
}

float ui_row_pill(float x, float y, float h, bool sel, u32 hex, const char *label) {
    float ph = h - 6;
    if (sel) return gui_pill(x, y + 3, ph, GUI_S_TINY, gui_rgb(HEX_INK), gui_rgb(HEX_ACCENT2), label);
    return gui_pill(x, y + 3, ph, GUI_S_TINY, gui_mix(HEX_PANEL, hex, 0.30f), gui_rgb(hex), label);
}

/* ---- Detail panel ---- */

float ui_detail_begin(const char *title) {
    gui_panel(UI_DETAIL_X, UI_LIST_Y, UI_DETAIL_W, UI_LIST_H);
    float x = UI_DETAIL_X + UI_PAD, w = UI_DETAIL_W - 2 * UI_PAD;
    float y = UI_LIST_Y + 10;
    int n = gui_text_wrap(x, y, GUI_S_BODY, gui_rgb(HEX_TEXT), w, 3, title ? title : "");
    return y + n * gui_line_h(GUI_S_BODY) + 6;
}

void ui_detail_empty(const char *icon_text, const char *hint) {
    gui_panel(UI_DETAIL_X, UI_LIST_Y, UI_DETAIL_W, UI_LIST_H);
    float cx = UI_DETAIL_X + UI_DETAIL_W / 2, cy = UI_LIST_Y + UI_LIST_H / 2 - 30;
    gui_circle(cx, cy, 24, gui_rgb(HEX_PANEL_HI));
    gui_text_mid(cx, cy - 24, 48, GUI_S_BODY, gui_rgb(HEX_ACCENT), GUI_CENTER, 0,
                 icon_text ? icon_text : "?");
    gui_text_wrap(UI_DETAIL_X + UI_PAD, cy + 36, GUI_S_TINY, gui_rgb(HEX_DIM),
                  UI_DETAIL_W - 2 * UI_PAD, 4, hint ? hint : "");
}

float ui_detail_field(float y, const char *label, const char *value, u32 value_hex) {
    float x = UI_DETAIL_X + UI_PAD, w = UI_DETAIL_W - 2 * UI_PAD;
    if (y > UI_LIST_Y + UI_LIST_H - 36) return y;   /* out of room */
    gui_text(x, y, GUI_S_TINY, gui_rgb(HEX_MUTED), GUI_LEFT, label);
    y += gui_line_h(GUI_S_TINY) - 2;
    /* Long values (paths, file names) get a second line when there is room */
    int lines = y + 2 * gui_line_h(GUI_S_SMALL) <= UI_LIST_Y + UI_LIST_H - 8 ? 2 : 1;
    int n = gui_text_wrap(x, y, GUI_S_SMALL, gui_rgb(value_hex), w, lines,
                          value && *value ? value : "-");
    return y + n * gui_line_h(GUI_S_SMALL) + 3;
}

float ui_detail_pill(float *x, float y, u32 bg, u32 fg, const char *label) {
    float w = gui_pill(*x, y, 20, GUI_S_TINY, bg, fg, label);
    *x += w + 6;
    return w;
}

/* ---- Full-screen states / dialogs ---- */

void ui_draw_boot(const char *message) {
    gui_vgrad(0, 0, GUI_W, GUI_H, gui_rgb(HEX_BG), gui_rgb(0x0B1118));
    float cx = GUI_W / 2.0f, cy = 190;
    gui_rrect(cx - 34, cy - 34, 68, 68, 18, gui_rgb(HEX_ACCENT));
    gui_rrect(cx - 13, cy - 13, 26, 26, 8, gui_rgb(HEX_BG));
    gui_text(cx, cy + 50, 1.2f, gui_rgb(HEX_TEXT), GUI_CENTER, "GameSync");
    gui_text(cx, cy + 84, GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_CENTER,
             "GameCube client  v" APP_VERSION);
    float w = 360, x = cx - w / 2, y = cy + 130;
    gui_rrect(x, y, w, 34, 8, gui_rgb(HEX_PANEL));
    gui_rrect(x, y, 5, 34, 2, gui_rgb(HEX_ACCENT));
    gui_text_mid(x + 16, y, 34, GUI_S_SMALL, gui_rgb(HEX_TEXT), GUI_LEFT, w - 28, message);
}

void ui_draw_confirm(const char *title, const char *message, u32 tone) {
    gui_dim();
    float w = 400, x = (GUI_W - w) / 2;
    char lines[6][GUI_WRAP_LINE];
    int n = gui_wrap(message, GUI_S_SMALL, w - 40, lines, 6);
    float lh = gui_line_h(GUI_S_SMALL);
    float h = 34 + 18 + n * lh + 18 + 36;
    float y = (GUI_H - h) / 2;
    gui_card(x, y, w, h, title, tone);
    float ty = y + 34 + 16;
    for (int i = 0; i < n; i++)
        gui_text(x + 20, ty + i * lh, GUI_S_SMALL, gui_rgb(i == 0 ? HEX_TEXT : HEX_DIM),
                 GUI_LEFT, lines[i]);
    /* Choice strip */
    float by = y + h - 30;
    gui_rect(x + 12, by - 8, w - 24, 2, gui_rgb(HEX_LINE));
    float bx = x + w - 20;
    float lw = gui_text_w(GUI_S_SMALL, "No");
    gui_text_mid(bx - lw, by, 20, GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_LEFT, 0, "No");
    bx -= lw + 6 + gui_button_w("B");
    gui_button(bx, by + 10, "B");
    bx -= 22;
    lw = gui_text_w(GUI_S_SMALL, "Yes");
    gui_text_mid(bx - lw, by, 20, GUI_S_SMALL, gui_rgb(HEX_TEXT), GUI_LEFT, 0, "Yes");
    bx -= lw + 6 + gui_button_w("A");
    gui_button(bx, by + 10, "A");
}

/* Button strip at the bottom of a modal card, laid out right to left. */
static void card_strip(float x, float w, float by, const char *const *btn,
                       const char *const *lab, int n) {
    gui_rect(x + 12, by - 8, w - 24, 2, gui_rgb(HEX_LINE));
    float bx = x + w - 20;
    for (int i = n - 1; i >= 0; i--) {
        float lw = gui_text_w(GUI_S_SMALL, lab[i]);
        gui_text_mid(bx - lw, by, 20, GUI_S_SMALL, gui_rgb(i == 0 ? HEX_TEXT : HEX_DIM),
                     GUI_LEFT, 0, lab[i]);
        bx -= lw + 6 + gui_button_w(btn[i]);
        gui_button(bx, by + 10, btn[i]);
        bx -= 22;
    }
}

void ui_draw_menu(const char *title, const char *subtitle,
                  const char *const *items, int n, int sel) {
    gui_dim();
    float w = 400, x = (GUI_W - w) / 2, rh = 28;
    float sh = subtitle && *subtitle ? gui_line_h(GUI_S_TINY) + 6 : 0;
    float h = 34 + 12 + sh + n * rh + 14 + 36;
    float y = (GUI_H - h) / 2;
    gui_card(x, y, w, h, title, HEX_ACCENT);
    float cy = y + 34 + 10;
    if (sh > 0) {
        gui_text_fit(x + 20, cy, GUI_S_TINY, gui_rgb(HEX_DIM), GUI_LEFT, w - 40, subtitle);
        cy += sh;
    }
    for (int i = 0; i < n; i++) {
        bool on = i == sel;
        if (on) gui_rrect(x + 14, cy, w - 28, rh - 4, 7, gui_rgb(HEX_ACCENT));
        gui_text_mid(x + 26, cy, rh - 4, GUI_S_SMALL, gui_rgb(on ? HEX_INK : HEX_TEXT),
                     GUI_LEFT, w - 52, items[i]);
        cy += rh;
    }
    static const char *const btn[] = { "A", "B" };
    static const char *const lab[] = { "Select", "Cancel" };
    card_strip(x, w, y + h - 30, btn, lab, 2);
}

void ui_draw_info(const char *title, const char *const *labels,
                  const char *const *values, int n) {
    gui_dim();
    float w = 460, x = (GUI_W - w) / 2;
    float lt = gui_line_h(GUI_S_TINY), ls = gui_line_h(GUI_S_SMALL);
    float h = 34 + 12 + n * (lt + ls + 4) + 10 + 36;
    float y = (GUI_H - h) / 2;
    if (y < GUI_SAFE_Y) y = GUI_SAFE_Y;
    gui_card(x, y, w, h, title, HEX_INFO);
    float cy = y + 34 + 10;
    for (int i = 0; i < n; i++) {
        gui_text(x + 20, cy, GUI_S_TINY, gui_rgb(HEX_MUTED), GUI_LEFT, labels[i]);
        cy += lt - 2;
        gui_text_fit(x + 20, cy, GUI_S_SMALL, gui_rgb(HEX_TEXT), GUI_LEFT, w - 40,
                     values[i] && *values[i] ? values[i] : "-");
        cy += ls + 6;
    }
    static const char *const btn[] = { "B" };
    static const char *const lab[] = { "Close" };
    card_strip(x, w, y + h - 30, btn, lab, 1);
}

void ui_human_size(uint64_t b, char *o, size_t n) {
    if (b >= (1ULL << 30))
        snprintf(o, n, "%llu.%02llu GB", (unsigned long long)(b >> 30),
                 (unsigned long long)(((b & ((1ULL << 30) - 1)) * 100) >> 30));
    else if (b >= (1ULL << 20))
        snprintf(o, n, "%llu.%01llu MB", (unsigned long long)(b >> 20),
                 (unsigned long long)(((b & ((1ULL << 20) - 1)) * 10) >> 20));
    else if (b >= (1ULL << 10)) snprintf(o, n, "%llu KB", (unsigned long long)(b >> 10));
    else snprintf(o, n, "%llu B", (unsigned long long)b);
}
