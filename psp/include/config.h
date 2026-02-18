#ifndef CONFIG_H
#define CONFIG_H

#include "common.h"

/* Load config.txt from SYNC_STATE_DIR. Populates state->server_url, api_key, wifi_ap_index.
 * Returns true on success. error_buf is filled on failure. */
bool config_load(SyncState *state, char *error_buf, size_t error_buf_size);

/* Save config to config.txt. Returns true on success. */
bool config_save(const SyncState *state);

/* Load or generate the console ID. Stored in CONSOLE_ID_FILE. */
void config_load_console_id(SyncState *state);

/* Load per-game sync state (last_synced_hash) from STATE_FILE.
 * game_id: 9-char product code.
 * hash_out: 65-byte buffer for hex hash string.
 * Returns true if a hash was found. */
bool config_get_last_hash(const char *game_id, char *hash_out);

/* Save the last synced hash for a game. */
bool config_set_last_hash(const char *game_id, const char *hash_hex);

#endif /* CONFIG_H */
