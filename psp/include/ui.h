#ifndef UI_H
#define UI_H

#include "common.h"

/* Initialize the PSP debug screen (pspDebugScreenInit). */
void ui_init(void);

/* Clear the screen. */
void ui_clear(void);

/* Draw the title list on screen.
 * selected: currently selected index.
 * scroll: first visible index. */
void ui_draw_list(const SyncState *state, int selected, int scroll);

/* Draw a status/progress message at the bottom of screen. */
void ui_status(const char *fmt, ...);

/* Draw the config screen. */
void ui_draw_config(const SyncState *state);

/* Show a simple message and wait for X to dismiss. */
void ui_message(const char *fmt, ...);

/* Ask user to confirm a sync action. Returns true if confirmed. */
bool ui_confirm(const TitleInfo *title, SyncAction action,
                const char *server_hash, uint32_t server_size);

#endif /* UI_H */
