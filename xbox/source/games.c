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
    const char *dir = (cfg && cfg->game_install_dir[0])
        ? cfg->game_install_dir
        : "F:\\Games";
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
    const char *base = (cfg && cfg->game_install_dir[0])
        ? cfg->game_install_dir
        : "F:\\Games";
    int blen = (int)strlen(base);
    while (blen > 0 && (base[blen - 1] == '\\' || base[blen - 1] == '/')) {
        blen--;
    }
    snprintf(out, out_len, "%.*s\\%s", blen, base, folder);
}

static const char *json_value_start(const char *body, const char *key)
{
    char needle[48];
    snprintf(needle, sizeof(needle), "\"%s\"", key);
    const char *p = strstr(body, needle);
    if (!p) return NULL;
    p = strchr(p, ':');
    if (!p) return NULL;
    p++;
    while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') p++;
    return p;
}

static int json_copy_string_obj(const char *obj, const char *key,
                                char *out, int out_len)
{
    const char *p = json_value_start(obj, key);
    if (!out || out_len <= 0) return -1;
    out[0] = '\0';
    if (!p || *p != '"') return -1;
    p++;
    int i = 0;
    while (*p && *p != '"' && i < out_len - 1) {
        if (*p == '\\' && p[1]) p++;
        out[i++] = *p++;
    }
    out[i] = '\0';
    return i > 0 ? 0 : -1;
}

static int json_read_u64_obj(const char *obj, const char *key, uint64_t *out)
{
    const char *p = json_value_start(obj, key);
    unsigned long long v = 0;
    if (!p || sscanf(p, "%llu", &v) != 1) return -1;
    if (out) *out = (uint64_t)v;
    return 0;
}

static int json_read_bool_obj(const char *obj, const char *key, int *out)
{
    const char *p = json_value_start(obj, key);
    if (!p) return -1;
    if (strncmp(p, "true", 4) == 0 || *p == '1') {
        if (out) *out = 1;
        return 0;
    }
    if (strncmp(p, "false", 5) == 0 || *p == '0') {
        if (out) *out = 0;
        return 0;
    }
    return -1;
}

int games_fetch_catalog(const XboxConfig *cfg, XboxRomList *out,
                        char *err, int err_len)
{
    if (!cfg || !out) return -1;
    memset(out, 0, sizeof(*out));

    char url[512];
    join_url(cfg->server_url, "/api/v1/roms?system=XBOX&limit=20000",
             url, sizeof(url));
    HttpResponse rsp = http_request(url, HTTP_GET,
                                    cfg->api_key, cfg->console_id,
                                    NULL, NULL, 0);
    if (!rsp.success || !rsp.body) {
        if (err) snprintf(err, err_len, "ROM catalog HTTP %d", rsp.status_code);
        http_response_free(&rsp);
        return -1;
    }

    const char *body = (const char *)rsp.body;
    const char *p = strstr(body, "\"roms\"");
    if (p) p = strchr(p, '[');
    if (!p) {
        if (err) snprintf(err, err_len, "Bad ROM catalog response");
        http_response_free(&rsp);
        return -1;
    }
    p++;

    while (*p && out->count < XBOX_MAX_ROMS) {
        const char *open = strchr(p, '{');
        const char *close = strchr(p, '}');
        if (!open || !close || close < open) break;

        XboxRomEntry *r = &out->roms[out->count];
        json_copy_string_obj(open, "rom_id", r->rom_id, sizeof(r->rom_id));
        json_copy_string_obj(open, "name", r->name, sizeof(r->name));
        json_copy_string_obj(open, "filename", r->filename, sizeof(r->filename));
        json_read_u64_obj(open, "size", &r->size);
        json_read_bool_obj(open, "is_bundle", &r->is_bundle);
        if (r->rom_id[0] && r->name[0]) out->count++;
        p = close + 1;
    }

    http_response_free(&rsp);
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
} ZipCtx;

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
        z->progress(msg, z->http_done, z->http_total, z->progress_user);
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
    if (z->progress && (z->http_done & 0x000FFFFFULL) < (uint64_t)size) {
        z->progress("Downloading game ZIP...", z->http_done, z->http_total,
                    z->progress_user);
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
