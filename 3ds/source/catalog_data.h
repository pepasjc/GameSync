#ifndef CATALOG_DATA_H
#define CATALOG_DATA_H

// Game catalog data: server JSON parsing, paging window math, file names,
// install planning, CIA header parsing and the "already installed" sets.
// Plain C + stdio/dirent, no libctru, so it also builds and runs on a PC for
// testing (3ds/tests/run_host_tests.sh).
//
// Adapted from the DS client's ds/source/catalog_data.c (same project); the
// JSON reader, paging window and name set are shared logic, the 3DS adds CIA
// install planning and the CIA title id reader.

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

#define CAT_ID_LEN 224     // rom_id (slug of the name; ~200 max seen)
#define CAT_NAME_LEN 176   // display name
#define CAT_FILE_LEN 200   // server file name, e.g. "Game (USA).zip"
#define CAT_TID_LEN 40     // server title_id (16-hex for some 3DS entries)

typedef struct {
    char rom_id[CAT_ID_LEN];
    char title_id[CAT_TID_LEN];
    char name[CAT_NAME_LEN];
    char filename[CAT_FILE_LEN];
    uint64_t size;          // size of the file on the server (zip size for zips)
    int ra_game_id;         // 0 = unknown to RetroAchievements
    int ra_achievements;    // >0 = has a set; 0 none; -1 count unknown
    bool ra_title_only;     // matched by name only (shown as "RA?")
    bool can_extract_nds;   // server advertises ?extract=nds
    bool can_extract_cia;   // server advertises ?extract=cia
    bool is_bundle;         // a folder on the server (served as a zip)
    bool truncated;         // rom_id/filename didn't fit: can't be downloaded
} CatEntry;

typedef struct {
    int total;       // filtered count on the server
    bool has_more;
    int has_ra;      // echo of the has_ra filter: 1 true, 0 false, -1 absent/null
} CatPageInfo;

// Has an achievement set worth a badge
static inline bool cat_entry_has_ra(const CatEntry *e) { return e->ra_achievements > 0; }

// Parse one /api/v1/roms response. Fills up to `max` entries.
// Returns the number of entries stored, or -1 if the JSON is malformed.
int cat_parse_page(const char *json, size_t len, CatEntry *out, int max, CatPageInfo *info);

// Parse /api/v1/roms/systems: {"systems":[...],"stats":{"NDS":123,...}}.
// Stores systems that appear in `wanted` (NULL-terminated), in `wanted`
// order, with their counts. Returns how many were found, -1 on bad JSON.
int cat_parse_systems(const char *json, size_t len, const char *const *wanted,
                      char out[][8], int *counts, int max);

// Percent-encode everything but unreserved characters
void cat_url_encode(const char *in, char *out, size_t size);

// Local file name for a catalog entry: file name stem (with a ".3ds" /
// ".cci" left by "Game.3ds.zip" removed too) with FAT-illegal characters
// replaced, plus `ext` (e.g. ".nds").
void cat_target_name(const char *filename, const char *ext, char *out, size_t size);

// First entry of a `window`-sized fetch that shows rows [first, first+rows),
// clamped to [0, total-window]. direction > 0 (scrolling down) puts most of
// the window below the rows, < 0 above them, 0 centres it.
int cat_window_start(int first, int rows, int window, int total, int direction);

// True if the loaded window [win_off, win_off+win_count) holds every
// existing row of [first, first+rows) (rows past `total` don't count).
bool cat_window_covers(int win_off, int win_count, int first, int rows, int total);

// Human-readable size: "812 KB", "64.2 MB", "1.25 GB"
void cat_format_size(uint64_t bytes, char *out, size_t size);

// Copy for the text console: non-ASCII UTF-8 sequences become '?'
void cat_ascii(const char *in, char *out, size_t size);

// ---------------------------------------------------------------------------
// Install planning
// ---------------------------------------------------------------------------

typedef enum {
    CAT_PLAN_NONE = 0,       // can't be installed from the 3DS
    CAT_PLAN_CIA,            // .cia on the server: stream into AM as-is
    CAT_PLAN_CIA_CONVERT,    // .3ds/.cci(.zip): server converts (?extract=cia)
    CAT_PLAN_3DS_FILE,       // .3ds/.cci, no server conversion: save to the SD
    CAT_PLAN_NDS,            // .nds/.dsi on the server: save to the SD as-is
    CAT_PLAN_NDS_EXTRACT,    // zipped DS ROM: server unzips (?extract=nds)
} CatPlanKind;

typedef struct {
    CatPlanKind kind;
    const char *query;       // "" or "?extract=..." to append to the URL
    // A raw .3ds/.cci on the server can also be saved to the SD as a file
    // (for GodMode9 to build a CIA from) when the server can't convert.
    bool file_fallback;
    const char *reason;      // why CAT_PLAN_NONE
} CatPlan;

// How (and whether) `e` of catalog system `system` installs on a 3DS
CatPlan cat_plan_install(const char *system, const CatEntry *e);

// ---------------------------------------------------------------------------
// CIA header
// ---------------------------------------------------------------------------

// Read the title id out of the start of a CIA stream (header, cert chain,
// ticket, then the TMD that carries the id). Layout per 3dbrew's CIA and
// Title metadata pages. Returns 1 with *title_id set, 0 if more bytes are
// needed (the TMD isn't in `len` yet), -1 if this isn't a CIA.
int cat_cia_title_id(const uint8_t *data, size_t len, uint64_t *title_id);

// Bytes of the CIA stream needed for cat_cia_title_id() to answer
#define CAT_CIA_PROBE_MAX (64 * 1024)

// ---------------------------------------------------------------------------
// rom_id -> installed title id (what a catalog CIA install put in AM)
// ---------------------------------------------------------------------------

typedef struct {
    char (*rom_ids)[CAT_ID_LEN];
    uint64_t *tids;
    int count, cap;
} CatCiaMap;

// Load "<16 hex title id> <rom_id>" lines; a missing file is an empty map.
void cat_cia_map_load(CatCiaMap *map, const char *path);
bool cat_cia_map_save(const CatCiaMap *map, const char *path);
// 0 when not present
uint64_t cat_cia_map_get(const CatCiaMap *map, const char *rom_id);
void cat_cia_map_set(CatCiaMap *map, const char *rom_id, uint64_t tid);
void cat_cia_map_free(CatCiaMap *map);

// Parse a 16-hex-digit title id ("0004000000055D00"); false otherwise
bool cat_parse_tid(const char *s, uint64_t *out);

// ---------------------------------------------------------------------------
// Set of ROM file names on the SD (case-insensitive, extension ignored)
// ---------------------------------------------------------------------------

typedef struct {
    char **names;   // lower-case stems, sorted after cat_names_sort()
    int count;
    int cap;
    bool sorted;
} CatNameSet;

void cat_names_add(CatNameSet *set, const char *file_name);
void cat_names_sort(CatNameSet *set);
bool cat_names_contains(const CatNameSet *set, const char *file_name);
void cat_names_free(CatNameSet *set);

// Add every file under `dir` whose extension is in `exts` (NULL-terminated,
// e.g. {".nds", ".dsi", NULL}), recursive to `max_depth`, "saves" folders
// and dot-files skipped. Returns the number of files added.
int cat_names_scan(CatNameSet *set, const char *dir, int max_depth, const char *const *exts);

// ---------------------------------------------------------------------------
// Catalog cache: one file per system on the SD card, kept with the server's
// fingerprint for that system (GET /api/v1/roms/fingerprints). Same strategy
// as the MiSTer client (mister/gamesync/catalogcache.py): only systems whose
// fingerprint moved are fetched again, and a server that can't be reached
// leaves the last copy usable.
//
// File format (UTF-8 text, one row per line, fields tab-separated, with
// '\\', tab, CR and LF escaped as \\ \t \r \n):
//   GSCAT <CAT_CACHE_VERSION>\t<fingerprint>
//   <flags hex>\t<size>\t<ra_game_id>\t<ra_achievements>\t<rom_id>\t<title_id>\t<filename>\t<name>
//   ...
//   END\t<row count>
// A file without the END line (interrupted write) or with another version
// is ignored. Rows keep the server's order.
// ---------------------------------------------------------------------------

// Bump when the stored row fields change, so an old cache is refetched
#define CAT_CACHE_VERSION 1
#define CAT_FP_LEN 72         // fingerprint (a 40-hex sha1 today)

enum {
    CAT_ROW_RA_TITLE_ONLY = 1 << 0,
    CAT_ROW_EXTRACT_NDS = 1 << 1,
    CAT_ROW_EXTRACT_CIA = 1 << 2,
    CAT_ROW_BUNDLE = 1 << 3,
    CAT_ROW_TRUNCATED = 1 << 4,
};

// One cached row; the strings point into the list's text buffer
typedef struct {
    const char *rom_id, *title_id, *filename, *name;
    uint64_t size;
    int32_t ra_game_id, ra_achievements;
    uint8_t flags;
} CatRow;

typedef struct {
    char *text;              // the file contents, unescaped in place
    CatRow *rows;
    int count;
    char fingerprint[CAT_FP_LEN];
} CatList;

// Load a cache file. False (and an empty list) if it is missing, from
// another cache version, cut short or malformed.
bool cat_list_load(CatList *list, const char *path);
void cat_list_free(CatList *list);
// Fingerprint stored in a cache file's header, without loading the rows.
// False if there is no usable header.
bool cat_cache_fingerprint(const char *path, char *out, size_t size);

void cat_row_to_entry(const CatRow *row, CatEntry *out);

// Rows matching the filters, as indices into list->rows (in list order).
// `search` is a case-insensitive substring of the name or the file name
// (the server's ?search= rule); ra_only keeps rows with an achievement set
// (the server's ?has_ra=true rule). `out` holds list->count ints.
int cat_list_filter(const CatList *list, const char *search, bool ra_only, int *out);

// Case-insensitive (ASCII) substring test; an empty needle matches
bool cat_icontains(const char *haystack, const char *needle);

// Writes <path>.part and renames it over <path> when finished
typedef struct {
    FILE *f;
    char path[256];
    int count;
    bool failed;
} CatCacheWriter;

bool cat_cache_begin(CatCacheWriter *w, const char *path, const char *fingerprint);
void cat_cache_add(CatCacheWriter *w, const CatEntry *e);
// Finish: true if the file is complete and in place
bool cat_cache_end(CatCacheWriter *w);
// Throw the partial file away
void cat_cache_abort(CatCacheWriter *w);

// Parse /api/v1/roms/fingerprints: {"systems": {"NDS": {"fingerprint":
// "...", "count": N}, ...}}. Stores the systems in `wanted` (NULL-terminated)
// that the server lists, in `wanted` order. Returns how many, -1 on bad JSON.
int cat_parse_fingerprints(const char *json, size_t len, const char *const *wanted,
                           char systems[][8], char fingerprints[][CAT_FP_LEN], int *counts, int max);

// Parse the /api/v1/roms/scan reply ({"status": "ok", "count": N}): the
// ROM count, or -1 if it isn't one
int cat_parse_scan_count(const char *json, size_t len);

#endif
