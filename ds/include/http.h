#ifndef HTTP_H
#define HTTP_H

#include <stdint.h>
#include <stddef.h>

// HTTP method types
typedef enum {
    HTTP_GET,
    HTTP_POST,
    HTTP_PUT
} HttpMethod;

// HTTP response
typedef struct {
    int status_code;
    uint8_t *body;
    size_t body_size;
    int success;
} HttpResponse;

// HTTP client initialization
int http_init(void);

// Make HTTP request
HttpResponse http_request(
    const char *url,
    HttpMethod method,
    const char *api_key,
    const uint8_t *body,
    size_t body_size
);

// Same, with an explicit Content-Type for the body (NULL = octet-stream)
HttpResponse http_request_ex(
    const char *url,
    HttpMethod method,
    const char *api_key,
    const char *content_type,
    const uint8_t *body,
    size_t body_size
);

// Turn the connection debug output on/off (default on)
void http_set_verbose(int verbose);

// Socket send/receive timeout in seconds for later requests (default 30)
void http_set_timeout(int seconds);

// Streaming GET: the body goes to `sink` in chunks (up to 32 KB) instead of
// RAM, for files too big to hold. `sink` returns 0 to go on, 1 to cancel,
// -1 on a write error. `total` is the Content-Length (0 if not sent).
typedef int (*HttpSinkFn)(const uint8_t *data, size_t size, uint32_t done, uint32_t total, void *user);

typedef enum {
    HTTP_DL_OK = 0,
    HTTP_DL_CONNECT = -1,    // DNS/socket/connect/send failed, or no response
    HTTP_DL_STATUS = -2,     // non-2xx status; `error` holds the start of the body
    HTTP_DL_WRITE = -3,      // sink reported a write error
    HTTP_DL_CANCELLED = -4,  // sink asked to stop
    HTTP_DL_SHORT = -5,      // connection ended before Content-Length bytes
} HttpDownloadResult;

typedef struct {
    int status_code;
    uint32_t total;          // Content-Length, 0 if unknown
    uint32_t received;       // body bytes passed to the sink
    char error[96];          // server's error text for HTTP_DL_STATUS
} HttpDownloadInfo;

HttpDownloadResult http_download(const char *url, const char *api_key,
                                 HttpSinkFn sink, void *user, HttpDownloadInfo *info);

// Free response body
void http_response_free(HttpResponse *response);

// Cleanup HTTP
void http_cleanup(void);

#endif
