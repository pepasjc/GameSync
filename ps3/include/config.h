#ifndef PS3SYNC_CONFIG_H
#define PS3SYNC_CONFIG_H

#include "common.h"

bool config_load(
    SyncState *state,
    bool *created_out,
    char *error_buf,
    size_t error_buf_size
);
bool config_save(const SyncState *state);
void config_load_console_id(SyncState *state);

#endif /* PS3SYNC_CONFIG_H */
