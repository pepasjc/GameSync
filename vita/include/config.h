#ifndef CONFIG_H
#define CONFIG_H

#include "common.h"

bool config_load(SyncState *state, char *error_buf, size_t error_buf_len);
bool config_save(const SyncState *state);
void config_load_console_id(SyncState *state);
bool config_get_last_hash(const char *game_id, char *hash_out);
bool config_set_last_hash(const char *game_id, const char *hash_hex);

/* Hash cache: skip rehashing a save if file_count and total_size are unchanged.
 * hash_out must be a 65-byte buffer. */
bool config_get_cached_hash(const char *game_id, int file_count, uint32_t total_size,
                            char *hash_out);
bool config_set_cached_hash(const char *game_id, int file_count, uint32_t total_size,
                            const char *hash_hex);

#endif
