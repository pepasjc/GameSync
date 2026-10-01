#ifndef WIIUSYNC_UI_H
#define WIIUSYNC_UI_H

#include "common.h"
#include "gfx.h"

/*
 * GameSync UI kit on top of gfx: the shared dark palette, header + view tabs,
 * footer button hints, status banner, list rows, detail panel, dialogs and
 * progress cards.  Same design language as the 3DS and DS clients.
 *
 * A frame is a draw callback: ui_render() runs it once per screen (TV at
 * 1280x720, GamePad scaled to 854x480) and flips both.  All coordinates are
 * logical 1280x720 pixels.
 *
 * OSScreen's framebuffers live in MEM1, which ProcUI takes away when the app
 * goes to the background (HOME menu).  ui_acquire_foreground() /
 * ui_release_foreground() are wired to the ProcUI acquire/release callbacks
 * so the buffers are re-allocated on the way back in.
 */

/* ---- palette (0xRRGGBB) ---- */
#define HEX_BG        0x0F1720
#define HEX_BG2       0x1B2633
#define HEX_PANEL     0x243244
#define HEX_PANEL_HI  0x2C3D52
#define HEX_LINE      0x33465C
#define HEX_TEXT      0xE6EDF3
#define HEX_DIM       0x8DA2B5
#define HEX_MUTED     0x5E7286
#define HEX_ACCENT    0x2EC4B6
#define HEX_ACCENT2   0x3DDBD9
#define HEX_OK        0x3FB950
#define HEX_WARN      0xF0B429
#define HEX_INFO      0x58A6FF
#define HEX_ERR       0xF85149
#define HEX_INK       0x0F1720   /* dark text on accent fills */
#define HEX_GC        0xA371F7   /* system tags */
#define HEX_WII       0x79C0FF
#define HEX_WIIU      0x39C5F7

/* ---- text sizes (logical px) ---- */
#define UI_S_TITLE  34
#define UI_S_HEAD   30
#define UI_S_BODY   26
#define UI_S_SMALL  22
#define UI_S_TINY   19

/* ---- layout ---- */
#define UI_HEADER_H   64
#define UI_TABS_H     46
#define UI_CONTENT_Y  (UI_HEADER_H + UI_TABS_H + 12)
#define UI_FOOTER_H   52
#define UI_FOOTER_Y   (GFX_H - UI_FOOTER_H)
#define UI_BANNER_H   40
#define UI_BANNER_Y   (UI_FOOTER_Y - UI_BANNER_H - 10)
#define UI_CONTENT_B  (UI_BANNER_Y - 10)

#define UI_LIST_X     24
#define UI_LIST_W     790
#define UI_DETAIL_X   (UI_LIST_X + UI_LIST_W + 16)
#define UI_DETAIL_W   (GFX_W - UI_DETAIL_X - 24)

void ui_init(void);
void ui_shutdown(void);

/* ---- frames ---- */

typedef void (*UiDrawFn)(void *ctx);

/* Draw ``fn`` on the TV and the GamePad and flip both. */
void ui_render(UiDrawFn fn, void *ctx);

/* Rows a list view shows at once (for scroll / page maths). */
int  ui_list_visible(void);

/* ---- status line (shown in the banner above the footer) ---- */

void ui_status(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
void ui_error(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
const char *ui_status_text(void);
bool ui_status_is_error(void);

/* ---- chrome ---- */

void ui_draw_background(void);
/* Header ("GameSync • <view>", version, net / SD / NAND chips) + view tabs. */
void ui_draw_header(const SyncState *state, AppView view);
/* Header with a free-form section title and no tabs (boot, editor). */
void ui_draw_header_plain(const SyncState *state, const char *section);

typedef struct {
    const char *button;   /* "A" "B" "X" "Y" "L" "R" "ZL" "ZR" "+" "-" "DPAD" "UD" "LR" "HOME" */
    const char *label;
} UiHint;

/* Status banner + footer bar with button hints. */
void ui_draw_footer(const UiHint *hints, int count);

/* ---- list panel ---- */

/* Panel background with a header strip: title left, ``right`` dim at the
 * right edge.  ``tabs`` (optional) draws a segmented selector after the
 * title instead. */
void ui_list_panel(const char *title, const char *right);
void ui_list_panel_tabs(const char *const *tabs, int count, int active,
                        const char *right);

/* Status icons for rows and cards. */
typedef enum {
    UI_ICON_NONE = 0,
    UI_ICON_DOT,          /* plain dot in ``color`` */
    UI_ICON_OK,           /* check */
    UI_ICON_UP,           /* upload arrow */
    UI_ICON_DOWN,         /* download arrow */
    UI_ICON_ALERT,        /* ! */
    UI_ICON_PLUS,         /* + (server only / new) */
    UI_ICON_UNKNOWN,      /* ? */
} UiIcon;

typedef struct {
    UiIcon      icon;
    uint32_t    icon_color;
    const char *tag;           /* small pill before the name (system) */
    uint32_t    tag_color;
    const char *text;
    uint32_t    text_color;    /* 0 = default */
    const char *right;         /* dim right-aligned text (size, blocks) */
    const char *badge;         /* status pill at the right edge */
    uint32_t    badge_color;
    int         permille;      /* >= 0: thin progress bar along the row */
} UiRow;

void ui_list_row(int slot, bool selected, const UiRow *row);
void ui_list_empty(const char *title, const char *hint);
void ui_list_scrollbar(int first, int visible, int total);

/* ---- detail panel (right-hand card for the selected item) ---- */

void ui_detail_begin(const char *title);
void ui_detail_pill(const char *label, uint32_t color);   /* pills share a line */
void ui_detail_field(const char *label, const char *fmt, ...)
    __attribute__((format(printf, 2, 3)));
void ui_detail_note(uint32_t color, const char *fmt, ...)
    __attribute__((format(printf, 2, 3)));
void ui_detail_progress(uint64_t done, uint64_t total, uint32_t color);
/* Status card with a coloured stripe and icon. */
void ui_detail_status(UiIcon icon, uint32_t color, const char *title, const char *sub);
void ui_detail_gap(int px);
/* Button hint inside the panel ("A  Install to NAND"). */
void ui_detail_action(const char *button, const char *label);

/* ---- widgets ---- */

int  ui_pill_w(int h, int size, const char *label);
int  ui_pill(int x, int y, int h, int size, uint32_t bg, uint32_t fg, const char *label);
int  ui_button_w(const char *button);
int  ui_button(int x, int cy, const char *button);
/* permille < 0 draws an animated indeterminate bar. */
void ui_bar(int x, int y, int w, int h, int permille, uint32_t color);
void ui_icon(int cx, int cy, int r, UiIcon icon, uint32_t color);

/* ---- modal cards ---- */

void ui_dim(void);
void ui_dialog(const char *title, uint32_t tone, const char *message,
               const UiHint *hints, int count);

typedef struct {
    const char *title;        /* card title ("Downloading", "Installing") */
    const char *name;         /* what (game name) */
    const char *detail;       /* second line (file name, step) */
    uint64_t    done, total;  /* total == 0 -> indeterminate */
    bool        bytes;        /* done/total are bytes (MB labels) */
    uint64_t    bps;          /* bytes per second, 0 = unknown */
    const char *hint_button;  /* e.g. "B" */
    const char *hint_label;   /* e.g. "Pause" */
} UiProgress;

void ui_progress_card(const UiProgress *p);

/* Boot card: logo, step list with done / current / pending marks. */
void ui_draw_boot(const char *const *steps, int count, int current, const char *note);

/* Text editor card: ``buf`` with a caret box at ``cur``; ``up`` / ``down``
 * are the characters Up / Down would put there (shown as a picker). */
void ui_draw_editor(const char *label, const char *buf, int cur, char up, char down);

const char *ui_view_name(AppView view);

/* Byte count as "1.5 GB" / "320 MB" / "12 KB". */
void ui_human_size(uint64_t b, char *out, size_t n);

/* ProcUI foreground handling (registered by ui_init). */
uint32_t ui_acquire_foreground(void *ctx);
uint32_t ui_release_foreground(void *ctx);

/* True exactly once after each foreground re-acquisition (HOME menu, app
 * switch).  ProcUI takes MEM1 away in the background, so the framebuffers
 * that come back are freshly allocated and blank — every loop that owns the
 * screen must repaint when this returns true, or the user is left staring at
 * a black screen until they happen to press a button. */
bool ui_consume_repaint_request(void);

#endif /* WIIUSYNC_UI_H */
