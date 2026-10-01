#ifndef UI_H
#define UI_H

// Screen output and the shared dialogs. Both screens are 16-bit bitmap
// backgrounds (MODE_5_2D, BG3); everything is drawn into a RAM back buffer
// per screen (ui_top / ui_bottom, see views.h) and copied to VRAM by
// ui_present(). stdout goes to the activity log (ui_log.h), which is shown
// live while a task screen is up.

#include "common.h"
#include "sync.h"
#include "theme.h"
#include <stdbool.h>
#include <stddef.h>

extern Surface ui_top, ui_bottom;

// Video modes, back buffers and the stdout hook. Call first.
void ui_init(void);
void ui_present(const Surface *s);
void ui_present_rows(const Surface *s, int y, int h);

// Wait for one of `keys` (0 = any button); returns the keys pressed
int ui_wait(int keys);

// Centred message card on the bottom screen; waits for `keys` (0 = any)
int ui_message(const char *toolbar, const char *title, const char *body, UiKind kind,
               const Hint *hints, int keys);

// Task screen (bottom): status card, progress bar and the live activity log.
// begin clears the log.
void ui_task_begin(const char *title, const char *status);
void ui_task_status(const char *status, const char *detail);
// Progress bar; redraws at most ~10 times a second (always at done == total)
void ui_task_progress(const char *status, const char *detail, uint32_t done, uint32_t total);
void ui_task_hints(const Hint *hints);
// Show the result in the status card (log stays visible), wait for `keys`
int ui_task_end(UiKind kind, const char *result, const char *detail, const Hint *hints, int keys);

// Footer hint lists used by several screens
extern const Hint HINTS_ANY[];       // "A Continue", accepts any button
extern const Hint HINTS_BACK_B[];    // "B Back"
extern const Hint HINTS_EXIT[];      // "START Exit"

// Show save details screen
void ui_show_save_details(Title *title);

// Confirmation before upload/download with local vs server info
// Returns: true if user confirms (A), false if cancelled (B)
bool ui_confirm_sync(Title *title, const char *server_hash, size_t server_size, bool is_upload);

// Show smart sync decision and get user confirmation
// Returns the action to execute (may differ from decision->action for conflicts)
// Returns SYNC_UP_TO_DATE if user cancels
SyncAction ui_confirm_smart_sync(Title *title, SyncDecision *decision);

#endif
