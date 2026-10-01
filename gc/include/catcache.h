#ifndef GCSYNC_CATCACHE_H
#define GCSYNC_CATCACHE_H

#include "common.h"
#include "roms.h"

/*
 * catcache — the server ROM catalog kept on the SD card between runs.
 *
 * Same strategy as the MiSTer client (mister/gamesync/catalogcache.py): the
 * server publishes a fingerprint per system (GET /api/v1/roms/fingerprints),
 * the rows the client uses are stored together with the fingerprint they
 * were fetched under, and on the next start the catalog is only downloaded
 * again when the fingerprint moved.  A server that can't be reached leaves
 * the last copy usable.
 *
 * One small text file per system, only the fields this client reads:
 *
 *   sd:/3dssync/cache/catalog_GC.tsv
 *     GSCATALOG <CATCACHE_VERSION>
 *     <SYSTEM>\t<fingerprint>\t<row count>
 *     <rom_id>\t<size>\t<extract_format>\t<filename>\t<name>     (x count)
 *     END
 *
 * Tabs / newlines inside a value are written as spaces.  A file whose
 * version, system or row count doesn't match, or that lacks the END line
 * (cut short), is ignored and refetched.
 * Pure C stdio — no libogc — so it builds and tests on a host.
 */

/* Bump when the stored row fields change. */
#define CATCACHE_VERSION 1
#define CATCACHE_FP_LEN  80

/* "<sd_root>/3dssync/cache/catalog_<system>.tsv" */
bool catcache_path(const char *sd_root, const char *system, char *out, size_t out_size);

/* Fingerprint of a valid cache file header (no rows read). */
bool catcache_read_fingerprint(const char *path, const char *system,
                               char *fp_out, size_t fp_size);

/* Replace `cat` with the cached rows.  False (and cat->count = 0) when the
 * file is missing, from another version / system, or truncated. */
bool catcache_load(const char *path, const char *system,
                   char *fp_out, size_t fp_size, RomCatalog *cat);

/* Atomic write (.part + rename); creates the cache folder. */
bool catcache_save(const char *path, const char *system, const char *fp,
                   const RomCatalog *cat);

/* Delete the cache file (and a stale .part). */
void catcache_wipe(const char *path);

#endif /* GCSYNC_CATCACHE_H */
