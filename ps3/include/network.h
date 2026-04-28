#ifndef PS3SYNC_NETWORK_H
#define PS3SYNC_NETWORK_H

#include "common.h"

#include <stdbool.h>
#include <stdint.h>

#define SYNC_PLAN_MAX 256

typedef struct {
    char upload[SYNC_PLAN_MAX][GAME_ID_LEN];
    int  upload_count;
    char download[SYNC_PLAN_MAX][GAME_ID_LEN];
    int  download_count;
    char conflict[SYNC_PLAN_MAX][GAME_ID_LEN];
    int  conflict_count;
} NetworkSyncPlan;

/* Optional callback called during large transfers.
 * Receives bytes downloaded so far and total (-1 if unknown).
 * Return non-zero to abort the transfer. */
typedef int (*NetProgressFn)(uint32_t downloaded, int total);
void network_set_progress_cb(NetProgressFn cb);

int  network_init(void);
void network_cleanup(void);

bool network_check_server(const SyncState *state);

/* Fetch metadata for one save from the server.
 * Returns 0 on success, 1 if not found (404), negative on error. */
int  network_get_save_info(const SyncState *state, const TitleInfo *title,
                           char *hash_out, uint32_t *size_out,
                           char *last_sync_out);
int  network_get_save_manifest(const SyncState *state, const char *title_id,
                               char *manifest_out, uint32_t manifest_out_size);

/* Merge server title list into state (adds server-only placeholder entries). */
void network_merge_server_titles(SyncState *state);

/* Fetch game names from the server and populate state->titles[].name. */
void network_fetch_names(SyncState *state);

/* Ask the server for a sync plan (upload/download/conflict per title). */
int  network_get_sync_plan(const SyncState *state, NetworkSyncPlan *plan);

/* Upload a bundle. Returns 0 on success, non-zero on error. */
int  network_upload_save(const SyncState *state, const char *game_code,
                         const uint8_t *bundle, uint32_t bundle_size);
int  network_upload_ps1_card(const SyncState *state, const TitleInfo *title,
                             const uint8_t *card_data, uint32_t card_size);

/* Download a bundle into out. Returns body length on success, <=0 on error. */
int  network_download_save(const SyncState *state, const char *game_code,
                           uint8_t *out, uint32_t out_size);
int  network_download_ps1_card(const SyncState *state, const TitleInfo *title,
                               uint8_t *out, uint32_t out_size);

/* Best-effort webMAN helper: remap /dev_usb000 to the fake USB staging area
 * and trigger a refresh so XMB can see newly staged saves. */
bool network_activate_fake_usb(void);

/* ----- ROM catalog + streaming downloads -----
 *
 * Used by the new ROM browser/download view.  These bypass the in-RAM
 * 8 MB bundle buffer (which only fits saves) and stream straight to disk
 * so multi-GB ISOs and PKGs can be downloaded on the PS3's tiny budget. */

/* Fetch raw catalog JSON for a system.  Caller-owned out buffer is filled
 * with up to out_size-1 bytes (NUL-terminated).  Returns body length on
 * success, <=0 on error.  status_out receives the HTTP status code. */
int network_fetch_rom_catalog(const SyncState *state,
                              const char *system_code,
                              char *out, uint32_t out_size,
                              int *status_out);

/* Ask the server to rescan its ROM directory and rebuild the catalog
 * cache.  Used by the ROM Catalog view's refresh action so the user can
 * pick up newly-added games without restarting the server.  Returns 0 on
 * a 200 OK response, negative on transport / HTTP error.  ``count_out``
 * (optional) receives the catalog row count the server reported. */
int network_trigger_rom_scan(const SyncState *state, int *count_out);

/* Streaming ROM download with Range support.
 *
 *   rom_id        catalog entry id (URL-safe)
 *   target_path   final on-disk path (a sibling .part file is the staging area)
 *   start_offset  bytes already on disk (use partial .part size to resume)
 *   total_out     receives full size in bytes when known (Content-Length +
 *                 start_offset for 206, or Content-Length for 200)
 *
 * Returns:
 *    0  download complete (file renamed from .part to target_path)
 *    1  paused — progress callback returned non-zero; .part kept on disk
 *   -1  network/server error (status code logged via debug_log)
 *   -2  filesystem write error
 *   -3  HTTP status not 200/206 (e.g. 404 ROM removed, 416 Range not
 *       satisfiable — caller should re-stat and retry from offset 0)
 *
 * Pumps sysutil + progress callback every 64 KB chunk so the firmware
 * does not consider the app frozen. */
int network_download_rom_resumable(const SyncState *state,
                                   const char *rom_id,
                                   const char *target_path,
                                   uint64_t start_offset,
                                   uint64_t *total_out);

/* Optional 64-bit progress variant — same NetProgressFn callback, but the
 * download streamer reports cumulative bytes written via this.  Set NULL to
 * fall back to the existing 32-bit network_set_progress_cb hook (sufficient
 * for the sysutil pump but truncates very large files). */
typedef int (*NetProgress64Fn)(uint64_t downloaded, uint64_t total);
void network_set_progress64_cb(NetProgress64Fn cb);

/* Fetch a bundle manifest as JSON.  Used by roms.c — declared here so
 * the linker can resolve the cross-module call without us pulling roms.h
 * into network.c. */
int network_fetch_rom_manifest_http(const SyncState *state,
                                    const char *rom_id,
                                    char *out, uint32_t out_size,
                                    int *status_out);

/* Streaming per-file download from inside a bundle.
 *
 *   rom_id        bundle catalog id
 *   bundle_file   relative file name within the bundle (manifest "name")
 *   target_path   final on-disk path (a sibling .part is the staging area)
 *   start_offset  bytes already on disk in <target_path>.part
 *   total_out     populated with the file's full size
 *
 * Same return contract as network_download_rom_resumable. */
int network_download_bundle_file_resumable(const SyncState *state,
                                           const char *rom_id,
                                           const char *bundle_file,
                                           const char *target_path,
                                           uint64_t start_offset,
                                           uint64_t *total_out);

#endif /* PS3SYNC_NETWORK_H */
