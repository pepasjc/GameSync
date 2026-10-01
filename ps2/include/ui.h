#ifndef PS2SYNC_UI_H
#define PS2SYNC_UI_H

#include "common.h"

/*
 * GameSync screens, drawn with the gsKit kit in gui.c.
 *
 *   Boot:    ui_init() takes the GS straight away and shows a splash with
 *            a live log card; every ui_log() line repaints it so IRX /
 *            memory card / network bring-up stays visible.  ui_boot_done()
 *            ends that phase — later ui_log() calls are kept off screen.
 *
 *   Running: redraws are event driven.  A frame is ui_begin(), the header
 *            + one view + optional overlays, then ui_flush().
 */

#include "downloads.h"
#include "roms.h"
#include "saves.h"

void ui_init(void);
void ui_log(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
void ui_boot_done(void);

/* Milliseconds since boot. */
uint32_t ui_ms(void);

/* Number of list rows on screen (page size for D-pad Left/Right). */
int  ui_list_visible(void);

void ui_begin(void);
/* Present the frame (waits for vblank). */
void ui_flush(void);
/* Present without the vblank wait: progress refreshes mid-transfer. */
void ui_flush_nowait(void);

void ui_status(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
void ui_error(const char *fmt, ...) __attribute__((format(printf, 1, 2)));

/* Cross-references used to badge rows ("installed", "queued"). */
void ui_set_context(const LocalRomList *local, const DownloadList *downloads);
/* Server view sync source: 0 = VMC, 1 = slot 1, 2 = slot 2. */
void ui_set_server_source(int source);
void ui_set_mmce(int port, int mode);
/* Catalog badge ("Cached", "Offline", or NULL) and a short description of
 * where the catalog cache lives, shown in Settings. */
void ui_set_catalog_info(const char *badge, const char *cache_desc);

/* Header, view tabs, status banner and the view's footer hints. */
void ui_draw_header(const SyncState *state, AppView view);
void ui_draw_roms(const RomCatalog *catalog, int selected, int scroll);
void ui_draw_local(const LocalRomList *list, int selected, int scroll);
void ui_draw_saves(const SaveVmcList *list, int selected, int scroll);
void ui_draw_mcard(const McGameList *list, int selected, int scroll);
void ui_draw_server(const ServerSaveList *list, int selected, int scroll);
void ui_draw_downloads(const DownloadList *list, int selected, int scroll);
void ui_draw_config(const SyncState *state, int selected_row);

/* Modal transfer card drawn over the current view. */
void ui_draw_transfer(const char *name, const char *target,
                      uint64_t done, uint64_t total, uint64_t bps,
                      uint32_t elapsed_ms);
/* Modal yes/no card: CROSS confirms, CIRCLE cancels. */
void ui_draw_confirm(const char *title, const char *message);
/* Modal action menu: Up/Down pick, CROSS runs, CIRCLE cancels. */
void ui_draw_menu(const char *title, const char *const *items, int count, int selected);
/* Modal details card: CIRCLE (or CROSS / TRIANGLE) closes. */
void ui_draw_info(const char *title, const char *message);
/* Full-screen message card (fatal boot errors). */
void ui_draw_message(const char *title, const char *message);

#endif /* PS2SYNC_UI_H */
