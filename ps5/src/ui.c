/*
 * GameSync PS5 client - the interface.
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Follows the layout and controls every GameSync client shares (spec in
 * steamdeck/README.md and steamdeck/ui/main_window.py):
 *
 *   header   GameSync          [L1] Saves Catalog Installed Downloads Settings [R1]  (o) server
 *   chips    system sub-tab (L2/R2), Saves status filter (Create), count
 *   body     list                                   | detail panel
 *   banner   last message
 *   footer   button hints                                       L1/R1 Tab  Options Exit
 *
 *   Up/Down rows, Left/Right pages (both repeat while held, faster the
 *   longer; Left/Right then jump by initial letter), L1/R1 tabs, L2/R2
 *   systems (one choice for every tab, kept in ui.txt), Cross the row's main action, Circle only cancels,
 *   Square the tab's secondary action, Triangle details or search,
 *   Create the Saves status filter, Options exits after asking.
 *
 * Everything runs on the main thread except catalog downloads (downloads.c);
 * long work redraws from its progress callback so the console keeps
 * getting frames.
 */
#include "ui.h"

#include "catalog.h"
#include "diskbench.h"
#include "downloads.h"
#include "gfx.h"
#include "installed.h"
#include "json.h"
#include "platform.h"
#include "sync.h"
#include "version.h"

#include <ctype.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ------------------------------------------------------------ look */

/* the shared palette (steamdeck/ui/theme.py, 3ds/source/gui.h) */
#define COL_BG 0x0B1020
#define COL_BG2 0x141B33
#define COL_PANEL 0x1D2746
#define COL_PANEL_HI 0x2A3760
#define COL_LINE 0x36446E
#define COL_TEXT 0xEEF2FA
#define COL_DIM 0x9BA9CD
#define COL_MUTED 0x6B789F
#define COL_ACCENT 0x22C3F6
#define COL_ACCENT2 0x7BDDFF
#define COL_BRAND_A 0x00D6FF /* logo gradient: cyan */
#define COL_BRAND_B 0x3A78FF /* blue */
#define COL_BRAND_C 0xA45CFF /* violet */
#define COL_OK 0x3DD68C
#define COL_WARN 0xFFB938
#define COL_INFO 0x6C93FF
#define COL_ERR 0xFF5D6C
#define COL_RA 0xE5B143
#define COL_RA_WEAK 0x8A712E
#define COL_RA_TEXT 0x161206
#define COL_INK 0x0B1020
/* PlayStation face buttons (the PS2/PSP clients' glyph colours) */
#define COL_CROSS 0x7CB2E8
#define COL_CIRCLE 0xFF6B6B
#define COL_SQUARE 0xE58FCB
#define COL_TRIANGLE 0x3FD6A8
#define COL_GLYPH_BASE 0x2D3A63

#define MARGIN 64
#define HEADER_H 104
#define CHIPS_Y (HEADER_H + 14)
#define CHIPS_H 52
#define LIST_Y (CHIPS_Y + CHIPS_H + 18)
#define BANNER_Y 936
#define FOOTER_Y 990
#define ROW_H 78
#define LIST_W 1176
#define DETAIL_X (MARGIN + LIST_W + 32)
#define DETAIL_W (GFX_W - MARGIN - DETAIL_X)
#define VISIBLE ((BANNER_Y - 12 - LIST_Y) / ROW_H)

typedef enum
{
    TAB_SAVES,
    TAB_CATALOG,
    TAB_INSTALLED,
    TAB_DOWNLOADS,
    TAB_SETTINGS,
    TAB_COUNT
} Tab;

static const char *const TAB_NAMES[TAB_COUNT] = {"Saves", "Catalog", "Installed", "Downloads",
                                                 "Settings"};

/* ------------------------------------------------------------ state */

static SyncContext sync_ctx;
static Tab tab;
static bool quit;
static bool data_ok;
static char data_status[160];
static int server_state = -1; /* -1 unknown, 0 offline, 1 online */

/* a list with a filter: indices into the source array */
typedef struct
{
    int *idx;
    int count;
    int cap;
    int sel;
    int top;
} View;

static void view_push(View *v, int i)
{
    if (v->count == v->cap)
    {
        int cap = v->cap ? v->cap * 2 : 256;
        int *grown = realloc(v->idx, (size_t)cap * sizeof(int));
        if (!grown)
            return;
        v->idx = grown;
        v->cap = cap;
    }
    v->idx[v->count++] = i;
}

static void view_clamp(View *v)
{
    if (v->sel >= v->count)
        v->sel = v->count ? v->count - 1 : 0;
    if (v->sel < 0)
        v->sel = 0;
    if (v->top > v->sel)
        v->top = v->sel;
    if (v->sel >= v->top + VISIBLE)
        v->top = v->sel - VISIBLE + 1;
    if (v->top > v->count - VISIBLE)
        v->top = v->count - VISIBLE > 0 ? v->count - VISIBLE : 0;
}

/* system chips: index 0 = All */
typedef struct
{
    char systems[64][12];
    int count; /* including All */
    int sel;
} Chips;

static void chips_reset(Chips *c)
{
    c->count = 1;
    snprintf(c->systems[0], sizeof(c->systems[0]), "All");
    c->sel = 0;
}

/* case-insensitive strcmp */
static int chip_cmp(const char *a, const char *b)
{
    for (;; ++a, ++b)
    {
        int ca = tolower((unsigned char)*a), cb = tolower((unsigned char)*b);
        if (ca != cb || !ca)
            return ca - cb;
    }
}

static void chips_add(Chips *c, const char *system)
{
    for (int i = 1; i < c->count; ++i)
        if (util_ieq(c->systems[i], system))
            return;
    if (c->count >= 64)
        return;
    /* alphabetical after "All" (callers pick the selection by name) */
    int at = 1;
    while (at < c->count && chip_cmp(c->systems[at], system) < 0)
        ++at;
    memmove(c->systems[at + 1], c->systems[at], (size_t)(c->count - at) * sizeof(c->systems[0]));
    snprintf(c->systems[at], sizeof(c->systems[0]), "%s", system);
    c->count++;
}

static const char *chips_current(const Chips *c)
{
    return c->sel > 0 && c->sel < c->count ? c->systems[c->sel] : NULL;
}

/* The system last picked with L2/R2 on any tab ("" = All), shared by every
 * tab and kept in <data>/ui.txt between runs. A tab without that system
 * keeps its own choice. */
static char pref_system[12];
static KvStore ui_state;

static void chips_pick(Chips *c, const char *own)
{
    c->sel = 0;
    if (!pref_system[0])
        return; /* All: every tab has it */
    const char *want[2] = {pref_system, own};
    for (int w = 0; w < 2; ++w)
        for (int i = 1; want[w] && want[w][0] && i < c->count; ++i)
            if (util_ieq(c->systems[i], want[w]))
            {
                c->sel = i;
                return;
            }
}

static void pref_system_set(const Chips *c)
{
    const char *sys = chips_current(c);
    snprintf(pref_system, sizeof(pref_system), "%s", sys ? sys : "");
    kv_set(&ui_state, "system", pref_system);
    kv_save(&ui_state);
}

/* Saves */
typedef enum
{
    SF_ALL,
    SF_ACTION,
    SF_UPLOAD,
    SF_DOWNLOAD,
    SF_CONFLICT,
    SF_SYNCED,
    SF_LOCAL,
    SF_SERVER,
    SF_COUNT
} StatusFilter;
static const char *const STATUS_FILTER_NAMES[SF_COUNT] = {
    "All", "Needs Action", "Upload", "Download", "Conflict", "Synced", "Local Only", "Server Only"};

static bool saves_loaded;
static View save_view;
static Chips save_chips;
static StatusFilter save_status = SF_ALL;

/* Catalog */
static Catalog catalog;
static bool catalog_loaded;
static char catalog_error[160];
static View cat_view;
static Chips cat_chips;
static bool cat_ra_only;
static char cat_query[96];
static uint8_t *cat_state; /* per ROM: 0 unknown, 1 not installed, 2 installed, 3 can't install */

/* Installed */
static InstalledList inst;
static bool inst_loaded;
static View inst_view;
static Chips inst_chips;
static char inst_query[96];

/* Downloads */
static DlItem dl_items[256];
static int dl_count;
static int dl_sel;

/* Settings */
static int set_sel;
static bool emu_dirs_checked;
static bool emu_dir_found[7];
static char ss_mode_text[160];

/* banner */
static char banner[220];
static uint32_t banner_color = COL_DIM;

/* input */
static uint32_t held_prev;
static uint64_t repeat_at;
static uint64_t held_since; /* when the d-pad direction held now went down */
/* Left/Right held this long jump from initial to initial instead of paging
 * (the quick scroll of the Steam Deck and MiSTer clients) */
#define LETTER_AFTER_MS 1500
static bool nav_letters;

static void set_banner(uint32_t color, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
static void set_banner(uint32_t color, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(banner, sizeof(banner), fmt, ap);
    va_end(ap);
    banner_color = color;
}

/* Buttons newly pressed this frame, with auto-repeat for the d-pad. */
static uint32_t read_input(void)
{
    uint32_t held = plat_poll(&quit);
    uint32_t pressed = held & ~held_prev;
    const uint32_t repeatable = BTN_UP | BTN_DOWN | BTN_LEFT | BTN_RIGHT;
    uint64_t now = plat_ticks_ms();
    if (pressed & repeatable)
    {
        repeat_at = now + 400;
        held_since = now;
    }
    else if ((held & repeatable) && now >= repeat_at)
    {
        pressed |= held & repeatable;
        uint64_t held_for = now - held_since;
        if (held & (BTN_LEFT | BTN_RIGHT))
            repeat_at = now + (held_for >= LETTER_AFTER_MS ? 250 : held_for >= 800 ? 60 : 100);
        else
            repeat_at = now + (held_for >= 1200 ? 40 : 150);
    }
    nav_letters = (held & (BTN_LEFT | BTN_RIGHT)) && now - held_since >= LETTER_AFTER_MS;
    held_prev = held;
    return pressed;
}

/* Uppercase first letter of a name, 0 if it has none. */
static char first_letter(const char *name)
{
    for (; name && *name; ++name)
        if (isalpha((unsigned char)*name))
            return (char)toupper((unsigned char)*name);
    return 0;
}

/* The first row whose name starts with another letter, toward dir; going
 * back it lands on the first row of that letter. Stops at the ends. */
static int letter_jump(const View *v, int dir, const char *(*name_of)(int))
{
    char current = first_letter(name_of(v->idx[v->sel]));
    int i = v->sel + dir;
    while (i >= 0 && i < v->count)
    {
        char l = first_letter(name_of(v->idx[i]));
        if (l && l != current)
            break;
        i += dir;
    }
    if (i < 0 || i >= v->count)
        return dir > 0 ? v->count - 1 : 0;
    if (dir < 0)
    {
        char l = first_letter(name_of(v->idx[i]));
        while (i > 0 && first_letter(name_of(v->idx[i - 1])) == l)
            --i;
    }
    return i;
}

static void view_nav(View *v, uint32_t pressed, const char *(*name_of)(int))
{
    if (!v->count)
        return;
    if (pressed & BTN_UP)
        v->sel = v->sel > 0 ? v->sel - 1 : v->count - 1;
    if (pressed & BTN_DOWN)
        v->sel = v->sel < v->count - 1 ? v->sel + 1 : 0;
    if ((pressed & (BTN_LEFT | BTN_RIGHT)) && nav_letters && name_of)
        v->sel = letter_jump(v, (pressed & BTN_LEFT) ? -1 : 1, name_of);
    else if (pressed & BTN_LEFT)
        v->sel = v->sel - VISIBLE < 0 ? 0 : v->sel - VISIBLE;
    else if (pressed & BTN_RIGHT)
        v->sel = v->sel + VISIBLE >= v->count ? v->count - 1 : v->sel + VISIBLE;
    view_clamp(v);
}

static bool chips_nav(Chips *c, uint32_t pressed)
{
    if (c->count <= 1)
        return false;
    if (pressed & BTN_L2)
        c->sel = (c->sel + c->count - 1) % c->count;
    else if (pressed & BTN_R2)
        c->sel = (c->sel + 1) % c->count;
    else
        return false;
    pref_system_set(c);
    return true;
}

/* ------------------------------------------------------------ glyphs */

typedef enum
{
    G_CROSS,
    G_CIRCLE,
    G_SQUARE,
    G_TRIANGLE,
    G_L1,
    G_R1,
    G_L2,
    G_R2,
    G_OPTIONS,
    G_CREATE,
    G_DPAD,
} Glyph;

/* Draws a button glyph centred vertically on cy; returns its width. */
static int draw_glyph(int x, int cy, Glyph g)
{
    if (g <= G_TRIANGLE)
    {
        float r = 18, cx = (float)x + r;
        gfx_disc(cx, (float)cy, r, COL_GLYPH_BASE);
        switch (g)
        {
        case G_CROSS:
            gfx_line(cx - 7, cy - 7, cx + 7, cy + 7, 3.2f, COL_CROSS);
            gfx_line(cx - 7, cy + 7, cx + 7, cy - 7, 3.2f, COL_CROSS);
            break;
        case G_CIRCLE: gfx_ring(cx, (float)cy, 8.5f, 3.0f, COL_CIRCLE); break;
        case G_SQUARE:
            gfx_line(cx - 7, cy - 7, cx + 7, cy - 7, 3, COL_SQUARE);
            gfx_line(cx + 7, cy - 7, cx + 7, cy + 7, 3, COL_SQUARE);
            gfx_line(cx + 7, cy + 7, cx - 7, cy + 7, 3, COL_SQUARE);
            gfx_line(cx - 7, cy + 7, cx - 7, cy - 7, 3, COL_SQUARE);
            break;
        default:
            gfx_line(cx, cy - 9, cx + 8.5f, cy + 6, 3, COL_TRIANGLE);
            gfx_line(cx + 8.5f, cy + 6, cx - 8.5f, cy + 6, 3, COL_TRIANGLE);
            gfx_line(cx - 8.5f, cy + 6, cx, cy - 9, 3, COL_TRIANGLE);
            break;
        }
        return 36;
    }
    static const char *const labels[] = {"L1", "R1", "L2", "R2", "Options", "Create", "D-pad"};
    const char *label = labels[g - G_L1];
    int w = gfx_text_width(FONT_SMALL, label) + 22;
    gfx_round_rect(x, cy - 17, w, 34, 10, COL_MUTED);
    gfx_text(x + w / 2, cy - 15, FONT_SMALL, COL_TEXT, ALIGN_CENTER, label);
    return w;
}

typedef struct
{
    Glyph glyph;
    const char *label;
} Hint;

static int draw_hints(int x, int cy, const Hint *hints, int n)
{
    for (int i = 0; i < n; ++i)
    {
        if (!hints[i].label)
            continue;
        x += draw_glyph(x, cy, hints[i].glyph) + 10;
        x += gfx_text(x, cy - 15, FONT_SMALL, COL_DIM, ALIGN_LEFT, hints[i].label) + 30;
    }
    return x;
}

/* ------------------------------------------------------------ chrome */

static void draw_system_badge(int x, int y, const char *system)
{
    int w = 104;
    gfx_round_rect(x, y, w, 34, 8, system_color(system));
    gfx_text_fit(x + w / 2, y + 2, FONT_SMALL, 0xFFFFFF, ALIGN_CENTER, w - 10, system);
}

/* "RA" (verified) / "RA?" (title match); returns width */
static int draw_ra_badge(int x, int y, RaLevel level)
{
    if (level == RA_NONE)
        return 0;
    const char *t = level == RA_VERIFIED ? "RA" : "RA?";
    int w = gfx_text_width(FONT_SMALL, t) + 20;
    gfx_round_rect(x, y, w, 32, 8, level == RA_VERIFIED ? COL_RA : COL_RA_WEAK);
    gfx_text(x + w / 2, y + 1, FONT_SMALL, level == RA_VERIFIED ? COL_RA_TEXT : COL_TEXT,
             ALIGN_CENTER, t);
    return w;
}

static int draw_pill(int right_x, int y, const char *text, uint32_t color)
{
    int w = gfx_text_width(FONT_SMALL, text) + 28;
    gfx_round_rect(right_x - w, y, w, 36, 18, color);
    gfx_text(right_x - w / 2, y + 2, FONT_SMALL, COL_INK, ALIGN_CENTER, text);
    return w;
}

/* The brand's sync gradient (cyan -> blue -> violet, as in the logo's arrows),
 * drawn as flat steps so it needs nothing beyond a rect fill. */
static unsigned int brand_lerp(unsigned int a, unsigned int b, int t, int n) {
    int ar = (a >> 16) & 0xFF, ag = (a >> 8) & 0xFF, ab = a & 0xFF;
    int br = (b >> 16) & 0xFF, bg = (b >> 8) & 0xFF, bb = b & 0xFF;
    return (unsigned int)(((ar + (br - ar) * t / n) << 16) | ((ag + (bg - ag) * t / n) << 8) |
                          (ab + (bb - ab) * t / n));
}

static void brand_bar(int x, int y, int w, int h) {
    enum { STEPS = 32, HALF = STEPS / 2 };
    for (int i = 0; i < STEPS; i++) {
        unsigned int c = i < HALF ? brand_lerp(COL_BRAND_A, COL_BRAND_B, i, HALF)
                                  : brand_lerp(COL_BRAND_B, COL_BRAND_C, i - HALF, HALF - 1);
        int x0 = x + w * i / STEPS, x1 = x + w * (i + 1) / STEPS;
        gfx_rect(x0, y, x1 - x0, h, c);
    }
}

static void draw_header(void)
{
    gfx_rect(0, 0, GFX_W, HEADER_H, COL_BG2);
    brand_bar(0, HEADER_H - 3, GFX_W, 3);
    gfx_text(MARGIN, 28, FONT_TITLE, COL_TEXT, ALIGN_LEFT, "GameSync");
    gfx_text(MARGIN + gfx_text_width(FONT_TITLE, "GameSync") + 14, 40, FONT_SMALL, COL_MUTED,
             ALIGN_LEFT, "PS5 v" APP_VERSION);

    /* tab strip between L1/R1, right-aligned before the server dot */
    char names[TAB_COUNT][32];
    int widths[TAB_COUNT], total = 0;
    for (int i = 0; i < TAB_COUNT; ++i)
    {
        int active = i == TAB_DOWNLOADS ? downloads_active() : 0;
        if (active)
            snprintf(names[i], sizeof(names[i]), "%s (%d)", TAB_NAMES[i], active);
        else
            snprintf(names[i], sizeof(names[i]), "%s", TAB_NAMES[i]);
        widths[i] = gfx_text_width(FONT_BODY, names[i]) + 44;
        total += widths[i] + 6;
    }
    int x = GFX_W - MARGIN - 40 - total - 110;
    x += draw_glyph(x, HEADER_H / 2, G_L1) + 12;
    for (int i = 0; i < TAB_COUNT; ++i)
    {
        if ((int)tab == i)
            gfx_round_rect(x, HEADER_H / 2 - 26, widths[i], 52, 26, COL_ACCENT);
        gfx_text(x + widths[i] / 2, HEADER_H / 2 - 19, FONT_BODY, (int)tab == i ? COL_INK : COL_DIM,
                 ALIGN_CENTER, names[i]);
        x += widths[i] + 6;
    }
    draw_glyph(x + 6, HEADER_H / 2, G_R1);
    uint32_t dot = server_state < 0 ? COL_MUTED : server_state ? COL_OK : COL_ERR;
    gfx_disc((float)(GFX_W - MARGIN - 10), HEADER_H / 2.0f, 10, dot);
}

/* chips row: [L2] chips [R2] on the left part, extra text on the right */
static void draw_chips(const Chips *c, const char *left_note, const char *right)
{
    int x = MARGIN, cy = CHIPS_Y + CHIPS_H / 2;
    if (left_note)
    {
        x += gfx_text(x, cy - 15, FONT_SMALL, COL_DIM, ALIGN_LEFT, left_note) + 30;
    }
    if (c && c->count > 1)
    {
        x += draw_glyph(x, cy, G_L2) + 10;
        /* start far enough left that the selected chip shows */
        int first = c->sel > 5 ? c->sel - 5 : 0;
        int limit = GFX_W - MARGIN - 420;
        for (int i = first; i < c->count; ++i)
        {
            const char *name = i == 0 ? "All" : c->systems[i];
            int w = gfx_text_width(FONT_SMALL, name) + 30;
            if (x + w > limit)
            {
                gfx_text(x, cy - 15, FONT_SMALL, COL_MUTED, ALIGN_LEFT, "...");
                x += 40;
                break;
            }
            bool on = i == c->sel;
            gfx_round_rect(x, cy - 19, w, 38, 19, on ? COL_ACCENT : COL_PANEL);
            gfx_text(x + w / 2, cy - 15, FONT_SMALL, on ? COL_INK : COL_DIM, ALIGN_CENTER, name);
            x += w + 8;
        }
        draw_glyph(x + 4, cy, G_R2);
    }
    if (right)
        gfx_text(GFX_W - MARGIN, cy - 15, FONT_SMALL, COL_DIM, ALIGN_RIGHT, right);
}

static void draw_footer(const Hint *hints, int n)
{
    if (banner[0])
        gfx_text_fit(MARGIN, BANNER_Y + 6, FONT_SMALL, banner_color, ALIGN_LEFT,
                     GFX_W - 2 * MARGIN, banner);
    gfx_rect(0, FOOTER_Y, GFX_W, GFX_H - FOOTER_Y, COL_BG2);
    int cy = FOOTER_Y + (GFX_H - FOOTER_Y) / 2;
    draw_hints(MARGIN, cy, hints, n);
    /* right side: always tabs + exit */
    int w = draw_glyph(0, -100, G_OPTIONS); /* measure */
    int x = GFX_W - MARGIN - w - 10 - gfx_text_width(FONT_SMALL, "Exit");
    int tabs_w = gfx_text_width(FONT_SMALL, "Tab") + 10 + 2 * 46;
    x -= tabs_w + 40;
    x += draw_glyph(x, cy, G_L1) + 4;
    x += draw_glyph(x, cy, G_R1) + 10;
    x += gfx_text(x, cy - 15, FONT_SMALL, COL_DIM, ALIGN_LEFT, "Tab") + 40;
    x += draw_glyph(x, cy, G_OPTIONS) + 10;
    gfx_text(x, cy - 15, FONT_SMALL, COL_DIM, ALIGN_LEFT, "Exit");
}

static void draw_empty(const char *title, const char *line)
{
    gfx_text(MARGIN + LIST_W / 2, 420, FONT_TITLE, COL_TEXT, ALIGN_CENTER, title);
    if (line)
        gfx_text_fit(MARGIN + LIST_W / 2, 486, FONT_BODY, COL_DIM, ALIGN_CENTER, LIST_W - 40, line);
}

static void draw_scrollbar(const View *v)
{
    if (v->count <= VISIBLE)
        return;
    int x = MARGIN + LIST_W + 10, y = LIST_Y, h = VISIBLE * ROW_H;
    gfx_rect(x, y, 6, h, COL_PANEL);
    int bar = h * VISIBLE / v->count;
    if (bar < 30)
        bar = 30;
    int pos = (h - bar) * v->top / (v->count - VISIBLE);
    gfx_rect(x, y + pos, 6, bar, COL_MUTED);
}

static void draw_row_base(int r, bool selected)
{
    int y = LIST_Y + r * ROW_H;
    gfx_round_rect(MARGIN, y, LIST_W, ROW_H - 8, 10, selected ? COL_PANEL_HI : COL_PANEL);
    if (selected)
        gfx_rect(MARGIN, y + 6, 5, ROW_H - 20, COL_ACCENT);
}

/* detail panel: title, then label/value rows */
static int detail_y;
static void detail_begin(const char *title)
{
    gfx_round_rect(DETAIL_X, LIST_Y, DETAIL_W, BANNER_Y - 12 - LIST_Y, 14, COL_PANEL);
    gfx_text_fit(DETAIL_X + 28, LIST_Y + 22, FONT_BODY, COL_TEXT, ALIGN_LEFT, DETAIL_W - 56, title);
    detail_y = LIST_Y + 76;
}

static int detail_step = 68; /* row pitch; Settings packs its rows tighter */

static void detail_row(const char *label, const char *value, uint32_t color)
{
    if (!value || !value[0])
        return;
    gfx_text(DETAIL_X + 28, detail_y, FONT_SMALL, COL_MUTED, ALIGN_LEFT, label);
    gfx_text_fit(DETAIL_X + 28, detail_y + 26, FONT_SMALL, color, ALIGN_LEFT, DETAIL_W - 56, value);
    detail_y += detail_step;
}

static void detail_hint(const char *text)
{
    gfx_text_fit(DETAIL_X + 28, BANNER_Y - 60, FONT_SMALL, COL_DIM, ALIGN_LEFT, DETAIL_W - 56, text);
}

/* ------------------------------------------------------------ saves */

static uint32_t status_color(SaveStatus s)
{
    switch (s)
    {
    case ST_SYNCED: return COL_OK;
    case ST_UPLOAD:
    case ST_DOWNLOAD:
    case ST_LOCAL_ONLY: return COL_INFO;
    case ST_SERVER_ONLY: return COL_WARN;
    case ST_CONFLICT:
    case ST_ERROR: return COL_ERR;
    default: return COL_MUTED;
    }
}

static bool status_passes(SaveStatus s)
{
    switch (save_status)
    {
    case SF_ACTION: return s != ST_SYNCED && s != ST_NONE && s != ST_UNKNOWN;
    case SF_UPLOAD: return s == ST_UPLOAD;
    case SF_DOWNLOAD: return s == ST_DOWNLOAD;
    case SF_CONFLICT: return s == ST_CONFLICT;
    case SF_SYNCED: return s == ST_SYNCED;
    case SF_LOCAL: return s == ST_LOCAL_ONLY;
    case SF_SERVER: return s == ST_SERVER_ONLY;
    default: return true;
    }
}

static void rebuild_saves(void)
{
    const char *keep = chips_current(&save_chips);
    char kept[12] = "";
    if (keep)
        snprintf(kept, sizeof(kept), "%s", keep);
    chips_reset(&save_chips);
    for (int i = 0; i < sync_ctx.saves.count; ++i)
        chips_add(&save_chips, sync_ctx.saves.items[i].system);
    chips_pick(&save_chips, kept);
    const char *sys = chips_current(&save_chips);
    save_view.count = 0;
    for (int i = 0; i < sync_ctx.saves.count; ++i)
    {
        const SaveEntry *e = &sync_ctx.saves.items[i];
        if (sys && !util_ieq(e->system, sys))
            continue;
        if (!status_passes(e->status))
            continue;
        view_push(&save_view, i);
    }
    view_clamp(&save_view);
}

static const char *save_name(int i)
{
    return sync_ctx.saves.items[i].name;
}

static SaveEntry *selected_save(void)
{
    if (!save_view.count)
        return NULL;
    return &sync_ctx.saves.items[save_view.idx[save_view.sel]];
}

static void short_hash(char *out, size_t size, const char *hash)
{
    snprintf(out, size, "%.12s", hash[0] ? hash : "-");
}

static void draw_saves(void)
{
    char right[64];
    snprintf(right, sizeof(right), "%d saves", save_view.count);
    char note[64];
    snprintf(note, sizeof(note), "Status: %s", STATUS_FILTER_NAMES[save_status]);
    draw_chips(&save_chips, note, right);
    static const Hint hints[] = {{G_CROSS, "Sync"},   {G_SQUARE, "Sync all"}, {G_TRIANGLE, "Details"},
                                 {G_CREATE, "Status"}, {G_L2, "System"}};
    if (!saves_loaded)
    {
        draw_empty("Looking for saves...", NULL);
        draw_footer(hints, 5);
        return;
    }
    if (!save_view.count)
    {
        draw_empty(sync_ctx.saves.count ? "Nothing matches this filter" : "No saves found",
                   sync_ctx.error[0] ? sync_ctx.error
                                     : "RetroArch, SwanStation and PS5SX2 saves show up here");
        draw_footer(hints, 5);
        return;
    }
    for (int r = 0; r < VISIBLE && save_view.top + r < save_view.count; ++r)
    {
        int i = save_view.top + r;
        const SaveEntry *e = &sync_ctx.saves.items[save_view.idx[i]];
        int y = LIST_Y + r * ROW_H;
        draw_row_base(r, i == save_view.sel);
        draw_system_badge(MARGIN + 18, y + 18, e->system);
        int pill = draw_pill(MARGIN + LIST_W - 18, y + 16, save_status_text(e->status),
                             status_color(e->status));
        gfx_text_fit(MARGIN + 138, y + 6, FONT_BODY, COL_TEXT, ALIGN_LEFT, LIST_W - 180 - pill,
                     e->name);
        char sub[128], size[32];
        if (e->local)
            util_format_size(size, sizeof(size), e->local_size);
        snprintf(sub, sizeof(sub), "%s  -  %s", emulator_name(e->emu), e->local ? size : "no save");
        gfx_text_fit(MARGIN + 138, y + 42, FONT_SMALL, COL_DIM, ALIGN_LEFT, LIST_W - 180 - pill, sub);
    }
    draw_scrollbar(&save_view);

    const SaveEntry *e = selected_save();
    detail_begin(e->name);
    int px = DETAIL_X + 28;
    px += draw_pill(px + gfx_text_width(FONT_SMALL, save_status_text(e->status)) + 28, detail_y - 6,
                    save_status_text(e->status), status_color(e->status)) +
          10;
    draw_system_badge(px, detail_y - 6, e->system);
    detail_y += 50;
    char h1[16], h2[16], s1[32] = "", s2[32] = "";
    short_hash(h1, sizeof(h1), e->local ? e->local_hash : "");
    short_hash(h2, sizeof(h2), e->server ? e->server_hash : "");
    if (e->local)
        util_format_size(s1, sizeof(s1), e->local_size);
    if (e->server)
        util_format_size(s2, sizeof(s2), e->server_size);
    detail_row("Title ID", e->title_id[0] ? e->title_id : "(unknown)", COL_TEXT);
    detail_row("Emulator", emulator_name(e->emu), COL_TEXT);
    if (e->kind == SAVE_PS2_FOLDER)
    {
        char where[160];
        snprintf(where, sizeof(where), "%s / %s", util_basename(e->path),
                 e->ps2_dir[0] ? e->ps2_dir : "(new)");
        detail_row("Card / folder", where, COL_TEXT);
    }
    else
        detail_row("Local save", util_basename(e->path), COL_TEXT);
    detail_row("Local size", e->local ? s1 : "no save", COL_TEXT);
    detail_row("Server size", e->server ? s2 : "no save", COL_TEXT);
    detail_row("Local hash", h1, COL_DIM);
    detail_row("Server hash", h2, COL_DIM);
    if (e->message[0])
        detail_row("Problem", e->message, COL_ERR);
    static const char *const hint_for[] = {
        [ST_UNKNOWN] = "Not compared with the server yet.",
        [ST_SYNCED] = "Both sides match.",
        [ST_UPLOAD] = "Only this console changed it: Cross uploads.",
        [ST_DOWNLOAD] = "Only the server changed it: Cross downloads.",
        [ST_CONFLICT] = "Both changed: Triangle to pick a side.",
        [ST_LOCAL_ONLY] = "Not on the server yet: Cross uploads.",
        [ST_SERVER_ONLY] = "Only on the server: Cross downloads.",
        [ST_NONE] = "No save on either side.",
        [ST_ERROR] = "Triangle to retry by hand.",
    };
    if (e->kind == SAVE_XBOX && (e->status == ST_DOWNLOAD || e->status == ST_SERVER_ONLY ||
                                 e->status == ST_CONFLICT))
        detail_hint("Quit XPSemu before downloading: it writes into its disk image "
                    "(backed up first as xbox_hdd.qcow2.bak).");
    else
        detail_hint(hint_for[e->status]);
    if (sync_ctx.saves.warning[0])
        set_banner(COL_WARN, "%s", sync_ctx.saves.warning);
    draw_footer(hints, 5);
}

/* ------------------------------------------------------------ catalog */

/* The catalog filter a row goes under: its system, or for an MSU pack
 * (enhanced-audio hack, a folder of ROM + audio) "SNES MSU" / "MD MSU", as
 * the MiSTer client's "SNES: MSU packs" stops. */
static const char *catalog_category(const RomRow *r)
{
    if (!r->is_bundle || !r->bundle_kind[0])
        return r->system;
    if (util_ieq(r->bundle_kind, "msu1"))
        return "SNES MSU";
    if (util_ieq(r->bundle_kind, "msu-md") || util_ieq(r->bundle_kind, "mdplus"))
        return "MD MSU";
    return r->system;
}

static void rebuild_catalog(void)
{
    const char *keep = chips_current(&cat_chips);
    char kept[12] = "";
    if (keep)
        snprintf(kept, sizeof(kept), "%s", keep);
    chips_reset(&cat_chips);
    for (int i = 0; i < catalog.system_count; ++i)
    {
        if (cat_ra_only)
        {
            bool any = false;
            for (int k = 0; k < catalog.rom_count && !any; ++k)
                any = util_ieq(catalog.roms[k].system, catalog.systems[i].system) &&
                      catalog.roms[k].ra_achievements > 0;
            if (!any)
                continue;
        }
        chips_add(&cat_chips, catalog.systems[i].system);
    }
    /* the MSU stops, for systems that have packs */
    for (int k = 0; k < catalog.rom_count; ++k)
        if (catalog_category(&catalog.roms[k]) != catalog.roms[k].system &&
            (!cat_ra_only || catalog.roms[k].ra_achievements > 0))
            chips_add(&cat_chips, catalog_category(&catalog.roms[k]));
    chips_pick(&cat_chips, kept);
    const char *sys = chips_current(&cat_chips);
    cat_view.count = 0;
    for (int i = 0; i < catalog.rom_count; ++i)
    {
        const RomRow *r = &catalog.roms[i];
        if (sys && !util_ieq(catalog_category(r), sys))
            continue;
        if (cat_ra_only && r->ra_achievements <= 0)
            continue;
        if (cat_query[0] && !catalog_matches(r, cat_query))
            continue;
        if (r->group[0] && r->group_head != i)
            continue; /* a multi-disc game is one row, its first disc's */
        view_push(&cat_view, i);
    }
    view_clamp(&cat_view);
}

static const char *cat_name(int i)
{
    const RomRow *r = &catalog.roms[i];
    return r->group[0] ? r->group : r->name;
}

/* A row's size, all its discs' for a multi-disc game. */
static uint64_t group_size(const RomRow *rom)
{
    return rom->group[0] ? rom->group_bytes : rom->size;
}

static uint8_t rom_state(int i)
{
    if (!cat_state)
        return 1;
    if (!cat_state[i] && catalog.roms[i].group[0])
    {
        InstallPlan plan;
        catalog_plan(&sync_ctx.config, &catalog.roms[i], &plan);
        cat_state[i] = catalog_group_installed(&sync_ctx.config, &catalog, &catalog.roms[i]) ? 2
                       : plan.supported                                                      ? 1
                                                                                             : 3;
    }
    if (!cat_state[i])
    {
        InstallPlan plan;
        catalog_plan(&sync_ctx.config, &catalog.roms[i], &plan);
        InstallPlan cue;
        catalog_plan_cue(&sync_ctx.config, &catalog.roms[i], &cue);
        cat_state[i] = catalog_installed(&plan) || catalog_installed(&cue) ? 2
                       : !plan.supported && !cue.supported                 ? 3
                                                                           : 1;
    }
    return cat_state[i];
}

static void draw_catalog(void)
{
    char right[96];
    snprintf(right, sizeof(right), "%d games%s%s", cat_view.count, cat_ra_only ? "  -  RA only" : "",
             catalog.offline ? "  -  offline" : "");
    char note[120] = "";
    if (cat_query[0])
        snprintf(note, sizeof(note), "Search: %s", cat_query);
    draw_chips(&cat_chips, note[0] ? note : NULL, right);
    bool sel_cue = catalog_loaded && cat_view.count && catalog.roms[cat_view.idx[cat_view.sel]].has_cue;
    Hint hints[] = {{G_CROSS, "Download"},
                    {G_SQUARE, cat_ra_only ? "Show all" : "RA only"},
                    {G_TRIANGLE, "Search"},
                    {G_CIRCLE, sel_cue ? "As CUE/BIN" : cat_query[0] ? "Clear search" : NULL},
                    {G_L2, "System"}};
    if (!catalog_loaded)
    {
        draw_empty(catalog_error[0] ? "Couldn't load the catalog" : "Loading the catalog...",
                   catalog_error[0] ? catalog_error : NULL);
        draw_footer(hints, 5);
        return;
    }
    if (!cat_view.count)
    {
        draw_empty("No games match", cat_query[0] ? "Circle clears the search" : NULL);
        draw_footer(hints, 5);
        return;
    }
    for (int r = 0; r < VISIBLE && cat_view.top + r < cat_view.count; ++r)
    {
        int i = cat_view.top + r;
        int ri = cat_view.idx[i];
        const RomRow *rom = &catalog.roms[ri];
        int y = LIST_Y + r * ROW_H;
        draw_row_base(r, i == cat_view.sel);
        draw_system_badge(MARGIN + 18, y + 18, rom->system);
        uint8_t st = rom_state(ri);
        char size[32];
        util_format_size(size, sizeof(size), group_size(rom));
        int right_w = st == 2 ? draw_pill(MARGIN + LIST_W - 18, y + 16, "Installed", COL_OK)
                              : gfx_text(MARGIN + LIST_W - 18, y + 20, FONT_SMALL, COL_DIM,
                                         ALIGN_RIGHT, size);
        int name_x = MARGIN + 138;
        int ra_w = draw_ra_badge(MARGIN + LIST_W - 36 - right_w - 70, y + 18, catalog_ra_level(rom));
        (void)ra_w;
        char discs[48] = "";
        if (rom->group[0])
            snprintf(discs, sizeof(discs), "%d discs", rom->group_size);
        gfx_text_fit(name_x, y + 6, FONT_BODY, st == 3 ? COL_MUTED : COL_TEXT, ALIGN_LEFT,
                     LIST_W - 160 - right_w - 120, rom->group[0] ? rom->group : rom->name);
        gfx_text_fit(name_x, y + 42, FONT_SMALL, COL_DIM, ALIGN_LEFT, LIST_W - 160 - right_w - 120,
                     rom->group[0] ? discs : rom->filename);
    }
    draw_scrollbar(&cat_view);

    const RomRow *rom = &catalog.roms[cat_view.idx[cat_view.sel]];
    InstallPlan plan;
    catalog_plan(&sync_ctx.config, rom, &plan);
    detail_begin(rom->group[0] ? rom->group : rom->name);
    int px = DETAIL_X + 28;
    draw_system_badge(px, detail_y - 6, rom->system);
    draw_ra_badge(px + 114, detail_y - 5, catalog_ra_level(rom));
    detail_y += 50;
    char size[32], ra[96] = "", disc[32] = "";
    util_format_size(size, sizeof(size), group_size(rom));
    if (rom->ra_achievements > 0)
        snprintf(ra, sizeof(ra), "%d achievements%s", rom->ra_achievements,
                 catalog_ra_level(rom) == RA_TITLE ? " (matched by name: this dump may not load them)"
                                                   : "");
    if (rom->group[0])
        snprintf(disc, sizeof(disc), "%d discs, installed together", rom->group_size);
    else if (rom->disc_total > 1)
        snprintf(disc, sizeof(disc), "%d of %d", rom->disc_index, rom->disc_total);
    detail_row("File", rom->filename, COL_TEXT);
    detail_row("Size", size, COL_TEXT);
    detail_row("Title ID", rom->title_id, COL_TEXT);
    detail_row("Disc", disc, COL_TEXT);
    if (rom->has_cue)
        detail_row("Also as", "CUE/BIN set: Circle (the server converts it)", COL_ACCENT2);
    detail_row("RetroAchievements", ra, COL_RA);
    detail_row("Emulator", emulator_name(plan.emu), COL_TEXT);
    char where[PATH_LEN];
    util_copy(where, sizeof(where), plan.m3u[0] ? plan.m3u : plan.dest);
    if (rom->group[0] && !plan.m3u[0])
    {
        char *slash = strrchr(where, '/'); /* the game's folder */
        if (slash)
            slash[1] = 0;
    }
    detail_row(plan.supported ? "Goes to" : "Can't install", plan.supported ? where : plan.reason,
               plan.supported ? COL_DIM : COL_WARN);
    detail_hint(rom->group[0] ? "Cross queues every disc on the Downloads tab."
                              : "Cross queues it on the Downloads tab.");
    draw_footer(hints, 5);
}

/* ------------------------------------------------------------ installed */

static const char *inst_name(int i)
{
    return inst.items[i].name;
}

static void rebuild_installed(void)
{
    const char *keep = chips_current(&inst_chips);
    char kept[12] = "";
    if (keep)
        snprintf(kept, sizeof(kept), "%s", keep);
    chips_reset(&inst_chips);
    for (int i = 0; i < inst.count; ++i)
        chips_add(&inst_chips, inst.items[i].system);
    chips_pick(&inst_chips, kept);
    const char *sys = chips_current(&inst_chips);
    inst_view.count = 0;
    for (int i = 0; i < inst.count; ++i)
    {
        const InstalledGame *g = &inst.items[i];
        if (sys && !util_ieq(g->system, sys))
            continue;
        if (inst_query[0])
        {
            RomRow probe;
            memset(&probe, 0, sizeof(probe));
            snprintf(probe.name, sizeof(probe.name), "%s", g->name);
            snprintf(probe.filename, sizeof(probe.filename), "%s", g->filename);
            if (!catalog_matches(&probe, inst_query))
                continue;
        }
        view_push(&inst_view, i);
    }
    view_clamp(&inst_view);
}

static void draw_installed(void)
{
    char right[64];
    snprintf(right, sizeof(right), "%d games", inst_view.count);
    char note[120] = "";
    if (inst_query[0])
        snprintf(note, sizeof(note), "Search: %s", inst_query);
    draw_chips(&inst_chips, note[0] ? note : NULL, right);
    Hint hints[] = {{G_CROSS, "Delete"},
                    {G_SQUARE, "Rescan"},
                    {G_TRIANGLE, "Search"},
                    {G_CIRCLE, inst_query[0] ? "Clear search" : NULL},
                    {G_L2, "System"}};
    if (!inst_view.count)
    {
        draw_empty(inst.count ? "No games match" : "No games installed",
                   inst.count ? NULL : "Games installed from the Catalog show up here");
        draw_footer(hints, 5);
        return;
    }
    for (int r = 0; r < VISIBLE && inst_view.top + r < inst_view.count; ++r)
    {
        int i = inst_view.top + r;
        const InstalledGame *g = &inst.items[inst_view.idx[i]];
        int y = LIST_Y + r * ROW_H;
        draw_row_base(r, i == inst_view.sel);
        draw_system_badge(MARGIN + 18, y + 18, g->system);
        char size[32];
        util_format_size(size, sizeof(size), g->size);
        int right_w = gfx_text(MARGIN + LIST_W - 18, y + 20, FONT_SMALL, COL_DIM, ALIGN_RIGHT, size);
        const RomRow *row =
            catalog_loaded ? catalog_find_file(&catalog, g->system, g->filename) : NULL;
        if (row)
            draw_ra_badge(MARGIN + LIST_W - 36 - right_w - 70, y + 18, catalog_ra_level(row));
        gfx_text_fit(MARGIN + 138, y + 6, FONT_BODY, COL_TEXT, ALIGN_LEFT, LIST_W - 280 - right_w,
                     g->name);
        char sub[96];
        if (g->discs > 1)
            snprintf(sub, sizeof(sub), "%s  -  %d discs", emulator_name(g->emu), g->discs);
        else
            snprintf(sub, sizeof(sub), "%s", emulator_name(g->emu));
        gfx_text_fit(MARGIN + 138, y + 42, FONT_SMALL, COL_DIM, ALIGN_LEFT, LIST_W - 280 - right_w,
                     sub);
    }
    draw_scrollbar(&inst_view);

    const InstalledGame *g = &inst.items[inst_view.idx[inst_view.sel]];
    detail_begin(g->name);
    draw_system_badge(DETAIL_X + 28, detail_y - 6, g->system);
    const RomRow *row = catalog_loaded ? catalog_find_file(&catalog, g->system, g->filename) : NULL;
    if (row)
        draw_ra_badge(DETAIL_X + 142, detail_y - 5, catalog_ra_level(row));
    detail_y += 50;
    char size[32], ra[64] = "";
    util_format_size(size, sizeof(size), g->size);
    if (row && row->ra_achievements > 0)
        snprintf(ra, sizeof(ra), "%d achievements", row->ra_achievements);
    detail_row("File", g->filename, COL_TEXT);
    detail_row("Size", size, COL_TEXT);
    detail_row("Emulator", emulator_name(g->emu), COL_TEXT);
    detail_row("RetroAchievements", ra, COL_RA);
    detail_row("Path", g->path, COL_DIM);
    draw_footer(hints, 5);
}

/* ------------------------------------------------------------ downloads */

static void draw_downloads(void)
{
    dl_count = downloads_snapshot(dl_items, 256);
    if (dl_sel >= dl_count)
        dl_sel = dl_count ? dl_count - 1 : 0;
    char right[64];
    snprintf(right, sizeof(right), "%d active", downloads_active());
    draw_chips(NULL, NULL, right);
    const DlItem *sel = dl_count ? &dl_items[dl_sel] : NULL;
    const char *main_action = !sel                        ? NULL
                              : sel->state == DL_RUNNING   ? "Pause"
                              : sel->state == DL_QUEUED    ? "Pause"
                              : sel->state == DL_PAUSED    ? "Resume"
                              : sel->state == DL_FAILED    ? "Retry"
                                                           : NULL;
    Hint hints[] = {{G_CROSS, main_action},
                    {G_SQUARE, "Clear finished"},
                    {G_TRIANGLE, sel && sel->state != DL_RUNNING ? "Remove" : NULL},
                    {G_CIRCLE, "Pause running"}};
    if (!dl_count)
    {
        draw_empty("No downloads", "Cross on a Catalog game queues it here");
        draw_footer(hints, 4);
        return;
    }
    int top = dl_sel >= VISIBLE ? dl_sel - VISIBLE + 1 : 0;
    for (int r = 0; r < VISIBLE && top + r < dl_count; ++r)
    {
        const DlItem *d = &dl_items[top + r];
        int y = LIST_Y + r * ROW_H;
        draw_row_base(r, top + r == dl_sel);
        draw_system_badge(MARGIN + 18, y + 18, d->rom.system);
        uint32_t color = d->state == DL_DONE     ? COL_OK
                         : d->state == DL_FAILED ? COL_ERR
                         : d->state == DL_PAUSED ? COL_WARN
                                                 : COL_INFO;
        int pill = draw_pill(MARGIN + LIST_W - 18, y + 16, download_state_text(d->state), color);
        gfx_text_fit(MARGIN + 138, y + 6, FONT_BODY, COL_TEXT, ALIGN_LEFT, LIST_W - 180 - pill,
                     d->rom.name);
        char sub[160];
        if (d->state == DL_RUNNING && d->total)
        {
            char a[32], b[32];
            util_format_size(a, sizeof(a), d->done);
            util_format_size(b, sizeof(b), d->total);
            if (d->speed > 0)
                snprintf(sub, sizeof(sub), "%s / %s  %.1f MB/s", a, b, d->speed / 1e6);
            else
                snprintf(sub, sizeof(sub), "%s / %s", a, b);
            int bar_x = MARGIN + 138 + 300, bar_w = LIST_W - 180 - pill - 320;
            gfx_round_rect(bar_x, y + 50, bar_w, 10, 5, COL_BG);
            uint64_t done = d->done > d->total ? d->total : d->done;
            int fill = (int)((uint64_t)bar_w * done / d->total);
            if (fill > 10)
                gfx_round_rect(bar_x, y + 50, fill, 10, 5, COL_ACCENT);
        }
        else if (d->state == DL_FAILED)
            snprintf(sub, sizeof(sub), "%s", d->error);
        else
            snprintf(sub, sizeof(sub), "%s", emulator_name(d->plan.emu));
        gfx_text_fit(MARGIN + 138, y + 42, FONT_SMALL, d->state == DL_FAILED ? COL_ERR : COL_DIM,
                     ALIGN_LEFT, 290, sub);
    }
    detail_begin(sel->rom.name);
    char size[32];
    util_format_size(size, sizeof(size), sel->rom.size);
    detail_row("Status", download_state_text(sel->state), COL_TEXT);
    detail_row("Size", size, COL_TEXT);
    char speed[32] = "";
    if (sel->state == DL_RUNNING && sel->speed > 0)
        snprintf(speed, sizeof(speed), "%.1f MB/s", sel->speed / 1e6);
    detail_row("Speed", speed, COL_TEXT);
    detail_row("Emulator", emulator_name(sel->plan.emu), COL_TEXT);
    detail_row("Goes to", sel->plan.cue ? sel->plan.unzip_dir : sel->plan.dest, COL_DIM);
    detail_row("Error", sel->error, COL_ERR);
    detail_hint("A paused download keeps its .part file and resumes where it stopped.");
    draw_footer(hints, 4);
}

/* ------------------------------------------------------------ settings */

enum
{
    SET_SERVER,
    SET_KEY,
    SET_PS1_TARGET,
    SET_PS2_CARD,
    SET_TEST,
    SET_REFRESH_CATALOG,
    SET_RESCAN,
    SET_COUNT
};

static void draw_settings(void)
{
    draw_chips(NULL, NULL, NULL);
    const Config *c = &sync_ctx.config;
    char masked[64] = "(not set)";
    size_t klen = strlen(c->api_key);
    if (klen)
    {
        size_t n = klen < 24 ? klen : 24;
        for (size_t i = 0; i < n; ++i)
            masked[i] = i < 4 ? c->api_key[i] : '*';
        masked[n] = 0;
    }
    char ps2_cards[96];
    if (c->ps2_per_game)
        util_copy(ps2_cards, sizeof(ps2_cards), "One per game");
    else
        snprintf(ps2_cards, sizeof(ps2_cards), "Shared: %s", c->ps2_card);
    const char *labels[SET_COUNT] = {"Server URL",
                                     "API key",
                                     "PS1 games install to",
                                     "PS5SX2 memory cards",
                                     "Test connection",
                                     "Refresh catalog",
                                     "Rescan saves and games"};
    const char *values[SET_COUNT] = {c->server_url[0] ? c->server_url : "(not set)",
                                     masked,
                                     c->ps1_in_retroarch ? "RetroArch" : "SwanStation",
                                     ps2_cards,
                                     "",
                                     "",
                                     ""};
    for (int i = 0; i < SET_COUNT; ++i)
    {
        int y = LIST_Y + i * ROW_H;
        draw_row_base(i, i == set_sel);
        gfx_text(MARGIN + 30, y + 18, FONT_BODY, COL_TEXT, ALIGN_LEFT, labels[i]);
        gfx_text_fit(MARGIN + LIST_W - 30, y + 18, FONT_BODY, COL_ACCENT2, ALIGN_RIGHT, 560,
                     values[i]);
    }
    detail_begin("This console");
    detail_step = 58;
    detail_row("Console id", c->console_id, COL_TEXT);
    detail_row("/data access", data_status, data_ok ? COL_OK : COL_WARN);
    detail_row("Settings file", data_path("config.txt"), COL_DIM);
    /* which emulator folders exist: checked once, not every frame */
    if (!emu_dirs_checked)
    {
        const char *dirs[7] = {c->retroarch_dir, c->swanstation_dir, c->ps5sx2_dir,
                               c->porpoise_dir, c->eden_dir, c->cemu_dir, c->xemu_dir};
        for (int i = 0; i < 7; ++i)
            emu_dir_found[i] = util_dir_exists(dirs[i]);
        SsInfo ss;
        saves_swanstation_info(c, &ss);
        bool main_profile = strcmp(ss.user_dir, c->swanstation_dir) == 0;
        snprintf(ss_mode_text, sizeof(ss_mode_text), "%s%s%s",
                 saves_swanstation_mode_name(ss.mode), main_profile ? "" : "  -  profile ",
                 main_profile ? "" : util_basename(ss.user_dir));
        emu_dirs_checked = true;
    }
    static const char *const names[7] = {"RetroArch", "SwanStation", "PS5SX2", "Porpoise",
                                         "ProsperoEden", "PS5CEMU (Wii U, 3DS)",
                                         "XPSemu (games only)"};
    const char *dirs[7] = {c->retroarch_dir, c->swanstation_dir, c->ps5sx2_dir, c->porpoise_dir,
                           c->eden_dir, c->cemu_dir, c->xemu_dir};
    for (int i = 0; i < 7; ++i)
    {
        char label[64];
        snprintf(label, sizeof(label), "%s%s", names[i], emu_dir_found[i] ? "" : "  (not found)");
        detail_row(label, dirs[i], emu_dir_found[i] ? COL_TEXT : COL_MUTED);
    }
    if (emu_dir_found[1])
        detail_row("SwanStation memory cards", ss_mode_text, COL_DIM);
    detail_step = 68;
    static const Hint hints[] = {{G_CROSS, "Change"}};
    draw_footer(hints, 1);
}

/* ------------------------------------------------------------ frames */

static void draw_view(void)
{
    gfx_clear(COL_BG);
    draw_header();
    switch (tab)
    {
    case TAB_SAVES: draw_saves(); break;
    case TAB_CATALOG: draw_catalog(); break;
    case TAB_INSTALLED: draw_installed(); break;
    case TAB_DOWNLOADS: draw_downloads(); break;
    default: draw_settings(); break;
    }
}

static void draw_modal_box(int w, int h, int *x, int *y)
{
    gfx_rect_blend(0, 0, GFX_W, GFX_H, 0x000000, 170);
    *x = (GFX_W - w) / 2;
    *y = (GFX_H - h) / 2;
    gfx_round_rect(*x, *y, w, h, 20, COL_PANEL_HI);
}

/* Progress overlay, drawn from inside long operations. */
typedef struct
{
    const char *title;
    char message[200];
    uint64_t done;
    uint64_t total;
    uint64_t last_frame;
} Busy;

static void busy_draw(Busy *b, bool force)
{
    uint64_t now = plat_ticks_ms();
    if (!force && now - b->last_frame < 33)
        return;
    b->last_frame = now;
    read_input();
    draw_view();
    int x, y;
    draw_modal_box(1000, 280, &x, &y);
    gfx_text(x + 48, y + 40, FONT_TITLE, COL_TEXT, ALIGN_LEFT, b->title);
    gfx_text_fit(x + 48, y + 112, FONT_BODY, COL_DIM, ALIGN_LEFT, 904, b->message);
    int bar_y = y + 190;
    gfx_round_rect(x + 48, bar_y, 904, 20, 10, COL_PANEL);
    if (b->total)
    {
        uint64_t done = b->done > b->total ? b->total : b->done;
        int fill = (int)(904 * done / b->total);
        if (fill > 0)
            gfx_round_rect(x + 48, bar_y, fill < 20 ? 20 : fill, 20, 10, COL_ACCENT);
    }
    else
    {
        int pos = (int)((now / 3) % 1104) - 200;
        int bx = x + 48 + (pos < 0 ? 0 : pos), bw = 200 + (pos < 0 ? pos : 0);
        if (bx + bw > x + 952)
            bw = x + 952 - bx;
        if (bw > 0)
            gfx_rect(bx, bar_y + 4, bw, 12, COL_ACCENT);
    }
    plat_present();
}

static void busy_progress(const char *message, int done, int total, void *user)
{
    Busy *b = user;
    snprintf(b->message, sizeof(b->message), "%s", message);
    b->done = (uint64_t)(done < 0 ? 0 : done);
    b->total = (uint64_t)(total < 0 ? 0 : total);
    busy_draw(b, false);
}

/* A question with up to four answers; returns the chosen index, -1 on Circle. */
static int ui_choose(const char *title, const char *body, const char *const *options, int count)
{
    int sel = 0;
    while (!quit)
    {
        uint32_t pressed = read_input();
        if (pressed & BTN_UP)
            sel = sel > 0 ? sel - 1 : count - 1;
        if (pressed & BTN_DOWN)
            sel = (sel + 1) % count;
        if (pressed & BTN_CROSS)
            return sel;
        if (pressed & BTN_CIRCLE)
            return -1;
        draw_view();
        int lines = 1;
        for (const char *c = body; *c; ++c)
            lines += *c == '\n';
        int x, y, h = 108 + lines * 42 + 30 + count * 70 + 80;
        draw_modal_box(1100, h, &x, &y);
        gfx_text(x + 48, y + 40, FONT_TITLE, COL_TEXT, ALIGN_LEFT, title);
        char text[600];
        snprintf(text, sizeof(text), "%s", body);
        int ly = y + 108;
        char *save = NULL;
        for (char *line = strtok_r(text, "\n", &save); line; line = strtok_r(NULL, "\n", &save))
        {
            gfx_text_fit(x + 48, ly, FONT_BODY, COL_DIM, ALIGN_LEFT, 1004, line);
            ly += 42;
        }
        int oy = y + h - 80 - count * 70;
        for (int i = 0; i < count; ++i)
        {
            if (i == sel)
                gfx_round_rect(x + 40, oy + i * 70, 1020, 60, 12, COL_ACCENT);
            gfx_text(x + 72, oy + i * 70 + 12, FONT_BODY, i == sel ? COL_INK : COL_TEXT, ALIGN_LEFT,
                     options[i]);
        }
        static const Hint hints[] = {{G_CROSS, "Choose"}, {G_CIRCLE, "Cancel"}};
        draw_hints(x + 48, y + h - 36, hints, 2);
        plat_present();
    }
    return -1;
}

static bool ui_confirm(const char *title, const char *body, const char *yes)
{
    const char *opts[] = {yes, "Cancel"};
    return ui_choose(title, body, opts, 2) == 0;
}

/* ------------------------------------------------------------ keyboard */

static bool ui_keyboard(const char *title, char *buf, size_t size)
{
    static const char *const lower[] = {"1234567890-", "qwertyuiop/", "asdfghjkl:_", "zxcvbnm.,@~"};
    static const char *const upper[] = {"1234567890-", "QWERTYUIOP/", "ASDFGHJKL:_", "ZXCVBNM.,@~"};
    static const char *const specials[] = {"Shift", "Space", "Delete", "Done"};
    const int cols = 11, rows = 5; /* row 4: specials */
    char text[256];
    snprintf(text, sizeof(text), "%s", buf);
    bool shift = false;
    int cx = 0, cy = 1;
    while (!quit)
    {
        uint32_t pressed = read_input();
        if (pressed & BTN_UP)
            cy = (cy + rows - 1) % rows;
        if (pressed & BTN_DOWN)
            cy = (cy + 1) % rows;
        int width = cy == 4 ? 4 : cols;
        if (cx >= width)
            cx = width - 1;
        if (pressed & BTN_LEFT)
            cx = (cx + width - 1) % width;
        if (pressed & BTN_RIGHT)
            cx = (cx + 1) % width;
        size_t len = strlen(text);
        if ((pressed & BTN_SQUARE) && len)
            text[--len] = 0;
        if (pressed & BTN_TRIANGLE)
            shift = !shift;
        if (pressed & BTN_CIRCLE)
            return false;
        if (pressed & BTN_OPTIONS)
            break;
        if (pressed & BTN_CROSS)
        {
            if (cy < 4)
            {
                if (len + 1 < sizeof(text) && len + 1 < size)
                {
                    text[len] = (shift ? upper : lower)[cy][cx];
                    text[len + 1] = 0;
                }
            }
            else if (cx == 0)
                shift = !shift;
            else if (cx == 1 && len + 1 < sizeof(text) && len + 1 < size)
            {
                text[len] = ' ';
                text[len + 1] = 0;
            }
            else if (cx == 2 && len)
                text[--len] = 0;
            else if (cx == 3)
                break;
        }
        draw_view();
        int x, y;
        draw_modal_box(1400, 770, &x, &y);
        gfx_text(x + 48, y + 36, FONT_TITLE, COL_TEXT, ALIGN_LEFT, title);
        gfx_round_rect(x + 48, y + 110, 1304, 76, 12, COL_BG);
        char shown[300];
        snprintf(shown, sizeof(shown), "%s_", text);
        const char *tail = shown;
        while (tail[1] && gfx_text_width(FONT_BODY, tail) > 1256)
            tail++;
        gfx_text(x + 72, y + 126, FONT_BODY, COL_TEXT, ALIGN_LEFT, tail);
        int kx = x + 48, ky = y + 220, kw = 112, kh = 84;
        for (int r = 0; r < 4; ++r)
            for (int c = 0; c < cols; ++c)
            {
                int px = kx + c * (kw + 7), py = ky + r * (kh + 10);
                bool on = r == cy && c == cx;
                gfx_round_rect(px, py, kw, kh, 10, on ? COL_ACCENT : COL_PANEL);
                char key[2] = {(shift ? upper : lower)[r][c], 0};
                gfx_text(px + kw / 2, py + 22, FONT_TITLE, on ? COL_INK : COL_TEXT, ALIGN_CENTER,
                         key);
            }
        int sw = (cols * (kw + 7) - 7 - 3 * 12) / 4;
        for (int c = 0; c < 4; ++c)
        {
            int px = kx + c * (sw + 12), py = ky + 4 * (kh + 10);
            bool on = cy == 4 && c == cx;
            gfx_round_rect(px, py, sw, kh, 10,
                           on ? COL_ACCENT : (c == 0 && shift) ? COL_LINE : COL_PANEL);
            gfx_text(px + sw / 2, py + 26, FONT_BODY, on ? COL_INK : COL_TEXT, ALIGN_CENTER,
                     specials[c]);
        }
        static const Hint hints[] = {{G_CROSS, "Type"},   {G_SQUARE, "Delete"},
                                     {G_TRIANGLE, "Shift"}, {G_OPTIONS, "Done"},
                                     {G_CIRCLE, "Cancel"}};
        draw_hints(x + 48, y + 730, hints, 5);
        plat_present();
    }
    if (quit)
        return false;
    snprintf(buf, size, "%s", text);
    return true;
}

/* ------------------------------------------------------------ actions */

static void load_catalog(void);

/* Puts each installed PS1 game's SwanStation card under the name the
 * current card mode reads (saves_swanstation_fix): the catalog gives each
 * game file's serial. Two different cards for one game are compared with
 * the server's save, so a stray download can be told from real progress. */
static int ss_conflicts;

static void repair_swanstation(void)
{
    ss_conflicts = 0;
    if (!catalog_loaded)
        return;
    InstalledList games = {0};
    installed_scan(&sync_ctx.config, &games);
    int fixed = 0;
    for (int i = 0; i < games.count; ++i)
    {
        const InstalledGame *g = &games.items[i];
        if (g->emu != EMU_SWANSTATION)
            continue;
        const RomRow *row = catalog_find_file(&catalog, "PS1", g->filename);
        if (!row)
        {
            /* a CUE/BIN install: the catalog row is the CHD of the same name */
            char chd[220];
            snprintf(chd, sizeof(chd), "%s.chd", g->name);
            row = catalog_find_file(&catalog, "PS1", chd);
        }
        if (!row)
            continue;
        /* a disc of a multi-disc game: SwanStation loads its playlist, whose
         * name is what its title mode names the card after */
        char game_path[PATH_LEN];
        util_copy(game_path, sizeof(game_path), g->path);
        if (row->group[0])
        {
            char *slash = strrchr(game_path, '/');
            if (slash)
                snprintf(slash + 1, sizeof(game_path) - (size_t)(slash + 1 - game_path), "%s.m3u",
                         row->group);
            if (saves_swanstation_rename_card(&sync_ctx.config, g->name, row->group))
                fixed++;
        }
        SsFixResult r = saves_swanstation_fix(&sync_ctx.config, game_path, row->title_id, NULL);
        if (r == SS_FIX_NEED_SERVER)
        {
            char path[160], hash[65] = "";
            snprintf(path, sizeof(path), "/api/v1/saves/%s/ps1-card/meta?slot=0", row->title_id);
            HttpBuffer out = {0};
            if (http_get(&sync_ctx.server, path, &out, NULL) == 200)
            {
                JsonValue *root = json_parse((const char *)out.data);
                util_copy(hash, sizeof(hash), json_get_str(root, "save_hash", ""));
                json_free(root);
            }
            http_buffer_free(&out);
            r = saves_swanstation_fix(&sync_ctx.config, game_path, row->title_id, hash);
        }
        if (r == SS_FIX_CHANGED)
            fixed++;
        else if (r == SS_FIX_CONFLICT)
        {
            ss_conflicts++;
            app_log("SwanStation: %s has two different cards; left both", g->name);
        }
    }
    installed_free(&games);
    if (fixed)
        app_log("SwanStation: fixed the cards of %d game(s)", fixed);
}

/* Per-game PS5SX2 cards: each installed PS2 game gets its card assigned in
 * its settings, made, and its save copied over from a shared card
 * (saves_ps5sx2_setup_game). The catalog gives each game file's serial. */
static void setup_ps5sx2_cards(void)
{
    if (!sync_ctx.config.ps2_per_game)
        return;
    InstalledList games = {0};
    installed_scan(&sync_ctx.config, &games);
    int changed = 0;
    for (int i = 0; i < games.count; ++i)
    {
        const InstalledGame *g = &games.items[i];
        if (g->emu != EMU_PS5SX2)
            continue;
        /* PS5SX2's serial for the image, else the catalog's id when it is one */
        char serial[32] = "";
        if (!saves_ps5sx2_serial(&sync_ctx.config, g->path, serial, sizeof(serial)))
        {
            const RomRow *row = catalog_loaded ? catalog_find_file(&catalog, "PS2", g->filename) : NULL;
            if (row && !strchr(row->title_id, '_'))
                util_copy(serial, sizeof(serial), row->title_id);
        }
        if (saves_ps5sx2_setup_game(&sync_ctx.config, g->name, serial))
            changed++;
    }
    installed_free(&games);
    if (changed)
        app_log("PS5SX2: set up the cards of %d game(s)", changed);
}

static void refresh_saves(void)
{
    Busy busy = {.title = "Looking at saves"};
    /* busy_draw redraws the tab behind the box while the list is freed and
     * rebuilt: the Saves view must not point into it until rebuild_saves */
    save_view.count = 0;
    sync_ctx.progress = busy_progress;
    sync_ctx.progress_user = &busy;
    busy_draw(&busy, true);
    /* the catalog names server-only RetroArch saves and SwanStation's serials */
    if (!catalog_loaded && config_ready(&sync_ctx.config) && !catalog_error[0])
        load_catalog();
    sync_ctx.catalog = catalog_loaded ? &catalog : NULL;
    repair_swanstation();
    setup_ps5sx2_cards();
    int game_index = config_ready(&sync_ctx.config) ? sync_ps5sx2_game_index(&sync_ctx) : 0;
    busy_draw(&busy, true);
    bool ok = sync_refresh(&sync_ctx);
    sync_ctx.progress = NULL;
    saves_loaded = true;
    if (config_ready(&sync_ctx.config))
        server_state = sync_ctx.compared ? 1 : 0;
    rebuild_saves();
    if (!ok)
        set_banner(COL_ERR, "%s", sync_ctx.error);
    else
    {
        int attention = 0;
        for (int i = 0; i < sync_ctx.saves.count; ++i)
            if (sync_ctx.saves.items[i].status != ST_SYNCED)
                attention++;
        set_banner(attention ? COL_WARN : COL_OK, "%d saves, %d need attention",
                   sync_ctx.saves.count, attention);
    }
    if (game_index > 0)
        set_banner(COL_OK, "PS5SX2: installed its game database (per-game fixes) from the server");
    else if (game_index < 0)
        set_banner(COL_WARN, "PS5SX2 has no game database (resources/GameIndex.yaml) and the "
                             "server couldn't send one: some games won't boot");
    if (ss_conflicts)
        set_banner(COL_WARN,
                   "SwanStation: %d game(s) have two different cards under both names; left as they "
                   "are (gamesync.log lists them)",
                   ss_conflicts);
}

/* ------------------------------------------------------------ install for a save */

/* "Star Ocean - Till the End of Time (USA) (Disc 1).chd" and the server's
 * "Star Ocean - Till the End of Time (USA)" both become
 * "staroceantilltheendoftimeusa": letters and digits, no extension, no disc tag. */
static void name_key(char *out, size_t size, const char *name)
{
    char tmp[256];
    util_copy(tmp, sizeof(tmp), name);
    const char *dot = strrchr(tmp, '.');
    if (dot && strlen(dot) <= 5 && !strchr(dot, ' ') && !strchr(dot, ')'))
        tmp[dot - tmp] = 0;
    size_t n = 0;
    for (const char *p = tmp; *p && n + 1 < size; ++p)
    {
        if (*p == '(' && tolower((unsigned char)p[1]) == 'd' && tolower((unsigned char)p[2]) == 'i' &&
            tolower((unsigned char)p[3]) == 's' && tolower((unsigned char)p[4]) == 'c' && p[5] == ' ')
        {
            const char *close = strchr(p, ')');
            if (close)
            {
                p = close;
                continue;
            }
        }
        if (isalnum((unsigned char)*p))
            out[n++] = (char)tolower((unsigned char)*p);
    }
    out[n] = 0;
}

/* The catalog game a save belongs to: same title id (a disc and its card share
 * a serial, a ROM and its .srm a slug), else the same system and name - a PS2
 * CHD's catalog id can be a name slug while its save goes by serial. -1 if none. */
static int game_for_save(const SaveEntry *e)
{
    if (!catalog_loaded)
        return -1;
    char want[200] = "", key[200];
    name_key(want, sizeof(want), e->name);
    int by_name = -1;
    for (int i = 0; i < catalog.rom_count; ++i)
    {
        const RomRow *r = &catalog.roms[i];
        if (e->title_id[0] && strcmp(r->title_id, e->title_id) == 0)
            return i;
        if (by_name < 0 && want[0] && util_ieq(r->system, e->system))
        {
            name_key(key, sizeof(key), r->filename);
            if (strcmp(key, want) == 0)
                by_name = i;
        }
    }
    return by_name;
}

/* How a game is installed for its save: as the catalog installs it. */
static void plan_for_save(const RomRow *r, InstallPlan *plan)
{
    catalog_plan(&sync_ctx.config, r, plan);
}

static int queue_group(const RomRow *head, bool cue, bool again);

static bool game_on_console(const RomRow *r)
{
    if (r->group[0])
        return catalog_group_installed(&sync_ctx.config, &catalog, r);
    InstallPlan plan;
    catalog_plan(&sync_ctx.config, r, &plan);
    if (plan.supported && catalog_installed(&plan))
        return true;
    if (!r->has_cue)
        return false;
    catalog_plan_cue(&sync_ctx.config, r, &plan);
    return plan.supported && catalog_installed(&plan);
}

/* Saves waiting for their game's download: synced once it has arrived. */
typedef struct
{
    char rom_id[128];
    char save_id[64];
} PendingSync;
static PendingSync pending[16];
static int pending_count;

/* Queues a save's game; with sync, the save follows once it's installed. */
static void install_for_save(const SaveEntry *e, int ri, bool sync)
{
    const RomRow *rom = &catalog.roms[ri];
    if (rom->group[0])
    {
        /* every disc; the save follows the last one (one worker: in order) */
        int rows[32];
        int n = catalog_group_rows(&catalog, rom, rows, 32);
        int queued = queue_group(rom, false, false);
        if (queued < 0)
            return;
        if (sync && n && pending_count < (int)(sizeof(pending) / sizeof(pending[0])))
        {
            util_copy(pending[pending_count].rom_id, sizeof(pending[0].rom_id),
                      catalog.roms[rows[n - 1]].rom_id);
            util_copy(pending[pending_count].save_id, sizeof(pending[0].save_id), e->title_id);
            pending_count++;
        }
        set_banner(COL_OK, "Queued %d disc(s) of %s%s  -  see Downloads", queued, rom->group,
                   sync ? "; its save follows once they're installed" : "");
        return;
    }
    InstallPlan plan;
    plan_for_save(rom, &plan);
    if (!plan.supported)
    {
        set_banner(COL_WARN, "%s", plan.reason);
        return;
    }
    downloads_set_server(&sync_ctx.server);
    bool queued = downloads_add(rom, &plan);
    if (sync && pending_count < (int)(sizeof(pending) / sizeof(pending[0])))
    {
        util_copy(pending[pending_count].rom_id, sizeof(pending[0].rom_id), rom->rom_id);
        util_copy(pending[pending_count].save_id, sizeof(pending[0].save_id), e->title_id);
        pending_count++;
    }
    if (!queued)
        set_banner(COL_DIM, "%s is already in the queue%s", rom->name,
                   sync ? "; its save follows once it's installed" : "");
    else
        set_banner(COL_OK, "Queued %s%s  -  see Downloads", rom->name,
                   sync ? "; its save follows once it's installed" : "");
}

/* Saves whose game has arrived: synced from the main loop (run_ready_syncs),
 * not from inside the download bookkeeping. */
typedef struct
{
    char save_id[64];
    char game[160];
    char stem[200]; /* the installed file's name without extension */
} ReadySync;
static ReadySync ready[16];
static int ready_count;

/* A download finished: the saves that were waiting for it are ready. */
static void mark_ready(const DlItem *done)
{
    for (int i = 0; i < pending_count;)
    {
        if (strcmp(pending[i].rom_id, done->rom.rom_id) != 0)
        {
            ++i;
            continue;
        }
        if (ready_count < (int)(sizeof(ready) / sizeof(ready[0])))
        {
            ReadySync *r = &ready[ready_count++];
            util_copy(r->save_id, sizeof(r->save_id), pending[i].save_id);
            util_copy(r->game, sizeof(r->game), done->rom.name);
            util_copy(r->stem, sizeof(r->stem), util_basename(done->plan.dest));
            util_strip_ext(r->stem);
        }
        pending[i] = pending[--pending_count];
    }
}

/* The game is installed: rescan, so the save gets the path the emulator
 * will read for it, and download it. */
static void run_ready_syncs(void)
{
    if (!ready_count)
        return;
    ReadySync jobs[16];
    int n = ready_count;
    memcpy(jobs, ready, sizeof(ReadySync) * (size_t)n);
    ready_count = 0;
    app_log("install and sync: rescanning for %d save(s)", n);
    refresh_saves();
    for (int k = 0; k < n; ++k)
    {
        const ReadySync *job = &jobs[k];
        SaveEntry *e = saves_find(&sync_ctx.saves, job->save_id);
        if (!e)
        {
            app_log("install and sync: %s isn't in the list", job->save_id);
            set_banner(COL_WARN, "Installed %s, but its save isn't on the server any more",
                       job->game);
            continue;
        }
        /* PS5SX2 hasn't read the new game's serial yet: its card is named
         * after the file just installed */
        if (e->emu == EMU_PS5SX2 && e->status == ST_SERVER_ONLY && sync_ctx.config.ps2_per_game)
        {
            util_copy(e->rom_file, sizeof(e->rom_file), job->stem);
            saves_ps5sx2_game_card(&sync_ctx.config, job->stem, false, e->path, sizeof(e->path));
        }
        app_log("install and sync: %s is %s, to %s", job->save_id, save_status_text(e->status),
                e->path);
        if (e->status != ST_SERVER_ONLY && e->status != ST_DOWNLOAD)
        {
            set_banner(COL_WARN, "Installed %s; its save is %s, so it's left for you (Details)",
                       job->game, save_status_text(e->status));
            continue;
        }
        Busy busy = {.title = "Syncing the save"};
        snprintf(busy.message, sizeof(busy.message), "%s", e->name);
        busy_draw(&busy, true);
        bool ok = sync_apply(&sync_ctx, e);
        app_log("install and sync: %s %s", job->save_id, ok ? "done" : e->message);
        if (ok)
            set_banner(COL_OK, "Installed %s and synced its save", job->game);
        else
            set_banner(COL_ERR, "Installed %s; the save: %s", job->game,
                       e->message[0] ? e->message : "failed");
    }
    rebuild_saves();
}

static void save_details(SaveEntry *e)
{
    char body[400];
    snprintf(body, sizeof(body), "%s\nThis console: %s\nServer: %s\nStatus: %s", e->name,
             e->local ? "has a save" : "no save", e->server ? "has a save" : "no save",
             save_status_text(e->status));
    const char *opts[5];
    int n = 0, up = -1, down = -1, install_sync = -1, install_only = -1;
    /* the game isn't here: install it (and bring the save along), as the
     * MiSTer and the other clients offer */
    int ri = game_for_save(e);
    if (ri >= 0 && !game_on_console(&catalog.roms[ri]))
    {
        size_t len = strlen(body);
        snprintf(body + len, sizeof(body) - len, "\nGame not installed: %s",
                 catalog.roms[ri].filename);
        if (e->server && !e->local)
        {
            install_sync = n;
            opts[n++] = "Install the game and sync the save";
        }
        install_only = n;
        opts[n++] = "Install the game only";
    }
    if (e->local && sync_ctx.compared)
    {
        up = n;
        opts[n++] = "Upload: replace the server's save with this console's";
    }
    if (e->server && sync_ctx.compared)
    {
        down = n;
        opts[n++] = "Download: replace this console's save with the server's";
    }
    opts[n++] = "Close";
    int pick = ui_choose(e->status == ST_CONFLICT ? "Both saves changed" : "Save details", body,
                         opts, n);
    if (pick >= 0 && (pick == install_sync || pick == install_only))
    {
        install_for_save(e, ri, pick == install_sync);
        return;
    }
    if (pick < 0 || (pick != up && pick != down))
        return;
    Busy busy = {.title = pick == up ? "Uploading" : "Downloading"};
    snprintf(busy.message, sizeof(busy.message), "%s", e->name);
    busy_draw(&busy, true);
    bool ok = pick == up ? sync_upload(&sync_ctx, e) : sync_download(&sync_ctx, e);
    if (ok)
        set_banner(COL_OK, "%s: synced", e->name);
    else
        set_banner(COL_ERR, "%s: %s", e->name, e->message[0] ? e->message : "failed");
    rebuild_saves();
}

static void sync_selected(void)
{
    SaveEntry *e = selected_save();
    if (!e)
        return;
    if (!sync_ctx.compared)
    {
        refresh_saves();
        return;
    }
    switch (e->status)
    {
    case ST_UPLOAD:
    case ST_LOCAL_ONLY:
    case ST_DOWNLOAD:
    case ST_SERVER_ONLY: break;
    default: save_details(e); return;
    }
    if (e->status == ST_SERVER_ONLY)
    {
        int ri = game_for_save(e);
        if (ri >= 0 && !game_on_console(&catalog.roms[ri]))
        {
            save_details(e);
            return;
        }
    }
    if (e->status == ST_SERVER_ONLY && e->kind == SAVE_PS2_FOLDER)
    {
        char body[300];
        snprintf(body, sizeof(body), "%s\nis added to %s (a backup is kept as .bak).", e->name,
                 util_basename(e->path));
        if (!ui_confirm("Add to the PS2 card?", body, "Download"))
            return;
    }
    Busy busy = {.title = "Syncing"};
    snprintf(busy.message, sizeof(busy.message), "%s", e->name);
    busy_draw(&busy, true);
    if (sync_apply(&sync_ctx, e))
        set_banner(COL_OK, "%s: synced", e->name);
    else
        set_banner(COL_ERR, "%s: %s", e->name, e->message[0] ? e->message : "failed");
    rebuild_saves();
}

static void sync_everything(void)
{
    if (!sync_ctx.compared)
    {
        refresh_saves();
        if (!sync_ctx.compared)
            return;
    }
    int up = 0, down = 0, conflicts = 0;
    for (int i = 0; i < sync_ctx.saves.count; ++i)
    {
        const SaveEntry *e = &sync_ctx.saves.items[i];
        if (e->status == ST_UPLOAD || e->status == ST_LOCAL_ONLY)
            up++;
        else if (e->status == ST_DOWNLOAD ||
                 (e->status == ST_SERVER_ONLY &&
                  ((e->emu == EMU_RETROARCH && e->kind == SAVE_SRM) || e->installed)))
            down++;
        else if (e->status == ST_CONFLICT)
            conflicts++;
    }
    if (!up && !down)
    {
        set_banner(conflicts ? COL_WARN : COL_OK, "Nothing to sync%s",
                   conflicts ? " (conflicts need Triangle)" : "");
        return;
    }
    char body[200];
    snprintf(body, sizeof(body), "%d to upload, %d to download\n%d conflicts are left alone", up,
             down, conflicts);
    if (!ui_confirm("Sync all?", body, "Sync all"))
        return;
    Busy busy = {.title = "Syncing all"};
    sync_ctx.progress = busy_progress;
    sync_ctx.progress_user = &busy;
    busy_draw(&busy, true);
    SyncSummary s = sync_all(&sync_ctx);
    sync_ctx.progress = NULL;
    set_banner(s.failed ? COL_ERR : s.conflicts ? COL_WARN : COL_OK,
               "%d uploaded, %d downloaded, %d conflicts, %d failed", s.uploaded, s.downloaded,
               s.conflicts, s.failed);
    if (s.uploaded + s.downloaded)
        plat_notify("GameSync: saves synced");
    rebuild_saves();
}

static void load_catalog(void)
{
    catalog_error[0] = 0;
    if (!config_ready(&sync_ctx.config))
    {
        snprintf(catalog_error, sizeof(catalog_error), "set the server URL and API key in Settings");
        return;
    }
    Busy busy = {.title = "Catalog"};
    snprintf(busy.message, sizeof(busy.message), "Checking the catalog");
    busy_draw(&busy, true);
    catalog_loaded = catalog_load(&sync_ctx.server, &catalog, busy_progress, &busy, catalog_error,
                                  sizeof(catalog_error));
    server_state = catalog.offline ? 0 : 1;
    free(cat_state);
    cat_state = calloc(catalog.rom_count ? (size_t)catalog.rom_count : 1, 1);
    rebuild_catalog();
    if (catalog_loaded && catalog.offline)
        set_banner(COL_WARN, "Server unreachable: showing the cached catalog");
}

/* Every disc of a multi-disc game, in order (one worker: disc 1 lands first);
 * the queued count, -1 when one can't be installed. */
static int queue_group(const RomRow *head, bool cue, bool again)
{
    int rows[32];
    int n = catalog_group_rows(&catalog, head, rows, 32);
    int queued = 0;
    downloads_set_server(&sync_ctx.server);
    for (int i = 0; i < n; ++i)
    {
        const RomRow *disc = &catalog.roms[rows[i]];
        InstallPlan plan;
        if (cue && disc->has_cue)
            catalog_plan_cue(&sync_ctx.config, disc, &plan);
        else
            catalog_plan(&sync_ctx.config, disc, &plan);
        if (!plan.supported)
        {
            set_banner(COL_WARN, "%s: %s", disc->filename, plan.reason);
            return -1;
        }
        if (!again && catalog_installed(&plan))
            continue;
        if (downloads_add(disc, &plan))
            queued++;
    }
    return queued;
}

/* Cross: the file as the server has it; Circle (cue): its CUE/BIN set */
static void queue_selected(bool cue)
{
    if (!cat_view.count)
        return;
    int ri = cat_view.idx[cat_view.sel];
    const RomRow *rom = &catalog.roms[ri];
    if (rom->group[0])
    {
        bool again = false;
        if (catalog_group_installed(&sync_ctx.config, &catalog, rom))
        {
            char body[400];
            snprintf(body, sizeof(body), "%s\nis already installed (%d discs). Download it again?",
                     rom->group, rom->group_size);
            if (!ui_confirm("Already installed", body, "Download again"))
                return;
            again = true;
        }
        int queued = queue_group(rom, cue, again);
        if (queued > 0)
            set_banner(COL_OK, "Queued %d disc(s) of %s%s  -  see Downloads", queued, rom->group,
                       cue ? " (CUE/BIN)" : "");
        else if (queued == 0)
            set_banner(COL_DIM, "Every disc of %s is installed or queued", rom->group);
        return;
    }
    InstallPlan plan;
    if (cue)
        catalog_plan_cue(&sync_ctx.config, rom, &plan);
    else
        catalog_plan(&sync_ctx.config, rom, &plan);
    if (!plan.supported)
    {
        set_banner(COL_WARN, "%s", plan.reason);
        return;
    }
    if (catalog_installed(&plan))
    {
        char body[400];
        snprintf(body, sizeof(body), "%s\nis already installed. Download it again?", rom->name);
        /* the new copy replaces it only once it has fully arrived */
        if (!ui_confirm("Already installed", body, "Download again"))
            return;
    }
    downloads_set_server(&sync_ctx.server);
    if (downloads_add(rom, &plan))
        set_banner(COL_OK, "Queued %s%s  -  see Downloads", rom->name, cue ? " (CUE/BIN)" : "");
    else
        set_banner(COL_DIM, "%s is already in the queue", rom->name);
}

static void scan_installed(void)
{
    Busy busy = {.title = "Installed games"};
    snprintf(busy.message, sizeof(busy.message), "Looking in the emulator folders");
    busy_draw(&busy, true);
    installed_scan(&sync_ctx.config, &inst);
    installed_group_discs(&inst);
    inst_loaded = true;
    rebuild_installed();
}

static void delete_installed(void)
{
    if (!inst_view.count)
        return;
    const InstalledGame *g = &inst.items[inst_view.idx[inst_view.sel]];
    char body[400];
    snprintf(body, sizeof(body), "%s\n%s\nSaves are kept.", g->name, g->path);
    if (!ui_confirm("Delete this game?", body, "Delete"))
        return;
    char error[160] = "";
    if (installed_delete(g, error, sizeof(error)))
        set_banner(COL_OK, "Deleted %s", g->name);
    else
        set_banner(COL_ERR, "%s", error);
    if (cat_state && catalog.rom_count)
        memset(cat_state, 0, (size_t)catalog.rom_count);
    installed_scan(&sync_ctx.config, &inst);
    installed_group_discs(&inst);
    rebuild_installed();
}

static void test_connection(void)
{
    Busy busy = {.title = "Testing the server"};
    snprintf(busy.message, sizeof(busy.message), "%s", sync_ctx.config.server_url);
    busy_draw(&busy, true);
    HttpBuffer out = {0};
    int status = http_get(&sync_ctx.server, "/api/v1/status", &out, NULL);
    http_buffer_free(&out);
    if (status != 200)
    {
        server_state = 0;
        set_banner(COL_ERR, "Server: %s", http_error_text(status));
        return;
    }
    server_state = 1;
    status = http_get(&sync_ctx.server, "/api/v1/roms/systems", &out, NULL);
    http_buffer_free(&out);
    if (status == 200)
        set_banner(COL_OK, "Connected: server and API key work");
    else if (status == 401 || status == 403)
        set_banner(COL_ERR, "Server reached, but the API key was refused");
    else
        set_banner(COL_ERR, "Server reached, then: %s", http_error_text(status));
}

static void settings_changed(void)
{
    config_save(&sync_ctx.config);
    config_apply(&sync_ctx.config, &sync_ctx.server);
    downloads_set_server(&sync_ctx.server);
    saves_loaded = false;
    catalog_loaded = false;
    sync_ctx.compared = false;
    server_state = -1;
}

static void settings_activate(void)
{
    Config *c = &sync_ctx.config;
    switch (set_sel)
    {
    case SET_SERVER:
    {
        char url[256];
        snprintf(url, sizeof(url), "%s", c->server_url[0] ? c->server_url : "http://");
        if (ui_keyboard("Server URL", url, sizeof(url)))
        {
            HttpServer probe;
            if (!http_server_set_url(&probe, url))
                set_banner(COL_ERR, "Use http://<IP address>:<port>");
            else
            {
                util_copy(c->server_url, sizeof(c->server_url), url);
                settings_changed();
            }
        }
        break;
    }
    case SET_KEY:
        if (ui_keyboard("API key", c->api_key, sizeof(c->api_key)))
            settings_changed();
        break;
    case SET_PS1_TARGET:
        c->ps1_in_retroarch = !c->ps1_in_retroarch;
        settings_changed();
        break;
    case SET_PS2_CARD:
        /* per game -> shared (asking which card) -> per game */
        if (c->ps2_per_game)
        {
            c->ps2_per_game = false;
            ui_keyboard("Shared PS5SX2 card (in memcards/)", c->ps2_card, sizeof(c->ps2_card));
            if (!c->ps2_card[0])
                util_copy(c->ps2_card, sizeof(c->ps2_card), "Mcd001.ps2");
        }
        else
            c->ps2_per_game = true;
        settings_changed();
        break;
    case SET_TEST: test_connection(); break;
    case SET_REFRESH_CATALOG:
    {
        Busy busy = {.title = "Refresh catalog"};
        snprintf(busy.message, sizeof(busy.message), "Asking the server to rescan");
        busy_draw(&busy, true);
        catalog_reset(&sync_ctx.server);
        load_catalog();
        if (catalog_loaded)
            set_banner(COL_OK, "Catalog refreshed: %d games", catalog.rom_count);
        else
            set_banner(COL_ERR, "%s", catalog_error);
        break;
    }
    case SET_RESCAN:
        /* emulators installed or moved since start-up */
        config_resolve(&sync_ctx.config);
        emu_dirs_checked = false;
        refresh_saves();
        scan_installed();
        break;
    }
}

/* ------------------------------------------------------------ loop */

/* A tab's chip after the shared system filter changed on another tab:
 * rebuilt, back at the top when the chip moved. */
static void apply_pref(Chips *c, View *v, void (*rebuild)(void))
{
    const char *before = chips_current(c);
    char was[12];
    snprintf(was, sizeof(was), "%s", before ? before : "");
    rebuild();
    const char *now = chips_current(c);
    if (!util_ieq(was, now ? now : ""))
    {
        v->sel = v->top = 0;
        view_clamp(v);
    }
}

static void enter_tab(void)
{
    if (tab == TAB_SAVES && saves_loaded)
        apply_pref(&save_chips, &save_view, rebuild_saves);
    else if (tab == TAB_CATALOG && catalog_loaded)
        apply_pref(&cat_chips, &cat_view, rebuild_catalog);
    else if (tab == TAB_INSTALLED && inst_loaded)
        apply_pref(&inst_chips, &inst_view, rebuild_installed);
    if (tab == TAB_SAVES && !saves_loaded && config_ready(&sync_ctx.config))
        refresh_saves();
    else if ((tab == TAB_CATALOG || tab == TAB_INSTALLED) && !catalog_loaded &&
             config_ready(&sync_ctx.config) && !catalog_error[0])
        load_catalog();
    if (tab == TAB_INSTALLED && !inst_loaded)
        scan_installed();
}

static void handle_input(uint32_t pressed)
{
    if (pressed & (BTN_L1 | BTN_R1))
    {
        tab = (Tab)((tab + ((pressed & BTN_L1) ? TAB_COUNT - 1 : 1)) % TAB_COUNT);
        enter_tab();
        return;
    }
    if (pressed & BTN_OPTIONS)
    {
        if (ui_confirm("Exit GameSync?",
                       downloads_active() ? "Downloads in progress will stop." : "", "Exit"))
            quit = true;
        return;
    }
    switch (tab)
    {
    case TAB_SAVES:
        view_nav(&save_view, pressed, save_name);
        if (chips_nav(&save_chips, pressed))
        {
            save_view.sel = save_view.top = 0;
            rebuild_saves();
        }
        if (pressed & BTN_SELECT)
        {
            save_status = (StatusFilter)((save_status + 1) % SF_COUNT);
            save_view.sel = save_view.top = 0;
            rebuild_saves();
        }
        if (pressed & BTN_CROSS)
            sync_selected();
        else if (pressed & BTN_SQUARE)
            sync_everything();
        else if ((pressed & BTN_TRIANGLE) && selected_save())
            save_details(selected_save());
        break;
    case TAB_CATALOG:
        if (!catalog_loaded)
        {
            if (pressed & BTN_CROSS)
                load_catalog();
            break;
        }
        view_nav(&cat_view, pressed, cat_name);
        if (chips_nav(&cat_chips, pressed))
        {
            cat_view.sel = cat_view.top = 0;
            rebuild_catalog();
        }
        if (pressed & BTN_CROSS)
            queue_selected(false);
        else if (pressed & BTN_SQUARE)
        {
            cat_ra_only = !cat_ra_only;
            cat_view.sel = cat_view.top = 0;
            rebuild_catalog();
            set_banner(cat_ra_only ? COL_RA : COL_DIM,
                       cat_ra_only ? "RetroAchievements only: %d games (Square shows all)"
                                   : "All games: %d",
                       cat_view.count);
        }
        else if (pressed & BTN_TRIANGLE)
        {
            if (ui_keyboard("Search the catalog", cat_query, sizeof(cat_query)))
            {
                cat_view.sel = cat_view.top = 0;
                rebuild_catalog();
            }
        }
        else if ((pressed & BTN_CIRCLE) && cat_view.count &&
                 catalog.roms[cat_view.idx[cat_view.sel]].has_cue)
            queue_selected(true);
        else if ((pressed & BTN_CIRCLE) && cat_query[0])
        {
            cat_query[0] = 0;
            rebuild_catalog();
        }
        break;
    case TAB_INSTALLED:
        view_nav(&inst_view, pressed, inst_name);
        if (chips_nav(&inst_chips, pressed))
        {
            inst_view.sel = inst_view.top = 0;
            rebuild_installed();
        }
        if (pressed & BTN_CROSS)
            delete_installed();
        else if (pressed & BTN_SQUARE)
            scan_installed();
        else if (pressed & BTN_TRIANGLE)
        {
            if (ui_keyboard("Search installed games", inst_query, sizeof(inst_query)))
            {
                inst_view.sel = inst_view.top = 0;
                rebuild_installed();
            }
        }
        else if ((pressed & BTN_CIRCLE) && inst_query[0])
        {
            inst_query[0] = 0;
            rebuild_installed();
        }
        break;
    case TAB_DOWNLOADS:
    {
        if (dl_count)
        {
            if (pressed & BTN_UP)
                dl_sel = dl_sel > 0 ? dl_sel - 1 : dl_count - 1;
            if (pressed & BTN_DOWN)
                dl_sel = dl_sel < dl_count - 1 ? dl_sel + 1 : 0;
        }
        const DlItem *d = dl_count && dl_sel < dl_count ? &dl_items[dl_sel] : NULL;
        if ((pressed & BTN_CROSS) && d)
        {
            if (d->state == DL_RUNNING || d->state == DL_QUEUED)
                downloads_pause(d->id);
            else if (d->state == DL_PAUSED || d->state == DL_FAILED)
                downloads_resume(d->id);
        }
        else if (pressed & BTN_SQUARE)
            downloads_clear_finished();
        else if ((pressed & BTN_TRIANGLE) && d && d->state != DL_RUNNING)
        {
            if (ui_confirm("Remove from the list?", d->rom.name, "Remove"))
                downloads_remove(d->id);
        }
        else if (pressed & BTN_CIRCLE)
        {
            for (int i = 0; i < dl_count; ++i)
                if (dl_items[i].state == DL_RUNNING)
                    downloads_pause(dl_items[i].id);
        }
        break;
    }
    default:
        if (pressed & BTN_UP)
            set_sel = (set_sel + SET_COUNT - 1) % SET_COUNT;
        if (pressed & BTN_DOWN)
            set_sel = (set_sel + 1) % SET_COUNT;
        if (pressed & BTN_CROSS)
            settings_activate();
        break;
    }
}

/* finished catalog installs: remember them for Sync all, refresh views */
static void collect_completed(void)
{
    DlItem done;
    bool any = false;
    while (downloads_take_completed(&done))
    {
        any = true;
        if (done.rom.title_id[0] && done.plan.emu != EMU_RETROARCH)
        {
            kv_set(&sync_ctx.installed, done.rom.title_id, emulator_tag(done.plan.emu));
            kv_save(&sync_ctx.installed);
        }
        if (done.plan.emu == EMU_PS5SX2 && sync_ctx.config.ps2_per_game)
        {
            char stem[200];
            util_copy(stem, sizeof(stem), util_basename(done.plan.dest));
            util_strip_ext(stem);
            saves_ps5sx2_setup_game(&sync_ctx.config, stem, done.rom.title_id);
        }
        if (done.plan.emu == EMU_SWANSTATION)
            saves_swanstation_fix(&sync_ctx.config,
                                  done.plan.m3u[0] ? done.plan.m3u
                                  : done.plan.cue  ? done.plan.check
                                                   : done.plan.dest,
                                  done.rom.title_id, NULL);
        if (done.rom.group[0] && catalog_loaded)
        {
            /* the last disc in: the playlist the emulator loads the game by */
            if (catalog_group_installed(&sync_ctx.config, &catalog, &done.rom))
            {
                catalog_group_finish(&sync_ctx.config, &catalog, &done.rom);
                set_banner(COL_OK, "Installed %s (%d discs)", done.rom.group, done.rom.group_size);
            }
            else
                set_banner(COL_OK, "Installed %s (more discs to come)", done.rom.filename);
        }
        else
            set_banner(COL_OK, "Installed %s", done.rom.name);
        app_log("installed %s -> %s", done.rom.rom_id, done.plan.dest);
        mark_ready(&done);
    }
    if (any)
    {
        if (cat_state && catalog.rom_count)
            memset(cat_state, 0, (size_t)catalog.rom_count);
        inst_loaded = false;
        sync_ctx.compared = false; /* new ROMs can bring RetroArch saves */
        if (tab == TAB_INSTALLED)
            scan_installed();
    }
}

static void bench_tick(const char *mode, void *user)
{
    Busy *b = user;
    snprintf(b->message, sizeof(b->message), "%s (up to 2 minutes each, 6 in all)", mode);
    busy_draw(b, true);
}

void ui_run(bool data_listable, const char *status)
{
    data_ok = data_listable;
    snprintf(data_status, sizeof(data_status), "%s", status);
    sync_init(&sync_ctx);
    downloads_set_server(&sync_ctx.server);
    chips_reset(&save_chips);
    chips_reset(&cat_chips);
    chips_reset(&inst_chips);
    kv_load(&ui_state, data_path("ui.txt"));
    const char *saved = kv_get(&ui_state, "system");
    snprintf(pref_system, sizeof(pref_system), "%s", saved ? saved : "");
    app_log("GameSync PS5 " APP_VERSION " started, /data: %s", status);
    if (!config_ready(&sync_ctx.config))
    {
        tab = TAB_SETTINGS;
        set_banner(COL_WARN, "Set the server URL and API key first");
    }
    else if (!data_ok)
        set_banner(COL_WARN, "/data can't be listed: %s", status);
    bool first = true;
    while (!quit)
    {
        uint32_t pressed = read_input();
        if (first)
        {
            draw_view();
            plat_present();
            if (data_ok && diskbench_requested())
            {
                Busy busy = {.title = "Testing the disk"};
                diskbench_run(bench_tick, &busy);
                set_banner(COL_OK, "Disk test done: the results are in gamesync.log");
            }
            if (config_ready(&sync_ctx.config) && data_ok)
                refresh_saves();
            first = false;
            continue;
        }
        collect_completed();
        run_ready_syncs();
        handle_input(pressed);
        draw_view();
        plat_present();
    }
    sync_shutdown(&sync_ctx);
    kv_free(&ui_state);
    catalog_free(&catalog);
    installed_free(&inst);
    free(cat_state);
}
