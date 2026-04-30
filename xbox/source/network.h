// Higher-level wrappers around the HTTP client.

#ifndef XBOX_NETWORK_H
#define XBOX_NETWORK_H

#include <stdint.h>

#include "config.h"
#include "saves.h"

// Maximum number of titles a single sync plan can carry. Bumping this just
// changes how much memory the SyncPlan arrays consume.
#define SYNC_MAX_TITLES   XBOX_MAX_TITLES
#define XBOX_SAVE_HASH_HEX_LEN 64

typedef struct {
    int upload_count;
    int download_count;
    int conflict_count;
    int up_to_date_count;
    int server_only_count;
    char (*upload_ids)[XBOX_TITLE_ID_LEN + 1];
    char (*download_ids)[XBOX_TITLE_ID_LEN + 1];
    char (*conflict_ids)[XBOX_TITLE_ID_LEN + 1];
    char (*up_to_date_ids)[XBOX_TITLE_ID_LEN + 1];
    char (*server_only_ids)[XBOX_TITLE_ID_LEN + 1];
    char (*server_only_names)[XBOX_NAME_MAX];   // parallel to server_only_ids
} SyncPlan;

typedef struct {
    int      exists;
    char     save_hash[XBOX_SAVE_HASH_HEX_LEN + 1];
    uint32_t client_timestamp;
    uint32_t save_size;
    int      file_count;
    char     server_timestamp[64];
} NetworkSaveMeta;

// Bring up the NIC according to config network_mode (auto/dhcp/static).
int network_init(const XboxConfig *cfg);

// Last error string captured by any network_* call. Persists until the
// next call clears it (i.e. the next request that succeeds wipes the
// previous error). Returns "" when nothing's been captured.
const char *network_last_error(void);

// Copy our resolved IPv4 address into ``out``. ``out_len`` >= 16 recommended.
void network_local_ip(char *out, int out_len);

// Hit GET /api/v1/status. Returns HTTP code (200 = healthy).
int network_status_check(const XboxConfig *cfg,
                         char *out_text, int out_text_len);

// POST a v5 bundle to /api/v1/saves/<title_id>.
int network_upload_save(const XboxConfig *cfg,
                        const char *title_id,
                        const uint8_t *bundle,
                        uint32_t bundle_size);

// Same upload endpoint, but with ?force=true to intentionally overwrite the
// server copy after the UI has asked for confirmation.
int network_upload_save_force(const XboxConfig *cfg,
                              const char *title_id,
                              const uint8_t *bundle,
                              uint32_t bundle_size);

// Stream a v5 bundle directly from disk using HTTP chunked transfer. This is
// for large Xbox saves that cannot fit in RAM as one assembled bundle.
int network_upload_save_stream(const XboxConfig *cfg,
                               const XboxSaveTitle *title,
                               uint32_t timestamp,
                               int force,
                               char *save_hash_hex);

// GET /api/v1/saves/<title_id>. Caller frees ``*out_data``.
int network_download_save(const XboxConfig *cfg,
                          const char *title_id,
                          uint8_t **out_data,
                          uint32_t *out_size);

// GET /api/v1/saves/<title_id>/meta. Returns 0 on a successful lookup or
// clean 404 (``out->exists`` tells which); negative on transport/server error.
int network_get_save_meta(const XboxConfig *cfg,
                          const char *title_id,
                          NetworkSaveMeta *out);

// POST /api/v1/sync with the local title list. Populates ``out`` with the
// server's plan; caller must release ``out`` via sync_plan_free.
// Returns 0 on success, negative on error.
int network_sync_plan(const XboxConfig *cfg,
                      const XboxSaveList *list,
                      SyncPlan *out);

void sync_plan_free(SyncPlan *p);

// Batch name lookup: POST /api/v1/titles/names. ``ids`` is a flat array of
// length ``count``; ``names`` is a parallel output array (each row >=
// XBOX_NAME_MAX chars). Names not resolved are written as empty strings.
// Returns 0 on success, negative on error.
int network_fetch_names(const XboxConfig *cfg,
                        const char (*ids)[XBOX_TITLE_ID_LEN + 1],
                        int count,
                        char (*names)[XBOX_NAME_MAX]);

#endif // XBOX_NETWORK_H
