#include "ra.h"
#include "ra_hash.h"
#include "ra_sets.h"
#include "http.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <time.h>
#include <dirent.h>
#include <sys/stat.h>

// ROM scan limits
#define RA_MAX_ROMS 1000
#define RA_MAX_DEPTH 4
#define RA_PATH_MAX 512

// Unlock ring in ramDump.bin (nds-bootstrap-ra retail/common/include/ra_engine.h)
#define RA_DUMP_UNLOCK_OFFSET 0x01FE0000
#define RA_UNLOCK_RECORDS 1024
#define RA_UNLOCK_MAGIC 0x31554152  // 'RAU1'

// Most of unlocks.log read per upload; the rest goes next time
#define RA_LOG_CHUNK (256 * 1024)

typedef struct {
    uint32_t magic;
    uint32_t seq;
    uint32_t achievement_id;
    uint32_t game_id;
    uint32_t points;
    uint32_t frame;
    uint8_t rtc[8];     // year-2000, month, day, weekday, hour, minute, second, 0
    char md5[32];
} RaUnlockRecord;

_Static_assert(sizeof(RaUnlockRecord) == 64, "RaUnlockRecord must be 64 bytes");

// ---------------------------------------------------------------------------
// Paths
// ---------------------------------------------------------------------------

static char ra_root[8] = "";

// SD root holding TWiLight Menu++ / nds-bootstrap (_nds). nds-bootstrap only
// runs RetroAchievements from the DSi SD, so sd: wins when both exist.
static const char *ra_get_root(void) {
    if (ra_root[0]) return ra_root;

    static const char *roots[] = { "sd:", "fat:", NULL };
    struct stat st;
    char path[32];
    for (int i = 0; roots[i]; i++) {
        snprintf(path, sizeof(path), "%s/_nds", roots[i]);
        if (stat(path, &st) == 0 && S_ISDIR(st.st_mode)) {
            strcpy(ra_root, roots[i]);
            return ra_root;
        }
    }
    strcpy(ra_root, "sd:");
    return ra_root;
}

static void ra_path(char *out, size_t size, const char *rel) {
    snprintf(out, size, "%s/_nds/%s", ra_get_root(), rel);
}

static void ra_ensure_dirs(void) {
    char path[64];
    ra_path(path, sizeof(path), "ra");
    mkdir(path, 0777);
    ra_path(path, sizeof(path), "ra/sets");
    mkdir(path, 0777);
}

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

// RA Sync (rasync.nds) runs the same screens unattended
static bool ra_auto = false;

static void wait_any_button(void) {
    if (ra_auto) {
        for (int i = 0; i < 60 && pmMainLoop(); i++) swiWaitForVBlank();
        return;
    }
    iprintf("\nPress any button\n");
    while (pmMainLoop()) {
        swiWaitForVBlank();
        scanKeys();
        if (keysDown()) break;
    }
}

static bool cancel_requested(void) {
    scanKeys();
    return (keysHeld() & KEY_B) != 0;
}

static const char *base_name(const char *path) {
    const char *slash = strrchr(path, '/');
    return slash ? slash + 1 : path;
}

static bool is_md5_hex(const char *s) {
    for (int i = 0; i < 32; i++) {
        char c = s[i];
        if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F')))
            return false;
    }
    return true;
}

// Days since 1970-01-01 for a proleptic Gregorian date
static long days_from_civil(int y, int m, int d) {
    y -= m <= 2;
    long era = (y >= 0 ? y : y - 399) / 400;
    long yoe = y - era * 400;
    long doy = (153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + d - 1;
    long doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    return era * 146097 + doe - 719468;
}

// Explain an HTTP failure shared by both actions. Returns true if the whole
// run should stop (server unreachable, bad key, no RA login).
// After ~100 back-to-back requests the DS network stack stops opening
// connections for a while (closed ones linger), so a request that gets no
// response at all is retried after a growing pause before giving up.
static HttpResponse ra_http(const char *url, HttpMethod method, const char *api_key,
                            const char *content_type, const uint8_t *body, size_t size) {
    HttpResponse resp = http_request_ex(url, method, api_key, content_type, body, size);
    for (int attempt = 1; resp.status_code == 0 && attempt <= 4; attempt++) {
        http_response_free(&resp);
        iprintf("  no response, retrying (%d)\n", attempt);
        for (int frame = 0; frame < 60 * 2 * attempt; frame++) swiWaitForVBlank();
        resp = http_request_ex(url, method, api_key, content_type, body, size);
    }
    return resp;
}

static bool report_http_failure(const HttpResponse *resp) {
    switch (resp->status_code) {
        case 0:
            iprintf(CON_RED "No response from server" CON_RESET "\n");
            iprintf("Check WiFi and server_url\n");
            return true;
        case 401:
        case 403:
            iprintf(CON_RED "API key rejected (HTTP %d)" CON_RESET "\n", resp->status_code);
            return true;
        case 503:
            iprintf(CON_RED "Server has no RA login" CON_RESET "\n");
            iprintf("Set SYNC_RA_USERNAME and run\n");
            iprintf("ra_login.py on the server\n");
            return true;
        default:
            iprintf("  HTTP %d\n", resp->status_code);
            return false;
    }
}

// ---------------------------------------------------------------------------
// ROM scan
// ---------------------------------------------------------------------------

typedef struct {
    char **paths;
    int count;
    int cap;
    bool truncated;
} RomList;

static void rom_list_add(RomList *list, const char *path) {
    if (list->count >= RA_MAX_ROMS) {
        list->truncated = true;
        return;
    }
    if (list->count == list->cap) {
        int cap = list->cap ? list->cap * 2 : 64;
        char **grown = realloc(list->paths, cap * sizeof(char *));
        if (!grown) {
            list->truncated = true;
            return;
        }
        list->paths = grown;
        list->cap = cap;
    }
    char *copy = strdup(path);
    if (!copy) {
        list->truncated = true;
        return;
    }
    list->paths[list->count++] = copy;
}

static void rom_list_free(RomList *list) {
    for (int i = 0; i < list->count; i++) free(list->paths[i]);
    free(list->paths);
    memset(list, 0, sizeof(*list));
}

static int rom_path_compare(const void *a, const void *b) {
    return strcasecmp(*(char * const *)a, *(char * const *)b);
}

// Collect .nds files under dir. Hidden entries (".", "..", macOS "._x.nds")
// and "saves" folders are skipped.
static void collect_roms(RomList *list, const char *dir, int depth) {
    DIR *d = opendir(dir);
    if (!d) return;

    struct dirent *ent;
    char path[RA_PATH_MAX];
    while ((ent = readdir(d)) != NULL && !list->truncated) {
        if (ent->d_name[0] == '.') continue;
        snprintf(path, sizeof(path), "%s/%s", dir, ent->d_name);

        bool is_dir = (ent->d_type == DT_DIR);
        if (ent->d_type == DT_UNKNOWN) {
            struct stat st;
            is_dir = (stat(path, &st) == 0 && S_ISDIR(st.st_mode));
        }

        if (is_dir) {
            if (depth < RA_MAX_DEPTH && strcasecmp(ent->d_name, "saves") != 0)
                collect_roms(list, path, depth + 1);
            continue;
        }

        const char *ext = strrchr(ent->d_name, '.');
        if (ext && strcasecmp(ext, ".nds") == 0)
            rom_list_add(list, path);
    }
    closedir(d);
}

// Same ROM folder the save scan uses for nds-bootstrap (<root>/roms/nds),
// falling back to <root>/roms.
static void find_roms(RomList *list, char *dir_out, size_t dir_size) {
    struct stat st;
    snprintf(dir_out, dir_size, "%s/roms/nds", ra_get_root());
    if (stat(dir_out, &st) != 0 || !S_ISDIR(st.st_mode))
        snprintf(dir_out, dir_size, "%s/roms", ra_get_root());

    collect_roms(list, dir_out, 0);
    if (list->count > 1)
        qsort(list->paths, list->count, sizeof(char *), rom_path_compare);
}

// ---------------------------------------------------------------------------
// Hash cache: "<md5>\t<size>\t<file name>" per line
// ---------------------------------------------------------------------------

typedef struct {
    char *name;
    uint32_t size;
    char md5[33];
} HashEntry;

typedef struct {
    HashEntry *entries;
    int count;
    int cap;
} HashCache;

static bool hash_cache_add(HashCache *cache, const char *name, uint32_t size, const char *md5) {
    if (cache->count == cache->cap) {
        int cap = cache->cap ? cache->cap * 2 : 64;
        HashEntry *grown = realloc(cache->entries, cap * sizeof(HashEntry));
        if (!grown) return false;
        cache->entries = grown;
        cache->cap = cap;
    }
    HashEntry *e = &cache->entries[cache->count];
    e->name = strdup(name);
    if (!e->name) return false;
    e->size = size;
    memcpy(e->md5, md5, 32);
    e->md5[32] = '\0';
    cache->count++;
    return true;
}

static void hash_cache_free(HashCache *cache) {
    for (int i = 0; i < cache->count; i++) free(cache->entries[i].name);
    free(cache->entries);
    memset(cache, 0, sizeof(*cache));
}

static void hash_cache_load(HashCache *cache) {
    char path[64];
    ra_path(path, sizeof(path), "ra/hashes.txt");
    FILE *f = fopen(path, "r");
    if (!f) return;

    char line[RA_PATH_MAX];
    while (fgets(line, sizeof(line), f)) {
        line[strcspn(line, "\r\n")] = '\0';
        char *tab1 = strchr(line, '\t');
        char *tab2 = tab1 ? strchr(tab1 + 1, '\t') : NULL;
        if (!tab2 || tab1 - line != 32 || !is_md5_hex(line) || !tab2[1]) continue;
        *tab1 = '\0';
        hash_cache_add(cache, tab2 + 1, (uint32_t)strtoul(tab1 + 1, NULL, 10), line);
    }
    fclose(f);
}

static void hash_cache_save(const HashCache *cache) {
    char path[64];
    ra_path(path, sizeof(path), "ra/hashes.txt");
    FILE *f = fopen(path, "w");
    if (!f) return;
    for (int i = 0; i < cache->count; i++) {
        const HashEntry *e = &cache->entries[i];
        fprintf(f, "%s\t%lu\t%s\n", e->md5, (unsigned long)e->size, e->name);
    }
    fclose(f);
}

static const char *hash_cache_find(const HashCache *cache, const char *name, uint32_t size) {
    for (int i = 0; i < cache->count; i++) {
        const HashEntry *e = &cache->entries[i];
        if (e->size == size && strcmp(e->name, name) == 0) return e->md5;
    }
    return NULL;
}

// ---------------------------------------------------------------------------
// Update achievement sets
// ---------------------------------------------------------------------------

// Sets per POST /ra/sets. Each connection costs the DSi stack local ports
// it runs out of after a few dozen, so ask for many sets at once; the
// response is held in RAM, so fewer in DS mode (4 MB).
#define RA_BATCH_DSI 32
#define RA_BATCH_DS 12
// A batch of sets RA hasn't sent the server yet can take a while
#define RA_BATCH_TIMEOUT 120

static int count_achievements(const char *set, size_t len) {
    static const char tag[] = "\nach\t";
    int n = 0;
    for (size_t i = 0; i + sizeof(tag) - 1 <= len; i++) {
        if (memcmp(set + i, tag, sizeof(tag) - 1) == 0) n++;
    }
    return n;
}

static bool write_set(const char *rom_name, const uint8_t *data, size_t size) {
    char path[RA_PATH_MAX];
    snprintf(path, sizeof(path), "%s/_nds/ra/sets/%s.txt", ra_get_root(), rom_name);
    FILE *f = fopen(path, "wb");
    if (!f) return false;
    bool ok = fwrite(data, 1, size, f) == size;
    if (fclose(f) != 0) ok = false;
    return ok;
}

// ROMs and their hashes ("" = couldn't hash), and what the batches did
typedef struct {
    char **paths;
    char (*md5s)[33];
    int count;
    const char (*batch)[33];
    int batch_count;
    bool *seen;             // per batch entry
    int with_set, unknown, errors;
    int last_achievements;  // of the last set written
    bool verbose;           // a line per ROM
} SetRun;

static void set_run_item(RaBatchKind kind, const char *md5, const char *data, size_t len, void *user) {
    SetRun *run = user;
    for (int b = 0; b < run->batch_count; b++) {
        if (strcmp(run->batch[b], md5) == 0) run->seen[b] = true;
    }
    bool valid = (kind != RA_BATCH_SET) || (len > 6 && memcmp(data, "RASET\t", 6) == 0);
    int achievements = (kind == RA_BATCH_SET && valid) ? count_achievements(data, len) : 0;

    for (int i = 0; i < run->count; i++) {
        if (strcmp(run->md5s[i], md5) != 0) continue;
        const char *name = base_name(run->paths[i]);
        if (kind == RA_BATCH_UNKNOWN) {
            run->unknown++;
        } else if (kind == RA_BATCH_ERROR) {
            run->errors++;
            if (run->verbose) iprintf("%.24s\n  " CON_RED "%.28s" CON_RESET "\n", name, data);
        } else if (!valid) {
            run->errors++;
            if (run->verbose) iprintf("%.24s\n  " CON_RED "bad set file" CON_RESET "\n", name);
        } else if (write_set(name, (const uint8_t *)data, len)) {
            run->with_set++;
            run->last_achievements = achievements;
            if (run->verbose) iprintf("%.24s\n  " CON_GREEN "%d achievements" CON_RESET "\n", name, achievements);
        } else {
            run->errors++;
            if (run->verbose) iprintf("%.24s\n  " CON_RED "SD write failed" CON_RESET "\n", name);
        }
    }
}

// Mark every ROM with this hash as failed
static void set_run_fail(SetRun *run, const char *md5) {
    for (int i = 0; i < run->count; i++) {
        if (strcmp(run->md5s[i], md5) == 0) run->errors++;
    }
}

// Fetch the sets for uniq[0..n) and write them for every matching ROM.
// Returns false if the whole run should stop.
static bool fetch_set_batch(SyncState *state, SetRun *run, const char (*uniq)[33], int n) {
    run->batch = uniq;
    run->batch_count = n;
    bool seen[RA_BATCH_DSI];
    memset(seen, 0, sizeof(seen));
    run->seen = seen;

    char json[RA_BATCH_DSI * 35 + 32];
    size_t len = ra_sets_request(json, sizeof(json), uniq, n);
    char url[320];
    snprintf(url, sizeof(url), "%s/api/v1/ra/sets", state->server_url);

    http_set_timeout(RA_BATCH_TIMEOUT);
    HttpResponse resp = ra_http(url, HTTP_POST, state->api_key, "application/json",
                                (const uint8_t *)json, len);
    http_set_timeout(0);

    bool go_on = true;
    if (resp.status_code == 200 && resp.body) {
        if (!ra_sets_parse((const char *)resp.body, resp.body_size, set_run_item, run))
            iprintf(CON_RED "Incomplete reply from server" CON_RESET "\n");
    } else if (resp.status_code == 404 || resp.status_code == 405) {
        iprintf(CON_RED "Server too old: update it" CON_RESET "\n");
        iprintf("(no POST /api/v1/ra/sets)\n");
        go_on = false;
    } else if (report_http_failure(&resp)) {
        go_on = false;
    }
    http_response_free(&resp);

    for (int b = 0; b < n; b++) {
        if (!seen[b]) set_run_fail(run, uniq[b]);
    }
    run->batch = NULL;
    run->batch_count = 0;
    run->seen = NULL;
    return go_on;
}

// Hash with the cache: cached by file name + size, else computed (and added
// to `fresh`). Returns false if the file isn't a readable DS ROM.
static bool hash_rom(const char *path, const HashCache *cache, HashCache *fresh, char md5[33]) {
    struct stat st;
    if (stat(path, &st) != 0) return false;
    const char *name = base_name(path);
    uint32_t size = (uint32_t)st.st_size;
    const char *cached = hash_cache_find(cache, name, size);
    if (cached) {
        strcpy(md5, cached);
    } else if (!ra_hash_nds_file(path, md5)) {
        return false;
    }
    if (fresh) hash_cache_add(fresh, name, size, md5);
    return true;
}

// Keep hashes of ROMs not seen this time, then write the cache
static void hash_cache_merge_save(HashCache *fresh, const HashCache *old) {
    for (int i = 0; i < old->count; i++) {
        const HashEntry *e = &old->entries[i];
        if (!hash_cache_find(fresh, e->name, e->size))
            hash_cache_add(fresh, e->name, e->size, e->md5);
    }
    hash_cache_save(fresh);
}

static void ra_update_sets(SyncState *state) {
    consoleClear();
    iprintf("=== Update Achievement Sets ===\n\n");

    char rom_dir[64];
    RomList roms = {0};
    iprintf("Scanning ROMs...\n");
    find_roms(&roms, rom_dir, sizeof(rom_dir));
    if (ra_auto) {
        // Unattended: only ROMs never hashed before (every other one is in
        // the cache, so RetroAchievements was already asked about it)
        HashCache seen = {0};
        hash_cache_load(&seen);
        int kept = 0;
        for (int i = 0; i < roms.count; i++) {
            struct stat st;
            bool known = stat(roms.paths[i], &st) == 0
                      && hash_cache_find(&seen, base_name(roms.paths[i]), (uint32_t)st.st_size);
            if (known) free(roms.paths[i]);
            else roms.paths[kept++] = roms.paths[i];
        }
        roms.count = kept;
        hash_cache_free(&seen);
        if (roms.count == 0) {
            rom_list_free(&roms);
            return;
        }
    }
    iprintf("%s: %d ROMs%s\n", rom_dir, roms.count, roms.truncated ? " (limit)" : "");
    if (roms.count == 0) {
        iprintf("\nNo .nds files found\n");
        rom_list_free(&roms);
        wait_any_button();
        return;
    }
    iprintf("Hold B to stop\n\n");

    ra_ensure_dirs();

    char (*md5s)[33] = calloc(roms.count, sizeof(*md5s));
    char (*uniq)[33] = calloc(roms.count, sizeof(*uniq));
    if (!md5s || !uniq) {
        free(md5s);
        free(uniq);
        rom_list_free(&roms);
        iprintf(CON_RED "Out of memory" CON_RESET "\n");
        wait_any_button();
        return;
    }

    // 1. Hash every ROM (no network)
    HashCache old_cache = {0}, new_cache = {0};
    hash_cache_load(&old_cache);
    int hash_failed = 0, hashed = 0, nuniq = 0;
    bool stopped = false, aborted = false;
    for (int i = 0; i < roms.count; i++) {
        if (!pmMainLoop() || cancel_requested()) {
            stopped = true;
            break;
        }
        iprintf("\rHashing %d/%d", i + 1, roms.count);
        if (!hash_rom(roms.paths[i], &old_cache, &new_cache, md5s[i])) {
            iprintf("\n%.24s\n  " CON_RED "not a DS ROM" CON_RESET "\n", base_name(roms.paths[i]));
            md5s[i][0] = '\0';
            hash_failed++;
            continue;
        }
        hashed++;
        bool dup = false;
        for (int u = 0; u < nuniq && !dup; u++) dup = (strcmp(uniq[u], md5s[i]) == 0);
        if (!dup) strcpy(uniq[nuniq++], md5s[i]);
    }
    iprintf("\n");
    hash_cache_merge_save(&new_cache, &old_cache);
    hash_cache_free(&old_cache);
    hash_cache_free(&new_cache);

    // 2. Ask for the sets, a batch of hashes per request
    SetRun run = { .paths = roms.paths, .md5s = md5s, .count = roms.count, .verbose = true };
    int batch = isDSiMode() ? RA_BATCH_DSI : RA_BATCH_DS;
    int requested = 0;
    if (!stopped && nuniq > 0) {
        iprintf("%d games, %d request%s\n", nuniq, (nuniq + batch - 1) / batch,
                (nuniq + batch - 1) / batch == 1 ? "" : "s");
        http_set_verbose(0);
        for (int b = 0; b < nuniq; b += batch) {
            if (!pmMainLoop() || cancel_requested()) {
                stopped = true;
                break;
            }
            int n = nuniq - b < batch ? nuniq - b : batch;
            iprintf("Getting sets %d-%d...\n", b + 1, b + n);
            requested += n;
            if (!fetch_set_batch(state, &run, (const char (*)[33])uniq + b, n)) {
                aborted = true;
                break;
            }
        }
        http_set_verbose(1);
    }

    int total = roms.count;
    free(md5s);
    free(uniq);
    rom_list_free(&roms);

    iprintf("\n");
    if (aborted) iprintf(CON_RED "Stopped on error" CON_RESET "\n");
    else if (stopped) iprintf("Stopped\n");
    iprintf("Hashed %d of %d ROMs\n", hashed, total);
    iprintf(" With achievements: %d\n", run.with_set);
    iprintf(" Not on RA:         %d\n", run.unknown);
    if (hash_failed) iprintf(" Unreadable:        %d\n", hash_failed);
    if (run.errors) iprintf(" Errors:            %d\n", run.errors);
    if (requested < nuniq && !stopped && !aborted) iprintf(" Not asked:         %d\n", nuniq - requested);
    if (run.with_set) iprintf("Sets in %s/_nds/ra/sets\n", ra_get_root());
    wait_any_button();
}

// ---------------------------------------------------------------------------
// Used by the game catalog
// ---------------------------------------------------------------------------

const char *ra_sd_root(void) {
    return ra_get_root();
}

int ra_install_set(SyncState *state, const char *rom_path, int *achievements) {
    if (achievements) *achievements = 0;
    ra_ensure_dirs();

    HashCache old_cache = {0}, new_cache = {0};
    hash_cache_load(&old_cache);
    char md5s[1][33];
    bool ok = hash_rom(rom_path, &old_cache, &new_cache, md5s[0]);
    if (ok) {
        hash_cache_merge_save(&new_cache, &old_cache);
    }
    hash_cache_free(&old_cache);
    hash_cache_free(&new_cache);
    if (!ok) return RA_SET_NOT_DS_ROM;

    char *paths[1] = { (char *)rom_path };
    SetRun run = { .paths = paths, .md5s = md5s, .count = 1, .verbose = false };
    http_set_verbose(0);
    bool reachable = fetch_set_batch(state, &run, (const char (*)[33])md5s, 1);
    http_set_verbose(1);

    if (run.with_set) {
        if (achievements) *achievements = run.last_achievements;
        return RA_SET_OK;
    }
    if (run.unknown) return RA_SET_UNKNOWN;
    return reachable ? RA_SET_ERROR : RA_SET_NO_SERVER;
}

// ---------------------------------------------------------------------------
// Unlock ring -> unlocks.log (same as nds-bootstrap-ra's flushUnlocks)
// ---------------------------------------------------------------------------

static int record_seq_compare(const void *a, const void *b) {
    uint32_t sa = ((const RaUnlockRecord *)a)->seq;
    uint32_t sb = ((const RaUnlockRecord *)b)->seq;
    return (sa > sb) - (sa < sb);
}

// Valid records in the ring, or -1 if ramDump.bin can't be read.
// With move=true they are appended to unlocks.log and the ring is zeroed.
static int ring_unlocks(bool move) {
    char dump_path[64];
    ra_path(dump_path, sizeof(dump_path), "nds-bootstrap/ramDump.bin");
    FILE *dump = fopen(dump_path, move ? "r+b" : "rb");
    if (!dump) return -1;

    const size_t ring_size = RA_UNLOCK_RECORDS * sizeof(RaUnlockRecord);
    RaUnlockRecord *records = malloc(ring_size);
    if (!records) {
        fclose(dump);
        return -1;
    }

    size_t got = 0;
    if (fseek(dump, RA_DUMP_UNLOCK_OFFSET, SEEK_SET) == 0)
        got = fread(records, sizeof(RaUnlockRecord), RA_UNLOCK_RECORDS, dump);

    int valid = 0;
    for (size_t i = 0; i < got; i++) {
        if (records[i].magic == RA_UNLOCK_MAGIC) records[valid++] = records[i];
    }

    if (move && valid > 0) {
        qsort(records, valid, sizeof(RaUnlockRecord), record_seq_compare);

        ra_ensure_dirs();
        char log_path[64];
        ra_path(log_path, sizeof(log_path), "ra/unlocks.log");
        FILE *log = fopen(log_path, "ab");
        if (!log) {
            // Keep the ring for next time
            free(records);
            fclose(dump);
            return -1;
        }
        for (int i = 0; i < valid; i++) {
            const RaUnlockRecord *r = &records[i];
            char md5[33];
            memcpy(md5, r->md5, 32);
            md5[32] = '\0';
            // achievement, game, md5, points, local time, frame
            fprintf(log, "%lu\t%lu\t%s\t%lu\t20%02lu-%02lu-%02lu %02lu:%02lu:%02lu\t%lu\n",
                (unsigned long)r->achievement_id, (unsigned long)r->game_id, md5,
                (unsigned long)r->points,
                (unsigned long)r->rtc[0], (unsigned long)r->rtc[1], (unsigned long)r->rtc[2],
                (unsigned long)r->rtc[4], (unsigned long)r->rtc[5], (unsigned long)r->rtc[6],
                (unsigned long)r->frame);
        }
        fclose(log);

        memset(records, 0, ring_size);
        fseek(dump, RA_DUMP_UNLOCK_OFFSET, SEEK_SET);
        fwrite(records, 1, ring_size, dump);
    }

    free(records);
    fclose(dump);
    return valid;
}

// ---------------------------------------------------------------------------
// uploaded.txt: "<byte offset>\n<first line of unlocks.log>\n"
// The first line detects a replaced/cleared log (then it starts over; the
// server skips repeats).
// ---------------------------------------------------------------------------

static void read_first_line(FILE *f, char *out, size_t size) {
    out[0] = '\0';
    fseek(f, 0, SEEK_SET);
    if (fgets(out, size, f)) out[strcspn(out, "\r\n")] = '\0';
}

static long load_uploaded_offset(FILE *log, long log_size) {
    char path[64];
    ra_path(path, sizeof(path), "ra/uploaded.txt");
    FILE *f = fopen(path, "r");
    if (!f) return 0;

    char line[160], first[160], log_first[160];
    long offset = 0;
    if (fgets(line, sizeof(line), f)) offset = strtol(line, NULL, 10);
    if (!fgets(first, sizeof(first), f)) first[0] = '\0';
    fclose(f);
    first[strcspn(first, "\r\n")] = '\0';

    read_first_line(log, log_first, sizeof(log_first));
    if (offset < 0 || offset > log_size || strcmp(first, log_first) != 0) return 0;
    return offset;
}

static void save_uploaded_offset(FILE *log, long offset) {
    char path[64], first[160];
    read_first_line(log, first, sizeof(first));
    ra_path(path, sizeof(path), "ra/uploaded.txt");
    FILE *f = fopen(path, "w");
    if (!f) return;
    fprintf(f, "%ld\n%s\n", offset, first);
    fclose(f);
}

// Lines in unlocks.log not uploaded yet, or -1 if there is no log
static int pending_log_lines(void) {
    char path[64];
    ra_path(path, sizeof(path), "ra/unlocks.log");
    FILE *log = fopen(path, "rb");
    if (!log) return -1;
    fseek(log, 0, SEEK_END);
    long size = ftell(log);
    long offset = load_uploaded_offset(log, size);
    fseek(log, offset, SEEK_SET);
    int lines = 0, c;
    while ((c = fgetc(log)) != EOF) {
        if (c == '\n') lines++;
    }
    fclose(log);
    return lines;
}

// ---------------------------------------------------------------------------
// Upload unlocks
// ---------------------------------------------------------------------------

typedef struct {
    uint32_t id;
    uint32_t game_id;
    char md5[33];
    long ago;
    bool sent;
} LogUnlock;

typedef struct {
    int submitted, already, duplicate, pending, ignored, error, other;
    char detail[64];  // first error detail
} UploadTally;

// Parse "<id>\t<game>\t<md5>\t<points>\tYYYY-MM-DD HH:MM:SS\t<frame>"
static bool parse_log_line(char *line, time_t now, LogUnlock *out) {
    char *fields[6];
    int n = 0;
    char *p = line;
    while (n < 6) {
        fields[n++] = p;
        p = strchr(p, '\t');
        if (!p) break;
        *p++ = '\0';
    }
    if (n < 5 || strlen(fields[2]) != 32 || !is_md5_hex(fields[2])) return false;

    char *end;
    unsigned long id = strtoul(fields[0], &end, 10);
    if (end == fields[0] || id == 0) return false;

    out->id = (uint32_t)id;
    out->game_id = (uint32_t)strtoul(fields[1], NULL, 10);
    for (int i = 0; i < 32; i++) {
        char c = fields[2][i];
        out->md5[i] = (c >= 'A' && c <= 'F') ? (char)(c - 'A' + 'a') : c;
    }
    out->md5[32] = '\0';
    out->sent = false;

    // Both the logged time and time() come from the DS clock
    int y, mo, d, h, mi, s;
    out->ago = 0;
    if (sscanf(fields[4], "%d-%d-%d %d:%d:%d", &y, &mo, &d, &h, &mi, &s) == 6 &&
        mo >= 1 && mo <= 12 && d >= 1 && d <= 31) {
        long long logged = (long long)days_from_civil(y, mo, d) * 86400LL
                           + h * 3600LL + mi * 60LL + s;
        long long ago = (long long)now - logged;
        if (ago < 0) ago = 0;
        if (ago > 0x7FFFFFFFLL) ago = 0x7FFFFFFFLL;
        out->ago = (long)ago;
    }
    return true;
}

static void tally_results(const char *json, UploadTally *t) {
    const char *p = json;
    while ((p = strstr(p, "\"status\"")) != NULL) {
        p += 8;
        while (*p == ' ' || *p == ':') p++;
        if (*p != '"') continue;
        p++;
        if (strncmp(p, "submitted\"", 10) == 0) t->submitted++;
        else if (strncmp(p, "already\"", 8) == 0) t->already++;
        else if (strncmp(p, "duplicate\"", 10) == 0) t->duplicate++;
        else if (strncmp(p, "pending\"", 8) == 0) t->pending++;
        else if (strncmp(p, "ignored\"", 8) == 0) t->ignored++;
        else if (strncmp(p, "error\"", 6) == 0) t->error++;
        else t->other++;
    }

    if (!t->detail[0] && (p = strstr(json, "\"detail\"")) != NULL) {
        p += 8;
        while (*p == ' ' || *p == ':') p++;
        if (*p == '"') {
            p++;
            size_t i = 0;
            while (*p && *p != '"' && i < sizeof(t->detail) - 1) t->detail[i++] = *p++;
            t->detail[i] = '\0';
        }
    }
}

static bool json_submit_flag(const char *json) {
    const char *p = strstr(json, "\"submit\"");
    if (!p) return false;
    p += 8;
    while (*p == ' ' || *p == ':') p++;
    return strncmp(p, "true", 4) == 0;
}

static void ra_upload_unlocks(SyncState *state) {
    consoleClear();
    iprintf("=== Upload Unlocks ===\n\n");

    int moved = ring_unlocks(true);
    if (moved > 0) iprintf("Moved %d from ramDump.bin\n", moved);

    char log_path[64];
    ra_path(log_path, sizeof(log_path), "ra/unlocks.log");
    FILE *log = fopen(log_path, "rb");
    if (!log) {
        iprintf("No unlocks yet\n(%s)\n", log_path);
        wait_any_button();
        return;
    }

    fseek(log, 0, SEEK_END);
    long log_size = ftell(log);
    long offset = load_uploaded_offset(log, log_size);
    long to_read = log_size - offset;
    if (to_read > RA_LOG_CHUNK) to_read = RA_LOG_CHUNK;

    char *buf = malloc(to_read + 1);
    if (!buf) {
        fclose(log);
        iprintf(CON_RED "Out of memory" CON_RESET "\n");
        wait_any_button();
        return;
    }
    fseek(log, offset, SEEK_SET);
    to_read = (long)fread(buf, 1, to_read, log);
    buf[to_read] = '\0';

    // Only whole lines
    char *last_nl = strrchr(buf, '\n');
    long used = last_nl ? (long)(last_nl - buf) + 1 : 0;
    buf[used] = '\0';
    bool more = (offset + used < log_size) && (to_read == RA_LOG_CHUNK);

    int max_lines = 0;
    for (long i = 0; i < used; i++) {
        if (buf[i] == '\n') max_lines++;
    }
    if (max_lines == 0) {
        free(buf);
        fclose(log);
        iprintf("Nothing new to upload\n");
        wait_any_button();
        return;
    }

    LogUnlock *unlocks = malloc(max_lines * sizeof(LogUnlock));
    if (!unlocks) {
        free(buf);
        fclose(log);
        iprintf(CON_RED "Out of memory" CON_RESET "\n");
        wait_any_button();
        return;
    }

    time_t now = time(NULL);
    int count = 0, skipped = 0;
    for (char *line = buf; line < buf + used; ) {
        char *nl = strchr(line, '\n');
        *nl = '\0';
        if (nl > line && nl[-1] == '\r') nl[-1] = '\0';
        if (line[0]) {
            if (parse_log_line(line, now, &unlocks[count])) count++;
            else skipped++;
        }
        line = nl + 1;
    }
    free(buf);

    iprintf("New unlocks: %d\n", count);
    if (skipped) iprintf("Unreadable lines: %d\n", skipped);
    iprintf("\n");

    UploadTally tally = {0};
    bool all_ok = true, live = true, aborted = false;
    int games = 0;
    http_set_verbose(0);

    for (int i = 0; i < count && !aborted; i++) {
        if (unlocks[i].sent) continue;
        const char *md5 = unlocks[i].md5;

        // Every unlock of this ROM, one id each
        int in_group = 0;
        for (int j = i; j < count; j++) {
            if (!unlocks[j].sent && strcmp(unlocks[j].md5, md5) == 0) in_group++;
        }
        size_t json_cap = 96 + (size_t)in_group * 48;
        char *json = malloc(json_cap);
        if (!json) {
            iprintf(CON_RED "Out of memory" CON_RESET "\n");
            all_ok = false;
            break;
        }
        size_t len = snprintf(json, json_cap, "{\"md5\":\"%s\",\"game_id\":%lu,\"unlocks\":[",
                              md5, (unsigned long)unlocks[i].game_id);
        int ids = 0;
        for (int j = i; j < count; j++) {
            LogUnlock *u = &unlocks[j];
            if (u->sent || strcmp(u->md5, md5) != 0) continue;
            u->sent = true;
            bool dup = false;
            for (int k = i; k < j; k++) {
                if (strcmp(unlocks[k].md5, md5) == 0 && unlocks[k].id == u->id) {
                    dup = true;
                    break;
                }
            }
            if (dup) continue;
            len += snprintf(json + len, json_cap - len, "%s{\"id\":%lu,\"ago\":%ld}",
                            ids ? "," : "", (unsigned long)u->id, u->ago);
            ids++;
        }
        len += snprintf(json + len, json_cap - len, "]}");
        games++;

        iprintf("%.8s... %d unlock%s\n", md5, ids, ids == 1 ? "" : "s");

        char url[384];
        snprintf(url, sizeof(url), "%s/api/v1/ra/unlocks", state->server_url);
        HttpResponse resp = ra_http(url, HTTP_POST, state->api_key, "application/json",
                                            (const uint8_t *)json, len);
        free(json);

        if (resp.status_code == 200 && resp.body) {
            UploadTally before = tally;
            tally_results((const char *)resp.body, &tally);
            if (!json_submit_flag((const char *)resp.body)) live = false;
            if (tally.other > before.other) all_ok = false; // a reply we don't understand
            iprintf("  ok\n");
        } else {
            all_ok = false;
            if (report_http_failure(&resp)) aborted = true;
        }
        http_response_free(&resp);
    }

    http_set_verbose(1);
    free(unlocks);

    // Once the server has them the unlocks are its to deliver: it keeps
    // pending ones until SYNC_RA_SUBMIT is on and retries failed ones itself.
    bool saved = false;
    if (all_ok && !aborted) {
        save_uploaded_offset(log, offset + used);
        saved = true;
    }
    fclose(log);

    iprintf("\n%d game%s\n", games, games == 1 ? "" : "s");
    iprintf(" Submitted: %-4d Already: %d\n", tally.submitted, tally.already);
    iprintf(" Duplicate: %-4d Pending: %d\n", tally.duplicate, tally.pending);
    iprintf(" Ignored:   %-4d Error:   %d\n", tally.ignored, tally.error + tally.other);
    if (tally.detail[0]) iprintf(CON_RED "%.60s" CON_RESET "\n", tally.detail);
    if (aborted) iprintf(CON_RED "Stopped on error" CON_RESET "\n");
    if (!saved) {
        iprintf("\nSome uploads failed; they\n");
        iprintf("will be retried next time\n");
    } else if (!live && tally.pending) {
        iprintf("\nStored on the server; it sends\n");
        iprintf("them to RetroAchievements once\n");
        iprintf("SYNC_RA_SUBMIT is turned on\n");
    } else if (tally.error) {
        iprintf("\nStored; the server retries the\n");
        iprintf("failed ones on the next upload\n");
    } else if (more) {
        iprintf("\nMore unlocks left: run again\n");
    }
    wait_any_button();
}

// ---------------------------------------------------------------------------
// Menu
// ---------------------------------------------------------------------------

void ra_menu(SyncState *state, bool has_wifi) {
    static const char *items[] = {
        "Update achievement sets",
        "Upload unlocks",
    };
    const int item_count = 2;
    int selected = 0;
    int waiting = 0;
    bool redraw = true, recount = true;

    while (pmMainLoop()) {
        if (recount) {
            int ring = ring_unlocks(false);
            int pending = pending_log_lines();
            waiting = (ring > 0 ? ring : 0) + (pending > 0 ? pending : 0);
            recount = false;
        }
        if (redraw) {
            consoleClear();
            iprintf("=== RetroAchievements ===\n\n");
            iprintf("Data: %s/_nds/ra\n", ra_get_root());
            iprintf("Unlocks to upload: %d\n\n", waiting);
            for (int i = 0; i < item_count; i++) {
                iprintf("%c %s\n", i == selected ? '>' : ' ', items[i]);
            }
            iprintf("\n");
            if (!has_wifi) iprintf(CON_RED "WiFi not connected" CON_RESET "\n\n");
            iprintf("A:Select  B:Back\n");
            redraw = false;
        }

        swiWaitForVBlank();
        scanKeys();
        int pressed = keysDown();

        if (pressed & KEY_B) break;
        if (pressed & KEY_UP) {
            selected = (selected - 1 + item_count) % item_count;
            redraw = true;
        }
        if (pressed & KEY_DOWN) {
            selected = (selected + 1) % item_count;
            redraw = true;
        }
        if (pressed & KEY_A) {
            if (!has_wifi) {
                consoleClear();
                iprintf("WiFi required\n");
                iprintf("Use Connect WiFi in the\nconfig menu first\n");
                wait_any_button();
            } else if (selected == 0) {
                ra_update_sets(state);
            } else {
                ra_upload_unlocks(state);
            }
            redraw = recount = true;
        }
    }
}

// ---------------------------------------------------------------------------
// RA Sync (rasync.nds): unattended, after quitting a game
// ---------------------------------------------------------------------------

int ra_pending_unlocks(void) {
    int ring = ring_unlocks(false);
    int lines = pending_log_lines();
    return (ring > 0 ? ring : 0) + (lines > 0 ? lines : 0);
}

bool ra_has_new_roms(void) {
    char rom_dir[64];
    RomList roms = {0};
    find_roms(&roms, rom_dir, sizeof(rom_dir));
    HashCache seen = {0};
    hash_cache_load(&seen);
    bool found = false;
    for (int i = 0; i < roms.count && !found; i++) {
        struct stat st;
        found = stat(roms.paths[i], &st) == 0
             && !hash_cache_find(&seen, base_name(roms.paths[i]), (uint32_t)st.st_size);
    }
    hash_cache_free(&seen);
    rom_list_free(&roms);
    return found;
}

void ra_auto_sync(SyncState *state) {
    ra_auto = true;
    if (ra_pending_unlocks() > 0) ra_upload_unlocks(state);
    ra_update_sets(state);
    ra_auto = false;
}
