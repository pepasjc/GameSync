#ifndef UI_H
#define UI_H

/*
 * PSP Save Sync - screens.  Built on the GU kit in gui.h.
 *
 * Every view is a full frame: header, a list panel on the left, a detail
 * panel for the selected row on the right, and a footer with the button
 * hints.  Main loops redraw a view once per iteration (paced by vblank);
 * blocking operations show a busy card over a snapshot of the last view.
 */

#include "common.h"
#include "downloads.h"
#include "roms.h"
#include "sync.h"

/* Rows visible in every list; main.c pages and scrolls by this. */
#define UI_LIST_ROWS 10

typedef enum {
    UI_TONE_INFO = 0,
    UI_TONE_OK,
    UI_TONE_WARN,
    UI_TONE_ERR,
} UiTone;

/* Bring up the GU display. */
void ui_init(void);

/* Online = WiFi connected and the server answered.  Drives the header
 * status dot and which button hints are offered. */
void ui_set_online(bool online);

/* Busy card with a spinner: drawn once per call, so long operations that
 * call it repeatedly animate.  Before the first view it is a splash. */
void ui_status(const char *fmt, ...) __attribute__((format(printf, 1, 2)));

/* Short banner shown over the views for a couple of seconds. */
void ui_toast(UiTone tone, const char *fmt, ...) __attribute__((format(printf, 2, 3)));

/* Modal card; Cross or Circle closes it. */
void ui_message(UiTone tone, const char *title, const char *fmt, ...)
    __attribute__((format(printf, 3, 4)));

/* Modal card offering a choice.  `buttons` lists the glyph names that
 * answer ("CROSS", "SQUARE", "TRIANGLE"); Circle always cancels.  Returns
 * the PSP_CTRL_* bit of the button pressed (PSP_CTRL_CIRCLE = cancel). */
uint32_t ui_ask(UiTone tone, const char *title,
                const char *const *buttons, const char *const *labels, int count,
                const char *fmt, ...) __attribute__((format(printf, 6, 7)));

/* Label / value card (Triangle "details"); Cross, Circle or Triangle
 * closes it. */
void ui_details(const char *title, const char *const *labels,
                const char *const *values, int count);

/* Full-screen error for unrecoverable states (bad config, nothing to
 * show).  Drawn once; the caller then sleeps until HOME. */
void ui_fatal(const char *title, const char *fmt, ...) __attribute__((format(printf, 2, 3)));

/* Compare dialog for a save: this PSP vs. the server, with `suggested`
 * as the Cross action.  Square forces an upload (unless allow_upload is
 * false), Triangle a download, Circle cancels.  Returns SYNC_UPLOAD,
 * SYNC_DOWNLOAD or -1 (cancelled / nothing to do).
 * server_last_sync: ISO 8601 (or NULL/empty). */
int ui_confirm(const TitleInfo *title, SyncAction suggested, bool allow_upload,
               const char *server_hash, uint32_t server_size,
               const char *server_last_sync);

/* Result of Sync all; Cross or Circle closes it. */
void ui_sync_summary(const SyncSummary *summary);

/* ---- Views (L / R cycle Saves -> Catalog -> Downloads -> Settings) ---- */

void ui_draw_saves(const SyncState *state, int selected, int scroll);

/* notice: optional line under the list ("Offline - cached copy"). */
void ui_draw_rom_catalog(const RomCatalog *catalog,
                         const DownloadList *downloads,
                         const char *const *systems, int system_count,
                         int system_index,
                         int selected, int scroll_offset,
                         const char *notice);

/* vsync = false while a transfer is running: the progress callback
 * redraws at a bounded rate and must not wait for vblank. */
void ui_draw_downloads(const DownloadList *downloads,
                       int selected, int scroll_offset,
                       bool active_in_progress,
                       uint64_t active_downloaded,
                       uint64_t active_total,
                       uint64_t active_bps,
                       bool vsync);

/* One Settings row: a label and its value; `action` rows run something
 * on Cross, the others are information.  `help` fills the detail panel. */
typedef struct {
    const char *label;
    char        value[260];
    const char *help;
    bool        action;
} UiSettingsRow;

void ui_draw_settings(const UiSettingsRow *rows, int count,
                      int selected, int scroll);

#endif /* UI_H */
