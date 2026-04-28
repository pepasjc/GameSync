/*
 * roms.h — ROM catalog browsing (PS3-only) for the GameSync PS3 client.
 *
 * The catalog is fetched from the GameSync server and presented as a flat
 * list the user can scroll and download.  Per-system filtering keeps the
 * payload small (PS3 catalogs only at the moment, though the helper takes
 * a system code so future expansion is one parameter away).
 *
 * Memory model:
 *   - The raw JSON response is fetched into a caller-owned scratch buffer
 *     (reuse the existing 8 MB net buffer in main.c — catalogs are kBs).
 *   - Parsed rom entries are copied into a fixed-size array on the
 *     RomCatalog struct so we don't have to chase malloc'd pointers across
 *     the long-lived UI loop.
 *
 * No streaming download here — that lives in network.c.  This file is
 * purely catalog metadata + target-path policy.
 */

#ifndef PS3SYNC_ROMS_H
#define PS3SYNC_ROMS_H

#include "common.h"

#include <stdbool.h>
#include <stdint.h>

/* Catalog cap.  Real PS3 catalogs are smaller than this so we just over-
 * provision and call it a day; if a real deployment exceeds this we'd
 * need pagination + windowing. */
#define ROM_CATALOG_MAX 512

/* Max length of a rom_id string returned by the server.  These look like
 * "PS3_BLUS30443" or "PS3_BUNDLE_journey" so 96 is plenty. */
#define ROM_ID_LEN 96

/* Per-bundle file cap.  PSN game directories rarely have more than a
 * dozen files (one main pkg + optional update pkg + optional dlc pkg(s) +
 * one .rap per pkg).  32 is comfortable headroom. */
#define ROM_BUNDLE_FILE_MAX 32

typedef struct {
    char     name[160];          /* relative path inside the bundle */
    uint64_t size;
} RomBundleFile;

typedef struct {
    char     rom_id[ROM_ID_LEN];
    char     filename[160];
    char     name[MAX_TITLE_LEN];     /* server's canonical name (can be empty) */
    char     system[8];               /* "PS3" */
    uint64_t size;                    /* total bytes the server will send */
    bool     is_bundle;               /* subfolder of .pkg/.rap/etc */
    int      file_count;              /* used when is_bundle is true */
} RomEntry;

typedef struct {
    RomEntry items[ROM_CATALOG_MAX];
    int      count;
    char     last_error[128];
} RomCatalog;

/* Result of a bundle manifest fetch.  Owned by the caller; populated by
 * roms_fetch_bundle_manifest().  ``files`` is the per-file list the
 * client iterates when downloading a bundle. */
typedef struct {
    RomBundleFile files[ROM_BUNDLE_FILE_MAX];
    int           count;
    uint64_t      total_size;
    char          last_error[128];
} RomBundleManifest;

/* Fetch + parse the PS3 catalog into ``catalog``.  Pass an existing scratch
 * buffer (e.g. the 8 MB net buffer in main.c) and its capacity so we don't
 * stack-blow PS3's tiny default thread.
 *
 * Returns true on success.  On failure ``catalog->last_error`` is filled
 * with a user-displayable message ("offline", "auth failed", ...). */
bool roms_fetch_ps3_catalog(const SyncState *state,
                            char *scratch_buf, uint32_t scratch_buf_size,
                            RomCatalog *catalog);

/* Compute on-disk target path for a ROM entry.
 *
 *   .iso → /dev_hdd0/PS3ISO/<filename>
 *   .pkg → /dev_hdd0/packages/<filename>
 *   else → /dev_hdd0/game/3DSSYNC00/USRDIR/downloads/<filename>
 *
 * The corresponding directory is mkdir()'d if missing.  out_path is filled
 * with at most out_size-1 bytes.  Returns false if the filename has no
 * recognized extension or out_size is too small. */
bool roms_resolve_target_path(const RomEntry *rom,
                              char *out_path, size_t out_size);

/* Per-file routing inside a PS3 bundle.
 *
 *   .pkg → /dev_hdd0/packages/<filename>
 *   .rap → /dev_hdd0/exdata/<filename>
 *   .iso → /dev_hdd0/PS3ISO/<filename>
 *   else → /dev_hdd0/packages/<filename>  (best-effort fallback)
 *
 * The basename is taken from the bundle file's relative path so a
 * subfolder like "DLC/Foo.pkg" still ends up in /dev_hdd0/packages,
 * matching how MultiMAN's package installer expects to find them. */
bool roms_resolve_bundle_file_target(const char *bundle_file_name,
                                     char *out_path, size_t out_size);

/* Fetch the bundle manifest from the server (GET /roms/{rom_id}/manifest).
 * Caller-owned scratch buffer is reused — pass the same buffer used for
 * the catalog fetch.  Returns true on success; populates manifest->count,
 * files, and total_size. */
bool roms_fetch_bundle_manifest(const SyncState *state,
                                const char *rom_id,
                                char *scratch_buf, uint32_t scratch_buf_size,
                                RomBundleManifest *manifest);

/* Make sure all target directories exist.  Called once at startup so
 * subsequent downloads don't have to retry the mkdir for each file. */
void roms_ensure_target_dirs(void);

/* Stat /dev_hdd0 for free bytes and compare against required.  Returns
 * false if the FS query fails OR if free space is insufficient.
 * available_out (optional) receives the queried free byte count. */
bool roms_check_free_space(uint64_t required_bytes, uint64_t *available_out);

#endif /* PS3SYNC_ROMS_H */
