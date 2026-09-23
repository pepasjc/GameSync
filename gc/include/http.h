#ifndef GCSYNC_HTTP_H
#define GCSYNC_HTTP_H

#include "common.h"

#include <stdio.h>

/*
 * Tiny socket-based HTTP/1.0 client — same shape as the PS2 / NDS clients,
 * but over libogc's net_* BSD socket API (BBA).  No keep-alive, no chunked
 * transfer-encoding, no TLS.  Host must be a dotted LAN IP.
 */

typedef int (*HttpProgressFn)(uint64_t downloaded, uint64_t total);
typedef int (*HttpStreamBeginFn)(uint64_t content_length, void *user);
typedef int (*HttpWriteFn)(const void *data, uint32_t len, void *user);

/* Called every ~0.5 s while a recv is waiting for data (e.g. the server
 * converting a ROM before responding).  Return non-zero to cancel. */
typedef int (*HttpWaitFn)(uint32_t waited_ms);
void http_set_wait_cb(HttpWaitFn cb);

typedef struct {
    const char *server_url;     /* "http://host:port" — no trailing slash */
    const char *api_key;
    const char *path;           /* "/api/v1/..." */
    const char *method;         /* "GET" / "POST" */
    uint64_t    range_start;    /* optional Range header; 0 = none */
    const uint8_t *body;        /* optional request body (in RAM) */
    FILE          *body_fp;     /* or streamed from a file (body_len bytes) */
    uint32_t       body_len;
    const char    *body_content_type;
} HttpRequest;

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

/* Parallel range download: split the resource into ``nconns`` byte-range
 * TCP streams (each with its own tiny lwIP window, so aggregate throughput
 * is ~nconns x a single stream) and write positioned into target_path.part,
 * renaming to target_path on success.
 *
 * Returns: 0 ok / 1 paused (wait-cb cancel) / -1 net / -2 fs / -3 HTTP /
 *          -9 range not supported (caller should fall back to single stream).
 * ``total_out`` receives the full content length. */
int  http_download_parallel(const char *server_url, const char *api_key,
                            const char *path, const char *target_path,
                            uint64_t start_offset, int nconns,
                            HttpProgressFn progress, uint64_t *total_out);

#endif /* GCSYNC_HTTP_H */
