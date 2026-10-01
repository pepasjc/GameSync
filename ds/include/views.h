#ifndef VIEWS_H
#define VIEWS_H

// The client's screens, drawn from plain data into a Surface. No input and
// no libnds here: the loops in main.c / ui.c / catalog.c / ra.c / config.c
// draw a view, present it and wait for keys. tests/render_screens.c renders
// every view on a PC for review.

#include "common.h"
#include "sync.h"
#include "theme.h"
#include "catalog_data.h"

// ---------------------------------------------------------------------------
// Main screen
// ---------------------------------------------------------------------------

#define SAVE_ROWS 11          // save list rows on the bottom screen
#define MENU_ITEMS 9          // settings / tools entries

// Top screen: details of the selected save, or the settings/tools menu when
// it has the focus (L)
void view_main_top(Surface *s, const SyncState *state, int selected, bool menu_focused,
                   int menu_selected, bool has_wifi);
// Bottom screen: the save list
void view_save_list(Surface *s, const SyncState *state, int selected, int scroll,
                    bool focused, bool has_wifi);
// Y: everything about one save
void view_save_details(Surface *s, const Title *title);

// ---------------------------------------------------------------------------
// Sync
// ---------------------------------------------------------------------------

typedef struct {
    const char *heading;          // screen title
    const char *game;
    bool has_local;
    uint32_t local_size;
    char local_hash[17];          // first 16 hex digits, "" = not calculated
    bool has_server;
    uint32_t server_size;
    char server_hash[17];
    char last_hash[17];           // "" = never synced
    SyncAction action;            // highlights the side that changed
} CompareView;

// Top screen: local vs server
void view_sync_compare(Surface *s, const CompareView *v);

// Bottom screen: the suggested action and its buttons
void view_sync_action(Surface *s, const char *game, SyncAction action, bool has_last_synced);
// Bottom screen: R (manual upload) / confirm-before-transfer
void view_transfer_confirm(Surface *s, const char *game, bool is_upload, bool has_server,
                           bool hashes_match);

// Summary card with coloured count rows (scan all, RA update, ...)
typedef struct {
    const char *label;
    const char *value;
    Color color;
} SummaryRow;
void view_summary(Surface *s, const char *toolbar, const char *title, UiKind kind,
                  const SummaryRow *rows, int nrows, const char *note, const Hint *hints);

// ---------------------------------------------------------------------------
// Task screen: status, progress bar and the activity log
// ---------------------------------------------------------------------------

#define TASK_LOG_X 6
#define TASK_LOG_Y 64
#define TASK_LOG_W 244
#define TASK_LOG_H 108

typedef struct {
    const char *title;            // toolbar
    const char *status;           // first line of the status card
    const char *detail;           // second line ("" = none)
    uint32_t done, total;         // progress bar when total > 0
    UiKind kind;                  // colour of the status card accent
    bool finished;                // result shown: status is the result text
    const Hint *hints;            // footer
} TaskView;

void view_task(Surface *s, const TaskView *v);
void view_task_log(Surface *s);   // redraw just the log panel

// ---------------------------------------------------------------------------
// Boot splash (top screen)
// ---------------------------------------------------------------------------

void view_splash(Surface *s, const char *status);

// ---------------------------------------------------------------------------
// Text field editor (D-pad)
// ---------------------------------------------------------------------------

void view_editor(Surface *s, const char *hint, const char *text, int len, int cursor,
                 const char *charset, int charset_len);

// ---------------------------------------------------------------------------
// RetroAchievements
// ---------------------------------------------------------------------------

void view_ra_menu(Surface *s, const char *root, bool has_wifi);

// ---------------------------------------------------------------------------
// Game catalog
// ---------------------------------------------------------------------------

#define CAT_ROWS 10              // list rows on the bottom screen

typedef struct {
    const CatEntry *entry;       // NULL = not loaded yet ("...")
    bool installed;
} CatRow;

typedef struct {
    const char *system;
    int nsystems;
    bool ra_only;
    const char *search;
    const char *error;           // "" = none
    bool filter_ignored;
    bool loading;
    int total;                   // -1 = not loaded
    int selected, scroll;
    CatRow rows[CAT_ROWS];
    int nrows;
    const CatEntry *current;     // selected entry, NULL if none
    bool current_installed;
    const char *rom_dir;
} CatalogView;

void view_catalog_details(Surface *s, const CatalogView *v);
void view_catalog_list(Surface *s, const CatalogView *v);
// Only the strip under the list ("Loading...", errors, legend)
void view_catalog_strip(Surface *s, const CatalogView *v);

void view_install_confirm(Surface *s, const CatEntry *e, const char *rom_dir, const char *target,
                          bool exists);

typedef struct {
    const char *name;
    bool started;                // false: "Connecting..."
    uint32_t done, total;
    uint32_t speed, avg;         // bytes/s
    unsigned elapsed, left;      // seconds, left = ~0u unknown
    // Result (finished)
    bool finished;
    UiKind kind;
    const char *result;          // heading
    const char *lines[5];        // up to 3, NULL-terminated
    Color line_colors[5];
} InstallView;

void view_install(Surface *s, const InstallView *v);

void view_format_time(unsigned seconds, char *out, size_t size);

#endif
