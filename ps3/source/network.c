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
#include <net/netctl.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <netdb.h>

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>
#include <ctype.h>
#include <errno.h>


/* If socketclose() is required by your PSL1GHT version, change this: */
#define NET_CLOSE(s)  close(s)

/* TCP connect timeout in seconds */
#define CONNECT_TIMEOUT_SEC  8

/* Maximum response body size for bundle downloads (8 MB) */
#define HTTP_RESP_SIZE  (8 * 1024 * 1024)

#define WEBMAN_HOST             "127.0.0.1"
#define WEBMAN_PORT             80
#define WEBMAN_FAKE_USB_PATH    "/dev_hdd0/tmp/fakeusb"

static bool             g_initialized   = false;
static NetProgressFn    g_progress_cb   = NULL;
static NetProgress64Fn  g_progress64_cb = NULL;

void network_set_progress_cb(NetProgressFn cb)     { g_progress_cb   = cb; }
void network_set_progress64_cb(NetProgress64Fn cb) { g_progress64_cb = cb; }

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

    /* Enlarge socket buffers — PS3 defaults are tiny (likely 8-16 KB)
     * which limits TCP window size and tanks throughput on wired LAN.
     * 256 KB each lets the kernel pipeline enough data to keep wired
     * 100 Mbit saturated without the sender stalling on window pressure. */
    int bufsize = 256 * 1024;
    setsockopt(s, SOL_SOCKET, SO_RCVBUF, &bufsize, sizeof(bufsize));
    setsockopt(s, SOL_SOCKET, SO_SNDBUF, &bufsize, sizeof(bufsize));

    /* Set send timeout so blocking connect doesn't hang indefinitely.
     * On LAN, ENETUNREACH comes back immediately anyway; this covers the
     * rare case where a SYN is dropped with no RST. */
    struct timeval stv = { .tv_sec = CONNECT_TIMEOUT_SEC, .tv_usec = 0 };
    setsockopt(s, SOL_SOCKET, SO_SNDTIMEO, &stv, sizeof(stv));

    if (connect(s, (struct sockaddr *)&addr, sizeof(addr)) != 0) {
        debug_log("net: connect(%s:%d) failed err=%d", host, port, errno);
        NET_CLOSE(s);
        return -1;
    }

    /* 1-second recv timeout so the body read loop can pump callbacks
     * between chunks without blocking indefinitely. */
    struct timeval rtv = { .tv_sec = 1, .tv_usec = 0 };
    setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, &rtv, sizeof(rtv));

    /* 1-second send timeout so upload chunks don't block indefinitely.
     * On real PS3 hardware (unlike RPCS3), a stalled send() without
     * sysutil pumping causes the firmware to consider the app frozen. */
    struct timeval stv_send = { .tv_sec = 1, .tv_usec = 0 };
    setsockopt(s, SOL_SOCKET, SO_SNDTIMEO, &stv_send, sizeof(stv_send));

    return s;
}

/* ---- Send all bytes (with callback pumping) ----
 *
 * Sends in 128KB chunks.  Between chunks (and on SO_SNDTIMEO timeouts)
 * the progress callback is invoked so sysUtilCheckCallback() gets called.
 * On real PS3 firmware, failing to pump sysutil for several seconds while
 * blocking in send() makes the system consider the app frozen — this does
 * NOT happen on RPCS3. */

static bool send_all(int s, const uint8_t *buf, uint32_t size) {
    uint32_t sent = 0;
    while (sent < size) {
        uint32_t chunk = size - sent;
        if (chunk > 131072) chunk = 131072;

        int n = (int)send(s, buf + sent, chunk, 0);
        if (n > 0) {
            sent += (uint32_t)n;
            /* Pump callbacks between chunks so the OS knows we're alive */
            if (g_progress_cb)
                g_progress_cb(sent, (int)size);
        } else if (n == 0) {
            return false;  /* connection closed */
        } else {
            /* SO_SNDTIMEO fired — pump callbacks, keep trying */
            if (g_progress_cb && g_progress_cb(sent, (int)size))
                return false;  /* abort requested */
            if (errno != EAGAIN && errno != EWOULDBLOCK && errno != ETIMEDOUT)
                return false;  /* hard error */
        }
    }
    return true;
}

/* ---- Read one line (strips \r\n) ----
 *
 * recv() inherits the socket's 1-second SO_RCVTIMEO so the body loop can
 * pump sysutil between chunks.  But the same timeout fires here too: if
 * the server takes longer than 1 s to start sending (slow endpoints like
 * /api/v1/roms which iterate the catalog before responding), the very
 * first recv returns EAGAIN and the old implementation bailed with an
 * empty status line — leading to ``HTTP 0`` even though bytes arrived
 * later.
 *
 * Retry on EAGAIN/EWOULDBLOCK/ETIMEDOUT, pumping the progress callback
 * each iteration so sysutil stays alive, and only bail after a hard
 * error or a long total wait (60 retries * 1 s = 60 s — generous enough
 * to cover catalog cold starts on a Pi). */
static int read_line(int s, char *buf, int max) {
    int len = 0;
    int retries = 60;
    while (len < max - 1 && retries > 0) {
        char c;
        int n = (int)recv(s, &c, 1, 0);
        if (n > 0) {
            if (c == '\n') break;
            if (c != '\r') buf[len++] = c;
            continue;
        }
        if (n == 0) break;  /* server closed */

        if (errno != EAGAIN && errno != EWOULDBLOCK && errno != ETIMEDOUT) {
            break;
        }
        /* Soft timeout — pump sysutil and try again. */
        if (g_progress_cb) {
            if (g_progress_cb(0, -1)) break;  /* abort requested */
        }
        retries--;
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
        debug_log("net: send_all hdr failed for %s %s errno=%d",
                  method, api_path, errno);
        NET_CLOSE(s); return -1;
    }
    if (body && body_size > 0) {
        if (!send_all(s, body, body_size)) {
            debug_log("net: send_all body failed for %s %s errno=%d",
                      method, api_path, errno);
            NET_CLOSE(s); return -1;
        }
    }

    /* Read status line — pump callbacks afterward */
    char line[512];
    read_line(s, line, sizeof(line));
    if (g_progress_cb) g_progress_cb(0, -1);

    int status = 0;
    const char *sp = strchr(line, ' ');
    if (sp) status = atoi(sp + 1);
    if (status_out) *status_out = status;
    debug_log("net: %s %s -> HTTP %d", method, api_path, status);

    /* Read headers, look for Content-Length.
     * Pump callbacks between header lines so the firmware doesn't
     * consider us frozen during slow responses on real hardware. */
    int content_length = -1;
    while (1) {
        int n = read_line(s, line, sizeof(line));
        if (n == 0) break;
        if (strncasecmp(line, "Content-Length:", 15) == 0)
            content_length = atoi(line + 15);
        if (g_progress_cb) g_progress_cb(0, -1);
    }

    /* Read body. SO_RCVTIMEO is 1 second so each recv() returns promptly,
     * allowing the progress callback to pump sysutil between chunks.
     * On real PS3 firmware (unlike RPCS3), failure to call
     * sysUtilCheckCallback() for several seconds causes the system to
     * consider the app frozen.  We therefore pump callbacks on EVERY
     * recv iteration — not just for large responses. */
    int total = 0;
    if (resp_buf && resp_buf_size > 0) {
        uint32_t cap  = resp_buf_size - 1;
        uint32_t want = (content_length > 0 && (uint32_t)content_length < cap)
                        ? (uint32_t)content_length : cap;

        while ((uint32_t)total < want) {
            uint32_t chunk = want - (uint32_t)total;
            if (chunk > 262144) chunk = 262144;
            int n = (int)recv(s, resp_buf + total, chunk, 0);
            if (n > 0) {
                total += n;
            } else if (n == 0) {
                break;  /* server closed connection */
            } else {
                /* Hard error (not a timeout) — give up */
                if (errno != EAGAIN && errno != EWOULDBLOCK && errno != ETIMEDOUT)
                    break;
            }
            /* Always pump callbacks so the PS3 OS doesn't kill us */
            if (g_progress_cb) {
                if (g_progress_cb((uint32_t)total, content_length))
                    { NET_CLOSE(s); return -1; }
            }
        }
        resp_buf[total] = '\0';
    }

    NET_CLOSE(s);
    return total;
}

static bool http_local_get(const char *host, int port, const char *path, int *status_out) {
    int s;
    char line[512];
    char hdr[512];
    int hlen;
    int status = 0;

    s = tcp_connect(host, port);
    if (s < 0) {
        debug_log("webman: connect failed for %s:%d", host, port);
        return false;
    }

    hlen = snprintf(hdr, sizeof(hdr),
        "GET %s HTTP/1.0\r\n"
        "Host: %s:%d\r\n"
        "Connection: close\r\n"
        "\r\n",
        path, host, port);
    if (!send_all(s, (const uint8_t *)hdr, (uint32_t)hlen)) {
        debug_log("webman: send failed for %s", path);
        NET_CLOSE(s);
        return false;
    }

    read_line(s, line, sizeof(line));
    if (line[0]) {
        const char *sp = strchr(line, ' ');
        if (sp) {
            status = atoi(sp + 1);
        }
    }
    if (status_out) {
        *status_out = status;
    }
    debug_log("webman: GET %s -> HTTP %d", path, status);

    while (read_line(s, line, sizeof(line)) > 0) {
    }

    while (recv(s, line, sizeof(line), 0) > 0) {
    }

    NET_CLOSE(s);
    return status >= 200 && status < 400;
}

bool network_activate_fake_usb(void) {
    bool remap_ok;
    bool eject_ok;
    bool insert_ok;

    remap_ok = http_local_get(
        WEBMAN_HOST,
        WEBMAN_PORT,
        "/remap.ps3?src=/dev_usb000&to=" WEBMAN_FAKE_USB_PATH,
        NULL
    );
    if (!remap_ok) {
        debug_log("webman: fake usb remap unavailable");
        return false;
    }

    eject_ok = http_local_get(
        WEBMAN_HOST,
        WEBMAN_PORT,
        "/xmb.ps3$eject/dev_usb000",
        NULL
    );
    if (!eject_ok) {
        debug_log("webman: fake usb eject failed (continuing)");
    }

    insert_ok = http_local_get(
        WEBMAN_HOST,
        WEBMAN_PORT,
        "/xmb.ps3$insert/dev_usb000",
        NULL
    );
    if (!insert_ok) {
        debug_log("webman: fake usb insert failed");
    }

    return remap_ok && insert_ok;
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

    /* Best-effort: wait for IP to be assigned (state 3 = IPObtained).
     * On HEN the XMB already has a connection; DHCP usually completes fast.
     * netCtlInit may return non-zero if already initialised — that's fine. */
    sysModuleLoad(SYSMODULE_NETCTL);
    netCtlInit();

    s32 ctl_state = 0;
    netCtlGetState(&ctl_state);
    debug_log("net: netctl state=%d", (int)ctl_state);

    if (ctl_state != NET_CTL_STATE_IPObtained) {
        debug_log("net: waiting for IP (state=%d)...", (int)ctl_state);
        /* Wait up to 15 seconds (NET_CTL_STATE_IPObtained == 3) */
        for (int i = 0; i < 150 && ctl_state != NET_CTL_STATE_IPObtained; i++) {
            usleep(100000);
            netCtlGetState(&ctl_state);
        }
        debug_log("net: netctl final state=%d", (int)ctl_state);
    }

    /* Log diagnostic info regardless of state */
    {
        union net_ctl_info info;
        if (netCtlGetInfo(NET_CTL_INFO_IP_ADDRESS, &info) == 0)
            debug_log("net: IP address = %s", info.ip_address);
        else
            debug_log("net: IP address = (unavailable)");
        if (netCtlGetInfo(NET_CTL_INFO_DEFAULT_ROUTE, &info) == 0)
            debug_log("net: gateway    = %s", info.default_route);
        if (netCtlGetInfo(NET_CTL_INFO_DEVICE, &info) == 0)
            debug_log("net: device     = %s", info.device == NET_CTL_DEVICE_WIRELESS ? "WiFi" : "Wired");
    }

    netCtlTerm();
    sysModuleUnload(SYSMODULE_NETCTL);

    g_initialized = true;
    debug_log("net: initialized (netctl final state=%d)", (int)ctl_state);
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

static bool title_uses_ps1_card_api(const TitleInfo *title) {
    return title &&
           (title->kind == SAVE_KIND_PS1 || title->kind == SAVE_KIND_PS1_VM1);
}

int network_get_save_info(const SyncState *state, const TitleInfo *title,
                           char *hash_out, uint32_t *size_out,
                           char *last_sync_out) {
    char path[256];
    const char *title_id;

    if (!title) {
        return -1;
    }

    title_id = title->title_id;
    if (title_uses_ps1_card_api(title)) {
        snprintf(path, sizeof(path), "/api/v1/saves/%s/ps1-card/meta?slot=0", title_id);
    } else {
        snprintf(path, sizeof(path), "/api/v1/saves/%s/meta", title_id);
    }

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
        if (!last_sync_out[0]) {
            parse_json_str(json, "server_timestamp", last_sync_out, 32);
        }
    }
    return 0;
}

int network_get_save_manifest(const SyncState *state, const char *title_id,
                              char *manifest_out, uint32_t manifest_out_size) {
    char path[256];
    int status = 0;
    int r;

    if (!manifest_out || manifest_out_size == 0) {
        return -1;
    }

    snprintf(path, sizeof(path), "/api/v1/saves/%s/manifest", title_id);
    r = http_request(state, "GET", path,
                     NULL, NULL, 0,
                     (uint8_t *)manifest_out, manifest_out_size - 1, &status);
    if (status == 404) {
        manifest_out[0] = '\0';
        return 1;
    }
    if (r < 0 || status != 200) {
        manifest_out[0] = '\0';
        return (status > 0) ? -status : -1;
    }

    manifest_out[r] = '\0';
    return 0;
}

int network_upload_save(const SyncState *state, const char *title_id,
                         const uint8_t *bundle, uint32_t bundle_size) {
    char path[256];
    snprintf(path, sizeof(path), "/api/v1/saves/%s?force=true&source=ps3",
             title_id);

    static uint8_t resp[512];
    int status = 0;
    http_request(state, "POST", path,
                 "application/octet-stream", bundle, bundle_size,
                 resp, sizeof(resp), &status);
    return (status == 200) ? 0 : (status > 0 ? status : -1);
}

int network_upload_ps1_card(const SyncState *state, const TitleInfo *title,
                            const uint8_t *card_data, uint32_t card_size) {
    char path[256];

    if (!title || !card_data) {
        return -1;
    }

    snprintf(path, sizeof(path), "/api/v1/saves/%s/ps1-card?slot=0",
             title->title_id);

    static uint8_t resp[512];
    int status = 0;
    http_request(state, "POST", path,
                 "application/octet-stream", card_data, card_size,
                 resp, sizeof(resp), &status);
    return (status == 200) ? 0 : (status > 0 ? status : -1);
}

int network_download_save(const SyncState *state, const char *title_id,
                           uint8_t *out, uint32_t out_size) {
    char path[256];
    snprintf(path, sizeof(path), "/api/v1/saves/%s", title_id);

    int status = 0;
    int r = http_request(state, "GET", path,
                         NULL, NULL, 0, out, out_size, &status);
    if (status != 200) return (status > 0) ? -status : -1;
    return r;
}

int network_download_ps1_card(const SyncState *state, const TitleInfo *title,
                              uint8_t *out, uint32_t out_size) {
    char path[256];
    int status = 0;

    if (!title || !out) {
        return -1;
    }

    snprintf(path, sizeof(path), "/api/v1/saves/%s/ps1-card?slot=0",
             title->title_id);

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
 *   2. Exact game_code match, but only when the server_id is the bare 9-char
 *      product code for a legacy save entry
 * Returns index into state->titles, or -1 if not found.
 */
static int find_local_title(const SyncState *state, const char *server_id) {
    int i;
    size_t slen = strlen(server_id);

    for (i = 0; i < state->num_titles; i++) {
        if (strcmp(state->titles[i].title_id,  server_id) == 0) return i;
        if (slen == 9 && strcmp(state->titles[i].game_code, server_id) == 0) return i;
    }
    return -1;
}

static int count_titles_with_code(const SyncState *state, const char *game_code) {
    int count = 0;
    for (int i = 0; i < state->num_titles; i++) {
        if (strcmp(state->titles[i].game_code, game_code) == 0) {
            count++;
        }
    }
    return count;
}

static void format_slot_name(const TitleInfo *title, const char *base_name,
                             char *out, size_t out_size) {
    const char *suffix;

    if (!title || !base_name || !out || out_size == 0) {
        return;
    }

    suffix = title->title_id;
    if (strlen(title->title_id) > 9) {
        suffix = title->title_id + 9;
        if (*suffix == '-' || *suffix == '_' || *suffix == '.') {
            suffix++;
        }
    }

    if (!suffix[0] || strcmp(suffix, title->title_id) == 0) {
        snprintf(out, out_size, "%s", base_name);
    } else {
        snprintf(out, out_size, "%s [%s]", base_name, suffix);
    }
}

void network_merge_server_titles(SyncState *state) {
    if (!state) return;

    /* Full libraries can exceed 256 KiB once multiple systems are stored.
       Keep this comfortably above the current desktop-visible payload size
       so late entries (like PS1 server-only saves) are not truncated. */
    static uint8_t resp[512 * 1024];
    int status = 0;
    int r = http_request(state, "GET", "/api/v1/titles?console_type=PS3&console_type=PS1",
                         NULL, NULL, 0, resp, sizeof(resp) - 1, &status);
    if (r <= 0 || status != 200) return;
    resp[r] = '\0';

    const char *p = (const char *)resp;
    while ((p = strstr(p, "\"title_id\"")) != NULL) {
        const char *obj_start = p;
        const char *obj_end;
        p = strchr(p, ':');
        if (!p) break;
        p++;
        while (*p == ' ' || *p == '\t') p++;
        if (*p != '"') continue;
        p++;

        char server_id[GAME_ID_LEN];
        char effective_id[GAME_ID_LEN];
        char retail_serial[GAME_ID_LEN];
        int len = 0;
        while (*p && *p != '"' && len < GAME_ID_LEN - 1)
            server_id[len++] = *p++;
        server_id[len] = '\0';
        strncpy(effective_id, server_id, sizeof(effective_id) - 1);
        effective_id[sizeof(effective_id) - 1] = '\0';
        retail_serial[0] = '\0';

        obj_end = strchr(obj_start, '}');
        if (obj_end) {
            const char *rp = strstr(obj_start, "\"retail_serial\"");
            if (rp && rp < obj_end) {
                rp = strchr(rp, ':');
                if (rp) {
                    rp++;
                    while (*rp == ' ' || *rp == '\t') rp++;
                    if (*rp == '"') {
                        int rlen = 0;
                        rp++;
                        while (*rp && *rp != '"' && rlen < GAME_ID_LEN - 1)
                            retail_serial[rlen++] = *rp++;
                        retail_serial[rlen] = '\0';
                    }
                }
            }
        }
        if (retail_serial[0]) {
            strncpy(effective_id, retail_serial, sizeof(effective_id) - 1);
            effective_id[sizeof(effective_id) - 1] = '\0';
        }

        debug_log("server merge: title_id=%s effective_id=%s retail_serial=%s",
                  server_id,
                  effective_id,
                  retail_serial[0] ? retail_serial : "(none)");
        if (!saves_is_relevant_game_code(effective_id)) {
            debug_log("server merge: skip title_id=%s effective_id=%s reason=not_relevant_game_code",
                      server_id, effective_id);
            continue;
        }

        /* If a local title matches exactly, or the server only has a legacy
           bare 9-char product-code entry for this save, just flag it as
           present on the server — don't create a duplicate entry. */
        int existing = find_local_title(state, effective_id);
        if (existing >= 0) {
            state->titles[existing].on_server = true;
            debug_log("server merge: matched existing title_id=%s local_index=%d local_path=%s",
                      effective_id, existing, state->titles[existing].local_path);
            continue;
        }

        if (state->num_titles >= MAX_TITLES) {
            debug_log("server merge: stop reason=max_titles");
            break;
        }

        TitleInfo *t = &state->titles[state->num_titles++];
        memset(t, 0, sizeof(*t));
        /* Store the full server title_id but extract 9-char game_code for lookup */
        strncpy(t->title_id,  effective_id, sizeof(t->title_id)  - 1);
        apollo_extract_game_code(effective_id, t->game_code, sizeof(t->game_code));
        if (t->game_code[0] == '\0')
            strncpy(t->game_code, effective_id, sizeof(t->game_code) - 1);
        strncpy(t->name, t->game_code, sizeof(t->name) - 1);
        t->kind        = apollo_detect_save_kind(t->game_code);
        if (t->kind == SAVE_KIND_PS1) {
            char vmc_root[PATH_LEN];
            t->kind = SAVE_KIND_PS1_VM1;
            apollo_get_ps1_vmc_root(vmc_root, sizeof(vmc_root));
            snprintf(t->local_path, sizeof(t->local_path), "%s/%s.VM1",
                     vmc_root, effective_id);
            debug_log("server merge: added ps1 server_only title_id=%s local_path=%s",
                      t->title_id, t->local_path);
        } else {
            /* Set download destination using the detected savedata root */
            snprintf(t->local_path, sizeof(t->local_path), "%s/%s",
                     state->savedata_root[0] ? state->savedata_root
                                             : "/dev_hdd0/home/00000001/savedata",
                     effective_id);
            debug_log("server merge: added title_id=%s kind=%d local_path=%s",
                      t->title_id, (int)t->kind, t->local_path);
        }
        t->server_only = true;
        t->on_server   = true;
        t->status      = TITLE_STATUS_SERVER_ONLY;
    }

    /* Set initial status for all titles based on local/server presence */
    for (int i = 0; i < state->num_titles; i++) {
        TitleInfo *t = &state->titles[i];
        if (t->status != TITLE_STATUS_SERVER_ONLY) {
            t->status = t->on_server ? TITLE_STATUS_UNKNOWN
                                     : TITLE_STATUS_LOCAL_ONLY;
        }
    }
    debug_log("server merge: done total_titles=%d", state->num_titles);
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

        int same_code = count_titles_with_code(state, key);
        for (int i = 0; i < state->num_titles; i++) {
            if (strcmp(state->titles[i].game_code, key) != 0) {
                continue;
            }
            if (same_code > 1 && state->titles[i].kind == SAVE_KIND_PS3) {
                format_slot_name(&state->titles[i], val, state->titles[i].name,
                                 sizeof(state->titles[i].name));
            } else {
                strncpy(state->titles[i].name, val, MAX_TITLE_LEN - 1);
                state->titles[i].name[MAX_TITLE_LEN - 1] = '\0';
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
        if (!state->titles[i].server_only &&
            state->titles[i].hash_calculated &&
            state->titles[i].kind == SAVE_KIND_PS3) count++;
    if (count == 0) return 0;

    int json_cap = 64 + count * 260;
    char *json = (char *)malloc((size_t)json_cap);
    if (!json) return -1;

    int pos = snprintf(json, (size_t)json_cap,
                       "{\"console_id\":\"%s\",\"titles\":[", state->console_id);
    bool first = true;
    for (int i = 0; i < state->num_titles; i++) {
        const TitleInfo *t = &state->titles[i];
        if (t->server_only || !t->hash_calculated || t->kind != SAVE_KIND_PS3) continue;

        char hash_hex[65];
        for (int j = 0; j < 32; j++)
            snprintf(&hash_hex[j * 2], 3, "%02x", t->hash[j]);
        hash_hex[64] = '\0';

        char last_hash[65] = "";
        bool has_last = state_get_last_hash(t->title_id, last_hash);

        if (!first) json[pos++] = ',';
        first = false;

        if (has_last) {
            pos += snprintf(json + pos, (size_t)(json_cap - pos),
                "{\"title_id\":\"%s\",\"save_hash\":\"%s\","
                "\"timestamp\":0,\"size\":%u,"
                "\"last_synced_hash\":\"%s\"}",
                t->title_id, hash_hex, t->total_size, last_hash);
        } else {
            pos += snprintf(json + pos, (size_t)(json_cap - pos),
                "{\"title_id\":\"%s\",\"save_hash\":\"%s\","
                "\"timestamp\":0,\"size\":%u}",
                t->title_id, hash_hex, t->total_size);
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

/* ============================================================
 * ROM catalog + streaming download (Range-resumable)
 * ============================================================
 *
 * The save bundle path uses an in-RAM 8 MB scratch buffer.  ROM downloads
 * can be 4–8 GB so they take a separate code path: header parsing without
 * the body buffer, then a chunked stream straight to a .part file with
 * fwrite(), pumping sysutil between chunks. */

int network_trigger_rom_scan(const SyncState *state, int *count_out) {
    if (count_out) *count_out = -1;
    if (!state) return -1;

    /* Response body is small JSON: {"status": "ok", "count": N}.
     * 4 KB is plenty even with whitespace + future fields. */
    static char resp[4096];
    int status = 0;
    debug_log("net: trigger_rom_scan");
    int n = http_request(state, "GET", "/api/v1/roms/scan",
                         NULL, NULL, 0,
                         (uint8_t *)resp, sizeof(resp), &status);
    if (n < 0 || status != 200) {
        debug_log("net: trigger_rom_scan failed status=%d n=%d", status, n);
        return n < 0 ? n : -1;
    }
    /* Best-effort count parse — failure isn't fatal, the caller still
     * gets a 0 return code so it knows the rescan completed. */
    if (count_out) {
        const char *p = strstr(resp, "\"count\"");
        if (p) {
            const char *colon = strchr(p, ':');
            if (colon) *count_out = atoi(colon + 1);
        }
    }
    debug_log("net: trigger_rom_scan OK count=%d",
              count_out ? *count_out : -1);
    return 0;
}

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
        pos += snprintf(path + pos, sizeof(path) - pos,
                        "system=%s&", system_code);
    }
    if (limit > 0) {
        pos += snprintf(path + pos, sizeof(path) - pos,
                        "limit=%d&", limit);
    }
    pos += snprintf(path + pos, sizeof(path) - pos, "offset=%d", offset);

    debug_log("net: fetch_rom_catalog GET %s server=%s out_size=%u",
              path, state->server_url, (unsigned)out_size);

    int status = 0;
    int n = http_request(state, "GET", path,
                         NULL, NULL, 0,
                         (uint8_t *)out, out_size, &status);
    if (status_out) *status_out = status;
    if (n < 0 || status != 200) {
        debug_log("net: fetch_rom_catalog (%s, offset=%d) failed status=%d n=%d",
                  system_code ? system_code : "all", offset, status, n);
        return n < 0 ? n : -1;
    }
    debug_log("net: fetch_rom_catalog OK offset=%d n=%d status=%d",
              offset, n, status);
    return n;
}

/* Internal: open a TCP connection, send a GET with optional Range header,
 * and read the status line + header block.  Leaves the socket positioned
 * at the start of the response body so the caller can stream it.
 *
 * Returns the connected socket on success (caller must NET_CLOSE it).
 * On error returns -1 and sets *status_out to the parsed code (or 0 if no
 * status line was received).
 *
 * On 200/206 *content_len_out is filled with the body length advertised by
 * Content-Length (or -1 if absent — then the caller streams until close).
 */
static int http_open_get_stream(const SyncState *state,
                                const char *api_path,
                                uint64_t range_start,
                                int *status_out,
                                int64_t *content_len_out) {
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

    char hdr[1024];
    int  hlen;
    if (range_start > 0) {
        /* Open-ended range: bytes=N- so the server sends everything from N
         * to EOF in one go.  Tested against the FastAPI catalog endpoint
         * which honors this exactly (see server/app/routes/roms.py). */
        hlen = snprintf(hdr, sizeof(hdr),
            "GET %s HTTP/1.0\r\n"
            "Host: %s:%d\r\n"
            "X-API-Key: %s\r\n"
            "X-Console-ID: %s\r\n"
            "Range: bytes=%llu-\r\n"
            "Connection: close\r\n"
            "\r\n",
            api_path, host, port,
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
            api_path, host, port,
            state->api_key, state->console_id);
    }

    if (!send_all(s, (const uint8_t *)hdr, (uint32_t)hlen)) {
        NET_CLOSE(s); return -1;
    }

    char line[512];
    read_line(s, line, sizeof(line));
    if (g_progress_cb) g_progress_cb(0, -1);

    int status = 0;
    const char *sp = strchr(line, ' ');
    if (sp) status = atoi(sp + 1);
    if (status_out) *status_out = status;
    debug_log("net: stream GET %s (range_start=%llu) -> HTTP %d",
              api_path, (unsigned long long)range_start, status);

    int64_t content_length = -1;
    while (1) {
        int n = read_line(s, line, sizeof(line));
        if (n == 0) break;
        if (strncasecmp(line, "Content-Length:", 15) == 0)
            content_length = (int64_t)strtoull(line + 15, NULL, 10);
        if (g_progress_cb) g_progress_cb(0, -1);
    }
    if (content_len_out) *content_len_out = content_length;

    if (status != 200 && status != 206) {
        NET_CLOSE(s);
        return -1;
    }

    return s;
}

/* Internal shared streamer used by both the single-file ROM path and the
 * per-bundle-file path.  Caller provides the absolute API path so we
 * don't have to teach the streamer which endpoint to talk to. */
static int download_stream_to_file(const SyncState *state,
                                   const char *api_path,
                                   const char *target_path,
                                   uint64_t start_offset,
                                   uint64_t *total_out) {
    int status = 0;
    int64_t content_length = -1;
    int s = http_open_get_stream(state, api_path,
                                 start_offset, &status, &content_length);
    if (s < 0) {
        debug_log("net: download_rom_resumable open failed status=%d", status);
        if (status == 416) return -3;  /* range past EOF — caller retries from 0 */
        if (status == 404) return -3;
        return -1;
    }

    /* Servers that don't honor Range will respond 200 with the full body.
     * Truncate the .part file in that case so we restart from offset 0. */
    bool resumed = (status == 206);
    uint64_t written = resumed ? start_offset : 0;
    uint64_t expected_total =
        (content_length >= 0)
            ? ((uint64_t)content_length + (resumed ? start_offset : 0))
            : 0;
    if (total_out) *total_out = expected_total;

    /* Open the .part file.  resumed=true → append.  resumed=false → trunc. */
    char part_path[PATH_LEN + 8];
    snprintf(part_path, sizeof(part_path), "%s.part", target_path);

    FILE *fp = fopen(part_path, resumed ? "ab" : "wb");
    if (!fp) {
        debug_log("net: cannot open %s", part_path);
        NET_CLOSE(s);
        return -2;
    }

    /* 256 KB recv chunk — larger than the old 64 KB to reduce syscall
     * overhead on wired LAN.  Still well within PS3's BSS budget and
     * the 1-second SO_RCVTIMEO ensures sysutil gets pumped regularly. */
    static uint8_t chunk[262144];
    int rc = 0;
    while (1) {
        int n = (int)recv(s, chunk, sizeof(chunk), 0);
        if (n > 0) {
            size_t w = fwrite(chunk, 1, (size_t)n, fp);
            if (w != (size_t)n) {
                debug_log("net: fwrite short (%u != %d) errno=%d",
                          (unsigned)w, n, errno);
                rc = -2;
                break;
            }
            written += (uint64_t)n;
            if (g_progress64_cb) {
                if (g_progress64_cb(written, expected_total)) {
                    /* Cancellation requested — keep .part on disk for
                     * resume next session. */
                    rc = 1;
                    break;
                }
            } else if (g_progress_cb) {
                /* Fallback: 32-bit clamp.  Truncates progress for >4 GB
                 * downloads but still pumps sysutil. */
                uint32_t w32 = (uint32_t)(written & 0xFFFFFFFFULL);
                int total32 =
                    (expected_total > 0xFFFFFFFFULL) ? -1
                                                     : (int)expected_total;
                if (g_progress_cb(w32, total32)) {
                    rc = 1;
                    break;
                }
            }
            /* Done? */
            if (expected_total > 0 && written >= expected_total)
                break;
        } else if (n == 0) {
            /* Server closed — done if we got everything, else error. */
            break;
        } else {
            /* SO_RCVTIMEO fired or hard error.  Pump sysutil and retry. */
            if (g_progress64_cb) {
                if (g_progress64_cb(written, expected_total)) { rc = 1; break; }
            } else if (g_progress_cb) {
                if (g_progress_cb(0, -1)) { rc = 1; break; }
            }
            if (errno != EAGAIN && errno != EWOULDBLOCK && errno != ETIMEDOUT) {
                debug_log("net: recv error errno=%d", errno);
                rc = -1;
                break;
            }
        }
    }

    fclose(fp);
    NET_CLOSE(s);

    if (rc != 0) {
        /* Either paused (1), or error (-1/-2).  Leave .part for resume. */
        return rc;
    }

    /* Sanity check: did we actually receive the expected bytes? */
    if (expected_total > 0 && written < expected_total) {
        debug_log("net: short download %llu/%llu — keep .part for resume",
                  (unsigned long long)written,
                  (unsigned long long)expected_total);
        return 1;  /* treat as paused so user can retry */
    }

    /* Atomic rename .part → target.  rename() may fail if target already
     * exists on some FS — try unlink-then-rename as a fallback. */
    if (rename(part_path, target_path) != 0) {
        debug_log("net: rename %s -> %s failed errno=%d, retrying",
                  part_path, target_path, errno);
        unlink(target_path);
        if (rename(part_path, target_path) != 0) {
            debug_log("net: rename retry failed errno=%d", errno);
            return -2;
        }
    }

    if (total_out) *total_out = written;
    return 0;
}

static bool is_url_path_safe(unsigned char c) {
    return (c >= 'A' && c <= 'Z') ||
           (c >= 'a' && c <= 'z') ||
           (c >= '0' && c <= '9') ||
           c == '-' || c == '.' || c == '_' || c == '~' ||
           c == '/';
}

static bool url_encode_path(const char *in, char *out, size_t out_size) {
    static const char hex[] = "0123456789ABCDEF";
    size_t j = 0;

    if (!in || !out || out_size == 0) return false;

    for (size_t i = 0; in[i] != '\0'; i++) {
        unsigned char c = (unsigned char)in[i];
        if (is_url_path_safe(c)) {
            if (j + 1 >= out_size) return false;
            out[j++] = (char)c;
            continue;
        }

        if (j + 3 >= out_size) return false;
        out[j++] = '%';
        out[j++] = hex[(c >> 4) & 0x0F];
        out[j++] = hex[c & 0x0F];
    }

    out[j] = '\0';
    return true;
}

/* Public wrappers for the two endpoints clients hit. */

int network_download_rom_resumable(const SyncState *state,
                                   const char *rom_id,
                                   const char *target_path,
                                   uint64_t start_offset,
                                   uint64_t *total_out) {
    return network_download_rom_resumable_ex(state, rom_id, NULL,
                                             target_path, start_offset,
                                             total_out);
}

int network_download_rom_resumable_ex(const SyncState *state,
                                      const char *rom_id,
                                      const char *extract_format,
                                      const char *target_path,
                                      uint64_t start_offset,
                                      uint64_t *total_out) {
    if (!state || !rom_id || !target_path) return -1;
    char api_path[512];
    if (extract_format && extract_format[0]) {
        snprintf(api_path, sizeof(api_path),
                 "/api/v1/roms/%s?extract=%s", rom_id, extract_format);
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
    /* Relative paths inside the bundle commonly contain spaces,
     * parentheses, brackets, and translation tags.  These must be escaped
     * before they are placed in the HTTP request line; otherwise the
     * server rejects the request as malformed before it reaches FastAPI. */
    char encoded_file[512];
    if (!url_encode_path(bundle_file, encoded_file, sizeof(encoded_file))) {
        debug_log("net: bundle file path too long to URL-encode: %s",
                  bundle_file);
        return -1;
    }

    char api_path[768];
    snprintf(api_path, sizeof(api_path),
             "/api/v1/roms/%s/file/%s", rom_id, encoded_file);
    return download_stream_to_file(state, api_path, target_path,
                                   start_offset, total_out);
}

int network_fetch_rom_manifest_http(const SyncState *state,
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
        debug_log("net: fetch_rom_manifest %s -> status=%d n=%d",
                  rom_id, status, n);
        return n < 0 ? n : -1;
    }
    return n;
}
