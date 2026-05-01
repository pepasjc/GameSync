// Minimal HTTP/1.0 client over lwIP's BSD sockets. Modelled on the NDS
// client's hand-rolled HTTP, ported to nxdk's lwIP shim.

#ifndef XBOX_HTTP_H
#define XBOX_HTTP_H

#include <stddef.h>
#include <stdint.h>

typedef enum {
    HTTP_GET  = 0,
    HTTP_POST = 1,
} HttpMethod;

typedef struct {
    int       status_code;   // e.g. 200, 401
    int       success;       // 1 if 2xx
    uint8_t  *body;          // malloc'd; may be NULL on empty response
    int       body_size;
} HttpResponse;

typedef int (*HttpWriteFn)(void *ctx, const uint8_t *data, size_t size);
typedef int (*HttpStreamProducer)(HttpWriteFn write, void *write_ctx, void *user);

// Issue a single HTTP request. ``body``/``body_size`` are optional and
// only used for POST. ``console_id`` (optional, NULL to skip) is sent as
// ``X-Console-ID`` header. ``content_type`` (NULL = "application/octet-stream")
// only matters for POST bodies. Returns a populated HttpResponse; caller
// must call http_response_free() to release the body buffer.
HttpResponse http_request(const char *url,
                          HttpMethod method,
                          const char *api_key,
                          const char *console_id,
                          const char *content_type,
                          const uint8_t *body,
                          size_t body_size);

// POST a body produced incrementally. Uses HTTP/1.1 chunked transfer so the
// caller does not need to know the compressed body size up front.
HttpResponse http_post_chunked(const char *url,
                               const char *api_key,
                               const char *console_id,
                               const char *content_type,
                               HttpStreamProducer producer,
                               void *producer_user);

// Stream a GET response body to a callback. This is for multi-GB ROM ZIPs:
// the body is never accumulated in RAM. Returns the HTTP status code on a
// completed request, or a negative value for transport / writer failure.
int http_get_stream(const char *url,
                    const char *api_key,
                    const char *console_id,
                    HttpWriteFn writer,
                    void *write_ctx,
                    uint64_t *out_content_length);

void http_response_free(HttpResponse *r);

#endif // XBOX_HTTP_H
