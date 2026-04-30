// Network init + REST endpoint wrappers.

#include "network.h"
#include "bundle.h"
#include "http.h"
#include "saves.h"
#include "state.h"

#include <ctype.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <hal/debug.h>
#include <lwip/inet.h>
#include <lwip/netif.h>
#include <lwip/tcpip.h>
#include <nxdk/net.h>
#include <windows.h>

extern struct netif *g_pnetif;

// Persistent last-error buffer. Mutated by every network_* helper; read by
// network_last_error() so the UI can surface a useful message in the status
// bar instead of letting it scroll off the debug console.
static char g_last_error[256] = {0};

static void net_err_clear(void) { g_last_error[0] = '\0'; }
static void net_err_set(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(g_last_error, sizeof(g_last_error), fmt, ap);
    va_end(ap);
}

const char *network_last_error(void) { return g_last_error; }

static int streq_ci(const char *a, const char *b)
{
    if (!a || !b) return 0;
    while (*a && *b) {
        if (tolower((unsigned char)*a) != tolower((unsigned char)*b)) {
            return 0;
        }
        a++;
        b++;
    }
    return *a == '\0' && *b == '\0';
}

static int parse_ipv4_le(const char *s, uint32_t *out)
{
    unsigned a, b, c, d;
    char tail;
    if (!s || !s[0] || !out) return -1;
    if (sscanf(s, "%u.%u.%u.%u%c", &a, &b, &c, &d, &tail) != 4) {
        return -1;
    }
    if (a > 255 || b > 255 || c > 255 || d > 255) return -1;
    *out = (a << 0) | (b << 8) | (c << 16) | (d << 24);
    return 0;
}

static void log_net_addr(const char *label, uint32_t v)
{
    debugPrint("%s %u.%u.%u.%u\n", label,
               (unsigned)((v >>  0) & 0xff),
               (unsigned)((v >>  8) & 0xff),
               (unsigned)((v >> 16) & 0xff),
               (unsigned)((v >> 24) & 0xff));
}

int network_init(const XboxConfig *cfg)
{
    net_err_clear();

    nx_net_parameters_t params;
    nx_net_parameters_t *use_params = NULL;
    memset(&params, 0, sizeof(params));
    params.ipv4_mode = NX_NET_AUTO;
    params.ipv6_mode = NX_NET_AUTO;

    const char *mode = (cfg && cfg->network_mode[0])
                           ? cfg->network_mode
                           : "auto";
    int expect_dhcp = 0;

    if (streq_ci(mode, "dhcp")) {
        params.ipv4_mode = NX_NET_DHCP;
        use_params = &params;
        expect_dhcp = 1;
        debugPrint("Net: nxNetInit (forced DHCP)...\n");
    } else if (streq_ci(mode, "static")) {
        params.ipv4_mode = NX_NET_STATIC;
        params.ipv6_mode = NX_NET_AUTO;
        if (!cfg ||
            parse_ipv4_le(cfg->static_ip, &params.ipv4_ip) != 0 ||
            parse_ipv4_le(cfg->static_netmask, &params.ipv4_netmask) != 0) {
            net_err_set("network: static mode needs static_ip and static_netmask");
            debugPrint("%s\n", network_last_error());
            return -1;
        }
        if (cfg->static_gateway[0]) {
            if (parse_ipv4_le(cfg->static_gateway, &params.ipv4_gateway) != 0) {
                net_err_set("network: bad static_gateway %s", cfg->static_gateway);
                debugPrint("%s\n", network_last_error());
                return -1;
            }
        }
        if (cfg->static_dns1[0]) {
            if (parse_ipv4_le(cfg->static_dns1, &params.ipv4_dns1) != 0) {
                net_err_set("network: bad static_dns1 %s", cfg->static_dns1);
                debugPrint("%s\n", network_last_error());
                return -1;
            }
        }
        if (cfg->static_dns2[0]) {
            if (parse_ipv4_le(cfg->static_dns2, &params.ipv4_dns2) != 0) {
                net_err_set("network: bad static_dns2 %s", cfg->static_dns2);
                debugPrint("%s\n", network_last_error());
                return -1;
            }
        }
        use_params = &params;
        debugPrint("Net: nxNetInit (static)...\n");
        log_net_addr("  IP.....", params.ipv4_ip);
        log_net_addr("  Mask...", params.ipv4_netmask);
        log_net_addr("  GW.....", params.ipv4_gateway);
    } else if (streq_ci(mode, "auto")) {
        expect_dhcp = 1;
        debugPrint("Net: nxNetInit (auto dashboard config)...\n");
    } else {
        net_err_set("network: bad network_mode %s", mode);
        debugPrint("%s\n", network_last_error());
        return -1;
    }

    int rc = nxNetInit(use_params);
    if (rc != 0) {
        if (rc == -2) {
            debugPrint("Net: initial DHCP wait timed out; waiting longer...\n");
        } else {
            net_err_set("network: nxNetInit rc=%d", rc);
            debugPrint("Net: %s\n", network_last_error());
            return -1;
        }
    }

    // Wait up to 30s for an IPv4 address. In DHCP modes, nxdk itself has
    // already waited briefly, but keeping this check makes static and
    // dashboard-config modes report the final address consistently.
    debugPrint(expect_dhcp ? "Net: waiting for IPv4 " : "Net: checking IPv4 ");
    for (int sec = 0; sec < 30; sec++) {
        for (int t = 0; t < 5; t++) {
            if (g_pnetif &&
                !ip4_addr_isany_val(*netif_ip4_addr(g_pnetif))) {
                char ip[16];
                network_local_ip(ip, sizeof(ip));
                debugPrint("\nNet: IP %s\n", ip);
                return 0;
            }
            Sleep(200);
        }
        debugPrint(".");
    }
    debugPrint("\nNet: DHCP timeout (30s).\n");
    debugPrint("     DHCP did not assign an address. Try network_mode=static.\n");
    net_err_set("network: no IPv4 address; set network_mode=static");
    return -2;
}

void network_local_ip(char *out, int out_len)
{
    if (!out || out_len <= 0) return;
    if (!g_pnetif) {
        snprintf(out, out_len, "0.0.0.0");
        return;
    }
    snprintf(out, out_len, "%s", ip4addr_ntoa(netif_ip4_addr(g_pnetif)));
}

static void join_url(const char *base, const char *path,
                     char *buf, int buf_len)
{
    int blen = (int)strlen(base);
    while (blen > 0 && base[blen - 1] == '/') blen--;
    snprintf(buf, buf_len, "%.*s%s", blen, base, path);
}

int network_status_check(const XboxConfig *cfg,
                         char *out_text, int out_text_len)
{
    char url[512];
    join_url(cfg->server_url, "/api/v1/status", url, sizeof(url));

    HttpResponse rsp = http_request(url, HTTP_GET,
                                    cfg->api_key, cfg->console_id,
                                    NULL, NULL, 0);
    int code = rsp.status_code;

    if (out_text && out_text_len > 0) {
        out_text[0] = '\0';
        if (rsp.body && rsp.body_size > 0) {
            const char *body = (const char *)rsp.body;
            char version[32] = "?";
            int  saves = -1;

            const char *vp = strstr(body, "\"version\"");
            if (vp) {
                vp = strchr(vp, ':');
                if (vp) {
                    while (*vp && (*vp == ':' || *vp == ' ' || *vp == '"')) vp++;
                    int i = 0;
                    while (*vp && *vp != '"' && *vp != ',' &&
                           i < (int)sizeof(version) - 1) {
                        version[i++] = *vp++;
                    }
                    version[i] = '\0';
                }
            }
            const char *sp = strstr(body, "\"save_count\"");
            if (!sp) sp = strstr(body, "\"saves\"");
            if (sp) {
                sp = strchr(sp, ':');
                if (sp) sscanf(sp + 1, " %d", &saves);
            }

            if (saves >= 0) {
                snprintf(out_text, out_text_len,
                         "v%s (%d saves)", version, saves);
            } else {
                snprintf(out_text, out_text_len, "v%s", version);
            }
        }
    }

    http_response_free(&rsp);
    return code > 0 ? code : -1;
}

int network_upload_save(const XboxConfig *cfg,
                        const char *title_id,
                        const uint8_t *bundle,
                        uint32_t bundle_size)
{
    net_err_clear();

    char path[128];
    snprintf(path, sizeof(path), "/api/v1/saves/%s", title_id);
    char url[512];
    join_url(cfg->server_url, path, url, sizeof(url));

    HttpResponse rsp = http_request(url, HTTP_POST,
                                    cfg->api_key, cfg->console_id,
                                    "application/octet-stream",
                                    bundle, bundle_size);
    int code = rsp.status_code;
    if (!rsp.success) {
        char preview[160] = "";
        if (rsp.body && rsp.body_size > 0) {
            int n = rsp.body_size < (int)sizeof(preview) - 1
                        ? rsp.body_size : (int)sizeof(preview) - 1;
            memcpy(preview, rsp.body, n);
            preview[n] = '\0';
        }
        if (code <= 0) {
            net_err_set("upload %s: transport fail", title_id);
        } else {
            net_err_set("upload %s: HTTP %d %s",
                        title_id, code, preview);
        }
    }
    http_response_free(&rsp);
    return code > 0 ? code : -1;
}

int network_download_save(const XboxConfig *cfg,
                          const char *title_id,
                          uint8_t **out_data,
                          uint32_t *out_size)
{
    net_err_clear();
    if (out_data) *out_data = NULL;
    if (out_size) *out_size = 0;

    char path[128];
    snprintf(path, sizeof(path), "/api/v1/saves/%s", title_id);
    char url[512];
    join_url(cfg->server_url, path, url, sizeof(url));

    HttpResponse rsp = http_request(url, HTTP_GET,
                                    cfg->api_key, cfg->console_id,
                                    NULL, NULL, 0);
    int code = rsp.status_code;
    if (rsp.success && rsp.body && rsp.body_size > 0) {
        if (out_data) *out_data = rsp.body;
        if (out_size) *out_size = (uint32_t)rsp.body_size;
        rsp.body = NULL;
        rsp.body_size = 0;
    } else {
        char preview[160] = "";
        if (rsp.body && rsp.body_size > 0) {
            int n = rsp.body_size < (int)sizeof(preview) - 1
                        ? rsp.body_size : (int)sizeof(preview) - 1;
            memcpy(preview, rsp.body, n);
            preview[n] = '\0';
        }
        if (code <= 0) {
            net_err_set("download %s: transport fail", title_id);
        } else {
            net_err_set("download %s: HTTP %d %s",
                        title_id, code, preview);
        }
    }
    http_response_free(&rsp);
    return code > 0 ? code : -1;
}

// ---------------------------------------------------------------------------
// Sync plan
// ---------------------------------------------------------------------------

// Append into a growable buffer; bails on overflow rather than reallocating
// (the request stays well under 64 KB for any realistic Xbox library).
static int append(char *buf, int *off, int cap, const char *fmt, ...)
{
    if (*off >= cap) return -1;
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf + *off, cap - *off, fmt, ap);
    va_end(ap);
    if (n < 0 || n >= cap - *off) return -1;
    *off += n;
    return 0;
}

// Find ``"name": [ ... ]`` in ``body`` and copy each quoted string into
// ``out_ids`` (limited to ``max``). Returns the count.
static int parse_string_array(const char *body, const char *name,
                              char (*out_ids)[XBOX_TITLE_ID_LEN + 1],
                              int max)
{
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
            if (len > XBOX_TITLE_ID_LEN) len = XBOX_TITLE_ID_LEN;
            memcpy(out_ids[count], start, len);
            out_ids[count][len] = '\0';
            count++;
            if (*p == '"') p++;
        } else {
            p++;
        }
    }
    return count;
}

static int filter_game_title_ids(char (*ids)[XBOX_TITLE_ID_LEN + 1],
                                 int count)
{
    if (!ids) return 0;
    int out = 0;
    for (int i = 0; i < count; i++) {
        if (!saves_is_game_title_id(ids[i])) continue;
        if (out != i) {
            snprintf(ids[out], XBOX_TITLE_ID_LEN + 1, "%s", ids[i]);
        }
        out++;
    }
    for (int i = out; i < count; i++) {
        ids[i][0] = '\0';
    }
    return out;
}

void sync_plan_free(SyncPlan *p)
{
    if (!p) return;
    free(p->upload_ids);
    free(p->download_ids);
    free(p->conflict_ids);
    free(p->up_to_date_ids);
    free(p->server_only_ids);
    free(p->server_only_names);
    memset(p, 0, sizeof(*p));
}

// ---------------------------------------------------------------------------
// Name lookup
// ---------------------------------------------------------------------------

// Walk a JSON ``"names": { "tid": "Name", ... }`` block and write every
// resolved entry into the parallel ``names`` array (matched by ``ids``).
// Unknown ids are left as empty strings. ``ids`` and ``names`` have
// ``count`` rows.
static void parse_names_object(const char *body,
                               const char (*ids)[XBOX_TITLE_ID_LEN + 1],
                               int count,
                               char (*names)[XBOX_NAME_MAX])
{
    for (int i = 0; i < count; i++) names[i][0] = '\0';

    const char *p = strstr(body, "\"names\"");
    if (!p) return;
    p = strchr(p, '{');
    if (!p) return;
    p++;

    // Walk "key":"value" pairs until we hit the closing brace.
    while (*p && *p != '}') {
        while (*p && *p != '"' && *p != '}') p++;
        if (*p != '"') break;
        p++;
        const char *kstart = p;
        while (*p && *p != '"') p++;
        if (*p != '"') break;
        int klen = (int)(p - kstart);
        char key[XBOX_TITLE_ID_LEN + 1];
        if (klen > XBOX_TITLE_ID_LEN) klen = XBOX_TITLE_ID_LEN;
        memcpy(key, kstart, klen);
        key[klen] = '\0';
        p++;
        while (*p && *p != ':') p++;
        if (!*p) break;
        p++;
        while (*p && *p != '"') p++;
        if (!*p) break;
        p++;
        const char *vstart = p;
        // Names occasionally contain backslash-escaped quotes; honour them.
        while (*p) {
            if (*p == '\\' && p[1]) { p += 2; continue; }
            if (*p == '"') break;
            p++;
        }
        int vlen = (int)(p - vstart);
        if (vlen >= XBOX_NAME_MAX) vlen = XBOX_NAME_MAX - 1;

        // Find this key in ids[] (linear scan; the list rarely exceeds ~256).
        for (int i = 0; i < count; i++) {
            if (strcmp(ids[i], key) == 0) {
                // Strip backslash-escapes when copying.
                int oi = 0;
                for (int j = 0; j < vlen && oi < XBOX_NAME_MAX - 1; j++) {
                    char c = vstart[j];
                    if (c == '\\' && j + 1 < vlen) {
                        c = vstart[++j];
                    }
                    names[i][oi++] = c;
                }
                names[i][oi] = '\0';
                break;
            }
        }
        if (*p == '"') p++;
    }
}

int network_fetch_names(const XboxConfig *cfg,
                        const char (*ids)[XBOX_TITLE_ID_LEN + 1],
                        int count,
                        char (*names)[XBOX_NAME_MAX])
{
    if (!cfg || !ids || !names || count <= 0) return -1;

    int   cap  = 64 * 1024;
    char *json = (char *)malloc(cap);
    if (!json) return -1;
    int   off  = 0;

    if (append(json, &off, cap, "{\"codes\":[") != 0) {
        free(json); return -1;
    }
    for (int i = 0; i < count; i++) {
        if (append(json, &off, cap, "%s\"%s\"", i == 0 ? "" : ",", ids[i]) != 0) {
            free(json); return -1;
        }
    }
    if (append(json, &off, cap, "]}") != 0) {
        free(json); return -1;
    }

    char url[512];
    join_url(cfg->server_url, "/api/v1/titles/names", url, sizeof(url));

    HttpResponse rsp = http_request(url, HTTP_POST,
                                    cfg->api_key, cfg->console_id,
                                    "application/json",
                                    (const uint8_t *)json, (size_t)off);
    free(json);

    if (!rsp.success || !rsp.body) {
        debugPrint("names: HTTP %d\n", rsp.status_code);
        http_response_free(&rsp);
        for (int i = 0; i < count; i++) names[i][0] = '\0';
        return -1;
    }

    parse_names_object((const char *)rsp.body, ids, count, names);
    http_response_free(&rsp);
    return 0;
}

int network_sync_plan(const XboxConfig *cfg,
                      const XboxSaveList *list,
                      SyncPlan *out)
{
    if (!cfg || !list || !out) return -1;
    memset(out, 0, sizeof(*out));
    net_err_clear();

    // Build JSON request.
    int    cap = 64 * 1024;
    char  *json = (char *)malloc(cap);
    if (!json) return -1;
    int    off = 0;

    if (append(json, &off, cap,
               "{\"console_id\":\"%s\",\"platforms\":[\"XBOX\"],\"titles\":[",
               cfg->console_id) != 0) goto fail;

    for (int i = 0; i < list->title_count; i++) {
        const XboxSaveTitle *t = &list->titles[i];

        char hash_hex[XBOX_HASH_BUF];
        uint8_t hash_raw[32];
        if (bundle_compute_save_hash(t, hash_raw, hash_hex) != 0) {
            debugPrint("sync: hash fail for %s\n", t->title_id);
            goto fail;
        }

        char last_hex[XBOX_HASH_BUF] = "";
        int has_last = state_get_last_hash(t->title_id, last_hex);

        if (append(json, &off, cap, "%s{", i == 0 ? "" : ",") != 0) goto fail;
        if (append(json, &off, cap, "\"title_id\":\"%s\",", t->title_id) != 0) goto fail;
        if (append(json, &off, cap, "\"save_hash\":\"%s\",", hash_hex) != 0) goto fail;
        if (append(json, &off, cap, "\"timestamp\":0,") != 0) goto fail;
        if (append(json, &off, cap, "\"size\":%u,", (unsigned)t->total_size) != 0) goto fail;
        if (append(json, &off, cap, "\"console_id\":\"%s\",", cfg->console_id) != 0) goto fail;
        if (has_last) {
            if (append(json, &off, cap, "\"last_synced_hash\":\"%s\"", last_hex) != 0) goto fail;
        } else {
            if (append(json, &off, cap, "\"last_synced_hash\":null") != 0) goto fail;
        }
        if (append(json, &off, cap, "}") != 0) goto fail;
    }
    if (append(json, &off, cap, "]}") != 0) goto fail;

    // POST.
    char url[512];
    join_url(cfg->server_url, "/api/v1/sync", url, sizeof(url));

    HttpResponse rsp = http_request(url, HTTP_POST,
                                    cfg->api_key, cfg->console_id,
                                    "application/json",
                                    (const uint8_t *)json, (size_t)off);
    free(json);
    json = NULL;

    if (!rsp.success || !rsp.body) {
        char preview[160] = "";
        if (rsp.body && rsp.body_size > 0) {
            int n = rsp.body_size < (int)sizeof(preview) - 1
                        ? rsp.body_size : (int)sizeof(preview) - 1;
            memcpy(preview, rsp.body, n);
            preview[n] = '\0';
        }
        if (rsp.status_code <= 0) {
            net_err_set("sync plan: transport fail");
        } else {
            net_err_set("sync plan: HTTP %d %s",
                        rsp.status_code, preview);
        }
        http_response_free(&rsp);
        return -1;
    }

    const char *body = (const char *)rsp.body;

    out->upload_ids        = calloc(SYNC_MAX_TITLES, XBOX_TITLE_ID_LEN + 1);
    out->download_ids      = calloc(SYNC_MAX_TITLES, XBOX_TITLE_ID_LEN + 1);
    out->conflict_ids      = calloc(SYNC_MAX_TITLES, XBOX_TITLE_ID_LEN + 1);
    out->up_to_date_ids    = calloc(SYNC_MAX_TITLES, XBOX_TITLE_ID_LEN + 1);
    out->server_only_ids   = calloc(SYNC_MAX_TITLES, XBOX_TITLE_ID_LEN + 1);
    out->server_only_names = calloc(SYNC_MAX_TITLES, XBOX_NAME_MAX);
    if (!out->upload_ids || !out->download_ids || !out->conflict_ids ||
        !out->up_to_date_ids || !out->server_only_ids ||
        !out->server_only_names) {
        http_response_free(&rsp);
        sync_plan_free(out);
        return -1;
    }

    out->upload_count      = parse_string_array(body, "upload",
                                                out->upload_ids, SYNC_MAX_TITLES);
    out->download_count    = parse_string_array(body, "download",
                                                out->download_ids, SYNC_MAX_TITLES);
    out->conflict_count    = parse_string_array(body, "conflict",
                                                out->conflict_ids, SYNC_MAX_TITLES);
    out->up_to_date_count  = parse_string_array(body, "up_to_date",
                                                out->up_to_date_ids, SYNC_MAX_TITLES);
    out->server_only_count = parse_string_array(body, "server_only",
                                                out->server_only_ids, SYNC_MAX_TITLES);
    out->server_only_count = filter_game_title_ids(out->server_only_ids,
                                                   out->server_only_count);

    http_response_free(&rsp);

    // Resolve names for the server-only entries so the UI can show them.
    if (out->server_only_count > 0) {
        network_fetch_names(cfg,
                            (const char (*)[XBOX_TITLE_ID_LEN + 1])out->server_only_ids,
                            out->server_only_count,
                            out->server_only_names);
    }
    return 0;

fail:
    free(json);
    sync_plan_free(out);
    return -1;
}
