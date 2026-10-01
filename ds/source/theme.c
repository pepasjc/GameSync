#include "theme.h"
#include <stdio.h>
#include <string.h>

#ifndef APP_VERSION
#define APP_VERSION "dev"
#endif

bool theme_wifi = false;

// ---------------------------------------------------------------------------
// Icons: 1-bit, bit 15 = leftmost pixel. Drawn for this project.
// ---------------------------------------------------------------------------

static const uint16_t logo_rows[] = { 0x2000, 0x7000, 0xF800, 0x2100, 0x2100, 0x2100, 0x2100, 0x07C0, 0x0380, 0x0100 };
const Icon icon_logo = { 10, 10, logo_rows };
static const uint16_t wifi_rows[] = { 0x3F80, 0x4040, 0x9F20, 0x2080, 0x0E00, 0x1100, 0x0000, 0x0400 };
const Icon icon_wifi = { 11, 8, wifi_rows };
static const uint16_t check_rows[] = { 0x0100, 0x0300, 0x8600, 0xCC00, 0x7800, 0x3000 };
const Icon icon_check = { 8, 6, check_rows };
static const uint16_t up_rows[] = { 0x1000, 0x3800, 0x7C00, 0xFE00, 0x3800, 0x3800, 0x3800 };
const Icon icon_up = { 7, 7, up_rows };
static const uint16_t down_rows[] = { 0x3800, 0x3800, 0x3800, 0xFE00, 0x7C00, 0x3800, 0x1000 };
const Icon icon_down = { 7, 7, down_rows };
static const uint16_t conflict_rows[] = { 0x0800, 0x1C00, 0x1400, 0x2A00, 0x2A00, 0x4900, 0x4100, 0x8880, 0xFF80 };
const Icon icon_conflict = { 9, 9, conflict_rows };
static const uint16_t cloud_rows[] = { 0x0E00, 0x3F80, 0x7FC0, 0xFFE0, 0xFFE0, 0x7FC0 };
const Icon icon_cloud = { 11, 6, cloud_rows };
static const uint16_t right_rows[] = { 0x0800, 0x0C00, 0xFE00, 0xFF00, 0xFE00, 0x0C00, 0x0800 };
const Icon icon_right = { 8, 7, right_rows };
static const uint16_t left_rows[] = { 0x1000, 0x3000, 0x7F00, 0xFF00, 0x7F00, 0x3000, 0x1000 };
const Icon icon_left = { 8, 7, left_rows };
static const uint16_t trophy_rows[] = { 0xFF80, 0xBE80, 0xBE80, 0x5D00, 0x1C00, 0x0800, 0x0800, 0x3E00 };
const Icon icon_trophy = { 9, 8, trophy_rows };
static const uint16_t sd_rows[] = { 0x3F00, 0x5500, 0xAB00, 0x8100, 0x8100, 0x8100, 0x8100, 0x8100, 0xFF00 };
const Icon icon_sd = { 8, 9, sd_rows };
static const uint16_t search_rows[] = { 0x3C00, 0x4200, 0x8100, 0x8100, 0x8100, 0x4200, 0x3D00, 0x0180, 0x0080 };
const Icon icon_search = { 9, 9, search_rows };
static const uint16_t gear_rows[] = { 0x1400, 0x7F00, 0x6300, 0xC180, 0x4100, 0xC180, 0x6300, 0x7F00, 0x1400 };
const Icon icon_gear = { 9, 9, gear_rows };
static const uint16_t key_rows[] = { 0x7000, 0x8800, 0x8FC0, 0x8940, 0x7140 };
const Icon icon_key = { 10, 5, key_rows };
static const uint16_t link_rows[] = { 0xFF80, 0x8080, 0xB280, 0xFF80, 0x8080, 0xB280, 0xFF80 };
const Icon icon_link = { 9, 7, link_rows };
static const uint16_t refresh_rows[] = { 0x3E00, 0x4100, 0x8180, 0x8780, 0x8000, 0x8000, 0x8080, 0x4100, 0x3E00 };
const Icon icon_refresh = { 9, 9, refresh_rows };
static const uint16_t box_rows[] = { 0x0800, 0x0800, 0x2A00, 0x1C00, 0x0800, 0x8080, 0x8080, 0xFF80 };
const Icon icon_box = { 9, 8, box_rows };
static const uint16_t info_rows[] = { 0x3E00, 0x4100, 0x8880, 0x8080, 0x8880, 0x8880, 0x8880, 0x4100, 0x3E00 };
const Icon icon_info = { 9, 9, info_rows };
static const uint16_t dot_rows[] = { 0x7000, 0xF800, 0xF800, 0xF800, 0x7000 };
const Icon icon_dot = { 5, 5, dot_rows };

// ---------------------------------------------------------------------------
// Background, header, footer
// ---------------------------------------------------------------------------

Color theme_kind_color(UiKind kind) {
    switch (kind) {
        case KIND_OK: return C_OK;
        case KIND_WARN: return C_WARN;
        case KIND_ERROR: return C_ERR;
        case KIND_DOWNLOAD: return C_INFO;
        case KIND_RA: return C_GOLD;
        default: return C_ACCENT;
    }
}

void theme_background(Surface *s) {
    gfx_vgradient(s, 0, 0, s->w, s->h, C_BG_TOP, C_BG_BOTTOM);
}

static void bar_background(Surface *s, int y, int h, bool line_below) {
    gfx_vgradient(s, 0, y, s->w, h, 0x0C131B, 0x101923);
    gfx_hline(s, 0, line_below ? y + h - 1 : y, s->w, C_CARD_LINE);
}

void theme_header(Surface *s, const char *title) {
    bar_background(s, 0, HEADER_H, true);

    // Logo badge
    gfx_round_rect(s, 5, 3, 14, 14, 4, C_ACCENT, 256);
    gfx_icon(s, &icon_logo, 7, 5, C_ON_ACCENT);
    int x = gfx_text(s, &font_bold, 24, 4, C_TEXT, "GameSync");

    // Version and WiFi on the right
    char ver[24];
    snprintf(ver, sizeof(ver), "v%s", APP_VERSION);
    int vx = s->w - 6 - gfx_text_width(&font_mono, ver);
    gfx_text(s, &font_mono, vx, 7, C_TEXT_FAINT, ver);
    int wx = vx - 17;
    if (theme_wifi) {
        gfx_icon(s, &icon_wifi, wx, 6, C_ACCENT);
    } else {
        gfx_icon(s, &icon_wifi, wx, 6, C_TEXT_FAINT);
        // red slash
        for (int i = 0; i < 9; i++) gfx_fill(s, wx + 1 + i, 5 + i, 1, 1, C_ERR);
    }

    if (title && title[0]) {
        gfx_fill(s, x + 4, 9, 2, 2, C_TEXT_FAINT);
        gfx_text_fit(s, &font_regular, x + 10, 4, wx - 6 - (x + 10), C_TEXT_DIM, title);
    }
}

void theme_toolbar(Surface *s, const char *title, const char *right) {
    bar_background(s, 0, HEADER_H, true);
    gfx_round_rect(s, 6, 5, 3, 10, 1, C_ACCENT, 256);
    int rw = right ? gfx_text_width(&font_regular, right) : 0;
    gfx_text_fit(s, &font_bold, 14, 4, s->w - 14 - rw - 14, C_TEXT, title ? title : "");
    if (right) gfx_text(s, &font_regular, s->w - 7 - rw, 4, C_TEXT_DIM, right);
}

static Color button_color(const char *b) {
    if (!strcmp(b, "A")) return C_ACCENT;
    if (!strcmp(b, "B")) return C_ERR;
    if (!strcmp(b, "X")) return C_INFO;
    if (!strcmp(b, "Y")) return C_WARN;
    return HEX(0x55677B);
}

// Little D-pad with the used arms lit
static int draw_dpad(Surface *s, int x, int y, bool vertical, bool horizontal) {
    Color off = HEX(0x55677B), on = C_TEXT;
    gfx_fill(s, x + 3, y, 3, 9, off);
    gfx_fill(s, x, y + 3, 9, 3, off);
    if (vertical) {
        gfx_fill(s, x + 3, y, 3, 3, on);
        gfx_fill(s, x + 3, y + 6, 3, 3, on);
    }
    if (horizontal) {
        gfx_fill(s, x, y + 3, 3, 3, on);
        gfx_fill(s, x + 6, y + 3, 3, 3, on);
    }
    return 9;
}

int theme_button(Surface *s, int x, int y, const char *b) {
    if (!strcmp(b, "UD")) return draw_dpad(s, x, y + 1, true, false);
    if (!strcmp(b, "LR")) return draw_dpad(s, x, y + 1, false, true);
    if (!strcmp(b, "DPAD")) return draw_dpad(s, x, y + 1, true, true);
    Color bg = button_color(b);
    int tw = gfx_text_width(&font_mono, b);
    int w = strlen(b) == 1 ? 11 : tw + 6;
    gfx_round_rect(s, x, y, w, 11, 5, bg, 256);
    Color fg = (bg == C_ACCENT || bg == C_WARN) ? C_ON_ACCENT : C_TEXT;
    gfx_text(s, &font_mono, x + (w - tw) / 2 + (strlen(b) == 1 ? 1 : 0), y + 2, fg, b);
    return w;
}

void theme_footer(Surface *s, const Hint *hints) {
    int y = s->h - FOOTER_H;
    bar_background(s, y, FOOTER_H, false);
    int x = 5;
    for (const Hint *h = hints; h && h->button; h++) {
        int lw = gfx_text_width(&font_regular, h->label);
        int bw = (!strcmp(h->button, "UD") || !strcmp(h->button, "LR") || !strcmp(h->button, "DPAD"))
                     ? 9 : (strlen(h->button) == 1 ? 11 : gfx_text_width(&font_mono, h->button) + 6);
        if (x + bw + 3 + lw > s->w - 3) break;
        x += theme_button(s, x, y + 3, h->button) + 3;
        x = gfx_text(s, &font_regular, x, y + 2, C_TEXT_DIM, h->label) + 8;
    }
}

// ---------------------------------------------------------------------------
// Panels and widgets
// ---------------------------------------------------------------------------

void theme_card(Surface *s, int x, int y, int w, int h, const char *title, Color title_color) {
    gfx_round_rect(s, x, y + 2, w, h, 6, C_SHADOW, 90);
    gfx_round_frame(s, x, y, w, h, 6, C_CARD_LINE, C_CARD);
    if (title) {
        gfx_round_rect(s, x + 1, y + 1, w - 2, 17, 5, C_CARD_HI, 256);
        gfx_fill(s, x + 1, y + 10, w - 2, 8, C_CARD_HI);
        gfx_hline(s, x + 1, y + 18, w - 2, C_CARD_LINE);
        gfx_round_rect(s, x + 7, y + 5, 3, 9, 1, title_color, 256);
        gfx_text_fit(s, &font_bold, x + 14, y + 3, w - 22, C_TEXT, title);
    }
}

int theme_pill_width(const char *text) {
    return gfx_text_width(&font_mono, text) + 8;
}

int theme_pill(Surface *s, int x, int y, const char *text, Color bg, Color fg) {
    int w = theme_pill_width(text);
    gfx_round_rect(s, x, y, w, 11, 5, bg, 256);
    gfx_text(s, &font_mono, x + 4, y + 2, fg, text);
    return w;
}

int theme_pill_outline(Surface *s, int x, int y, const char *text, Color c) {
    int w = theme_pill_width(text);
    gfx_round_rect(s, x, y, w, 11, 5, c, 256);
    gfx_round_rect(s, x + 1, y + 1, w - 2, 9, 4, gfx_mix(C_CARD, c, 40), 256);
    gfx_text(s, &font_mono, x + 4, y + 2, c, text);
    return w;
}

void theme_progress(Surface *s, int x, int y, int w, int h, uint32_t done, uint32_t total, Color c) {
    int r = h / 2;
    gfx_round_rect(s, x, y, w, h, r, C_INSET, 256);
    if (!total) return;
    if (done > total) done = total;
    int fw = (int)((uint64_t)done * w / total);
    if (fw <= 0) return;
    if (fw < h) fw = h < w ? h : w;  // a visible nub from the start
    gfx_round_rect(s, x, y, fw, h, r, c, 256);
    if (h >= 6) gfx_hline(s, x + r, y + 1, fw - 2 * r, gfx_mix(c, C_TEXT, 90));
}

void theme_scrollbar(Surface *s, int x, int y, int h, int first, int visible, int total) {
    if (total <= visible || h < 8) return;
    gfx_round_rect(s, x, y, 3, h, 1, C_INSET, 256);
    int th = h * visible / total;
    if (th < 8) th = 8;
    int ty = y + (int)((int64_t)(h - th) * first / (total - visible));
    gfx_round_rect(s, x, ty, 3, th, 1, C_TEXT_FAINT, 256);
}

void theme_row(Surface *s, int x, int y, int w, int h, bool selected, bool focused) {
    if (!selected) return;
    if (focused) {
        gfx_round_rect(s, x, y, w, h, 4, C_ACCENT, 256);
    } else {
        gfx_round_rect(s, x, y, w, h, 4, C_CARD_LINE, 256);
    }
}

void theme_kv(Surface *s, int x, int y, int w, const char *key, const char *value, Color value_color) {
    int kx = gfx_text(s, &font_regular, x, y, C_TEXT_DIM, key);
    int room = x + w - kx - 8;
    int vw = gfx_text_width(&font_regular, value);
    if (vw > room) vw = room;
    gfx_text_fit(s, &font_regular, x + w - vw, y, room, value_color, value);
}

void theme_status_dot(Surface *s, int cx, int cy, Color c) {
    gfx_round_rect(s, cx - 3, cy - 3, 7, 7, 3, gfx_mix(C_BG, c, 90), 256);
    gfx_round_rect(s, cx - 2, cy - 2, 5, 5, 2, c, 256);
}

void theme_message(Surface *s, const char *header_title, const char *title, const char *body,
                   UiKind kind, const Hint *hints) {
    theme_background(s);
    theme_toolbar(s, header_title, NULL);
    int w = 232, x = (s->w - w) / 2;
    int lines = body && body[0] ? gfx_text_wrap(NULL, &font_regular, 0, 0, w - 20, 13, 9, 0, body) : 0;
    int h = 26 + lines * 13 + 8;
    if (h < 56) h = 56;
    int y = CONTENT_Y + (CONTENT_H - h) / 2;
    theme_card(s, x, y, w, h, title, theme_kind_color(kind));
    if (lines) gfx_text_wrap(s, &font_regular, x + 10, y + 24, w - 20, 13, 9, C_TEXT, body);
    theme_footer(s, hints);
}
