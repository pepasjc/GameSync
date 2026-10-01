#include "games.h"

#include "http.h"

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <hal/debug.h>
#include <nxdk/mount.h>
#include <windows.h>

#define ZIP_LOCAL_SIG   0x04034B50u
#define ZIP_CENTRAL_SIG 0x02014B50u
#define ZIP_END_SIG     0x06054B50u

#ifndef XBOX_PATH_MAX
#define XBOX_PATH_MAX 260
#endif

static uint16_t rd16(const uint8_t *p)
{
    return (uint16_t)p[0] | ((uint16_t)p[1] << 8);
}

static uint32_t rd32(const uint8_t *p)
{
    return (uint32_t)p[0]
        | ((uint32_t)p[1] << 8)
        | ((uint32_t)p[2] << 16)
        | ((uint32_t)p[3] << 24);
}

static int streq_ci(const char *a, const char *b)
{
    if (!a || !b) return 0;
    while (*a && *b) {
        if (tolower((unsigned char)*a) != tolower((unsigned char)*b)) return 0;
        a++; b++;
    }
    return *a == '\0' && *b == '\0';
}

XboxGameFormat games_config_format(const XboxConfig *cfg)
{
    if (cfg && streq_ci(cfg->game_format, "folder")) {
        return XBOX_GAME_FORMAT_FOLDER;
    }
    return XBOX_GAME_FORMAT_CCI;
}

const char *games_format_name(XboxGameFormat fmt)
{
    return fmt == XBOX_GAME_FORMAT_FOLDER ? "folder" : "cci";
}

static const char *install_dir_for(const XboxConfig *cfg)
{
    return (cfg && cfg->game_install_dir[0])
        ? cfg->game_install_dir
        : "F:\\Games";
}

static void join_url(const char *base, const char *path,
                     char *buf, int buf_len)
{
    int blen = (int)strlen(base);
    while (blen > 0 && base[blen - 1] == '/') blen--;
    snprintf(buf, buf_len, "%.*s%s", blen, base, path);
}

static int ensure_dir_one(const char *path)
{
    if (CreateDirectoryA(path, NULL)) return 0;
    DWORD err = GetLastError();
    if (err == ERROR_ALREADY_EXISTS) return 0;
    return -1;
}

static int ensure_dir(const char *path)
{
    char tmp[XBOX_CFG_PATH_LEN + XBOX_ROM_NAME_MAX + 32];
    int len = (int)strlen(path);
    if (len <= 0 || len >= (int)sizeof(tmp)) return -1;
    memcpy(tmp, path, len + 1);

    int start = 0;
    if (len >= 3 && tmp[1] == ':' && (tmp[2] == '\\' || tmp[2] == '/')) {
        start = 3;
    }

    for (int i = start; i <= len; i++) {
        if (tmp[i] == '\\' || tmp[i] == '/' || tmp[i] == '\0') {
            char saved = tmp[i];
            tmp[i] = '\0';
            if (i > start && ensure_dir_one(tmp) != 0) return -1;
            tmp[i] = saved;
            if (saved == '\0') break;
        }
    }
    return 0;
}

int games_mount_target(const XboxConfig *cfg, char *err, int err_len)
{
    const char *dir = install_dir_for(cfg);
    char drive = toupper((unsigned char)dir[0]);
    if (drive == 'F' && !nxIsDriveMounted('F')) {
        nxMountDrive('F', "\\Device\\Harddisk0\\Partition6\\");
    }
    if (ensure_dir(dir) != 0) {
        if (err) snprintf(err, err_len, "Could not create %s", dir);
        return -1;
    }
    return 0;
}

static void sanitize_folder_name(const char *src, char *out, int out_len)
{
    int oi = 0;
    if (!src || !src[0]) src = "Game";
    for (int i = 0; src[i] && oi < out_len - 1; i++) {
        unsigned char c = (unsigned char)src[i];
        if (c < 32 || c == '<' || c == '>' || c == ':' || c == '"' ||
            c == '/' || c == '\\' || c == '|' || c == '?' || c == '*') {
            c = '_';
        }
        out[oi++] = (char)c;
        if (oi >= 42) break;  // FATX filename limit is tight; stay safe.
    }
    while (oi > 0 && (out[oi - 1] == ' ' || out[oi - 1] == '.')) oi--;
    if (oi == 0) {
        snprintf(out, out_len, "Game");
    } else {
        out[oi] = '\0';
    }
}

static void make_target_dir(const XboxConfig *cfg, const XboxRomEntry *rom,
                            char *out, int out_len)
{
    char folder[64];
    sanitize_folder_name(rom->name[0] ? rom->name : rom->rom_id,
                         folder, sizeof(folder));
    const char *base = install_dir_for(cfg);
    int blen = (int)strlen(base);
    while (blen > 0 && (base[blen - 1] == '\\' || base[blen - 1] == '/')) {
        blen--;
    }
    snprintf(out, out_len, "%.*s\\%s", blen, base, folder);
}

// ---------------------------------------------------------------------------
// ROM catalog, cached on disk (see catalog_cache.h)
// ---------------------------------------------------------------------------

#define CATALOG_SYSTEM     "XBOX"
#define CATALOG_PAGE_SIZE  500
#define CATALOG_MAX_PAGES  100

static int read_whole_file(const char *path, char **out, size_t *out_len)
{
    *out = NULL;
    *out_len = 0;
    HANDLE h = CreateFileA(path, GENERIC_READ, FILE_SHARE_READ, NULL,
                           OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) return -1;
    DWORD size = GetFileSize(h, NULL);
    if (size == INVALID_FILE_SIZE || size == 0 || size > 64u * 1024u * 1024u) {
        CloseHandle(h);
        return -1;
    }
    char *buf = (char *)malloc((size_t)size + 1);
    if (!buf) {
        CloseHandle(h);
        return -1;
    }
    DWORD got = 0;
    BOOL ok = ReadFile(h, buf, size, &got, NULL);
    CloseHandle(h);
    if (!ok || got != size) {
        free(buf);
        return -1;
    }
    buf[size] = '\0';
    *out = buf;
    *out_len = (size_t)size;
    return 0;
}

static void catalog_cache_delete(void)
{
    DeleteFileA(XBOX_CATALOG_CACHE_PATH);
}

// Written to a .part file and moved into place, so a power cut mid-write
// leaves the previous copy (or nothing), never a torn cache.
static void catalog_cache_save(const char *fingerprint, const XboxRomList *list)
{
    size_t len = 0;
    char *buf = catcache_serialize(CATALOG_SYSTEM, fingerprint, list, &len);
    if (!buf) return;

    const char *part = XBOX_CATALOG_CACHE_PATH ".part";
    HANDLE h = CreateFileA(part, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS,
                           FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) {
        free(buf);
        return;
    }
    DWORD written = 0;
    BOOL ok = WriteFile(h, buf, (DWORD)len, &written, NULL);
    CloseHandle(h);
    free(buf);
    if (!ok || written != (DWORD)len) {
        DeleteFileA(part);
        return;
    }
    DeleteFileA(XBOX_CATALOG_CACHE_PATH);
    if (!MoveFileA(part, XBOX_CATALOG_CACHE_PATH)) DeleteFileA(part);
}

static int catalog_cache_load(XboxRomList *out, char *fp, int fp_len)
{
    char *buf = NULL;
    size_t len = 0;
    if (read_whole_file(XBOX_CATALOG_CACHE_PATH, &buf, &len) != 0) return -1;
    int rc = catcache_parse(buf, len, CATALOG_SYSTEM, fp, fp_len, out);
    free(buf);
    return rc;
}

// Every XBOX row, paged like the MiSTer client's list_roms().
static int catalog_fetch_all(const XboxConfig *cfg, XboxRomList *out,
                             int expected,
                             CatalogProgressFn progress, void *progress_user,
                             char *err, int err_len)
{
    rom_list_clear(out);
    int offset = 0;
    for (int page = 0; page < CATALOG_MAX_PAGES; page++) {
        char path[128], url[512];
        snprintf(path, sizeof(path),
                 "/api/v1/roms?system=" CATALOG_SYSTEM "&limit=%d&offset=%d",
                 CATALOG_PAGE_SIZE, offset);
        join_url(cfg->server_url, path, url, sizeof(url));
        HttpResponse rsp = http_request(url, HTTP_GET, cfg->api_key,
                                        cfg->console_id, NULL, NULL, 0);
        if (!rsp.success || !rsp.body) {
            if (err) snprintf(err, err_len, "ROM catalog HTTP %d", rsp.status_code);
            http_response_free(&rsp);
            return -1;
        }
        int rows = 0, more = 0;
        int rc = catcache_parse_roms_page((const char *)rsp.body, out,
                                          &rows, &more);
        http_response_free(&rsp);
        if (rc == -1) {
            if (err) snprintf(err, err_len, "Bad ROM catalog response");
            return -1;
        }
        offset += rows;
        if (progress) progress(out->count, expected, progress_user);
        if (rc == -2) break;            // list full: keep what fits
        if (!more || rows == 0) break;
    }
    return 0;
}

int games_load_catalog(const XboxConfig *cfg, XboxRomList *out, int force,
                       CatalogProgressFn progress, void *progress_user,
                       CatalogLoadInfo *info, char *err, int err_len)
{
    if (!cfg || !out) return -1;
    CatalogLoadInfo local_info;
    if (!info) info = &local_info;
    memset(info, 0, sizeof(*info));

    char url[512];
    if (force) {
        // The server's catalog lives in memory and only moves on a scan.
        join_url(cfg->server_url, "/api/v1/roms/scan", url, sizeof(url));
        HttpResponse scan = http_request(url, HTTP_GET, cfg->api_key,
                                         cfg->console_id, NULL, NULL, 0);
        if (scan.success) {
            info->rescan = 1;
            if (scan.body) {
                const char *c = strstr((const char *)scan.body, "\"count\"");
                if (c) c = strchr(c, ':');
                if (c) info->rescan_count = atoi(c + 1);
            }
        } else if (scan.status_code == 403 || scan.status_code == 404 ||
                   scan.status_code == 405) {
            info->rescan = 0;
        } else {
            info->rescan = -1;
        }
        http_response_free(&scan);
        catalog_cache_delete();
    }

    XboxRomList fresh = { 0, 0, NULL };
    XboxRomList cached = { 0, 0, NULL };
    char cached_fp[CATCACHE_FP_MAX] = "";

    join_url(cfg->server_url, "/api/v1/roms/fingerprints", url, sizeof(url));
    HttpResponse rsp = http_request(url, HTTP_GET, cfg->api_key,
                                    cfg->console_id, NULL, NULL, 0);
    int status = rsp.status_code;
    int ok_response = rsp.success && rsp.body;
    char server_fp[CATCACHE_FP_MAX] = "";
    int expected = 0;
    int found = -1;
    if (ok_response) {
        found = catcache_find_fingerprint((const char *)rsp.body, CATALOG_SYSTEM,
                                          server_fp, sizeof(server_fp),
                                          &expected);
    }
    http_response_free(&rsp);

    if (status == 404 || status == 405 || (ok_response && found < 0)) {
        // Server without fingerprints: fetch everything, cache nothing.
        if (catalog_fetch_all(cfg, &fresh, 0, progress, progress_user,
                              err, err_len) != 0) {
            rom_list_free(&fresh);
            return -1;
        }
        rom_list_take(out, &fresh);
        info->source = CATALOG_UNCACHED;
        return 0;
    }

    if (!ok_response) {
        // Offline: the last copy beats an empty tab.
        if (catalog_cache_load(&cached, cached_fp, sizeof(cached_fp)) == 0) {
            rom_list_take(out, &cached);
            info->source = CATALOG_FROM_CACHE_OFFLINE;
            return 0;
        }
        rom_list_free(&cached);
        if (err) snprintf(err, err_len, "Catalog: server unreachable (HTTP %d)",
                          status);
        return -1;
    }

    if (found == 0) {
        // The server has no Xbox games at all.
        catalog_cache_delete();
        rom_list_clear(out);
        info->source = CATALOG_FROM_SERVER;
        return 0;
    }

    int have_cache = catalog_cache_load(&cached, cached_fp,
                                        sizeof(cached_fp)) == 0;
    if (have_cache && server_fp[0] && strcmp(cached_fp, server_fp) == 0) {
        rom_list_take(out, &cached);
        info->source = CATALOG_FROM_CACHE;
        return 0;
    }

    if (catalog_fetch_all(cfg, &fresh, expected, progress, progress_user,
                          err, err_len) != 0) {
        rom_list_free(&fresh);
        if (have_cache) {
            // Stale beats nothing; the next load tries again.
            rom_list_take(out, &cached);
            info->source = CATALOG_FROM_CACHE_OFFLINE;
            return 0;
        }
        rom_list_free(&cached);
        return -1;
    }
    rom_list_free(&cached);
    if (server_fp[0]) catalog_cache_save(server_fp, &fresh);
    rom_list_take(out, &fresh);
    info->source = CATALOG_FROM_SERVER;
    return 0;
}

typedef enum {
    ZIP_STATE_HEADER,
    ZIP_STATE_NAME,
    ZIP_STATE_FILE,
    ZIP_STATE_DONE,
    ZIP_STATE_ERROR,
} ZipState;

typedef struct {
    char target_dir[XBOX_CFG_PATH_LEN + XBOX_ROM_NAME_MAX + 32];
    char err[160];
    ZipState state;
    uint8_t header[30];
    int header_got;
    char name[XBOX_PATH_MAX];
    int name_len;
    int extra_len;
    int name_got;
    int method;
    int flags;
    uint32_t remaining;
    uint32_t files;
    HANDLE out;
    uint64_t http_done;
    uint64_t http_total;
    GameProgressFn progress;
    void *progress_user;
    int cancelled;
} ZipCtx;

// Report progress; a non-zero return from the UI cancels the download.
static int delete_tree(const char *path, char *err, int err_len);

static int zip_progress(ZipCtx *z, const char *msg)
{
    if (!z->progress) return 0;
    if (z->progress(msg, z->http_done, z->http_total, z->progress_user) != 0) {
        z->cancelled = 1;
        snprintf(z->err, sizeof(z->err), "Download cancelled");
        return -1;
    }
    return 0;
}

static int is_bad_zip_path(const char *name)
{
    if (!name || !name[0]) return 1;
    if (strstr(name, "..")) return 1;
    if (strchr(name, ':')) return 1;
    if (name[0] == '/' || name[0] == '\\') return 1;
    return 0;
}

static void make_output_path(ZipCtx *z, const char *name,
                             char *out, int out_len)
{
    int off = snprintf(out, out_len, "%s\\", z->target_dir);
    for (int i = 0; name[i] && off < out_len - 1; i++) {
        char c = name[i];
        if (c == '/') c = '\\';
        out[off++] = c;
    }
    out[off] = '\0';
}

static void ensure_parent_dir(const char *path)
{
    char tmp[XBOX_PATH_MAX + XBOX_CFG_PATH_LEN];
    snprintf(tmp, sizeof(tmp), "%s", path);
    char *slash = strrchr(tmp, '\\');
    if (slash) {
        *slash = '\0';
        ensure_dir(tmp);
    }
}

static int zip_open_current(ZipCtx *z)
{
    if (is_bad_zip_path(z->name)) {
        snprintf(z->err, sizeof(z->err), "Bad ZIP path");
        return -1;
    }

    char path[XBOX_PATH_MAX + XBOX_CFG_PATH_LEN];
    make_output_path(z, z->name, path, sizeof(path));

    int n = (int)strlen(path);
    if (n > 0 && (path[n - 1] == '\\' || path[n - 1] == '/')) {
        ensure_dir(path);
        z->out = INVALID_HANDLE_VALUE;
        return 0;
    }

    ensure_parent_dir(path);
    z->out = CreateFileA(path, GENERIC_WRITE, 0, NULL,
                         CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (z->out == INVALID_HANDLE_VALUE) {
        snprintf(z->err, sizeof(z->err), "Could not write %s", z->name);
        return -1;
    }
    z->files++;
    if (z->progress && (z->files % 8) == 1) {
        char msg[96];
        snprintf(msg, sizeof(msg), "Installing %u file(s)...", (unsigned)z->files);
        if (zip_progress(z, msg) != 0) return -1;
    }
    return 0;
}

static int zip_consume(ZipCtx *z, const uint8_t *data, size_t size)
{
    size_t off = 0;
    while (off < size) {
        if (z->state == ZIP_STATE_DONE) return 0;
        if (z->state == ZIP_STATE_ERROR) return -1;

        if (z->state == ZIP_STATE_HEADER) {
            int need = 30 - z->header_got;
            int take = (int)(size - off);
            if (take > need) take = need;
            memcpy(z->header + z->header_got, data + off, take);
            z->header_got += take;
            off += take;
            if (z->header_got < 30) continue;

            uint32_t sig = rd32(z->header);
            if (sig == ZIP_CENTRAL_SIG || sig == ZIP_END_SIG) {
                z->state = ZIP_STATE_DONE;
                return 0;
            }
            if (sig != ZIP_LOCAL_SIG) {
                snprintf(z->err, sizeof(z->err), "Bad ZIP signature");
                z->state = ZIP_STATE_ERROR;
                return -1;
            }
            z->flags = rd16(z->header + 6);
            z->method = rd16(z->header + 8);
            z->remaining = rd32(z->header + 18);
            z->name_len = rd16(z->header + 26);
            z->extra_len = rd16(z->header + 28);
            z->name_got = 0;
            z->name[0] = '\0';
            if ((z->flags & 0x08) || z->method != 0 || z->remaining == 0xFFFFFFFFu) {
                snprintf(z->err, sizeof(z->err), "Unsupported ZIP entry");
                z->state = ZIP_STATE_ERROR;
                return -1;
            }
            if (z->name_len <= 0 || z->name_len >= (int)sizeof(z->name)) {
                snprintf(z->err, sizeof(z->err), "ZIP path too long");
                z->state = ZIP_STATE_ERROR;
                return -1;
            }
            z->state = ZIP_STATE_NAME;
        } else if (z->state == ZIP_STATE_NAME) {
            int total = z->name_len + z->extra_len;
            int need = total - z->name_got;
            int take = (int)(size - off);
            if (take > need) take = need;
            for (int i = 0; i < take; i++) {
                int pos = z->name_got + i;
                if (pos < z->name_len) z->name[pos] = (char)data[off + i];
            }
            z->name_got += take;
            off += take;
            if (z->name_got < total) continue;
            z->name[z->name_len] = '\0';
            if (zip_open_current(z) != 0) {
                z->state = ZIP_STATE_ERROR;
                return -1;
            }
            z->state = ZIP_STATE_FILE;
        } else if (z->state == ZIP_STATE_FILE) {
            uint32_t take = z->remaining;
            if (take > size - off) take = (uint32_t)(size - off);
            if (take > 0 && z->out != INVALID_HANDLE_VALUE) {
                DWORD written = 0;
                if (!WriteFile(z->out, data + off, take, &written, NULL) ||
                    written != take) {
                    snprintf(z->err, sizeof(z->err), "Write failed");
                    z->state = ZIP_STATE_ERROR;
                    return -1;
                }
            }
            z->remaining -= take;
            off += take;
            if (z->remaining == 0) {
                if (z->out != INVALID_HANDLE_VALUE) {
                    CloseHandle(z->out);
                    z->out = INVALID_HANDLE_VALUE;
                }
                z->header_got = 0;
                z->state = ZIP_STATE_HEADER;
            }
        }
    }
    return 0;
}

static int zip_http_write(void *ctx, const uint8_t *data, size_t size)
{
    ZipCtx *z = (ZipCtx *)ctx;
    z->http_done += (uint64_t)size;
    if ((z->http_done & 0x000FFFFFULL) < (uint64_t)size &&
        zip_progress(z, "Downloading game ZIP...") != 0) {
        return -1;
    }
    return zip_consume(z, data, size);
}

int games_download_rom(const XboxConfig *cfg,
                       const XboxRomEntry *rom,
                       XboxGameFormat fmt,
                       GameProgressFn progress,
                       void *progress_user,
                       char *err,
                       int err_len)
{
    if (!cfg || !rom) return -1;
    if (games_mount_target(cfg, err, err_len) != 0) return -1;

    ZipCtx z;
    memset(&z, 0, sizeof(z));
    z.state = ZIP_STATE_HEADER;
    z.out = INVALID_HANDLE_VALUE;
    z.progress = progress;
    z.progress_user = progress_user;
    make_target_dir(cfg, rom, z.target_dir, sizeof(z.target_dir));
    // A cancelled download removes the folder only if it created it, so an
    // earlier install of the same game is never wiped by a cancel.
    int existed = GetFileAttributesA(z.target_dir) != INVALID_FILE_ATTRIBUTES;
    if (ensure_dir(z.target_dir) != 0) {
        if (err) snprintf(err, err_len, "Could not create game dir");
        return -1;
    }

    char url[640];
    char path[256];
    snprintf(path, sizeof(path), "/api/v1/roms/%s?extract=%s",
             rom->rom_id, games_format_name(fmt));
    join_url(cfg->server_url, path, url, sizeof(url));

    if (progress) {
        progress(fmt == XBOX_GAME_FORMAT_FOLDER
                     ? "Downloading extracted folder ZIP..."
                     : "Downloading CCI bundle ZIP...",
                 0, 0, progress_user);
    }

    int code = http_get_stream(url, cfg->api_key, cfg->console_id,
                               zip_http_write, &z, &z.http_total);
    if (z.out != INVALID_HANDLE_VALUE) {
        CloseHandle(z.out);
        z.out = INVALID_HANDLE_VALUE;
    }
    if (z.cancelled) {
        if (!existed) delete_tree(z.target_dir, NULL, 0);
        if (err) snprintf(err, err_len, "Download cancelled");
        return GAMES_DOWNLOAD_CANCELLED;
    }
    if (code < 0) {
        if (err) snprintf(err, err_len, "%s",
                          z.err[0] ? z.err : "Download failed");
        return -1;
    }
    if (code < 200 || code >= 300) {
        if (err) snprintf(err, err_len, "ROM download HTTP %d", code);
        return -1;
    }
    if (z.state == ZIP_STATE_ERROR || z.files == 0) {
        if (err) snprintf(err, err_len, "%s",
                          z.err[0] ? z.err : "ZIP had no files");
        return -1;
    }
    if (progress) {
        progress("Game download complete", z.http_done, z.http_total, progress_user);
    }
    return 0;
}

static void clean_install_dir(const XboxConfig *cfg, char *out, int out_len)
{
    const char *base = install_dir_for(cfg);
    snprintf(out, out_len, "%s", base);
    int len = (int)strlen(out);
    while (len > 3 && (out[len - 1] == '\\' || out[len - 1] == '/')) {
        out[--len] = '\0';
    }
}

static int cmp_ci(const char *a, const char *b)
{
    if (!a) a = "";
    if (!b) b = "";
    while (*a && *b) {
        int ca = tolower((unsigned char)*a);
        int cb = tolower((unsigned char)*b);
        if (ca != cb) return ca - cb;
        a++;
        b++;
    }
    return (unsigned char)*a - (unsigned char)*b;
}

static int installed_cmp(const void *va, const void *vb)
{
    const XboxInstalledGame *a = (const XboxInstalledGame *)va;
    const XboxInstalledGame *b = (const XboxInstalledGame *)vb;
    return cmp_ci(a->name, b->name);
}

static void scan_installed_tree(const char *root, XboxInstalledGame *game)
{
    char search[XBOX_INSTALLED_PATH_MAX * 2];
    int n = snprintf(search, sizeof(search), "%s\\*", root);
    if (n <= 0 || n >= (int)sizeof(search)) return;

    WIN32_FIND_DATAA fd;
    HANDLE h = FindFirstFileA(search, &fd);
    if (h == INVALID_HANDLE_VALUE) return;

    do {
        if (strcmp(fd.cFileName, ".") == 0 ||
            strcmp(fd.cFileName, "..") == 0) {
            continue;
        }

        char child[XBOX_INSTALLED_PATH_MAX * 2];
        n = snprintf(child, sizeof(child), "%s\\%s", root, fd.cFileName);
        if (n <= 0 || n >= (int)sizeof(child)) continue;

        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
            game->dir_count++;
            scan_installed_tree(child, game);
        } else {
            uint64_t size = ((uint64_t)fd.nFileSizeHigh << 32)
                          | (uint64_t)fd.nFileSizeLow;
            game->size += size;
            game->file_count++;
        }
    } while (FindNextFileA(h, &fd));

    FindClose(h);
}

int games_scan_installed(const XboxConfig *cfg,
                         XboxInstalledGameList *out,
                         char *err,
                         int err_len)
{
    if (!out) return -1;
    memset(out, 0, sizeof(*out));

    if (games_mount_target(cfg, err, err_len) != 0) return -1;

    char base[XBOX_INSTALLED_PATH_MAX];
    clean_install_dir(cfg, base, sizeof(base));

    char search[XBOX_INSTALLED_PATH_MAX * 2];
    int n = snprintf(search, sizeof(search), "%s\\*", base);
    if (n <= 0 || n >= (int)sizeof(search)) {
        if (err) snprintf(err, err_len, "Install path too long");
        return -1;
    }

    WIN32_FIND_DATAA fd;
    HANDLE h = FindFirstFileA(search, &fd);
    if (h == INVALID_HANDLE_VALUE) {
        DWORD code = GetLastError();
        if (code == ERROR_FILE_NOT_FOUND || code == ERROR_NO_MORE_FILES) {
            return 0;
        }
        if (err) snprintf(err, err_len, "Could not read %s", base);
        return -1;
    }

    do {
        if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)) continue;
        if (strcmp(fd.cFileName, ".") == 0 ||
            strcmp(fd.cFileName, "..") == 0) {
            continue;
        }
        if (out->count >= XBOX_MAX_INSTALLED_GAMES) break;

        XboxInstalledGame *game = &out->games[out->count++];
        snprintf(game->name, sizeof(game->name), "%s", fd.cFileName);
        n = snprintf(game->path, sizeof(game->path),
                     "%s\\%s", base, fd.cFileName);
        if (n <= 0 || n >= (int)sizeof(game->path)) {
            out->count--;
            continue;
        }
        game->dir_count = 1;
        scan_installed_tree(game->path, game);
        out->total_size += game->size;
    } while (FindNextFileA(h, &fd));

    FindClose(h);

    if (out->count > 1) {
        qsort(out->games, out->count, sizeof(out->games[0]), installed_cmp);
    }
    return 0;
}

static int path_has_parent_ref(const char *path)
{
    if (!path) return 1;
    if (strstr(path, "..")) return 1;
    return 0;
}

static int path_is_child_of_base(const char *path, const char *base)
{
    if (!path || !base || !path[0] || !base[0]) return 0;

    int blen = (int)strlen(base);
    for (int i = 0; i < blen; i++) {
        if (!path[i]) return 0;
        if (tolower((unsigned char)path[i]) !=
            tolower((unsigned char)base[i])) {
            return 0;
        }
    }

    char next = path[blen];
    if (next != '\\' && next != '/') return 0;
    if (path[blen + 1] == '\0') return 0;
    return !path_has_parent_ref(path + blen + 1);
}

static int delete_tree(const char *path, char *err, int err_len)
{
    char search[XBOX_INSTALLED_PATH_MAX * 2];
    int n = snprintf(search, sizeof(search), "%s\\*", path);
    if (n <= 0 || n >= (int)sizeof(search)) {
        if (err) snprintf(err, err_len, "Path too long");
        return -1;
    }

    WIN32_FIND_DATAA fd;
    HANDLE h = FindFirstFileA(search, &fd);
    if (h != INVALID_HANDLE_VALUE) {
        do {
            if (strcmp(fd.cFileName, ".") == 0 ||
                strcmp(fd.cFileName, "..") == 0) {
                continue;
            }

            char child[XBOX_INSTALLED_PATH_MAX * 2];
            n = snprintf(child, sizeof(child), "%s\\%s", path, fd.cFileName);
            if (n <= 0 || n >= (int)sizeof(child)) {
                FindClose(h);
                if (err) snprintf(err, err_len, "Path too long");
                return -1;
            }

            if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
                if (delete_tree(child, err, err_len) != 0) {
                    FindClose(h);
                    return -1;
                }
            } else {
                SetFileAttributesA(child, FILE_ATTRIBUTE_NORMAL);
                if (!DeleteFileA(child)) {
                    DWORD code = GetLastError();
                    FindClose(h);
                    if (err) snprintf(err, err_len,
                                      "Delete failed %s (%lu)",
                                      fd.cFileName,
                                      (unsigned long)code);
                    return -1;
                }
            }
        } while (FindNextFileA(h, &fd));
        FindClose(h);
    }

    SetFileAttributesA(path, FILE_ATTRIBUTE_NORMAL);
    if (!RemoveDirectoryA(path)) {
        DWORD code = GetLastError();
        if (err) snprintf(err, err_len,
                          "Remove failed %s (%lu)",
                          path,
                          (unsigned long)code);
        return -1;
    }
    return 0;
}

int games_uninstall_installed(const XboxConfig *cfg,
                              const XboxInstalledGame *game,
                              char *err,
                              int err_len)
{
    if (!game || !game->path[0]) {
        if (err) snprintf(err, err_len, "No installed game selected");
        return -1;
    }

    char base[XBOX_INSTALLED_PATH_MAX];
    clean_install_dir(cfg, base, sizeof(base));
    if (!path_is_child_of_base(game->path, base)) {
        if (err) snprintf(err, err_len,
                          "Refusing to delete outside %s", base);
        return -1;
    }

    return delete_tree(game->path, err, err_len);
}

int games_get_f_drive_space(XboxDriveSpace *out, char *err, int err_len)
{
    if (!out) return -1;
    memset(out, 0, sizeof(*out));

    if (!nxIsDriveMounted('F')) {
        nxMountDrive('F', "\\Device\\Harddisk0\\Partition6\\");
    }

    DWORD sectors_per_cluster = 0;
    DWORD bytes_per_sector = 0;
    DWORD free_clusters = 0;
    DWORD total_clusters = 0;
    if (!GetDiskFreeSpaceA("F:\\",
                           &sectors_per_cluster,
                           &bytes_per_sector,
                           &free_clusters,
                           &total_clusters)) {
        if (err) snprintf(err, err_len, "Could not read F: disk space");
        return -1;
    }

    uint64_t cluster_size = (uint64_t)sectors_per_cluster
                          * (uint64_t)bytes_per_sector;
    out->free_bytes = cluster_size * (uint64_t)free_clusters;
    out->total_bytes = cluster_size * (uint64_t)total_clusters;
    return 0;
}
