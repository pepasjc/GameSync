#ifndef GCSYNC_UI_H
#define GCSYNC_UI_H

#include "common.h"
#include "gui.h"

/*
 * ui — GameSync screen furniture on top of gui.c: the status line, the
 * per-screen chrome (header, view tabs, status banner, footer hints), the
 * scrolling list panel on the left, the detail panel on the right, and the
 * boot / confirm screens.
 *
 * Screen layout (logical 640x480):
 *   header bar   | logo  GameSync • <view>          v0.x  ● ip  [SD sp2] |
 *   tab strip    | L  Catalog Installed Queue VMC Cards Server Settings R |
 *   body         | list panel (rows + selection bar)  | detail panel      |
 *   banner       | status / error line                                    |
 *   footer       | button hints                                           |
 */

/* List panel (left) and detail panel (right) geometry */
#define UI_LIST_X   GUI_SAFE_X
#define UI_LIST_Y   GUI_BODY_Y
#define UI_LIST_W   348
#define UI_LIST_H   (GUI_BANNER_Y - 6 - GUI_BODY_Y)
#define UI_LIST_HEAD 28           /* title strip inside the list panel */
#define UI_ROW_H    24
#define UI_DETAIL_X (UI_LIST_X + UI_LIST_W + 10)
#define UI_DETAIL_W (GUI_SAFE_R - UI_DETAIL_X)
#define UI_PAD      12

void ui_init(void);

/* Rows a list panel shows (for paging / scroll clamping) */
int  ui_list_visible(void);

void ui_status(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
void ui_error(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
const char *ui_status_text(void);
bool ui_status_is_error(void);

const char *ui_view_name(AppView view);   /* header title */

/* Header + tabs + status banner + footer for a main view */
void ui_draw_chrome(const SyncState *state, AppView view, const GuiHint *hints, int nhints);

/* ---- List panel ---- */

/* Draws one row.  (x, y, w, h) is the row box; sel = row under the bar
 * (draw text in HEX_INK then). */
typedef void (*UiRowFn)(int idx, float x, float y, float w, float h, bool sel);

/* Panel + title strip ("<title>  n/total") + rows + scrollbar.  When the
 * list is empty, `empty` is shown centred instead (wrapped). */
void ui_list(const char *title, int count, int sel, int scroll, UiRowFn row, const char *empty);
/* Same panel, but rows start `skip` px lower (a custom banner sits on top) */
void ui_list_ex(const char *title, int count, int sel, int scroll, int rows,
                float skip, UiRowFn row, const char *empty);

/* Row helpers: main text left (fitted), optional right-aligned text */
void ui_row_text(float x, float y, float w, float h, bool sel, u32 hex,
                 const char *text, const char *right);
/* A small pill inside a row; returns its width */
float ui_row_pill(float x, float y, float h, bool sel, u32 hex, const char *label);

/* ---- Detail panel ---- */

/* Panel + wrapped title (<= 3 lines).  Returns the y cursor below it. */
float ui_detail_begin(const char *title);
/* Empty detail panel with a centred hint */
void  ui_detail_empty(const char *icon_text, const char *hint);
/* "LABEL" small/dim, value below.  Returns the next y. */
float ui_detail_field(float y, const char *label, const char *value, u32 value_hex);
/* Row of pills starting at y; call ui_detail_pill repeatedly, x advances */
float ui_detail_pill(float *x, float y, u32 bg, u32 fg, const char *label);

/* ---- Full-screen states / dialogs ---- */

void ui_draw_boot(const char *message);
/* Confirm card over whatever is already drawn this frame */
void ui_draw_confirm(const char *title, const char *message, u32 tone);

/* Choice card (action menu): items stacked, `sel` highlighted, with an
 * "A Select / B Cancel" strip.  `subtitle` may be NULL. */
void ui_draw_menu(const char *title, const char *subtitle,
                  const char *const *items, int n, int sel);
/* Read-only details card: label / value pairs and a "B Close" strip */
void ui_draw_info(const char *title, const char *const *labels,
                  const char *const *values, int n);

/* Helpers */
void ui_human_size(uint64_t b, char *out, size_t n);

#endif /* GCSYNC_UI_H */
