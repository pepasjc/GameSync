#ifndef CONFIG_H
#define CONFIG_H

#include "common.h"

// Load config from sdmc:/3ds/3dssync/config.txt
// Returns true on success, false on failure.
// On failure, writes a human-readable error to error_out.
// Format: key=value per line (server_url=..., api_key=...)
bool config_load(AppConfig *config, char *error_out, int error_size);

// Save config to sdmc:/3ds/3dssync/config.txt
// Returns true on success, false on failure.
bool config_save(const AppConfig *config);

// Open software keyboard to edit a string field
// Returns true if user confirmed, false if cancelled
// hint: placeholder text shown when empty
// max_len: maximum characters allowed
bool config_edit_field(const char *hint, char *buffer, int max_len);

// Hash cache: skip rehashing a save if file_count and total_size are unchanged.
// hash_out must be a 65-byte buffer.
// mtime == 0 disables caching (used for archive saves where mtime is unavailable).
bool config_get_cached_hash(const char *title_id_hex, int file_count, u32 total_size,
                            u32 mtime, char *hash_out);
bool config_set_cached_hash(const char *title_id_hex, int file_count, u32 total_size,
                            u32 mtime, const char *hash_hex);

#endif // CONFIG_H
