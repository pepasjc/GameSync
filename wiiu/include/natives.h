#ifndef WIIUSYNC_NATIVES_H
#define WIIUSYNC_NATIVES_H

#include "common.h"
#include "savetree.h"

/*
 * Native (non-GameCube) save families, both reached through libmocha's FSA
 * devoptab mounts:
 *
 *   vWii   storage_slccmpt01:/title/00010000/<tidlo>/data/
 *          title_id = "WII_" + the ASCII-decoded tidlo, e.g. WII_RMCE.
 *          That keeps the same shape as the GameCube client's GC_<code> and
 *          resolves to console_type "WII" on the server with no changes.
 *
 *   Wii U  storage_mlc01:/usr/save/00050000/<tidlo>/user/
 *          title_id = the native 16-hex title id, uppercase.
 *
 * Both are bundled whole (vWii excludes nocopy/, which by definition must not
 * leave the console).  Restores always take an SD backup first.
 */

/* Bring up libmocha and mount SLC + MLC.  Fills state->mocha_ok /
 * slc_mounted / mlc_mounted / mocha_error.  Returns 0 when mocha is up. */
int  natives_init(SyncState *state);
void natives_shutdown(SyncState *state);

/* Mount a FAT32 USB drive as "usb:" so downloaded games can live there
 * instead of on the SD card.  This is a different device from the WFS
 * ``storage_usb01:`` mount above — that one is the console's own formatted
 * drive and holds Wii U saves, not /games or /wbfs folders.
 *
 * Called by natives_init(); exposed so the Config view can retry after the
 * user plugs a drive in.  Fills state->usb_fat_ready / usb_fat_error. */
bool natives_mount_usb_fat(SyncState *state);

/* (Re)attach the console's WFS USB (storage_usb01) for save reads; sets
 * state->usb_mounted.  Idempotent — the install view calls it to refresh the
 * flag before showing the USB target label. */
bool natives_mount_usb_wfs(SyncState *state);

/* ---- vWii ---- */

void vwiisaves_scan(const SyncState *state, SaveTitleList *out);

/* Absolute data/ dir for a WII_xxxx title id.  Returns false if the id is
 * not a well-formed vWii id. */
bool vwiisaves_root_for(const char *title_id, char *out, size_t out_size);

/* ---- Wii U ---- */

void wiiusaves_scan(const SyncState *state, SaveTitleList *out);

/* Absolute user/ dir for a 00050000xxxxxxxx title id. */
bool wiiusaves_root_for(const char *title_id, char *out, size_t out_size);

/* ---- shared ---- */

/* Resolve the on-console root for any native title id (vWii or Wii U). */
bool natives_root_for(const char *title_id, char *out, size_t out_size);

/* Copy a title's current on-console save to
 * <sd>/3dssync/backup/<title_id>/ before a restore overwrites it.
 * A missing source (server-only title) is not an error. */
int  natives_backup(const SyncState *state, const char *title_id,
                    char *msg, size_t msg_size);

#endif /* WIIUSYNC_NATIVES_H */
