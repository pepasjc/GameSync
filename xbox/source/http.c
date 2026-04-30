// Hand-rolled HTTP/1.0 client over lwIP's BSD-sockets shim. Direct port of
// the NDS client's http.c, with iprintf->debugPrint and standard <sys/...>
// includes swapped for <lwip/...>.

#include "http.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <hal/debug.h>
#include <lwip/inet.h>
#include <lwip/netdb.h>
#include <lwip/sockets.h>

#define HTTP_HEADER_BUF  4096
#define HTTP_TIMEOUT_SEC 30

// Quieter diagnostic output by default. Flip to 1 while debugging the wire.
#ifndef HTTP_VERBOSE
#define HTTP_VERBOSE 0
#endif

#if HTTP_VERBOSE
#define HLOG(...) debugPrint(__VA_ARGS__)
#else
#define HLOG(...) ((void)0)
#endif

// Parse "http://host:port/path" into out_host/out_port/out_path. Returns 0
// on success, -1 if scheme isn't http (https not supported - LAN only).
static int parse_url(const char *url,
                     char *host, int host_cap,
                     int *port,
                     char *path, int path_cap)
{
    const char *start = url;

    if (strncmp(url, "http://", 7) == 0) {
        start = url + 7;
    } else if (strncmp(url, "https://", 8) == 0) {
        return -1;  // TLS not supported on this client.
    }

    const char *colon = strchr(start, ':');
    const char *slash = strchr(start, '/');
    int host_len;

    if (colon && (!slash || colon < slash)) {
        host_len = (int)(colon - start);
        if (host_len >= host_cap) host_len = host_cap - 1;
        memcpy(host, start, host_len);
        host[host_len] = '\0';
        *port = atoi(colon + 1);
        if (slash) {
            snprintf(path, path_cap, "%s", slash);
        } else {
            snprintf(path, path_cap, "/");
        }
    } else if (slash) {
        host_len = (int)(slash - start);
        if (host_len >= host_cap) host_len = host_cap - 1;
        memcpy(host, start, host_len);
        host[host_len] = '\0';
        *port = 80;
        snprintf(path, path_cap, "%s", slash);
    } else {
        snprintf(host, host_cap, "%s", start);
        *port = 80;
        snprintf(path, path_cap, "/");
    }
    return 0;
}

static int send_all(int s, const void *data, size_t size)
{
    const uint8_t *p = (const uint8_t *)data;
    size_t off = 0;
    while (off < size) {
        int n = send(s, p + off, size - off, 0);
        if (n <= 0) return -1;
        off += (size_t)n;
    }
    return 0;
}

static int send_chunk(void *ctx, const uint8_t *data, size_t size)
{
    int s = *(int *)ctx;
    char hdr[24];

    if (size == 0) return 0;
    int n = snprintf(hdr, sizeof(hdr), "%X\r\n", (unsigned)size);
    if (n <= 0 || n >= (int)sizeof(hdr)) return -1;
    if (send_all(s, hdr, (size_t)n) != 0) return -1;
    if (send_all(s, data, size) != 0) return -1;
    if (send_all(s, "\r\n", 2) != 0) return -1;
    return 0;
}

HttpResponse http_request(const char *url,
                          HttpMethod method,
                          const char *api_key,
                          const char *console_id,
                          const char *content_type,
                          const uint8_t *body,
                          size_t body_size)
{
    HttpResponse rsp = {0};
    char host[256] = {0};
    char path[512] = {0};
    int  port = 80;
    int  s = -1;

    if (parse_url(url, host, sizeof(host), &port, path, sizeof(path)) != 0) {
        debugPrint("http: bad url %s\n", url);
        return rsp;
    }

    HLOG("http: %s %s:%d%s\n",
         method == HTTP_GET ? "GET" : "POST", host, port, path);

    // Resolve.
    struct addrinfo hints;
    memset(&hints, 0, sizeof(hints));
    hints.ai_family = AF_INET;
    hints.ai_socktype = SOCK_STREAM;
    char port_str[8];
    snprintf(port_str, sizeof(port_str), "%d", port);

    struct addrinfo *res = NULL;
    int gai = getaddrinfo(host, port_str, &hints, &res);
    if (gai != 0 || !res) {
        debugPrint("http: dns fail (%d) %s\n", gai, host);
        return rsp;
    }

    s = socket(res->ai_family, res->ai_socktype, res->ai_protocol);
    if (s < 0) {
        debugPrint("http: socket fail\n");
        freeaddrinfo(res);
        return rsp;
    }

    struct timeval tv;
    tv.tv_sec = HTTP_TIMEOUT_SEC;
    tv.tv_usec = 0;
    setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, (const char *)&tv, sizeof(tv));
    setsockopt(s, SOL_SOCKET, SO_SNDTIMEO, (const char *)&tv, sizeof(tv));

    if (connect(s, res->ai_addr, res->ai_addrlen) < 0) {
        debugPrint("http: connect fail %s:%d\n", host, port);
        freeaddrinfo(res);
        close(s);
        return rsp;
    }
    freeaddrinfo(res);

    // Build request headers.
    char req[HTTP_HEADER_BUF];
    const char *m = (method == HTTP_GET) ? "GET" : "POST";
    int hdr_len = snprintf(req, sizeof(req),
        "%s %s HTTP/1.0\r\n"
        "Host: %s:%d\r\n"
        "User-Agent: XboxSync/1.0\r\n"
        "X-API-Key: %s\r\n",
        m, path, host, port, api_key ? api_key : "");

    if (console_id && console_id[0]) {
        hdr_len += snprintf(req + hdr_len, sizeof(req) - hdr_len,
                            "X-Console-ID: %s\r\n", console_id);
    }
    if (body && body_size > 0 && method == HTTP_POST) {
        const char *ct = (content_type && content_type[0])
                             ? content_type
                             : "application/octet-stream";
        hdr_len += snprintf(req + hdr_len, sizeof(req) - hdr_len,
            "Content-Type: %s\r\n"
            "Content-Length: %u\r\n", ct, (unsigned)body_size);
    }
    hdr_len += snprintf(req + hdr_len, sizeof(req) - hdr_len,
                        "Connection: close\r\n\r\n");

    if (send(s, req, hdr_len, 0) < 0) {
        debugPrint("http: send headers fail\n");
        close(s);
        return rsp;
    }

    // Body.
    if (body && body_size > 0 && method == HTTP_POST) {
        size_t off = 0;
        while (off < body_size) {
            int chunk = send(s, body + off, body_size - off, 0);
            if (chunk <= 0) {
                debugPrint("http: send body fail at %u/%u\n",
                           (unsigned)off, (unsigned)body_size);
                close(s);
                return rsp;
            }
            off += (size_t)chunk;
        }
    }

    // Receive headers + first chunk of body.
    char hbuf[HTTP_HEADER_BUF];
    int  total = 0;
    char *body_sep = NULL;
    int  content_length = -1;

    while (total < (int)sizeof(hbuf) - 1) {
        int n = recv(s, hbuf + total, (int)sizeof(hbuf) - 1 - total, 0);
        if (n <= 0) {
            if (total == 0) {
                debugPrint("http: recv timeout\n");
                close(s);
                return rsp;
            }
            break;
        }
        total += n;
        hbuf[total] = '\0';

        if (!body_sep) {
            body_sep = strstr(hbuf, "\r\n\r\n");
            if (body_sep) body_sep += 4;
        }
        if (body_sep && content_length < 0) {
            char *cl = strstr(hbuf, "Content-Length:");
            if (!cl) cl = strstr(hbuf, "content-length:");
            if (cl) sscanf(cl + 15, " %d", &content_length);
        }
        if (body_sep && content_length >= 0) {
            int body_off = (int)(body_sep - hbuf);
            int got = total - body_off;
            if (content_length > (int)(sizeof(hbuf) - body_off - 64)) break;
            if (got >= content_length) break;
        }
    }

    sscanf(hbuf, "HTTP/%*d.%*d %d", &rsp.status_code);

    if (body_sep && content_length > 0) {
        int body_off = (int)(body_sep - hbuf);
        int in_buf = total - body_off;
        rsp.body_size = content_length;
        rsp.body = (uint8_t *)malloc(content_length + 1);
        if (!rsp.body) {
            close(s);
            return rsp;
        }
        if (in_buf > 0) {
            int copy = in_buf < content_length ? in_buf : content_length;
            memcpy(rsp.body, body_sep, copy);
        }
        int received = in_buf < content_length ? in_buf : content_length;
        while (received < content_length) {
            int n = recv(s, rsp.body + received, content_length - received, 0);
            if (n <= 0) break;
            received += n;
        }
        if (received != content_length) {
            free(rsp.body);
            rsp.body = NULL;
            rsp.body_size = 0;
        } else {
            rsp.body[content_length] = '\0';
        }
    }

    shutdown(s, SHUT_RDWR);
    close(s);
    rsp.success = (rsp.status_code >= 200 && rsp.status_code < 300);
    return rsp;
}

HttpResponse http_post_chunked(const char *url,
                               const char *api_key,
                               const char *console_id,
                               const char *content_type,
                               HttpStreamProducer producer,
                               void *producer_user)
{
    HttpResponse rsp = {0};
    char host[256] = {0};
    char path[512] = {0};
    int  port = 80;
    int  s = -1;

    if (!producer ||
        parse_url(url, host, sizeof(host), &port, path, sizeof(path)) != 0) {
        debugPrint("http: bad chunked url %s\n", url ? url : "");
        return rsp;
    }

    HLOG("http: POST chunked %s:%d%s\n", host, port, path);

    struct addrinfo hints;
    memset(&hints, 0, sizeof(hints));
    hints.ai_family = AF_INET;
    hints.ai_socktype = SOCK_STREAM;
    char port_str[8];
    snprintf(port_str, sizeof(port_str), "%d", port);

    struct addrinfo *res = NULL;
    int gai = getaddrinfo(host, port_str, &hints, &res);
    if (gai != 0 || !res) {
        debugPrint("http: dns fail (%d) %s\n", gai, host);
        return rsp;
    }

    s = socket(res->ai_family, res->ai_socktype, res->ai_protocol);
    if (s < 0) {
        debugPrint("http: socket fail\n");
        freeaddrinfo(res);
        return rsp;
    }

    struct timeval tv;
    tv.tv_sec = HTTP_TIMEOUT_SEC;
    tv.tv_usec = 0;
    setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, (const char *)&tv, sizeof(tv));
    setsockopt(s, SOL_SOCKET, SO_SNDTIMEO, (const char *)&tv, sizeof(tv));

    if (connect(s, res->ai_addr, res->ai_addrlen) < 0) {
        debugPrint("http: connect fail %s:%d\n", host, port);
        freeaddrinfo(res);
        close(s);
        return rsp;
    }
    freeaddrinfo(res);

    char req[HTTP_HEADER_BUF];
    const char *ct = (content_type && content_type[0])
                         ? content_type
                         : "application/octet-stream";
    int hdr_len = snprintf(req, sizeof(req),
        "POST %s HTTP/1.1\r\n"
        "Host: %s:%d\r\n"
        "User-Agent: XboxSync/1.0\r\n"
        "X-API-Key: %s\r\n",
        path, host, port, api_key ? api_key : "");

    if (console_id && console_id[0]) {
        hdr_len += snprintf(req + hdr_len, sizeof(req) - hdr_len,
                            "X-Console-ID: %s\r\n", console_id);
    }
    hdr_len += snprintf(req + hdr_len, sizeof(req) - hdr_len,
        "Content-Type: %s\r\n"
        "Transfer-Encoding: chunked\r\n"
        "Connection: close\r\n\r\n", ct);

    if (hdr_len <= 0 || hdr_len >= (int)sizeof(req) ||
        send_all(s, req, (size_t)hdr_len) != 0) {
        debugPrint("http: send chunked headers fail\n");
        close(s);
        return rsp;
    }

    if (producer(send_chunk, &s, producer_user) != 0 ||
        send_all(s, "0\r\n\r\n", 5) != 0) {
        debugPrint("http: send chunked body fail\n");
        close(s);
        return rsp;
    }

    // Receive headers + first chunk of body.
    char hbuf[HTTP_HEADER_BUF];
    int  total = 0;
    char *body_sep = NULL;
    int  content_length = -1;

    while (total < (int)sizeof(hbuf) - 1) {
        int n = recv(s, hbuf + total, (int)sizeof(hbuf) - 1 - total, 0);
        if (n <= 0) {
            if (total == 0) {
                debugPrint("http: recv timeout\n");
                close(s);
                return rsp;
            }
            break;
        }
        total += n;
        hbuf[total] = '\0';

        if (!body_sep) {
            body_sep = strstr(hbuf, "\r\n\r\n");
            if (body_sep) body_sep += 4;
        }
        if (body_sep && content_length < 0) {
            char *cl = strstr(hbuf, "Content-Length:");
            if (!cl) cl = strstr(hbuf, "content-length:");
            if (cl) sscanf(cl + 15, " %d", &content_length);
        }
        if (body_sep && content_length >= 0) {
            int body_off = (int)(body_sep - hbuf);
            int got = total - body_off;
            if (content_length > (int)(sizeof(hbuf) - body_off - 64)) break;
            if (got >= content_length) break;
        }
    }

    sscanf(hbuf, "HTTP/%*d.%*d %d", &rsp.status_code);

    if (body_sep && content_length > 0) {
        int body_off = (int)(body_sep - hbuf);
        int in_buf = total - body_off;
        rsp.body_size = content_length;
        rsp.body = (uint8_t *)malloc(content_length + 1);
        if (!rsp.body) {
            close(s);
            return rsp;
        }
        if (in_buf > 0) {
            int copy = in_buf < content_length ? in_buf : content_length;
            memcpy(rsp.body, body_sep, copy);
        }
        int received = in_buf < content_length ? in_buf : content_length;
        while (received < content_length) {
            int n = recv(s, rsp.body + received, content_length - received, 0);
            if (n <= 0) break;
            received += n;
        }
        if (received != content_length) {
            free(rsp.body);
            rsp.body = NULL;
            rsp.body_size = 0;
        } else {
            rsp.body[content_length] = '\0';
        }
    }

    shutdown(s, SHUT_RDWR);
    close(s);
    rsp.success = (rsp.status_code >= 200 && rsp.status_code < 300);
    return rsp;
}

int http_get_stream(const char *url,
                    const char *api_key,
                    const char *console_id,
                    HttpWriteFn write,
                    void *write_ctx,
                    uint64_t *out_content_length)
{
    char host[256] = {0};
    char path[512] = {0};
    int  port = 80;
    int  s = -1;
    int  status = 0;

    if (out_content_length) *out_content_length = 0;
    if (!write ||
        parse_url(url, host, sizeof(host), &port, path, sizeof(path)) != 0) {
        debugPrint("http: bad stream url %s\n", url ? url : "");
        return -1;
    }

    struct addrinfo hints;
    memset(&hints, 0, sizeof(hints));
    hints.ai_family = AF_INET;
    hints.ai_socktype = SOCK_STREAM;
    char port_str[8];
    snprintf(port_str, sizeof(port_str), "%d", port);

    struct addrinfo *res = NULL;
    int gai = getaddrinfo(host, port_str, &hints, &res);
    if (gai != 0 || !res) {
        debugPrint("http: dns fail (%d) %s\n", gai, host);
        return -1;
    }

    s = socket(res->ai_family, res->ai_socktype, res->ai_protocol);
    if (s < 0) {
        freeaddrinfo(res);
        return -1;
    }

    struct timeval tv;
    tv.tv_sec = HTTP_TIMEOUT_SEC;
    tv.tv_usec = 0;
    setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, (const char *)&tv, sizeof(tv));
    setsockopt(s, SOL_SOCKET, SO_SNDTIMEO, (const char *)&tv, sizeof(tv));

    if (connect(s, res->ai_addr, res->ai_addrlen) < 0) {
        debugPrint("http: connect fail %s:%d\n", host, port);
        freeaddrinfo(res);
        close(s);
        return -1;
    }
    freeaddrinfo(res);

    char req[HTTP_HEADER_BUF];
    int hdr_len = snprintf(req, sizeof(req),
        "GET %s HTTP/1.0\r\n"
        "Host: %s:%d\r\n"
        "User-Agent: XboxSync/1.0\r\n"
        "X-API-Key: %s\r\n",
        path, host, port, api_key ? api_key : "");

    if (console_id && console_id[0]) {
        hdr_len += snprintf(req + hdr_len, sizeof(req) - hdr_len,
                            "X-Console-ID: %s\r\n", console_id);
    }
    hdr_len += snprintf(req + hdr_len, sizeof(req) - hdr_len,
                        "Connection: close\r\n\r\n");

    if (hdr_len <= 0 || hdr_len >= (int)sizeof(req) ||
        send_all(s, req, (size_t)hdr_len) != 0) {
        close(s);
        return -1;
    }

    char hbuf[HTTP_HEADER_BUF];
    int  total = 0;
    char *body_sep = NULL;
    uint64_t content_length = 0;
    int have_content_length = 0;

    while (total < (int)sizeof(hbuf) - 1) {
        int n = recv(s, hbuf + total, (int)sizeof(hbuf) - 1 - total, 0);
        if (n <= 0) {
            close(s);
            return -1;
        }
        total += n;
        hbuf[total] = '\0';
        body_sep = strstr(hbuf, "\r\n\r\n");
        if (body_sep) {
            body_sep += 4;
            break;
        }
    }

    sscanf(hbuf, "HTTP/%*d.%*d %d", &status);
    {
        char *cl = strstr(hbuf, "Content-Length:");
        if (!cl) cl = strstr(hbuf, "content-length:");
        if (cl) {
            unsigned long long parsed = 0;
            if (sscanf(cl + 15, " %llu", &parsed) == 1) {
                content_length = (uint64_t)parsed;
                have_content_length = 1;
            }
        }
    }
    if (out_content_length && have_content_length) {
        *out_content_length = content_length;
    }

    if (status < 200 || status >= 300) {
        shutdown(s, SHUT_RDWR);
        close(s);
        return status > 0 ? status : -1;
    }

    if (!body_sep) {
        shutdown(s, SHUT_RDWR);
        close(s);
        return -1;
    }

    int body_off = (int)(body_sep - hbuf);
    int in_buf = total - body_off;
    uint64_t received = 0;
    if (in_buf > 0) {
        if (write(write_ctx, (const uint8_t *)body_sep, (size_t)in_buf) != 0) {
            shutdown(s, SHUT_RDWR);
            close(s);
            return -2;
        }
        received += (uint64_t)in_buf;
    }

    uint8_t buf[8192];
    for (;;) {
        if (have_content_length && received >= content_length) {
            break;
        }
        int n = recv(s, buf, sizeof(buf), 0);
        if (n <= 0) break;
        if (write(write_ctx, buf, (size_t)n) != 0) {
            shutdown(s, SHUT_RDWR);
            close(s);
            return -2;
        }
        received += (uint64_t)n;
    }

    shutdown(s, SHUT_RDWR);
    close(s);
    if (have_content_length && received != content_length) {
        return -3;
    }
    return status;
}

void http_response_free(HttpResponse *r)
{
    if (r && r->body) {
        free(r->body);
        r->body = NULL;
        r->body_size = 0;
    }
}
