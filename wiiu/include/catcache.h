#ifndef WIIUSYNC_CATCACHE_H
#define WIIUSYNC_CATCACHE_H

#include "roms.h"

/*
 * On-SD copy of the server ROM catalog, one file per system, refreshed by
 * difference — the same strategy as the MiSTer client (catalogcache.py).
 *
 * The server publishes a fingerprint per system (GET /api/v1/roms/fingerprints).
 * Each system's rows are stored next to the fingerprint they were fetched
 * under; a catalog load only refetches a system whose fingerprint moved, and
 * an unreachable server falls back to the stored copy.
 *
 * File: <sd>/3dssync/catalog/<SYSTEM>.bin, big-endian binary:
 *
 *   "GSWC"  u32 version  u32 count  str fingerprint  str system
 *   count x row:
 *     str rom_id  str filename  str name  str system  u64 size
 *     str extract_format  u8 is_bundle  str content_type
 *     u8 related_count  related_count x str related_id
 *
 *   str = u16 length + bytes (no terminator)
 *
 * Only the fields RomEntry carries are stored.  Bump CATCACHE_VERSION when a
 * row field is added or changes meaning so older files are refetched rather
 * than misread.
 */

#define CATCACHE_VERSION  1
#define CATCACHE_FP_LEN   80

/* "<sd_root>/3dssync/catalog" */
void catcache_dir(const char *sd_root, char *out, size_t out_size);

/* Load ``system`` into ``cat`` (count reset first).  ``fp_out`` receives the
 * stored fingerprint.  False when the file is missing, from another version
 * or damaged — ``cat`` is then left empty. */
bool catcache_load(const char *dir, const char *system,
                   char *fp_out, size_t fp_size, RomCatalog *cat);

/* Write ``cat`` as the copy of ``system`` under ``fingerprint`` (atomically:
 * a .part file renamed into place).  False on any I/O error. */
bool catcache_save(const char *dir, const char *system,
                   const char *fingerprint, const RomCatalog *cat);

/* Drop one system's copy / every copy. */
void catcache_drop(const char *dir, const char *system);
void catcache_wipe(const char *dir);

#endif /* WIIUSYNC_CATCACHE_H */
