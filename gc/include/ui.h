#ifndef GCSYNC_UI_H
#define GCSYNC_UI_H

#include "common.h"

/*
 * UI over gxflux: a GX-rendered textured-font console.  ui_init() hands the
 * GS to gxflux and brings up the console grid.  Each frame is composed by
 * clearing the buffer, emitting positioned/coloured text, then flushing.
 *
 * Drawing primitives are deliberately low level so view bodies (in main.c)
 * can compose lists, highlight bars and progress rows uniformly.
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

int  ui_rows(void);
int  ui_cols(void);
int  ui_list_visible(void);   /* rows available for a scrolling list body */
int  ui_list_top(void);       /* first body row (0-based) */

void ui_clear(void);          /* clear the text buffer for a new frame */
void ui_flush(void);          /* render the buffer to the screen */

void ui_text(int row, int col, int color, const char *fmt, ...);
void ui_text_hl(int row, bool selected, int color, const char *fmt, ...);

void ui_status(const char *fmt, ...);
void ui_error(const char *fmt, ...);
const char *ui_status_text(void);
bool ui_status_is_error(void);

void ui_draw_header(const SyncState *state, AppView view);
void ui_draw_footer(const char *hint);   /* hint row + status line */
void ui_draw_message(const char *title, const char *message);

const char *ui_view_name(AppView view);

#endif /* GCSYNC_UI_H */
