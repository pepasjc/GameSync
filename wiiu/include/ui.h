#ifndef WIIUSYNC_UI_H
#define WIIUSYNC_UI_H

#include "common.h"

/*
 * UI over OSScreen — a software text console mirrored on the TV and the
 * GamePad.  Deliberately the same API as the GameCube client's ui.h (gxflux)
 * so the view bodies in main.c port across unchanged.
 *
 * OSScreen's framebuffers live in MEM1, which ProcUI takes away when the
 * app goes to the background (HOME menu).  ui_acquire_foreground() /
 * ui_release_foreground() are wired to the ProcUI acquire/release callbacks
 * so the buffers are re-allocated on the way back in.
 */

/* Colour indices for ui_text / ui_text_hl. */
enum {
    UI_WHITE = 0,
    UI_GREEN,
    UI_RED,
    UI_YELLOW,
    UI_CYAN,
    UI_BLUE,
    UI_GREY,
};

void ui_init(void);
void ui_shutdown(void);

int  ui_rows(void);
int  ui_cols(void);
int  ui_list_visible(void);   /* rows available for a scrolling list body */
int  ui_list_top(void);       /* first body row (0-based) */

void ui_clear(void);          /* clear the text buffer for a new frame */
void ui_flush(void);          /* render the buffer to both screens */

void ui_text(int row, int col, int color, const char *fmt, ...);
void ui_text_hl(int row, bool selected, int color, const char *fmt, ...);

/* Draw a progress bar as the background of ``row`` this frame (the row's
 * text renders on top of it).  Cleared by ui_clear(); total == 0 hides it. */
void ui_progress(int row, uint64_t done, uint64_t total);

void ui_status(const char *fmt, ...);
void ui_error(const char *fmt, ...);
const char *ui_status_text(void);
bool ui_status_is_error(void);

void ui_draw_header(const SyncState *state, AppView view);
void ui_draw_footer(const char *hint);   /* hint row + status line */
void ui_draw_message(const char *title, const char *message);

const char *ui_view_name(AppView view);

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
