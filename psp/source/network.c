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
#include <pspnet.h>
#include <pspnet_inet.h>
#include <pspnet_apctl.h>
#include <pspnet_resolver.h>
#include <psputility.h>
#include <sys/socket.h>
#include <arpa/inet.h>
#include <netinet/in.h>
#include <unistd.h>

#include "network.h"

#define HTTP_BUF_SIZE   (64 * 1024)   /* 64KB for HTTP headers */
#define RECV_CHUNK      4096

static bool g_net_initialized = false;
static bool g_connected = false;

/* ---- Network init ---- */

int network_init(void) {
    int ret;

    ret = sceNetInit(128 * 1024, 42, 4 * 1024, 42, 4 * 1024);
    if (ret < 0) return ret;

    ret = sceNetInetInit();
    if (ret < 0) { sceNetTerm(); return ret; }

    ret = sceNetApctlInit(0x8000, 48);
    if (ret < 0) { sceNetInetTerm(); sceNetTerm(); return ret; }

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
    struct hostent *he = NULL;
    /* Try direct IP first */
    unsigned int addr = sceNetInetInetAddr(host);
    if (addr == 0xFFFFFFFF) {
        /* Need DNS resolution */
        int rid;
        if (sceNetResolverCreate(&rid, NULL, 0) < 0) return -1;
        char ip_str[64];
        if (sceNetResolverStartNtoA(rid, host, (struct in_addr *)&addr, 2, 3) < 0) {
            sceNetResolverDelete(rid);
            return -1;
        }
        sceNetResolverDelete(rid);
    }

    int sock = sceNetInetSocket(AF_INET, SOCK_STREAM, 0);
    if (sock < 0) return -1;

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
    bool chunked = false;

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
            "Content-Length: %u\r\n",
            content_type ? content_type : "application/octet-stream",
            body_size);
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

int network_get_save_info(const SyncState *state, const char *game_id,
                          char *hash_out, uint32_t *size_out) {
    char path[256];
    snprintf(path, sizeof(path), "/api/v1/saves/%s/meta", game_id);

    static uint8_t resp[1024];
    int r = network_http_get(state, path, resp, sizeof(resp));
    if (r <= 0) return (r == -404) ? 1 : -1;

    resp[r] = '\0';

    /* Parse JSON: {"save_hash":"...","save_size":...} */
    char *hash_key = strstr((char *)resp, "\"save_hash\":");
    if (hash_key && hash_out) {
        char *start = strchr(hash_key, '"');
        if (start) start = strchr(start + 1, '"');
        if (start) {
            start++;
            char *end = strchr(start, '"');
            if (end && (end - start) <= 64) {
                int len = end - start;
                strncpy(hash_out, start, len);
                hash_out[len] = '\0';
            }
        }
    }

    char *size_key = strstr((char *)resp, "\"save_size\":");
    if (size_key && size_out) {
        *size_out = atoi(size_key + 12);
    }

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
