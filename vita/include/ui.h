#ifndef UI_H
#define UI_H

/*
 * GameSync Vita screens, drawn with the vita2d kit in gui.c.
 *
 * Every view call draws one complete frame.  Dialogs (notice, confirm,
 * summary, settings) and the busy overlay repaint the last view as their
 * backdrop, dimmed, with a card on top.
 */

#include "common.h"
#include "downloads.h"
#include "roms.h"
#include "sync.h"

/* Rows the list panels show; main.c pages and scrolls by this. */
#define UI_LIST_ROWS 11

typedef enum {
    UI_TONE_ACCENT = 0,
    UI_TONE_OK,
    UI_TONE_WARN,
    UI_TONE_INFO,
    UI_TONE_ERR,
} UiTone;

void ui_init(void);
void ui_term(void);

/* Server status shown in the header: -1 unknown, 0 offline, 1 online. */
void ui_set_online(int online);

/* One frame with a "working" card over the current view.  Call right
 * before blocking work (network, hashing); the card stays up until the
 * next frame is drawn. */
void ui_busy(const char *fmt, ...) __attribute__((format(printf, 1, 2)));

/* Short non-blocking notice shown at the bottom of the views for a few
 * seconds. */
void ui_toast(UiTone tone, const char *fmt, ...) __attribute__((format(printf, 2, 3)));

/* Modal message card; waits for Cross (or Circle) to dismiss. */
void ui_notice(UiTone tone, const char *title, const char *fmt, ...)
    __attribute__((format(printf, 3, 4)));

/* Compare-and-confirm card for a sync action.  Returns the action to run
 * (SYNC_UPLOAD / SYNC_DOWNLOAD), or -1 when cancelled or there is nothing
 * to do.  Upload / download confirm with Cross; a conflict picks a side
 * with Square (keep this Vita's) or Triangle (keep the server's); Circle
 * cancels.  server_last_sync: ISO 8601 string (or NULL/empty) for the
 * server save date. */
int ui_confirm(const TitleInfo *title, SyncAction action,
                const char *server_hash, uint32_t server_size,
                const char *server_last_sync);

/* Result card for "Sync all"; waits for Cross. */
void ui_sync_summary(const SyncSummary *summary);

/* Read-only settings / about card; waits for Circle or Cross. */
void ui_show_settings(const SyncState *state);

/* Forget the cached "last synced" state of each save (call after any sync). */
void ui_invalidate_sync_state(void);

/* ----- Views ----- */

void ui_draw_list(const SyncState *state, int selected, int scroll);

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

/* Repaint the Downloads view with fresh transfer numbers, presenting
 * without waiting for vblank so the transfer never stalls on the
 * display.  Call after at least one full ui_draw_downloads; the caller
 * rate-limits it. */
void ui_draw_progress_partial(const DownloadList *downloads,
                              uint64_t active_downloaded,
                              uint64_t active_total,
                              uint64_t active_bps);

#endif /* UI_H */
