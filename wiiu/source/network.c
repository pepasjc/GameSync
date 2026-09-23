/*
 * network.c — Wii U network bring-up + REST wrappers.
 *
 * wut's startup code initialises nsysnet's socket library, so bring-up is
 * only about learning whether the console actually has an address: ACInitialize
 * + ACGetAssignedAddress.  Everything else is a thin layer over http.c.
 */

#include "wiiunet.h"
#include "bundle.h"
#include "http.h"
#include "iowrite.h"
#include "state.h"

#include <ctype.h>
#include <errno.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include <coreinit/core.h>
#include <nn/ac.h>

static NetProgress64Fn g_progress_cb   = NULL;
static uint64_t        g_progress_base = 0;
static char            g_last_error[256] = {0};

const char *network_last_error(void) { return g_last_error; }

static void net_err_clear(void) { g_last_error[0] = '\0'; }
static void net_err_set(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(g_last_error, sizeof(g_last_error), fmt, ap);
    va_end(ap);
}

void network_set_progress64_cb(NetProgress64Fn cb) { g_progress_cb = cb; }

static int progress_bridge(uint64_t done, uint64_t total) {
    if (g_progress_cb) {
        uint64_t abs_done  = g_progress_base + done;
        uint64_t abs_total = total > 0 ? g_progress_base + total : 0;
        return g_progress_cb(abs_done, abs_total);
    }
    return 0;
}

int network_init(SyncState *state) {
    if (!state) return -1;
    net_err_clear();
    state->net_ready = false;
    snprintf(state->ip, sizeof(state->ip), "0.0.0.0");

    ACInitialize();
    ACConnect();

    uint32_t ip = 0;
    if (NNResult_IsFailure(ACGetAssignedAddress(&ip)) || ip == 0) {
        net_err_set("no network connection (check System Settings)");
        snprintf(state->ip, sizeof(state->ip), "no-net");
        return -1;
    }

    snprintf(state->ip, sizeof(state->ip), "%u.%u.%u.%u",
             (unsigned)((ip >> 24) & 0xFF), (unsigned)((ip >> 16) & 0xFF),
             (unsigned)((ip >>  8) & 0xFF), (unsigned)(ip & 0xFF));
    state->net_ready = true;
    return 0;
}

void network_shutdown(void) {
    ACFinalize();
}

bool network_is_ready(const SyncState *state) {
    return state && state->net_ready;
}

bool network_check_server(const SyncState *state) {
    HttpRequest req = {0};
    req.server_url = state->server_url;
    req.api_key    = state->api_key;
    req.console_id = state->console_id;
    req.path       = "/api/v1/status";
    req.method     = "GET";
    req.header_timeout_ms = HTTP_API_TIMEOUT_MS;

    static uint8_t buf[1024];
    int status = 0;
    int n = http_get_buf(&req, buf, sizeof(buf), &status);
    return (n >= 0 && status == 200);
}

/* ---- ROM catalog / downloads ---- */

int network_fetch_rom_catalog(const SyncState *state,
                              const char *system_code,
                              int offset, int limit,
                              char *out, uint32_t out_size,
                              int *status_out)
{
    if (!network_is_ready(state)) {
        if (status_out) *status_out = 0;
        return -100;
    }

    char path[256];
    snprintf(path, sizeof(path),
             "/api/v1/roms?system=%s&offset=%d&limit=%d",
             system_code ? system_code : "GC", offset, limit);

    HttpRequest req = {0};
    req.server_url = state->server_url;
    req.api_key    = state->api_key;
    req.console_id = state->console_id;
    req.path       = path;
    req.method     = "GET";
    req.header_timeout_ms = HTTP_API_TIMEOUT_MS;

    return http_get_buf(&req, (uint8_t *)out, out_size, status_out);
}

int network_download_path_resumable(const SyncState *state,
                                    const char *path,
                                    const char *target_path,
                                    uint64_t start_offset,
                                    uint64_t *total_out)
{
    if (total_out) *total_out = 0;

    char part[SAVE_DIR_LEN + 8];
    snprintf(part, sizeof(part), "%s.part", target_path);

    FILE *fp = fopen(part, start_offset > 0 ? "ab" : "wb");
    if (!fp) return -2;

    HttpRequest req = {0};
    req.server_url  = state->server_url;
    req.api_key     = state->api_key;
    req.console_id  = state->console_id;
    req.path        = path;
    req.method      = "GET";
    req.range_start = start_offset;
    /* On the download worker thread: blocking recv hot path; the main thread
     * enforces stall/cancel via http_abort_active().  On the main thread
     * (fallback path) nobody could do that, so keep the pumpable
     * select-based receive there. */
    req.io_blocking = !OSIsMainCore();

    HttpResponseInfo info;
    g_progress_base = start_offset;

    /* Writes go through a background thread (iowrite.c) so the socket keeps
     * draining while the SD/USB write is in flight — on one thread the
     * transfer runs at the serial sum of the two.  The writer thread hands
     * the filesystem 1 MB aligned blocks, so stdio buffering would only add
     * a copy. */
    IoWriter iow;
    int rc;
    if (iow_start(&iow, fp) == 0) {
        setvbuf(fp, NULL, _IONBF, 0);
        rc = http_get_stream_cb(&req, NULL, iow_stream_writer, &iow,
                                progress_bridge, &info);
        int werr = iow_finish(&iow);
        if (werr != 0 && rc == 0) rc = -7;
    } else {
        /* Allocation failed — synchronous fallback. */
        static char io_buf[256 * 1024];
        setvbuf(fp, io_buf, _IOFBF, sizeof(io_buf));
        rc = http_get_stream(&req, fp, progress_bridge, &info);
    }
    g_progress_base = 0;
    fclose(fp);

    if (total_out && info.content_length > 0)
        *total_out = info.content_length + start_offset;

    if (rc == 1) return 1;
    if (rc < 0) {
        if (info.status > 0 && (info.status < 200 || info.status >= 300)) return -3;
        return -1;
    }

    if (rename(part, target_path) != 0) {
        remove(target_path);
        if (rename(part, target_path) != 0) return -2;
    }
    return 0;
}

int network_download_rom_resumable(const SyncState *state,
                                   const char *rom_id,
                                   const char *extract_fmt,
                                   const char *target_path,
                                   uint64_t start_offset,
                                   uint64_t *total_out)
{
    char path[512];
    if (extract_fmt && extract_fmt[0])
        snprintf(path, sizeof(path), "/api/v1/roms/%s?extract=%s", rom_id, extract_fmt);
    else
        snprintf(path, sizeof(path), "/api/v1/roms/%s", rom_id);

    return network_download_path_resumable(state, path, target_path,
                                           start_offset, total_out);
}

/* ---- tiny JSON helpers (shared by the plan / manifest / name parsers) ---- */

static const char *json_value_start(const char *body, const char *key) {
    if (!body || !key) return NULL;
    char needle[64];
    snprintf(needle, sizeof(needle), "\"%s\"", key);
    const char *p = strstr(body, needle);
    if (!p) return NULL;
    p = strchr(p, ':');
    if (!p) return NULL;
    p++;
    while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') p++;
    return p;
}

static int json_copy_string(const char *body, const char *key,
                            char *out, int out_len) {
    if (!out || out_len <= 0) return -1;
    out[0] = '\0';
    const char *p = json_value_start(body, key);
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

/* Append into a fixed buffer; bails on overflow rather than reallocating. */
static int append(char *buf, int *off, int cap, const char *fmt, ...) {
    if (*off >= cap) return -1;
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf + *off, (size_t)(cap - *off), fmt, ap);
    va_end(ap);
    if (n < 0 || n >= cap - *off) return -1;
    *off += n;
    return 0;
}

/* Find ``"name": [ ... ]`` and copy each quoted string into ``out_ids``. */
static int parse_string_array(const char *body, const char *name,
                              char (*out_ids)[TITLE_ID_LEN], int max) {
    char key[64];
    snprintf(key, sizeof(key), "\"%s\"", name);
    const char *p = strstr(body, key);
    if (!p) return 0;
    p = strchr(p, '[');
    if (!p) return 0;
    p++;
    int count = 0;
    while (*p && *p != ']' && count < max) {
        if (*p == '"') {
            const char *start = ++p;
            while (*p && *p != '"') p++;
            int len = (int)(p - start);
            if (len > TITLE_ID_LEN - 1) len = TITLE_ID_LEN - 1;
            memcpy(out_ids[count], start, (size_t)len);
            out_ids[count][len] = '\0';
            count++;
            if (*p == '"') p++;
        } else {
            p++;
        }
    }
    return count;
}

/* ---- WBFS manifest ---- */

int network_fetch_wbfs_manifest(const SyncState *state, const char *rom_id,
                                char *scratch, uint32_t scratch_size,
                                WbfsManifest *out) {
    if (!state || !rom_id || !out || !scratch) return -1;
    memset(out, 0, sizeof(*out));

    char path[256];
    snprintf(path, sizeof(path), "/api/v1/roms/%s/wbfs-manifest", rom_id);

    HttpRequest req = {0};
    req.server_url = state->server_url;
    req.api_key    = state->api_key;
    req.console_id = state->console_id;
    req.path       = path;
    req.method     = "GET";

    int status = 0;
    int n = http_get_buf(&req, (uint8_t *)scratch, scratch_size, &status);
    if (n < 0 || status != 200) {
        snprintf(out->last_error, sizeof(out->last_error),
                 "wbfs-manifest failed (HTTP %d, n=%d)", status, n);
        return -1;
    }

    json_copy_string(scratch, "game_id", out->game_id, sizeof(out->game_id));
    json_copy_string(scratch, "name",    out->name,    sizeof(out->name));

    /* files: [ {"name": "...", "size": N}, ... ] */
    const char *p = strstr(scratch, "\"files\"");
    if (p) p = strchr(p, '[');
    while (p && out->part_count < WBFS_MAX_PARTS) {
        p = strchr(p, '{');
        if (!p) break;
        const char *obj_end = strchr(p, '}');
        if (!obj_end) break;

        /* Bound the search to this object by temporarily terminating it. */
        size_t len = (size_t)(obj_end - p) + 1;
        char obj[256];
        if (len >= sizeof(obj)) len = sizeof(obj) - 1;
        memcpy(obj, p, len);
        obj[len] = '\0';

        WbfsPart *part = &out->parts[out->part_count];
        if (json_copy_string(obj, "name", part->name, sizeof(part->name)) == 0) {
            const char *sv = json_value_start(obj, "size");
            part->size = sv ? strtoull(sv, NULL, 10) : 0;
            out->part_count++;
        }
        p = obj_end + 1;
    }

    if (out->part_count == 0) {
        snprintf(out->last_error, sizeof(out->last_error),
                 "wbfs-manifest returned no parts");
        return -1;
    }
    return 0;
}

int network_fetch_bundle_manifest(const SyncState *state, const char *rom_id,
                                  char *scratch, uint32_t scratch_size,
                                  BundleManifest *out) {
    if (!state || !rom_id || !out || !scratch) return -1;
    memset(out, 0, sizeof(*out));

    char path[256];
    snprintf(path, sizeof(path), "/api/v1/roms/%s/manifest", rom_id);

    HttpRequest req = {0};
    req.server_url = state->server_url;
    req.api_key    = state->api_key;
    req.console_id = state->console_id;
    req.path       = path;
    req.method     = "GET";

    int status = 0;
    int n = http_get_buf(&req, (uint8_t *)scratch, scratch_size, &status);
    if (n < 0 || status != 200) {
        snprintf(out->last_error, sizeof(out->last_error),
                 "manifest failed (HTTP %d, n=%d)", status, n);
        return -1;
    }

    json_copy_string(scratch, "name", out->name, sizeof(out->name));

    /* files: [ {"name": "00000000.app", "size": N}, ... ].  A WUP set is
     * 20-50 entries; anything past BUNDLE_MAX_FILES is reported rather than
     * silently truncated, because a partial install is worse than none. */
    const char *p = strstr(scratch, "\"files\"");
    if (p) p = strchr(p, '[');
    while (p) {
        p = strchr(p, '{');
        if (!p) break;
        const char *obj_end = strchr(p, '}');
        if (!obj_end) break;

        size_t len = (size_t)(obj_end - p) + 1;
        char obj[512];
        if (len >= sizeof(obj)) len = sizeof(obj) - 1;
        memcpy(obj, p, len);
        obj[len] = '\0';

        if (out->file_count >= BUNDLE_MAX_FILES) {
            snprintf(out->last_error, sizeof(out->last_error),
                     "bundle has more than %d files", BUNDLE_MAX_FILES);
            return -1;
        }

        BundleFile *bf = &out->files[out->file_count];
        if (json_copy_string(obj, "name", bf->name, sizeof(bf->name)) == 0) {
            const char *sv = json_value_start(obj, "size");
            bf->size = sv ? strtoull(sv, NULL, 10) : 0;
            out->total_size += bf->size;
            out->file_count++;
        }
        p = obj_end + 1;
    }

    if (out->file_count == 0) {
        snprintf(out->last_error, sizeof(out->last_error),
                 "manifest returned no files");
        return -1;
    }
    return 0;
}

/* ---- smart sync ---- */

void sync_plan_free(SyncPlan *p) {
    if (!p) return;
    free(p->upload_ids);
    free(p->download_ids);
    free(p->conflict_ids);
    free(p->up_to_date_ids);
    free(p->server_only_ids);
    free(p->server_only_names);
    memset(p, 0, sizeof(*p));
}

/* Walk a JSON ``"names": { "tid": "Name", ... }`` block. */
static void parse_names_object(const char *body,
                               const char (*ids)[TITLE_ID_LEN],
                               int count,
                               char (*names)[SAVE_NAME_MAX]) {
    for (int i = 0; i < count; i++) names[i][0] = '\0';

    const char *p = strstr(body, "\"names\"");
    if (!p) return;
    p = strchr(p, '{');
    if (!p) return;
    p++;

    while (*p && *p != '}') {
        while (*p && *p != '"' && *p != '}') p++;
        if (*p != '"') break;
        p++;
        const char *kstart = p;
        while (*p && *p != '"') p++;
        if (*p != '"') break;
        int klen = (int)(p - kstart);
        char key[TITLE_ID_LEN];
        if (klen > TITLE_ID_LEN - 1) klen = TITLE_ID_LEN - 1;
        memcpy(key, kstart, (size_t)klen);
        key[klen] = '\0';
        p++;
        while (*p && *p != ':') p++;
        if (!*p) break;
        p++;
        while (*p && *p != '"') p++;
        if (!*p) break;
        p++;
        const char *vstart = p;
        while (*p) {
            if (*p == '\\' && p[1]) { p += 2; continue; }
            if (*p == '"') break;
            p++;
        }
        int vlen = (int)(p - vstart);

        for (int i = 0; i < count; i++) {
            if (strcmp(ids[i], key) != 0) continue;
            int oi = 0;
            for (int j = 0; j < vlen && oi < SAVE_NAME_MAX - 1; j++) {
                char c = vstart[j];
                if (c == '\\' && j + 1 < vlen) c = vstart[++j];
                names[i][oi++] = c;
            }
            names[i][oi] = '\0';
            break;
        }
        if (*p == '"') p++;
    }
}

/* Escape a meta.xml longname for a JSON string body.  Names carry quotes and
 * the odd control character; an unescaped one would truncate the request into
 * malformed JSON and lose every name after it. */
static void json_escape(const char *in, char *out, size_t out_size) {
    size_t j = 0;
    for (const unsigned char *p = (const unsigned char *)in; *p; p++) {
        unsigned char c = *p;
        const char *esc = NULL;
        char buf[8];
        switch (c) {
            case '"':  esc = "\\\""; break;
            case '\\': esc = "\\\\"; break;
            case '\n': esc = "\\n";  break;
            case '\r': esc = "\\r";  break;
            case '\t': esc = "\\t";  break;
            default:
                if (c < 0x20) {
                    snprintf(buf, sizeof(buf), "\\u%04x", c);
                    esc = buf;
                }
                break;
        }
        size_t need = esc ? strlen(esc) : 1;
        if (j + need + 1 > out_size) break;
        if (esc) {
            memcpy(out + j, esc, need);
            j += need;
        } else {
            out[j++] = (char)c;
        }
    }
    out[j] = '\0';
}

int network_push_name_hints(const SyncState *state, const SaveTitleList *list) {
    if (!state || !list || list->title_count <= 0) return -1;
    net_err_clear();

    int   cap  = 64 * 1024;
    char *json = (char *)malloc((size_t)cap);
    if (!json) return -1;
    int   off = 0;
    int   sent = 0;

    if (append(json, &off, cap, "{\"codes\":{") != 0) goto fail;
    for (int i = 0; i < list->title_count; i++) {
        const SaveTitle *t = &list->titles[i];
        if (!t->game_code[0]) continue;
        if (append(json, &off, cap, "%s\"%s\":\"%s\"", sent ? "," : "",
                   t->title_id, t->game_code) != 0) goto fail;
        sent++;
    }

    int named = 0;
    if (append(json, &off, cap, "},\"names\":{") != 0) goto fail;
    for (int i = 0; i < list->title_count; i++) {
        const SaveTitle *t = &list->titles[i];
        if (!t->name[0]) continue;
        char esc[2 * SAVE_NAME_MAX];
        json_escape(t->name, esc, sizeof(esc));
        if (append(json, &off, cap, "%s\"%s\":\"%s\"", named ? "," : "",
                   t->title_id, esc) != 0) goto fail;
        named++;
    }
    if (append(json, &off, cap, "}}") != 0) goto fail;

    /* Nothing worth telling the server (vWii-only list, or no meta.xml). */
    if (sent == 0 && named == 0) { free(json); return 0; }

    HttpRequest req = {0};
    req.server_url        = state->server_url;
    req.api_key           = state->api_key;
    req.console_id        = state->console_id;
    req.path              = "/api/v1/titles/update_names";
    req.method            = "POST";
    req.header_timeout_ms = HTTP_API_TIMEOUT_MS;
    req.body              = (const uint8_t *)json;
    req.body_len          = (uint32_t)off;
    req.body_content_type = "application/json";

    uint32_t resp_cap = 4096;
    char *resp = (char *)malloc(resp_cap);
    if (!resp) goto fail;

    int status = 0;
    int n = http_get_buf(&req, (uint8_t *)resp, resp_cap, &status);
    free(resp);
    free(json);

    if (n < 0 || status != 200) {
        net_err_set("name hints: HTTP %d", status);
        return -1;
    }
    return 0;

fail:
    free(json);
    return -1;
}

int network_fetch_names(const SyncState *state,
                        const char (*ids)[TITLE_ID_LEN],
                        int count,
                        char (*names)[SAVE_NAME_MAX]) {
    if (!state || !ids || !names || count <= 0) return -1;

    int   cap  = 64 * 1024;
    char *json = (char *)malloc((size_t)cap);
    if (!json) return -1;
    int   off  = 0;

    if (append(json, &off, cap, "{\"codes\":[") != 0) { free(json); return -1; }
    for (int i = 0; i < count; i++) {
        if (append(json, &off, cap, "%s\"%s\"", i == 0 ? "" : ",", ids[i]) != 0) {
            free(json); return -1;
        }
    }
    if (append(json, &off, cap, "]}") != 0) { free(json); return -1; }

    HttpRequest req = {0};
    req.server_url        = state->server_url;
    req.api_key           = state->api_key;
    req.console_id        = state->console_id;
    req.path              = "/api/v1/titles/names";
    req.method            = "POST";
    req.header_timeout_ms = HTTP_API_TIMEOUT_MS;
    req.body              = (const uint8_t *)json;
    req.body_len          = (uint32_t)off;
    req.body_content_type = "application/json";

    uint32_t resp_cap = 64 * 1024;
    char *resp = (char *)malloc(resp_cap);
    if (!resp) { free(json); return -1; }

    int status = 0;
    int n = http_get_buf(&req, (uint8_t *)resp, resp_cap, &status);
    free(json);

    if (n < 0 || status != 200) {
        for (int i = 0; i < count; i++) names[i][0] = '\0';
        free(resp);
        return -1;
    }
    parse_names_object(resp, ids, count, names);
    free(resp);
    return 0;
}

int network_sync_plan(const SyncState *state,
                      const SaveTitleList *list,
                      const char *const *platforms, int platform_count,
                      SyncPlan *out) {
    if (!state || !list || !out) return -1;
    memset(out, 0, sizeof(*out));
    net_err_clear();

    int   cap  = 128 * 1024;
    char *json = (char *)malloc((size_t)cap);
    if (!json) return -1;
    int   off = 0;

    if (append(json, &off, cap, "{\"console_id\":\"%s\",\"platforms\":[",
               state->console_id) != 0) goto fail;
    for (int i = 0; i < platform_count; i++)
        if (append(json, &off, cap, "%s\"%s\"", i == 0 ? "" : ",", platforms[i]) != 0)
            goto fail;
    if (append(json, &off, cap, "],\"titles\":[") != 0) goto fail;

    for (int i = 0; i < list->title_count; i++) {
        const SaveTitle *t = &list->titles[i];

        char hash_hex[STATE_HASH_BUF];
        uint8_t hash_raw[32];
        if (!state_get_cached_save_hash(state, t, hash_hex)) {
            if (bundle_compute_save_hash(t, hash_raw, hash_hex) != 0) goto fail;
            state_set_cached_save_hash(state, t, hash_hex);
        }

        char last_hex[STATE_HASH_BUF] = "";
        int has_last = state_get_last_hash(state, t->title_id, last_hex);

        if (append(json, &off, cap, "%s{", i == 0 ? "" : ",") != 0) goto fail;
        if (append(json, &off, cap, "\"title_id\":\"%s\",", t->title_id) != 0) goto fail;
        if (append(json, &off, cap, "\"save_hash\":\"%s\",", hash_hex) != 0) goto fail;
        if (append(json, &off, cap, "\"timestamp\":%u,", (unsigned)t->latest_mtime) != 0) goto fail;
        if (append(json, &off, cap, "\"size\":%u,", (unsigned)t->total_size) != 0) goto fail;
        if (append(json, &off, cap, "\"console_id\":\"%s\",", state->console_id) != 0) goto fail;
        if (has_last) {
            if (append(json, &off, cap, "\"last_synced_hash\":\"%s\"", last_hex) != 0) goto fail;
        } else {
            if (append(json, &off, cap, "\"last_synced_hash\":null") != 0) goto fail;
        }
        if (append(json, &off, cap, "}") != 0) goto fail;
    }
    if (append(json, &off, cap, "]}") != 0) goto fail;

    HttpRequest req = {0};
    req.server_url        = state->server_url;
    req.api_key           = state->api_key;
    req.console_id        = state->console_id;
    req.path              = "/api/v1/sync";
    req.method            = "POST";
    req.header_timeout_ms = 4 * HTTP_API_TIMEOUT_MS;
    req.body              = (const uint8_t *)json;
    req.body_len          = (uint32_t)off;
    req.body_content_type = "application/json";

    uint32_t resp_cap = 128 * 1024;
    char *resp = (char *)malloc(resp_cap);
    if (!resp) goto fail;

    int status = 0;
    int n = http_get_buf(&req, (uint8_t *)resp, resp_cap, &status);
    free(json);
    json = NULL;

    if (n < 0 || status != 200) {
        net_err_set("sync plan: HTTP %d (n=%d)", status, n);
        free(resp);
        return -1;
    }

    out->upload_ids        = calloc(SYNC_MAX_TITLES, TITLE_ID_LEN);
    out->download_ids      = calloc(SYNC_MAX_TITLES, TITLE_ID_LEN);
    out->conflict_ids      = calloc(SYNC_MAX_TITLES, TITLE_ID_LEN);
    out->up_to_date_ids    = calloc(SYNC_MAX_TITLES, TITLE_ID_LEN);
    out->server_only_ids   = calloc(SYNC_MAX_TITLES, TITLE_ID_LEN);
    out->server_only_names = calloc(SYNC_MAX_TITLES, SAVE_NAME_MAX);
    if (!out->upload_ids || !out->download_ids || !out->conflict_ids ||
        !out->up_to_date_ids || !out->server_only_ids || !out->server_only_names) {
        free(resp);
        sync_plan_free(out);
        return -1;
    }

    out->upload_count      = parse_string_array(resp, "upload",      out->upload_ids,      SYNC_MAX_TITLES);
    out->download_count    = parse_string_array(resp, "download",    out->download_ids,    SYNC_MAX_TITLES);
    out->conflict_count    = parse_string_array(resp, "conflict",    out->conflict_ids,    SYNC_MAX_TITLES);
    out->up_to_date_count  = parse_string_array(resp, "up_to_date",  out->up_to_date_ids,  SYNC_MAX_TITLES);
    out->server_only_count = parse_string_array(resp, "server_only", out->server_only_ids, SYNC_MAX_TITLES);
    free(resp);

    if (out->server_only_count > 0)
        network_fetch_names(state,
                            (const char (*)[TITLE_ID_LEN])out->server_only_ids,
                            out->server_only_count,
                            out->server_only_names);
    return 0;

fail:
    free(json);
    sync_plan_free(out);
    return -1;
}

/* ---- bundle upload (chunked) / download (streamed) ---- */

typedef struct {
    HttpWriteFn write;
    void       *write_ctx;
} BundleHttpWriter;

static int bundle_http_write(void *ctx, const uint8_t *data, size_t size) {
    BundleHttpWriter *w = (BundleHttpWriter *)ctx;
    return w->write(w->write_ctx, data, size);
}

typedef struct {
    const SaveTitle *title;
    uint32_t         timestamp;
    char            *save_hash_hex;
} StreamUploadCtx;

static int upload_stream_producer(HttpWriteFn write, void *write_ctx, void *user) {
    StreamUploadCtx *u = (StreamUploadCtx *)user;
    BundleHttpWriter bw = { write, write_ctx };
    return bundle_stream_create(u->title, u->timestamp,
                                bundle_http_write, &bw, u->save_hash_hex);
}

/* Percent-encode a query-parameter value into ``out``.  Truncates rather than
 * overflowing; a clipped name is cosmetic, a smashed stack is not. */
static void url_encode(const char *in, char *out, size_t out_size) {
    static const char HEX[] = "0123456789ABCDEF";
    size_t j = 0;
    for (const unsigned char *p = (const unsigned char *)in; *p; p++) {
        unsigned char c = *p;
        int safe = isalnum(c) || c == '-' || c == '_' || c == '.' || c == '~';
        size_t need = safe ? 1 : 3;
        if (j + need + 1 > out_size) break;
        if (safe) {
            out[j++] = (char)c;
        } else {
            out[j++] = '%';
            out[j++] = HEX[c >> 4];
            out[j++] = HEX[c & 0x0F];
        }
    }
    out[j] = '\0';
}

int network_upload_save_stream(const SyncState *state,
                               const SaveTitle *title,
                               uint32_t timestamp,
                               int force,
                               char *save_hash_hex) {
    net_err_clear();
    if (!state || !title) return -1;
    if (save_hash_hex) save_hash_hex[0] = '\0';

    /* Name hints.  A 16-hex Wii U title id tells the server nothing about the
     * game, so a save uploaded without these is listed as raw hex by every
     * other client — the desktop app has no console NAND to read a name from.
     * The product code is preferred (it resolves to the DAT's canonical
     * name); the meta.xml longname is the fallback for titles the DAT lacks. */
    char code_q[32] = "";
    if (title->game_code[0]) {
        char enc[24];
        url_encode(title->game_code, enc, sizeof(enc));
        snprintf(code_q, sizeof(code_q), "&game_code=%s", enc);
    }
    char name_q[3 * SAVE_NAME_MAX + 16] = "";
    if (title->name[0]) {
        char enc[3 * SAVE_NAME_MAX];
        url_encode(title->name, enc, sizeof(enc));
        snprintf(name_q, sizeof(name_q), "&game_name=%s", enc);
    }

    char path[256 + sizeof(name_q)];
    snprintf(path, sizeof(path),
             "/api/v1/saves/%s?source=wiiu&console_id=%s%s%s%s",
             title->title_id, state->console_id, force ? "&force=true" : "",
             code_q, name_q);

    HttpRequest req = {0};
    req.server_url        = state->server_url;
    req.api_key           = state->api_key;
    req.console_id        = state->console_id;
    req.path              = path;
    req.method            = "POST";
    req.body_content_type = "application/octet-stream";
    req.header_timeout_ms = 4 * HTTP_API_TIMEOUT_MS;

    StreamUploadCtx ctx = { title, timestamp, save_hash_hex };
    uint8_t resp[512];
    int code = http_post_chunked(&req, upload_stream_producer, &ctx,
                                 resp, sizeof(resp));
    if (code < 200 || code >= 300)
        net_err_set("upload %s: HTTP %d", title->title_id, code);
    return code;
}

typedef struct {
    FILE    *fp;
    uint64_t written;
} DownloadFileCtx;

static int download_file_writer(void *user, const uint8_t *data, size_t size) {
    DownloadFileCtx *d = (DownloadFileCtx *)user;
    if (fwrite(data, 1, size, d->fp) != size) return -1;
    d->written += size;
    return 0;
}

int network_download_save_to_file(const SyncState *state,
                                  const char *title_id,
                                  const char *dest_path,
                                  uint64_t *out_size) {
    net_err_clear();
    if (out_size) *out_size = 0;
    if (!state || !title_id || !dest_path) return -1;

    FILE *fp = fopen(dest_path, "wb");
    if (!fp) {
        net_err_set("download %s: cannot create %s", title_id, dest_path);
        return -1;
    }

    char path[160];
    snprintf(path, sizeof(path), "/api/v1/saves/%s", title_id);

    HttpRequest req = {0};
    req.server_url = state->server_url;
    req.api_key    = state->api_key;
    req.console_id = state->console_id;
    req.path       = path;
    req.method     = "GET";
    req.header_timeout_ms = 4 * HTTP_API_TIMEOUT_MS;

    DownloadFileCtx ctx = { fp, 0 };
    HttpResponseInfo info;
    int rc = http_get_stream_cb(&req, NULL, download_file_writer, &ctx,
                                NULL, &info);
    fclose(fp);

    if (rc != 0) {
        remove(dest_path);
        net_err_set("download %s: rc=%d HTTP %d", title_id, rc, info.status);
        return info.status > 0 ? info.status : -1;
    }
    if (info.content_length > 0 && ctx.written != info.content_length) {
        remove(dest_path);
        net_err_set("download %s: short file %llu/%llu", title_id,
                    (unsigned long long)ctx.written,
                    (unsigned long long)info.content_length);
        return -1;
    }
    if (out_size) *out_size = ctx.written;
    return info.status;
}
