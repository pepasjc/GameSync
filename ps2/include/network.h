#ifndef PS2SYNC_NETWORK_H
#define PS2SYNC_NETWORK_H

#include "common.h"

/*
 * PS2 networking lifecycle:
 *
 *   1. main.c loads IOP modules (dev9, netman, smap, ps2ip, ps2ips)
 *      from the embedded IRX blobs.
 *   2. network_init()        — bring NetMan + ps2ip up, static IP or DHCP.
 *   3. network_check_server  — GET /api/v1/status to verify URL+key.
 *   4. ROM/save fetches via http.c (sockets).
 *   5. network_shutdown()    — clean up before exit.
 *
 * No retry policy lives here; callers handle that.
 */

int  network_init(SyncState *state);
void network_shutdown(void);
bool network_is_ready(const SyncState *state);
bool network_check_server(const SyncState *state);

/* Streaming progress callback for ROM downloads.  Same shape as the
 * PSP client.  Returning non-zero aborts the transfer. */
typedef int (*NetProgress64Fn)(uint64_t downloaded, uint64_t total);
void network_set_progress64_cb(NetProgress64Fn cb);
typedef int (*NetStreamBeginFn)(uint64_t content_length, void *user);
typedef int (*NetWriteFn)(const void *data, uint32_t len, void *user);

/* Catalog fetch — GET /api/v1/roms?system=PS2&offset=X&limit=Y */
int network_fetch_rom_catalog(const SyncState *state,
                              const char *system_code,
                              int offset, int limit,
                              char *out, uint32_t out_size,
                              int *status_out);

/* Plain GET of an /api/v1 path into a fixed buffer, NUL-terminated.
 * Returns the body length or a negative error; -100 when the network is
 * not up.  Used for /roms/fingerprints and /roms/scan. */
int network_get(const SyncState *state, const char *path,
                char *out, uint32_t out_size, int *status_out);

/* Resumable streaming download.  Returns:
 *    0 ok
 *    1 paused (callback returned non-zero)
 *   -1 network error
 *   -2 fs error
 *   -3 HTTP non-2xx */
int network_download_rom_resumable(const SyncState *state,
                                   const char *rom_id,
                                   const char *extract_fmt,
                                   const char *target_path,
                                   uint64_t start_offset,
                                   uint64_t *total_out);

/* Same ROM endpoint, but body bytes are delivered to caller-owned storage
 * instead of a filesystem path. Used by APA/HDLoader installs. */
int network_download_rom_to_sink(const SyncState *state,
                                 const char *rom_id,
                                 const char *extract_fmt,
                                 NetStreamBeginFn begin,
                                 NetWriteFn writer,
                                 void *user,
                                 uint64_t start_offset,
                                 uint64_t *total_out);

#endif /* PS2SYNC_NETWORK_H */
