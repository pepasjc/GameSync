#ifndef UI_H
#define UI_H

#include "common.h"
#include "sync.h"

// Initialize both screens for console output
void ui_init(void);

// Reinitialize after gfx restart (e.g., after swkbd applet)
void ui_reinit(void);

// View mode for title list filtering
#define VIEW_ALL  0
#define VIEW_3DS  1
#define VIEW_NDS  2

// Draw the title list on the top screen.
// selected = currently highlighted index, count = number of titles.
// view_mode controls the tab label in the header.
void ui_draw_title_list(const TitleInfo *titles, int count, int selected, int scroll_offset, int view_mode);

// Draw status/action bar on the bottom screen
void ui_draw_status(const char *status_line);

// Show a message on the bottom screen (clears it first)
void ui_draw_message(const char *msg);

// Lightweight progress update - overwrites line 1 without clearing screen
void ui_update_progress(const char *msg);

// Clear both screens
void ui_clear(void);

// Show save details dialog on top screen
// Returns when user presses B to close
void ui_show_save_details(const TitleInfo *title, const SaveDetails *details);

// Show sync confirmation dialog with save details
// Returns true if user confirmed (A), false if cancelled (B)
bool ui_confirm_sync(const TitleInfo *title, const SaveDetails *details, bool is_upload);

// Show smart sync dialog - suggests action based on hash comparison.
// Returns the SyncAction to perform (SYNC_ACTION_UPLOAD, SYNC_ACTION_DOWNLOAD,
// SYNC_ACTION_UP_TO_DATE, or SYNC_ACTION_CONFLICT).
// For SYNC_ACTION_CONFLICT, caller should prompt for override (R=upload, L=download).
SyncAction ui_confirm_smart_sync(const TitleInfo *title, const SaveDetails *details, SyncAction suggested);

// Show history versions and let user select one to download.
// Returns the selected timestamp string (caller must free), or NULL if cancelled.
char *ui_show_history(const TitleInfo *title, HistoryVersion *versions, int version_count);

// Config editor result codes
#define CONFIG_RESULT_UNCHANGED 0
#define CONFIG_RESULT_SAVED     1
#define CONFIG_RESULT_RESCAN    2
#define CONFIG_RESULT_UPDATE    3

// Show config editor menu on top screen
// Returns CONFIG_RESULT_* code
int ui_show_config_editor(AppConfig *config);

#endif // UI_H
