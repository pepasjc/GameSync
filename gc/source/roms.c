/*
 * roms.c — GameCube ROM catalog client.
 *
 * Paginated JSON walker (same as the PS2/PSP clients) + GC routing:
 *   sd:/games/<name>.iso   — flat folder Swiss / GC Loader / FlippyDrive read.
 */

#include "roms.h"
#include "gcnet.h"

#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>

static char g_sd_root[8]      = SD_ROOT;
static char g_games_folder[64] = DEFAULT_GAMES_DIR;
static char g_downloads_file[96] = DOWNLOADS_FILE;

void roms_set_target(const char *sd_root, const char *games_folder) {
    if (sd_root && sd_root[0]) {
        strncpy(g_sd_root, sd_root, sizeof(g_sd_root) - 1);
        g_sd_root[sizeof(g_sd_root) - 1] = '\0';
    }
    if (games_folder && games_folder[0]) {
        /* normalise to a leading-slash, no-trailing-slash folder;
         * "/" (SD root — the GC Loader convention) becomes "". */
        snprintf(g_games_folder, sizeof(g_games_folder), "%s%s",
                 games_folder[0] == '/' ? "" : "/", games_folder);
        size_t n = strlen(g_games_folder);
        while (n > 0 && g_games_folder[n - 1] == '/') g_games_folder[--n] = '\0';
    }
    snprintf(g_downloads_file, sizeof(g_downloads_file),
             "%s%s/downloads.dat", g_sd_root, APP_DATA_SUBDIR);
}

const char *roms_downloads_file(void) { return g_downloads_file; }

const char *roms_games_dir(char *out, size_t out_size) {
    snprintf(out, out_size, "%s%s", g_sd_root, g_games_folder);
    return out;
}

/* --- Tiny JSON helpers --- */

static const char *skip_ws(const char *p) {
    while (*p && (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r')) p++;
    return p;
}

static const char *find_key(const char *p, const char *end, const char *key) {
    char needle[64];
    int  n = snprintf(needle, sizeof(needle), "\"%s\"", key);
    if (n <= 0) return NULL;
    int depth = 0;
    while (p < end) {
        if (*p == '{' || *p == '[') { depth++; p++; continue; }
        if (*p == '}' || *p == ']') { if (depth == 0) return NULL; depth--; p++; continue; }
        if (*p == '"') {
            if (depth == 0 && (p + n) <= end && strncmp(p, needle, (size_t)n) == 0) {
                const char *q = skip_ws(p + n);
                if (q < end && *q == ':') return skip_ws(q + 1);
            }
            p++;
            while (p < end && *p != '"') { if (*p == '\\' && p + 1 < end) p++; p++; }
            if (p < end) p++;
            continue;
        }
        p++;
    }
    return NULL;
}

static bool extract_str(const char *p, const char *end, char *out, size_t out_size) {
    if (!p || p >= end || *p != '"') return false;
    p++;
    size_t len = 0;
    while (p < end && *p != '"' && len + 1 < out_size) {
        if (*p == '\\' && p + 1 < end) {
            char c = p[1];
            if (c == '"' || c == '\\' || c == '/') { out[len++] = c; p += 2; continue; }
            if (c == 'n') { out[len++] = '\n'; p += 2; continue; }
            if (c == 'r') { out[len++] = '\r'; p += 2; continue; }
            if (c == 't') { out[len++] = '\t'; p += 2; continue; }
            out[len++] = *p; p++;
            continue;
        }
        out[len++] = *p++;
    }
    out[len] = '\0';
    return true;
}

static bool extract_u64(const char *p, const char *end, uint64_t *out) {
    if (!p || p >= end) return false;
    p = skip_ws(p);
    if (p >= end) return false;
    char *endp = NULL;
    errno = 0;
    unsigned long long v = strtoull(p, &endp, 10);
    if (errno != 0 || endp == p) return false;
    *out = (uint64_t)v;
    return true;
}

static bool object_bounds(const char *p, const char *end, const char **end_out) {
    if (p >= end || *p != '{') return false;
    int depth = 0;
    while (p < end) {
        if (*p == '{') depth++;
        else if (*p == '}') { depth--; if (depth == 0) { *end_out = p; return true; } }
        else if (*p == '"') {
            p++;
            while (p < end && *p != '"') { if (*p == '\\' && p + 1 < end) p++; p++; }
        }
        p++;
    }
    return false;
}

/* --- Catalog parse --- */

static bool parse_catalog_page(const char *scratch_buf, int n,
                               RomCatalog *catalog, bool *has_more_out) {
    if (has_more_out) *has_more_out = false;

    const char *body_end = scratch_buf + n;
    const char *body = skip_ws(scratch_buf);
    if (body < body_end && *body == '{') body++;

    const char *more_v = find_key(body, body_end, "has_more");
    if (more_v && has_more_out) {
        const char *q = skip_ws(more_v);
        *has_more_out = (q < body_end && *q == 't');
    }

    const char *roms_v = find_key(body, body_end, "roms");
    if (!roms_v || *roms_v != '[') {
        snprintf(catalog->last_error, sizeof(catalog->last_error),
                 "Catalog response missing 'roms' array");
        return false;
    }

    const char *p = roms_v + 1;
    while (p < body_end && catalog->count < ROM_CATALOG_MAX) {
        p = skip_ws(p);
        if (p >= body_end || *p == ']') break;
        if (*p == ',') { p++; continue; }
        if (*p != '{') break;

        const char *obj_end = NULL;
        if (!object_bounds(p, body_end, &obj_end)) break;

        RomEntry *e = &catalog->items[catalog->count];
        memset(e, 0, sizeof(*e));

        const char *v;
        v = find_key(p + 1, obj_end, "rom_id");
        if (v) extract_str(v, obj_end, e->rom_id, sizeof(e->rom_id));
        v = find_key(p + 1, obj_end, "filename");
        if (v) extract_str(v, obj_end, e->filename, sizeof(e->filename));
        v = find_key(p + 1, obj_end, "name");
        if (v) extract_str(v, obj_end, e->name, sizeof(e->name));
        v = find_key(p + 1, obj_end, "system");
        if (v) extract_str(v, obj_end, e->system, sizeof(e->system));
        v = find_key(p + 1, obj_end, "size");
        if (v) extract_u64(v, obj_end, &e->size);
        v = find_key(p + 1, obj_end, "extract_format");
        if (v) extract_str(v, obj_end, e->extract_format, sizeof(e->extract_format));

        if (!e->name[0] && e->filename[0])
            strncpy(e->name, e->filename, sizeof(e->name) - 1);

        if (e->rom_id[0] && e->filename[0]) catalog->count++;
        p = obj_end + 1;
    }
    return true;
}

bool roms_fetch_catalog(const SyncState *state, const char *system_code,
                        char *scratch_buf, uint32_t scratch_buf_size,
                        RomCatalog *catalog) {
    if (!state || !catalog || !scratch_buf) return false;
    catalog->count = 0;
    catalog->last_error[0] = '\0';

    const int page_size = 200;
    int offset = 0, pages = 0;

    while (catalog->count < ROM_CATALOG_MAX) {
        int status = 0;
        int n = network_fetch_rom_catalog(
            state, (system_code && system_code[0]) ? system_code : "GC",
            offset, page_size, scratch_buf, scratch_buf_size, &status);
        if (n <= 0 || status != 200) {
            if (catalog->count == 0) {
                if (n == -100)
                    snprintf(catalog->last_error, sizeof(catalog->last_error),
                             "Network not ready (ip=%s)", state->ip);
                else
                    snprintf(catalog->last_error, sizeof(catalog->last_error),
                             "Catalog fetch failed (HTTP %d, n=%d)", status, n);
                return false;
            }
            break;
        }

        bool has_more = false;
        int  before = catalog->count;
        if (!parse_catalog_page(scratch_buf, n, catalog, &has_more)) return false;
        int parsed = catalog->count - before;
        pages++;
        if (!has_more || parsed == 0) break;
        offset += page_size;
        if (pages >= (ROM_CATALOG_MAX / page_size) + 2) break;
    }
    return true;
}

const char *roms_preferred_extract_format(const RomEntry *rom) {
    if (!rom) return "";
    return rom->extract_format;
}

void roms_mkdir_p(const char *path) {
    if (!path || !*path) return;
    char buf[256];
    strncpy(buf, path, sizeof(buf) - 1);
    buf[sizeof(buf) - 1] = '\0';

    char *p = buf;
    char *colon = strchr(buf, ':');
    if (colon) { p = colon + 1; if (*p == '/') p++; }
    else if (*p == '/') p++;

    for (; *p; p++) {
        if (*p == '/') { *p = '\0'; if (buf[0]) mkdir(buf, 0777); *p = '/'; }
    }
    mkdir(buf, 0777);
}

void roms_ensure_target_dirs(void) {
    char games[128];
    roms_games_dir(games, sizeof(games));
    roms_mkdir_p(games);
    char data[96];
    snprintf(data, sizeof(data), "%s%s", g_sd_root, APP_DATA_SUBDIR);
    roms_mkdir_p(data);
}

/* Strip directory + replace extension with .iso when extracting. */
static void make_target_filename(const RomEntry *rom, char *out, size_t out_size) {
    const char *leaf = rom->filename[0] ? rom->filename : rom->name;
    const char *slash = strrchr(leaf, '/');
    if (slash) leaf = slash + 1;

    const char *fmt = roms_preferred_extract_format(rom);
    if (fmt && fmt[0]) {
        /* Extracted to ISO: keep the stem, force .iso. */
        char stem[160];
        strncpy(stem, leaf, sizeof(stem) - 1);
        stem[sizeof(stem) - 1] = '\0';
        char *dot = strrchr(stem, '.');
        if (dot) *dot = '\0';
        snprintf(out, out_size, "%s.iso", stem);
    } else {
        snprintf(out, out_size, "%s", leaf);
    }
}

bool roms_resolve_target_path(const RomEntry *rom, char *out_path, size_t out_size) {
    if (!rom || !out_path || out_size < 32) return false;
    char fname[180];
    make_target_filename(rom, fname, sizeof(fname));
    char games[128];
    roms_games_dir(games, sizeof(games));
    int n = snprintf(out_path, out_size, "%s/%s", games, fname);
    return n > 0 && (size_t)n < out_size;
}

/* --- Local scan --- */

static bool is_rom_ext(const char *fname) {
    size_t n = strlen(fname);
    static const char *exts[] = { ".iso", ".gcm", ".rvz", ".ciso", ".gcz", NULL };
    for (int i = 0; exts[i]; i++) {
        size_t el = strlen(exts[i]);
        if (n > el && strcasecmp(fname + n - el, exts[i]) == 0) return true;
    }
    return false;
}

static void scan_local_dir(const char *dir, LocalRomList *out) {
    errno = 0;
    DIR *d = opendir(dir);
    if (!d) {
        if (out->last_error[0] == '\0')
            snprintf(out->last_error, sizeof(out->last_error),
                     "Cannot open %s (errno %d)", dir, errno);
        return;
    }

    struct dirent *de;
    while ((de = readdir(d)) != NULL && out->count < LOCAL_ROMS_MAX) {
        if (de->d_name[0] == '.') continue;
        if (!is_rom_ext(de->d_name)) continue;

        LocalRom *e = &out->items[out->count];
        memset(e, 0, sizeof(*e));
        strncpy(e->filename, de->d_name, sizeof(e->filename) - 1);
        snprintf(e->path, sizeof(e->path), "%s/%s", dir, de->d_name);

        /* Display name = filename minus extension. */
        strncpy(e->name, de->d_name, sizeof(e->name) - 1);
        char *dot = strrchr(e->name, '.');
        if (dot) *dot = '\0';

        struct stat st;
        if (stat(e->path, &st) == 0) e->size = (uint64_t)st.st_size;
        out->count++;
    }
    closedir(d);
}

void roms_scan_local(LocalRomList *out) {
    if (!out) return;
    out->count = 0;
    out->last_error[0] = '\0';

    /* Scan the configured install folder AND the SD root — GC Loader users
     * commonly keep ISOs at the root of the card. */
    char dir[128];
    roms_games_dir(dir, sizeof(dir));
    scan_local_dir(dir, out);
    if (strcmp(dir, SD_ROOT) != 0)
        scan_local_dir(SD_ROOT, out);

    if (out->count > 0) out->last_error[0] = '\0';
    else if (out->last_error[0] == '\0')
        snprintf(out->last_error, sizeof(out->last_error),
                 "No ISOs in %s or sd:/", dir);
}
