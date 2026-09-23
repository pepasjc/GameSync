#ifndef GCSYNC_NETWORK_H
#define GCSYNC_NETWORK_H

#include "common.h"

/*
 * GameCube networking lifecycle (Broadband Adapter):
 *
 *   1. network_init()       — if_config() static IP or DHCP via the BBA.
 *   2. network_check_server — GET /api/v1/status to verify URL + key.
 *   3. ROM/save fetches via http.c (net_* sockets).
 *
 * No IRX, NetMan or ps2ip here — libogc's net stack drives the BBA directly.
 */

int  network_init(SyncState *state);
void network_shutdown(void);
bool network_is_ready(const SyncState *state);
bool network_check_server(const SyncState *state);

/* Streaming progress callback for ROM downloads — non-zero aborts. */
typedef int (*NetProgress64Fn)(uint64_t downloaded, uint64_t total);
void network_set_progress64_cb(NetProgress64Fn cb);

/* Catalog fetch — GET /api/v1/roms?system=GC&offset=X&limit=Y */
int network_fetch_rom_catalog(const SyncState *state,
                              const char *system_code,
                              int offset, int limit,
                              char *out, uint32_t out_size,
                              int *status_out);

/* Resumable streaming download to target_path (.part + atomic rename).
 *    0 ok / 1 paused / -1 net error / -2 fs error / -3 HTTP non-2xx */
int network_download_rom_resumable(const SyncState *state,
                                   const char *rom_id,
                                   const char *extract_fmt,
                                   const char *target_path,
                                   uint64_t start_offset,
                                   uint64_t *total_out);

#endif /* GCSYNC_NETWORK_H */
