#ifndef UI_H
#define UI_H

// Screens and dialogs of the 3DS client, drawn with citro2d (see gui.h).
//
// Every screen is redrawn each frame. Blocking work (network, AM installs)
// draws a frame through ui_progress()/ui_busy() whenever it has news; modal
// dialogs draw on the bottom screen over a dimmed "backdrop", which is
// whatever screen is current (the save list, the catalog...).

#include "common.h"
#include "gui.h"
#include "sync.h"

void ui_init(void);
void ui_exit(void);

// ---------------------------------------------------------------------------
// Backdrop
// ---------------------------------------------------------------------------

typedef void (*UiDrawFn)(void *ctx);

typedef struct {
    UiDrawFn top, bottom;
    void *ctx;
} UiBackdrop;

// Set what's drawn behind dialogs / progress; returns the previous one
UiBackdrop ui_set_backdrop(UiDrawFn top, UiDrawFn bottom, void *ctx);
void ui_restore_backdrop(UiBackdrop previous);
void ui_draw_backdrop_top(void);
void ui_draw_backdrop_bottom(void);

// ---------------------------------------------------------------------------
// Dialogs and progress
// ---------------------------------------------------------------------------

typedef enum {
    UI_TONE_ACCENT = 0,
    UI_TONE_OK,
    UI_TONE_WARN,
    UI_TONE_INFO,
    UI_TONE_ERR,
} UiTone;

u32 ui_tone_hex(UiTone tone);

// Body markup: a line starting with UI_DIM is drawn in the secondary colour,
// one starting with UI_HI in the dialog's tone colour.
#define UI_DIM "\x01"
#define UI_HI "\x02"

typedef struct {
    u32 keys;             // KEY_* mask that picks this button
    const char *button;   // glyph, see gui_button()
    const char *label;
} UiButton;

// Modal card on the bottom screen. Returns the key that closed it (one of the
// buttons' keys), 0 if the app is closing. With count == 0 any button closes it.
u32 ui_dialog(UiTone tone, const char *title, const char *body, const UiButton *buttons, int count);
// Message closed by any button
void ui_message(UiTone tone, const char *title, const char *body);
// A = yes, B = no
bool ui_confirm(UiTone tone, const char *title, const char *body, const char *yes_label);

typedef struct {
    const char *title;    // card title
    const char *name;     // what's being worked on (bold line)
    const char *status;   // line under the name
    float frac;           // 0..1; < 0 = indeterminate; > 1 = no bar
    const char *left;     // under the bar, left ("12 MB / 64 MB")
    const char *right;    // under the bar, right ("18%")
    const char *info1;    // extra lines (speed, time)
    const char *info2;
    bool cancel_hint;     // "Hold B to cancel"
    UiTone tone;
} UiProgress;

// Draw one frame: backdrop + progress card. Doesn't wait for vsync.
void ui_progress(const UiProgress *p);
// Indeterminate progress card with a message
void ui_busy(const char *title, const char *message);

// ---------------------------------------------------------------------------
// Scrolling list with an animated selection bar
// ---------------------------------------------------------------------------

typedef struct {
    float sel, scroll;
    int count;
    bool init;
} UiListAnim;

typedef void (*UiRowFn)(void *ctx, int index, float x, float y, float w, float h, bool selected);

// Draw rows [scroll, scroll+rows) of a list at (x, y). Rows sliding in/out
// overflow by up to one row: draw the header/footer bars afterwards.
void ui_list(UiListAnim *anim, float x, float y, float w, int rows, float row_h,
             int count, int selected, int scroll, UiRowFn row, void *ctx);

// Row under a touch on the bottom screen, or -1
int ui_list_touch(float y, int rows, float row_h, int count, int scroll);

// ---------------------------------------------------------------------------
// Save list (main screen)
// ---------------------------------------------------------------------------

#define VIEW_ALL  0
#define VIEW_3DS  1
#define VIEW_NDS  2

#define SAVES_ROWS 9
#define SAVES_ROW_H 21
#define SAVES_LIST_Y GUI_HEADER_H

typedef struct {
    const TitleInfo *titles;   // all titles
    const int *filtered;       // visible -> titles[] index
    int count;                 // visible count
    int selected, scroll;
    int view_mode;
    int marked;
    const char *status;
    UiTone status_tone;
    const SaveDetails *details;  // last compare of the selected title, or NULL
} SavesView;

void ui_draw_saves_top(const SavesView *v);
void ui_draw_saves_bottom(const SavesView *v);

// ---------------------------------------------------------------------------
// Save dialogs
// ---------------------------------------------------------------------------

// Save details (local vs server); returns when B (or A) is pressed
void ui_show_save_details(const TitleInfo *title, const SaveDetails *details);

// Upload / download confirmation with save details. A = true, B = false.
bool ui_confirm_sync(const TitleInfo *title, const SaveDetails *details, bool is_upload);

// Smart sync dialog: shows the comparison and the suggested action.
// Returns SYNC_ACTION_UPLOAD / SYNC_ACTION_DOWNLOAD to perform, or
// SYNC_ACTION_UP_TO_DATE for "nothing to do / cancelled". For a conflict
// R uploads, L downloads, B cancels.
SyncAction ui_confirm_smart_sync(const TitleInfo *title, const SaveDetails *details, SyncAction suggested);

// History versions; returns the chosen timestamp (caller frees) or NULL
char *ui_show_history(const TitleInfo *title, HistoryVersion *versions, int version_count);

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------

#define CONFIG_RESULT_UNCHANGED 0
#define CONFIG_RESULT_SAVED     1
#define CONFIG_RESULT_RESCAN    2
#define CONFIG_RESULT_UPDATE    3
#define CONFIG_RESULT_CATALOG   4

// Settings menu (L). Returns a CONFIG_RESULT_* code.
int ui_show_config_editor(AppConfig *config);

// Edit a text field: the system keyboard, or a D-pad editor if the keyboard
// applet can't be started. Returns true if confirmed.
bool ui_edit_text(const char *title, const char *hint, char *buffer, int max_len);

#endif // UI_H
