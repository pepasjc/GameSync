#ifndef PS2SYNC_UI_H
#define PS2SYNC_UI_H

#include "common.h"

/*
 * Two-phase UI:
 *
 *   Phase 1 (boot): libdebug scr_printf scrolling console, used for
 *   IRX-load and module-init diagnostics.  ui_boot_init() sets it up;
 *   the rest of boot just calls scr_printf directly.
 *
 *   Phase 2 (running): gsKit framebuffer at 640x448 (NTSC).  ui_init()
 *   takes over the GS, uploads the ROM-resident font (FONTM) as a
 *   texture, and from then on every screen is drawn from scratch via
 *   gsKit_clear + a series of textured quads.  The libdebug console
 *   stops working after ui_init() (both libraries claim the GS) — so
 *   anything to be shown post-boot must go through ui_*.
 *
 * LIST_VISIBLE is computed in ui.c from the actual font metrics.
 */

#include "downloads.h"
#include "roms.h"
#include "saves.h"

/* Phase 1: bring up libdebug.  Cheap; safe to call before SIF reset. */
void ui_boot_init(void);

/* Phase 2: hand the GS to gsKit and load FONTM. */
void ui_init(void);

/* Number of list rows visible after ui_init().  Valid only after the
 * gsKit phase has started — reads computed font metrics. */
int  ui_list_visible(void);

void ui_clear(void);
/* Submit queued primitives + flip.  Required to display anything. */
void ui_flush(void);

void ui_status(const char *fmt, ...);
void ui_error(const char *fmt, ...);

void ui_draw_header(const SyncState *state, AppView view);
void ui_draw_roms(const RomCatalog *catalog, int selected, int scroll);
void ui_draw_local(const LocalRomList *list, int selected, int scroll);
void ui_draw_saves(const SaveVmcList *list, int selected, int scroll);
void ui_draw_mcard(const McGameList *list, int selected, int scroll);
void ui_draw_server(const ServerSaveList *list, int selected, int scroll);
void ui_set_server_source(const char *src);
void ui_set_mmce(int port, int mode);
void ui_draw_downloads(const DownloadList *list, int selected, int scroll,
                       uint64_t active_done, uint64_t active_total,
                       uint64_t active_bps);
void ui_draw_config(const SyncState *state);

void ui_draw_message(const char *title, const char *message);

#endif /* PS2SYNC_UI_H */
