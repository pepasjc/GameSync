/*
 * Vita Save Sync - UI
 *
 * Uses the VitaSDK debugScreen library for text output.
 * This replaces the custom framebuffer/font renderer.
 *
 * Colors via ANSI escape codes:
 *   \e[38;2;R;G;Bm  = set foreground RGB
 *   \e[48;2;R;G;Bm  = set background RGB
 *   \e[0m           = reset colors
 *   \e[H            = cursor home
 *   \e[2J           = clear screen
 *   \e[row;colH     = set cursor position (1-based)
 */

#include <stdio.h>
#include <stdarg.h>
#include <string.h>

#include <psp2/ctrl.h>
#include <psp2/kernel/threadmgr.h>

#include "debugScreen.h"
#include "ui.h"

/* Foreground color presets (RGB) */
#define FG_WHITE    "\e[38;2;255;255;255m"
#define FG_GREEN    "\e[38;2;0;255;0m"
#define FG_YELLOW   "\e[38;2;255;255;0m"
#define FG_RESET    "\e[0m"
#define BG_BLACK    "\e[48;2;0;0;0m"
#define CLR_SCREEN  "\e[H\e[2J"
#define CLR_LINE    "\e[2K"

/* Screen is 960x544, default font is 8x8 → 120x68 chars, or 2x scaled → 60x34 */
#define STATUS_ROW  33   /* last usable row (0-based, 2x font) */
#define LIST_START  3

void ui_init(void) {
    sceCtrlSetSamplingMode(SCE_CTRL_MODE_DIGITAL);
    psvDebugScreenInit();
    /* Scale font 2x for readability */
    PsvDebugScreenFont *font = psvDebugScreenScaleFont2x(psvDebugScreenGetFont());
    if (font) psvDebugScreenSetFont(font);
    /* Clear to black */
    psvDebugScreenClear(0x000000);
}

void ui_term(void) {
    psvDebugScreenFinish();
}

void ui_clear(void) {
    psvDebugScreenClear(0x000000);
}

/* Move cursor to row,col (0-based) */
static void goto_rc(int row, int col) {
    psvDebugScreenPrintf("\e[%d;%dH", row + 1, col + 1);
}

void ui_status(const char *fmt, ...) {
    char buf[256];
    va_list args;
    va_start(args, fmt);
    vsnprintf(buf, sizeof(buf), fmt, args);
    va_end(args);

    goto_rc(STATUS_ROW, 0);
    psvDebugScreenPuts(CLR_LINE);
    psvDebugScreenPuts(FG_YELLOW);
    psvDebugScreenPuts(buf);
    psvDebugScreenPuts(FG_RESET);
}

void ui_message(const char *fmt, ...) {
    char buf[512];
    va_list args;
    va_start(args, fmt);
    vsnprintf(buf, sizeof(buf), fmt, args);
    va_end(args);

    psvDebugScreenPuts(CLR_SCREEN);
    goto_rc(2, 0);
    psvDebugScreenPuts(FG_WHITE);
    psvDebugScreenPuts(buf);
    psvDebugScreenPuts(FG_RESET);

    goto_rc(STATUS_ROW, 0);
    psvDebugScreenPuts(FG_GREEN "Press X to continue" FG_RESET);

    /* Wait for all buttons to be released first, so a button held during
     * a previous dialog (e.g. the confirm screen) doesn't immediately
     * dismiss this message before the user can read it. */
    SceCtrlData pad;
    do {
        sceCtrlReadBufferPositive2(0, &pad, 1);
        sceKernelDelayThread(16000);
    } while (pad.buttons != 0);

    uint32_t prev = 0;
    while (1) {
        sceCtrlReadBufferPositive2(0, &pad, 1);
        uint32_t just = pad.buttons & ~prev;
        prev = pad.buttons;
        if (just & SCE_CTRL_CROSS) break;
        sceKernelDelayThread(16000);
    }
}

static const char *_view_names[APP_VIEW_COUNT] = {
    "Saves", "ROM Catalog", "Downloads"
};

/* Row 0 on every view: which view is active and where START goes. */
static void draw_tab_strip(AppView current) {
    goto_rc(0, 0);
    psvDebugScreenPuts(CLR_LINE);
    psvDebugScreenPuts(FG_GREEN);
    psvDebugScreenPuts("[");
    for (int i = 0; i < APP_VIEW_COUNT; i++) {
        if (i == (int)current) psvDebugScreenPuts(FG_YELLOW "*");
        psvDebugScreenPuts(_view_names[i]);
        if (i == (int)current) psvDebugScreenPuts("*" FG_GREEN);
        if (i + 1 < APP_VIEW_COUNT) psvDebugScreenPuts(" | ");
    }
    int next = ((int)current + 1) % APP_VIEW_COUNT;
    psvDebugScreenPrintf("]  START->%s", _view_names[next]);
    psvDebugScreenPuts(FG_RESET);
}

void ui_draw_list(const SyncState *state, int selected, int scroll) {
    psvDebugScreenPuts(CLR_SCREEN);

    draw_tab_strip(APP_VIEW_SAVES);

    /* Header */
    goto_rc(1, 0);
    const char *type_str =
        (state->scan_vita_saves && state->scan_psp_emu_saves) ? "Vita+PSP" :
        state->scan_vita_saves ? "Vita only" : "PSP emu only";
    psvDebugScreenPrintf("v%s  %d saves [%s] | X:Sync Sq:Up Tri:Dn Sel:All",
                         APP_VERSION, state->num_titles, type_str);

    /* Separator */
    goto_rc(2, 0);
    psvDebugScreenPuts("------------------------------------------------------------");

    /* List */
    int visible = STATUS_ROW - LIST_START - 2;
    int end = scroll + visible;
    if (end > state->num_titles) end = state->num_titles;

    for (int i = scroll; i < end; i++) {
        const TitleInfo *t = &state->titles[i];
        bool sel = (i == selected);
        goto_rc(LIST_START + (i - scroll), 0);
        psvDebugScreenPuts(CLR_LINE);
        if (sel) psvDebugScreenPuts(FG_YELLOW);
        const char *plat = (t->platform == PLATFORM_VITA) ? "VITA" :
                           t->is_psx                      ? "PS1"  : "PSP";
        const char *display = (t->name[0] && strcmp(t->name, t->game_id) != 0)
                              ? t->name : t->game_id;
        char line[56];
        snprintf(line, sizeof(line), "%s %-4s %s%s", sel ? ">" : " ", plat, display,
                 t->server_only ? " [srv]" : "");
        psvDebugScreenPrintf("%-55s", line);
        if (sel) psvDebugScreenPuts(FG_RESET);
    }

    /* Nav hint */
    goto_rc(STATUS_ROW - 1, 0);
    psvDebugScreenPuts("Up/Dn:nav  L/R:page  Start:view  PS:exit");
}

bool ui_confirm(const TitleInfo *title, SyncAction action,
                const char *server_hash, uint32_t server_size,
                const char *server_last_sync) {
    psvDebugScreenPuts(CLR_SCREEN);

    const char *action_str =
        action == SYNC_UPLOAD   ? "UPLOAD to server" :
        action == SYNC_DOWNLOAD ? "DOWNLOAD from server" :
        action == SYNC_CONFLICT ? "CONFLICT - choose action" :
        "UP TO DATE";

    goto_rc(1, 0);
    psvDebugScreenPuts(FG_GREEN);
    psvDebugScreenPrintf("Game:    %s (%s)", title->name, title->game_id);
    psvDebugScreenPuts(FG_RESET);

    goto_rc(2, 0);
    const char *plat_str =
        (title->platform != PLATFORM_PSP_EMU) ? " PS Vita native" :
        title->is_psx                          ? " PS1 (PSone Classic)" :
                                                 " PSP (emulated)";
    psvDebugScreenPrintf("Platform:%s", plat_str);

    goto_rc(3, 0);
    psvDebugScreenPuts(FG_YELLOW);
    psvDebugScreenPrintf("Action:  %s", action_str);
    psvDebugScreenPuts(FG_RESET);

    goto_rc(4, 0);
    if (title->server_only)
        psvDebugScreenPrintf("Local:   (not on device yet)");
    else
        psvDebugScreenPrintf("Local:   %u bytes  (%d files)", title->total_size, title->file_count);

    goto_rc(5, 0);
    if (server_hash && server_hash[0]) {
        psvDebugScreenPrintf("Server:  %u bytes", server_size);
        if (server_last_sync && server_last_sync[0]) {
            /* Format "2024-01-15T14:30:00..." -> "2024-01-15 14:30" */
            char date_str[20] = "";
            if (strlen(server_last_sync) >= 16 && server_last_sync[10] == 'T')
                snprintf(date_str, sizeof(date_str), "%.10s %.5s",
                         server_last_sync, server_last_sync + 11);
            else
                snprintf(date_str, sizeof(date_str), "%.16s", server_last_sync);
            goto_rc(6, 0);
            psvDebugScreenPrintf("Date:    %s", date_str);
        }
    } else {
        psvDebugScreenPuts("Server:  (no save)");
    }

    /* Drain any held buttons so the button that opened this confirm screen
     * (e.g. X from the game list) doesn't immediately trigger a choice here. */
    {
        SceCtrlData _pad;
        do {
            sceCtrlReadBufferPositive2(0, &_pad, 1);
            sceKernelDelayThread(16000);
        } while (_pad.buttons != 0);
    }

    if (action == SYNC_UP_TO_DATE) {
        goto_rc(8, 0);
        psvDebugScreenPuts("Already up to date. Press X.");

        SceCtrlData pad;
        uint32_t prev = 0;
        while (1) {
            sceCtrlReadBufferPositive2(0, &pad, 1);
            uint32_t just = pad.buttons & ~prev;
            prev = pad.buttons;
            if (just & (SCE_CTRL_CROSS | SCE_CTRL_CIRCLE)) break;
            sceKernelDelayThread(16000);
        }
        return false;
    }

    goto_rc(9, 0);
    psvDebugScreenPuts(FG_GREEN "X: Confirm  |  O: Cancel" FG_RESET);

    SceCtrlData pad;
    uint32_t prev = 0;
    while (1) {
        sceCtrlReadBufferPositive2(0, &pad, 1);
        uint32_t just = pad.buttons & ~prev;
        prev = pad.buttons;
        if (just & SCE_CTRL_CROSS)  return true;
        if (just & SCE_CTRL_CIRCLE) return false;
        sceKernelDelayThread(16000);
    }
}

void ui_draw_config(const SyncState *state) {
    psvDebugScreenPuts(CLR_SCREEN);
    goto_rc(0, 0);
    psvDebugScreenPuts(FG_GREEN "=== Config ===" FG_RESET);
    goto_rc(2, 0); psvDebugScreenPrintf("Server:     %s", state->server_url);
    goto_rc(3, 0); psvDebugScreenPrintf("API Key:    %s", state->api_key[0] ? "(set)" : "(not set)");
    goto_rc(4, 0); psvDebugScreenPrintf("Console ID: %s", state->console_id);
    goto_rc(5, 0); psvDebugScreenPrintf("WiFi:       %s", state->network_connected ? "Connected" : "Not connected");
    goto_rc(6, 0); psvDebugScreenPrintf("Scan Vita:  %s", state->scan_vita_saves ? "Yes" : "No");
    goto_rc(7, 0); psvDebugScreenPrintf("Scan PSP:   %s", state->scan_psp_emu_saves ? "Yes" : "No");
    goto_rc(9, 0); psvDebugScreenPrintf("Edit config at: %s", CONFIG_PATH);
}

/* ============================================================
 * ROM Catalog + Downloads views
 * ============================================================ */

/* Row 0 is the tab strip, 1 the view header, 2 the status line; the
 * list runs from LIST_START_ROW to just above the nav hint. */
#define LIST_START_ROW     3
#define LIST_VISIBLE_ROWS  (STATUS_ROW - 2 - LIST_START_ROW)
#define SCREEN_COLS        60

#define KIB (1024ULL)
#define MIB (1024ULL * 1024ULL)
#define GIB (1024ULL * 1024ULL * 1024ULL)

/* Print a line at (row, 0), padded to the full width so whatever was
 * there before is overwritten without a screen clear. */
static void put_row(int row, const char *text) {
    goto_rc(row, 0);
    psvDebugScreenPrintf("%-*.*s", SCREEN_COLS - 1, SCREEN_COLS - 1, text);
}

static void format_size_short(uint64_t bytes, char *out, size_t out_size) {
    if (bytes >= GIB)
        snprintf(out, out_size, "%.2fG", (double)bytes / (double)GIB);
    else if (bytes >= MIB)
        snprintf(out, out_size, "%.1fM", (double)bytes / (double)MIB);
    else if (bytes >= KIB)
        snprintf(out, out_size, "%.0fK", (double)bytes / (double)KIB);
    else
        snprintf(out, out_size, "%lluB", (unsigned long long)bytes);
}

static void format_bps_short(uint64_t bps, char *out, size_t out_size) {
    if (bps == 0) { snprintf(out, out_size, "--"); return; }
    if (bps >= MIB)
        snprintf(out, out_size, "%.1fMB/s", (double)bps / (double)MIB);
    else if (bps >= KIB)
        snprintf(out, out_size, "%.0fKB/s", (double)bps / (double)KIB);
    else
        snprintf(out, out_size, "%lluB/s", (unsigned long long)bps);
}

static void format_eta_short(uint64_t remaining, uint64_t bps,
                             char *out, size_t out_size) {
    if (bps == 0 || remaining == 0) { snprintf(out, out_size, "--"); return; }
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

static const char *status_tag(DownloadStatus s) {
    switch (s) {
        case DL_STATUS_QUEUED:    return "Q   ";
        case DL_STATUS_ACTIVE:    return "ACT ";
        case DL_STATUS_PAUSED:    return "PAUS";
        case DL_STATUS_COMPLETED: return "DONE";
        case DL_STATUS_ERROR:     return "ERR ";
    }
    return "    ";
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
    psvDebugScreenPuts(CLR_SCREEN);
    draw_tab_strip(current_view);

    char line[SCREEN_COLS + MAX_TITLE_LEN];
    snprintf(line, sizeof(line), "ROM Catalog [%s]   L/R: switch system",
             current_system ? current_system : "PSP");
    put_row(1, line);
    put_row(2, (status_line && status_line[0]) ? status_line : "");

    int total = catalog ? catalog->count : 0;
    if (total == 0) {
        if (catalog && catalog->last_error[0])
            snprintf(line, sizeof(line), "ERROR: %s", catalog->last_error);
        else
            snprintf(line, sizeof(line), "(no ROMs in this catalog yet)");
        put_row(LIST_START_ROW, line);
    } else {
        int end = scroll_offset + LIST_VISIBLE_ROWS;
        if (end > total) end = total;
        for (int i = scroll_offset; i < end; i++) {
            const RomEntry *r = &catalog->items[i];
            const char *tag = "    ";
            const DownloadEntry *dl = find_dl_const(downloads, r->rom_id);
            if (dl) tag = status_tag(dl->status);
            else if (roms_entry_unsupported(r)) tag = "N/A ";

            char size_buf[16];
            format_size_short(r->size, size_buf, sizeof(size_buf));

            bool sel = (i == selected);
            const char *display = r->name[0] ? r->name : r->filename;
            /* Multi-disc games show as one row; say how many discs the
             * EBOOT will carry. */
            char label_buf[MAX_TITLE_LEN + 16];
            if (r->disc_total > 1) {
                snprintf(label_buf, sizeof(label_buf), "%s (%d discs)",
                         display, r->disc_total);
                display = label_buf;
            }
            snprintf(line, sizeof(line), "%c[%s] %-43.43s %7s",
                     sel ? '>' : ' ', tag, display, size_buf);
            if (sel) psvDebugScreenPuts(FG_YELLOW);
            put_row(LIST_START_ROW + (i - scroll_offset), line);
            if (sel) psvDebugScreenPuts(FG_RESET);
        }
    }

    snprintf(line, sizeof(line),
             "%d/%d  X:download  Tri:resume  O:refresh  L/R:system",
             total > 0 ? selected + 1 : 0, total);
    put_row(STATUS_ROW - 1, line);
}

/* The four rows describing the in-flight transfer.  Shared by the full
 * draw and the partial repaint so both render identically. */
static void draw_active_panel(const DownloadList *downloads,
                              uint64_t active_downloaded,
                              uint64_t active_total,
                              uint64_t active_bps) {
    const DownloadEntry *active = NULL;
    int total = downloads ? downloads->count : 0;
    for (int i = 0; i < total; i++) {
        if (downloads->items[i].status == DL_STATUS_ACTIVE) {
            active = &downloads->items[i];
            break;
        }
    }

    const char *display_name =
        (active && active->name[0]) ? active->name :
        ((active && active->filename[0]) ? active->filename : "(unknown)");
    const char *file = (active && active->target_path[0]) ? active->target_path : "";
    const char *base_slash = strrchr(file, '/');
    const char *current_basename = base_slash ? base_slash + 1 : file;

    uint64_t off = active_downloaded;
    uint64_t tot = (active_total > 0) ? active_total : (active ? active->total : 0);
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

    char line[SCREEN_COLS + MAX_TITLE_LEN];
    int row = LIST_START_ROW;
    snprintf(line, sizeof(line), "Now:  %.52s", display_name);
    put_row(row++, line);
    snprintf(line, sizeof(line), "File: %.52s", current_basename);
    put_row(row++, line);
    snprintf(line, sizeof(line), "%3d%%  %7s/%-7s  %9s  ETA %s",
             pct, off_buf, tot_buf, bps_buf, eta_buf);
    put_row(row++, line);
    put_row(row++, "[Sq:pause]  [O:cancel after pause]");
}

void ui_draw_downloads(const DownloadList *downloads,
                       int selected, int scroll_offset,
                       const char *status_line,
                       bool active_in_progress,
                       uint64_t active_downloaded,
                       uint64_t active_total,
                       uint64_t active_bps,
                       AppView current_view) {
    psvDebugScreenPuts(CLR_SCREEN);
    draw_tab_strip(current_view);

    char line[SCREEN_COLS + MAX_TITLE_LEN];
    put_row(1, "Downloads");
    put_row(2, (status_line && status_line[0]) ? status_line : "");

    int total = downloads ? downloads->count : 0;
    int row = LIST_START_ROW;

    if (active_in_progress) {
        draw_active_panel(downloads, active_downloaded, active_total, active_bps);
        row += 5;   /* 4 panel rows + blank separator */
    }

    if (total == 0) {
        put_row(row, "(no downloads queued - switch to ROM Catalog)");
    } else {
        int rows_avail = STATUS_ROW - 1 - row;
        if (rows_avail > LIST_VISIBLE_ROWS) rows_avail = LIST_VISIBLE_ROWS;
        if (rows_avail < 1) rows_avail = 1;
        int end = scroll_offset + rows_avail;
        if (end > total) end = total;

        for (int i = scroll_offset; i < end; i++) {
            const DownloadEntry *e = &downloads->items[i];
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
            bool sel = (i == selected);
            const char *display = e->name[0] ? e->name : e->filename;
            snprintf(line, sizeof(line), "%c[%s] %-45.45s %3d%%",
                     sel ? '>' : ' ', status_tag(e->status), display, pct);
            if (sel) psvDebugScreenPuts(FG_YELLOW);
            put_row(row + (i - scroll_offset), line);
            if (sel) psvDebugScreenPuts(FG_RESET);
        }
    }

    snprintf(line, sizeof(line),
             "%d/%d  X:start/resume  Sq:pause  O:cancel  Tri:clear done",
             total > 0 ? selected + 1 : 0, total);
    put_row(STATUS_ROW - 1, line);
}

void ui_draw_progress_partial(const DownloadList *downloads,
                              uint64_t active_downloaded,
                              uint64_t active_total,
                              uint64_t active_bps) {
    draw_active_panel(downloads, active_downloaded, active_total, active_bps);
}
