/*
 * Vita Save Sync - Network (SceNet + SceHttp)
 *
 * Uses the VitaSDK high-level SceHttp library.
 * WiFi is managed by the Vita OS; this module only checks if it is connected.
 * If not connected, the user must enable WiFi via Settings > Network > WiFi.
 */

#include <stdio.h>
#include <string.h>
#include <stdlib.h>

#include <psp2/net/net.h>
#include <psp2/net/netctl.h>
#include <psp2/net/http.h>
#include <psp2/sysmodule.h>
#include <psp2/kernel/threadmgr.h>

#include "network.h"
#include "config.h"

#define NET_POOL_SIZE   (4 * 1024 * 1024)
#define HTTP_POOL_SIZE  (1 * 1024 * 1024)

static bool g_initialized = false;
static bool g_connected   = false;
static int  g_tmpl        = -1;

static char g_net_memory[NET_POOL_SIZE];

int network_init(void) {
    sceSysmoduleLoadModule(SCE_SYSMODULE_NET);
    sceSysmoduleLoadModule(SCE_SYSMODULE_HTTP);

    SceNetInitParam netParam;
    netParam.memory = g_net_memory;
    netParam.size   = NET_POOL_SIZE;
    netParam.flags  = 0;

    int ret = sceNetInit(&netParam);
    if (ret < 0) return ret;

    ret = sceNetCtlInit();
    if (ret < 0) { sceNetTerm(); return ret; }

    ret = sceHttpInit(HTTP_POOL_SIZE);
    if (ret < 0) { sceNetCtlTerm(); sceNetTerm(); return ret; }

    g_tmpl = sceHttpCreateTemplate("vitasync/1.0", SCE_HTTP_VERSION_1_1, SCE_TRUE);
    if (g_tmpl < 0) {
        sceHttpTerm();
        sceNetCtlTerm();
        sceNetTerm();
        return g_tmpl;
    }

    g_initialized = true;
    return 0;
}

void network_cleanup(void) {
    if (g_tmpl >= 0) {
        sceHttpDeleteTemplate(g_tmpl);
        g_tmpl = -1;
    }
    if (g_initialized) {
        sceHttpTerm();
        sceNetCtlTerm();
        sceNetTerm();
        g_initialized = false;
        g_connected   = false;
    }
}

int network_connect(void) {
    if (!g_initialized) return -1;

    /* The Vita OS manages WiFi; check if we already have an IP address. */
    SceNetCtlInfo info;
    if (sceNetCtlInetGetInfo(SCE_NETCTL_INFO_GET_IP_ADDRESS, &info) == 0
            && info.ip_address[0] != '\0') {
        g_connected = true;
        return 0;
    }

    /* Not connected: user must enable WiFi in Settings > Network > WiFi */
    return -1;
}

bool network_is_connected(void) {
    return g_connected;
}

/* ---- Internal HTTP helper ---- */

static int http_do_request(const SyncState *state,
                           int method,
                           const char *url,
                           const char *content_type,
                           const uint8_t *body, uint32_t body_size,
                           uint8_t *out, uint32_t out_size,
                           int *status_out) {
    if (!g_initialized || g_tmpl < 0) return -1;

    int conn = sceHttpCreateConnectionWithURL(g_tmpl, url, 0);
    if (conn < 0) return conn;

    uint64_t content_length = (method == SCE_HTTP_METHOD_POST) ? body_size : 0;
    int req = sceHttpCreateRequestWithURL(conn, method, url, content_length);
    if (req < 0) { sceHttpDeleteConnection(conn); return req; }

    sceHttpAddRequestHeader(req, "X-API-Key",    state->api_key,    SCE_HTTP_HEADER_OVERWRITE);
    sceHttpAddRequestHeader(req, "X-Console-ID", state->console_id, SCE_HTTP_HEADER_OVERWRITE);

    if (content_type && body && body_size > 0)
        sceHttpAddRequestHeader(req, "Content-Type", content_type,
                                SCE_HTTP_HEADER_OVERWRITE);

    int ret = sceHttpSendRequest(req, body, body_size);
    if (ret < 0) { sceHttpDeleteRequest(req); sceHttpDeleteConnection(conn); return ret; }

    int status = 0;
    sceHttpGetStatusCode(req, &status);
    if (status_out) *status_out = status;

    int total = 0;
    if (out && out_size > 0) {
        unsigned int chunk;
        while ((ret = sceHttpReadData(req, out + total, out_size - total - 1)) > 0) {
            total += ret;
            if ((uint32_t)total >= out_size - 1) break;
        }
        out[total] = '\0';
    }

    sceHttpDeleteRequest(req);
    sceHttpDeleteConnection(conn);
    return total;
}

/* ---- Public API ---- */

bool network_check_server(const SyncState *state) {
    char url[512];
    snprintf(url, sizeof(url), "%s/api/v1/status", state->server_url);

    static uint8_t resp[256];
    int status = 0;
    int r = http_do_request(state, SCE_HTTP_METHOD_GET, url,
                            NULL, NULL, 0, resp, sizeof(resp), &status);
    return (r >= 0 && status == 200);
}

/* Parse a JSON string value for key into out (up to out_size-1 chars). */
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
    strncpy(out, p, len);
    out[len] = '\0';
}

int network_get_save_info(const SyncState *state, const char *game_id,
                          char *hash_out, uint32_t *size_out,
                          char *last_sync_out) {
    char url[512];
    snprintf(url, sizeof(url), "%s/api/v1/saves/%s/meta",
             state->server_url, game_id);

    static uint8_t resp[1024];
    int status = 0;
    int r = http_do_request(state, SCE_HTTP_METHOD_GET, url,
                            NULL, NULL, 0, resp, sizeof(resp) - 1, &status);

    if (status == 404) return 1;
    if (r < 0 || status != 200) return (status > 0) ? -status : r;

    const char *json = (char *)resp;
    if (hash_out) parse_json_str(json, "save_hash", hash_out, 65);
    if (size_out) {
        char *p = strstr(json, "\"save_size\":");
        if (p) *size_out = (uint32_t)atoi(p + 12);
    }
    if (last_sync_out) {
        last_sync_out[0] = '\0';
        parse_json_str(json, "last_sync", last_sync_out, 32);
    }
    return 0;
}

/* Parse a JSON array of strings for key into out[][GAME_ID_LEN]. */
static int parse_id_array(const char *json, const char *key,
                          char out[][GAME_ID_LEN], int max_count) {
    char search[32];
    snprintf(search, sizeof(search), "\"%s\"", key);
    const char *p = strstr(json, search);
    if (!p) return 0;
    p += strlen(search);
    while (*p == ':' || *p == ' ') p++;
    if (*p != '[') return 0;
    p++;

    int count = 0;
    while (*p && *p != ']' && count < max_count) {
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

/* Build and send a sync plan request for a filtered subset of titles.
 *
 * console_id_override: if non-NULL, use this as "console_id" in the JSON body
 *                      instead of state->console_id. Used to direct PSP emu saves
 *                      to the shared "psp" server slot regardless of the Vita's ID.
 * platform_filter:     PLATFORM_VITA or PLATFORM_PSP_EMU; only titles matching
 *                      this platform are included. Pass -1 to include all.
 *
 * Results are *appended* to plan (counts must be initialised by caller).
 * Returns 0 on success, -1 on error (empty title list treated as success).
 */
static int _sync_plan_for_platform(const SyncState *state, NetworkSyncPlan *plan,
                                   const char *console_id_override,
                                   int platform_filter) {
    /* Count matching titles */
    int count = 0;
    for (int i = 0; i < state->num_titles; i++) {
        const TitleInfo *t = &state->titles[i];
        if (platform_filter >= 0 && (int)t->platform != platform_filter) continue;
        if (t->hash_calculated) count++;
    }
    if (count == 0) return 0;  /* nothing to sync for this group */

    const char *cid = console_id_override ? console_id_override : state->console_id;

    int json_cap = 64 + count * 260;
    char *json = malloc(json_cap);
    if (!json) return -1;

    int pos = snprintf(json, json_cap, "{\"console_id\":\"%s\",\"titles\":[", cid);
    bool first = true;
    for (int i = 0; i < state->num_titles; i++) {
        const TitleInfo *t = &state->titles[i];
        if (platform_filter >= 0 && (int)t->platform != platform_filter) continue;
        if (!t->hash_calculated) continue;

        char hash_hex[65];
        for (int j = 0; j < 32; j++)
            sprintf(&hash_hex[j * 2], "%02x", t->hash[j]);
        hash_hex[64] = '\0';

        char last_hash[65] = "";
        bool has_last = config_get_last_hash(t->game_id, last_hash);

        if (!first) json[pos++] = ',';
        first = false;

        if (has_last) {
            pos += snprintf(json + pos, json_cap - pos,
                "{\"title_id\":\"%s\",\"save_hash\":\"%s\","
                "\"timestamp\":0,\"size\":%u,"
                "\"last_synced_hash\":\"%s\"}",
                t->game_id, hash_hex, t->total_size, last_hash);
        } else {
            pos += snprintf(json + pos, json_cap - pos,
                "{\"title_id\":\"%s\",\"save_hash\":\"%s\","
                "\"timestamp\":0,\"size\":%u}",
                t->game_id, hash_hex, t->total_size);
        }
    }
    pos += snprintf(json + pos, json_cap - pos, "]}");

    static uint8_t resp[16384];
    int resp_len = 0;
    int r = network_post_json(state, "/api/v1/sync",
                              json, resp, sizeof(resp) - 1, &resp_len);
    free(json);

    if (r != 0 || resp_len <= 0) return -1;
    resp[resp_len] = '\0';
    const char *resp_str = (char *)resp;

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

int network_get_sync_plan(const SyncState *state, NetworkSyncPlan *plan) {
    memset(plan, 0, sizeof(*plan));

    /* Native Vita saves use the device's own console_id slot on the server. */
    int vita_ret = _sync_plan_for_platform(state, plan, NULL, PLATFORM_VITA);

    /* PSP emu saves share the canonical "psp" slot so they sync with native
     * PSP hardware regardless of which Vita device is being used. */
    int psp_ret = _sync_plan_for_platform(state, plan, "psp", PLATFORM_PSP_EMU);

    /* Return -1 only if both failed — a PSP-only failure should not discard
     * the Vita portion of the plan that already succeeded. */
    if (vita_ret != 0 && psp_ret != 0)
        return -1;

    return 0;
}

int network_upload_save(const SyncState *state, const TitleInfo *title,
                        const uint8_t *bundle, uint32_t bundle_size) {
    char url[512];
    snprintf(url, sizeof(url), "%s/api/v1/saves/%s?force=true&source=%s",
             state->server_url, title->game_id,
             title->platform == PLATFORM_PSP_EMU ? "psp" : "vita");

    static uint8_t resp[512];
    int status = 0;
    int r = http_do_request(state, SCE_HTTP_METHOD_POST, url,
                            "application/octet-stream", bundle, bundle_size,
                            resp, sizeof(resp), &status);
    /* Return HTTP status on failure so caller can log it.
     * Negative values: SceHttp error; positive != 200: server rejected. */
    if (status == 200) return 0;
    return (status > 0) ? status : r;
}

int network_download_save(const SyncState *state, const char *game_id,
                          uint8_t *out, uint32_t out_size) {
    char url[512];
    snprintf(url, sizeof(url), "%s/api/v1/saves/%s", state->server_url, game_id);

    int status = 0;
    int r = http_do_request(state, SCE_HTTP_METHOD_GET, url,
                            NULL, NULL, 0, out, out_size, &status);
    if (status != 200) return -status;
    return r;
}

void network_fetch_names(SyncState *state) {
    if (!state || state->num_titles == 0) return;

    /* Build {"codes":["ID1","ID2",...]} */
    /* Each entry: up to 35 chars (quoted + comma): "ULUS10272DATA00", = 35 */
    int json_cap = 16 + state->num_titles * 38;
    char *json = malloc(json_cap);
    if (!json) return;

    int pos = 0;
    pos += snprintf(json + pos, json_cap - pos, "{\"codes\":[");
    for (int i = 0; i < state->num_titles; i++) {
        pos += snprintf(json + pos, json_cap - pos,
                        "%s\"%s\"", i > 0 ? "," : "", state->titles[i].game_id);
    }
    pos += snprintf(json + pos, json_cap - pos, "]}");

    static uint8_t resp[65536];   /* 64KB — enough for ~4000 name entries */
    int resp_len = 0;
    int r = network_post_json(state, "/api/v1/titles/names",
                              json, resp, sizeof(resp) - 1, &resp_len);
    free(json);

    if (r != 0 || resp_len <= 0) return;
    resp[resp_len] = '\0';

    /* Helper: parse a JSON object of string key->string value pairs.
     * Calls set_fn(state, key, val) for each pair found. */
    char *p;

    /* Parse "names" object -> populate title->name */
    p = strstr((char *)resp, "\"names\"");
    if (p) {
        p = strchr(p + 7, '{');
        if (p) p++;
        while (p && *p && *p != '}') {
            while (*p == ' ' || *p == '\n' || *p == '\r' || *p == '\t' || *p == ',') p++;
            if (*p != '"' || *p == '}') break;
            p++;
            char key[GAME_ID_LEN]; int key_len = 0;
            while (*p && *p != '"' && key_len < (int)sizeof(key) - 1) key[key_len++] = *p++;
            key[key_len] = '\0'; if (*p == '"') p++;
            while (*p == ' ' || *p == ':') p++;
            if (*p != '"') break; p++;
            char val[MAX_TITLE_LEN]; int val_len = 0;
            while (*p && val_len < (int)sizeof(val) - 1) {
                if (*p == '\\' && *(p+1) == '"') { val[val_len++] = '"'; p += 2; continue; }
                if (*p == '"') break;
                val[val_len++] = *p++;
            }
            val[val_len] = '\0'; if (*p == '"') p++;
            for (int i = 0; i < state->num_titles; i++) {
                if (strcmp(state->titles[i].game_id, key) == 0) {
                    strncpy(state->titles[i].name, val, MAX_TITLE_LEN - 1);
                    state->titles[i].name[MAX_TITLE_LEN - 1] = '\0';
                    break;
                }
            }
        }
    }

    /* Parse "types" object -> set is_psx flag */
    p = strstr((char *)resp, "\"types\"");
    if (p) {
        p = strchr(p + 7, '{');
        if (p) p++;
        while (p && *p && *p != '}') {
            while (*p == ' ' || *p == '\n' || *p == '\r' || *p == '\t' || *p == ',') p++;
            if (*p != '"' || *p == '}') break;
            p++;
            char key[GAME_ID_LEN]; int key_len = 0;
            while (*p && *p != '"' && key_len < (int)sizeof(key) - 1) key[key_len++] = *p++;
            key[key_len] = '\0'; if (*p == '"') p++;
            while (*p == ' ' || *p == ':') p++;
            if (*p != '"') break; p++;
            char val[8]; int val_len = 0;
            while (*p && *p != '"' && val_len < 7) val[val_len++] = *p++;
            val[val_len] = '\0'; if (*p == '"') p++;
            for (int i = 0; i < state->num_titles; i++) {
                if (strcmp(state->titles[i].game_id, key) == 0) {
                    state->titles[i].is_psx = (strcmp(val, "PSX") == 0);
                    break;
                }
            }
        }
    }
}

int network_post_json(const SyncState *state, const char *path,
                      const char *json,
                      uint8_t *out, uint32_t out_size, int *out_len) {
    char url[512];
    snprintf(url, sizeof(url), "%s%s", state->server_url, path);

    int status = 0;
    int r = http_do_request(state, SCE_HTTP_METHOD_POST, url,
                            "application/json",
                            (const uint8_t *)json, strlen(json),
                            out, out_size, &status);
    if (out_len) *out_len = r;
    return (status == 200) ? 0 : -1;
}
