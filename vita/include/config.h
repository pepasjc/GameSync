#ifndef CONFIG_H
#define CONFIG_H

#include "common.h"

bool config_load(SyncState *state, char *error_buf, size_t error_buf_len);
bool config_save(const SyncState *state);
void config_load_console_id(SyncState *state);
bool config_get_last_hash(const char *game_id, char *hash_out);
bool config_set_last_hash(const char *game_id, const char *hash_hex);

#endif
