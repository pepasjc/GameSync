/*
 * PSP Save Sync - UI (using pspDebugScreen)
 *
 * Simple text-based UI using PSP's debug screen.
 * A proper graphical UI using gu/libpspgum could be added later.
 */

#include <stdio.h>
#include <stdarg.h>
#include <string.h>

#include <pspdebug.h>
#include <pspctrl.h>
#include <pspkernel.h>

#include "ui.h"

/* PSP screen is 480x272, debug screen is 60 cols x ~34 rows at default font */
#define SCREEN_COLS     60
#define LIST_START_ROW  3
#define STATUS_ROW      33

void ui_init(void) {
    pspDebugScreenInit();
    pspDebugScreenClear();
}

void ui_clear(void) {
    pspDebugScreenClear();
}

void ui_status(const char *fmt, ...) {
    char buf[256];
    va_list args;
    va_start(args, fmt);
    vsnprintf(buf, sizeof(buf), fmt, args);
    va_end(args);
    pspDebugScreenSetXY(0, STATUS_ROW);
    pspDebugScreenPrintf("%s\n", buf);
}

/* Wait until no buttons are held, then return 0.
 * Call this before starting any new input loop to guarantee a clean state. */
static void drain_buttons(void) {
    SceCtrlData pad;
    do {
        sceCtrlReadBufferPositive(&pad, 1);
        sceKernelDelayThread(16000);
    } while (pad.Buttons != 0);
}

void ui_message(const char *fmt, ...) {
    char buf[512];
    va_list args;
    va_start(args, fmt);
    vsnprintf(buf, sizeof(buf), fmt, args);
    va_end(args);
    pspDebugScreenClear();
    pspDebugScreenSetXY(0, 1);
    pspDebugScreenPrintf("%s", buf);

    pspDebugScreenPrintf("\n\nPress X to continue\n");

    drain_buttons();

    SceCtrlData pad;
    uint32_t prev = 0;
    while (1) {
        sceCtrlReadBufferPositive(&pad, 1);
        uint32_t just = pad.Buttons & ~prev;
        prev = pad.Buttons;
        if (just & PSP_CTRL_CROSS) break;
        sceKernelDelayThread(16000);
    }
}

void ui_draw_list(const SyncState *state, int selected, int scroll) {
    pspDebugScreenSetXY(0, 0);
    pspDebugScreenPrintf("=== PSP Save Sync v%s ===", APP_VERSION);

    pspDebugScreenSetXY(0, 1);
    pspDebugScreenPrintf("%d saves | X:Sync Sq:Upload Tri:Download Sel:ScanAll",
                         state->num_titles);

    pspDebugScreenSetXY(0, 2);
    pspDebugScreenPrintf("-----------------------------------------------");

    int visible = 28;  /* rows available for list */
    int end = scroll + visible;
    if (end > state->num_titles) end = state->num_titles;

    for (int i = scroll; i < end; i++) {
        pspDebugScreenSetXY(0, LIST_START_ROW + (i - scroll));
        const TitleInfo *t = &state->titles[i];

        const char *cursor = (i == selected) ? ">" : " ";
        const char *plat = t->is_psx ? "PS1" : "PSP";
        const char *display = (t->name[0] && strcmp(t->name, t->game_id) != 0)
                              ? t->name : t->game_id;
        char line[56];
        snprintf(line, sizeof(line), "%s %-4s %s%s", cursor, plat, display,
                 t->server_only ? " [srv]" : "");
        pspDebugScreenPrintf("%-55s", line);
    }

    /* Controls reminder */
    pspDebugScreenSetXY(0, LIST_START_ROW + visible + 1);
    pspDebugScreenPrintf("Up/Down: navigate | L/R: page | HOME: exit");
}

bool ui_confirm(const TitleInfo *title, SyncAction action,
                const char *server_hash, uint32_t server_size,
                const char *server_last_sync) {
    pspDebugScreenClear();
    pspDebugScreenSetXY(0, 1);

    const char *action_str =
        action == SYNC_UPLOAD   ? "UPLOAD to server" :
        action == SYNC_DOWNLOAD ? "DOWNLOAD from server" :
        action == SYNC_CONFLICT ? "CONFLICT - manual decision needed" :
        "UP TO DATE";

    pspDebugScreenPrintf("Game:   %s (%s)\n\n", title->name, title->game_id);
    pspDebugScreenPrintf("Action: %s\n\n", action_str);
    if (title->server_only)
        pspDebugScreenPrintf("Local:  (not on device yet)\n");
    else
        pspDebugScreenPrintf("Local:  %u bytes\n", title->total_size);
    if (server_hash && server_hash[0]) {
        pspDebugScreenPrintf("Server: %u bytes\n", server_size);
        if (server_last_sync && server_last_sync[0]) {
            /* Format "2024-01-15T14:30:00..." -> "2024-01-15 14:30" */
            char date_str[20] = "";
            if (strlen(server_last_sync) >= 16 && server_last_sync[10] == 'T')
                snprintf(date_str, sizeof(date_str), "%.10s %.5s",
                         server_last_sync, server_last_sync + 11);
            else
                snprintf(date_str, sizeof(date_str), "%.16s", server_last_sync);
            pspDebugScreenPrintf("Date:   %s\n", date_str);
        }
    } else {
        pspDebugScreenPrintf("Server: (no save)\n");
    }

    /* Always drain before starting input so a held button from the previous
     * screen doesn't immediately trigger a choice here. */
    drain_buttons();

    if (action == SYNC_UP_TO_DATE) {
        pspDebugScreenPrintf("\nAlready up to date. Press X.\n");
        SceCtrlData pad;
        uint32_t prev = 0;
        while (1) {
            sceCtrlReadBufferPositive(&pad, 1);
            uint32_t just = pad.Buttons & ~prev;
            prev = pad.Buttons;
            if (just & (PSP_CTRL_CROSS | PSP_CTRL_CIRCLE)) break;
            sceKernelDelayThread(16000);
        }
        return false;
    }

    pspDebugScreenPrintf("\nX: Confirm | O: Cancel\n");

    SceCtrlData pad;
    uint32_t prev = 0;
    while (1) {
        sceCtrlReadBufferPositive(&pad, 1);
        uint32_t just = pad.Buttons & ~prev;
        prev = pad.Buttons;
        if (just & PSP_CTRL_CROSS)  return true;
        if (just & PSP_CTRL_CIRCLE) return false;
        sceKernelDelayThread(16000);
    }
}

void ui_draw_config(const SyncState *state) {
    pspDebugScreenSetXY(0, 0);
    pspDebugScreenPrintf("=== Config ===\n\n");
    pspDebugScreenPrintf("Server:     %s\n", state->server_url);
    pspDebugScreenPrintf("API Key:    %s\n",
        state->api_key[0] ? "(set)" : "(not set)");
    pspDebugScreenPrintf("WiFi AP:    %d\n", state->wifi_ap_index);
    pspDebugScreenPrintf("Console ID: %s\n", state->console_id);
    pspDebugScreenPrintf("WiFi:       %s\n",
        state->wifi_connected ? "Connected" : "Not connected");
    pspDebugScreenPrintf("\nEdit config.txt at:\n%s\n", CONFIG_PATH);
}

/* ============================================================
 * ROM Catalog + Downloads views (text-mode pspDebugScreen)
 * ============================================================ */

#define LIST_VISIBLE_ROWS 22

static const char *_view_names[APP_VIEW_COUNT] = {
    "Saves", "ROM Catalog", "Downloads"
};

static void draw_tab_strip(AppView current) {
    pspDebugScreenSetXY(0, 0);
    pspDebugScreenPrintf("[");
    for (int i = 0; i < APP_VIEW_COUNT; i++) {
        if (i == (int)current) pspDebugScreenPrintf("*");
        pspDebugScreenPrintf("%s", _view_names[i]);
        if (i == (int)current) pspDebugScreenPrintf("*");
        if (i + 1 < APP_VIEW_COUNT) pspDebugScreenPrintf(" | ");
    }
    int next = ((int)current + 1) % APP_VIEW_COUNT;
    pspDebugScreenPrintf("]  START -> %s\n", _view_names[next]);
}

static void format_size_short(uint64_t bytes, char *out, size_t out_size) {
    if (bytes >= (1ULL << 30)) {
        snprintf(out, out_size, "%.2fG",
                 (double)bytes / (double)(1ULL << 30));
    } else if (bytes >= (1ULL << 20)) {
        snprintf(out, out_size, "%.1fM",
                 (double)bytes / (double)(1ULL << 20));
    } else if (bytes >= (1ULL << 10)) {
        snprintf(out, out_size, "%.0fK",
                 (double)bytes / (double)(1ULL << 10));
    } else {
        snprintf(out, out_size, "%lluB", (unsigned long long)bytes);
    }
}

static void format_bps_short(uint64_t bps, char *out, size_t out_size) {
    if (bps == 0) { snprintf(out, out_size, "--"); return; }
    if (bps >= (1ULL << 20)) {
        snprintf(out, out_size, "%.1fMB/s",
                 (double)bps / (double)(1ULL << 20));
    } else if (bps >= (1ULL << 10)) {
        snprintf(out, out_size, "%.0fKB/s",
                 (double)bps / (double)(1ULL << 10));
    } else {
        snprintf(out, out_size, "%lluB/s", (unsigned long long)bps);
    }
}

static void format_eta_short(uint64_t remaining, uint64_t bps,
                             char *out, size_t out_size) {
    if (bps == 0 || remaining == 0) {
        snprintf(out, out_size, "--"); return;
    }
    uint64_t s = remaining / bps;
    if (s >= 3600)
        snprintf(out, out_size, "%lluh%02llum",
                 (unsigned long long)(s / 3600),
                 (unsigned long long)((s % 3600) / 60));
    else if (s >= 60)
        snprintf(out, out_size, "%llum%02llus",
                 (unsigned long long)(s / 60),
                 (unsigned long long)(s % 60));
    else
        snprintf(out, out_size, "%llus", (unsigned long long)s);
}

static const DownloadEntry *find_dl_const(const DownloadList *list,
                                          const char *rom_id) {
    if (!list || !rom_id) return NULL;
    for (int i = 0; i < list->count; i++) {
        if (strcmp(list->items[i].rom_id, rom_id) == 0)
            return &list->items[i];
    }
    return NULL;
}

void ui_draw_rom_catalog(const RomCatalog *catalog,
                         const DownloadList *downloads,
                         const char *current_system,
                         int selected, int scroll_offset,
                         const char *status_line,
                         AppView current_view) {
    pspDebugScreenClear();
    draw_tab_strip(current_view);

    pspDebugScreenSetXY(0, 1);
    pspDebugScreenPrintf("ROM Catalog [%s]   L1/R1: switch system\n",
                         current_system ? current_system : "PSP");
    if (status_line && status_line[0]) {
        pspDebugScreenPrintf("%s\n", status_line);
    } else {
        pspDebugScreenPrintf("\n");
    }

    int total = catalog ? catalog->count : 0;
    if (total == 0) {
        pspDebugScreenSetXY(0, LIST_START_ROW);
        if (catalog && catalog->last_error[0]) {
            pspDebugScreenPrintf("ERROR: %s\n", catalog->last_error);
        } else {
            pspDebugScreenPrintf("(no ROMs in this catalog yet)\n");
        }
    } else {
        int end = scroll_offset + LIST_VISIBLE_ROWS;
        if (end > total) end = total;
        for (int i = scroll_offset; i < end; i++) {
            const RomEntry *r = &catalog->items[i];
            pspDebugScreenSetXY(0, LIST_START_ROW + (i - scroll_offset));

            const char *tag = "    ";
            const DownloadEntry *dl = find_dl_const(downloads, r->rom_id);
            if (dl) {
                switch (dl->status) {
                    case DL_STATUS_QUEUED:    tag = "Q   "; break;
                    case DL_STATUS_ACTIVE:    tag = "ACT "; break;
                    case DL_STATUS_PAUSED:    tag = "PAUS"; break;
                    case DL_STATUS_COMPLETED: tag = "DONE"; break;
                    case DL_STATUS_ERROR:     tag = "ERR "; break;
                }
            }

            char size_buf[16];
            format_size_short(r->size, size_buf, sizeof(size_buf));

            char marker = (i == selected) ? '>' : ' ';
            const char *display = r->name[0] ? r->name : r->filename;
            /* Multi-disc games appear as a single row (disc 2+ are
             * filtered out at parse time); annotate with the disc
             * count so the user knows the EBOOT will bundle all of
             * them.  44-char column will truncate long names — that's
             * fine, the disc count is what's load-bearing. */
            char label_buf[MAX_TITLE_LEN + 16];
            if (r->disc_total > 1) {
                snprintf(label_buf, sizeof(label_buf),
                         "%s (%d discs)", display, r->disc_total);
                display = label_buf;
            }
            pspDebugScreenPrintf(
                "%c[%s] %-44.44s %7s\n",
                marker, tag, display, size_buf);
        }
    }

    pspDebugScreenSetXY(0, STATUS_ROW);
    pspDebugScreenPrintf(
        "%d/%d  X:queue/start  T:resume  O:refresh  L1/R1:sys",
        selected + 1, total);
}

void ui_draw_downloads(const DownloadList *downloads,
                       int selected, int scroll_offset,
                       const char *status_line,
                       bool active_in_progress,
                       uint64_t active_downloaded,
                       uint64_t active_total,
                       uint64_t active_bps,
                       AppView current_view) {
    pspDebugScreenClear();
    draw_tab_strip(current_view);

    pspDebugScreenSetXY(0, 1);
    pspDebugScreenPrintf("Downloads\n");
    if (status_line && status_line[0]) {
        pspDebugScreenPrintf("%s\n", status_line);
    } else {
        pspDebugScreenPrintf("\n");
    }

    int total = downloads ? downloads->count : 0;
    int row = LIST_START_ROW;

    /* Active panel — header rows describing the in-flight transfer.
     * Squeezed to 4 lines to keep the queue list visible below. */
    if (active_in_progress) {
        const DownloadEntry *active = NULL;
        for (int i = 0; i < total; i++) {
            if (downloads->items[i].status == DL_STATUS_ACTIVE) {
                active = &downloads->items[i];
                break;
            }
        }
        const char *display_name =
            (active && active->name[0]) ? active->name :
            ((active && active->filename[0]) ? active->filename : "(unknown)");
        const char *file = (active && active->target_path[0]) ?
            active->target_path : "";
        const char *base_slash = file ? strrchr(file, '/') : NULL;
        const char *current_basename = base_slash ? base_slash + 1 : file;

        uint64_t off = active_downloaded;
        uint64_t tot = (active_total > 0) ? active_total
                       : (active ? active->total : 0);
        int pct = 0;
        if (tot > 0) {
            pct = (int)((off * 100ULL) / tot);
            if (pct > 100) pct = 100;
        }
        char off_buf[16], tot_buf[16], bps_buf[16], eta_buf[16];
        format_size_short(off, off_buf, sizeof(off_buf));
        format_size_short(tot, tot_buf, sizeof(tot_buf));
        format_bps_short(active_bps, bps_buf, sizeof(bps_buf));
        uint64_t remaining = (tot > off) ? (tot - off) : 0;
        format_eta_short(remaining, active_bps, eta_buf, sizeof(eta_buf));

        pspDebugScreenSetXY(0, row++);
        pspDebugScreenPrintf("Now: %.50s\n", display_name);
        pspDebugScreenSetXY(0, row++);
        pspDebugScreenPrintf("File: %.55s\n", current_basename);
        pspDebugScreenSetXY(0, row++);
        pspDebugScreenPrintf("%3d%%  %7s/%-7s  %9s  ETA %s\n",
                             pct, off_buf, tot_buf, bps_buf, eta_buf);
        pspDebugScreenSetXY(0, row++);
        pspDebugScreenPrintf("[Sq:pause] [O:cancel after pause]\n");
        row++;  /* blank separator */
    }

    if (total == 0) {
        pspDebugScreenSetXY(0, row);
        pspDebugScreenPrintf("(no downloads queued — switch to ROM Catalog)\n");
    } else {
        int rows_avail = STATUS_ROW - 1 - row;
        if (rows_avail > LIST_VISIBLE_ROWS) rows_avail = LIST_VISIBLE_ROWS;
        if (rows_avail < 1) rows_avail = 1;
        int end = scroll_offset + rows_avail;
        if (end > total) end = total;

        for (int i = scroll_offset; i < end; i++) {
            const DownloadEntry *e = &downloads->items[i];
            const char *tag = "    ";
            switch (e->status) {
                case DL_STATUS_QUEUED:    tag = "Q   "; break;
                case DL_STATUS_ACTIVE:    tag = "ACT "; break;
                case DL_STATUS_PAUSED:    tag = "PAUS"; break;
                case DL_STATUS_COMPLETED: tag = "DONE"; break;
                case DL_STATUS_ERROR:     tag = "ERR "; break;
            }
            uint64_t off = e->offset;
            uint64_t tot = e->total;
            if (e->status == DL_STATUS_ACTIVE && active_in_progress) {
                off = active_downloaded;
                if (active_total > 0) tot = active_total;
            }
            int pct = 0;
            if (tot > 0) {
                pct = (int)((off * 100ULL) / tot);
                if (pct > 100) pct = 100;
            }
            char marker = (i == selected) ? '>' : ' ';
            const char *display = e->name[0] ? e->name : e->filename;

            pspDebugScreenSetXY(0, row + (i - scroll_offset));
            pspDebugScreenPrintf(
                "%c[%s] %-40.40s %3d%%\n",
                marker, tag, display, pct);
        }
    }

    pspDebugScreenSetXY(0, STATUS_ROW);
    pspDebugScreenPrintf(
        "%d/%d  X:start/resume  Sq:pause  O:cancel  T:clear done",
        selected + 1, total);
}
