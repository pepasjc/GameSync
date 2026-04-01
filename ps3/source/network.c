/*
 * PS3 Save Sync - Network (socket-based HTTP)
 *
 * Uses PSL1GHT BSD socket API over TCP.
 * Only plain HTTP (no TLS) — the server runs locally on the LAN.
 *
 * PSL1GHT notes:
 *   - Call netInitialize() before any socket operations.
 *   - Use close() or socketclose() depending on your PSL1GHT version.
 *     If close() fails to link, change NET_CLOSE to socketclose().
 *   - gethostbyname() is available in <netdb.h>.
 */

#include "network.h"
#include "apollo.h"
#include "saves.h"
#include "state.h"
#include "debug.h"

#include <sysmodule/sysmodule.h>
#include <net/net.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <netdb.h>

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>
#include <ctype.h>

/* If socketclose() is required by your PSL1GHT version, change this: */
#define NET_CLOSE(s)  close(s)

/* Maximum response body size for bundle downloads (8 MB) */
#define HTTP_RESP_SIZE  (8 * 1024 * 1024)

static bool g_initialized = false;

/* ---- URL parsing ---- */

static bool parse_url(const char *url,
                      char *host, int host_max,
                      int  *port,
                      char *path, int path_max) {
    if (strncmp(url, "http://", 7) != 0) return false;
    const char *p = url + 7;

    const char *slash = strchr(p, '/');
    size_t auth_len   = slash ? (size_t)(slash - p) : strlen(p);
    const char *colon = (const char *)memchr(p, ':', auth_len);

    size_t host_len;
    if (colon) {
        host_len = (size_t)(colon - p);
        *port    = atoi(colon + 1);
    } else {
        host_len = auth_len;
        *port    = 80;
    }
    if (host_len >= (size_t)host_max) host_len = (size_t)host_max - 1;
    memcpy(host, p, host_len);
    host[host_len] = '\0';

    if (slash) {
        strncpy(path, slash, path_max - 1);
        path[path_max - 1] = '\0';
    } else {
        strncpy(path, "/", path_max - 1);
    }
    return true;
}

/* ---- TCP connect ---- */

static int tcp_connect(const char *host, int port) {
    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port   = htons((uint16_t)port);

    /* Try direct IP address first */
    unsigned long ip = inet_addr(host);
    if (ip != (unsigned long)INADDR_NONE) {
        addr.sin_addr.s_addr = (uint32_t)ip;
    } else {
        struct hostent *h = gethostbyname(host);
        if (!h) { debug_log("net: gethostbyname(%s) failed", host); return -1; }
        memcpy(&addr.sin_addr, h->h_addr_list[0], (size_t)h->h_length);
    }

    int s = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (s < 0) { debug_log("net: socket() failed %d", s); return -1; }

    /* 15-second receive timeout */
    struct timeval tv = { .tv_sec = 15, .tv_usec = 0 };
    setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

    if (connect(s, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        debug_log("net: connect(%s:%d) failed", host, port);
        NET_CLOSE(s);
        return -1;
    }
    return s;
}

/* ---- Send all bytes ---- */

static bool send_all(int s, const uint8_t *buf, uint32_t size) {
    uint32_t sent = 0;
    while (sent < size) {
        int n = (int)send(s, buf + sent, size - sent, 0);
        if (n <= 0) return false;
        sent += (uint32_t)n;
    }
    return true;
}

/* ---- Read one line (strips \r\n) ---- */

static int read_line(int s, char *buf, int max) {
    int len = 0;
    while (len < max - 1) {
        char c;
        int n = (int)recv(s, &c, 1, 0);
        if (n <= 0) break;
        if (c == '\n') break;
        if (c != '\r') buf[len++] = c;
    }
    buf[len] = '\0';
    return len;
}

/* ---- Core HTTP request ----
 *
 * api_path: absolute path + optional query, e.g. "/api/v1/saves/BCUS98233"
 * Returns body byte count on success, -1 on connection failure.
 * status_out receives the HTTP status code (e.g. 200, 404, 409).
 */
static int http_request(const SyncState *state,
                        const char *method,
                        const char *api_path,
                        const char *content_type,
                        const uint8_t *body,
                        uint32_t body_size,
                        uint8_t *resp_buf,
                        uint32_t resp_buf_size,
                        int *status_out) {
    char host[128];
    int  port;
    char base_path[512];

    if (!parse_url(state->server_url, host, sizeof(host), &port,
                   base_path, sizeof(base_path))) {
        debug_log("net: bad server_url: %s", state->server_url);
        return -1;
    }

    int s = tcp_connect(host, port);
    if (s < 0) return -1;

    /* Build request header */
    char hdr[1024];
    int  hlen;
    if (body && body_size > 0) {
        hlen = snprintf(hdr, sizeof(hdr),
            "%s %s HTTP/1.0\r\n"
            "Host: %s:%d\r\n"
            "X-API-Key: %s\r\n"
            "X-Console-ID: %s\r\n"
            "Content-Type: %s\r\n"
            "Content-Length: %u\r\n"
            "Connection: close\r\n"
            "\r\n",
            method, api_path, host, port,
            state->api_key, state->console_id,
            content_type ? content_type : "application/octet-stream",
            (unsigned)body_size);
    } else {
        hlen = snprintf(hdr, sizeof(hdr),
            "%s %s HTTP/1.0\r\n"
            "Host: %s:%d\r\n"
            "X-API-Key: %s\r\n"
            "X-Console-ID: %s\r\n"
            "Connection: close\r\n"
            "\r\n",
            method, api_path, host, port,
            state->api_key, state->console_id);
    }

    if (!send_all(s, (const uint8_t *)hdr, (uint32_t)hlen)) {
        NET_CLOSE(s); return -1;
    }
    if (body && body_size > 0) {
        if (!send_all(s, body, body_size)) {
            NET_CLOSE(s); return -1;
        }
    }

    /* Read status line */
    char line[512];
    read_line(s, line, sizeof(line));
    int status = 0;
    const char *sp = strchr(line, ' ');
    if (sp) status = atoi(sp + 1);
    if (status_out) *status_out = status;
    debug_log("net: %s %s -> HTTP %d", method, api_path, status);

    /* Read headers, look for Content-Length */
    int content_length = -1;
    while (1) {
        int n = read_line(s, line, sizeof(line));
        if (n == 0) break;
        if (strncasecmp(line, "Content-Length:", 15) == 0)
            content_length = atoi(line + 15);
    }

    /* Read body */
    int total = 0;
    if (resp_buf && resp_buf_size > 0) {
        uint32_t cap = resp_buf_size - 1;
        if (content_length > 0) {
            uint32_t want = (uint32_t)content_length < cap ? (uint32_t)content_length : cap;
            while ((uint32_t)total < want) {
                int n = (int)recv(s, resp_buf + total, want - (uint32_t)total, 0);
                if (n <= 0) break;
                total += n;
            }
        } else {
            while ((uint32_t)total < cap) {
                int n = (int)recv(s, resp_buf + total, cap - (uint32_t)total, 0);
                if (n <= 0) break;
                total += n;
            }
        }
        resp_buf[total] = '\0';
    }

    NET_CLOSE(s);
    return total;
}

/* ---- JSON helpers ---- */

static void parse_json_str(const char *json, const char *key,
                           char *out, int out_size) {
    char search[64];
    snprintf(search, sizeof(search), "\"%s\":", key);
    const char *p = strstr(json, search);
    if (!p) return;
    p += strlen(search);
    while (*p == ' ') p++;
    if (*p != '"') return;
    p++;
    const char *end = strchr(p, '"');
    if (!end) return;
    int len = (int)(end - p);
    if (len >= out_size) len = out_size - 1;
    strncpy(out, p, (size_t)len);
    out[len] = '\0';
}

static int parse_id_array(const char *json, const char *key,
                          char out[][GAME_ID_LEN], int max) {
    char search[32];
    snprintf(search, sizeof(search), "\"%s\"", key);
    const char *p = strstr(json, search);
    if (!p) return 0;
    p += strlen(search);
    while (*p == ':' || *p == ' ') p++;
    if (*p != '[') return 0;
    p++;

    int count = 0;
    while (*p && *p != ']' && count < max) {
        while (*p == ' ' || *p == ',' || *p == '\n' || *p == '\r') p++;
        if (*p != '"') break;
        p++;
        int len = 0;
        while (*p && *p != '"' && len < GAME_ID_LEN - 1)
            out[count][len++] = *p++;
        out[count][len] = '\0';
        if (*p == '"') p++;
        if (len > 0) count++;
    }
    return count;
}

/* ---- Public API ---- */

int network_init(void) {
    if (g_initialized) return 0;
    sysModuleLoad(SYSMODULE_NET);
    int ret = netInitialize();
    if (ret < 0) {
        debug_log("net: netInitialize failed %d", ret);
        sysModuleUnload(SYSMODULE_NET);
        return ret;
    }
    g_initialized = true;
    debug_log("net: initialized");
    return 0;
}

void network_cleanup(void) {
    if (g_initialized) {
        netDeinitialize();
        sysModuleUnload(SYSMODULE_NET);
        g_initialized = false;
    }
}

bool network_check_server(const SyncState *state) {
    static uint8_t resp[256];
    int status = 0;
    int r = http_request(state, "GET", "/api/v1/status",
                         NULL, NULL, 0, resp, sizeof(resp), &status);
    return r >= 0 && status == 200;
}

int network_get_save_info(const SyncState *state, const char *game_code,
                          char *hash_out, uint32_t *size_out,
                          char *last_sync_out) {
    char path[256];
    snprintf(path, sizeof(path), "/api/v1/saves/%s/meta", game_code);

    static uint8_t resp[1024];
    int status = 0;
    int r = http_request(state, "GET", path,
                         NULL, NULL, 0, resp, sizeof(resp) - 1, &status);
    if (status == 404) return 1;
    if (r < 0 || status != 200) return (status > 0) ? -status : -1;

    const char *json = (const char *)resp;
    if (hash_out)  parse_json_str(json, "save_hash", hash_out, 65);
    if (size_out) {
        const char *p = strstr(json, "\"save_size\":");
        if (p) *size_out = (uint32_t)atoi(p + 12);
    }
    if (last_sync_out) {
        last_sync_out[0] = '\0';
        parse_json_str(json, "last_sync", last_sync_out, 32);
    }
    return 0;
}

int network_upload_save(const SyncState *state, const char *game_code,
                        const uint8_t *bundle, uint32_t bundle_size) {
    char path[256];
    snprintf(path, sizeof(path), "/api/v1/saves/%s?force=true&source=ps3",
             game_code);

    static uint8_t resp[512];
    int status = 0;
    http_request(state, "POST", path,
                 "application/octet-stream", bundle, bundle_size,
                 resp, sizeof(resp), &status);
    return (status == 200) ? 0 : (status > 0 ? status : -1);
}

int network_download_save(const SyncState *state, const char *game_code,
                          uint8_t *out, uint32_t out_size) {
    char path[256];
    snprintf(path, sizeof(path), "/api/v1/saves/%s", game_code);

    int status = 0;
    int r = http_request(state, "GET", path,
                         NULL, NULL, 0, out, out_size, &status);
    if (status != 200) return (status > 0) ? -status : -1;
    return r;
}

/* ---- Merge server titles ---- */

/*
 * Find a local title that matches a server title_id.
 * Matches by (in order):
 *   1. Exact title_id match
 *   2. Exact game_code match
 *   3. 9-char prefix match — handles variants like BLJS10001GAME vs BLJS10001
 * Returns index into state->titles, or -1 if not found.
 */
static int find_local_title(const SyncState *state, const char *server_id) {
    char code9[10];
    int i;
    size_t slen = strlen(server_id);

    if (slen >= 9) {
        memcpy(code9, server_id, 9);
        code9[9] = '\0';
    } else {
        code9[0] = '\0';
    }

    for (i = 0; i < state->num_titles; i++) {
        if (strcmp(state->titles[i].title_id,  server_id) == 0) return i;
        if (strcmp(state->titles[i].game_code, server_id) == 0) return i;
        if (code9[0] && strcmp(state->titles[i].game_code, code9) == 0) return i;
    }
    return -1;
}

void network_merge_server_titles(SyncState *state) {
    if (!state) return;

    static uint8_t resp[256 * 1024];
    int status = 0;
    int r = http_request(state, "GET", "/api/v1/titles",
                         NULL, NULL, 0, resp, sizeof(resp) - 1, &status);
    if (r <= 0 || status != 200) return;
    resp[r] = '\0';

    const char *p = (const char *)resp;
    while ((p = strstr(p, "\"title_id\"")) != NULL) {
        p = strchr(p, ':');
        if (!p) break;
        p++;
        while (*p == ' ' || *p == '\t') p++;
        if (*p != '"') continue;
        p++;

        char server_id[GAME_ID_LEN];
        int len = 0;
        while (*p && *p != '"' && len < GAME_ID_LEN - 1)
            server_id[len++] = *p++;
        server_id[len] = '\0';

        if (!saves_is_relevant_game_code(server_id)) continue;

        /* If a local title matches (exact or by 9-char prefix), just flag it
           as present on the server — don't create a duplicate entry. */
        int existing = find_local_title(state, server_id);
        if (existing >= 0) {
            state->titles[existing].on_server = true;
            continue;
        }

        if (state->num_titles >= MAX_TITLES) break;

        TitleInfo *t = &state->titles[state->num_titles++];
        memset(t, 0, sizeof(*t));
        /* Store the full server title_id but extract 9-char game_code for lookup */
        strncpy(t->title_id,  server_id, sizeof(t->title_id)  - 1);
        apollo_extract_game_code(server_id, t->game_code, sizeof(t->game_code));
        if (t->game_code[0] == '\0')
            strncpy(t->game_code, server_id, sizeof(t->game_code) - 1);
        strncpy(t->name, t->game_code, sizeof(t->name) - 1);
        t->kind        = apollo_detect_save_kind(t->game_code);
        t->server_only = true;
        t->on_server   = true;
    }
}

/* ---- Fetch game names ---- */

void network_fetch_names(SyncState *state) {
    if (!state || state->num_titles == 0) return;

    int json_cap = 16 + state->num_titles * 22;
    char *json = (char *)malloc((size_t)json_cap);
    if (!json) return;

    int pos = snprintf(json, (size_t)json_cap, "{\"codes\":[");
    for (int i = 0; i < state->num_titles; i++) {
        pos += snprintf(json + pos, (size_t)(json_cap - pos),
                        "%s\"%s\"", i > 0 ? "," : "",
                        state->titles[i].game_code);
    }
    pos += snprintf(json + pos, (size_t)(json_cap - pos), "]}");

    static uint8_t resp[65536];
    int status = 0;
    int r = http_request(state, "POST", "/api/v1/titles/names",
                         "application/json",
                         (const uint8_t *)json, (uint32_t)pos,
                         resp, sizeof(resp) - 1, &status);
    free(json);
    if (r <= 0 || status != 200) return;
    resp[r] = '\0';

    /* Parse "names": {"CODE": "Game Title", ...} */
    char *p = strstr((char *)resp, "\"names\"");
    if (!p) return;
    p = strchr(p + 7, '{');
    if (!p) return;
    p++;

    while (*p && *p != '}') {
        while (*p == ' ' || *p == '\n' || *p == '\r' || *p == '\t' || *p == ',') p++;
        if (*p != '"' || *p == '}') break;
        p++;
        char key[GAME_ID_LEN]; int klen = 0;
        while (*p && *p != '"' && klen < GAME_ID_LEN - 1) key[klen++] = *p++;
        key[klen] = '\0'; if (*p == '"') p++;
        while (*p == ' ' || *p == ':') p++;
        if (*p != '"') break; p++;
        char val[MAX_TITLE_LEN]; int vlen = 0;
        while (*p && vlen < MAX_TITLE_LEN - 1) {
            if (*p == '\\' && *(p + 1) == '"') { val[vlen++] = '"'; p += 2; continue; }
            if (*p == '"') break;
            val[vlen++] = *p++;
        }
        val[vlen] = '\0'; if (*p == '"') p++;

        for (int i = 0; i < state->num_titles; i++) {
            if (strcmp(state->titles[i].game_code, key) == 0) {
                strncpy(state->titles[i].name, val, MAX_TITLE_LEN - 1);
                state->titles[i].name[MAX_TITLE_LEN - 1] = '\0';
                break;
            }
        }
    }
}

/* ---- Sync plan ---- */

int network_get_sync_plan(const SyncState *state, NetworkSyncPlan *plan) {
    memset(plan, 0, sizeof(*plan));

    /* Count titles with computed hashes */
    int count = 0;
    for (int i = 0; i < state->num_titles; i++)
        if (!state->titles[i].server_only && state->titles[i].hash_calculated) count++;
    if (count == 0) return 0;

    int json_cap = 64 + count * 260;
    char *json = (char *)malloc((size_t)json_cap);
    if (!json) return -1;

    int pos = snprintf(json, (size_t)json_cap,
                       "{\"console_id\":\"%s\",\"titles\":[", state->console_id);
    bool first = true;
    for (int i = 0; i < state->num_titles; i++) {
        const TitleInfo *t = &state->titles[i];
        if (t->server_only || !t->hash_calculated) continue;

        char hash_hex[65];
        for (int j = 0; j < 32; j++)
            snprintf(&hash_hex[j * 2], 3, "%02x", t->hash[j]);
        hash_hex[64] = '\0';

        char last_hash[65] = "";
        bool has_last = state_get_last_hash(t->game_code, last_hash);

        if (!first) json[pos++] = ',';
        first = false;

        if (has_last) {
            pos += snprintf(json + pos, (size_t)(json_cap - pos),
                "{\"title_id\":\"%s\",\"save_hash\":\"%s\","
                "\"timestamp\":0,\"size\":%u,"
                "\"last_synced_hash\":\"%s\"}",
                t->game_code, hash_hex, t->total_size, last_hash);
        } else {
            pos += snprintf(json + pos, (size_t)(json_cap - pos),
                "{\"title_id\":\"%s\",\"save_hash\":\"%s\","
                "\"timestamp\":0,\"size\":%u}",
                t->game_code, hash_hex, t->total_size);
        }
    }
    pos += snprintf(json + pos, (size_t)(json_cap - pos), "]}");

    static uint8_t resp[16384];
    int status = 0;
    int r = http_request(state, "POST", "/api/v1/sync",
                         "application/json",
                         (const uint8_t *)json, (uint32_t)pos,
                         resp, sizeof(resp) - 1, &status);
    free(json);

    if (r <= 0 || status != 200) return -1;
    resp[r] = '\0';
    const char *resp_str = (const char *)resp;

    int room;
    room = SYNC_PLAN_MAX - plan->upload_count;
    if (room > 0)
        plan->upload_count   += parse_id_array(resp_str, "upload",
                                   plan->upload   + plan->upload_count,   room);
    room = SYNC_PLAN_MAX - plan->download_count;
    if (room > 0)
        plan->download_count += parse_id_array(resp_str, "download",
                                   plan->download + plan->download_count, room);
    room = SYNC_PLAN_MAX - plan->conflict_count;
    if (room > 0)
        plan->conflict_count += parse_id_array(resp_str, "conflict",
                                   plan->conflict + plan->conflict_count, room);
    return 0;
}
