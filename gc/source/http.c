/*
 * http.c — minimal HTTP/1.0 client over libogc's net_* BSD sockets (BBA).
 *
 * Same protocol shape as the PS2/NDS clients.  No keep-alive, no chunked
 * encoding, no TLS.  Host must be a dotted LAN IP (no DNS).
 *
 * NOTE: GameCube/PPC is big-endian, so the socket address is built with
 * htonl/htons rather than the byte-wise little-endian trick the PS2 used.
 */

#include "http.h"

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <ctype.h>
#include <errno.h>
#include <unistd.h>

#include <gccore.h>
#include <network.h>

#ifndef IPPROTO_IP
#define IPPROTO_IP 0
#endif

#define RECV_BUF_SIZE 8192

/* Waiting-for-data callback (UI pump / user cancel) — see http.h. */
static HttpWaitFn g_wait_cb = NULL;
void http_set_wait_cb(HttpWaitFn cb) { g_wait_cb = cb; }

#define HTTP_HEADER_WAIT_MS (10u * 60u * 1000u)   /* server-side ROM conversion */
#define HTTP_BODY_WAIT_MS   (60u * 1000u)

/* Timed receive via per-call non-blocking recv (MSG_DONTWAIT), paced at
 * vsync.  Deliberately NOT net_select(): libogc's BBA select creates and
 * destroys a condition variable on a shared list every call while the
 * packet IRQ walks and signals that list — the timeout path races the IRQ
 * and hard-locks the console (reset button dead).
 * Returns >0 bytes, 0 = closed, -1 = error/timeout, -2 = user cancel. */
static int recv_timed(int fd, void *buf, int len, uint32_t max_wait_ms) {
    uint32_t waited_ms = 0, next_cb = 500;
    for (;;) {
        int n = net_recv(fd, buf, len, MSG_DONTWAIT);
        if (n >= 0) return n;
        if (n != -EWOULDBLOCK && n != -EAGAIN) return -1;
        usleep(1000);   /* 1 ms; short so a real stall costs little */
        waited_ms += 1;
        if (waited_ms >= next_cb) {
            next_cb += 500;
            if (g_wait_cb && g_wait_cb(waited_ms)) return -2;
        }
        if (waited_ms >= max_wait_ms) return -1;
    }
}

static int parse_url(const char *url, char *host_out, size_t host_size,
                     int *port_out, char *path_out, size_t path_size)
{
    const char *p = url;
    if (strncmp(p, "http://", 7) != 0) return -1;
    p += 7;

    const char *host_start = p;
    while (*p && *p != ':' && *p != '/') p++;
    size_t host_len = p - host_start;
    if (host_len == 0 || host_len >= host_size) return -1;
    memcpy(host_out, host_start, host_len);
    host_out[host_len] = '\0';

    int port = 80;
    if (*p == ':') {
        p++;
        port = atoi(p);
        while (*p && *p != '/') p++;
    }
    *port_out = port;

    if (*p == '\0') {
        if (path_size > 1) { path_out[0] = '/'; path_out[1] = '\0'; }
    } else {
        size_t plen = strlen(p);
        if (plen >= path_size) return -1;
        strcpy(path_out, p);
    }
    return 0;
}

static int parse_dotted_ip(const char *host, struct in_addr *out) {
    unsigned a, b, c, d;
    char extra;
    if (sscanf(host, "%u.%u.%u.%u%c", &a, &b, &c, &d, &extra) != 4) return -1;
    if (a > 255 || b > 255 || c > 255 || d > 255) return -1;
    out->s_addr = htonl((a << 24) | (b << 16) | (c << 8) | d);
    return 0;
}

static int connect_to(const char *host, int port) {
    struct in_addr ip;
    if (parse_dotted_ip(host, &ip) != 0) return -2;

    int fd = net_socket(AF_INET, SOCK_STREAM, IPPROTO_IP);
    if (fd < 0) return -1;

    /* Bigger receive window — single-stream LAN throughput is window-bound. */
    int rcvbuf = 128 * 1024;
    net_setsockopt(fd, SOL_SOCKET, SO_RCVBUF, &rcvbuf, sizeof(rcvbuf));

    /* Bound recv so a stalled server/connection errors out instead of
     * blocking the (single-threaded) app forever. */
    struct timeval tv = { .tv_sec = 30, .tv_usec = 0 };
    net_setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family      = AF_INET;
    addr.sin_port        = htons((u16)port);
    addr.sin_addr.s_addr = ip.s_addr;

    if (net_connect(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        net_close(fd);
        return -3;
    }
    return fd;
}

static int send_all(int fd, const void *buf, size_t len) {
    const char *p = (const char *)buf;
    size_t remaining = len;
    while (remaining) {
        int n = net_send(fd, p, remaining, 0);
        if (n <= 0) return -1;
        p         += n;
        remaining -= n;
    }
    return 0;
}

/* Send the request body: from RAM (req->body) or streamed from a file
 * (req->body_fp) so 16 MB card images don't need a 16 MB heap buffer. */
static int send_body(int fd, const HttpRequest *req) {
    if (req->body_len == 0) return 0;
    if (req->body_fp) {
        static char chunk[32768];
        uint32_t remaining = req->body_len;
        while (remaining) {
            size_t want = remaining < sizeof(chunk) ? remaining : sizeof(chunk);
            size_t rd = fread(chunk, 1, want, req->body_fp);
            if (rd == 0) return -1;
            if (send_all(fd, chunk, rd) < 0) return -1;
            remaining -= (uint32_t)rd;
        }
        return 0;
    }
    return send_all(fd, req->body, req->body_len);
}

static int build_request(char *out, size_t out_size,
                         const HttpRequest *req,
                         const char *host, int port,
                         const char *path)
{
    int n = snprintf(out, out_size,
        "%s %s HTTP/1.0\r\n"
        "Host: %s:%d\r\n"
        "X-API-Key: %s\r\n"
        "User-Agent: gcsync/" APP_VERSION "\r\n"
        "Connection: close\r\n",
        req->method ? req->method : "GET",
        path, host, port,
        req->api_key ? req->api_key : "");
    if (n < 0 || (size_t)n >= out_size) return -1;

    if (req->range_start > 0) {
        n += snprintf(out + n, out_size - n,
                      "Range: bytes=%llu-\r\n",
                      (unsigned long long)req->range_start);
        if ((size_t)n >= out_size) return -1;
    }
    if (req->body_len > 0) {
        n += snprintf(out + n, out_size - n,
                      "Content-Type: %s\r\n"
                      "Content-Length: %lu\r\n",
                      req->body_content_type ? req->body_content_type
                                             : "application/octet-stream",
                      (unsigned long)req->body_len);
        if ((size_t)n >= out_size) return -1;
    }
    n += snprintf(out + n, out_size - n, "\r\n");
    return n;
}

static int read_headers(int fd, char *buf, size_t buf_size, size_t *total_out) {
    size_t total = 0;
    if (total_out) *total_out = 0;
    while (total + 1 < buf_size) {
        int n = recv_timed(fd, buf + total, buf_size - 1 - total, HTTP_HEADER_WAIT_MS);
        if (n == -2) return -2;   /* user cancel */
        if (n <= 0) return -1;
        total += (size_t)n;
        buf[total] = '\0';
        char *eoh = strstr(buf, "\r\n\r\n");
        if (eoh) {
            size_t header_len = (size_t)((eoh - buf) + 4);
            if (total_out) *total_out = total;
            return (int)header_len;
        }
    }
    return -1;
}

static int parse_status(const char *headers) {
    if (strncmp(headers, "HTTP/", 5) != 0) return 0;
    const char *p = strchr(headers, ' ');
    if (!p) return 0;
    return atoi(p + 1);
}

static uint64_t parse_content_length(const char *headers, size_t header_len) {
    const char *needle = "content-length:";
    size_t nlen = strlen(needle);
    for (size_t i = 0; i + nlen < header_len; i++) {
        bool match = true;
        for (size_t j = 0; j < nlen; j++) {
            if (tolower((unsigned char)headers[i + j]) != needle[j]) { match = false; break; }
        }
        if (match) {
            const char *p = headers + i + nlen;
            while (*p == ' ' || *p == '\t') p++;
            return strtoull(p, NULL, 10);
        }
    }
    return 0;
}

int http_get_buf(const HttpRequest *req,
                 uint8_t *out, uint32_t out_size,
                 int *status_out)
{
    if (status_out) *status_out = 0;

    char host[64];
    int  port;
    char path[1024];
    if (parse_url(req->server_url, host, sizeof(host), &port, path, sizeof(path)) != 0)
        return -1;
    snprintf(path, sizeof(path), "%s", req->path);

    int fd = connect_to(host, port);
    if (fd < 0) return -2;

    char header_buf[1024];
    int  header_len = build_request(header_buf, sizeof(header_buf), req, host, port, path);
    if (header_len < 0) { net_close(fd); return -3; }

    if (send_all(fd, header_buf, header_len) < 0) { net_close(fd); return -4; }
    if (send_body(fd, req) < 0) {
        net_close(fd); return -4;
    }

    char rbuf[RECV_BUF_SIZE];
    size_t total_read = 0;
    int  hlen = read_headers(fd, rbuf, sizeof(rbuf), &total_read);
    if (hlen < 0) { net_close(fd); return -5; }

    int status = parse_status(rbuf);
    if (status_out) *status_out = status;

    uint32_t cap = out_size > 0 ? out_size - 1 : 0;

    uint64_t content_length = parse_content_length(rbuf, (size_t)hlen);
    if (content_length > (uint64_t)cap) { net_close(fd); return -6; }

    size_t body_in_rbuf = (total_read > (size_t)hlen) ? total_read - (size_t)hlen : 0;

    uint32_t written = 0;
    if (body_in_rbuf > 0 && cap > 0) {
        uint32_t take = (body_in_rbuf < cap) ? (uint32_t)body_in_rbuf : cap;
        memcpy(out, rbuf + hlen, take);
        written = take;
    }
    while (written < cap) {
        int n = recv_timed(fd, out + written, cap - written, HTTP_BODY_WAIT_MS);
        if (n <= 0) break;
        written += n;
    }
    if (out_size > 0) out[written] = '\0';

    net_close(fd);
    return (int)written;
}

static int file_stream_writer(const void *data, uint32_t len, void *user) {
    FILE *fp = (FILE *)user;
    if (!fp) return -1;
    return fwrite(data, 1, len, fp) == len ? 0 : -1;
}

int http_get_stream_cb(const HttpRequest *req,
                       HttpStreamBeginFn begin,
                       HttpWriteFn writer,
                       void *user,
                       HttpProgressFn progress,
                       HttpResponseInfo *info_out)
{
    if (info_out) { info_out->status = 0; info_out->content_length = 0; }
    if (!writer) return -1;

    char host[64];
    int  port;
    char path[1024];
    if (parse_url(req->server_url, host, sizeof(host), &port, path, sizeof(path)) != 0)
        return -1;
    snprintf(path, sizeof(path), "%s", req->path);

    int fd = connect_to(host, port);
    if (fd < 0) return -2;

    char header_buf[1024];
    int  header_len = build_request(header_buf, sizeof(header_buf), req, host, port, path);
    if (header_len < 0) { net_close(fd); return -3; }

    if (send_all(fd, header_buf, header_len) < 0) { net_close(fd); return -4; }
    if (send_body(fd, req) < 0) {
        net_close(fd); return -4;
    }

    char rbuf[RECV_BUF_SIZE];
    size_t total_read = 0;
    int  hlen = read_headers(fd, rbuf, sizeof(rbuf), &total_read);
    if (hlen == -2) { net_close(fd); return 1; }   /* user cancel = paused */
    if (hlen < 0) { net_close(fd); return -5; }

    int status = parse_status(rbuf);
    uint64_t content_length = parse_content_length(rbuf, (size_t)hlen);
    if (info_out) { info_out->status = status; info_out->content_length = content_length; }

    if (status < 200 || status >= 300) { net_close(fd); return -6; }

    if (begin && begin(content_length, user) != 0) { net_close(fd); return -9; }

    uint64_t total_done   = 0;
    size_t   in_rbuf_body = (total_read > (size_t)hlen) ? total_read - (size_t)hlen : 0;
    if (in_rbuf_body > 0) {
        if (writer(rbuf + hlen, (uint32_t)in_rbuf_body, user) != 0) { net_close(fd); return -7; }
        total_done += in_rbuf_body;
    }

    static char chunk[65536];
    while (1) {
        int n = recv_timed(fd, chunk, sizeof(chunk), HTTP_BODY_WAIT_MS);
        if (n == -2) { net_close(fd); return 1; }   /* user cancel = paused */
        if (n == 0) break;
        if (n < 0)  { net_close(fd); return -8; }
        if (writer(chunk, (uint32_t)n, user) != 0) { net_close(fd); return -7; }
        total_done += (uint64_t)n;
        if (progress && progress(total_done, content_length) != 0) { net_close(fd); return 1; }
    }

    net_close(fd);
    return 0;
}

int http_get_stream(const HttpRequest *req,
                    FILE *out_fp,
                    HttpProgressFn progress,
                    HttpResponseInfo *info_out)
{
    int rc = http_get_stream_cb(req, NULL, file_stream_writer, out_fp, progress, info_out);
    if (out_fp) fflush(out_fp);
    return rc;
}

/* ---- Parallel range download ---- */

#include <fcntl.h>
#include <unistd.h>

#define HTTP_MAX_CONNS 6

/* Find "\r\n\r\n" in a non-terminated byte buffer. */
static char *find_eoh(char *buf, int len) {
    for (int i = 0; i + 3 < len; i++)
        if (buf[i] == '\r' && buf[i+1] == '\n' && buf[i+2] == '\r' && buf[i+3] == '\n')
            return buf + i;
    return NULL;
}

/* Parse "Content-Range: bytes S-E/TOTAL" -> TOTAL (0 if absent). */
static uint64_t parse_content_range_total(const char *headers, size_t hlen) {
    const char *needle = "content-range:";
    size_t nlen = strlen(needle);
    for (size_t i = 0; i + nlen < hlen; i++) {
        bool m = true;
        for (size_t j = 0; j < nlen; j++)
            if (tolower((unsigned char)headers[i + j]) != needle[j]) { m = false; break; }
        if (!m) continue;
        const char *p = headers + i + nlen;
        const char *slash = strchr(p, '/');
        if (!slash || (size_t)(slash - headers) >= hlen) return 0;
        return strtoull(slash + 1, NULL, 10);
    }
    return 0;
}

/* Send a bounded GET (bytes=start-end) on a fresh connection; returns fd. */
static int open_range(const char *host, int port, const char *api_key,
                      const char *path, uint64_t start, uint64_t end) {
    int fd = connect_to(host, port);
    if (fd < 0) return fd;
    char req[1024];
    int n = snprintf(req, sizeof(req),
        "GET %s HTTP/1.0\r\nHost: %s:%d\r\nX-API-Key: %s\r\n"
        "User-Agent: gcsync/" APP_VERSION "\r\nConnection: close\r\n"
        "Range: bytes=%llu-%llu\r\n\r\n",
        path, host, port, api_key ? api_key : "",
        (unsigned long long)start, (unsigned long long)end);
    if (n < 0 || (size_t)n >= sizeof(req) || send_all(fd, req, n) < 0) {
        net_close(fd);
        return -1;
    }
    return fd;
}

typedef struct {
    int      fd;
    int      state;     /* 0 = reading headers, 1 = body, 2 = done */
    char     hbuf[RECV_BUF_SIZE];
    int      hlen;
    uint64_t start;     /* first file offset of this range */
    uint64_t foff;      /* file offset to write next */
    uint64_t remaining; /* body bytes still expected */
} PConn;

int http_download_parallel(const char *server_url, const char *api_key,
                           const char *path, const char *target_path,
                           uint64_t start_offset, int nconns,
                           HttpProgressFn progress, uint64_t *total_out) {
    if (total_out) *total_out = 0;
    if (nconns < 2) nconns = 2;
    if (nconns > HTTP_MAX_CONNS) nconns = HTTP_MAX_CONNS;

    char host[64]; int port; char dummy[8];
    if (parse_url(server_url, host, sizeof(host), &port, dummy, sizeof(dummy)) != 0)
        return -1;

    /* Probe: one-byte range to learn the total size and confirm 206 support.
     * Also triggers (and caches) any server-side conversion. */
    int pfd = connect_to(host, port);
    if (pfd < 0) return -1;
    {
        char req[1024];
        int n = snprintf(req, sizeof(req),
            "GET %s HTTP/1.0\r\nHost: %s:%d\r\nX-API-Key: %s\r\n"
            "Connection: close\r\nRange: bytes=0-0\r\n\r\n",
            path, host, port, api_key ? api_key : "");
        if (n < 0 || send_all(pfd, req, n) < 0) { net_close(pfd); return -1; }
    }
    char pbuf[RECV_BUF_SIZE]; size_t ptot = 0;
    int phlen = read_headers(pfd, pbuf, sizeof(pbuf), &ptot);
    int pstatus = phlen >= 0 ? parse_status(pbuf) : 0;
    uint64_t total = phlen >= 0 ? parse_content_range_total(pbuf, (size_t)phlen) : 0;
    net_close(pfd);
    if (phlen == -2) return 1;
    if (pstatus != 206 || total == 0) return -9;   /* no range support -> fall back */
    if (total_out) *total_out = total;

    char part[640];
    snprintf(part, sizeof(part), "%s.part", target_path);

    /* Already complete (resume offset == size): just finalize. */
    if (start_offset >= total) {
        if (rename(part, target_path) != 0) { unlink(target_path); rename(part, target_path); }
        return 0;
    }

    /* Preserve the existing prefix when resuming; only truncate a fresh dl. */
    int outfd = open(part, O_WRONLY | O_CREAT | (start_offset ? 0 : O_TRUNC), 0666);
    if (outfd < 0) return -2;

    /* Split the remaining [start_offset, total) span across the connections. */
    PConn c[HTTP_MAX_CONNS];
    uint64_t span = total - start_offset;
    uint64_t per = span / (uint64_t)nconns;
    if (per == 0) { nconns = 1; per = span; }
    int rc = 0;
    int opened = 0;
    for (int i = 0; i < nconns; i++) {
        uint64_t s = start_offset + (uint64_t)i * per;
        uint64_t e = (i == nconns - 1) ? (total - 1) : (s + per - 1);
        c[i].fd = open_range(host, port, api_key, path, s, e);
        if (c[i].fd < 0) { rc = -1; break; }
        c[i].state = 0; c[i].hlen = 0; c[i].start = s; c[i].foff = s; c[i].remaining = 0;
        opened++;
    }

    static char chunk[32768];
    uint64_t done = 0;
    while (rc == 0) {
        int active = 0;
        bool any_data = false;
        for (int i = 0; i < opened; i++) {
            if (c[i].state == 2) continue;
            active++;
            int n = net_recv(c[i].fd, chunk, sizeof(chunk), MSG_DONTWAIT);
            if (n == 0) { c[i].state = 2; net_close(c[i].fd); continue; }
            if (n < 0) {
                if (n == -EWOULDBLOCK || n == -EAGAIN) continue;
                rc = -1; break;
            }
            any_data = true;
            const char *body = chunk; int blen = n;
            if (c[i].state == 0) {
                int take = n; if (c[i].hlen + take > (int)sizeof(c[i].hbuf)) take = sizeof(c[i].hbuf) - c[i].hlen;
                memcpy(c[i].hbuf + c[i].hlen, chunk, take);
                c[i].hlen += take;
                char *eoh = find_eoh(c[i].hbuf, c[i].hlen);
                if (!eoh) continue;
                int hl = (int)(eoh - c[i].hbuf) + 4;
                if (parse_status(c[i].hbuf) != 206) { rc = -3; break; }   /* must be partial */
                c[i].remaining = parse_content_length(c[i].hbuf, hl);
                body = c[i].hbuf + hl; blen = c[i].hlen - hl;
                c[i].state = 1;
            }
            if (blen > 0) {
                if ((uint64_t)blen > c[i].remaining) blen = (int)c[i].remaining;
                if (lseek(outfd, (off_t)c[i].foff, SEEK_SET) < 0 ||
                    write(outfd, body, blen) != blen) { rc = -2; break; }
                c[i].foff += blen; c[i].remaining -= blen; done += blen;
                if (c[i].remaining == 0) { c[i].state = 2; net_close(c[i].fd); }
            }
        }
        if (rc != 0) break;
        if (active == 0) break;   /* all done */
        if (progress) progress(start_offset + done, total);
        if (g_wait_cb && g_wait_cb(0)) { rc = 1; break; }
        if (!any_data) usleep(1000);
    }

    for (int i = 0; i < opened; i++)
        if (c[i].state != 2) net_close(c[i].fd);
    close(outfd);

    if (rc == 0 && done == span) {
        if (rename(part, target_path) != 0) {
            unlink(target_path);
            if (rename(part, target_path) != 0) return -2;
        }
        if (progress) progress(total, total);
        return 0;
    }

    /* Cancel or error: keep the .part but report the largest CONTIGUOUS
     * prefix that is fully written (out-of-order surplus past it is
     * overwritten on resume), so the next run resumes correctly. */
    uint64_t prefix = start_offset;
    for (int i = 0; i < opened; i++) {
        if (c[i].start != prefix) break;   /* gap before this range */
        prefix = c[i].foff;
        if (c[i].state != 2) break;        /* range incomplete */
    }
    if (progress) progress(prefix, total);   /* -> resume offset for the caller */
    return rc ? rc : -1;
}
