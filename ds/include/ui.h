#ifndef UI_H
#define UI_H

#include "common.h"
#include "sync.h"
#include <stdbool.h>
#include <stddef.h>

// Show save details screen
void ui_show_save_details(Title *title);

// Show confirmation dialog before upload/download with local vs server info
// Returns: true if user confirms (A), false if cancelled (B)
bool ui_confirm_sync(Title *title, const char *server_hash, size_t server_size, bool is_upload);

// Show smart sync decision and get user confirmation
// Returns the action to execute (may differ from decision->action for conflicts)
// Returns SYNC_UP_TO_DATE if user cancels
SyncAction ui_confirm_smart_sync(Title *title, SyncDecision *decision);

// Entries in the config menu (see ui_draw_config)
#define UI_CONFIG_ITEMS 9

// Draw config menu on current console
void ui_draw_config(const SyncState *state, int selected, bool focused, bool has_wifi);

#endif
