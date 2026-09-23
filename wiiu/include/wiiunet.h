#ifndef WIIUSYNC_NETWORK_H
#define WIIUSYNC_NETWORK_H

#include "common.h"
#include "savetree.h"

/*
 * Wii U networking.  The console's own network settings apply (no static-IP
 * config like the GameCube/BBA client), so bring-up is just:
 *   1. nsysnet sockets (initialised by wut's startup code)
 *   2. ACInitialize + ACGetAssignedAddress to learn our IPv4 address
 *
 * Everything above that is a thin wrapper over http.c.
 */

int  network_init(SyncState *state);
void network_shutdown(void);
bool network_is_ready(const SyncState *state);
bool network_check_server(const SyncState *state);

/* Last error string captured by any network_* call ("" when clean). */
const char *network_last_error(void);

/* Streaming progress callback for ROM downloads — non-zero aborts. */
typedef int (*NetProgress64Fn)(uint64_t downloaded, uint64_t total);
void network_set_progress64_cb(NetProgress64Fn cb);

/* ---- ROM catalog / downloads ---- */

/* GET /api/v1/roms?system=<code>&offset=X&limit=Y */
int network_fetch_rom_catalog(const SyncState *state,
                              const char *system_code,
                              int offset, int limit,
                              char *out, uint32_t out_size,
                              int *status_out);

/* Resumable streaming download to target_path (.part + atomic rename).
 *    0 ok / 1 paused / -1 net error / -2 fs error / -3 HTTP non-2xx */
int network_download_rom_resumable(const SyncState *state,
                                   const char *rom_id,
                                   const char *extract_fmt,
                                   const char *target_path,
                                   uint64_t start_offset,
                                   uint64_t *total_out);

/* Download an arbitrary server path (used for Wii WBFS split parts, which
 * are not addressed by rom_id alone). Same return codes as above. */
int network_download_path_resumable(const SyncState *state,
                                    const char *path,
                                    const char *target_path,
                                    uint64_t start_offset,
                                    uint64_t *total_out);

/* ---- Wii WBFS conversion manifest ---- */

#define WBFS_MAX_PARTS 8

typedef struct {
    char     name[64];
    uint64_t size;
} WbfsPart;

typedef struct {
    char     game_id[8];       /* ID6 */
    char     name[MAX_TITLE_LEN];
    int      part_count;
    WbfsPart parts[WBFS_MAX_PARTS];
    char     last_error[160];
} WbfsManifest;

/* GET /api/v1/roms/{rom_id}/wbfs-manifest — triggers the server-side
 * RVZ -> ISO -> WBFS conversion, which can take minutes on a big game. */
int network_fetch_wbfs_manifest(const SyncState *state, const char *rom_id,
                                char *scratch, uint32_t scratch_size,
                                WbfsManifest *out);

/* ---- bundle manifest (Wii U WUP folders) ---- */

/* A WUP set is one .app (+ .h3) per content plus title.tmd/tik/cert.  Real
 * dumps run 20-50 files; 128 leaves headroom for titles with DLC/update
 * contents merged in without letting a bad manifest blow the stack. */
#define BUNDLE_MAX_FILES 128

typedef struct {
    char     name[128];        /* path relative to the bundle root */
    uint64_t size;
} BundleFile;

typedef struct {
    char       name[MAX_TITLE_LEN];
    int        file_count;
    uint64_t   total_size;
    BundleFile files[BUNDLE_MAX_FILES];
    char       last_error[160];
} BundleManifest;

/* GET /api/v1/roms/{rom_id}/manifest — the file list for a bundle entry.
 * Each file is then fetched from /roms/{rom_id}/file/<name>, so a 15 GB Wii U
 * title downloads incrementally and resumably instead of as one ZIP. */
int network_fetch_bundle_manifest(const SyncState *state, const char *rom_id,
                                  char *scratch, uint32_t scratch_size,
                                  BundleManifest *out);

/* ---- smart sync (vWii / Wii U) ---- */

#define SYNC_MAX_TITLES SAVE_MAX_TITLES

typedef struct {
    int upload_count;
    int download_count;
    int conflict_count;
    int up_to_date_count;
    int server_only_count;
    char (*upload_ids)[TITLE_ID_LEN];
    char (*download_ids)[TITLE_ID_LEN];
    char (*conflict_ids)[TITLE_ID_LEN];
    char (*up_to_date_ids)[TITLE_ID_LEN];
    char (*server_only_ids)[TITLE_ID_LEN];
    char (*server_only_names)[SAVE_NAME_MAX];   /* parallel to server_only_ids */
} SyncPlan;

void sync_plan_free(SyncPlan *p);

/* POST /api/v1/sync with the local title list (platforms: WII + WIIU). */
int network_sync_plan(const SyncState *state,
                      const SaveTitleList *list,
                      const char *const *platforms, int platform_count,
                      SyncPlan *out);

/* Stream a v5 bundle straight from disk with HTTP chunked transfer, so a
 * large Wii U save never has to be assembled in RAM.  Returns the HTTP
 * status code (negative on transport failure). */
int network_upload_save_stream(const SyncState *state,
                               const SaveTitle *title,
                               uint32_t timestamp,
                               int force,
                               char *save_hash_hex);

/* Stream GET /api/v1/saves/<title_id> to ``dest_path``. */
int network_download_save_to_file(const SyncState *state,
                                  const char *title_id,
                                  const char *dest_path,
                                  uint64_t *out_size);

/* Batch name lookup: POST /api/v1/titles/names. */
int network_fetch_names(const SyncState *state,
                        const char (*ids)[TITLE_ID_LEN],
                        int count,
                        char (*names)[SAVE_NAME_MAX]);

/* Teach the server the names of this console's Wii U saves:
 * POST /api/v1/titles/update_names with the product codes and longnames read
 * from each title's meta.xml.
 *
 * A 16-hex Wii U title id resolves to no name anywhere on the server (its low
 * word is not the product code), so without this a save shows as raw hex on
 * the desktop / Steam Deck / Android clients, which have no console NAND to
 * read.  Upload already sends a hint, but a save that is up to date is never
 * re-uploaded, so the sync path has to carry it too.
 *
 * The server only fills in titles still named after their own id, so this is
 * safe to send on every sync.  Returns 0 when the server accepted it. */
int network_push_name_hints(const SyncState *state, const SaveTitleList *list);

#endif /* WIIUSYNC_NETWORK_H */
