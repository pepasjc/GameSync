/*
 * gcsaves.c — GameCube save sync through Nintendont virtual memory cards.
 *
 * Trimmed port of gc/source/saves.c: the CARD/EXI slot code is gone (a Wii U
 * has no memory-card slots) and the card-image scan targets Nintendont's
 * layout.  The wire protocol is byte-for-byte identical, so saves are
 * interchangeable with the GameCube client, Dolphin and the Android app.
 */

#include "gcsaves.h"
#include "http.h"

#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <unistd.h>

static uint8_t g_gci[GCSAVES_GCI_MAX];

void saves_title_id_from_gamecode(const char *gamecode, char *out, size_t out_size) {
    char code[5] = {0};
    for (int i = 0; i < 4 && gamecode[i] && gamecode[i] != ' '; i++)
        code[i] = (char)toupper((unsigned char)gamecode[i]);
    snprintf(out, out_size, "GC_%s", code);
}

/* ---- card-image scan ---- */

/* Same gate the GameCube client uses: 0.5-16 MB and a whole number of
 * 8 KB blocks.  Nintendont writes 16 Mbit (2 MB) cards by default but users
 * configure others, and per-game cards are named after the game code. */
static bool card_size_ok(uint32_t size) {
    return size >= 0x80000 && size <= 0x1000000 && (size % GC_BLOCK_SIZE) == 0;
}

static bool has_card_ext(const char *fname) {
    size_t n = strlen(fname);
    static const char *exts[] = { ".raw", ".gcp", ".mc", ".bin", ".mcd", NULL };
    for (int i = 0; exts[i]; i++) {
        size_t el = strlen(exts[i]);
        if (n > el && strcasecmp(fname + n - el, exts[i]) == 0) return true;
    }
    return false;
}

static bool already_listed(const SaveVmcList *out, const char *path) {
    for (int i = 0; i < out->count; i++)
        if (strcmp(out->items[i].path, path) == 0) return true;
    return false;
}

static void scan_card_dir(const char *dir, SaveVmcList *out) {
    DIR *d = opendir(dir);
    if (!d) return;
    struct dirent *de;
    while ((de = readdir(d)) != NULL && out->count < GCSAVES_MAX_CARDS) {
        if (de->d_name[0] == '.') continue;
        if (!has_card_ext(de->d_name)) continue;

        char path[SAVE_DIR_LEN];
        snprintf(path, sizeof(path), "%s/%s", dir, de->d_name);
        struct stat st;
        if (stat(path, &st) != 0) continue;
        if (!card_size_ok((uint32_t)st.st_size)) continue;
        if (already_listed(out, path)) continue;

        SaveVmc *v = &out->items[out->count];
        memset(v, 0, sizeof(*v));
        strncpy(v->path, path, sizeof(v->path) - 1);
        strncpy(v->filename, de->d_name, sizeof(v->filename) - 1);
        v->size = (uint32_t)st.st_size;
        out->count++;
    }
    closedir(d);
}

void gcsaves_scan_cards(const SyncState *state, SaveVmcList *out) {
    if (!out) return;
    out->count = 0;
    out->last_error[0] = '\0';

    char dir[SAVE_DIR_LEN];

    /* Configured Nintendont saves folder (default /saves). */
    sdpath(state, state->nin_saves_dir[0] ? state->nin_saves_dir
                                          : DEFAULT_NIN_SAVES_DIR,
           dir, sizeof(dir));
    scan_card_dir(dir, out);

    /* Conventional fallbacks: Nintendont's own default and a plain VMC dir. */
    sdpath(state, "/saves", dir, sizeof(dir));
    scan_card_dir(dir, out);
    sdpath(state, "/VMC", dir, sizeof(dir));
    scan_card_dir(dir, out);

    if (out->count == 0)
        snprintf(out->last_error, sizeof(out->last_error),
                 "No GC card images in %s", state->nin_saves_dir);
}

/* ---- per-save transfer ---- */

static int post_gci(const SyncState *state, const char *title_id,
                    const uint8_t *body, uint32_t len,
                    char *msg, size_t msg_size) {
    char path[128];
    snprintf(path, sizeof(path), "/api/v1/saves/%s/gc-card?format=gci", title_id);

    HttpRequest req = {0};
    req.server_url        = state->server_url;
    req.api_key           = state->api_key;
    req.console_id        = state->console_id;
    req.path              = path;
    req.method            = "POST";
    req.header_timeout_ms = 4 * HTTP_API_TIMEOUT_MS;
    req.body              = body;
    req.body_len          = len;
    req.body_content_type = "application/octet-stream";

    static uint8_t resp[512];
    int status = 0;
    int n = http_get_buf(&req, resp, sizeof(resp), &status);
    if (n >= 0 && status == 200) {
        snprintf(msg, msg_size, "Uploaded %s (%u KB)", title_id, (unsigned)(len / 1024));
        return 0;
    }
    snprintf(msg, msg_size, "Upload %s failed (HTTP %d, n=%d)", title_id, status, n);
    return -1;
}

int gcsaves_upload_save(const SyncState *state, VmcfsCard *card, int idx,
                        char *msg, size_t msg_size) {
    if (!state || !card || idx < 0 || idx >= card->count) return -1;
    int n = vmcfs_read_gci(card, idx, g_gci, GCSAVES_GCI_MAX);
    if (n < 0) { snprintf(msg, msg_size, "Read save failed (%d)", n); return -1; }
    return post_gci(state, card->saves[idx].title_id, g_gci, (uint32_t)n, msg, msg_size);
}

int gcsaves_restore_save(const SyncState *state, VmcfsCard *card,
                         const char *title_id, char *msg, size_t msg_size) {
    if (!state || !card || !title_id || !title_id[0]) return -1;

    char path[128];
    snprintf(path, sizeof(path), "/api/v1/saves/%s/gc-card?format=gci", title_id);

    HttpRequest req = {0};
    req.server_url = state->server_url;
    req.api_key    = state->api_key;
    req.console_id = state->console_id;
    req.path       = path;
    req.method     = "GET";
    req.header_timeout_ms = HTTP_API_TIMEOUT_MS;

    int status = 0;
    int n = http_get_buf(&req, g_gci, GCSAVES_GCI_MAX, &status);
    if (n < 0 || status != 200) {
        snprintf(msg, msg_size, "Download %s failed (HTTP %d, n=%d)", title_id, status, n);
        return -1;
    }
    return vmcfs_write_gci(card, g_gci, (uint32_t)n, msg, msg_size);
}

int gcsaves_import_card(const SyncState *state, const SaveVmc *vmc,
                        char *msg, size_t msg_size) {
    if (!state || !vmc) return -1;
    FILE *fp = fopen(vmc->path, "rb");
    if (!fp) { snprintf(msg, msg_size, "Open %s failed", vmc->filename); return -1; }

    /* Stream the image from SD — cards can be 16 MB. */
    HttpRequest req = {0};
    req.server_url        = state->server_url;
    req.api_key           = state->api_key;
    req.console_id        = state->console_id;
    req.path              = "/api/v1/saves/gc-vmc/import";
    req.method            = "POST";
    req.header_timeout_ms = 4 * HTTP_API_TIMEOUT_MS;
    req.body_fp           = fp;
    req.body_len          = vmc->size;
    req.body_content_type = "application/octet-stream";

    static uint8_t resp[4096];
    int status = 0;
    int n = http_get_buf(&req, resp, sizeof(resp), &status);
    fclose(fp);
    if (n >= 0 && status == 200) {
        int games = 0;
        for (char *p = (char *)resp; (p = strstr(p, "title_id")) != NULL; p++) games++;
        snprintf(msg, msg_size, "Imported %d game(s) from %s", games, vmc->filename);
        return 0;
    }
    snprintf(msg, msg_size, "Import %s failed (HTTP %d, n=%d)", vmc->filename, status, n);
    return -1;
}

/* ---- server saves (JSON walk of /titles) ---- */

static const char *skip_ws(const char *p) {
    while (*p && (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r')) p++;
    return p;
}

static const char *find_key(const char *p, const char *end, const char *key) {
    char needle[64];
    int n = snprintf(needle, sizeof(needle), "\"%s\"", key);
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
            out[len++] = *p; p++;
            continue;
        }
        out[len++] = *p++;
    }
    out[len] = '\0';
    return true;
}

static bool extract_u32(const char *p, const char *end, uint32_t *out) {
    if (!p || p >= end) return false;
    p = skip_ws(p);
    char *endp = NULL;
    errno = 0;
    unsigned long long v = strtoull(p, &endp, 10);
    if (errno != 0 || endp == p) return false;
    *out = (uint32_t)v;
    return true;
}

static bool object_bounds(const char *p, const char *end, const char **end_out) {
    if (p >= end || *p != '{') return false;
    int depth = 0;
    while (p < end) {
        if (*p == '{') depth++;
        else if (*p == '}') { depth--; if (depth == 0) { *end_out = p; return true; } }
        else if (*p == '"') { p++; while (p < end && *p != '"') { if (*p == '\\' && p + 1 < end) p++; p++; } }
        p++;
    }
    return false;
}

void gcsaves_fetch_server(const SyncState *state,
                          char *scratch, uint32_t scratch_size,
                          ServerSaveList *out) {
    if (!out) return;
    out->count = 0;
    out->last_error[0] = '\0';

    HttpRequest req = {0};
    req.server_url = state->server_url;
    req.api_key    = state->api_key;
    req.console_id = state->console_id;
    req.path       = "/api/v1/titles?console_type=GC";
    req.method     = "GET";
    req.header_timeout_ms = HTTP_API_TIMEOUT_MS;

    int status = 0;
    int n = http_get_buf(&req, (uint8_t *)scratch, scratch_size, &status);
    if (n < 0 || status != 200) {
        snprintf(out->last_error, sizeof(out->last_error),
                 "Server fetch failed (HTTP %d, n=%d)", status, n);
        return;
    }

    const char *body_end = scratch + n;
    /* Skip the root object's opening brace so find_key matches "titles" at
     * depth 0 (otherwise it descends to depth 1 and never matches). */
    const char *body = skip_ws(scratch);
    if (body < body_end && *body == '{') body++;
    const char *titles_v = find_key(body, body_end, "titles");
    if (!titles_v || *titles_v != '[') {
        snprintf(out->last_error, sizeof(out->last_error), "No 'titles' array");
        return;
    }

    const char *p = titles_v + 1;
    while (p < body_end && out->count < GCSAVES_MAX_SERVER) {
        p = skip_ws(p);
        if (p >= body_end || *p == ']') break;
        if (*p == ',') { p++; continue; }
        if (*p != '{') break;

        const char *obj_end = NULL;
        if (!object_bounds(p, body_end, &obj_end)) break;

        ServerSave *s = &out->items[out->count];
        memset(s, 0, sizeof(*s));

        const char *v;
        v = find_key(p + 1, obj_end, "title_id");
        if (v) extract_str(v, obj_end, s->title_id, sizeof(s->title_id));
        v = find_key(p + 1, obj_end, "game_name");
        if (v) extract_str(v, obj_end, s->name, sizeof(s->name));
        if (!s->name[0]) {
            v = find_key(p + 1, obj_end, "name");
            if (v) extract_str(v, obj_end, s->name, sizeof(s->name));
        }
        v = find_key(p + 1, obj_end, "client_timestamp");
        if (v) extract_u32(v, obj_end, &s->timestamp);

        if (s->title_id[0]) {
            if (!s->name[0]) strncpy(s->name, s->title_id, sizeof(s->name) - 1);
            out->count++;
        }
        p = obj_end + 1;
    }

    if (out->count == 0 && out->last_error[0] == '\0')
        snprintf(out->last_error, sizeof(out->last_error), "No GC saves on server");
}

int gcsaves_pull_all(const SyncState *state, const ServerSaveList *server,
                     char *msg, size_t msg_size) {
    if (!state || !server) return -1;

    char dir[SAVE_DIR_LEN];
    sdpath(state, APP_DATA_SUBDIR, dir, sizeof(dir));
    mkdir(dir, 0777);
    sdpath(state, APP_DATA_SUBDIR "/gci", dir, sizeof(dir));
    mkdir(dir, 0777);

    int ok = 0, fail = 0;
    for (int i = 0; i < server->count; i++) {
        const char *tid = server->items[i].title_id;
        char path[128];
        snprintf(path, sizeof(path), "/api/v1/saves/%s/gc-card?format=gci", tid);

        HttpRequest req = {0};
        req.server_url = state->server_url;
        req.api_key    = state->api_key;
        req.console_id = state->console_id;
        req.path       = path;
        req.method     = "GET";
    req.header_timeout_ms = HTTP_API_TIMEOUT_MS;

        int status = 0;
        int n = http_get_buf(&req, g_gci, GCSAVES_GCI_MAX, &status);
        if (n < 0 || status != 200) { fail++; continue; }

        char out_path[SAVE_DIR_LEN];
        snprintf(out_path, sizeof(out_path), "%s/%s.gci", dir, tid);
        FILE *fp = fopen(out_path, "wb");
        if (!fp) { fail++; continue; }
        size_t wr = fwrite(g_gci, 1, (size_t)n, fp);
        fclose(fp);
        if (wr == (size_t)n) ok++; else fail++;
    }
    snprintf(msg, msg_size, "Pulled %d GCI(s), %d failed", ok, fail);
    return ok;
}
