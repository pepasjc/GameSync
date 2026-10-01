#ifndef CATALOG_DATA_H
#define CATALOG_DATA_H

// Game catalog data: server JSON parsing, paging window math, file names and
// the "already on the SD" name set. Plain C + stdio/dirent, no libnds, so it
// also builds and runs on a PC for testing.

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define CAT_ID_LEN 224     // rom_id (slug of the name; ~200 max seen)
#define CAT_NAME_LEN 176   // display name
#define CAT_FILE_LEN 200   // server file name, e.g. "Game (USA).zip"

typedef struct {
    char rom_id[CAT_ID_LEN];
    char name[CAT_NAME_LEN];
    char filename[CAT_FILE_LEN];
    uint32_t size;          // size of the file on the server (zip size for zips)
    int ra_game_id;         // 0 = unknown to RetroAchievements
    int ra_achievements;    // >0 = has a set; 0 none; -1 count unknown
    bool ra_title_only;     // matched by name only (shown as "RA?")
    bool can_extract_nds;   // server advertises ?extract=nds
    bool truncated;         // rom_id didn't fit: can't be downloaded
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

// Same, one entry at a time: each entry is parsed into `scratch` and handed
// to `fn` (return false to skip the rest). Returns the number of entries
// handed over, or -1 if the JSON is malformed. Holds no more than one entry,
// so a page of hundreds of rows needs no array.
typedef bool (*CatEntryFn)(const CatEntry *e, void *user);
int cat_parse_page_cb(const char *json, size_t len, CatEntry *scratch, CatEntryFn fn, void *user,
                      CatPageInfo *info);

// Parse /api/v1/roms/fingerprints: {"systems":{"NDS":{"fingerprint":"..",
// "count":N},...}}. Stores the systems that appear in `wanted`, in `wanted`
// order, with fingerprint and count. Returns how many, -1 on bad JSON
// (including a reply with no "systems" object).
#define CAT_FP_LEN 64
int cat_parse_fingerprints(const char *json, size_t len, const char *const *wanted,
                           char out[][8], char fps[][CAT_FP_LEN], int *counts, int max);

// Local search, like the server's: the text (ASCII case-insensitive) in the
// name or the file name. Empty/NULL matches everything.
bool cat_entry_matches(const CatEntry *e, const char *search);

// Parse /api/v1/roms/systems: {"systems":[...],"stats":{"NDS":123,...}}.
// Stores systems that appear in `wanted` (NULL-terminated), in `wanted`
// order, with their counts. Returns how many were found, -1 on bad JSON.
int cat_parse_systems(const char *json, size_t len, const char *const *wanted,
                      char out[][8], int *counts, int max);

// Percent-encode everything but unreserved characters
void cat_url_encode(const char *in, char *out, size_t size);

// Local file name for a catalog entry: file name stem with FAT-illegal
// characters replaced, plus ".nds".
void cat_target_name(const char *filename, char *out, size_t size);

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

// Add every .nds/.dsi file under `dir` (recursive to `max_depth`, "saves"
// folders and dot-files skipped). Returns the number of files added.
int cat_names_scan(CatNameSet *set, const char *dir, int max_depth);

#endif
