/*
 * PSP Save Sync - Network (WiFi + HTTP)
 *
 * Uses PSP's built-in WiFi with sceNetApctl for connection,
 * and raw TCP sockets for HTTP/1.0 communication with the server.
 */

#include <stdio.h>
#include <string.h>
#include <stdlib.h>

#include <pspkernel.h>
#include <pspdebug.h>
#include <pspmoduleinfo.h>
#include <pspnet.h>
#include <pspnet_inet.h>
#include <pspnet_apctl.h>
#include <pspnet_resolver.h>
#include <pspiofilemgr.h>
#include <psputility.h>
#include <psputility_netmodules.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <arpa/inet.h>
#include <netinet/in.h>
#include <unistd.h>

#include "network.h"
#include "config.h"
#include "saves.h"

#define HTTP_BUF_SIZE   (64 * 1024)   /* 64KB for HTTP headers */
#define RECV_CHUNK      4096

static bool g_net_initialized = false;
static bool g_connected = false;

/* ---- Network init ---- */

static void network_term_partial(void) {
    sceNetApctlTerm();
    sceNetResolverTerm();
    sceNetInetTerm();
    sceNetTerm();
}

/* Scan the export table of the named module for a function by NID.
 * Returns the function address or NULL if not found.
 *
 * PSP PRX module layout (from base of first segment):
 *   SceModuleInfo: attr(2) + ver(2) + name(28) + gp(4) + libent_top(4) + libent_btm(4)
 * Each SceLibEntTable entry:
 *   libname_ptr(4) + ver(2) + attr(2) + len(1) + varcount(1) + funccount(2)
 *   + nid_table_ptr(4) + func_table_ptr(4)  [len is in words, typically 5]
 */
static void *find_export(const char *module_name, uint32_t nid) {
    SceUID ids[64];
    int count = 0;
    if (sceKernelGetModuleIdList(ids, sizeof(ids), &count) < 0) return NULL;

    for (int i = 0; i < count; i++) {
        SceKernelModuleInfo info;
        memset(&info, 0, sizeof(info));
        info.size = sizeof(info);
        if (sceKernelQueryModuleInfo(ids[i], &info) != 0) continue;
        if (strcmp(info.name, module_name) != 0) continue;

        /* SceModuleInfo is at the start of the first loadable segment */
        uint8_t *base = (uint8_t *)info.segmentaddr[0];
        uint32_t libent_top = *(uint32_t *)(base + 36);
        uint32_t libent_btm = *(uint32_t *)(base + 40);

        pspDebugScreenPrintf("  %s seg=%08X ent=%08X..%08X\n",
            module_name, info.segmentaddr[0], libent_top, libent_btm);

        /* Walk export entries */
        uint8_t *ep = (uint8_t *)libent_top;
        while ((uint32_t)ep < libent_btm) {
            uint8_t len_words = ep[8];
            if (len_words == 0) break;

            const char *libname = *(const char **)ep;
            uint16_t funccount  = *(uint16_t *)(ep + 10);
            uint32_t *nids      = *(uint32_t **)(ep + 12);
            uint32_t *funcs     = *(uint32_t **)(ep + 16);

            pspDebugScreenPrintf("    lib='%s' funcs=%d\n",
                libname ? libname : "(null)", funccount);

            for (int j = 0; j < (int)funccount; j++) {
                if (nids[j] == nid) {
                    pspDebugScreenPrintf("    NID %08X -> fn %08X\n", nid, funcs[j]);
                    return (void *)funcs[j];
                }
            }
            ep += len_words * 4;
        }
        return NULL; /* module found, function not in it */
    }
    return NULL;
}

int network_init(void) {
    int ret;

    ret = sceUtilityLoadNetModule(PSP_NET_MODULE_COMMON);
    pspDebugScreenPrintf("LoadNetModule(COMMON): 0x%08X\n", ret);
    ret = sceUtilityLoadNetModule(PSP_NET_MODULE_INET);
    pspDebugScreenPrintf("LoadNetModule(INET):   0x%08X\n", ret);

    /* Try calling sceNetInit via stub (works on PPSSPP / OFW). */
    ret = sceNetInit(0x20000, 0x20, 0x1000, 0x20, 0x1000);
    pspDebugScreenPrintf("sceNetInit (stub):  0x%08X\n", ret);

    if (ret == (int)0x8002013A) {
        /* Stub couldn't resolve "sceNet" library — on PRO-C the module may
         * export under a different name. Scan the export table directly and
         * call sceNetInit by function pointer, bypassing stub resolution. */
        pspDebugScreenPrintf("Trying direct export scan...\n");
        typedef int (*NetInitFn)(int, int, int, int, int);
        NetInitFn fn = (NetInitFn)find_export("sceNet_Library", 0x39AF39A6);
        if (fn) {
            ret = fn(0x20000, 0x20, 0x1000, 0x20, 0x1000);
            pspDebugScreenPrintf("sceNetInit (direct): 0x%08X\n", ret);
        } else {
            pspDebugScreenPrintf("sceNetInit not found in export table\n");
        }
    }

    if (ret != 0) {
        pspDebugScreenPrintf("Network init failed at sceNetInit: 0x%08X\n", ret);
        return ret;
    }

    ret = sceNetInetInit();
    pspDebugScreenPrintf("sceNetInetInit:     0x%08X\n", ret);
    if (ret != 0) { sceNetTerm(); return ret; }

    ret = sceNetResolverInit();
    pspDebugScreenPrintf("sceNetResolverInit: 0x%08X\n", ret);
    if (ret != 0) { network_term_partial(); return ret; }

    ret = sceNetApctlInit(0x1600, 0x42);
    pspDebugScreenPrintf("sceNetApctlInit:    0x%08X\n", ret);
    if (ret != 0) { network_term_partial(); return ret; }

    pspDebugScreenPrintf("Network init OK\n");
    g_net_initialized = true;
    return 0;
}

int network_connect_ap(int ap_index) {
    if (!g_net_initialized) return -1;

    /* PSP access points are indexed 1-3 */
    int psp_ap = ap_index + 1;
    if (psp_ap < 1 || psp_ap > 3) psp_ap = 1;

    int ret = sceNetApctlConnect(psp_ap);
    if (ret < 0) return ret;

    /* Wait for connection */
    int state = 0;
    int retries = 0;
    while (retries++ < 300) {  /* up to ~30 seconds */
        sceNetApctlGetState(&state);
        if (state == PSP_NET_APCTL_STATE_GOT_IP) {
            g_connected = true;
            return 0;
        }
        sceKernelDelayThread(100000);  /* 100ms */
    }
    return -1;
}

void network_disconnect(void) {
    if (g_connected) {
        sceNetApctlDisconnect();
        g_connected = false;
    }
    if (g_net_initialized) {
        sceNetApctlTerm();
        sceNetResolverTerm();
        sceNetInetTerm();
        sceNetTerm();
        g_net_initialized = false;
    }
}

bool network_is_connected(void) {
    return g_connected;
}

/* ---- HTTP client ---- */

/* Parse "http://host:port/path" into host, port, path components. */
static int parse_url(const char *url, char *host, int host_len,
                     int *port, char *path, int path_len) {
    if (strncmp(url, "http://", 7) != 0) return -1;
    const char *p = url + 7;

    /* Find host:port */
    const char *slash = strchr(p, '/');
    const char *colon = strchr(p, ':');

    if (colon && (!slash || colon < slash)) {
        int host_size = colon - p;
        if (host_size >= host_len) return -1;
        strncpy(host, p, host_size);
        host[host_size] = '\0';
        *port = atoi(colon + 1);
    } else {
        int host_size = slash ? (int)(slash - p) : (int)strlen(p);
        if (host_size >= host_len) return -1;
        strncpy(host, p, host_size);
        host[host_size] = '\0';
        *port = 80;
    }

    if (slash)
        strncpy(path, slash, path_len - 1);
    else
        strncpy(path, "/", path_len - 1);
    path[path_len - 1] = '\0';
    return 0;
}

/* Connect a TCP socket to host:port. Returns fd or negative on error. */
static int tcp_connect(const char *host, int port) {
    /* Resolve hostname */
    /* Try direct IP first */
    unsigned int addr = sceNetInetInetAddr(host);
    if (addr == 0xFFFFFFFF) {
        /* Need DNS resolution */
        int rid;
        if (sceNetResolverCreate(&rid, NULL, 0) < 0) return -1;
        if (sceNetResolverStartNtoA(rid, host, (struct in_addr *)&addr, 2, 3) < 0) {
            sceNetResolverDelete(rid);
            return -1;
        }
        sceNetResolverDelete(rid);
    }

    int sock = sceNetInetSocket(AF_INET, SOCK_STREAM, 0);
    if (sock < 0) return -1;

    /* Don't tune SO_RCVBUF/SO_SNDBUF — the PSP TCP stack misbehaves
     * with non-default values (128 KB locks the connection mid-stream;
     * 32 KB causes throughput to wobble worse than the default).
     * Sticking with the kernel default 16 KB recv buffer gives the
     * most stable ~650 KB/s on a clean 802.11g link to a LAN server.
     * The CPU clock bump in main.c (333/333/166 MHz) is what gets us
     * most of the throughput win without touching the socket layer. */

    struct sockaddr_in sa;
    memset(&sa, 0, sizeof(sa));
    sa.sin_family = AF_INET;
    sa.sin_addr.s_addr = addr;
    sa.sin_port = htons(port);

    if (sceNetInetConnect(sock, (struct sockaddr *)&sa, sizeof(sa)) < 0) {
        sceNetInetClose(sock);
        return -1;
    }
    return sock;
}

/* Send all bytes on socket. Returns 0 on success. */
static int tcp_send_all(int sock, const uint8_t *data, int len) {
    int sent = 0;
    while (sent < len) {
        int r = sceNetInetSend(sock, data + sent, len - sent, 0);
        if (r <= 0) return -1;
        sent += r;
    }
    return 0;
}

/* Receive HTTP response. Returns response body length, or negative on error.
 * out receives the body; headers are parsed internally. */
static int http_receive_response(int sock, int *status_out,
                                 uint8_t *out, uint32_t out_size) {
    static char header_buf[HTTP_BUF_SIZE];
    int header_len = 0;
    int content_length = -1;

    /* Read until we find the end of headers (\r\n\r\n) */
    char *hdr_end = NULL;
    while (!hdr_end && header_len < (int)sizeof(header_buf) - 1) {
        int r = sceNetInetRecv(sock, header_buf + header_len,
                               sizeof(header_buf) - header_len - 1, 0);
        if (r <= 0) return -1;
        header_len += r;
        header_buf[header_len] = '\0';
        hdr_end = strstr(header_buf, "\r\n\r\n");
    }
    if (!hdr_end) return -1;

    /* Parse HTTP status line */
    if (sscanf(header_buf, "HTTP/1.%*d %d", status_out) != 1)
        *status_out = 200;

    /* Parse Content-Length */
    char *cl = strstr(header_buf, "Content-Length: ");
    if (cl) content_length = atoi(cl + 16);

    /* Body starts after \r\n\r\n */
    uint8_t *body_start = (uint8_t *)(hdr_end + 4);
    int body_in_header = (int)(header_buf + header_len - (char *)body_start);

    int received = 0;
    if (body_in_header > 0 && out_size > 0) {
        int copy = body_in_header < (int)out_size ? body_in_header : (int)out_size;
        memcpy(out, body_start, copy);
        received = copy;
    }

    /* Continue receiving */
    while (1) {
        if (content_length >= 0 && received >= content_length) break;
        if (received >= (int)out_size) break;  /* buffer full */

        int r = sceNetInetRecv(sock, out + received, out_size - received, 0);
        if (r <= 0) break;
        received += r;
    }

    return received;
}

/* Build and send an HTTP request.
 * method: "GET" or "POST"
 * Returns response body length, or negative on error.
 * status_out: HTTP status code. */
static int http_request(const SyncState *state, const char *method, const char *path,
                        const char *content_type, const uint8_t *body, uint32_t body_size,
                        uint8_t *out, uint32_t out_size, int *status_out) {
    char host[256];
    int port;
    char url_path[256];

    char full_url[512];
    snprintf(full_url, sizeof(full_url), "%s%s", state->server_url, path);

    if (parse_url(full_url, host, sizeof(host), &port, url_path, sizeof(url_path)) < 0)
        return -1;

    int sock = tcp_connect(host, port);
    if (sock < 0) return -1;

    /* Build HTTP request */
    char headers[1024];
    int hlen = snprintf(headers, sizeof(headers),
        "%s %s HTTP/1.0\r\n"
        "Host: %s:%d\r\n"
        "X-API-Key: %s\r\n"
        "X-Console-ID: %s\r\n",
        method, url_path, host, port,
        state->api_key, state->console_id);

    if (body && body_size > 0) {
        hlen += snprintf(headers + hlen, sizeof(headers) - hlen,
            "Content-Type: %s\r\n"
            "Content-Length: %lu\r\n",
            content_type ? content_type : "application/octet-stream",
            (unsigned long)body_size);
    }
    hlen += snprintf(headers + hlen, sizeof(headers) - hlen, "\r\n");

    if (tcp_send_all(sock, (uint8_t *)headers, hlen) < 0) {
        sceNetInetClose(sock);
        return -1;
    }

    if (body && body_size > 0) {
        if (tcp_send_all(sock, body, body_size) < 0) {
            sceNetInetClose(sock);
            return -1;
        }
    }

    int r = http_receive_response(sock, status_out, out, out_size);
    sceNetInetClose(sock);
    return r;
}

/* ---- Public API ---- */

int network_http_get(const SyncState *state, const char *path,
                     uint8_t *out, uint32_t out_size) {
    int status = 0;
    int r = http_request(state, "GET", path, NULL, NULL, 0, out, out_size, &status);
    if (r < 0) return r;
    return (status == 200) ? r : -status;
}

int network_http_post(const SyncState *state, const char *path,
                      const uint8_t *body, uint32_t body_size,
                      uint8_t *out, uint32_t out_size, int *out_len) {
    int status = 0;
    int r = http_request(state, "POST", path, "application/octet-stream",
                         body, body_size, out, out_size, &status);
    if (out_len) *out_len = r;
    if (r < 0) return r;
    return (status == 200) ? 0 : -1;
}

int network_http_post_json(const SyncState *state, const char *path,
                           const char *json,
                           uint8_t *out, uint32_t out_size, int *out_len) {
    int status = 0;
    int r = http_request(state, "POST", path, "application/json",
                         (const uint8_t *)json, strlen(json),
                         out, out_size, &status);
    if (out_len) *out_len = r;
    if (r < 0) return r;
    return (status == 200) ? 0 : -1;
}

bool network_check_server(const SyncState *state) {
    static uint8_t resp[256];
    int r = network_http_get(state, "/api/v1/status", resp, sizeof(resp));
    return r > 0;
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
    char path[256];
    snprintf(path, sizeof(path), "/api/v1/saves/%s/meta", game_id);

    static uint8_t resp[1024];
    int r = network_http_get(state, path, resp, sizeof(resp) - 1);
    if (r <= 0) return (r == -404) ? 1 : -1;

    resp[r] = '\0';
    const char *json = (char *)resp;

    if (hash_out) parse_json_str(json, "save_hash", hash_out, 65);

    char *size_key = strstr(json, "\"save_size\":");
    if (size_key && size_out)
        *size_out = (uint32_t)atoi(size_key + 12);

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

int network_get_sync_plan(const SyncState *state, NetworkSyncPlan *plan) {
    memset(plan, 0, sizeof(*plan));

    /* Build request JSON */
    int json_cap = 64 + state->num_titles * 260;
    char *json = malloc(json_cap);
    if (!json) return -1;

    int pos = snprintf(json, json_cap,
                       "{\"console_id\":\"psp\",\"titles\":[");
    bool first = true;
    for (int i = 0; i < state->num_titles; i++) {
        const TitleInfo *t = &state->titles[i];
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
    int r = network_http_post_json(state, "/api/v1/sync",
                                   json, resp, sizeof(resp) - 1, &resp_len);
    free(json);

    if (r != 0 || resp_len <= 0) return -1;
    resp[resp_len] = '\0';
    const char *resp_str = (char *)resp;

    plan->upload_count   = parse_id_array(resp_str, "upload",   plan->upload,   SYNC_PLAN_MAX);
    plan->download_count = parse_id_array(resp_str, "download", plan->download, SYNC_PLAN_MAX);
    plan->conflict_count = parse_id_array(resp_str, "conflict", plan->conflict, SYNC_PLAN_MAX);
    return 0;
}

int network_upload_save(const SyncState *state, TitleInfo *title,
                        const uint8_t *bundle, uint32_t bundle_size) {
    char path[256];
    snprintf(path, sizeof(path), "/api/v1/saves/%s?force=true&source=psp", title->game_id);

    static uint8_t resp[512];
    int out_len;
    return network_http_post(state, path, bundle, bundle_size, resp, sizeof(resp), &out_len);
}

int network_download_save(const SyncState *state, const char *game_id,
                          uint8_t *out, uint32_t out_size) {
    char path[256];
    snprintf(path, sizeof(path), "/api/v1/saves/%s", game_id);
    return network_http_get(state, path, out, out_size);
}

void network_fetch_names(SyncState *state) {
    if (!state || state->num_titles == 0) return;

    /* Build {"codes":["ID1","ID2",...]} */
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

    static uint8_t resp[65536];
    int resp_len = 0;
    int r = network_http_post_json(state, "/api/v1/titles/names",
                                   json, resp, sizeof(resp) - 1, &resp_len);
    free(json);

    if (r != 0 || resp_len <= 0) return;
    resp[resp_len] = '\0';

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
                    state->titles[i].is_psx = (strcmp(val, "PS1") == 0 || strcmp(val, "PSX") == 0);
                    break;
                }
            }
        }
    }
}

static bool has_title(const SyncState *state, const char *game_id) {
    for (int i = 0; i < state->num_titles; i++) {
        if (strcmp(state->titles[i].game_id, game_id) == 0)
            return true;
    }
    return false;
}

/* Server-only titles are materialized as placeholder entries so the user can
 * download them even when ms0:/PSP/SAVEDATA/<GAMEID> does not exist yet. */
static void add_server_title(SyncState *state, const char *game_id, const char *console_type) {
    if (!game_id || !game_id[0]) return;
    if (!saves_is_valid_game_id(game_id)) return;
    if (console_type && console_type[0] && strcmp(console_type, "VITA") == 0) return;
    if (has_title(state, game_id)) return;
    if (state->num_titles >= MAX_TITLES) return;

    TitleInfo *t = &state->titles[state->num_titles++];
    memset(t, 0, sizeof(*t));
    strncpy(t->game_id, game_id, GAME_ID_LEN - 1);
    strncpy(t->name, game_id, MAX_TITLE_LEN - 1);
    snprintf(t->save_dir, SAVE_DIR_LEN, "%s/%s", SAVEDATA_PATH, game_id);
    t->is_psx = saves_is_psx_prefix(game_id);
    t->server_only = true;
    t->on_server = true;
}

/* Merge the server title catalog into the local list so PSP/PS1 saves remain
 * visible and downloadable even when there is no local save directory yet.
 *
 * Filters server-side via ?console_type so the response stays small
 * (avoids the 256 KB buffer truncating tail entries on a server with
 * many non-PSP/PS1 saves) and so we don't waste cycles parsing 3DS /
 * NDS / MiSTer rows that ``saves_is_valid_game_id`` would reject. */
int network_merge_server_titles(SyncState *state,
                                int *out_added, int *out_seen) {
    if (out_added) *out_added = 0;
    if (out_seen)  *out_seen  = 0;
    if (!state) return -1;

    /* 512 KB resp buffer — saves response with PSP+PS1 filter rarely
     * exceeds 64 KB, but oversize so future growth doesn't silently
     * truncate the tail (which is exactly how new uploads vanish from
     * the merge). */
    static uint8_t resp[512 * 1024];
    int r = network_http_get(state,
                             "/api/v1/titles?console_type=PSP&console_type=PSX&console_type=PS1",
                             resp, sizeof(resp) - 1);
    if (r <= 0) return -1;
    resp[r] = '\0';

    int seen = 0;
    int before_count = state->num_titles;

    char *p = (char *)resp;
    while ((p = strstr(p, "\"title_id\"")) != NULL) {
        /* Find the enclosing object's bounds.  Walk forward from the
         * key, balancing { / } across nested arrays so a stray '}'
         * inside a string value doesn't cut us off early. */
        char *object_end = NULL;
        {
            int depth = 1;
            char *q = p;
            /* Backtrack to find object opener. */
            while (q > (char *)resp && *q != '{') q--;
            if (*q != '{') break;
            char *scan = q + 1;
            bool in_string = false;
            while (*scan) {
                if (in_string) {
                    if (*scan == '\\' && scan[1]) { scan += 2; continue; }
                    if (*scan == '"') in_string = false;
                } else {
                    if (*scan == '"') in_string = true;
                    else if (*scan == '{') depth++;
                    else if (*scan == '}') {
                        depth--;
                        if (depth == 0) { object_end = scan; break; }
                    }
                }
                scan++;
            }
        }

        p = strchr(p, ':');
        if (!p) break;
        p++;
        while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') p++;
        if (*p != '"') continue;
        p++;

        char game_id[GAME_ID_LEN];
        int len = 0;
        while (*p && *p != '"' && len < GAME_ID_LEN - 1)
            game_id[len++] = *p++;
        game_id[len] = '\0';

        char console_type[16] = "";
        if (object_end) {
            char *type_key = strstr(p, "\"console_type\"");
            if (type_key && type_key < object_end) {
                type_key = strchr(type_key, ':');
                if (type_key) {
                    type_key++;
                    while (*type_key == ' ' || *type_key == '\t' || *type_key == '\r' || *type_key == '\n')
                        type_key++;
                    if (*type_key == '"') {
                        type_key++;
                        int tlen = 0;
                        while (*type_key && *type_key != '"' && tlen < (int)sizeof(console_type) - 1)
                            console_type[tlen++] = *type_key++;
                        console_type[tlen] = '\0';
                    }
                }
            }
        }

        seen++;
        add_server_title(state, game_id, console_type);
    }

    if (out_seen)  *out_seen  = seen;
    if (out_added) *out_added = state->num_titles - before_count;
    return 0;
}

/* ============================================================
 * ROM catalog + streaming download (Range-resumable)
 * ============================================================ */

static NetProgress64Fn g_progress64_cb = NULL;

void network_set_progress64_cb(NetProgress64Fn cb) { g_progress64_cb = cb; }

int network_fetch_rom_catalog(const SyncState *state,
                              const char *system_code,
                              int offset, int limit,
                              char *out, uint32_t out_size,
                              int *status_out) {
    if (!state || !out || out_size < 2) return -1;
    if (offset < 0) offset = 0;
    if (limit  < 0) limit  = 0;

    char path[256];
    int  pos = 0;
    pos += snprintf(path + pos, sizeof(path) - pos, "/api/v1/roms?");
    if (system_code && system_code[0]) {
        pos += snprintf(path + pos, sizeof(path) - pos, "system=%s&", system_code);
    }
    if (limit > 0) {
        pos += snprintf(path + pos, sizeof(path) - pos, "limit=%d&", limit);
    }
    pos += snprintf(path + pos, sizeof(path) - pos, "offset=%d", offset);

    int status = 0;
    int n = http_request(state, "GET", path,
                         NULL, NULL, 0,
                         (uint8_t *)out, out_size, &status);
    if (status_out) *status_out = status;
    if (n < 0 || status != 200) {
        return n < 0 ? n : -1;
    }
    return n;
}

int network_fetch_rom_manifest(const SyncState *state,
                               const char *rom_id,
                               char *out, uint32_t out_size,
                               int *status_out) {
    if (!state || !rom_id || !out || out_size < 2) return -1;
    char path[256];
    snprintf(path, sizeof(path), "/api/v1/roms/%s/manifest", rom_id);

    int status = 0;
    int n = http_request(state, "GET", path,
                         NULL, NULL, 0,
                         (uint8_t *)out, out_size, &status);
    if (status_out) *status_out = status;
    if (n < 0 || status != 200) {
        return n < 0 ? n : -1;
    }
    return n;
}

int network_trigger_rom_scan(const SyncState *state, int *count_out) {
    if (count_out) *count_out = -1;
    if (!state) return -1;
    static uint8_t resp[4096];
    int status = 0;
    int n = http_request(state, "GET", "/api/v1/roms/scan",
                         NULL, NULL, 0, resp, sizeof(resp), &status);
    if (n < 0 || status != 200) return n < 0 ? n : -1;
    if (count_out) {
        const char *p = strstr((const char *)resp, "\"count\"");
        if (p) {
            const char *colon = strchr(p, ':');
            if (colon) *count_out = atoi(colon + 1);
        }
    }
    return 0;
}

/* Carry-over body bytes that arrived alongside the response headers.
 * Lives at file scope so the streaming recv helper can find them. */
static uint8_t *g_stream_carry     = NULL;
static int      g_stream_carry_len = 0;
static int      g_stream_carry_pos = 0;

/* Internal: open a TCP connection, send GET with optional Range, leave
 * the socket positioned at the start of the response body so the
 * caller can stream it.  Returns socket fd on success or -1 on error. */
static int http_open_get_stream(const SyncState *state,
                                const char *api_path,
                                uint64_t range_start,
                                int *status_out,
                                int64_t *content_len_out) {
    char host[256];
    int port;
    char url_path[256];
    char full_url[512];
    snprintf(full_url, sizeof(full_url), "%s%s", state->server_url, api_path);
    if (parse_url(full_url, host, sizeof(host), &port,
                  url_path, sizeof(url_path)) < 0) {
        return -1;
    }

    int sock = tcp_connect(host, port);
    if (sock < 0) return -1;

    char hdr[1024];
    int  hlen;
    if (range_start > 0) {
        hlen = snprintf(hdr, sizeof(hdr),
            "GET %s HTTP/1.0\r\n"
            "Host: %s:%d\r\n"
            "X-API-Key: %s\r\n"
            "X-Console-ID: %s\r\n"
            "Range: bytes=%llu-\r\n"
            "Connection: close\r\n"
            "\r\n",
            url_path, host, port,
            state->api_key, state->console_id,
            (unsigned long long)range_start);
    } else {
        hlen = snprintf(hdr, sizeof(hdr),
            "GET %s HTTP/1.0\r\n"
            "Host: %s:%d\r\n"
            "X-API-Key: %s\r\n"
            "X-Console-ID: %s\r\n"
            "Connection: close\r\n"
            "\r\n",
            url_path, host, port,
            state->api_key, state->console_id);
    }

    if (tcp_send_all(sock, (uint8_t *)hdr, hlen) < 0) {
        sceNetInetClose(sock);
        return -1;
    }

    /* Walk headers byte-by-byte until \r\n\r\n.  Same trick as
     * http_receive_response but we keep the socket open and don't
     * pre-read body bytes into a buffer. */
    static char header_buf[HTTP_BUF_SIZE];
    int header_len = 0;
    char *hdr_end = NULL;
    while (!hdr_end && header_len < (int)sizeof(header_buf) - 1) {
        int r = sceNetInetRecv(sock, header_buf + header_len,
                               sizeof(header_buf) - header_len - 1, 0);
        if (r <= 0) { sceNetInetClose(sock); return -1; }
        header_len += r;
        header_buf[header_len] = '\0';
        hdr_end = strstr(header_buf, "\r\n\r\n");
    }
    if (!hdr_end) { sceNetInetClose(sock); return -1; }

    int status = 200;
    if (sscanf(header_buf, "HTTP/1.%*d %d", &status) != 1) status = 200;
    if (status_out) *status_out = status;

    int64_t content_length = -1;
    char *cl = strstr(header_buf, "Content-Length: ");
    if (cl) content_length = strtoll(cl + 16, NULL, 10);
    if (content_len_out) *content_len_out = content_length;

    if (status != 200 && status != 206) {
        sceNetInetClose(sock);
        return -1;
    }

    /* Some bytes of body may already be in header_buf past \r\n\r\n —
     * we have to keep them.  Stash a pointer + length pair so the
     * streamer drains them before the next sceNetInetRecv. */
    g_stream_carry_pos = 0;
    g_stream_carry_len = (int)(header_buf + header_len - (hdr_end + 4));
    if (g_stream_carry_len > 0) {
        g_stream_carry = (uint8_t *)(hdr_end + 4);
    } else {
        g_stream_carry = NULL;
        g_stream_carry_len = 0;
    }

    return sock;
}

static int stream_recv(int sock, uint8_t *buf, int max) {
    /* First drain any carry-over body bytes from the header read. */
    if (g_stream_carry && g_stream_carry_pos < g_stream_carry_len) {
        int avail = g_stream_carry_len - g_stream_carry_pos;
        int copy = avail < max ? avail : max;
        memcpy(buf, g_stream_carry + g_stream_carry_pos, copy);
        g_stream_carry_pos += copy;
        if (g_stream_carry_pos >= g_stream_carry_len) {
            g_stream_carry = NULL;
            g_stream_carry_len = g_stream_carry_pos = 0;
        }
        return copy;
    }
    return sceNetInetRecv(sock, buf, max, 0);
}

/* Shared streaming core — used by single-file ROM and per-bundle-file
 * downloads.  Caller provides the absolute API path (with any query
 * string already appended). */
static int download_stream_to_file(const SyncState *state,
                                   const char *api_path,
                                   const char *target_path,
                                   uint64_t start_offset,
                                   uint64_t *total_out) {
    int status = 0;
    int64_t content_length = -1;
    int sock = http_open_get_stream(state, api_path,
                                    start_offset, &status, &content_length);
    if (sock < 0) {
        if (status == 416 || status == 404) return -3;
        return -1;
    }

    bool resumed = (status == 206);
    uint64_t written = resumed ? start_offset : 0;
    uint64_t expected_total =
        (content_length >= 0)
            ? ((uint64_t)content_length + (resumed ? start_offset : 0))
            : 0;
    if (total_out) *total_out = expected_total;

    char part_path[512];
    snprintf(part_path, sizeof(part_path), "%s.part", target_path);

    FILE *fp = fopen(part_path, resumed ? "ab" : "wb");
    if (!fp) {
        sceNetInetClose(sock);
        return -2;
    }

    static uint8_t chunk[65536];
    int rc = 0;
    while (1) {
        int n = stream_recv(sock, chunk, sizeof(chunk));
        if (n > 0) {
            size_t w = fwrite(chunk, 1, (size_t)n, fp);
            if (w != (size_t)n) { rc = -2; break; }
            written += (uint64_t)n;
            if (g_progress64_cb &&
                g_progress64_cb(written, expected_total))
            {
                rc = 1;
                break;
            }
            if (expected_total > 0 && written >= expected_total) break;
        } else if (n == 0) {
            break;
        } else {
            rc = -1;
            break;
        }
    }

    fclose(fp);
    sceNetInetClose(sock);

    if (rc != 0) return rc;

    if (expected_total > 0 && written < expected_total) {
        return 1;
    }

    /* Atomic rename via sceIoRename — libc rename() on PSP is mapped
     * onto FAT operations that have been observed to return success
     * while leaving the source ``.part`` in place (the entry shows up
     * under the target name but the .part file is not unlinked).
     * sceIoRename is the underlying syscall and is reliable; we still
     * unlink the target first to handle the "already exists" case
     * since FAT rename refuses to overwrite. */
    sceIoRemove(target_path);
    int ren = sceIoRename(part_path, target_path);
    if (ren < 0) {
        /* Fallback to libc rename in case sceIoRename is unavailable
         * or refuses for some other reason. */
        if (rename(part_path, target_path) != 0) return -2;
    }

    /* Belt-and-braces: if either rename path silently kept the
     * source file (FAT driver bug we've seen before), nuke it now so
     * the user doesn't see a stray .part next to the final file. */
    {
        struct stat st;
        if (stat(part_path, &st) == 0) {
            sceIoRemove(part_path);
        }
    }

    if (total_out) *total_out = written;
    return 0;
}

int network_download_rom_resumable(const SyncState *state,
                                   const char *rom_id,
                                   const char *extract_fmt,
                                   const char *target_path,
                                   uint64_t start_offset,
                                   uint64_t *total_out) {
    if (!state || !rom_id || !target_path) return -1;
    char api_path[512];
    if (extract_fmt && extract_fmt[0]) {
        snprintf(api_path, sizeof(api_path),
                 "/api/v1/roms/%s?extract=%s", rom_id, extract_fmt);
    } else {
        snprintf(api_path, sizeof(api_path), "/api/v1/roms/%s", rom_id);
    }
    return download_stream_to_file(state, api_path, target_path,
                                   start_offset, total_out);
}

int network_download_bundle_file_resumable(const SyncState *state,
                                           const char *rom_id,
                                           const char *bundle_file,
                                           const char *target_path,
                                           uint64_t start_offset,
                                           uint64_t *total_out) {
    if (!state || !rom_id || !bundle_file || !target_path) return -1;
    char api_path[768];
    snprintf(api_path, sizeof(api_path),
             "/api/v1/roms/%s/file/%s", rom_id, bundle_file);
    return download_stream_to_file(state, api_path, target_path,
                                   start_offset, total_out);
}
