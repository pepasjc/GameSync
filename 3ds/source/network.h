#ifndef NETWORK_H
#define NETWORK_H

#include "common.h"

// Initialize httpc service. Call once at startup.
bool network_init(void);

// Cleanup httpc service. Call at shutdown.
void network_exit(void);

// Did the server answer the most recent request? -1 nothing sent yet,
// 0 no answer (down / unreachable), 1 answered (any HTTP status).
int network_server_state(void);

// HTTP GET - returns malloc'd response body, sets out_size and out_status.
// Returns NULL on failure. Caller must free.
u8 *network_get(const AppConfig *config, const char *path,
                u32 *out_size, u32 *out_status);

// HTTP POST with binary body - returns malloc'd response body.
// Returns NULL on failure. Caller must free.
u8 *network_post(const AppConfig *config, const char *path,
                 const u8 *body, u32 body_size,
                 u32 *out_size, u32 *out_status);

// HTTP POST with JSON body - convenience wrapper.
u8 *network_post_json(const AppConfig *config, const char *path,
                      const char *json_body,
                      u32 *out_size, u32 *out_status);

// ---------------------------------------------------------------------------
// Streaming download (ROM catalog): the body is handed to a sink chunk by
// chunk and never held in RAM as a whole.
// ---------------------------------------------------------------------------

typedef enum {
    NET_DL_OK = 0,
    NET_DL_CONNECT,     // couldn't connect / no response at all
    NET_DL_TIMEOUT,     // server never started answering within max_wait_s
    NET_DL_STATUS,      // HTTP status other than 200 (info->status, info->error)
    NET_DL_CANCELLED,   // wait or sink asked to stop
    NET_DL_SINK,        // sink failed (write error etc.)
    NET_DL_SHORT,       // connection lost before the whole body arrived
} NetDlResult;

typedef struct {
    u32 status;         // HTTP status code, 0 if none
    u64 total;          // Content-Length, 0 if unknown
    u64 received;       // body bytes passed to the sink
    char error[256];    // start of the error body for NET_DL_STATUS
} NetDlInfo;

// Called about once a second while the server prepares the response (a CIA
// conversion can take minutes). Return true to cancel.
typedef bool (*NetWaitCb)(u32 seconds, void *user);

// Called for every received chunk. Return 0 to continue, 1 to cancel,
// -1 on error.
typedef int (*NetSinkCb)(const u8 *data, u32 size, u64 done, u64 total, void *user);

#define NET_DL_CHUNK 0x10000  // 64 KB per receive

NetDlResult network_download(const AppConfig *config, const char *path,
                             u32 max_wait_s, NetWaitCb wait, NetSinkCb sink,
                             void *user, NetDlInfo *info);

#endif // NETWORK_H
