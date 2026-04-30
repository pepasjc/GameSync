// Save Sync - original Xbox client.
//
// Phase 8: SDL2 + SDL_ttf renderer for a CRT-friendly UI.

#include <SDL.h>
#include <SDL_ttf.h>
#include <hal/debug.h>
#include <hal/video.h>
#include <hal/xbox.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include <xboxkrnl/xboxkrnl.h>

#include "bundle.h"
#include "config.h"
#include "network.h"
#include "saves.h"
#include "state.h"
#include "sync.h"
#include "ui.h"

#ifndef APP_VERSION
#define APP_VERSION "dev"
#endif

#define LIST_VISIBLE   9   // rows the body can show with the top status bar

static XboxSaveList g_list;
static XboxConfig   g_cfg;
static SyncPlan     g_plan;
static int          g_plan_loaded = 0;
static char         g_status[200] = "Press LB to fetch sync plan.";
typedef enum {
    UI_STATUS_INFO_KIND,
    UI_STATUS_BUSY_KIND,
    UI_STATUS_SUCCESS_KIND,
    UI_STATUS_ERROR_KIND,
} StatusKind;
static StatusKind   g_status_kind = UI_STATUS_INFO_KIND;
static char         g_local_ip[16] = "0.0.0.0";
static char         g_server_text[64] = "";

static void set_status_kind(StatusKind kind, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    g_status_kind = kind;
    vsnprintf(g_status, sizeof(g_status), fmt, ap);
    va_end(ap);
}

static const char *fmt_kb(uint32_t bytes, char *buf, size_t buflen)
{
    snprintf(buf, buflen, "%u KB", (unsigned)(bytes / 1024));
    return buf;
}

// Truncate a string with "..." suffix to fit at most ``max_chars``.
static void short_str(const char *src, char *out, int max_chars)
{
    int n = (int)strlen(src);
    if (n <= max_chars) {
        snprintf(out, max_chars + 1, "%s", src);
    } else {
        snprintf(out, max_chars + 1, "%.*s...", max_chars - 3, src);
    }
}

static void short_hash(const char *src, char *out, int out_len)
{
    if (!out || out_len <= 0) return;
    if (!src || !src[0]) {
        snprintf(out, out_len, "n/a");
        return;
    }

    int n = (int)strlen(src);
    if (n <= 30) {
        snprintf(out, out_len, "%s", src);
    } else {
        snprintf(out, out_len, "%.16s...%.8s", src, src + n - 8);
    }
}

static void fmt_timestamp(uint32_t ts, char *out, int out_len)
{
    if (!out || out_len <= 0) return;
    if (ts == 0) {
        snprintf(out, out_len, "n/a");
    } else {
        snprintf(out, out_len, "%u", (unsigned)ts);
    }
}

static UiColor status_color(TitleStatus s)
{
    switch (s) {
    case TITLE_STATUS_UP_TO_DATE:    return UI_STATUS_OK;
    case TITLE_STATUS_NEEDS_UPLOAD:  return UI_STATUS_UPLOAD;
    case TITLE_STATUS_NEEDS_DOWNLOAD:return UI_STATUS_DOWNLOAD;
    case TITLE_STATUS_CONFLICT:      return UI_STATUS_CONFLICT;
    case TITLE_STATUS_SERVER_ONLY:   return UI_STATUS_NEW;
    default:                         return UI_STATUS_UNKNOWN;
    }
}

static const char *status_label(TitleStatus s)
{
    switch (s) {
    case TITLE_STATUS_UP_TO_DATE:    return " OK ";
    case TITLE_STATUS_NEEDS_UPLOAD:  return " UP ";
    case TITLE_STATUS_NEEDS_DOWNLOAD:return "DOWN";
    case TITLE_STATUS_CONFLICT:      return "CFLT";
    case TITLE_STATUS_SERVER_ONLY:   return "NEW ";
    default:                         return " ?  ";
    }
}

static UiColor status_bar_color(void)
{
    switch (g_status_kind) {
    case UI_STATUS_BUSY_KIND:    return UI_STATUS_UPLOAD;
    case UI_STATUS_SUCCESS_KIND: return UI_STATUS_OK;
    case UI_STATUS_ERROR_KIND:   return UI_STATUS_CONFLICT;
    default:             return UI_ROW_BG_SEL;
    }
}

static UiColor status_bar_text_color(void)
{
    switch (g_status_kind) {
    case UI_STATUS_BUSY_KIND:
    case UI_STATUS_SUCCESS_KIND:
    case UI_STATUS_ERROR_KIND: {
        UiColor dark = { 0x0B, 0x18, 0x10, 0xFF };
        return dark;
    }
    default:
        return UI_ACCENT;
    }
}

// ---------------------------------------------------------------------------
// Layout helpers
// ---------------------------------------------------------------------------

#define HEADER_H      60
#define FOOTER_H      80
#define STATUS_BAR_H  28
#define ROW_H         30
#define LIST_Y        (HEADER_H + STATUS_BAR_H + 8)

// X-positions inside the row.
#define COL_BADGE_X    (UI_SAFE_X + 4)
#define COL_BADGE_W    52
#define COL_TID_X      (COL_BADGE_X + COL_BADGE_W + 12)
#define COL_TID_W      78
#define COL_NAME_X     (COL_TID_X + COL_TID_W + 8)
#define COL_SIZE_X     (UI_W - UI_SAFE_X - 70)

static void draw_header(void)
{
    ui_rect(0, 0, UI_W, HEADER_H, UI_HEADER_BG);

    // Title left, IP/server right.
    char title[64];
    snprintf(title, sizeof(title), "Save Sync v%s - Xbox", APP_VERSION);
    ui_text(UI_SAFE_X, 12, title, UI_ACCENT, UI_FONT_HEADER);

    char info[96];
    snprintf(info, sizeof(info), "%s   IP %s", g_server_text, g_local_ip);
    int w = ui_text_width(info, UI_FONT_SMALL);
    ui_text(UI_W - UI_SAFE_X - w, 14, info, UI_TEXT_DIM, UI_FONT_SMALL);

    // Plan summary line.
    char plan[160];
    if (g_plan_loaded) {
        snprintf(plan, sizeof(plan),
                 "Plan: up %d   down %d   new %d   ok %d   conflict %d",
                 g_plan.upload_count,
                 g_plan.download_count,
                 g_plan.server_only_count,
                 g_plan.up_to_date_count,
                 g_plan.conflict_count);
    } else {
        snprintf(plan, sizeof(plan),
                 "Plan: not fetched yet - press LB");
    }
    ui_text(UI_SAFE_X, 38, plan, UI_TEXT, UI_FONT_BODY);
}

static void draw_footer(void)
{
    int y0 = UI_H - FOOTER_H;
    ui_rect(0, y0, UI_W, FOOTER_H, UI_FOOTER_BG);

    ui_text(UI_SAFE_X, y0 + 6,
            "A smart  X upload  Y download  B clear cache",
            UI_TEXT, UI_FONT_BODY);
    ui_text(UI_SAFE_X, y0 + 28,
            "D-pad/Stick L/R page  LB refresh  RB sync-all  START exit",
            UI_TEXT_DIM, UI_FONT_SMALL);
}

static void draw_status_bar(void)
{
    int status_y = HEADER_H;
    ui_rect(0, status_y, UI_W, STATUS_BAR_H, status_bar_color());

    char line[160];
    snprintf(line, sizeof(line), "%s", g_status);

    // Compute the per-character width budget heuristically.
    int max_chars = (int)strlen(line);
    int budget = UI_W - 2 * UI_SAFE_X;
    while (max_chars > 4) {
        if (ui_text_width(line, UI_FONT_SMALL) <= budget) break;
        line[--max_chars] = '\0';
    }
    ui_text(UI_SAFE_X, status_y + 5, line,
            status_bar_text_color(), UI_FONT_SMALL);
}

static void draw_row(int row_idx, int local_count, int cursor, int y)
{
    int  selected = (row_idx == cursor);
    int  is_local = (row_idx < local_count);

    if (selected) {
        ui_rect(UI_SAFE_X - 4, y - 2, UI_W - 2 * UI_SAFE_X + 8, ROW_H,
                UI_ROW_BG_SEL);
    }

    const char  *tid = NULL;
    const char  *name = NULL;
    char         size_buf[24] = "";
    TitleStatus  st = TITLE_STATUS_UNKNOWN;

    if (is_local) {
        const XboxSaveTitle *t = &g_list.titles[row_idx];
        tid = t->title_id;
        name = t->name[0] ? t->name : t->title_id;
        fmt_kb(t->total_size, size_buf, sizeof(size_buf));
        if (g_plan_loaded) st = sync_plan_status(&g_plan, t->title_id);
    } else {
        int j = row_idx - local_count;
        tid  = g_plan.server_only_ids[j];
        name = g_plan.server_only_names[j][0]
                   ? g_plan.server_only_names[j]
                   : g_plan.server_only_ids[j];
        snprintf(size_buf, sizeof(size_buf), "server");
        st = TITLE_STATUS_SERVER_ONLY;
    }

    UiColor sc = status_color(st);

    // Badge: filled rect with status label centered inside.
    ui_rect(COL_BADGE_X, y + 2, COL_BADGE_W, ROW_H - 6, sc);
    {
        const char *lbl = status_label(st);
        int lw = ui_text_width(lbl, UI_FONT_SMALL);
        UiColor on = { 0x10, 0x20, 0x18, 0xFF };  // dark text on light badge
        ui_text(COL_BADGE_X + (COL_BADGE_W - lw) / 2, y + 5,
                lbl, on, UI_FONT_SMALL);
    }

    // Title id (monospace-ish, accent color).
    ui_text(COL_TID_X, y + 4, tid, UI_TEXT, UI_FONT_BODY);

    // Game name, truncated to fit. Plenty of room between the TID column
    // and the right-aligned size, so allow a long name.
    char name_short[64];
    short_str(name, name_short, 52);
    ui_text(COL_NAME_X, y + 4, name_short, UI_TEXT, UI_FONT_BODY);

    // Size, right-aligned.
    int sw = ui_text_width(size_buf, UI_FONT_BODY);
    ui_text(UI_W - UI_SAFE_X - sw, y + 4, size_buf, UI_TEXT_DIM, UI_FONT_BODY);
}

static int total_rows(void)
{
    int n = g_list.title_count;
    if (g_plan_loaded) n += g_plan.server_only_count;
    return n;
}

static void redraw(int cursor, int scroll)
{
    ui_clear(UI_BG);

    draw_header();
    draw_status_bar();

    int total = total_rows();
    int y = LIST_Y;
    int end = scroll + LIST_VISIBLE;
    if (end > total) end = total;

    if (total == 0) {
        ui_text(UI_SAFE_X, y + 24,
                "No saves on Xbox or server.",
                UI_TEXT_DIM, UI_FONT_BODY);
    } else {
        for (int i = scroll; i < end; i++) {
            draw_row(i, g_list.title_count, cursor, y);
            y += ROW_H;
        }

        // Scrollbar hint.
        if (total > LIST_VISIBLE) {
            char nav[24];
            snprintf(nav, sizeof(nav), "%d / %d", cursor + 1, total);
            int w = ui_text_width(nav, UI_FONT_SMALL);
            ui_text(UI_W - UI_SAFE_X - w,
                    LIST_Y + LIST_VISIBLE * ROW_H + 2,
                    nav, UI_TEXT_DIM, UI_FONT_SMALL);
        }
    }

    draw_footer();
    ui_present();
}

static void clamp_cursor_scroll(int *cursor, int *scroll)
{
    int total = total_rows();
    if (total <= 0) {
        *cursor = 0;
        *scroll = 0;
        return;
    }

    if (*cursor < 0) *cursor = 0;
    if (*cursor >= total) *cursor = total - 1;

    int max_scroll = total > LIST_VISIBLE ? total - LIST_VISIBLE : 0;
    if (*scroll < 0) *scroll = 0;
    if (*scroll > max_scroll) *scroll = max_scroll;
    if (*cursor < *scroll) *scroll = *cursor;
    if (*cursor >= *scroll + LIST_VISIBLE)
        *scroll = *cursor - LIST_VISIBLE + 1;
    if (*scroll > max_scroll) *scroll = max_scroll;
}

static int page_rows(int direction, int *cursor, int *scroll)
{
    int total = total_rows();
    if (total <= LIST_VISIBLE || direction == 0) return 0;

    int old_cursor = *cursor;
    int old_scroll = *scroll;
    int max_scroll = total - LIST_VISIBLE;
    int offset = *cursor - *scroll;
    if (offset < 0) offset = 0;
    if (offset >= LIST_VISIBLE) offset = LIST_VISIBLE - 1;

    *scroll += direction * LIST_VISIBLE;
    if (*scroll < 0) *scroll = 0;
    if (*scroll > max_scroll) *scroll = max_scroll;

    *cursor = *scroll + offset;
    if (*cursor >= total) *cursor = total - 1;

    return old_cursor != *cursor || old_scroll != *scroll;
}

// ---------------------------------------------------------------------------
// Side-effect helpers (network/sync ops with status updates)
// ---------------------------------------------------------------------------

static void resolve_local_names(void);

static int row_to_title(int cursor, const char **out_tid, XboxSaveTitle **out_local)
{
    if (cursor < g_list.title_count) {
        *out_tid = g_list.titles[cursor].title_id;
        *out_local = &g_list.titles[cursor];
        return 0;
    }
    if (!g_plan_loaded) return -1;
    int idx = cursor - g_list.title_count;
    if (idx < 0 || idx >= g_plan.server_only_count) return -1;
    *out_tid = g_plan.server_only_ids[idx];
    *out_local = NULL;
    return 0;
}

static void plan_remove_from_bucket(char (*ids)[XBOX_TITLE_ID_LEN + 1],
                                    int *count,
                                    const char *tid)
{
    if (!ids || !count || !tid) return;
    for (int i = 0; i < *count; i++) {
        if (strcmp(ids[i], tid) != 0) continue;
        for (int j = i; j + 1 < *count; j++) {
            snprintf(ids[j], XBOX_TITLE_ID_LEN + 1, "%s", ids[j + 1]);
        }
        (*count)--;
        ids[*count][0] = '\0';
        i--;
    }
}

static void plan_remove_from_server_only(const char *tid)
{
    if (!g_plan.server_only_ids || !tid) return;
    for (int i = 0; i < g_plan.server_only_count; i++) {
        if (strcmp(g_plan.server_only_ids[i], tid) != 0) continue;
        for (int j = i; j + 1 < g_plan.server_only_count; j++) {
            snprintf(g_plan.server_only_ids[j], XBOX_TITLE_ID_LEN + 1, "%s",
                     g_plan.server_only_ids[j + 1]);
            if (g_plan.server_only_names) {
                snprintf(g_plan.server_only_names[j], XBOX_NAME_MAX, "%s",
                         g_plan.server_only_names[j + 1]);
            }
        }
        g_plan.server_only_count--;
        g_plan.server_only_ids[g_plan.server_only_count][0] = '\0';
        if (g_plan.server_only_names) {
            g_plan.server_only_names[g_plan.server_only_count][0] = '\0';
        }
        i--;
    }
}

static int plan_contains_ok(const char *tid)
{
    if (!g_plan.up_to_date_ids || !tid) return 0;
    for (int i = 0; i < g_plan.up_to_date_count; i++) {
        if (strcmp(g_plan.up_to_date_ids[i], tid) == 0) return 1;
    }
    return 0;
}

static void plan_mark_title_ok(const char *tid)
{
    if (!g_plan_loaded || !tid) return;

    plan_remove_from_bucket(g_plan.upload_ids, &g_plan.upload_count, tid);
    plan_remove_from_bucket(g_plan.download_ids, &g_plan.download_count, tid);
    plan_remove_from_bucket(g_plan.conflict_ids, &g_plan.conflict_count, tid);
    plan_remove_from_bucket(g_plan.up_to_date_ids, &g_plan.up_to_date_count, tid);
    plan_remove_from_server_only(tid);

    if (!plan_contains_ok(tid) && g_plan.up_to_date_count < SYNC_MAX_TITLES) {
        snprintf(g_plan.up_to_date_ids[g_plan.up_to_date_count],
                 XBOX_TITLE_ID_LEN + 1, "%s", tid);
        g_plan.up_to_date_count++;
    }
}

static void rescan_local_preserve_plan(void)
{
    saves_scan(&g_list);
    resolve_local_names();
}

static const char *manual_op_name(UiKey op)
{
    return op == UI_KEY_X ? "upload" : "download";
}

static void draw_confirm_dialog(int cursor, int scroll,
                                UiKey op,
                                const char *tid,
                                const XboxSaveTitle *local,
                                const char *local_hash,
                                const NetworkSaveMeta *server)
{
    redraw(cursor, scroll);

    UiColor border = { 0x7E, 0xE8, 0xA1, 0xFF };
    UiColor panel  = { 0x08, 0x1A, 0x13, 0xFF };
    UiColor head   = { 0x10, 0x36, 0x25, 0xFF };
    UiColor dark   = { 0x0B, 0x18, 0x10, 0xFF };

    const int x = 44;
    const int y = 96;
    const int w = 552;
    const int h = 292;
    ui_rect(x - 2, y - 2, w + 4, h + 4, border);
    ui_rect(x, y, w, h, panel);
    ui_rect(x, y, w, 34, head);

    char line[180];
    snprintf(line, sizeof(line), "Confirm %s", manual_op_name(op));
    ui_text(x + 14, y + 7, line, UI_ACCENT, UI_FONT_BODY);

    char display_name[56];
    if (local && local->name[0]) {
        short_str(local->name, display_name, 46);
    } else {
        snprintf(display_name, sizeof(display_name), "%s", tid);
    }
    snprintf(line, sizeof(line), "Title %s  %s", tid, display_name);
    ui_text(x + 14, y + 44, line, UI_TEXT, UI_FONT_SMALL);

    char local_hash_short[40];
    char server_hash_short[40];
    char local_ts[24];
    char server_ts[24];
    short_hash(local_hash, local_hash_short, sizeof(local_hash_short));
    short_hash(server && server->exists ? server->save_hash : "",
               server_hash_short, sizeof(server_hash_short));
    fmt_timestamp(local ? local->latest_mtime : 0, local_ts, sizeof(local_ts));
    fmt_timestamp(server && server->exists ? server->client_timestamp : 0,
                  server_ts, sizeof(server_ts));

    int ly = y + 72;
    if (local) {
        char kb[24];
        snprintf(line, sizeof(line), "Local: %s, %d file(s)",
                 fmt_kb(local->total_size, kb, sizeof(kb)),
                 local->file_count);
    } else {
        snprintf(line, sizeof(line), "Local: not present on this Xbox");
    }
    ui_text(x + 14, ly, line, UI_TEXT, UI_FONT_SMALL);
    ly += 24;
    snprintf(line, sizeof(line), "Local hash: %s", local_hash_short);
    ui_text(x + 14, ly, line, UI_TEXT_DIM, UI_FONT_SMALL);
    ly += 24;
    snprintf(line, sizeof(line), "Local timestamp: %s", local_ts);
    ui_text(x + 14, ly, line, UI_TEXT_DIM, UI_FONT_SMALL);

    ly += 34;
    if (server && server->exists) {
        char kb[24];
        snprintf(line, sizeof(line), "Server: %s, %d file(s)",
                 fmt_kb(server->save_size, kb, sizeof(kb)),
                 server->file_count);
    } else {
        snprintf(line, sizeof(line), "Server: no save found");
    }
    ui_text(x + 14, ly, line, UI_TEXT, UI_FONT_SMALL);
    ly += 24;
    snprintf(line, sizeof(line), "Server hash: %s", server_hash_short);
    ui_text(x + 14, ly, line, UI_TEXT_DIM, UI_FONT_SMALL);
    ly += 24;
    snprintf(line, sizeof(line), "Server timestamp: %s", server_ts);
    ui_text(x + 14, ly, line, UI_TEXT_DIM, UI_FONT_SMALL);
    ly += 24;
    snprintf(line, sizeof(line), "Server uploaded: %s",
             (server && server->exists && server->server_timestamp[0])
                 ? server->server_timestamp
                 : "n/a");
    ui_text(x + 14, ly, line, UI_TEXT_DIM, UI_FONT_SMALL);

    ui_rect(x + 14, y + h - 44, 150, 28, UI_STATUS_OK);
    ui_text(x + 36, y + h - 39, "A confirm", dark, UI_FONT_SMALL);
    ui_rect(x + 184, y + h - 44, 130, 28, UI_STATUS_CONFLICT);
    ui_text(x + 210, y + h - 39, "B cancel", dark, UI_FONT_SMALL);

    ui_present();
}

static int confirm_manual_transfer(int cursor, int scroll,
                                   UiKey op,
                                   const char *tid,
                                   XboxSaveTitle *local)
{
    char local_hash[XBOX_SAVE_HASH_HEX_LEN + 1] = "";

    if (local) {
        uint8_t raw[32];
        if (!state_get_cached_save_hash(local, local_hash)) {
            set_status_kind(UI_STATUS_BUSY_KIND, "Computing local hash: %s", tid);
            redraw(cursor, scroll);
            if (bundle_compute_save_hash(local, raw, local_hash) != 0) {
                set_status_kind(UI_STATUS_ERROR_KIND,
                                "Could not hash local save: %s", tid);
                return 0;
            }
            state_set_cached_save_hash(local, local_hash);
        }
    }

    NetworkSaveMeta server;
    set_status_kind(UI_STATUS_BUSY_KIND, "Fetching server metadata: %s", tid);
    redraw(cursor, scroll);
    if (network_get_save_meta(&g_cfg, tid, &server) != 0) {
        const char *ne = network_last_error();
        set_status_kind(UI_STATUS_ERROR_KIND, "%s",
                        (ne && ne[0]) ? ne : "Server metadata fetch failed");
        return 0;
    }
    if (op == UI_KEY_Y && !server.exists) {
        set_status_kind(UI_STATUS_ERROR_KIND,
                        "No server save to download: %s", tid);
        return 0;
    }

    draw_confirm_dialog(cursor, scroll, op, tid, local, local_hash, &server);
    while (1) {
        ui_pump();
        UiKey k = ui_poll_key();
        if (k == UI_KEY_A) {
            return 1;
        }
        if (k == UI_KEY_B || k == UI_KEY_BACK || k == UI_KEY_START) {
            set_status_kind(UI_STATUS_INFO_KIND, "%s cancelled: %s",
                            op == UI_KEY_X ? "Upload" : "Download", tid);
            return 0;
        }
        ui_sleep(20);
    }
}

static void resolve_local_names(void)
{
    int n = g_list.title_count;
    if (n <= 0) return;
    char (*ids)[XBOX_TITLE_ID_LEN + 1] = (char (*)[XBOX_TITLE_ID_LEN + 1])
        calloc(n, XBOX_TITLE_ID_LEN + 1);
    char (*names)[XBOX_NAME_MAX] = (char (*)[XBOX_NAME_MAX])
        calloc(n, XBOX_NAME_MAX);
    if (!ids || !names) { free(ids); free(names); return; }

    for (int i = 0; i < n; i++) {
        snprintf(ids[i], XBOX_TITLE_ID_LEN + 1, "%s",
                 g_list.titles[i].title_id);
    }
    if (network_fetch_names(&g_cfg, ids, n, names) == 0) {
        for (int i = 0; i < n; i++) {
            snprintf(g_list.titles[i].name, XBOX_NAME_MAX, "%s", names[i]);
        }
    }
    free(ids);
    free(names);
}

static void refresh_plan(void)
{
    if (g_plan_loaded) sync_plan_free(&g_plan);
    g_plan_loaded = 0;
    set_status_kind(UI_STATUS_BUSY_KIND, "Fetching sync plan...");
    if (sync_compute_plan(&g_cfg, &g_list, &g_plan) != 0) {
        const char *ne = network_last_error();
        set_status_kind(UI_STATUS_ERROR_KIND, "Plan fetch failed%s%s",
                        (ne && ne[0]) ? ": " : "",
                        (ne && ne[0]) ? ne : "");
        return;
    }
    g_plan_loaded = 1;
    set_status_kind(UI_STATUS_SUCCESS_KIND, "Plan loaded: up %d  down %d  new %d",
                    g_plan.upload_count,
                    g_plan.download_count,
                    g_plan.server_only_count);
}

static void rescan(void)
{
    set_status_kind(UI_STATUS_BUSY_KIND, "Rescanning E:\\UDATA...");
    saves_scan(&g_list);
    resolve_local_names();
    if (g_plan_loaded) { sync_plan_free(&g_plan); g_plan_loaded = 0; }
    set_status_kind(UI_STATUS_SUCCESS_KIND, "Scan complete: found %d title(s)",
                    g_list.title_count);
}

static void clear_hash_cache(void)
{
    if (state_clear_hash_cache() != 0) {
        set_status_kind(UI_STATUS_ERROR_KIND, "Could not clear hash cache");
        return;
    }
    if (g_plan_loaded) { sync_plan_free(&g_plan); g_plan_loaded = 0; }
    set_status_kind(UI_STATUS_SUCCESS_KIND,
                    "Hash cache cleared; press LB to refresh plan");
}

static void run_sync_one(int cursor, int scroll, UiKey op)
{
    const char *tid = NULL;
    XboxSaveTitle *local = NULL;
    if (row_to_title(cursor, &tid, &local) != 0) {
        set_status_kind(UI_STATUS_ERROR_KIND, "No row selected");
        return;
    }
    char tid_copy[XBOX_TITLE_ID_LEN + 1];
    snprintf(tid_copy, sizeof(tid_copy), "%s", tid);
    tid = tid_copy;

    TitleStatus prior_status = g_plan_loaded
                                   ? sync_plan_status(&g_plan, tid)
                                   : TITLE_STATUS_UNKNOWN;
    int rc = -1;
    switch (op) {
    case UI_KEY_A:
        if (!g_plan_loaded) {
            set_status_kind(UI_STATUS_ERROR_KIND, "Refresh plan first (LB)");
            return;
        }
        if (prior_status == TITLE_STATUS_CONFLICT) {
            set_status_kind(UI_STATUS_ERROR_KIND,
                            "Conflict: use X upload or Y download for %s",
                            tid);
            return;
        }
        set_status_kind(UI_STATUS_BUSY_KIND, "Smart sync in progress: %s", tid);
        redraw(cursor, scroll);
        rc = sync_one_smart(&g_cfg, &g_list, tid, &g_plan);
        break;
    case UI_KEY_X:
        if (!local) {
            set_status_kind(UI_STATUS_ERROR_KIND, "No local copy to upload");
            return;
        }
        if (!confirm_manual_transfer(cursor, scroll, op, tid, local)) {
            return;
        }
        set_status_kind(UI_STATUS_BUSY_KIND, "Uploading %s...", tid);
        redraw(cursor, scroll);
        rc = sync_one_upload_force(&g_cfg, local);
        break;
    case UI_KEY_Y:
        if (!confirm_manual_transfer(cursor, scroll, op, tid, local)) {
            return;
        }
        set_status_kind(UI_STATUS_BUSY_KIND, "Downloading %s...", tid);
        redraw(cursor, scroll);
        rc = sync_one_download(&g_cfg, &g_list, tid);
        break;
    default: return;
    }
    if (rc == 0) {
        if (op == UI_KEY_Y ||
            prior_status == TITLE_STATUS_NEEDS_DOWNLOAD ||
            prior_status == TITLE_STATUS_SERVER_ONLY) {
            rescan_local_preserve_plan();
        }
        plan_mark_title_ok(tid);
        set_status_kind(UI_STATUS_SUCCESS_KIND, "%s complete: %s",
                        op == UI_KEY_A ? "Smart sync" :
                        op == UI_KEY_X ? "Upload" : "Download",
                        tid);
    } else {
        const char *ne = network_last_error();
        if (ne && ne[0]) {
            set_status_kind(UI_STATUS_ERROR_KIND, "%s", ne);
        } else {
            set_status_kind(UI_STATUS_ERROR_KIND, "%s failed: %s",
                            op == UI_KEY_A ? "Smart sync" :
                            op == UI_KEY_X ? "Upload" : "Download",
                            tid);
        }
    }
}

// Cursor + scroll forwarded via the user pointer so the progress callback
// can repaint a coherent screen between titles.
typedef struct { int cursor; int scroll; } RedrawCtx;

static void sync_progress_cb(const char *msg, int done, int total,
                             void *user)
{
    RedrawCtx *rc = (RedrawCtx *)user;
    (void)done; (void)total;
    set_status_kind(UI_STATUS_BUSY_KIND, "%s", msg);
    redraw(rc->cursor, rc->scroll);
    // Keep SDL events drained so the controller stays responsive and
    // the OS doesn't think we're locked.
    ui_pump();
}

static void run_sync_all(int cursor, int scroll)
{
    if (!g_plan_loaded) {
        set_status_kind(UI_STATUS_ERROR_KIND, "Refresh plan first (LB)");
        return;
    }
    set_status_kind(UI_STATUS_BUSY_KIND, "Sync all: starting...");
    redraw(cursor, scroll);

    RedrawCtx rc = { cursor, scroll };
    SyncSummary s;
    sync_run_all(&g_cfg, &g_list, &g_plan, sync_progress_cb, &rc, &s);

    int failures = s.upload_failed + s.download_failed;
    set_status_kind(failures ? UI_STATUS_ERROR_KIND : UI_STATUS_SUCCESS_KIND,
                    "Sync-all complete: up %d down %d skip %d cflt %d fail %d",
                    s.uploaded, s.downloaded, s.up_to_date,
                    s.conflicts, failures);
    sync_plan_free(&g_plan);
    g_plan_loaded = 0;
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

// Try to bring up just the gamepad subsystem so boot_fail() can offer a
// clean exit. Best-effort: returns 0 if a pad poll loop is feasible.
static int boot_pad_init(void)
{
    if (SDL_InitSubSystem(SDL_INIT_GAMECONTROLLER) != 0) return -1;
    int n = SDL_NumJoysticks();
    for (int i = 0; i < n; i++) {
        if (SDL_IsGameController(i)) SDL_GameControllerOpen(i);
    }
    return 0;
}

static void boot_fail(const char *msg)
{
    // Keep prior diagnostic prints visible - just append our message.
    debugPrint("\n----\n%s\n\nPress START or BACK to return to dashboard.\n",
               msg);

    int have_pad = (boot_pad_init() == 0);
    while (1) {
        if (have_pad) {
            SDL_GameControllerUpdate();
            SDL_Event e;
            while (SDL_PollEvent(&e)) {
                if (e.type == SDL_CONTROLLERBUTTONDOWN) {
                    if (e.cbutton.button == SDL_CONTROLLER_BUTTON_START ||
                        e.cbutton.button == SDL_CONTROLLER_BUTTON_BACK) {
                        HalReturnToFirmware(HalQuickRebootRoutine);
                    }
                }
                if (e.type == SDL_CONTROLLERDEVICEADDED) {
                    SDL_GameControllerOpen(e.cdevice.which);
                }
            }
        }
        Sleep(50);
    }
}

int main(void)
{
    XVideoSetMode(640, 480, 32, REFRESH_DEFAULT);

    if (saves_init() != 0)              boot_fail("ERROR: failed to mount E:\\");

    char cfg_err[256] = {0};
    if (config_load(&g_cfg, cfg_err, sizeof(cfg_err)) != 0) boot_fail(cfg_err);

    saves_scan(&g_list);

    if (network_init(&g_cfg) != 0) {
        const char *ne = network_last_error();
        char m[240];
        snprintf(m, sizeof(m), "ERROR: %s",
                 (ne && ne[0]) ? ne : "network init failed");
        boot_fail(m);
    }
    network_local_ip(g_local_ip, sizeof(g_local_ip));

    char st[128] = {0};
    int code = network_status_check(&g_cfg, st, sizeof(st));
    if (code != 200) {
        char m[160];
        snprintf(m, sizeof(m), "ERROR: server status HTTP %d", code);
        boot_fail(m);
    }
    snprintf(g_server_text, sizeof(g_server_text), "%s", st);

    resolve_local_names();

    {
        char ui_err[256] = {0};
        if (ui_init(ui_err, sizeof(ui_err)) != 0) {
            char m[320];
            snprintf(m, sizeof(m), "ERROR: SDL init failed\n%s", ui_err);
            boot_fail(m);
        }
    }

    int cursor = 0;
    int scroll = 0;
    redraw(cursor, scroll);

    while (1) {
        ui_pump();
        UiKey k = ui_poll_key();

        if (k == UI_KEY_START || k == UI_KEY_BACK) {
            ui_shutdown();
            HalReturnToFirmware(HalQuickRebootRoutine);
        }

        int redraw_needed = 0;
        switch (k) {
        case UI_KEY_NONE: break;
        case UI_KEY_UP:
            if (cursor > 0) cursor--;
            if (cursor < scroll) scroll = cursor;
            redraw_needed = 1; break;
        case UI_KEY_DOWN: {
            int max = total_rows();
            if (cursor + 1 < max) cursor++;
            if (cursor >= scroll + LIST_VISIBLE)
                scroll = cursor - LIST_VISIBLE + 1;
            redraw_needed = 1; break;
        }
        case UI_KEY_LEFT:
            if (page_rows(-1, &cursor, &scroll)) redraw_needed = 1;
            break;
        case UI_KEY_RIGHT:
            if (page_rows(1, &cursor, &scroll)) redraw_needed = 1;
            break;
        case UI_KEY_LB:
            // Refresh plan: rescan local UDATA first (catches new saves
            // from games run in this session), then ask the server.
            rescan();
            cursor = 0; scroll = 0;
            redraw(cursor, scroll);
            refresh_plan();
            redraw_needed = 1; break;
        case UI_KEY_RB:
            redraw(cursor, scroll);
            run_sync_all(cursor, scroll);
            clamp_cursor_scroll(&cursor, &scroll);
            redraw_needed = 1; break;
        case UI_KEY_B:
            clear_hash_cache();
            redraw_needed = 1; break;
        case UI_KEY_A:
        case UI_KEY_X:
        case UI_KEY_Y:
            run_sync_one(cursor, scroll, k);
            clamp_cursor_scroll(&cursor, &scroll);
            redraw_needed = 1; break;
        default: break;
        }

        if (redraw_needed) redraw(cursor, scroll);
        ui_sleep(20);
    }
    return 0;
}
