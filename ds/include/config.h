#ifndef CONFIG_H
#define CONFIG_H

#include "common.h"

// Load config from file
bool config_load(SyncState *state, char *error, size_t error_size);

// Save config to file
bool config_save(const SyncState *state);

// Folder holding the config file in use ("sd:/dssync" or "fat:/dssync")
const char *config_dir(void);

// Edit a text field using D-pad editor
// Returns true if user confirms changes
bool config_edit_field(const char *hint, char *buffer, int max_len);

#endif
