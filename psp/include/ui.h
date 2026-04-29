#ifndef UI_H
#define UI_H

#include "common.h"
#include "downloads.h"
#include "roms.h"

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

/* Ask user to confirm a sync action. Returns true if confirmed.
 * server_last_sync: ISO 8601 string (or NULL/empty) to show server save date. */
bool ui_confirm(const TitleInfo *title, SyncAction action,
                const char *server_hash, uint32_t server_size,
                const char *server_last_sync);

/* ROM Catalog + Downloads views — text-mode list rendering.  Both
 * accept a tab strip indicator (which view we're in) so the user
 * always sees how to cycle to the next one via START. */
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

#endif /* UI_H */
