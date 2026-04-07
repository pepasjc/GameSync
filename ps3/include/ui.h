#ifndef PS3SYNC_UI_H
#define PS3SYNC_UI_H

#include "common.h"
#include "sync.h"

bool ui_init(char *error_buf, size_t error_buf_size);
void ui_shutdown(void);

/* Called from the sysutil callback in main.c */
void ui_notify_exit(void);
void ui_notify_menu_open(void);
void ui_notify_menu_close(void);
int  ui_exit_requested(void);
int  ui_menu_open(void);
void ui_clear(void);

/* Show a one-line progress/status message immediately (non-blocking). */
void ui_status(const char *fmt, ...);

/* Show a multi-line message and block until Cross is pressed. */
void ui_message(const char *fmt, ...);

/* Show a sync confirmation dialog.
 * Returns true if the user pressed Cross (confirm), false for Circle (cancel). */
bool ui_confirm(const TitleInfo *title, SyncAction action,
                const char *server_hash, uint32_t server_size,
                const char *server_last_sync);

/* Legacy draw helpers (keep for error path before ioPadInit) */
void ui_draw_message(const char *title, const char *message, const char *footer);
void ui_draw_list(const SyncState *state,
                  const int *visible, int visible_count,
                  int selected, int scroll_offset,
                  const char *status_line, bool config_created,
                  bool show_server_only);

#endif /* PS3SYNC_UI_H */
