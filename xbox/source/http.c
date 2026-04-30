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

void http_response_free(HttpResponse *r)
{
    if (r && r->body) {
        free(r->body);
        r->body = NULL;
        r->body_size = 0;
    }
}
