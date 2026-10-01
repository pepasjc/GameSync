#include "ra.h"
#include "ra_hash.h"
#include "ra_sets.h"
#include "http.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <dirent.h>
#include <sys/stat.h>

// ROM scan limits
#define RA_MAX_ROMS 1000
#define RA_MAX_DEPTH 4
#define RA_PATH_MAX 512

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

static void wait_any_button(void) {
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
// Menu
// ---------------------------------------------------------------------------

void ra_menu(SyncState *state, bool has_wifi) {
    bool redraw = true;

    while (pmMainLoop()) {
        if (redraw) {
            consoleClear();
            iprintf("=== RetroAchievements ===\n\n");
            iprintf("Data: %s/_nds/ra\n\n", ra_get_root());
            iprintf("Saves the achievement set of\n");
            iprintf("every ROM in the ROM folder\n");
            iprintf("for nds-bootstrap-ra\n\n");
            if (!has_wifi) iprintf(CON_RED "WiFi not connected" CON_RESET "\n\n");
            iprintf("A:Update sets  B:Back\n");
            redraw = false;
        }

        swiWaitForVBlank();
        scanKeys();
        int pressed = keysDown();

        if (pressed & KEY_B) break;
        if (pressed & KEY_A) {
            if (!has_wifi) {
                consoleClear();
                iprintf("WiFi required\n");
                iprintf("Use Connect WiFi in the\nconfig menu first\n");
                wait_any_button();
            } else {
                ra_update_sets(state);
            }
            redraw = true;
        }
    }
}
