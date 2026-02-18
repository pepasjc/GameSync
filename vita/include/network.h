#ifndef NETWORK_H
#define NETWORK_H

#include "common.h"

/* Initialize network using SceNet + SceHttp (VitaSDK).
 * Must be called once before any network operations. */
int network_init(void);
void network_cleanup(void);

/* Connect to WiFi (uses system WiFi settings).
 * Returns 0 on success. */
int network_connect(void);

bool network_is_connected(void);
bool network_check_server(const SyncState *state);

int network_get_save_info(const SyncState *state, const char *game_id,
                          char *hash_out, uint32_t *size_out);

int network_upload_save(const SyncState *state, const TitleInfo *title,
                        const uint8_t *bundle, uint32_t bundle_size);

/* Returns bytes received or negative on error. */
int network_download_save(const SyncState *state, const char *game_id,
                          uint8_t *out, uint32_t out_size);

int network_post_json(const SyncState *state, const char *path,
                      const char *json,
                      uint8_t *out, uint32_t out_size, int *out_len);

#endif
