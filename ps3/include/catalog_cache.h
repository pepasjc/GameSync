/*
 * catalog_cache.h — the ROM catalog kept on the HDD between runs.
 *
 * Same strategy as the MiSTer client (mister/gamesync/catalogcache.py):
 * the server publishes a fingerprint per system (GET /api/v1/roms/fingerprints)
 * and the client keeps each system's rows on disk next to the fingerprint they
 * were fetched under.  On the next run only a system whose fingerprint moved
 * is fetched again; a server that cannot be reached leaves the last copy
 * usable.
 *
 * One file per system, plain text so it can be inspected over FTP:
 *
 *   GSCATALOG <CATALOG_CACHE_VERSION>
 *   system=<SYS>
 *   fingerprint=<fp>
 *   count=<n>
 *   rom_id \t filename \t name \t system \t size \t is_bundle \t file_count \t extract_format
 *   ...
 *
 * Only the RomEntry fields the client uses are stored.  The module is plain
 * stdio so it builds and can be exercised on a host compiler too.
 */

#ifndef PS3SYNC_CATALOG_CACHE_H
#define PS3SYNC_CATALOG_CACHE_H

#include "roms.h"

#include <stdbool.h>
#include <stddef.h>

/* Bump whenever the stored row fields change so an older cache is refetched
 * instead of misread. */
#define CATALOG_CACHE_VERSION 1

/* Where the per-system files live: <dir>/catalog_<SYS>.dat */
#define CATALOG_CACHE_DIR APP_DIR

#define CATALOG_FP_LEN 96

/* Build the cache file path for a system. */
void catcache_path(const char *dir, const char *system, char *out, size_t out_size);

/* Load a system's cached rows into `catalog` (replacing its contents) and its
 * fingerprint into fp_out.  Returns false when the file is missing, has a
 * different format version, belongs to another system or is truncated —
 * `catalog` is left empty then. */
bool catcache_load(const char *dir, const char *system,
                   char *fp_out, size_t fp_size, RomCatalog *catalog);

/* Read only the fingerprint from a cache file header (no rows). */
bool catcache_peek(const char *dir, const char *system, char *fp_out, size_t fp_size);

/* Write `catalog` as the cache for `system` (rows from another system are
 * skipped).  Written to a .part file and renamed so a crash never leaves a
 * half file behind.  Returns false on any I/O error. */
bool catcache_save(const char *dir, const char *system, const char *fingerprint,
                   const RomCatalog *catalog);

/* Delete a system's cache file (no error if absent). */
void catcache_drop(const char *dir, const char *system);

/* Look up one system in a /roms/fingerprints response body:
 *   {"systems": {"PS3": {"fingerprint": "...", "count": N}, ...}}
 * Returns 1 and fills fp_out / count_out (optional) when the system is
 * listed, 0 when it is not (the server has no games for it), -1 when the
 * body has no "systems" object at all. */
int catcache_fingerprint_for(const char *json, const char *system,
                             char *fp_out, size_t fp_size, int *count_out);

#endif /* PS3SYNC_CATALOG_CACHE_H */
