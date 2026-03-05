#ifndef NETWORK_H
#define NETWORK_H

#include "common.h"

/* Initialize PSP network modules (sceNet, sceNetInet, sceNetApctl).
 * Must be called before any network operations.
 * Returns 0 on success. */
int network_init(void);

/* Connect to WiFi using one of the PSP's saved access points (index 0-2).
 * Returns 0 on success. This can take several seconds. */
int network_connect_ap(int ap_index);

/* Disconnect from WiFi. */
void network_disconnect(void);

/* Check if WiFi is connected. */
bool network_is_connected(void);

/* HTTP GET request.
 * url: full URL
 * api_key, console_id: authentication headers
 * out: buffer to receive response body
 * out_size: size of out
 * Returns response body length, or negative on error. */
int network_http_get(const SyncState *state, const char *path,
                     uint8_t *out, uint32_t out_size);

/* HTTP POST request with binary body.
 * Returns 0 on success. */
int network_http_post(const SyncState *state, const char *path,
                      const uint8_t *body, uint32_t body_size,
                      uint8_t *out, uint32_t out_size, int *out_len);

/* HTTP POST with JSON body.
 * Returns 0 on success. */
int network_http_post_json(const SyncState *state, const char *path,
                           const char *json,
                           uint8_t *out, uint32_t out_size, int *out_len);

/* Check if the server is reachable. Returns true if /api/v1/status returns 200. */
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

/* Upload a save bundle for a game. Returns 0 on success. */
int network_upload_save(const SyncState *state, TitleInfo *title,
                        const uint8_t *bundle, uint32_t bundle_size);

/* Download a save bundle for a game.
 * out: buffer to receive bundle data.
 * Returns bundle size in bytes, or negative on error. */
int network_download_save(const SyncState *state, const char *game_id,
                          uint8_t *out, uint32_t out_size);

/* Fetch game names from the server for all titles in state.
 * Populates title->name for any title whose name is found.
 * Silently does nothing if the request fails. */
void network_fetch_names(SyncState *state);

#endif /* NETWORK_H */
