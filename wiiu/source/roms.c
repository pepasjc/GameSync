/*
 * roms.c — ROM catalog client + on-SD install layout.
 *
 * Paginated JSON walker ported from gc/source/roms.c, with Wii U specific
 * routing: Nintendont wants sd:/games/<GAMEID6>/game.iso, USB loaders want
 * sd:/wbfs/<Name> [ID6]/<ID6>.wbfs.
 */

#include "roms.h"
#include "wiiunet.h"

#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <unistd.h>

/* GameCube disc magic at offset 0x1C. */
#define GC_DISC_MAGIC 0xC2339F3DU

static char g_sd_root[64]        = SD_ROOT_DEFAULT;
/* Root that downloaded games are written under.  Equals g_sd_root unless the
 * user selected USB storage AND a FAT32 drive actually mounted. */
static char g_storage_root[64]   = SD_ROOT_DEFAULT;
static bool g_storage_is_usb     = false;
static char g_games_dir[96]      = DEFAULT_GAMES_DIR;
static char g_wbfs_dir[96]       = DEFAULT_WBFS_DIR;
static char g_install_dir[96]    = DEFAULT_INSTALL_DIR;
static char g_downloads_file[SAVE_DIR_LEN];

static void normalise_folder(char *dst, size_t dst_size, const char *src) {
    if (!src || !src[0]) return;
    snprintf(dst, dst_size, "%s%s", src[0] == '/' ? "" : "/", src);
    size_t n = strlen(dst);
    while (n > 1 && dst[n - 1] == '/') dst[--n] = '\0';
}

void roms_set_target(const SyncState *state) {
    if (state && state->sd_root[0]) {
        strncpy(g_sd_root, state->sd_root, sizeof(g_sd_root) - 1);
        g_sd_root[sizeof(g_sd_root) - 1] = '\0';
    }
    if (state) {
        normalise_folder(g_games_dir,   sizeof(g_games_dir),   state->games_dir);
        normalise_folder(g_wbfs_dir,    sizeof(g_wbfs_dir),    state->wbfs_dir);
        normalise_folder(g_install_dir, sizeof(g_install_dir), state->install_dir);
    }

    /* "usb" only takes effect once the FAT32 drive is actually mounted —
     * otherwise every download would fail at fopen with no explanation.
     * roms_storage_is_usb() lets the UI show which one won. */
    g_storage_is_usb = state && !strcasecmp(state->rom_storage, "usb") &&
                       state->usb_fat_ready;
    snprintf(g_storage_root, sizeof(g_storage_root), "%s",
             g_storage_is_usb ? USB_ROOT_DEFAULT : g_sd_root);

    /* The download ledger stays on SD with the rest of the app data: it must
     * survive the USB drive being unplugged mid-queue. */
    snprintf(g_downloads_file, sizeof(g_downloads_file),
             "%s%s/downloads.dat", g_sd_root, APP_DATA_SUBDIR);
}

const char *roms_downloads_file(void) { return g_downloads_file; }

bool roms_storage_is_usb(void) { return g_storage_is_usb; }

const char *roms_storage_root(void) { return g_storage_root; }

const char *roms_games_dir(char *out, size_t out_size) {
    snprintf(out, out_size, "%s%s", g_storage_root, g_games_dir);
    return out;
}

const char *roms_wbfs_dir(char *out, size_t out_size) {
    snprintf(out, out_size, "%s%s", g_storage_root, g_wbfs_dir);
    return out;
}

const char *roms_install_dir(char *out, size_t out_size) {
    snprintf(out, out_size, "%s%s", g_storage_root, g_install_dir);
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

/* Read a JSON array of strings into a fixed 2-D char buffer.
 * ``out`` is ``count_max`` slots of ``slot_len`` bytes each; extra elements
 * are dropped rather than overflowing. */
static bool extract_str_array(const char *p, const char *end,
                              char (*out)[ROM_ID_LEN],
                              int count_max, size_t slot_len, int *count_out) {
    if (count_out) *count_out = 0;
    if (!p || p >= end || *p != '[') return false;
    p++;
    int n = 0;
    while (p < end && n < count_max) {
        p = skip_ws(p);
        if (p >= end || *p == ']') break;
        if (*p == ',') { p++; continue; }
        if (*p != '"') break;
        if (!extract_str(p, end, out[n], slot_len)) break;
        n++;
        /* Step past the string we just consumed. */
        p++;
        while (p < end && *p != '"') { if (*p == '\\' && p + 1 < end) p++; p++; }
        if (p < end) p++;
    }
    if (count_out) *count_out = n;
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
        v = find_key(p + 1, obj_end, "is_bundle");
        if (v) e->is_bundle = (*skip_ws(v) == 't');
        v = find_key(p + 1, obj_end, "content_type");
        if (v) extract_str(v, obj_end, e->content_type, sizeof(e->content_type));
        v = find_key(p + 1, obj_end, "related_rom_ids");
        if (v) extract_str_array(v, obj_end, e->related, WIIU_RELATED_MAX,
                                 ROM_ID_LEN, &e->related_count);

        if (!e->name[0] && e->filename[0])
            strncpy(e->name, e->filename, sizeof(e->name) - 1);
        if (!e->system[0])
            strncpy(e->system, catalog->system, sizeof(e->system) - 1);

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
    snprintf(catalog->system, sizeof(catalog->system), "%s",
             (system_code && system_code[0]) ? system_code : "GC");

    const int page_size = 200;
    int offset = 0, pages = 0;

    while (catalog->count < ROM_CATALOG_MAX) {
        int status = 0;
        int n = network_fetch_rom_catalog(state, catalog->system,
                                          offset, page_size,
                                          scratch_buf, scratch_buf_size, &status);
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
    char buf[SAVE_DIR_LEN];
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
    char dir[SAVE_DIR_LEN];
    roms_games_dir(dir, sizeof(dir));
    roms_mkdir_p(dir);
    roms_wbfs_dir(dir, sizeof(dir));
    roms_mkdir_p(dir);
    roms_install_dir(dir, sizeof(dir));
    roms_mkdir_p(dir);
    /* App data always on SD, never on the removable storage root. */
    snprintf(dir, sizeof(dir), "%s%s", g_sd_root, APP_DATA_SUBDIR);
    roms_mkdir_p(dir);
    roms_games_dir(dir, sizeof(dir));
    strncat(dir, "/_dl", sizeof(dir) - strlen(dir) - 1);
    roms_mkdir_p(dir);
}

/* Filesystem-safe copy of an arbitrary server id / game name. */
static void sanitise(const char *in, char *out, size_t out_size) {
    size_t j = 0;
    for (size_t i = 0; in[i] && j + 1 < out_size; i++) {
        unsigned char c = (unsigned char)in[i];
        bool ok = (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
                  (c >= '0' && c <= '9') || c == '_' || c == '-' ||
                  c == '.' || c == ' ' || c == '(' || c == ')';
        if (!ok) c = '_';
        if (c == ' ' && (j == 0 || out[j - 1] == ' ')) continue;
        out[j++] = (char)c;
    }
    while (j > 0 && (out[j - 1] == ' ' || out[j - 1] == '.')) j--;
    out[j] = '\0';
}

bool roms_resolve_gc_staging_path(const RomEntry *rom, char *out, size_t out_size) {
    if (!rom || !out) return false;
    char id[96];
    sanitise(rom->rom_id, id, sizeof(id));
    char games[SAVE_DIR_LEN];
    roms_games_dir(games, sizeof(games));
    int n = snprintf(out, out_size, "%s/_dl/%s.iso", games, id);
    return n > 0 && (size_t)n < out_size;
}

bool roms_gameid_from_iso(const char *iso_path, char *out_id6, size_t out_size) {
    if (!iso_path || !out_id6 || out_size < 7) return false;
    FILE *fp = fopen(iso_path, "rb");
    if (!fp) return false;

    unsigned char head[0x20];
    size_t got = fread(head, 1, sizeof(head), fp);
    fclose(fp);
    if (got != sizeof(head)) return false;

    uint32_t magic = ((uint32_t)head[0x1C] << 24) | ((uint32_t)head[0x1D] << 16) |
                     ((uint32_t)head[0x1E] <<  8) | (uint32_t)head[0x1F];
    if (magic != GC_DISC_MAGIC) return false;

    for (int i = 0; i < 6; i++) {
        unsigned char c = head[i];
        if (c < 0x20 || c > 0x7E) return false;
        out_id6[i] = (char)c;
    }
    out_id6[6] = '\0';
    return true;
}

int roms_install_gc_iso(const char *staging_path, char *msg, size_t msg_size) {
    char id6[8];
    if (!roms_gameid_from_iso(staging_path, id6, sizeof(id6))) {
        snprintf(msg, msg_size, "Not a GameCube ISO (bad disc magic)");
        return -1;
    }

    char games[SAVE_DIR_LEN], dir[SAVE_DIR_LEN], dest[SAVE_DIR_LEN];
    roms_games_dir(games, sizeof(games));
    snprintf(dir,  sizeof(dir),  "%s/%s", games, id6);
    snprintf(dest, sizeof(dest), "%s/game.iso", dir);
    roms_mkdir_p(dir);

    remove(dest);
    if (rename(staging_path, dest) != 0) {
        snprintf(msg, msg_size, "Move to %s failed (errno %d)", dest, errno);
        return -1;
    }
    snprintf(msg, msg_size, "Installed %s", id6);
    return 0;
}

bool roms_is_wiiu_title_id(const char *s) {
    if (!s) return false;
    if (strncasecmp(s, "0005", 4) != 0) return false;
    for (int i = 0; i < 16; i++)
        if (!isxdigit((unsigned char)s[i])) return false;
    return s[16] == '\0';
}

void roms_wup_game_dir(const char *title_id, const char *name,
                       char *out, size_t out_size) {
    char root[SAVE_DIR_LEN];
    roms_install_dir(root, sizeof(root));

    /* Name the folder after the title id, uppercase hex, exactly as NUSspli
     * and WUP Installer do.
     *
     * This is not cosmetic.  A game-name folder puts spaces and parentheses
     * into the FSA path handed to MCP_InstallTitleAsync, and that was the one
     * structural difference between our staged folders (which MCP refused
     * without reporting anything) and NUSspli's (which install fine) — the
     * file sets themselves were byte-for-byte equivalent.  A hex id also
     * makes the folder self-identifying and lets a re-download land on top of
     * the previous one instead of creating a near-duplicate. */
    if (roms_is_wiiu_title_id(title_id)) {
        char upper[17];
        for (int i = 0; i < 16; i++)
            upper[i] = (char)toupper((unsigned char)title_id[i]);
        upper[16] = '\0';
        snprintf(out, out_size, "%s/%s", root, upper);
        return;
    }

    /* No usable id (shouldn't happen for a catalog bundle) — fall back to the
     * sanitised name so the download still has somewhere to go. */
    char clean[MAX_TITLE_LEN];
    sanitise(name && name[0] ? name : "title", clean, sizeof(clean));
    if (strlen(clean) > 80) clean[80] = '\0';
    snprintf(out, out_size, "%s/%s", root, clean);
}

bool roms_is_wup_dir(const char *dir) {
    if (!dir || !dir[0]) return false;
    char probe[SAVE_DIR_LEN];
    struct stat st;
    snprintf(probe, sizeof(probe), "%s/title.tmd", dir);
    if (stat(probe, &st) == 0 && S_ISREG(st.st_mode)) return true;
    /* Some dumps use the uppercase names WUP Installer also accepts. */
    snprintf(probe, sizeof(probe), "%s/TITLE.TMD", dir);
    return stat(probe, &st) == 0 && S_ISREG(st.st_mode);
}

void roms_wbfs_game_dir(const char *name, const char *id6,
                        char *out, size_t out_size) {
    char clean[MAX_TITLE_LEN];
    sanitise(name && name[0] ? name : id6, clean, sizeof(clean));
    /* USB Loader GX truncates long folder names; keep it comfortably short. */
    if (strlen(clean) > 80) clean[80] = '\0';

    char root[SAVE_DIR_LEN];
    roms_wbfs_dir(root, sizeof(root));
    snprintf(out, out_size, "%s/%s [%s]", root, clean, id6);
}

/* --- Local scan --- */

static void push_local(LocalRomList *out, const char *name, const char *filename,
                       const char *path, const char *system) {
    if (out->count >= LOCAL_ROMS_MAX) return;
    LocalRom *e = &out->items[out->count];
    memset(e, 0, sizeof(*e));
    strncpy(e->name,     name,     sizeof(e->name) - 1);
    strncpy(e->filename, filename, sizeof(e->filename) - 1);
    strncpy(e->path,     path,     sizeof(e->path) - 1);
    strncpy(e->system,   system,   sizeof(e->system) - 1);
    struct stat st;
    if (stat(path, &st) == 0) e->size = (uint64_t)st.st_size;
    out->count++;
}

/* Nintendont: <games>/<GAMEID6>/game.iso (also accepts disc2.iso siblings). */
static void scan_games_dir(LocalRomList *out) {
    char games[SAVE_DIR_LEN];
    roms_games_dir(games, sizeof(games));

    DIR *d = opendir(games);
    if (!d) {
        if (out->last_error[0] == '\0')
            snprintf(out->last_error, sizeof(out->last_error),
                     "Cannot open %s (errno %d)", games, errno);
        return;
    }
    struct dirent *de;
    while ((de = readdir(d)) != NULL && out->count < LOCAL_ROMS_MAX) {
        if (de->d_name[0] == '.') continue;
        if (strcmp(de->d_name, "_dl") == 0) continue;

        char sub[SAVE_DIR_LEN], iso[SAVE_DIR_LEN];
        snprintf(sub, sizeof(sub), "%s/%s", games, de->d_name);
        struct stat st;
        if (stat(sub, &st) != 0 || !S_ISDIR(st.st_mode)) continue;

        snprintf(iso, sizeof(iso), "%s/game.iso", sub);
        if (stat(iso, &st) == 0)
            push_local(out, de->d_name, "game.iso", iso, "GC");
    }
    closedir(d);
}

static bool has_wbfs_ext(const char *fname) {
    size_t n = strlen(fname);
    if (n < 6) return false;
    return strcasecmp(fname + n - 5, ".wbfs") == 0 ||
           strcasecmp(fname + n - 4, ".iso") == 0;
}

/* USB loaders: <wbfs>/<Name [ID6]>/<something>.wbfs */
static void scan_wbfs_dir(LocalRomList *out) {
    char root[SAVE_DIR_LEN];
    roms_wbfs_dir(root, sizeof(root));

    DIR *d = opendir(root);
    if (!d) return;
    struct dirent *de;
    while ((de = readdir(d)) != NULL && out->count < LOCAL_ROMS_MAX) {
        if (de->d_name[0] == '.') continue;

        char sub[SAVE_DIR_LEN];
        snprintf(sub, sizeof(sub), "%s/%s", root, de->d_name);
        struct stat st;
        if (stat(sub, &st) != 0 || !S_ISDIR(st.st_mode)) continue;

        DIR *g = opendir(sub);
        if (!g) continue;
        struct dirent *ge;
        while ((ge = readdir(g)) != NULL && out->count < LOCAL_ROMS_MAX) {
            if (ge->d_name[0] == '.') continue;
            if (!has_wbfs_ext(ge->d_name)) continue;
            char f[SAVE_DIR_LEN];
            snprintf(f, sizeof(f), "%s/%s", sub, ge->d_name);
            push_local(out, de->d_name, ge->d_name, f, "WII");
            break;   /* one row per game — .wbf1 parts are not listed */
        }
        closedir(g);
    }
    closedir(d);
}

/* Wii U WUP folders staged for MCP install: <install>/<Name>/title.tmd */
static void scan_install_dir(LocalRomList *out) {
    char root[SAVE_DIR_LEN];
    roms_install_dir(root, sizeof(root));

    DIR *d = opendir(root);
    if (!d) return;
    struct dirent *de;
    while ((de = readdir(d)) != NULL && out->count < LOCAL_ROMS_MAX) {
        if (de->d_name[0] == '.') continue;

        char sub[SAVE_DIR_LEN];
        snprintf(sub, sizeof(sub), "%s/%s", root, de->d_name);
        struct stat st;
        if (stat(sub, &st) != 0 || !S_ISDIR(st.st_mode)) continue;
        /* A folder still downloading has no title.tmd yet, so this doubles
         * as the "ready to install" filter. */
        if (!roms_is_wup_dir(sub)) continue;

        push_local(out, de->d_name, "title.tmd", sub, "WIIU");
        if (roms_is_wiiu_title_id(de->d_name)) {
            LocalRom *e = &out->items[out->count - 1];
            snprintf(e->title_id, sizeof(e->title_id), "%s", de->d_name);
        }
    }
    closedir(d);
}

void roms_scan_installable(LocalRomList *out) {
    if (!out) return;
    out->count = 0;
    out->last_error[0] = '\0';

    scan_install_dir(out);

    if (out->count == 0) {
        char root[SAVE_DIR_LEN];
        roms_install_dir(root, sizeof(root));
        snprintf(out->last_error, sizeof(out->last_error),
                 "No WUP folders under %s", root);
    }
}

void roms_scan_local(LocalRomList *out) {
    if (!out) return;
    out->count = 0;
    out->last_error[0] = '\0';

    scan_games_dir(out);
    scan_wbfs_dir(out);
    scan_install_dir(out);

    if (out->count > 0) out->last_error[0] = '\0';
    else if (out->last_error[0] == '\0')
        snprintf(out->last_error, sizeof(out->last_error), "No games installed");
}
