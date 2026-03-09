#include "network.h"
#include <3ds/svc.h>

#define HTTP_BUF_SIZE 0x1000 // 4KB read chunks
#define MAX_RESPONSE  (2 * 1024 * 1024) // 2MB max response
#define MAX_POST_SIZE 0x70000 // 448KB max POST body (leave headroom in 512KB buffer)

#define TIMEOUT_RESPONSE (15ULL * 1000000000ULL) // 15s for server to respond
#define TIMEOUT_TRANSFER (30ULL * 1000000000ULL) // 30s per data chunk

// Brief delay between requests to let httpc clean up
static void request_delay(void) {
    svcSleepThread(50000000LL); // 50ms
}

bool network_init(void) {
    // Shared memory size for POST data (512KB)
    return R_SUCCEEDED(httpcInit(0x80000));
}

void network_exit(void) {
    httpcExit();
}

// Build full URL from config base + path
static void build_url(const AppConfig *config, const char *path, char *url, int url_size) {
    snprintf(url, url_size, "%s/api/v1%s", config->server_url, path);
}

// Download data with timeout - wraps httpcReceiveDataTimeout + httpcGetDownloadSizeState
static Result download_data_timeout(httpcContext *context, u8 *buffer, u32 size, u32 *downloadedsize, u64 timeout) {
    u32 pos_before = 0, contentsize = 0;
    Result ret = httpcGetDownloadSizeState(context, &pos_before, &contentsize);
    if (R_FAILED(ret)) return ret;

    ret = httpcReceiveDataTimeout(context, buffer, size, timeout);

    u32 pos_after = 0;
    httpcGetDownloadSizeState(context, &pos_after, &contentsize);
    if (downloadedsize) *downloadedsize = pos_after - pos_before;

    return ret;
}

// Read full response body with dynamic buffer
static u8 *read_response(httpcContext *context, u32 *out_size) {
    u32 size = 0;
    u32 buf_cap = HTTP_BUF_SIZE;
    u8 *buf = (u8 *)malloc(buf_cap);
    if (!buf) return NULL;

    Result res;
    do {
        // Grow buffer if needed
        if (size + HTTP_BUF_SIZE > buf_cap) {
            buf_cap *= 2;
            if (buf_cap > MAX_RESPONSE) {
                free(buf);
                return NULL;
            }
            u8 *new_buf = (u8 *)realloc(buf, buf_cap);
            if (!new_buf) {
                free(buf);
                return NULL;
            }
            buf = new_buf;
        }

        u32 read = 0;
        res = download_data_timeout(context, buf + size, HTTP_BUF_SIZE, &read, TIMEOUT_TRANSFER);
        size += read;

        if (res == (Result)HTTPC_RESULTCODE_TIMEDOUT) {
            free(buf);
            return NULL;
        }
    } while (res == (s32)HTTPC_RESULTCODE_DOWNLOADPENDING);

    if (R_FAILED(res) && res != HTTPC_RESULTCODE_DOWNLOADPENDING) {
        free(buf);
        return NULL;
    }

    *out_size = size;
    return buf;
}

u8 *network_get(const AppConfig *config, const char *path,
                u32 *out_size, u32 *out_status) {
    request_delay(); // Let previous request fully clean up

    char url[MAX_URL_LEN + 128];
    build_url(config, path, url, sizeof(url));

    httpcContext context;
    Result res = httpcOpenContext(&context, HTTPC_METHOD_GET, url, 0);
    if (R_FAILED(res)) return NULL;

    httpcSetSSLOpt(&context, SSLCOPT_DisableVerify);
    httpcSetKeepAlive(&context, HTTPC_KEEPALIVE_DISABLED);
    httpcAddRequestHeaderField(&context, "User-Agent", "3DSSaveSync/" APP_VERSION);
    httpcAddRequestHeaderField(&context, "X-API-Key", config->api_key);
    httpcAddRequestHeaderField(&context, "X-Console-ID", config->console_id);
    httpcAddRequestHeaderField(&context, "Connection", "close");

    res = httpcBeginRequest(&context);
    if (R_FAILED(res)) {
        httpcCancelConnection(&context);
        httpcCloseContext(&context);
        return NULL;
    }

    res = httpcGetResponseStatusCodeTimeout(&context, out_status, TIMEOUT_RESPONSE);
    if (R_FAILED(res)) {
        httpcCancelConnection(&context);
        httpcCloseContext(&context);
        return NULL;
    }

    u8 *body = read_response(&context, out_size);
    httpcCancelConnection(&context);
    httpcCloseContext(&context);
    return body;
}

u8 *network_post(const AppConfig *config, const char *path,
                 const u8 *body, u32 body_size,
                 u32 *out_size, u32 *out_status) {
    request_delay(); // Let previous request fully clean up

    // Reject oversized POST bodies that would overflow httpc buffer
    if (body_size > MAX_POST_SIZE) return NULL;

    char url[MAX_URL_LEN + 128];
    build_url(config, path, url, sizeof(url));

    httpcContext context;
    Result res = httpcOpenContext(&context, HTTPC_METHOD_POST, url, 0);
    if (R_FAILED(res)) return NULL;

    httpcSetSSLOpt(&context, SSLCOPT_DisableVerify);
    httpcSetKeepAlive(&context, HTTPC_KEEPALIVE_DISABLED);
    httpcAddRequestHeaderField(&context, "User-Agent", "3DSSaveSync/" APP_VERSION);
    httpcAddRequestHeaderField(&context, "X-API-Key", config->api_key);
    httpcAddRequestHeaderField(&context, "X-Console-ID", config->console_id);
    httpcAddRequestHeaderField(&context, "Connection", "close");
    httpcAddRequestHeaderField(&context, "Content-Type", "application/octet-stream");

    res = httpcAddPostDataRaw(&context, (u32 *)body, body_size);
    if (R_FAILED(res)) {
        httpcCancelConnection(&context);
        httpcCloseContext(&context);
        return NULL;
    }

    res = httpcBeginRequest(&context);
    if (R_FAILED(res)) {
        httpcCancelConnection(&context);
        httpcCloseContext(&context);
        return NULL;
    }

    res = httpcGetResponseStatusCodeTimeout(&context, out_status, TIMEOUT_RESPONSE);
    if (R_FAILED(res)) {
        httpcCancelConnection(&context);
        httpcCloseContext(&context);
        return NULL;
    }

    u8 *resp = read_response(&context, out_size);
    httpcCancelConnection(&context);
    httpcCloseContext(&context);
    return resp;
}

u8 *network_post_json(const AppConfig *config, const char *path,
                      const char *json_body,
                      u32 *out_size, u32 *out_status) {
    request_delay(); // Let previous request fully clean up

    u32 json_len = strlen(json_body);
    if (json_len > MAX_POST_SIZE) return NULL;

    char url[MAX_URL_LEN + 128];
    build_url(config, path, url, sizeof(url));

    httpcContext context;
    Result res = httpcOpenContext(&context, HTTPC_METHOD_POST, url, 0);
    if (R_FAILED(res)) return NULL;

    httpcSetSSLOpt(&context, SSLCOPT_DisableVerify);
    httpcSetKeepAlive(&context, HTTPC_KEEPALIVE_DISABLED);
    httpcAddRequestHeaderField(&context, "User-Agent", "3DSSaveSync/" APP_VERSION);
    httpcAddRequestHeaderField(&context, "X-API-Key", config->api_key);
    httpcAddRequestHeaderField(&context, "X-Console-ID", config->console_id);
    httpcAddRequestHeaderField(&context, "Connection", "close");
    httpcAddRequestHeaderField(&context, "Content-Type", "application/json");

    res = httpcAddPostDataRaw(&context, (u32 *)json_body, json_len);
    if (R_FAILED(res)) {
        httpcCancelConnection(&context);
        httpcCloseContext(&context);
        return NULL;
    }

    res = httpcBeginRequest(&context);
    if (R_FAILED(res)) {
        httpcCancelConnection(&context);
        httpcCloseContext(&context);
        return NULL;
    }

    res = httpcGetResponseStatusCodeTimeout(&context, out_status, TIMEOUT_RESPONSE);
    if (R_FAILED(res)) {
        httpcCancelConnection(&context);
        httpcCloseContext(&context);
        return NULL;
    }

    u8 *resp = read_response(&context, out_size);
    httpcCancelConnection(&context);
    httpcCloseContext(&context);
    return resp;
}
