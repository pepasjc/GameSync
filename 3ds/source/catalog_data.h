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

#endif
