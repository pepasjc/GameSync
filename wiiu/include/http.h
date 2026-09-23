#ifndef WIIUSYNC_HTTP_H
#define WIIUSYNC_HTTP_H

#include "common.h"

#include <stdio.h>

/*
 * Tiny socket-based HTTP/1.0 client — the same shape as the PS2 / NDS /
 * GameCube clients, but over nsysnet's BSD sockets.  No keep-alive, no TLS.
 *
 * Improvement over the GameCube port: the host is resolved with
 * getaddrinfo(), so ``server_url`` accepts hostnames as well as dotted IPs.
 *
 * Chunked POST (http_post_chunked) is used for streamed bundle uploads, so a
 * multi-megabyte Wii U save never has to be assembled in RAM.
 */

typedef int (*HttpProgressFn)(uint64_t downloaded, uint64_t total);
typedef int (*HttpStreamBeginFn)(uint64_t content_length, void *user);
typedef int (*HttpWriteFn)(void *user, const uint8_t *data, size_t len);

/* Called every ~0.5 s while a recv is waiting for data (e.g. the server
 * converting a ROM before responding).  Return non-zero to cancel. */
typedef int (*HttpWaitFn)(uint32_t waited_ms);
void http_set_wait_cb(HttpWaitFn cb);

typedef struct {
    const char *server_url;     /* "http://host:port" — no trailing slash */
    const char *api_key;
    const char *console_id;     /* optional X-Console-Id header */
    const char *path;           /* "/api/v1/..." */
    const char *method;         /* "GET" / "POST" */
    uint64_t    range_start;    /* optional Range header; 0 = none */
    const uint8_t *body;        /* optional request body (in RAM) */
    FILE          *body_fp;     /* or streamed from a file (body_len bytes) */
    uint32_t       body_len;
    const char    *body_content_type;
    /* How long to wait for the response headers, in ms.  0 = the default
     * 30-minute allowance, which only makes sense for requests that can
     * trigger a server-side ROM conversion.  Ordinary API calls should set
     * something short so an unreachable server fails fast instead of
     * looking like a hang. */
    uint32_t       header_timeout_ms;
    /* Worker-thread mode: use plain blocking recv() in the hot loop (fastest
     * path, no per-refill select round-trip into nsysnet).  The caller MUST
     * enforce timeouts/cancel from another thread via http_abort_active() —
     * with this set the timeouts above are not applied. */
    int            io_blocking;
} HttpRequest;

/* Force the worker's in-flight streamed transfer (io_blocking) to fail by
 * shutting its socket down.  Safe to call from another thread; no-op when
 * nothing is active. */
void http_abort_active(void);

/* Header wait for a plain API call (status, catalog, titles, sync, saves). */
#define HTTP_API_TIMEOUT_MS 15000u

typedef struct {
    int      status;            /* HTTP status code */
    uint64_t content_length;    /* 0 if unknown */
} HttpResponseInfo;

/* Fixed-buffer GET/POST: writes body bytes into out (capped at out_size).
 * Returns body length on success, negative on failure. */
int  http_get_buf(const HttpRequest *req,
                  uint8_t *out, uint32_t out_size,
                  int *status_out);

/* Streaming GET to a stdio FILE.  setvbuf() with a fat buffer first. */
int  http_get_stream(const HttpRequest *req,
                     FILE *out_fp,
                     HttpProgressFn progress,
                     HttpResponseInfo *info_out);

/* Generic streaming GET — begin() runs after headers, writer() gets chunks. */
int  http_get_stream_cb(const HttpRequest *req,
                        HttpStreamBeginFn begin,
                        HttpWriteFn writer,
                        void *user,
                        HttpProgressFn progress,
                        HttpResponseInfo *info_out);

/* Chunked POST.  ``producer`` is handed a write function it calls as many
 * times as it likes; each call becomes one HTTP chunk.  The response body
 * (up to resp_size) lands in ``resp``.  Returns the HTTP status, or negative
 * on transport failure. */
typedef int (*HttpProducerFn)(HttpWriteFn write, void *write_ctx, void *user);

int  http_post_chunked(const HttpRequest *req,
                       HttpProducerFn producer,
                       void *user,
                       uint8_t *resp, uint32_t resp_size);

/* ---- transfer profiling ----
 *
 * Splits a download's wall-clock into its three candidates so a slow transfer
 * can be attributed instead of guessed at:
 *   recv_us      waiting on / reading from the socket
 *   write_us     handing bytes to the writer (SD or USB)
 *   progress_us  the per-chunk UI callback
 * Overhead is two OSGetSystemTime reads per 64 KB chunk, so it stays on in
 * normal builds. */
typedef struct {
    uint64_t recv_us;
    uint64_t write_us;
    uint64_t progress_us;
    uint64_t recv_bytes;
    uint32_t recv_calls;
    int      rcvbuf;      /* SO_RCVBUF the stack actually accepted */
} HttpPerf;

void http_perf_reset(void);
void http_perf_get(HttpPerf *out);

#endif /* WIIUSYNC_HTTP_H */
