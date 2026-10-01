/*
 * GameSync PS3 — screens.
 *
 * Every view shares one layout on a logical 1280x720 canvas (see gui.h):
 *
 *   header   logo, "GameSync • <view>", view tabs, version, server status
 *   list     rounded panel with a toolbar, selection bar and scrollbar
 *   detail   panel describing the selected row
 *   banner   last status message
 *   footer   PlayStation button hints
 *
 * Dialogs (messages, confirmations, progress) are cards drawn over a dimmed
 * copy of the last full screen.
 */

#include "ui.h"
#include "gui.h"
#include "sync.h"
#include "roms.h"
#include "downloads.h"
#include "catalog_cache.h"

#include <SDL/SDL.h>
#include <io/pad.h>
#include <sysutil/sysutil.h>

#include <stdbool.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

static char g_status_line[256];
static volatile int g_ui_exit = 0;   /* set by sysutil EXIT_GAME */
static volatile int g_xmb_open = 0;  /* set by sysutil MENU_OPEN/CLOSE */
static bool g_online = false;
static bool g_ready = false;
/* Bumped every time a dialog takes input, so the main loop can ignore the
 * button that closed it (see ui_dialog_serial). */
static unsigned g_dialog_serial = 0;

unsigned ui_dialog_serial(void) { return g_dialog_serial; }

void ui_notify_exit(void)       { g_ui_exit  = 1; }
void ui_notify_menu_open(void)  { g_xmb_open = 1; }
void ui_notify_menu_close(void) { g_xmb_open = 0; }
int  ui_exit_requested(void)    { return g_ui_exit; }
int  ui_menu_open(void)         { return g_xmb_open; }
void ui_set_online(bool online) { g_online = online; }

#define MAX_PADS_UI 7

/* ---- Layout (logical 1280x720) ---- */
#define LIST_X      GUI_MARGIN_X
#define LIST_W      752
#define PANEL_Y     GUI_CONTENT_Y
#define PANEL_H     512
#define DETAIL_X    (LIST_X + LIST_W + 12)
#define DETAIL_W    (GUI_W - GUI_MARGIN_X - DETAIL_X)
#define BANNER_Y    (PANEL_Y + PANEL_H + 10)
#define BANNER_H    38
#define TOOLBAR_Y   (PANEL_Y + 12)
#define TOOLBAR_H   30
#define ROWS_Y      (PANEL_Y + 54)
#define ROW_H       32
#define TAG_H       22
#define STATUS_TAG_W 112
#define DL_TAG_W     132

static const char *const k_view_names[APP_VIEW_COUNT] = {
    "Saves", "ROM Catalog", "Downloads", "Settings"
};

/* ------------------------------------------------------------------ */
/* Small helpers                                                       */
/* ------------------------------------------------------------------ */

static void format_size(uint64_t bytes, char *out, size_t out_size) {
    if (bytes >= (1ULL << 30)) {
        snprintf(out, out_size, "%.2f GiB", (double)bytes / (double)(1ULL << 30));
    } else if (bytes >= (1ULL << 20)) {
        snprintf(out, out_size, "%.1f MiB", (double)bytes / (double)(1ULL << 20));
    } else if (bytes >= (1ULL << 10)) {
        snprintf(out, out_size, "%.0f KiB", (double)bytes / (double)(1ULL << 10));
    } else {
        snprintf(out, out_size, "%llu B", (unsigned long long)bytes);
    }
}

/* ETA: bytes remaining / bytes-per-second, as "12m34s", "1h05m" or "--". */
static void format_eta(uint64_t remaining, uint64_t bps, char *out, size_t out_size) {
    if (bps == 0 || remaining == 0) {
        snprintf(out, out_size, "--");
        return;
    }
    uint64_t secs = remaining / bps;
    if (secs >= 3600) {
        snprintf(out, out_size, "%lluh%02llum",
                 (unsigned long long)(secs / 3600), (unsigned long long)((secs % 3600) / 60));
    } else if (secs >= 60) {
        snprintf(out, out_size, "%llum%02llus",
                 (unsigned long long)(secs / 60), (unsigned long long)(secs % 60));
    } else {
        snprintf(out, out_size, "%llus", (unsigned long long)secs);
    }
}

static void format_bps(uint64_t bps, char *out, size_t out_size) {
    if (bps == 0) { snprintf(out, out_size, "--"); return; }
    if (bps >= (1ULL << 20)) {
        snprintf(out, out_size, "%.2f MiB/s", (double)bps / (double)(1ULL << 20));
    } else if (bps >= (1ULL << 10)) {
        snprintf(out, out_size, "%.1f KiB/s", (double)bps / (double)(1ULL << 10));
    } else {
        snprintf(out, out_size, "%llu B/s", (unsigned long long)bps);
    }
}

static int percent_of(uint64_t off, uint64_t tot) {
    if (tot == 0) return 0;
    uint64_t p = (off * 100ULL) / tot;
    return p > 100 ? 100 : (int)p;
}

static void hash_hex(const uint8_t hash[32], char out[65]) {
    static const char hex_chars[] = "0123456789abcdef";
    for (int j = 0; j < 32; j++) {
        out[j * 2]     = hex_chars[(hash[j] >> 4) & 0x0F];
        out[j * 2 + 1] = hex_chars[hash[j] & 0x0F];
    }
    out[64] = '\0';
}

static const char *basename_of(const char *path) {
    const char *slash = path ? strrchr(path, '/') : NULL;
    return slash ? slash + 1 : (path ? path : "");
}

/* Pill of fixed width with its label centred. */
static void tag_fixed(int x, int y, int w, uint32_t color, const char *label) {
    gui_rrect(x, y, w, TAG_H, TAG_H / 2, gui_mix(color, HEX_BG, 0.72f));
    gui_text_mid(x + w / 2, y, TAG_H, GUI_F_SMALL, color, GUI_CENTER, w - 8, label);
}

/* Label / value line in a detail panel; returns the next y. */
static int kv_row(int x, int y, int w, const char *label, const char *value, uint32_t value_color) {
    gui_text(x, y + 2, GUI_F_SMALL, HEX_DIM, GUI_LEFT, label);
    gui_text_fit(x + 112, y, GUI_F_BODY, value_color, GUI_LEFT, w - 112, value);
    return y + 28;
}

/* Wrapped (up to `lines`) value in the small font; returns the next y. */
static int kv_wrap(int x, int y, int w, const char *label, const char *value,
                   uint32_t value_color, int lines) {
    gui_text(x, y + 2, GUI_F_SMALL, HEX_DIM, GUI_LEFT, label);
    int n = gui_text_wrap(x + 112, y + 2, GUI_F_SMALL, value_color, w - 112, lines, value);
    if (n < 1) n = 1;
    int lh = gui_line_h(GUI_F_SMALL);
    return y + 6 + n * lh + 4;
}

static void begin_view(AppView view) {
    gui_clear();
    int x = gui_header(k_view_names[view]);
    int tabs_x = x + 40;
    if (tabs_x < 430) tabs_x = 430;
    int cy = GUI_HEADER_Y + GUI_HEADER_H / 2;
    /* L1 / R1 cycle these tabs (wrapping). */
    int bw = gui_button(tabs_x, cy, "L1");
    int end = gui_tabs(tabs_x + bw + 8, GUI_HEADER_Y + 8, GUI_HEADER_H - 16,
                       k_view_names, APP_VIEW_COUNT, (int)view);
    gui_button(end + 8, cy, "R1");
    gui_header_status(g_online);
}

static void begin_screen(const char *section) {
    gui_clear();
    gui_header(section);
    gui_header_status(g_online);
}

/* SELECT + segmented chips for a view's internal sub-tabs, drawn in the
 * list toolbar.  Returns the right edge. */
static int draw_subtabs(int x, const char *const *labels, int count, int active) {
    int cy = TOOLBAR_Y + TOOLBAR_H / 2;
    x += gui_button(x, cy, "SELECT") + 8;
    return gui_tabs(x, TOOLBAR_Y + 1, TOOLBAR_H - 2, labels, count, active);
}

static uint32_t banner_tone(const char *s) {
    if (!s) return HEX_ACCENT;
    if (strstr(s, "fail") || strstr(s, "Fail") || strstr(s, "error") || strstr(s, "Error"))
        return HEX_ERR;
    if (strstr(s, "Offline") || strstr(s, "offline") || strstr(s, "conflict"))
        return HEX_WARN;
    return HEX_ACCENT;
}

static void draw_banner(const char *text, const char *right) {
    const char *t = (text && text[0]) ? text : "Ready.";
    int right_w = 0;
    gui_banner(LIST_X, BANNER_Y, GUI_W - 2 * GUI_MARGIN_X, BANNER_H, banner_tone(t), NULL);
    if (right && right[0]) {
        right_w = gui_text_w(GUI_F_SMALL, right);
        if (right_w > 420) right_w = 420;
        gui_text_mid(GUI_W - GUI_MARGIN_X - 16 - right_w, BANNER_Y, BANNER_H, GUI_F_SMALL,
                     HEX_DIM, GUI_LEFT, 420, right);
    }
    gui_text_mid(LIST_X + 20, BANNER_Y, BANNER_H, GUI_F_BODY, HEX_TEXT, GUI_LEFT,
                 GUI_W - 2 * GUI_MARGIN_X - 56 - right_w, t);
}

/* Selection bar for list row `row` (0-based on screen). */
static int row_y(int row) { return ROWS_Y + row * ROW_H; }

static void draw_row_bar(int row) {
    gui_rrect(LIST_X + 10, row_y(row), LIST_W - 30, ROW_H - 2, 8, HEX_ACCENT);
}

static void draw_empty(const char *title, const char *hint) {
    int cx = LIST_X + LIST_W / 2;
    int cy = PANEL_Y + PANEL_H / 2 - 30;
    gui_circle(cx, cy - 28, 18, HEX_PANEL_HI);
    gui_rrect(cx - 7, cy - 35, 14, 14, 3, HEX_MUTED);
    gui_text(cx, cy, GUI_F_TITLE, HEX_TEXT, GUI_CENTER, title);
    if (hint) {
        char lines[3][GUI_WRAP_LINE];
        int n = gui_wrap(hint, GUI_F_SMALL, LIST_W - 120, lines, 3);
        for (int i = 0; i < n; i++)
            gui_text(cx, cy + 36 + i * gui_line_h(GUI_F_SMALL), GUI_F_SMALL, HEX_DIM,
                     GUI_CENTER, lines[i]);
    }
}

static void finish_view(void) {
    gui_present(true);
}

/* ------------------------------------------------------------------ */
/* Init / shutdown                                                     */
/* ------------------------------------------------------------------ */

bool ui_init(char *error_buf, size_t error_buf_size) {
    if (!gui_init(error_buf, error_buf_size)) return false;
    g_ready = true;
    /* Boot backdrop so early progress cards have something behind them. */
    begin_screen("Starting");
    gui_present(true);
    return true;
}

void ui_shutdown(void) {
    g_ready = false;
    gui_shutdown();
}

void ui_clear(void) {
    if (!g_ready) return;
    gui_clear();
}

/* ------------------------------------------------------------------ */
/* Saves view                                                          */
/* ------------------------------------------------------------------ */

static const char *kind_label(const TitleInfo *title) {
    switch (title->kind) {
        case SAVE_KIND_PS3:     return "PS3";
        case SAVE_KIND_PS1_VM1: return "PS1";
        case SAVE_KIND_PS1:     return "PS1";
        default:                return "???";
    }
}

static uint32_t kind_color(const TitleInfo *title) {
    return title->kind == SAVE_KIND_PS3 ? HEX_PS3 : HEX_PS1;
}

static void title_status_style(TitleStatus st, const char **label, uint32_t *color,
                               const char **long_label) {
    switch (st) {
        case TITLE_STATUS_LOCAL_ONLY:
            *label = "LOCAL";    *color = HEX_WARN; *long_label = "Only on this PS3"; break;
        case TITLE_STATUS_SERVER_ONLY:
            *label = "SERVER";   *color = HEX_INFO; *long_label = "Only on the server"; break;
        case TITLE_STATUS_SYNCED:
            *label = "SYNCED";   *color = HEX_OK;   *long_label = "Up to date"; break;
        case TITLE_STATUS_UPLOAD:
            *label = "UPLOAD";   *color = HEX_WARN; *long_label = "Changed here - upload"; break;
        case TITLE_STATUS_DOWNLOAD:
            *label = "DOWNLOAD"; *color = HEX_INFO; *long_label = "Newer on server - download"; break;
        case TITLE_STATUS_CONFLICT:
            *label = "CONFLICT"; *color = HEX_ERR;  *long_label = "Both sides changed"; break;
        default:
            *label = "UNCHECKED"; *color = HEX_DIM; *long_label = "Not compared yet"; break;
    }
}

static void draw_save_detail(const TitleInfo *title) {
    int x = DETAIL_X + 22;
    int w = DETAIL_W - 44;
    int y = PANEL_Y + 18;
    const char *label, *long_label;
    uint32_t color;
    char buf[160];

    gui_panel(DETAIL_X, PANEL_Y, DETAIL_W, PANEL_H);

    int n = gui_text_wrap(x, y, GUI_F_HUGE, HEX_TEXT, w, 2,
                          title->name[0] ? title->name : title->game_code);
    y += n * gui_line_h(GUI_F_HUGE) + 8;

    title_status_style(title->status, &label, &color, &long_label);
    int tx = x;
    tx += gui_tag(tx, y, TAG_H, kind_color(title), kind_label(title)) + 6;
    tx += gui_tag(tx, y, TAG_H, color, label) + 6;
    if (title->ps1_shared_card) {
        snprintf(buf, sizeof(buf), "Card slot %d", title->ps1_slot_index + 1);
        gui_tag(tx, y, TAG_H, HEX_DIM, buf);
    }
    y += TAG_H + 14;

    y = kv_row(x, y, w, "Code", title->game_code, HEX_TEXT);
    y = kv_row(x, y, w, "Status", long_label, color);
    if (title->server_only) {
        y = kv_row(x, y, w, "Local", "Not on this PS3", HEX_DIM);
    } else {
        char size_buf[32];
        format_size(title->total_size, size_buf, sizeof(size_buf));
        snprintf(buf, sizeof(buf), "%s  -  %d file%s", size_buf, title->file_count,
                 title->file_count == 1 ? "" : "s");
        y = kv_row(x, y, w, "Size", buf, HEX_TEXT);
    }
    y = kv_wrap(x, y, w, "Location",
                title->server_only ? "(not on device)" : title->local_path,
                title->server_only ? HEX_DIM : HEX_TEXT, 2);

    gui_rect(x, y + 2, w, 1, HEX_LINE);
    y += 12;

    /* Hashes: a label line (with the match verdict on the right), then the
     * 64 hex digits as two lines of 32. */
    int slh = gui_line_h(GUI_F_SMALL) + 3;
    char local_hex[65];
    bool have_server = title->on_server && title->server_meta_loaded && title->server_hash[0];
    if (title->hash_calculated) hash_hex(title->hash, local_hex);

    gui_text(x, y, GUI_F_SMALL, HEX_DIM, GUI_LEFT, "Local hash");
    if (title->hash_calculated) {
        gui_textf(x, y + slh, GUI_F_SMALL, HEX_TEXT, GUI_LEFT, "%.32s", local_hex);
        gui_textf(x, y + 2 * slh, GUI_F_SMALL, HEX_TEXT, GUI_LEFT, "%.32s", local_hex + 32);
    } else {
        gui_text(x, y + slh, GUI_F_SMALL, HEX_MUTED, GUI_LEFT,
                 title->server_only ? "No local save" : "Not computed yet - Triangle > Refresh hash");
    }
    y += 3 * slh + 12;

    gui_text(x, y, GUI_F_SMALL, HEX_DIM, GUI_LEFT, "Server hash");
    if (title->hash_calculated && have_server) {
        bool same = strcmp(local_hex, title->server_hash) == 0;
        const char *verdict = same ? "Hashes match" : "Hashes differ";
        gui_tag(x + w - gui_pill_w(TAG_H, GUI_F_SMALL, verdict), y - 2, TAG_H,
                same ? HEX_OK : HEX_WARN, verdict);
    }
    if (!title->on_server) {
        gui_text(x, y + slh, GUI_F_SMALL, HEX_MUTED, GUI_LEFT, "Not on server");
    } else if (!title->server_meta_loaded) {
        gui_text(x, y + slh, GUI_F_SMALL, HEX_MUTED, GUI_LEFT, "Loading...");
    } else if (title->server_hash[0]) {
        gui_textf(x, y + slh, GUI_F_SMALL, HEX_TEXT, GUI_LEFT, "%.32s", title->server_hash);
        if (strlen(title->server_hash) > 32)
            gui_textf(x, y + 2 * slh, GUI_F_SMALL, HEX_TEXT, GUI_LEFT, "%.32s",
                      title->server_hash + 32);
    } else {
        gui_text(x, y + slh, GUI_F_SMALL, HEX_MUTED, GUI_LEFT, "Unavailable");
    }

    /* Secondary actions pinned to the bottom of the panel. */
    static const GuiHint more1[] = { { "TRIANGLE", "Upload, download, compare, rehash" } };
    static const GuiHint more2[] = { { "SQUARE", "Sync all saves" } };
    int by = PANEL_Y + PANEL_H - 70;
    gui_rect(x, by - 8, w, 1, HEX_LINE);
    gui_hints(x, by + 12, more1, 1, 22);
    gui_hints(x, by + 42, more2, 1, 22);
}

void ui_draw_list(
    const SyncState *state,
    const int *visible,
    int visible_count,
    int selected,
    int scroll_offset,
    const char *status_line,
    bool config_created,
    bool show_server_only,
    const char *const *filters,
    int filter_count,
    int filter_index
) {
    char buf[300];
    if (!g_ready || g_xmb_open) return;

    begin_view(APP_VIEW_SAVES);
    gui_panel(LIST_X, PANEL_Y, LIST_W, PANEL_H);

    /* Toolbar: user, filter, count */
    int tx = LIST_X + 16;
    int cy = TOOLBAR_Y + TOOLBAR_H / 2;
    tx = draw_subtabs(tx, filters, filter_count, filter_index) + 16;
    if (state->selected_user > 0) snprintf(buf, sizeof(buf), "User %08d", state->selected_user);
    else                          snprintf(buf, sizeof(buf), "User auto");
    tx += gui_text_mid(tx, TOOLBAR_Y, TOOLBAR_H, GUI_F_SMALL, HEX_TEXT, GUI_LEFT, 0, buf) + 12;
    if (!show_server_only)
        tx += gui_tag(tx, cy - TAG_H / 2, TAG_H, HEX_DIM, "Server-only hidden") + 8;
    if (config_created)
        gui_tag(tx, cy - TAG_H / 2, TAG_H, HEX_WARN, "New config");
    snprintf(buf, sizeof(buf), "%d of %d", visible_count, state->num_titles);
    gui_text_mid(LIST_X + LIST_W - 22, TOOLBAR_Y, TOOLBAR_H, GUI_F_SMALL, HEX_DIM,
                 GUI_RIGHT, 0, buf);
    gui_rect(LIST_X + 12, ROWS_Y - 10, LIST_W - 24, 1, HEX_LINE);

    if (visible_count == 0) {
        draw_empty("No saves found",
                   "Press SELECT to change the PS3 / PS1 filter, check the Settings tab "
                   "(L1 / R1), or rescan from the Triangle menu.");
        gui_panel(DETAIL_X, PANEL_Y, DETAIL_W, PANEL_H);
        gui_text_wrap(DETAIL_X + 22, PANEL_Y + 22, GUI_F_BODY, HEX_DIM, DETAIL_W - 44, 4,
                      "PS3 saves are read from the selected user's savedata folder; "
                      "PS1 cards from the memory card images.");
    } else {
        int end = scroll_offset + UI_LIST_ROWS;
        if (end > visible_count) end = visible_count;
        for (int i = scroll_offset; i < end; i++) {
            const TitleInfo *title = &state->titles[visible[i]];
            int r = i - scroll_offset;
            int y = row_y(r);
            bool sel = (i == selected);
            const char *label, *long_label;
            uint32_t color;

            if (sel) draw_row_bar(r);
            title_status_style(title->status, &label, &color, &long_label);
            int ty = y + (ROW_H - 2 - TAG_H) / 2;
            tag_fixed(LIST_X + 20, ty, STATUS_TAG_W, color, label);
            tag_fixed(LIST_X + 20 + STATUS_TAG_W + 8, ty, 46, kind_color(title), kind_label(title));

            int name_x = LIST_X + 20 + STATUS_TAG_W + 8 + 46 + 12;
            int code_w = gui_text_w(GUI_F_SMALL, title->game_code);
            gui_text_mid(LIST_X + LIST_W - 34, y, ROW_H - 2, GUI_F_SMALL,
                         sel ? HEX_INK : HEX_DIM, GUI_RIGHT, 0, title->game_code);
            gui_text_mid(name_x, y, ROW_H - 2, GUI_F_BODY,
                         sel ? HEX_INK : (title->server_only ? HEX_DIM : HEX_TEXT),
                         GUI_LEFT, LIST_X + LIST_W - 46 - code_w - name_x,
                         title->name[0] ? title->name : title->game_code);
        }
        gui_scrollbar(LIST_X + LIST_W - 14, ROWS_Y, UI_LIST_ROWS * ROW_H - 2,
                      scroll_offset, UI_LIST_ROWS, visible_count);

        if (selected >= 0 && selected < visible_count)
            draw_save_detail(&state->titles[visible[selected]]);
        else
            gui_panel(DETAIL_X, PANEL_Y, DETAIL_W, PANEL_H);
    }

    snprintf(buf, sizeof(buf), "Server: %s", state->server_url);
    draw_banner(status_line, buf);

    static const GuiHint hints[] = {
        { "CROSS", "Sync" }, { "SQUARE", "Sync all" }, { "TRIANGLE", "Details / actions" },
        { "SELECT", "Filter" }, { "LR", "Page" }, { "L1/R1", "Tabs" }, { "START", "Exit" },
    };
    gui_footer(hints, (int)(sizeof(hints) / sizeof(hints[0])));
    finish_view();
}

/* ------------------------------------------------------------------ */
/* Settings                                                            */
/* ------------------------------------------------------------------ */

static void mask_key(const char *key, char *out, size_t out_size) {
    size_t len = key ? strlen(key) : 0;
    if (len == 0) { snprintf(out, out_size, "(not set)"); return; }
    if (len <= 4) { snprintf(out, out_size, "****"); return; }
    snprintf(out, out_size, "%.4s********", key);
}

static void draw_switch(int x_right, int cy, bool on) {
    int w = 48, h = 24;
    int x = x_right - w;
    gui_rrect(x, cy - h / 2, w, h, h / 2, on ? HEX_ACCENT : HEX_LINE);
    gui_circle(on ? x + w - h / 2 : x + h / 2, cy, h / 2 - 4, on ? HEX_INK : HEX_DIM);
}

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
) {
    static const char *labels[UI_SETTINGS_FIELDS] = {
        "Server URL", "API key", "PS3 user", "Scan PS3 saves", "Scan PS1 cards",
        "Show server-only saves", "Refresh catalog", "Save and apply", "Discard changes"
    };
    static const char *help[UI_SETTINGS_FIELDS] = {
        "Address of your GameSync server, e.g. http://192.168.1.100:8000",
        "The server's SYNC_API_KEY. Sent as the X-API-Key header on every request.",
        "Which PS3 user's saves to scan. Auto picks the first user that has save data. "
        "Left / Right change it.",
        "Include PS3 HDD save folders (dev_hdd0/home/<user>/savedata).",
        "Include PS1 memory card images (.VM1) and the saves inside them.",
        "List saves that exist only on the server, so they can be downloaded here.",
        "Ask the server to rescan its ROM folder, throw away the catalog cached on "
        "this PS3 and download every system again. Runs immediately.",
        "Write config.txt, rescan the saves and reconnect to the server.",
        "Throw away the changes made here and reload the saved settings.",
    };
    char user_buf[32], key_buf[64];
    if (!g_ready || g_xmb_open) return;

    /* The editor loop calls this ~20 times a second; only repaint when a
     * value changed or something else (a dialog, the text editor) drew. */
    static unsigned last_frame = 0;
    static char last_sig[512];
    char sig[512];
    snprintf(sig, sizeof(sig), "%s|%s|%d|%d|%d|%d|%d|%d|%d|%.120s", server_url, api_key,
             selected_user, scan_ps3, scan_ps1, show_server_only, selected_field, dirty,
             g_online, status_line ? status_line : "");
    if (last_frame != 0 && last_frame == gui_frame_id() && strcmp(sig, last_sig) == 0) return;

    if (selected_user <= 0) snprintf(user_buf, sizeof(user_buf), "Auto");
    else                    snprintf(user_buf, sizeof(user_buf), "%08d", selected_user);
    mask_key(api_key, key_buf, sizeof(key_buf));

    begin_view(APP_VIEW_SETTINGS);
    gui_panel(LIST_X, PANEL_Y, LIST_W, PANEL_H);
    gui_text_mid(LIST_X + 22, TOOLBAR_Y, TOOLBAR_H, GUI_F_BOLD, HEX_TEXT, GUI_LEFT, 0,
                 "Settings");
    gui_tag(LIST_X + LIST_W - 22 - gui_pill_w(TAG_H, GUI_F_SMALL,
                                               dirty ? "Unsaved changes" : "Saved"),
            TOOLBAR_Y + (TOOLBAR_H - TAG_H) / 2, TAG_H, dirty ? HEX_WARN : HEX_OK,
            dirty ? "Unsaved changes" : "Saved");
    gui_rect(LIST_X + 12, ROWS_Y - 10, LIST_W - 24, 1, HEX_LINE);

    const int rh = 46;
    for (int i = 0; i < UI_SETTINGS_FIELDS; i++) {
        int y = ROWS_Y + i * rh + (i >= UI_SETTINGS_FIRST_ACTION ? 18 : 0);
        bool sel = (i == selected_field);
        if (i == UI_SETTINGS_FIRST_ACTION) gui_rect(LIST_X + 20, y - 12, LIST_W - 40, 1, HEX_LINE);
        if (sel) gui_rrect(LIST_X + 10, y, LIST_W - 20, rh - 6, 9, HEX_ACCENT);

        uint32_t fg = sel ? HEX_INK : (i >= UI_SETTINGS_FIRST_ACTION ? HEX_ACCENT2 : HEX_TEXT);
        gui_text_mid(LIST_X + 28, y, rh - 6, GUI_F_BODY, fg, GUI_LEFT, 300, labels[i]);

        int vx = LIST_X + LIST_W - 30;
        int cy = y + (rh - 6) / 2;
        uint32_t vc = sel ? HEX_INK : HEX_DIM;
        switch (i) {
            case 0: gui_text_mid(vx, y, rh - 6, GUI_F_BODY, vc, GUI_RIGHT, 400, server_url); break;
            case 1: gui_text_mid(vx, y, rh - 6, GUI_F_BODY, vc, GUI_RIGHT, 0, key_buf); break;
            case 2: {
                int bw = gui_text_w(GUI_F_BODY, user_buf);
                gui_text_mid(vx, y, rh - 6, GUI_F_BODY, vc, GUI_RIGHT, 0, user_buf);
                if (sel) {
                    gui_line(vx - bw - 22, cy, vx - bw - 14, cy - 6, 2, HEX_INK);
                    gui_line(vx - bw - 22, cy, vx - bw - 14, cy + 6, 2, HEX_INK);
                }
                break;
            }
            case 3: draw_switch(vx, cy, scan_ps3); break;
            case 4: draw_switch(vx, cy, scan_ps1); break;
            case 5: draw_switch(vx, cy, show_server_only); break;
            default:
                gui_line(vx - 8, cy - 6, vx, cy, 2, vc);
                gui_line(vx - 8, cy + 6, vx, cy, 2, vc);
                break;
        }
    }

    /* Help panel */
    int sel = (selected_field >= 0 && selected_field < UI_SETTINGS_FIELDS) ? selected_field : 0;
    gui_panel(DETAIL_X, PANEL_Y, DETAIL_W, PANEL_H);
    int x = DETAIL_X + 22, w = DETAIL_W - 44, y = PANEL_Y + 18;
    gui_rrect(x, y + 4, 5, 22, 2, HEX_ACCENT);
    gui_text(x + 14, y, GUI_F_TITLE, HEX_TEXT, GUI_LEFT, labels[sel]);
    y += 44;
    int n = gui_text_wrap(x, y, GUI_F_BODY, HEX_DIM, w, 6, help[sel]);
    y += n * gui_line_h(GUI_F_BODY) + 20;
    gui_rect(x, y, w, 1, HEX_LINE);
    y += 14;
    y = kv_wrap(x, y, w, "Config file", CONFIG_PATH, HEX_TEXT, 3);
    y = kv_wrap(x, y, w, "Debug log", DEBUG_LOG_FILE, HEX_TEXT, 3);
    y = kv_wrap(x, y, w, "Catalog cache", CATALOG_CACHE_DIR "/catalog_<system>.dat", HEX_TEXT, 3);

    if (status_line && status_line[0])
        draw_banner(status_line, NULL);
    else
        draw_banner(dirty ? "Unsaved changes - choose Save and apply to keep them."
                          : "Settings are stored in config.txt on the PS3 HDD.", NULL);

    static const GuiHint hints[] = {
        { "UD", "Select" }, { "LR", "Change" }, { "CROSS", "Edit / toggle / run" },
        { "CIRCLE", "Discard and back" }, { "L1/R1", "Tabs" }, { "START", "Exit" },
    };
    gui_footer(hints, (int)(sizeof(hints) / sizeof(hints[0])));
    finish_view();
    last_frame = gui_frame_id();
    snprintf(last_sig, sizeof(last_sig), "%s", sig);
}

void ui_draw_text_editor(const char *label, const char *value, int cursor_pos) {
    if (!g_ready || g_xmb_open) return;
    if (!value) value = "";

    /* Same repaint-only-on-change rule as the settings screen. */
    static unsigned last_frame = 0;
    static char last_sig[600];
    char sig[600];
    snprintf(sig, sizeof(sig), "%d|%s|%s", cursor_pos, label ? label : "", value);
    if (last_frame != 0 && last_frame == gui_frame_id() && strcmp(sig, last_sig) == 0) return;

    gui_backdrop();
    int cw = 920, ch = 300;
    int cx = (GUI_W - cw) / 2, cy = (GUI_H - ch) / 2 - 20;
    gui_card(cx, cy, cw, ch, label ? label : "Edit value", HEX_ACCENT);

    int len = (int)strlen(value);
    if (cursor_pos < 0) cursor_pos = 0;
    if (cursor_pos > len) cursor_pos = len;

    /* Input box, scrolled so the cursor stays visible. */
    int bx = cx + 24, by = cy + 76, bw = cw - 48, bh = 54;
    gui_rrect(bx - 1, by - 1, bw + 2, bh + 2, 10, HEX_ACCENT);
    gui_rrect(bx, by, bw, bh, 10, HEX_BG);

    int inner = bw - 40;
    int start = 0;
    char tmp[512];
    for (;;) {
        int n = cursor_pos + 1 - start;
        if (n > len - start) n = len - start;
        if (n < 0) n = 0;
        snprintf(tmp, sizeof(tmp), "%.*s", n, value + start);
        if (gui_text_w(GUI_F_TITLE, tmp) + 20 <= inner || start >= cursor_pos) break;
        start++;
    }
    snprintf(tmp, sizeof(tmp), "%.*s", cursor_pos - start, value + start);
    int caret_x = bx + 20 + gui_text_w(GUI_F_TITLE, tmp);
    char cur[2] = { cursor_pos < len ? value[cursor_pos] : ' ', '\0' };
    int cur_w = gui_text_w(GUI_F_TITLE, cur[0] == ' ' ? "n" : cur);
    int ty = by + (bh - gui_line_h(GUI_F_TITLE)) / 2;
    gui_rrect(caret_x - 1, by + 9, cur_w + 2, bh - 18, 4, HEX_ACCENT);
    gui_text_fit(bx + 20, ty, GUI_F_TITLE, HEX_TEXT, GUI_LEFT, inner, value + start);
    /* Re-draw the character under the caret in ink so it reads on teal. */
    if (cur[0] != ' ') gui_text(caret_x, ty, GUI_F_TITLE, HEX_INK, GUI_LEFT, cur);

    gui_textf(bx, by + bh + 14, GUI_F_SMALL, HEX_DIM, GUI_LEFT,
              "Position %d of %d   -   characters: a-z A-Z 0-9 : / . _ - ? & = %% + [ ] ( ) @ ,",
              cursor_pos + 1, len + (cursor_pos >= len ? 1 : 0));

    static const GuiHint hints1[] = {
        { "UD", "Change character" }, { "LR", "Move cursor" },
        { "SQUARE", "Insert space" }, { "TRIANGLE", "Delete" },
    };
    static const GuiHint hints2[] = { { "CROSS", "Accept" }, { "CIRCLE", "Cancel" } };
    gui_rect(cx + 18, cy + ch - 84, cw - 36, 1, HEX_LINE);
    gui_hints(bx, cy + ch - 60, hints1, 4, 26);
    gui_hints(bx, cy + ch - 28, hints2, 2, 26);
    gui_present(false);
    last_frame = gui_frame_id();
    snprintf(last_sig, sizeof(last_sig), "%s", sig);
}

/* ------------------------------------------------------------------ */
/* Dialogs                                                             */
/* ------------------------------------------------------------------ */

static void drain_buttons(void) {
    padInfo padinfo;
    padData paddata;
    int done = 0;
    while (!done) {
        sysUtilCheckCallback();
        if (g_ui_exit || g_xmb_open) return;
        done = 1;
        ioPadGetInfo(&padinfo);
        for (int i = 0; i < MAX_PADS_UI; i++) {
            if (!padinfo.status[i]) continue;
            ioPadGetData(i, &paddata);
            if (paddata.BTN_CROSS || paddata.BTN_CIRCLE || paddata.BTN_SQUARE ||
                paddata.BTN_TRIANGLE || paddata.BTN_START || paddata.BTN_L3 || paddata.BTN_R3 ||
                paddata.BTN_UP || paddata.BTN_DOWN) {
                done = 0;
            }
        }
        SDL_PumpEvents();
        usleep(16000);
    }
}

void ui_status(const char *fmt, ...) {
    va_list args;
    va_start(args, fmt);
    vsnprintf(g_status_line, sizeof(g_status_line), fmt, args);
    va_end(args);

    if (!g_ready || g_xmb_open) return;

    gui_backdrop();
    int cw = 640, ch = 132;
    int cx = (GUI_W - cw) / 2, cy = (GUI_H - ch) / 2;
    gui_rrect(cx - 1, cy - 1, cw + 2, ch + 2, 14, HEX_LINE);
    gui_rrect(cx, cy, cw, ch, 14, HEX_PANEL);

    /* Static "busy" mark: three dots in the accent colour. */
    for (int i = 0; i < 3; i++)
        gui_circle(cx + 36 + i * 16, cy + 40, 5, i == 1 ? HEX_ACCENT2 : HEX_ACCENT);
    gui_text(cx + 90, cy + 26, GUI_F_TITLE, HEX_TEXT, GUI_LEFT, "Working");
    gui_text_wrap(cx + 28, cy + 66, GUI_F_BODY, HEX_DIM, cw - 56, 2, g_status_line);
    gui_present(false);
}

static bool contains_ci(const char *hay, const char *needle) {
    size_t nl = strlen(needle);
    for (; *hay; hay++) {
        size_t i = 0;
        while (i < nl && hay[i] &&
               (hay[i] | 0x20) == (needle[i] | 0x20)) i++;
        if (i == nl) return true;
    }
    return false;
}

/* Pick a card title + tone for a free-form message: the first line becomes
 * the title when it is short, the rest is the body. */
static void message_split(const char *msg, char *title, size_t title_size,
                          const char **body, uint32_t *tone) {
    size_t first = strcspn(msg, "\n");
    if (first > 0 && first < 64) {
        snprintf(title, title_size, "%.*s", (int)first, msg);
        size_t tl = strlen(title);
        while (tl > 0 && title[tl - 1] == ':') title[--tl] = '\0';
        *body = msg + first;
        while (**body == '\n') (*body)++;
    } else {
        snprintf(title, title_size, "GameSync");
        *body = msg;
    }
    if (contains_ci(msg, "fail") || contains_ci(msg, "cannot") || contains_ci(msg, "error") ||
        contains_ci(msg, "not enough"))
        *tone = HEX_ERR;
    else if (contains_ci(title, "done") || contains_ci(title, " ok") || contains_ci(title, "complete") ||
             contains_ci(title, "finished") || contains_ci(title, "staged") ||
             contains_ci(title, "downloaded") || contains_ci(title, "refreshed"))
        *tone = HEX_OK;
    else if (contains_ci(msg, "offline") || contains_ci(msg, "only exists"))
        *tone = HEX_WARN;
    else
        *tone = HEX_ACCENT;
}

/* Dialog-local button bits (main.c has its own set for the views). */
#define DLG_CROSS    (1U << 0)
#define DLG_CIRCLE   (1U << 1)
#define DLG_UP       (1U << 2)
#define DLG_DOWN     (1U << 3)
#define DLG_LEFT     (1U << 4)
#define DLG_RIGHT    (1U << 5)
#define DLG_TRIANGLE (1U << 6)
#define DLG_START    (1U << 7)

/* Physical buttons of the first connected pad.  libpad reports the
 * physical Cross / Circle regardless of the console's Japanese / Western
 * "enter button" setting, so Cross is always confirm here. */
static unsigned dlg_buttons(void) {
    padInfo padinfo;
    padData paddata;
    unsigned b = 0;
    ioPadGetInfo(&padinfo);
    for (int i = 0; i < MAX_PADS_UI; i++) {
        if (!padinfo.status[i]) continue;
        ioPadGetData(i, &paddata);
        if (paddata.BTN_CROSS)    b |= DLG_CROSS;
        if (paddata.BTN_CIRCLE)   b |= DLG_CIRCLE;
        if (paddata.BTN_UP)       b |= DLG_UP;
        if (paddata.BTN_DOWN)     b |= DLG_DOWN;
        if (paddata.BTN_LEFT)     b |= DLG_LEFT;
        if (paddata.BTN_RIGHT)    b |= DLG_RIGHT;
        if (paddata.BTN_TRIANGLE) b |= DLG_TRIANGLE;
        if (paddata.BTN_START)    b |= DLG_START;
        break;
    }
    return b;
}

/* Block until one of `mask` is newly pressed (buttons already held when the
 * dialog opens are ignored until released).  Returns the pressed bits, or 0
 * when the app is asked to exit. */
static unsigned wait_for_press(unsigned mask) {
    unsigned prev = dlg_buttons();
    g_dialog_serial++;
    while (1) {
        sysUtilCheckCallback();
        if (g_ui_exit) return 0;
        SDL_PumpEvents();
        if (g_xmb_open) {
            usleep(50000);
            continue;
        }
        unsigned b = dlg_buttons();
        unsigned just = b & ~prev & mask;
        prev = b;
        if (just) return just;
        usleep(30000);
    }
}

/* Message cards close with Cross or Circle. */
static void wait_for_cross(void) {
    wait_for_press(DLG_CROSS | DLG_CIRCLE);
}

/* Draws the message card; footer_hint NULL = "Cross: continue". */
static void draw_message_card(const char *msg, const char *footer_text) {
    char title[96];
    const char *body;
    uint32_t tone;
    char lines[18][GUI_WRAP_LINE];

    message_split(msg, title, sizeof(title), &body, &tone);
    int cw = 880;
    int n = gui_wrap(body, GUI_F_BODY, cw - 56, lines, 18);
    int lh = gui_line_h(GUI_F_BODY);
    int ch = 76 + (n > 0 ? n * lh + 16 : 0) + 58;
    if (ch > GUI_FOOTER_Y - GUI_CONTENT_Y) ch = GUI_FOOTER_Y - GUI_CONTENT_Y;
    int cx = (GUI_W - cw) / 2;
    int cy = GUI_CONTENT_Y + (GUI_FOOTER_Y - GUI_CONTENT_Y - ch) / 2;

    gui_card(cx, cy, cw, ch, title, tone);
    for (int i = 0; i < n; i++) {
        if (cy + 70 + (i + 1) * lh > cy + ch - 58) break;
        gui_text(cx + 28, cy + 70 + i * lh, GUI_F_BODY, HEX_TEXT, GUI_LEFT, lines[i]);
    }
    gui_rect(cx + 18, cy + ch - 50, cw - 36, 1, HEX_LINE);
    if (footer_text) {
        gui_text_mid(cx + 28, cy + ch - 48, 44, GUI_F_SMALL, HEX_DIM, GUI_LEFT, cw - 56,
                     footer_text);
    } else {
        static const GuiHint h[] = { { "CROSS", "Continue" } };
        gui_hints(cx + 28, cy + ch - 26, h, 1, 0);
    }
}

void ui_message(const char *fmt, ...) {
    char buf[1024];
    va_list args;
    va_start(args, fmt);
    vsnprintf(buf, sizeof(buf), fmt, args);
    va_end(args);

    if (!g_ready) return;

    gui_backdrop();
    draw_message_card(buf, NULL);
    gui_present(false);
    wait_for_cross();
}

void ui_draw_message(const char *title, const char *message, const char *footer) {
    char full[1100];
    if (!g_ready || g_xmb_open) return;
    begin_screen(title && strcmp(title, "GameSync PS3") ? title : "Error");
    /* Fixed card title so the whole message is shown as the body. */
    snprintf(full, sizeof(full), "Something went wrong\n%s", message ? message : "");
    draw_message_card(full, footer);
    gui_present(true);
}

bool ui_confirm(const TitleInfo *title, SyncAction action,
                const char *server_hash, uint32_t server_size,
                const char *server_last_sync) {
    if (!g_ready) return false;

    drain_buttons();
    g_dialog_serial++;

    const char *heading;
    uint32_t tone;
    switch (action) {
        case SYNC_UPLOAD:     heading = "Upload to server?";        tone = HEX_WARN; break;
        case SYNC_DOWNLOAD:   heading = "Download from server?";    tone = HEX_INFO; break;
        case SYNC_CONFLICT:   heading = "Conflict - both changed";  tone = HEX_ERR;  break;
        case SYNC_UP_TO_DATE: heading = "Already up to date";       tone = HEX_OK;   break;
        default:              heading = "Cannot decide what to do"; tone = HEX_ERR;  break;
    }

    gui_backdrop();
    int cw = 820, ch = 352;
    int cx = (GUI_W - cw) / 2;
    int cy = GUI_CONTENT_Y + (GUI_FOOTER_Y - GUI_CONTENT_Y - ch) / 2;
    gui_card(cx, cy, cw, ch, heading, tone);

    int x = cx + 28, y = cy + 70;
    int n = gui_text_wrap(x, y, GUI_F_TITLE, HEX_TEXT, cw - 56, 2,
                          title->name[0] ? title->name : title->game_code);
    y += n * gui_line_h(GUI_F_TITLE) + 8;
    int tx = x;
    tx += gui_tag(tx, y, TAG_H, kind_color(title), kind_label(title)) + 6;
    gui_tag(tx, y, TAG_H, HEX_DIM, title->game_code);
    y += TAG_H + 18;

    /* Two columns: this PS3 vs. the server. */
    int colw = (cw - 56 - 16) / 2;
    int lx = x, rx = x + colw + 16;
    int bh = 128;
    gui_rrect(lx, y, colw, bh, 10, HEX_BG2);
    gui_rrect(rx, y, colw, bh, 10, HEX_BG2);
    gui_text(lx + 16, y + 12, GUI_F_BOLD, action == SYNC_UPLOAD ? HEX_WARN : HEX_TEXT,
             GUI_LEFT, "This PS3");
    gui_text(rx + 16, y + 12, GUI_F_BOLD, action == SYNC_DOWNLOAD ? HEX_INFO : HEX_TEXT,
             GUI_LEFT, "Server");

    char size_buf[32], line[96];
    if (title->server_only) {
        gui_text(lx + 16, y + 48, GUI_F_BODY, HEX_DIM, GUI_LEFT, "Not on this PS3");
    } else {
        format_size(title->total_size, size_buf, sizeof(size_buf));
        gui_text(lx + 16, y + 48, GUI_F_BODY, HEX_TEXT, GUI_LEFT, size_buf);
        snprintf(line, sizeof(line), "%d file%s", title->file_count,
                 title->file_count == 1 ? "" : "s");
        gui_text(lx + 16, y + 78, GUI_F_SMALL, HEX_DIM, GUI_LEFT, line);
    }
    if (server_hash && server_hash[0]) {
        format_size(server_size, size_buf, sizeof(size_buf));
        gui_text(rx + 16, y + 48, GUI_F_BODY, HEX_TEXT, GUI_LEFT, size_buf);
        if (server_last_sync && server_last_sync[0]) {
            char date[24];
            if (strlen(server_last_sync) >= 16 && server_last_sync[10] == 'T')
                snprintf(date, sizeof(date), "%.10s %.5s", server_last_sync, server_last_sync + 11);
            else
                snprintf(date, sizeof(date), "%.16s", server_last_sync);
            snprintf(line, sizeof(line), "Synced %s", date);
            gui_text(rx + 16, y + 78, GUI_F_SMALL, HEX_DIM, GUI_LEFT, line);
        }
    } else {
        gui_text(rx + 16, y + 48, GUI_F_BODY, HEX_DIM, GUI_LEFT, "No save on server");
    }
    /* Direction arrow between the columns */
    if (action == SYNC_UPLOAD || action == SYNC_DOWNLOAD) {
        int ax = lx + colw + 8, ay = y + bh / 2;
        gui_circle(ax, ay, 16, tone);
        if (action == SYNC_UPLOAD) {          /* this PS3 -> server */
            gui_rect(ax - 8, ay - 2, 9, 5, HEX_INK);
            gui_tri(ax + 9, ay, ax + 1, ay - 7, ax + 1, ay + 7, HEX_INK);
        } else {                              /* server -> this PS3 */
            gui_rect(ax - 1, ay - 2, 9, 5, HEX_INK);
            gui_tri(ax - 9, ay, ax - 1, ay - 7, ax - 1, ay + 7, HEX_INK);
        }
    }

    gui_rect(cx + 18, cy + ch - 50, cw - 36, 1, HEX_LINE);
    if (action == SYNC_UP_TO_DATE) {
        static const GuiHint h[] = { { "CROSS", "OK" } };
        gui_hints(cx + 28, cy + ch - 26, h, 1, 0);
    } else {
        static const GuiHint h[] = { { "CROSS", "Confirm" }, { "CIRCLE", "Cancel" } };
        gui_hints(cx + 28, cy + ch - 26, h, 2, 28);
    }
    gui_present(false);

    padInfo padinfo;
    padData paddata;
    int prev_cross = 1, prev_circle = 1;
    while (1) {
        sysUtilCheckCallback();
        if (g_ui_exit) return false;
        if (g_xmb_open) {
            SDL_PumpEvents();
            usleep(50000);
            continue;
        }
        SDL_PumpEvents();
        ioPadGetInfo(&padinfo);
        for (int i = 0; i < MAX_PADS_UI; i++) {
            if (!padinfo.status[i]) continue;
            ioPadGetData(i, &paddata);
            if (action == SYNC_UP_TO_DATE) {
                if ((!prev_cross && paddata.BTN_CROSS) || (!prev_circle && paddata.BTN_CIRCLE))
                    return false;
            } else {
                if (!prev_cross  && paddata.BTN_CROSS)  return true;
                if (!prev_circle && paddata.BTN_CIRCLE) return false;
            }
            prev_cross  = paddata.BTN_CROSS;
            prev_circle = paddata.BTN_CIRCLE;
        }
        usleep(50000);
    }
}

bool ui_ask(const char *title, const char *body, const char *confirm_label) {
    if (!g_ready) return false;

    char lines[8][GUI_WRAP_LINE];
    int cw = 760;
    int n = gui_wrap(body ? body : "", GUI_F_BODY, cw - 56, lines, 8);
    int lh = gui_line_h(GUI_F_BODY);
    int ch = 76 + (n > 0 ? n * lh + 16 : 0) + 58;
    int cx = (GUI_W - cw) / 2;
    int cy = GUI_CONTENT_Y + (GUI_FOOTER_Y - GUI_CONTENT_Y - ch) / 2;

    gui_backdrop();
    gui_card(cx, cy, cw, ch, title ? title : "GameSync", HEX_ACCENT);
    for (int i = 0; i < n; i++)
        gui_text(cx + 28, cy + 70 + i * lh, GUI_F_BODY, HEX_TEXT, GUI_LEFT, lines[i]);
    gui_rect(cx + 18, cy + ch - 50, cw - 36, 1, HEX_LINE);
    GuiHint h[2] = { { "CROSS", confirm_label ? confirm_label : "Confirm" },
                     { "CIRCLE", "Cancel" } };
    gui_hints(cx + 28, cy + ch - 26, h, 2, 28);
    gui_present(false);

    return (wait_for_press(DLG_CROSS | DLG_CIRCLE) & DLG_CROSS) != 0;
}

int ui_choose(const char *title, const char *body,
              const char *const *options, int count, int initial) {
    if (!g_ready || !options || count <= 0) return -1;

    char lines[6][GUI_WRAP_LINE];
    int cw = 760;
    int n = gui_wrap(body ? body : "", GUI_F_BODY, cw - 56, lines, 6);
    int lh = gui_line_h(GUI_F_BODY);
    int oh = 40;
    int body_h = n > 0 ? n * lh + 14 : 0;
    int ch = 70 + body_h + count * oh + 12 + 58;
    if (ch > GUI_FOOTER_Y - GUI_CONTENT_Y) ch = GUI_FOOTER_Y - GUI_CONTENT_Y;
    int cx = (GUI_W - cw) / 2;
    int cy = GUI_CONTENT_Y + (GUI_FOOTER_Y - GUI_CONTENT_Y - ch) / 2;
    int sel = (initial >= 0 && initial < count) ? initial : 0;
    unsigned prev = dlg_buttons();
    g_dialog_serial++;

    while (1) {
        gui_backdrop();
        gui_card(cx, cy, cw, ch, title ? title : "GameSync", HEX_ACCENT);
        for (int i = 0; i < n; i++)
            gui_text(cx + 28, cy + 66 + i * lh, GUI_F_BODY, HEX_DIM, GUI_LEFT, lines[i]);
        int oy = cy + 66 + body_h;
        for (int i = 0; i < count; i++) {
            int y = oy + i * oh;
            bool on = (i == sel);
            if (on) gui_rrect(cx + 18, y, cw - 36, oh - 6, 9, HEX_ACCENT);
            gui_text_mid(cx + 36, y, oh - 6, GUI_F_BODY, on ? HEX_INK : HEX_TEXT, GUI_LEFT,
                         cw - 72, options[i]);
        }
        gui_rect(cx + 18, cy + ch - 50, cw - 36, 1, HEX_LINE);
        static const GuiHint h[] = { { "UD", "Choose" }, { "CROSS", "Select" },
                                     { "CIRCLE", "Close" } };
        gui_hints(cx + 28, cy + ch - 26, h, 3, 28);
        gui_present(false);

        /* Wait for a fresh press, then act on it. */
        unsigned just = 0;
        while (!just) {
            sysUtilCheckCallback();
            if (g_ui_exit) return -1;
            SDL_PumpEvents();
            if (g_xmb_open) { usleep(50000); continue; }
            unsigned b = dlg_buttons();
            just = b & ~prev;
            prev = b;
            if (!just) usleep(30000);
        }
        if (just & DLG_CIRCLE) return -1;
        if (just & DLG_CROSS) return sel;
        if (just & DLG_UP)   sel = (sel - 1 + count) % count;
        if (just & DLG_DOWN) sel = (sel + 1) % count;
    }
}

/* ------------------------------------------------------------------ */
/* ROM catalog                                                         */
/* ------------------------------------------------------------------ */

/* Local const lookup — downloads_find() takes a non-const list. */
static const DownloadEntry *find_download_const(const DownloadList *list, const char *rom_id) {
    if (!list || !rom_id) return NULL;
    for (int i = 0; i < list->count; i++) {
        if (strcmp(list->items[i].rom_id, rom_id) == 0)
            return &list->items[i];
    }
    return NULL;
}

static void dl_status_style(DownloadStatus st, const char **label, uint32_t *color) {
    switch (st) {
        case DL_STATUS_QUEUED:    *label = "QUEUED";      *color = HEX_ACCENT2; break;
        case DL_STATUS_ACTIVE:    *label = "DOWNLOADING"; *color = HEX_INFO;    break;
        case DL_STATUS_PAUSED:    *label = "PAUSED";      *color = HEX_WARN;    break;
        case DL_STATUS_COMPLETED: *label = "INSTALLED";   *color = HEX_OK;      break;
        case DL_STATUS_ERROR:     *label = "ERROR";       *color = HEX_ERR;     break;
        default:                  *label = "?";           *color = HEX_DIM;     break;
    }
}

static void rom_target_text(const RomEntry *r, char *out, size_t out_size) {
    if (r->is_bundle) {
        if (strcmp(r->system, "PS1") == 0)
            snprintf(out, out_size, "%s/<game>/", ROM_TARGET_PSXISO_DIR);
        else
            snprintf(out, out_size, "%s + %s", ROM_TARGET_PKG_DIR, ROM_TARGET_EXDATA_DIR);
        return;
    }
    if (!roms_resolve_target_path(r, out, out_size))
        snprintf(out, out_size, "%s", ROM_TARGET_FALLBACK_DIR);
}

static void draw_rom_detail(const RomEntry *r, const DownloadEntry *dl) {
    int x = DETAIL_X + 22, w = DETAIL_W - 44, y = PANEL_Y + 18;
    char buf[PATH_LEN];

    gui_panel(DETAIL_X, PANEL_Y, DETAIL_W, PANEL_H);
    int n = gui_text_wrap(x, y, GUI_F_HUGE, HEX_TEXT, w, 3, r->name[0] ? r->name : r->filename);
    y += n * gui_line_h(GUI_F_HUGE) + 8;

    int tx = x;
    tx += gui_tag(tx, y, TAG_H, strcmp(r->system, "PS1") == 0 ? HEX_PS1 : HEX_PS3,
                  r->system[0] ? r->system : "?") + 6;
    format_size(r->size, buf, sizeof(buf));
    tx += gui_tag(tx, y, TAG_H, HEX_DIM, buf) + 6;
    if (r->is_bundle) {
        snprintf(buf, sizeof(buf), "Bundle - %d files", r->file_count);
        gui_tag(tx, y, TAG_H, HEX_ACCENT2, buf);
    } else if (r->extract_format[0]) {
        snprintf(buf, sizeof(buf), "Unpacks to %s", r->extract_format);
        gui_tag(tx, y, TAG_H, HEX_ACCENT2, buf);
    }
    y += TAG_H + 16;

    y = kv_wrap(x, y, w, "File", r->filename, HEX_TEXT, 2);
    rom_target_text(r, buf, sizeof(buf));
    y = kv_wrap(x, y, w, "Installs to", buf, HEX_TEXT, 3);

    gui_rect(x, y + 2, w, 1, HEX_LINE);
    y += 14;
    if (!dl) {
        y = kv_row(x, y, w, "Download", "Not downloaded", HEX_DIM);
    } else {
        const char *label;
        uint32_t color;
        dl_status_style(dl->status, &label, &color);
        gui_text(x, y + 2, GUI_F_SMALL, HEX_DIM, GUI_LEFT, "Download");
        gui_tag(x + 112, y, TAG_H, color, label);
        y += 34;
        if (dl->status != DL_STATUS_COMPLETED && dl->total > 0) {
            int pct = percent_of(dl->offset, dl->total);
            gui_bar(x, y, w, 10, (float)pct / 100.0f, color);
            char off_buf[24], tot_buf[24];
            format_size(dl->offset, off_buf, sizeof(off_buf));
            format_size(dl->total, tot_buf, sizeof(tot_buf));
            gui_textf(x, y + 18, GUI_F_SMALL, HEX_DIM, GUI_LEFT, "%s / %s", off_buf, tot_buf);
            gui_textf(x + w, y + 18, GUI_F_SMALL, HEX_TEXT, GUI_RIGHT, "%d%%", pct);
        }
    }
}

void ui_draw_rom_catalog(const RomCatalog *catalog,
                         const DownloadList *downloads,
                         const char *const *systems, int system_count,
                         int system_index,
                         int selected, int scroll_offset,
                         const char *status_line,
                         const char *source_note) {
    char buf[128];
    if (!g_ready || g_xmb_open) return;

    begin_view(APP_VIEW_ROMS);
    gui_panel(LIST_X, PANEL_Y, LIST_W, PANEL_H);

    int total = catalog ? catalog->count : 0;
    int cy = TOOLBAR_Y + TOOLBAR_H / 2;
    int tx = LIST_X + 16;
    (void)cy;
    tx = draw_subtabs(tx, systems, system_count, system_index) + 12;
    if (source_note && source_note[0])
        gui_tag(tx, TOOLBAR_Y + (TOOLBAR_H - TAG_H) / 2, TAG_H,
                strstr(source_note, "ffline") ? HEX_WARN : HEX_DIM, source_note);
    snprintf(buf, sizeof(buf), "%d title%s  -  %d in queue", total, total == 1 ? "" : "s",
             downloads ? downloads->count : 0);
    gui_text_mid(LIST_X + LIST_W - 22, TOOLBAR_Y, TOOLBAR_H, GUI_F_SMALL, HEX_DIM, GUI_RIGHT, 0, buf);
    gui_rect(LIST_X + 12, ROWS_Y - 10, LIST_W - 24, 1, HEX_LINE);

    const char *sys = (systems && system_index >= 0 && system_index < system_count)
                      ? systems[system_index] : "";

    if (total == 0) {
        char title[96];
        snprintf(title, sizeof(title), "No %s games in the catalog", sys);
        draw_empty(title, catalog && catalog->last_error[0]
                          ? catalog->last_error
                          : "Add games to the server's ROM folder, then run Settings > "
                            "Refresh catalog.");
        gui_panel(DETAIL_X, PANEL_Y, DETAIL_W, PANEL_H);
        gui_text_wrap(DETAIL_X + 22, PANEL_Y + 22, GUI_F_BODY, HEX_DIM, DETAIL_W - 44, 8,
                      "PS3 ISOs go to /dev_hdd0/PS3ISO and packages to /dev_hdd0/packages. "
                      "PS1 games are unpacked to /dev_hdd0/PSXISO/<game>/ for webMAN.");
    } else {
        int end = scroll_offset + UI_LIST_ROWS;
        if (end > total) end = total;
        for (int i = scroll_offset; i < end; i++) {
            const RomEntry *r = &catalog->items[i];
            int row = i - scroll_offset;
            int y = row_y(row);
            bool sel = (i == selected);
            const DownloadEntry *dl = find_download_const(downloads, r->rom_id);

            if (sel) draw_row_bar(row);
            int name_x = LIST_X + 24;
            if (dl) {
                const char *label;
                uint32_t color;
                dl_status_style(dl->status, &label, &color);
                tag_fixed(LIST_X + 20, y + (ROW_H - 2 - TAG_H) / 2, DL_TAG_W, color, label);
                name_x = LIST_X + 20 + DL_TAG_W + 12;
            }
            format_size(r->size, buf, sizeof(buf));
            int size_w = gui_text_w(GUI_F_SMALL, buf);
            gui_text_mid(LIST_X + LIST_W - 34, y, ROW_H - 2, GUI_F_SMALL,
                         sel ? HEX_INK : HEX_DIM, GUI_RIGHT, 0, buf);
            gui_text_mid(name_x, y, ROW_H - 2, GUI_F_BODY, sel ? HEX_INK : HEX_TEXT, GUI_LEFT,
                         LIST_X + LIST_W - 50 - size_w - name_x,
                         r->name[0] ? r->name : r->filename);
        }
        gui_scrollbar(LIST_X + LIST_W - 14, ROWS_Y, UI_LIST_ROWS * ROW_H - 2,
                      scroll_offset, UI_LIST_ROWS, total);

        if (selected >= 0 && selected < total) {
            const RomEntry *r = &catalog->items[selected];
            draw_rom_detail(r, find_download_const(downloads, r->rom_id));
        } else {
            gui_panel(DETAIL_X, PANEL_Y, DETAIL_W, PANEL_H);
        }
    }

    draw_banner(catalog && catalog->last_error[0] ? catalog->last_error : status_line, NULL);

    static const GuiHint hints[] = {
        { "CROSS", "Download / resume" }, { "TRIANGLE", "Details" }, { "SELECT", "PS3 / PS1" },
        { "LR", "Page" }, { "L1/R1", "Tabs" }, { "START", "Exit" },
    };
    gui_footer(hints, (int)(sizeof(hints) / sizeof(hints[0])));
    finish_view();
}

/* ------------------------------------------------------------------ */
/* Downloads                                                           */
/* ------------------------------------------------------------------ */

static void draw_active_download(const DownloadEntry *active,
                                 uint64_t downloaded, uint64_t total, uint64_t bps) {
    int x = DETAIL_X + 22, w = DETAIL_W - 44, y = PANEL_Y + 18;
    char off_buf[24], tot_buf[24], bps_buf[24], eta_buf[24];

    gui_panel(DETAIL_X, PANEL_Y, DETAIL_W, PANEL_H);
    gui_rrect(x, y + 4, 5, 22, 2, HEX_ACCENT);
    gui_text(x + 14, y, GUI_F_TITLE, HEX_TEXT, GUI_LEFT, "Downloading");
    y += 44;

    const char *name = active && active->name[0] ? active->name
                     : (active && active->filename[0] ? active->filename : "(unknown)");
    int n = gui_text_wrap(x, y, GUI_F_BOLD, HEX_TEXT, w, 2, name);
    y += n * gui_line_h(GUI_F_BOLD) + 6;

    const char *file = active ? basename_of(active->target_path) : "";
    if (active && active->is_bundle && active->bundle_count > 0) {
        char line[GUI_WRAP_LINE];
        snprintf(line, sizeof(line), "File %d of %d: %s",
                 active->bundle_index + 1, active->bundle_count, file);
        n = gui_text_wrap(x, y, GUI_F_SMALL, HEX_DIM, w, 2, line);
    } else {
        n = gui_text_wrap(x, y, GUI_F_SMALL, HEX_DIM, w, 2, file);
    }
    y += n * gui_line_h(GUI_F_SMALL) + 18;

    uint64_t tot = total > 0 ? total : (active ? active->total : 0);
    int pct = percent_of(downloaded, tot);
    gui_bar(x, y, w, 16, (float)pct / 100.0f, HEX_ACCENT);
    y += 26;
    format_size(downloaded, off_buf, sizeof(off_buf));
    format_size(tot, tot_buf, sizeof(tot_buf));
    gui_textf(x, y, GUI_F_BODY, HEX_TEXT, GUI_LEFT, "%s / %s", off_buf, tot_buf);
    gui_textf(x + w, y - 4, GUI_F_HUGE, HEX_ACCENT2, GUI_RIGHT, "%d%%", pct);
    y += 46;

    format_bps(bps, bps_buf, sizeof(bps_buf));
    format_eta(tot > downloaded ? tot - downloaded : 0, bps, eta_buf, sizeof(eta_buf));
    y = kv_row(x, y, w, "Speed", bps_buf, HEX_TEXT);
    y = kv_row(x, y, w, "Remaining", eta_buf, HEX_TEXT);

    int by = PANEL_Y + PANEL_H - 56;
    gui_rect(x, by - 8, w, 1, HEX_LINE);
    static const GuiHint h[] = { { "CIRCLE", "Pause (keeps progress)" } };
    gui_hints(x, by + 18, h, 1, 0);
}

static void draw_download_detail(const DownloadEntry *e) {
    int x = DETAIL_X + 22, w = DETAIL_W - 44, y = PANEL_Y + 18;
    const char *label;
    uint32_t color;
    char buf[64];

    gui_panel(DETAIL_X, PANEL_Y, DETAIL_W, PANEL_H);
    int n = gui_text_wrap(x, y, GUI_F_HUGE, HEX_TEXT, w, 3, e->name[0] ? e->name : e->filename);
    y += n * gui_line_h(GUI_F_HUGE) + 8;

    dl_status_style(e->status, &label, &color);
    int tx = x;
    tx += gui_tag(tx, y, TAG_H, strcmp(e->system, "PS1") == 0 ? HEX_PS1 : HEX_PS3,
                  e->system[0] ? e->system : "?") + 6;
    tx += gui_tag(tx, y, TAG_H, color, label) + 6;
    if (e->is_bundle && e->bundle_count > 0) {
        snprintf(buf, sizeof(buf), "File %d/%d", e->bundle_index + 1, e->bundle_count);
        gui_tag(tx, y, TAG_H, HEX_ACCENT2, buf);
    }
    y += TAG_H + 16;

    y = kv_wrap(x, y, w, "File", e->filename, HEX_TEXT, 2);
    y = kv_wrap(x, y, w, "Target", e->target_path[0] ? e->target_path : "(not resolved yet)",
                HEX_TEXT, 3);
    y += 6;

    int pct = e->status == DL_STATUS_COMPLETED ? 100 : percent_of(e->offset, e->total);
    gui_bar(x, y, w, 12, (float)pct / 100.0f, color);
    y += 22;
    char off_buf[24], tot_buf[24];
    format_size(e->status == DL_STATUS_COMPLETED ? e->total : e->offset, off_buf, sizeof(off_buf));
    format_size(e->total, tot_buf, sizeof(tot_buf));
    gui_textf(x, y, GUI_F_SMALL, HEX_DIM, GUI_LEFT, "%s / %s", off_buf, tot_buf);
    gui_textf(x + w, y, GUI_F_SMALL, HEX_TEXT, GUI_RIGHT, "%d%%", pct);
    y += 34;

    const char *advice = NULL;
    switch (e->status) {
        case DL_STATUS_QUEUED:    advice = "Press Cross to start."; break;
        case DL_STATUS_PAUSED:    advice = "Press Cross to resume from where it stopped."; break;
        case DL_STATUS_ERROR:     advice = "Press Cross to retry, or Triangle to remove it."; break;
        case DL_STATUS_COMPLETED: advice = "Installed. Square clears finished entries."; break;
        default: break;
    }
    if (advice) gui_text_wrap(x, y, GUI_F_SMALL, HEX_DIM, w, 3, advice);
}

void ui_draw_downloads(const DownloadList *downloads,
                       int selected, int scroll_offset,
                       const char *status_line,
                       bool active_in_progress,
                       uint64_t active_downloaded,
                       uint64_t active_total,
                       uint64_t active_bps) {
    char buf[128];
    if (!g_ready || g_xmb_open) return;

    begin_view(APP_VIEW_DOWNLOADS);
    gui_panel(LIST_X, PANEL_Y, LIST_W, PANEL_H);

    int total = downloads ? downloads->count : 0;
    int counts[5] = { 0, 0, 0, 0, 0 };
    const DownloadEntry *active = NULL;
    for (int i = 0; i < total; i++) {
        int s = (int)downloads->items[i].status;
        if (s >= 0 && s < 5) counts[s]++;
        if (downloads->items[i].status == DL_STATUS_ACTIVE && !active)
            active = &downloads->items[i];
    }

    gui_text_mid(LIST_X + 22, TOOLBAR_Y, TOOLBAR_H, GUI_F_BOLD, HEX_TEXT, GUI_LEFT, 0, "Queue");
    int tx = LIST_X + 22 + gui_text_w(GUI_F_BOLD, "Queue") + 16;
    int ty = TOOLBAR_Y + (TOOLBAR_H - TAG_H) / 2;
    if (counts[DL_STATUS_ACTIVE]) tx += gui_tag(tx, ty, TAG_H, HEX_INFO, "1 active") + 6;
    if (counts[DL_STATUS_QUEUED]) {
        snprintf(buf, sizeof(buf), "%d queued", counts[DL_STATUS_QUEUED]);
        tx += gui_tag(tx, ty, TAG_H, HEX_ACCENT2, buf) + 6;
    }
    if (counts[DL_STATUS_PAUSED]) {
        snprintf(buf, sizeof(buf), "%d paused", counts[DL_STATUS_PAUSED]);
        tx += gui_tag(tx, ty, TAG_H, HEX_WARN, buf) + 6;
    }
    if (counts[DL_STATUS_COMPLETED]) {
        snprintf(buf, sizeof(buf), "%d done", counts[DL_STATUS_COMPLETED]);
        tx += gui_tag(tx, ty, TAG_H, HEX_OK, buf) + 6;
    }
    if (counts[DL_STATUS_ERROR]) {
        snprintf(buf, sizeof(buf), "%d failed", counts[DL_STATUS_ERROR]);
        gui_tag(tx, ty, TAG_H, HEX_ERR, buf);
    }
    snprintf(buf, sizeof(buf), "%d entr%s", total, total == 1 ? "y" : "ies");
    gui_text_mid(LIST_X + LIST_W - 22, TOOLBAR_Y, TOOLBAR_H, GUI_F_SMALL, HEX_DIM, GUI_RIGHT, 0, buf);
    gui_rect(LIST_X + 12, ROWS_Y - 10, LIST_W - 24, 1, HEX_LINE);

    if (total == 0) {
        draw_empty("No downloads yet",
                   "Open the ROM Catalog (L1 / R1) and press Cross on a game to download it.");
    } else {
        int end = scroll_offset + UI_LIST_ROWS;
        if (end > total) end = total;
        for (int i = scroll_offset; i < end; i++) {
            const DownloadEntry *e = &downloads->items[i];
            int row = i - scroll_offset;
            int y = row_y(row);
            bool sel = (i == selected);
            const char *label;
            uint32_t color;

            uint64_t off = e->offset, tot = e->total;
            if (e->status == DL_STATUS_ACTIVE && active_in_progress) {
                off = active_downloaded;
                if (active_total > 0) tot = active_total;
            }
            int pct = e->status == DL_STATUS_COMPLETED ? 100 : percent_of(off, tot);

            if (sel) draw_row_bar(row);
            dl_status_style(e->status, &label, &color);
            tag_fixed(LIST_X + 20, y + (ROW_H - 2 - TAG_H) / 2, DL_TAG_W, color, label);

            int bar_w = 120;
            int pct_x = LIST_X + LIST_W - 34;
            int bar_x = pct_x - 48 - bar_w;
            snprintf(buf, sizeof(buf), "%d%%", pct);
            gui_text_mid(pct_x, y, ROW_H - 2, GUI_F_SMALL, sel ? HEX_INK : HEX_TEXT,
                         GUI_RIGHT, 0, buf);
            if (sel)
                gui_bar_ex(bar_x, y + (ROW_H - 2) / 2 - 4, bar_w, 8, (float)pct / 100.0f,
                           gui_mix(HEX_ACCENT, HEX_INK, 0.35f), HEX_INK);
            else
                gui_bar(bar_x, y + (ROW_H - 2) / 2 - 4, bar_w, 8, (float)pct / 100.0f, color);

            int name_x = LIST_X + 20 + DL_TAG_W + 12;
            gui_text_mid(name_x, y, ROW_H - 2, GUI_F_BODY, sel ? HEX_INK : HEX_TEXT, GUI_LEFT,
                         bar_x - 14 - name_x, e->name[0] ? e->name : e->filename);
        }
        gui_scrollbar(LIST_X + LIST_W - 14, ROWS_Y, UI_LIST_ROWS * ROW_H - 2,
                      scroll_offset, UI_LIST_ROWS, total);
    }

    if (active_in_progress)
        draw_active_download(active, active_downloaded, active_total, active_bps);
    else if (selected >= 0 && selected < total)
        draw_download_detail(&downloads->items[selected]);
    else {
        gui_panel(DETAIL_X, PANEL_Y, DETAIL_W, PANEL_H);
        gui_text_wrap(DETAIL_X + 22, PANEL_Y + 22, GUI_F_BODY, HEX_DIM, DETAIL_W - 44, 8,
                      "Downloads resume where they stopped, even after the app is closed.");
    }

    draw_banner(status_line, NULL);

    static const GuiHint hints[] = {
        { "CROSS", "Start / resume" }, { "CIRCLE", "Pause" }, { "SQUARE", "Clear finished" },
        { "TRIANGLE", "Options" }, { "LR", "Page" }, { "L1/R1", "Tabs" }, { "START", "Exit" },
    };
    gui_footer(hints, (int)(sizeof(hints) / sizeof(hints[0])));
    finish_view();
}
