#ifndef UI_H
#define UI_H

#include "common.h"
#include "downloads.h"
#include "roms.h"

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

/* Ask user to confirm a sync action. Returns true if confirmed (X), false for cancel (O).
 * server_last_sync: ISO 8601 string (or NULL/empty) to show server save date. */
bool ui_confirm(const TitleInfo *title, SyncAction action,
                const char *server_hash, uint32_t server_size,
                const char *server_last_sync);

/* ROM Catalog + Downloads views.  Both draw the tab strip on row 0 so
 * the user always sees how to cycle views with START. */
void ui_draw_rom_catalog(const RomCatalog *catalog,
                         const DownloadList *downloads,
                         const char *current_system,
                         int selected, int scroll_offset,
                         const char *status_line,
                         AppView current_view);

void ui_draw_downloads(const DownloadList *downloads,
                       int selected, int scroll_offset,
                       const char *status_line,
                       bool active_in_progress,
                       uint64_t active_downloaded,
                       uint64_t active_total,
                       uint64_t active_bps,
                       AppView current_view);

/* Repaint only the four active-download rows — no clear, no list
 * redraw — so progress ticks don't flicker.  Call after at least one
 * full ui_draw_downloads. */
void ui_draw_progress_partial(const DownloadList *downloads,
                              uint64_t active_downloaded,
                              uint64_t active_total,
                              uint64_t active_bps);

#endif /* UI_H */
