#ifndef PS2SYNC_CATCACHE_H
#define PS2SYNC_CATCACHE_H

/*
 * On-disk copy of the ROM catalog, refreshed by fingerprint.
 *
 * Same strategy as the MiSTer client (mister/gamesync/catalogcache.py):
 * the server publishes a fingerprint per system (GET /api/v1/roms/
 * fingerprints) and the catalog rows are kept on disk together with the
 * fingerprint they were fetched under.  On the next start only a system
 * whose fingerprint moved is fetched again; an unreachable server leaves
 * the last copy usable.
 *
 * The PS2 client only browses one system, so a file holds one system's
 * rows in a compact binary form — only the RomEntry fields the client
 * uses, strings length-prefixed — instead of JSON:
 *
 *   "GSCC" u32 version
 *   str system, str fingerprint, u32 count
 *   count x { str rom_id, str filename, str name, str serial,
 *             str extract_format, u64 size, u8 flags, u8 file_count }
 *
 *   str   = u8 length + bytes (no terminator); integers little endian
 *   flags = bit0 is_bundle, bit1 is_cd
 *
 * Pure C (stdio + memory buffers) so it can be compiled and tested on a
 * host.  The memory-card copy is read and written by the caller through
 * libmc (config.c), since newlib stdio cannot reach mc0:.
 */

#include "roms.h"

#include <stdbool.h>
#include <stddef.h>

/* Bump when a stored field is added or changes meaning, so an older file is
 * refetched rather than misread. */
#define CATCACHE_VERSION 1

/* Largest cache written to a memory card (8 MB total): anything bigger is
 * kept in RAM for the session instead. */
#define CATCACHE_MC_MAX_BYTES (128u * 1024u)

/* Bytes catcache_save() would write for this catalog. */
size_t catcache_encoded_size(const char *system, const char *fingerprint,
                             const RomCatalog *cat);

/* Same format in a memory buffer, for the memory card copy (libmc, not
 * stdio).  encode returns the byte count, 0 if it does not fit in cap;
 * decode fails (cat->count = 0) on a foreign / damaged buffer. */
size_t catcache_encode(void *buf, size_t cap, const char *system,
                       const char *fingerprint, const RomCatalog *cat);
bool catcache_decode(const void *buf, size_t len, const char *system,
                     char *fp_out, size_t fp_size, RomCatalog *cat);

/* stdio file on mass storage.  Write atomically (path.tmp, then rename).  Returns false on any I/O
 * error; a failed write is never fatal to the caller. */
bool catcache_save(const char *path, const char *system,
                   const char *fingerprint, const RomCatalog *cat);

/* Read a cache written for `system`.  Fills `cat` and the stored
 * fingerprint.  Returns false (and leaves cat->count = 0) when the file is
 * missing, from another version or system, or damaged. */
bool catcache_load(const char *path, const char *system,
                   char *fp_out, size_t fp_size, RomCatalog *cat);

void catcache_remove(const char *path);

/* Find `system`'s fingerprint in a /roms/fingerprints response body:
 *   {"systems": {"PS2": {"fingerprint": "...", "count": N}, ...}}
 * Returns 1 when found, 0 when the server does not list the system,
 * -1 when the body is not a fingerprints response. */
int catcache_parse_fingerprint(const char *json, size_t len, const char *system,
                               char *fp_out, size_t fp_size, int *count_out);

#endif /* PS2SYNC_CATCACHE_H */
