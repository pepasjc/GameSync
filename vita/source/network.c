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

int network_get_save_info(const SyncState *state, const char *game_id,
                          char *hash_out, uint32_t *size_out) {
    char url[512];
    snprintf(url, sizeof(url), "%s/api/v1/saves/%s/meta",
             state->server_url, game_id);

    static uint8_t resp[1024];
    int status = 0;
    int r = http_do_request(state, SCE_HTTP_METHOD_GET, url,
                            NULL, NULL, 0, resp, sizeof(resp) - 1, &status);

    if (status == 404) return 1;   /* no save on server */
    if (r < 0 || status != 200) return -1;

    /* Parse {"save_hash":"...","save_size":...} */
    if (hash_out) {
        char *p = strstr((char *)resp, "\"save_hash\":");
        if (p) {
            char *s = strchr(p + 12, '"');
            if (s) {
                s++;
                char *e = strchr(s, '"');
                if (e && (e - s) <= 64) {
                    strncpy(hash_out, s, e - s);
                    hash_out[e - s] = '\0';
                }
            }
        }
    }
    if (size_out) {
        char *p = strstr((char *)resp, "\"save_size\":");
        if (p) *size_out = (uint32_t)atoi(p + 12);
    }
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
    http_do_request(state, SCE_HTTP_METHOD_POST, url,
                    "application/octet-stream", bundle, bundle_size,
                    resp, sizeof(resp), &status);
    return (status == 200) ? 0 : -1;
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
