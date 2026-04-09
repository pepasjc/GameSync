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

#endif /* PS3SYNC_NETWORK_H */
