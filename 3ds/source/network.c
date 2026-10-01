#include "network.h"
#include <3ds/svc.h>

#define HTTP_BUF_SIZE 0x1000 // 4KB read chunks
#define MAX_RESPONSE  (2 * 1024 * 1024) // 2MB max response
#define MAX_POST_SIZE 0x70000 // 448KB max POST body (leave headroom in 512KB buffer)

#define TIMEOUT_RESPONSE (15ULL * 1000000000ULL) // 15s for server to respond
#define TIMEOUT_TRANSFER (30ULL * 1000000000ULL) // 30s per data chunk

// Whether the server answered the last request: -1 unknown, 0 no, 1 yes
static int server_state = -1;

static void note_server(bool answered) {
    server_state = answered ? 1 : 0;
}

int network_server_state(void) {
    return server_state;
}

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
    return network_get_timeout(config, path, out_size, out_status, 0);
}

u8 *network_get_timeout(const AppConfig *config, const char *path,
                        u32 *out_size, u32 *out_status, u32 response_timeout_s) {
    u64 response_timeout = response_timeout_s ? response_timeout_s * 1000000000ULL : TIMEOUT_RESPONSE;
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
        note_server(false);
        httpcCancelConnection(&context);
        httpcCloseContext(&context);
        return NULL;
    }

    res = httpcGetResponseStatusCodeTimeout(&context, out_status, response_timeout);
    note_server(R_SUCCEEDED(res));
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
        note_server(false);
        httpcCancelConnection(&context);
        httpcCloseContext(&context);
        return NULL;
    }

    res = httpcGetResponseStatusCodeTimeout(&context, out_status, TIMEOUT_RESPONSE);
    note_server(R_SUCCEEDED(res));
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
        note_server(false);
        httpcCancelConnection(&context);
        httpcCloseContext(&context);
        return NULL;
    }

    res = httpcGetResponseStatusCodeTimeout(&context, out_status, TIMEOUT_RESPONSE);
    note_server(R_SUCCEEDED(res));
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

// ---------------------------------------------------------------------------
// Streaming download
// ---------------------------------------------------------------------------

static void close_context(httpcContext *context) {
    httpcCancelConnection(context);
    httpcCloseContext(context);
}

NetDlResult network_download(const AppConfig *config, const char *path,
                             u32 max_wait_s, NetWaitCb wait, NetSinkCb sink,
                             void *user, NetDlInfo *info) {
    memset(info, 0, sizeof(*info));
    request_delay();

    char url[MAX_URL_LEN + 1024];
    build_url(config, path, url, sizeof(url));

    httpcContext context;
    if (R_FAILED(httpcOpenContext(&context, HTTPC_METHOD_GET, url, 0)))
        return NET_DL_CONNECT;

    httpcSetSSLOpt(&context, SSLCOPT_DisableVerify);
    httpcSetKeepAlive(&context, HTTPC_KEEPALIVE_DISABLED);
    httpcAddRequestHeaderField(&context, "User-Agent", "3DSSaveSync/" APP_VERSION);
    httpcAddRequestHeaderField(&context, "X-API-Key", config->api_key);
    httpcAddRequestHeaderField(&context, "X-Console-ID", config->console_id);
    httpcAddRequestHeaderField(&context, "Connection", "close");

    if (R_FAILED(httpcBeginRequest(&context))) {
        note_server(false);
        close_context(&context);
        return NET_DL_CONNECT;
    }

    // The server only answers once a conversion (?extract=cia) is done, so
    // wait in one-second slices and let the caller show progress / cancel.
    u32 status = 0;
    u32 waited = 0;
    while (1) {
        Result res = httpcGetResponseStatusCodeTimeout(&context, &status, 1000000000ULL);
        if (R_SUCCEEDED(res)) break;
        if (res != (Result)HTTPC_RESULTCODE_TIMEDOUT) {
            note_server(false);
            close_context(&context);
            return NET_DL_CONNECT;
        }
        waited++;
        if (wait && wait(waited, user)) {
            close_context(&context);
            return NET_DL_CANCELLED;
        }
        if (waited >= max_wait_s) {
            close_context(&context);
            return NET_DL_TIMEOUT;
        }
    }
    info->status = status;
    note_server(true);

    u8 *buf = (u8 *)malloc(NET_DL_CHUNK);
    if (!buf) {
        close_context(&context);
        return NET_DL_SINK;
    }

    if (status != 200) {
        // Keep the start of the error text (FastAPI sends plain text/JSON)
        u32 got = 0;
        download_data_timeout(&context, buf, sizeof(info->error) - 1, &got, TIMEOUT_RESPONSE);
        if (got > sizeof(info->error) - 1) got = sizeof(info->error) - 1;
        memcpy(info->error, buf, got);
        info->error[got] = '\0';
        free(buf);
        close_context(&context);
        return NET_DL_STATUS;
    }

    u32 pos = 0, content = 0;
    httpcGetDownloadSizeState(&context, &pos, &content);
    info->total = content;

    NetDlResult result = NET_DL_OK;
    Result res;
    do {
        u32 got = 0;
        res = download_data_timeout(&context, buf, NET_DL_CHUNK, &got, TIMEOUT_TRANSFER);
        if (got > 0) {
            info->received += got;
            int s = sink(buf, got, info->received, info->total, user);
            if (s != 0) {
                result = (s > 0) ? NET_DL_CANCELLED : NET_DL_SINK;
                break;
            }
        }
        if (res == (Result)HTTPC_RESULTCODE_TIMEDOUT) {
            result = NET_DL_SHORT;
            break;
        }
    } while (res == (Result)HTTPC_RESULTCODE_DOWNLOADPENDING);

    if (result == NET_DL_OK) {
        if (R_FAILED(res) && res != (Result)HTTPC_RESULTCODE_DOWNLOADPENDING)
            result = NET_DL_SHORT;
        else if (info->total && info->received < info->total)
            result = NET_DL_SHORT;
    }

    free(buf);
    close_context(&context);
    return result;
}
