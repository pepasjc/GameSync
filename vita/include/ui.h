#ifndef UI_H
#define UI_H

#include "common.h"

/* Initialize framebuffer display. */
void ui_init(void);

/* Release framebuffer memory. Call before exit. */
void ui_term(void);

/* Clear screen to black. */
void ui_clear(void);

/* Draw the title list. selected = currently highlighted index, scroll = first visible. */
void ui_draw_list(const SyncState *state, int selected, int scroll);

/* Print a status/progress line near the bottom of the screen. */
void ui_status(const char *fmt, ...);

/* Draw the config info screen. */
void ui_draw_config(const SyncState *state);

/* Show a full-screen message and wait for X to dismiss. */
void ui_message(const char *fmt, ...);

/* Ask user to confirm a sync action. Returns true if confirmed (X), false for cancel (O). */
bool ui_confirm(const TitleInfo *title, SyncAction action,
                const char *server_hash, uint32_t server_size);

#endif /* UI_H */
