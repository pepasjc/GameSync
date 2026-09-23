/*
 * http.c — minimal HTTP/1.0 client over nsysnet BSD sockets.
 *
 * Ported from the GameCube client (gc/source/http.c).  Differences:
 *   - plain socket()/connect()/recv()/send() instead of libogc's net_*
 *   - getaddrinfo() host resolution, so server_url may be a hostname
 *   - adds http_post_chunked() for streamed bundle uploads
 *
 * PowerPC is big-endian, so socket addresses are built with htonl/htons.
 */

#include "http.h"

#include <ctype.h>
#include <errno.h>
#include <netdb.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <unistd.h>

#include <coreinit/thread.h>

#define RECV_BUF_SIZE 8192

/* Waiting-for-data callback (UI pump / user cancel) — see http.h. */
static HttpWaitFn g_wait_cb = NULL;
void http_set_wait_cb(HttpWaitFn cb) { g_wait_cb = cb; }

#define HTTP_HEADER_WAIT_MS (30u * 60u * 1000u)   /* server-side ROM conversion */
#define HTTP_BODY_WAIT_MS   (60u * 1000u)
#define HTTP_CONNECT_WAIT_MS 6000u                /* TCP handshake */

static void sleep_ms(unsigned ms) { OSSleepTicks(OSMillisecondsToTicks(ms)); }

static uint64_t now_us(void) { return (uint64_t)OSTicksToMicroseconds(OSGetSystemTime()); }

/* Where a transfer's wall-clock actually goes.  Kept because guessing at this
 * cost a wrong fix once already: a slow download can be the socket, the SD
 * write, or the per-chunk UI work, and only measurement separates them. */
static HttpPerf g_perf;

void http_perf_reset(void) { memset(&g_perf, 0, sizeof(g_perf)); }
void http_perf_get(HttpPerf *out) { if (out) *out = g_perf; }

/* How long a single select() wait blocks before we surface to the UI pump.
 * Only spent when the socket is genuinely idle — data arriving wakes the
 * select immediately, so this is a responsiveness knob, not a throughput one. */
#define RECV_POLL_SLICE_MS 20u

/* The transfer a worker thread is currently running, for http_abort_active().
 * Only one streamed transfer runs at a time (the download worker). */
static volatile int g_active_fd = -1;

void http_abort_active(void) {
    int fd = g_active_fd;
    /* shutdown, not close: the worker still owns the fd, this only forces
     * its blocked recv to return.  Racing a concurrent close is avoided
     * because the worker clears g_active_fd first. */
    if (fd >= 0) shutdown(fd, SHUT_RDWR);
}

/* Timed receive.
 *
 * blocking mode (worker thread): a plain blocking recv(), the same hot path
 * curl uses in NUSspli — no select(), no extra IPC round-trips into nsysnet.
 * Timeouts and user cancel are enforced from outside via http_abort_active()
 * (the main thread watches progress and shuts the socket down), so a stuck
 * server can't wedge the worker.
 *
 * non-blocking mode (main-thread calls): the socket stays non-blocking
 * (nsysnet has no SO_RCVTIMEO) and we wait on select() — that keeps the
 * single-threaded UI pumpable while still returning the instant bytes land.
 * It used to sleep a flat 5 ms per empty read instead of waiting on the fd;
 * on a LAN that dominated everything and pinned throughput near 750 KB/s.
 *
 * Returns >0 bytes, 0 = closed, -1 = error/timeout, -2 = user cancel. */
static int recv_timed(int fd, void *buf, int len, uint32_t max_wait_ms,
                      int blocking) {
    if (blocking) {
        for (;;) {
            int n = (int)recv(fd, buf, (size_t)len, 0);
            if (n >= 0) return n;
            if (errno != EINTR) return -1;
        }
    }

    uint32_t waited_ms = 0, next_cb = 500;
    for (;;) {
        int n = (int)recv(fd, buf, (size_t)len, MSG_DONTWAIT);
        if (n >= 0) return n;
        if (errno != EWOULDBLOCK && errno != EAGAIN && errno != EINTR) return -1;

        fd_set rfds;
        FD_ZERO(&rfds);
        FD_SET(fd, &rfds);
        struct timeval tv;
        tv.tv_sec  = 0;
        tv.tv_usec = (long)RECV_POLL_SLICE_MS * 1000;

        int sel = select(fd + 1, &rfds, NULL, NULL, &tv);
        if (sel > 0) continue;                       /* data ready — read it */
        if (sel < 0 && errno != EINTR) return -1;

        /* Timed out with nothing to read: only now has real time passed. */
        waited_ms += RECV_POLL_SLICE_MS;
        if (waited_ms >= next_cb) {
            next_cb += 500;
            if (g_wait_cb && g_wait_cb(waited_ms)) return -2;
        }
        if (waited_ms >= max_wait_ms) return -1;
    }
}

static uint32_t header_wait(const HttpRequest *req) {
    return (req && req->header_timeout_ms) ? req->header_timeout_ms
                                           : HTTP_HEADER_WAIT_MS;
}

static int parse_url(const char *url, char *host_out, size_t host_size,
                     int *port_out, char *path_out, size_t path_size)
{
    const char *p = url;
    if (strncmp(p, "http://", 7) != 0) return -1;
    p += 7;

    const char *host_start = p;
    while (*p && *p != ':' && *p != '/') p++;
    size_t host_len = (size_t)(p - host_start);
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

/* Connect with a bounded wait.
 *
 * A blocking connect() to a host that is simply not there sits in the kernel
 * for the full TCP retransmit budget — on the console that reads as a hard
 * freeze, because the whole app is one thread.  So: put the socket in
 * non-blocking mode, poll for writability, then read SO_ERROR.
 * Returns 0 on success, -1 on failure/timeout. */
static int connect_timed(int fd, const struct sockaddr *addr, socklen_t addrlen) {
    int nb = 1;
    setsockopt(fd, SOL_SOCKET, SO_NONBLOCK, &nb, sizeof(nb));

    int rc = connect(fd, addr, addrlen);
    if (rc != 0 && errno != EINPROGRESS && errno != EWOULDBLOCK && errno != EALREADY) {
        return -1;
    }

    if (rc != 0) {
        uint32_t waited = 0;
        for (;;) {
            fd_set wfds, efds;
            FD_ZERO(&wfds); FD_SET(fd, &wfds);
            FD_ZERO(&efds); FD_SET(fd, &efds);
            struct timeval tv = { .tv_sec = 0, .tv_usec = 100 * 1000 };

            int sel = select(fd + 1, NULL, &wfds, &efds, &tv);
            if (sel > 0) {
                if (FD_ISSET(fd, &efds)) return -1;
                int soerr = 0;
                socklen_t len = sizeof(soerr);
                if (getsockopt(fd, SOL_SOCKET, SO_ERROR, &soerr, &len) != 0) return -1;
                if (soerr != 0) return -1;
                break;
            }
            if (sel < 0 && errno != EINTR) return -1;

            waited += 100;
            if (g_wait_cb && (waited % 500) == 0 && g_wait_cb(waited)) return -1;
            if (waited >= HTTP_CONNECT_WAIT_MS) return -1;
        }
    }

    nb = 0;
    setsockopt(fd, SOL_SOCKET, SO_NONBLOCK, &nb, sizeof(nb));
    return 0;
}

static int connect_to(const char *host, int port) {
    char portstr[8];
    snprintf(portstr, sizeof(portstr), "%d", port);

    struct addrinfo hints;
    memset(&hints, 0, sizeof(hints));
    hints.ai_family   = AF_INET;
    hints.ai_socktype = SOCK_STREAM;

    struct addrinfo *res = NULL;
    if (getaddrinfo(host, portstr, &hints, &res) != 0 || !res) return -2;

    int fd = -1;
    for (struct addrinfo *ai = res; ai; ai = ai->ai_next) {
        fd = socket(ai->ai_family, ai->ai_socktype, ai->ai_protocol);
        if (fd < 0) continue;

        /*
         * Wii U TCP tuning.  These MUST be set before connect(): window
         * scaling is negotiated in the SYN, so enabling it afterwards has no
         * effect on the connection.
         *
         * Nintendo's stack ships with window scaling OFF, which caps the
         * advertised receive window at 65535 bytes however large SO_RCVBUF
         * is.  A window-bound stream runs at window/RTT and is completely
         * insensitive to how fast the application reads — which is why a
         * download sat at ~750 KB/s on a LAN that measures 13.8 MB/s, and why
         * changing the receive loop's polling made no difference at all.
         *
         * SO_WINSCALE first, then SO_RCVBUF: the buffer size is what the
         * scaled window is derived from.
         */
        int on = 1;
        setsockopt(fd, SOL_SOCKET, SO_WINSCALE, &on, sizeof(on));
        setsockopt(fd, SOL_SOCKET, SO_TCPSACK, &on, sizeof(on));
        /* Slow start costs a visible ramp on every one of a WUP set's 25-47
         * separate connections; on a LAN there is no congestion to discover. */
        setsockopt(fd, SOL_SOCKET, SO_NOSLOWSTART, &on, sizeof(on));

        /* An oversized SO_RCVBUF request can be rejected outright, leaving
         * the tiny default in place — so fall down a cascade and keep the
         * largest size the stack actually accepts.  (NUSspli ships 128 KB;
         * anything at or above that is comfortably past the LAN
         * bandwidth-delay product once window scaling is on.) */
        static const int rcvbuf_sizes[] = { 1024 * 1024, 512 * 1024,
                                            256 * 1024, 128 * 1024 };
        for (size_t i = 0; i < sizeof(rcvbuf_sizes) / sizeof(rcvbuf_sizes[0]); i++) {
            if (setsockopt(fd, SOL_SOCKET, SO_RCVBUF,
                           &rcvbuf_sizes[i], sizeof(int)) == 0)
                break;
        }

        int sndbuf = 128 * 1024;   /* uploads: same reasoning, NUSspli's size */
        setsockopt(fd, SOL_SOCKET, SO_SNDBUF, &sndbuf, sizeof(sndbuf));

        /* Read back what the stack actually accepted — the perf line reports
         * it, so a rejected option shows up as a number instead of a theory. */
        int applied = 0;
        socklen_t applied_len = sizeof(applied);
        if (getsockopt(fd, SOL_SOCKET, SO_RCVBUF, &applied, &applied_len) == 0)
            g_perf.rcvbuf = applied;

        if (connect_timed(fd, ai->ai_addr, ai->ai_addrlen) == 0) {
            /* Our request headers are small; Nagle would hold them waiting
             * for more data that never comes. */
            setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &on, sizeof(on));
            break;
        }
        close(fd);
        fd = -1;
    }
    freeaddrinfo(res);
    return fd < 0 ? -3 : fd;
}

static int send_all(int fd, const void *buf, size_t len) {
    const char *p = (const char *)buf;
    size_t remaining = len;
    while (remaining) {
        int n = (int)send(fd, p, remaining, 0);
        if (n <= 0) {
            if (n < 0 && (errno == EWOULDBLOCK || errno == EAGAIN || errno == EINTR)) {
                sleep_ms(5);
                continue;
            }
            return -1;
        }
        p         += n;
        remaining -= (size_t)n;
    }
    return 0;
}

/* Send the request body: from RAM (req->body) or streamed from a file
 * (req->body_fp) so multi-megabyte card images need no big heap buffer. */
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
                         const char *path,
                         int chunked)
{
    /* Chunked request bodies are an HTTP/1.1 feature — h11 (uvicorn) rejects
     * chunked framing on a 1.0 request, so the streamed-upload path has to
     * advertise 1.1.  Everything else stays on 1.0 to keep the response
     * framing "read until close". */
    int n = snprintf(out, out_size,
        "%s %s HTTP/%s\r\n"
        "Host: %s:%d\r\n"
        "X-API-Key: %s\r\n"
        "User-Agent: wiiusync/" APP_VERSION "\r\n"
        "Connection: close\r\n",
        req->method ? req->method : "GET",
        path, chunked ? "1.1" : "1.0",
        host, port,
        req->api_key ? req->api_key : "");
    if (n < 0 || (size_t)n >= out_size) return -1;

    if (req->console_id && req->console_id[0]) {
        n += snprintf(out + n, out_size - n, "X-Console-Id: %s\r\n", req->console_id);
        if ((size_t)n >= out_size) return -1;
    }
    if (req->range_start > 0) {
        n += snprintf(out + n, out_size - n,
                      "Range: bytes=%llu-\r\n",
                      (unsigned long long)req->range_start);
        if ((size_t)n >= out_size) return -1;
    }
    if (chunked) {
        n += snprintf(out + n, out_size - n,
                      "Content-Type: %s\r\n"
                      "Transfer-Encoding: chunked\r\n",
                      req->body_content_type ? req->body_content_type
                                             : "application/octet-stream");
        if ((size_t)n >= out_size) return -1;
    } else if (req->body_len > 0) {
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

static int read_headers(int fd, char *buf, size_t buf_size, size_t *total_out,
                        uint32_t header_wait_ms, int blocking) {
    size_t total = 0;
    if (total_out) *total_out = 0;
    while (total + 1 < buf_size) {
        int n = recv_timed(fd, buf + total, (int)(buf_size - 1 - total),
                           header_wait_ms, blocking);
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

/* Open a connection and send the request head (+ body when not chunked).
 * Returns the socket fd, or negative. */
static int open_request(const HttpRequest *req, int chunked) {
    char host[128];
    int  port;
    char path[1024];
    if (parse_url(req->server_url, host, sizeof(host), &port, path, sizeof(path)) != 0)
        return -1;
    snprintf(path, sizeof(path), "%s", req->path);

    int fd = connect_to(host, port);
    if (fd < 0) return -2;

    char header_buf[1280];
    int  header_len = build_request(header_buf, sizeof(header_buf),
                                    req, host, port, path, chunked);
    if (header_len < 0) { close(fd); return -3; }

    if (send_all(fd, header_buf, (size_t)header_len) < 0) { close(fd); return -4; }
    if (!chunked && send_body(fd, req) < 0) { close(fd); return -4; }
    return fd;
}

int http_get_buf(const HttpRequest *req,
                 uint8_t *out, uint32_t out_size,
                 int *status_out)
{
    if (status_out) *status_out = 0;

    int fd = open_request(req, 0);
    if (fd < 0) return fd;

    char rbuf[RECV_BUF_SIZE];
    size_t total_read = 0;
    int  hlen = read_headers(fd, rbuf, sizeof(rbuf), &total_read,
                             header_wait(req), 0);
    if (hlen < 0) { close(fd); return -5; }

    int status = parse_status(rbuf);
    if (status_out) *status_out = status;

    uint32_t cap = out_size > 0 ? out_size - 1 : 0;

    uint64_t content_length = parse_content_length(rbuf, (size_t)hlen);
    if (content_length > (uint64_t)cap) { close(fd); return -6; }

    size_t body_in_rbuf = (total_read > (size_t)hlen) ? total_read - (size_t)hlen : 0;

    uint32_t written = 0;
    if (body_in_rbuf > 0 && cap > 0) {
        uint32_t take = (body_in_rbuf < cap) ? (uint32_t)body_in_rbuf : cap;
        memcpy(out, rbuf + hlen, take);
        written = take;
    }
    while (written < cap) {
        int n = recv_timed(fd, out + written, (int)(cap - written),
                           HTTP_BODY_WAIT_MS, 0);
        if (n <= 0) break;
        written += (uint32_t)n;
    }
    if (out_size > 0) out[written] = '\0';

    close(fd);
    return (int)written;
}

static int file_stream_writer(void *user, const uint8_t *data, size_t len) {
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

    int blocking = req->io_blocking;

    int fd = open_request(req, 0);
    if (fd < 0) return fd;
    if (blocking) g_active_fd = fd;

    char rbuf[RECV_BUF_SIZE];
    size_t total_read = 0;
    int  hlen = read_headers(fd, rbuf, sizeof(rbuf), &total_read,
                             header_wait(req), blocking);
    if (hlen == -2) { g_active_fd = -1; close(fd); return 1; }   /* user cancel = paused */
    if (hlen < 0) { g_active_fd = -1; close(fd); return -5; }

    int status = parse_status(rbuf);
    uint64_t content_length = parse_content_length(rbuf, (size_t)hlen);
    if (info_out) { info_out->status = status; info_out->content_length = content_length; }

    int rc = 0;
    uint64_t total_done   = 0;
    size_t   in_rbuf_body = (total_read > (size_t)hlen) ? total_read - (size_t)hlen : 0;

    if (status < 200 || status >= 300) { rc = -6; goto out; }
    if (begin && begin(content_length, user) != 0) { rc = -9; goto out; }

    if (in_rbuf_body > 0) {
        if (writer(user, (const uint8_t *)(rbuf + hlen), in_rbuf_body) != 0) {
            rc = -7; goto out;
        }
        total_done += in_rbuf_body;
    }

    static char chunk[65536];
    while (1) {
        uint64_t t0 = now_us();
        int n = recv_timed(fd, chunk, sizeof(chunk), HTTP_BODY_WAIT_MS, blocking);
        uint64_t t1 = now_us();
        g_perf.recv_us += t1 - t0;
        g_perf.recv_calls++;

        if (n == -2) { rc = 1; goto out; }   /* user cancel = paused */
        if (n == 0) break;
        if (n < 0)  { rc = -8; goto out; }
        g_perf.recv_bytes += (uint64_t)n;

        if (writer(user, (const uint8_t *)chunk, (size_t)n) != 0) { rc = -7; goto out; }
        g_perf.write_us += now_us() - t1;

        total_done += (uint64_t)n;
        uint64_t t2 = now_us();
        if (progress && progress(total_done, content_length) != 0) { rc = 1; goto out; }
        g_perf.progress_us += now_us() - t2;
    }

out:
    g_active_fd = -1;
    close(fd);
    return rc;
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

/* ---- chunked POST ---- */

typedef struct {
    int fd;
    int failed;
} ChunkCtx;

static int chunk_writer(void *user, const uint8_t *data, size_t len) {
    ChunkCtx *c = (ChunkCtx *)user;
    if (c->failed) return -1;
    if (len == 0) return 0;

    char head[32];
    int hn = snprintf(head, sizeof(head), "%x\r\n", (unsigned)len);
    if (send_all(c->fd, head, (size_t)hn) < 0 ||
        send_all(c->fd, data, len) < 0 ||
        send_all(c->fd, "\r\n", 2) < 0) {
        c->failed = 1;
        return -1;
    }
    return 0;
}

int http_post_chunked(const HttpRequest *req,
                      HttpProducerFn producer,
                      void *user,
                      uint8_t *resp, uint32_t resp_size)
{
    if (resp && resp_size) resp[0] = '\0';
    if (!producer) return -1;

    int fd = open_request(req, 1);
    if (fd < 0) return fd;

    ChunkCtx ctx = { fd, 0 };
    int prc = producer(chunk_writer, &ctx, user);
    if (prc != 0 || ctx.failed) { close(fd); return -4; }

    if (send_all(fd, "0\r\n\r\n", 5) < 0) { close(fd); return -4; }

    char rbuf[RECV_BUF_SIZE];
    size_t total_read = 0;
    int hlen = read_headers(fd, rbuf, sizeof(rbuf), &total_read,
                            header_wait(req), 0);
    if (hlen < 0) { close(fd); return -5; }

    int status = parse_status(rbuf);
    if (resp && resp_size > 1) {
        size_t body = (total_read > (size_t)hlen) ? total_read - (size_t)hlen : 0;
        if (body > resp_size - 1) body = resp_size - 1;
        memcpy(resp, rbuf + hlen, body);
        resp[body] = '\0';
    }
    close(fd);
    return status;
}
