/*
 * catcache.h — the server's ROM catalog kept on the memory card between runs.
 *
 * Same strategy as the MiSTer client (mister/gamesync/catalogcache.py):
 * the server publishes one fingerprint per system
 * (``GET /api/v1/roms/fingerprints``), each system's rows are stored next
 * to the fingerprint they were fetched under, and on the next start only
 * the systems whose fingerprint moved are fetched again.  A server that
 * can't be reached leaves the last copy usable.
 *
 * One binary file (CATALOG_CACHE_FILE), streamed, never loaded whole:
 *
 *   "GSCC"  u32 version
 *   section*:  char system[8]  u8 fp_len  fp[fp_len]
 *              u32 row_count   u32 payload_bytes   rows...
 *   row:       str rom_id  str filename  str name  str system  str title_id
 *              u64 size  u8 is_bundle  u16 file_count  str extract_format
 *              u8 disc_index  u8 disc_total
 *   str:       u8 len + bytes          (all integers little-endian)
 *
 * Only the RomEntry fields the client shows or installs from are kept.
 * Bump CATCACHE_VERSION whenever a row field is added or changes meaning;
 * a file with another version is treated as empty.
 *
 * Pure C + stdio so it builds on the host for tests (vita/tests/).
 */

#ifndef VITASYNC_CATCACHE_H
#define VITASYNC_CATCACHE_H

#include <stdbool.h>
#include <stddef.h>

#include "roms.h"

#define CATCACHE_VERSION      1
#define CATCACHE_FP_LEN       80
#define CATCACHE_SYSTEM_LEN   8
#define CATCACHE_MAX_SYSTEMS  64

typedef struct {
    char system[CATCACHE_SYSTEM_LEN];
    char fingerprint[CATCACHE_FP_LEN];
    int  count;
} CatFingerprint;

typedef struct {
    CatFingerprint items[CATCACHE_MAX_SYSTEMS];
    int count;
} CatFingerprints;

typedef enum {
    CATCACHE_FRESH = 0,  /* cached copy matches the server's fingerprint */
    CATCACHE_STALE = 1,  /* missing or out of date: refetch, then put */
    CATCACHE_GONE  = 2,  /* server lists no ROMs for it: cache dropped */
} CatPlan;

/* Cache file path (default CATALOG_CACHE_FILE).  Tests point it at a
 * temp file. */
void catcache_set_path(const char *path);

/* Parse a ``/roms/fingerprints`` body.  False when it isn't the
 * expected ``{"systems": {...}}`` shape. */
bool catcache_parse_fingerprints(const char *json, size_t len, CatFingerprints *out);

/* Server fingerprint entry for a system, or NULL. */
const CatFingerprint *catcache_find_fp(const CatFingerprints *fps, const char *system);

/* Stored fingerprint / row count for a system.  False when the cache has
 * no (valid) section for it. */
bool catcache_info(const char *system, char *fp_out, size_t fp_size, int *count_out);

/* Load one system's rows into `out` (count reset first).  False when the
 * system isn't cached or the file is damaged (then out->count is 0). */
bool catcache_load(const char *system, RomCatalog *out);

/* Compare the cached copy with the server's fingerprints.  A system the
 * server no longer lists is dropped from the file (CATCACHE_GONE). */
CatPlan catcache_plan(const char *system, const CatFingerprints *server);

/* Store (replace) a system's rows under `fingerprint`.  Written straight
 * to disk with a temp file + rename, so there is no separate save step. */
bool catcache_put(const char *system, const char *fingerprint, const RomCatalog *rows);

/* Remove one system / the whole cache. */
bool catcache_drop(const char *system);
void catcache_clear(void);

#endif /* VITASYNC_CATCACHE_H */
