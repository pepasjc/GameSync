#ifndef CATALOG_CACHE_H
#define CATALOG_CACHE_H

// The server's game list kept on the SD between runs, one file per system
// (<config dir>/cache/NDS.cat), stamped with the fingerprint the server
// published for that system (GET /api/v1/roms/fingerprints). On the next
// start only a system whose fingerprint moved is downloaded again; with no
// server the last copy is still browsable.
//
// A DS in DS mode has 4 MB of RAM, so the list is never held in memory: the
// file holds compact variable-length records followed by a table of their
// offsets. Only that table (4 bytes a game) is loaded; rows are read from
// the file a screenful at a time, and the RA filter / search walk the file.
//
// Plain C + stdio, no libnds: tests/test_catalog_cache.c runs it on a PC.
//
// File layout (little-endian):
//   header, CC_HEADER_SIZE bytes
//     0  "GSDC"        magic
//     4  u16 version   CC_VERSION, bumped when the record changes
//     6  u16 header size
//     8  u32 count     number of records
//    12  u32 index     file offset of the offset table
//    16  char[8]       system ("NDS")
//    24  char[64]      server fingerprint, NUL-terminated
//    88  8 reserved bytes
//   records, back to back, in the server's order
//     u32 size, s32 ra_game_id, s16 ra_achievements, u8 flags,
//     u8 rom_id length, u8 name length, u8 file name length, 2 reserved,
//     then the three strings without NULs
//   offset table: count x u32, bit 31 set when the game has an RA set

#include "catalog_data.h"
#include <stdio.h>

#define CC_VERSION 1
#define CC_HEADER_SIZE 96
#define CC_RECORD_HEAD 16
#define CC_MAX_COUNT 200000
#define CC_RA_BIT 0x80000000u
#define CC_OFFSET(ref) ((ref) & ~CC_RA_BIT)

typedef struct {
    FILE *f;
    char system[8];
    char fingerprint[CAT_FP_LEN];
    uint32_t count;
    uint32_t index_offset;
    uint32_t *refs;       // count entries: record offset | CC_RA_BIT
    char *iobuf;          // stdio buffer for f
} CatCache;

// Open a cache file: checks the header and loads the offset table.
// Returns false (and leaves *c closed) for a missing, older or damaged file.
bool cc_open(CatCache *c, const char *path);
void cc_close(CatCache *c);
static inline bool cc_is_open(const CatCache *c) { return c->f != NULL; }

// Header only: system, fingerprint and count of a valid file
bool cc_peek(const char *path, char system[8], char fingerprint[CAT_FP_LEN], uint32_t *count);

// Read one record (ref from c->refs or a filtered list)
bool cc_read(const CatCache *c, uint32_t ref, CatEntry *e);

// The refs of the records passing the filter, in file order, malloc'ed into
// *out (NULL when none match). `scratch` is a work entry. Returns the count,
// -1 on a read error or out of memory.
int cc_filter(const CatCache *c, bool ra_only, const char *search, uint32_t **out, CatEntry *scratch);

// Writing: records are appended as the pages arrive, the offset table and
// the header go in at the end, and the finished file replaces `final_path`
// in one rename, so an interrupted download leaves the old copy intact.
typedef struct {
    FILE *f;
    char part[160];
    char system[8];
    uint32_t count, cap, pos;
    uint32_t *refs;
    bool failed;
} CatCacheWriter;

bool ccw_begin(CatCacheWriter *w, const char *part_path, const char *system);
bool ccw_add(CatCacheWriter *w, const CatEntry *e);
bool ccw_finish(CatCacheWriter *w, const char *fingerprint, const char *final_path);
void ccw_abort(CatCacheWriter *w);

#endif
