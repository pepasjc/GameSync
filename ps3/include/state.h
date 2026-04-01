#ifndef PS3SYNC_STATE_H
#define PS3SYNC_STATE_H

#include "common.h"

bool state_get_last_hash(const char *title_id, char *hash_out);
bool state_set_last_hash(const char *title_id, const char *hash_hex);
bool state_get_cached_hash(
    const char *title_id,
    int file_count,
    uint32_t total_size,
    char *hash_out
);
bool state_set_cached_hash(
    const char *title_id,
    int file_count,
    uint32_t total_size,
    const char *hash_hex
);

#endif /* PS3SYNC_STATE_H */

