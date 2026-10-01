#ifndef PS3SYNC_UI_H
#define PS3SYNC_UI_H

#include "common.h"
#include "downloads.h"
#include "roms.h"
#include "sync.h"

/* Rows visible in every list view (Saves, ROM Catalog, Downloads); main.c
 * uses it for scrolling and Left/Right paging. */
#define UI_LIST_ROWS 14

bool ui_init(char *error_buf, size_t error_buf_size);
void ui_shutdown(void);

/* Server reachability, shown in the header of every screen. */
void ui_set_online(bool online);

/* Called from the sysutil callback in main.c */
void ui_notify_exit(void);
void ui_notify_menu_open(void);
void ui_notify_menu_close(void);
int  ui_exit_requested(void);
int  ui_menu_open(void);
void ui_clear(void);

/* Show a one-line progress/status message immediately (non-blocking). */
void ui_status(const char *fmt, ...);

/* Show a multi-line message and block until Cross (or Circle) is pressed. */
void ui_message(const char *fmt, ...);

/* Changes whenever a dialog (message, confirm, choice) has read the pad.
 * The main loop compares it to skip the frame in which the button that
 * closed the dialog is still held. */
unsigned ui_dialog_serial(void);

/* Yes / no card: Cross confirms (returns true), Circle cancels. */
bool ui_ask(const char *title, const char *body, const char *confirm_label);

/* Option list card: Up / Down choose, Cross selects (returns the index),
 * Circle closes (returns -1).  `body` may be NULL. */
int ui_choose(const char *title, const char *body,
              const char *const *options, int count, int initial);

/* Show a sync confirmation dialog.
 * Returns true if the user pressed Cross (confirm), false for Circle (cancel). */
bool ui_confirm(const TitleInfo *title, SyncAction action,
                const char *server_hash, uint32_t server_size,
                const char *server_last_sync);

/* Full-screen views.  ui_draw_message is the fatal-error screen used before
 * the main loop runs. */
void ui_draw_message(const char *title, const char *message, const char *footer);
void ui_draw_list(const SyncState *state,
                  const int *visible, int visible_count,
                  int selected, int scroll_offset,
                  const char *status_line, bool config_created,
                  bool show_server_only,
                  const char *const *filters, int filter_count, int filter_index);

/* Settings tab rows: 0 server URL, 1 API key, 2 PS3 user, 3 scan PS3,
 * 4 scan PS1, 5 show server-only saves, then the actions 6 refresh
 * catalog, 7 save and apply, 8 discard changes. */
#define UI_SETTINGS_FIELDS       9
#define UI_SETTINGS_FIRST_ACTION 6
#define UI_SETTINGS_REFRESH      6
#define UI_SETTINGS_SAVE         7
#define UI_SETTINGS_DISCARD      8

void ui_draw_config_editor(
    const char *server_url,
    const char *api_key,
    int selected_user,
    bool scan_ps3,
    bool scan_ps1,
    bool show_server_only,
    int selected_field,
    bool dirty,
    const char *status_line
);
void ui_draw_text_editor(const char *label, const char *value, int cursor_pos);

/* ----- ROM catalog + download views ----- */
void ui_draw_rom_catalog(const RomCatalog *catalog,
                         const DownloadList *downloads,
                         const char *const *systems, int system_count,
                         int system_index,
                         int selected, int scroll_offset,
                         const char *status_line,
                         const char *source_note);

void ui_draw_downloads(const DownloadList *downloads,
                       int selected, int scroll_offset,
                       const char *status_line,
                       bool active_in_progress,
                       uint64_t active_downloaded,
                       uint64_t active_total,
                       uint64_t active_bps);

#endif /* PS3SYNC_UI_H */
