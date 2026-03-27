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

#endif
