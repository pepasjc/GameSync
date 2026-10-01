// ROM catalog rows, their JSON parsing and the on-disk catalog cache.
//
// Same strategy as the MiSTer client (mister/gamesync/catalogcache.py): the
// server publishes a fingerprint per system (GET /api/v1/roms/fingerprints),
// the client keeps the rows it shows on disk next to the fingerprint they
// were fetched under, and on the next start only refetches when the
// fingerprint moved. The Xbox only ever shows the XBOX system, so the cache
// holds one system.
//
// Everything in this file is plain C with no Xbox dependencies so the host
// tests in xbox/tests/ can exercise it; the file I/O lives in games.c.
//
// Cache file format (text, one record per line, fields tab-separated):
//
//     GSXCAT <CATCACHE_VERSION>
//     system <SYSTEM>
//     fingerprint <fingerprint>
//     count <N>
//     <rom_id>\t<name>\t<filename>\t<size>\t<is_bundle>      (N lines)
//
// Bump CATCACHE_VERSION whenever the row fields change so an older cache is
// refetched instead of misread.

#ifndef XBOX_CATALOG_CACHE_H
#define XBOX_CATALOG_CACHE_H

#include <stddef.h>
#include <stdint.h>

#define CATCACHE_VERSION     1
#define CATCACHE_FP_MAX      96

#define XBOX_ROM_ID_MAX      128
#define XBOX_ROM_NAME_MAX    96
#define XBOX_ROM_FILE_MAX    96
// Hard ceiling on catalog rows kept in RAM (~330 bytes each, so ~6.6 MB at
// the limit); the list itself grows on demand.
#define XBOX_MAX_ROMS        20000

typedef struct {
    char     rom_id[XBOX_ROM_ID_MAX];
    char     name[XBOX_ROM_NAME_MAX];
    char     filename[XBOX_ROM_FILE_MAX];
    uint64_t size;
    int      is_bundle;
} XboxRomEntry;

// Heap-backed, grows on demand. Zero-initialise, release with rom_list_free.
typedef struct {
    int           count;
    int           cap;
    XboxRomEntry *roms;
} XboxRomList;

void rom_list_free(XboxRomList *list);
// Empty the list but keep the allocation.
void rom_list_clear(XboxRomList *list);
// Append a slot; returns NULL when out of memory or at XBOX_MAX_ROMS.
XboxRomEntry *rom_list_push(XboxRomList *list);
// Move ``src`` into ``dst`` (dst's old rows are freed, src ends up empty).
void rom_list_take(XboxRomList *dst, XboxRomList *src);

// Parse one page of GET /api/v1/roms into ``out`` (appended). Rows without
// rom_id or name are skipped. ``page_rows`` gets the number of objects in
// the page (including skipped ones), ``has_more`` the server's flag.
// Returns 0 on success, -1 on a malformed body, -2 when the list is full.
int catcache_parse_roms_page(const char *body, XboxRomList *out,
                             int *page_rows, int *has_more);

// Look ``system`` up in a GET /api/v1/roms/fingerprints body. Returns 1 and
// fills fp/count when found, 0 when the server does not list the system,
// -1 when the body is not a fingerprints response.
int catcache_find_fingerprint(const char *json, const char *system,
                              char *fp, int fp_len, int *count);

// Serialise ``list`` as a cache file. Returns a malloc'd buffer (caller
// frees) and its length, or NULL when out of memory.
char *catcache_serialize(const char *system, const char *fingerprint,
                         const XboxRomList *list, size_t *out_len);

// Parse a cache file into ``out`` (cleared first). Returns 0 when the file
// is complete, the right version and the right system; -1 otherwise (and
// ``out`` is left empty).
int catcache_parse(const char *buf, size_t len, const char *system,
                   char *fp, int fp_len, XboxRomList *out);

#endif // XBOX_CATALOG_CACHE_H
