#ifndef NETWORK_H
#define NETWORK_H

#include "common.h"

/* Initialize network using SceNet + SceHttp (VitaSDK).
 * Must be called once before any network operations. */
int network_init(void);
void network_cleanup(void);

/* Connect to WiFi (uses system WiFi settings).
 * Returns 0 on success. */
int network_connect(void);

bool network_is_connected(void);
bool network_check_server(const SyncState *state);

/* Sync plan returned by network_get_sync_plan */
#define SYNC_PLAN_MAX MAX_TITLES
typedef struct {
    char upload  [SYNC_PLAN_MAX][GAME_ID_LEN]; int upload_count;
    char download[SYNC_PLAN_MAX][GAME_ID_LEN]; int download_count;
    char conflict[SYNC_PLAN_MAX][GAME_ID_LEN]; int conflict_count;
} NetworkSyncPlan;

/* Fetch save metadata from server for a game.
 * hash_out:      65-byte buffer for hex hash (or empty if no save).
 * last_sync_out: 32-byte buffer for ISO 8601 timestamp, or NULL to skip.
 * Returns 0 if save exists, 1 if not found, negative on error. */
int network_get_save_info(const SyncState *state, const char *game_id,
                          char *hash_out, uint32_t *size_out,
                          char *last_sync_out);

/* POST all title hashes to /api/v1/sync and get a sync plan.
 * Returns 0 on success, negative on error. */
int network_get_sync_plan(const SyncState *state, NetworkSyncPlan *plan);

int network_upload_save(const SyncState *state, const TitleInfo *title,
                        const uint8_t *bundle, uint32_t bundle_size);

/* Returns bytes received or negative on error. */
int network_download_save(const SyncState *state, const char *game_id,
                          uint8_t *out, uint32_t out_size);

int network_post_json(const SyncState *state, const char *path,
                      const char *json,
                      uint8_t *out, uint32_t out_size, int *out_len);

/* Fetch game names from the server for all titles in state.
 * Populates title->name for any title whose name is found.
 * Silently does nothing if the request fails. */
void network_fetch_names(SyncState *state);

/* Merge downloadable Vita/PSP/PS1 titles from the server into state->titles.
 * Existing local entries are preserved; only missing titles are added. */
void network_merge_server_titles(SyncState *state);

/* ----- ROM catalog + streaming downloads (mirror of the PSP client) -----
 *
 * These back the ROM Catalog / Downloads views.  Downloads stream
 * straight to disk in 64 KB chunks with HTTP Range resume, so a
 * multi-hundred-MB CSO or EBOOT never has to fit in RAM. */

/* Paginated catalog fetch (``GET /api/v1/roms?system=&limit=&offset=``).
 * Returns bytes received, or negative on error; ``status_out`` gets the
 * HTTP status. */
int network_fetch_rom_catalog(const SyncState *state,
                              const char *system_code,
                              int offset, int limit,
                              char *out, uint32_t out_size,
                              int *status_out);

/* ``GET /api/v1/roms/scan`` so games added on the server show up
 * without restarting the app.  Returns 0 on 200 OK; ``count_out``
 * (optional) receives the server's catalog row count. */
int network_trigger_rom_scan(const SyncState *state, int *count_out);

/* Streaming progress callback — cumulative bytes on disk and the
 * expected total (0 when unknown).  Return non-zero to pause. */
typedef int (*NetProgress64Fn)(uint64_t downloaded, uint64_t total);
void network_set_progress64_cb(NetProgress64Fn cb);

/* Resumable streaming download of a ROM (or its ``?extract=<fmt>``
 * conversion).  Bytes go to ``<target_path>.part``; on completion the
 * .part is renamed into place.
 *
 * Returns:
 *    0  complete
 *    1  paused — progress callback returned non-zero; .part kept
 *   -1  network/server error
 *   -2  filesystem write error
 *   -3  HTTP non-200/206 (404 / 416 / 503 conversion not configured) */
int network_download_rom_resumable(const SyncState *state,
                                   const char *rom_id,
                                   const char *extract_fmt,
                                   const char *target_path,
                                   uint64_t start_offset,
                                   uint64_t *total_out);

/* HTTP status of the last network_download_rom_resumable call, so the
 * UI can tell a 503 (server can't convert) from a 404. */
int network_last_download_status(void);

#endif
