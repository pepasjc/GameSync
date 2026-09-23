/*
 * ui.c — colored text UI rendered via scr_putchar() at 8-pixel column
 * pitch.
 *
 * Why we don't just use scr_printf:
 *
 *   libdebug's scr_printf hardcodes a 7-pixel character cell, so 80
 *   columns × 7 px = 560 px = 87.5 % of a 640 px frame buffer.  After
 *   CRT overscan that's only ~80 % of the visible screen — content
 *   appears squashed against the left edge.  scr_putchar(x, y, ...)
 *   takes raw pixel coordinates, so calling it at col*8 spacing gives
 *   80 cols × 8 px = 640 px = full-width output without patching
 *   ps2sdk's libdebug.
 *
 * Layout (80 cols × 28 rows, 8x8 char cells, NTSC 640x224 single field):
 *
 *   row 0..1   header (app + view + net/storage/server line)
 *   row 2      blue separator
 *   row 3..24  scrolling list (22 rows visible)
 *   row 25     per-view button hints (always shown)
 *   row 26     blue separator
 *   row 27     status / error line
 */

#include "ui.h"

#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include <debug.h>

/* PS2 PSMCT32 stores pixels as 0xAABBGGRR.  Alpha is ignored when
 * scr_putchar paints to the frame buffer directly. */
#define RGB(r, g, b)    ((u32)((b) << 16) | (u32)((g) << 8) | (u32)(r))

#define COLS         80
#define HEADER_ROWS   2
#define SEP1_ROW      2
#define LIST_TOP      3
#define LIST_BOTTOM  25    /* exclusive */
#define HINT_ROW     25    /* persistent per-view button hints */
#define SEP2_ROW     26
#define STATUS_ROW   27
#define ROWS         28

#define LIST_VISIBLE (LIST_BOTTOM - LIST_TOP)

static const u32 C_BG          = RGB(0x10, 0x12, 0x18);
static const u32 C_HEADER_BG   = RGB(0x1e, 0x3a, 0x5f);
static const u32 C_HEADER_TEXT = RGB(0xff, 0xff, 0xff);
static const u32 C_TEXT        = RGB(0xff, 0xff, 0xff);
static const u32 C_TEXT_DIM    = RGB(0xa0, 0xa8, 0xb0);
static const u32 C_SEL_BG      = RGB(0x32, 0x70, 0xb0);
static const u32 C_SEL_TEXT    = RGB(0xff, 0xff, 0xff);
static const u32 C_ACCENT      = RGB(0x80, 0xc0, 0xff);
static const u32 C_STATUS_BG   = RGB(0xe6, 0xe6, 0xe6);
static const u32 C_STATUS_TEXT = RGB(0x20, 0x20, 0x28);
static const u32 C_ERROR_BG    = RGB(0x60, 0x10, 0x10);
static const u32 C_ERROR_TEXT  = RGB(0xff, 0xff, 0xff);
static const u32 C_SEP         = RGB(0x60, 0x80, 0xa0);

static char g_status[256];
static AppView g_view_for_hints = APP_VIEW_ROMS;
static char g_server_source_label[8] = "VMC";
static int  g_mmce_mode_disp[2] = {1, 1};   /* per slot: 0 off,1 auto,2 gen1,3 gen2 */

void ui_set_server_source(const char *src) {
    snprintf(g_server_source_label, sizeof(g_server_source_label), "%s", src);
}

void ui_set_mmce(int port, int mode) {
    if (port < 0 || port > 1) return;
    g_mmce_mode_disp[port] = mode;
}

/* ---- Pixel-precise putchar helpers ---- */

/* scr_putchar takes (x_pixel, y_pixel, color, char).  color sets the
 * font (foreground); the bg colour comes from a global set via
 * scr_setbgcolor.  We thread bg through each helper so callers don't
 * have to remember to update the global. */
static void put_char(int col, int row, u32 fg, u32 bg, int ch) {
    scr_setbgcolor(bg);
    scr_putchar(col * 8, row * 8, fg, ch);
}

static void put_string(int col, int row, u32 fg, u32 bg,
                       const char *s, int max_chars)
{
    scr_setbgcolor(bg);
    int x = col * 8;
    int y = row * 8;
    int written = 0;
    for (; *s; s++) {
        if (max_chars >= 0 && written >= max_chars) break;
        if (col + written >= COLS) break;
        unsigned char c = (unsigned char)*s;
        if (c < 0x20 || c > 0x7e) c = '?';
        scr_putchar(x, y, fg, c);
        x += 8;
        written++;
    }
}

static void put_printf(int col, int row, u32 fg, u32 bg,
                       int max_chars, const char *fmt, ...)
{
    char buf[256];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    put_string(col, row, fg, bg, buf, max_chars);
}

static void fill_row(int row, u32 bg) {
    scr_setbgcolor(bg);
    int y = row * 8;
    for (int c = 0; c < COLS; c++) {
        scr_putchar(c * 8, y, bg, ' ');
    }
}

static void fill_screen(u32 bg) {
    for (int r = 0; r < ROWS; r++) {
        fill_row(r, bg);
    }
}

static void draw_separator(int row) {
    scr_setbgcolor(C_BG);
    int y = row * 8;
    for (int c = 0; c < COLS; c++) {
        scr_putchar(c * 8, y, C_SEP, '-');
    }
}

/* ---- Status line + hints ---- */

static const char *view_label(AppView v) {
    switch (v) {
        case APP_VIEW_ROMS:      return "ROMs";
        case APP_VIEW_LOCAL:     return "Local";
        case APP_VIEW_DOWNLOADS: return "Downloads";
        case APP_VIEW_SAVES:     return "VMC";
        case APP_VIEW_MCARD:     return "MC Slot1";
        case APP_VIEW_MCARD2:    return "MC Slot2";
        case APP_VIEW_SERVER:    return "Server";
        case APP_VIEW_CONFIG:    return "Config";
        default:                 return "?";
    }
}

static const char *view_hints(AppView v) {
    switch (v) {
        case APP_VIEW_ROMS:
            return "X=fetch  []=queue  /\\=download  D-pad=move  L/R=page";
        case APP_VIEW_LOCAL:
            return "X=rescan  []=delete  D-pad=move  L/R=page";
        case APP_VIEW_DOWNLOADS:
            return "X=start/resume  []=remove  D-pad=move";
        case APP_VIEW_SAVES:
            return "X=upload card  /\\=pull all  []=rescan  D-pad=move";
        case APP_VIEW_MCARD:
        case APP_VIEW_MCARD2:
            return "X=up /\\=restore []=scan R1=switch SELECT=device";
        case APP_VIEW_SERVER: {
            static char buf[80];
            snprintf(buf, sizeof(buf),
                     "START=src[%s] X=dl /\\=up L1=all R1=switch SEL=device",
                     g_server_source_label);
            return buf;
        }
        case APP_VIEW_CONFIG:
            return "Left/Right=storage  /\\=format APA HDD  relaunch after storage change";
        default:
            return "";
    }
}

static const char *storage_pref_label(StoragePreference pref) {
    switch (pref) {
        case STORAGE_PREF_USB:  return "usb";
        case STORAGE_PREF_HDD:  return "hdd";
        case STORAGE_PREF_AUTO:
        default:                return "auto";
    }
}

static const char *storage_backend_label(const SyncState *state) {
    if (!state || !state->usb_ready) return "not-ready";
    switch (state->storage_backend) {
        case STORAGE_BACKEND_HDLOADER: return "hdd0:hdl";
        case STORAGE_BACKEND_MASS:     return state->usb_root;
        case STORAGE_BACKEND_NONE:
        default:                       return "not-ready";
    }
}

/* Persistent row showing the current view's button mappings. */
static void draw_hint_line(void) {
    fill_row(HINT_ROW, C_HEADER_BG);
    put_string(0, HINT_ROW, C_ACCENT, C_HEADER_BG,
               view_hints(g_view_for_hints), COLS);
}

static void draw_status_line(void) {
    const char *line = g_status[0] ? g_status : "Ready";

    u32 fg = C_STATUS_TEXT, bg = C_STATUS_BG;
    if (g_status[0] && strncmp(g_status, "ERR:", 4) == 0) {
        fg = C_ERROR_TEXT;
        bg = C_ERROR_BG;
    } else if (!g_status[0]) {
        fg = C_TEXT_DIM;
    }
    fill_row(STATUS_ROW, bg);
    put_string(0, STATUS_ROW, fg, bg, line, COLS);
}

/* ---- API ---- */

void ui_boot_init(void) {
    init_scr();
    scr_clear();
    g_status[0] = '\0';
}

void ui_init(void) {
    fill_screen(C_BG);
}

int ui_list_visible(void) {
    return LIST_VISIBLE;
}

void ui_clear(void) {
    fill_screen(C_BG);
}

void ui_flush(void) {
    /* scr_putchar is synchronous (DMA-blocks until upload done), so
     * no end-of-frame submit is needed. */
}

void ui_status(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(g_status, sizeof(g_status), fmt, ap);
    va_end(ap);
}

void ui_error(const char *fmt, ...) {
    char buf[240];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    snprintf(g_status, sizeof(g_status), "ERR: %s", buf);
}

/* ---- Header ---- */

void ui_draw_header(const SyncState *state, AppView view) {
    g_view_for_hints = view;

    fill_row(0, C_HEADER_BG);
    put_printf(0, 0, C_HEADER_TEXT, C_HEADER_BG, COLS,
               "PS2 Save Sync v%s   View: %-9s  L2/R2=screens  CIRCLE=exit",
               APP_VERSION, view_label(view));

    fill_row(1, C_HEADER_BG);
    put_printf(0, 1, C_TEXT_DIM, C_HEADER_BG, COLS,
               "Net: %-15s  Store: %-7s  Server: %.38s",
               state->ip[0]         ? state->ip         : "not-ready",
               storage_backend_label(state),
               state->server_url[0] ? state->server_url : "(unconfigured)");

    draw_separator(SEP1_ROW);
    draw_hint_line();
    draw_separator(SEP2_ROW);
    draw_status_line();
}

/* ---- ROM catalog list ---- */

static void truncate_to(char *dst, size_t cap, const char *src) {
    if (cap == 0) return;
    size_t n = src ? strlen(src) : 0;
    if (n >= cap) n = cap - 1;
    if (src) memcpy(dst, src, n);
    dst[n] = '\0';
}

static void clear_list_area(void) {
    for (int r = LIST_TOP; r < LIST_BOTTOM; r++) {
        fill_row(r, C_BG);
    }
}

void ui_draw_roms(const RomCatalog *catalog, int selected, int scroll) {
    clear_list_area();

    if (catalog->count == 0) {
        put_printf(2, LIST_TOP, C_TEXT, C_BG, COLS, "%s",
                   catalog->last_error[0]
                       ? catalog->last_error
                       : "No ROMs.  Press X to fetch catalog.");
        return;
    }

    for (int i = 0; i < LIST_VISIBLE; i++) {
        int idx = scroll + i;
        if (idx >= catalog->count) break;

        const RomEntry *r = &catalog->items[idx];
        int row = LIST_TOP + i;

        bool is_sel = (idx == selected);
        u32 bg = is_sel ? C_SEL_BG : C_BG;
        u32 fg = is_sel ? C_SEL_TEXT : C_TEXT;
        fill_row(row, bg);

        char name[80];
        truncate_to(name, sizeof(name),
                    r->name[0] ? r->name : r->filename);

        unsigned mb = (unsigned)(r->size / (1024ULL * 1024ULL));
        const char *serial = r->serial[0] ? r->serial : "";

        /* prefix(1) + serial(12) + sp(1) + name(57) + sp(1) + size(5) + " MB"(3) = 80 */
        put_printf(0, row, fg, bg, COLS,
                   "%s%-12.12s %-57.57s %5u MB",
                   is_sel ? ">" : " ",
                   serial, name, mb);
    }
}

/* ---- Local ISO list ---- */

void ui_draw_local(const LocalRomList *list, int selected, int scroll) {
    clear_list_area();

    if (list->count == 0) {
        put_printf(2, LIST_TOP, C_TEXT, C_BG, COLS, "%s",
                   list->last_error[0]
                       ? list->last_error
                       : "No ISOs installed.");
        return;
    }

    for (int i = 0; i < LIST_VISIBLE; i++) {
        int idx = scroll + i;
        if (idx >= list->count) break;

        const LocalRom *r = &list->items[idx];
        int row = LIST_TOP + i;

        bool is_sel = (idx == selected);
        u32 bg = is_sel ? C_SEL_BG : C_BG;
        u32 fg = is_sel ? C_SEL_TEXT : C_TEXT;
        fill_row(row, bg);

        unsigned mb = (unsigned)(r->size / (1024ULL * 1024ULL));
        /* prefix(1) + serial(12) + sp(1) + name(51) + sp(1) +
         * "["(1) + tag(3) + "]"(1) + sp(1) + size(5) + " MB"(3) = 80 */
        put_printf(0, row, fg, bg, COLS,
                   "%s%-12.12s %-51.51s [%-3s] %5u MB",
                   is_sel ? ">" : " ",
                   r->serial, r->name,
                   r->is_cd ? "CD" : "DVD",
                   mb);
    }
}

/* ---- Saves (VMC / MemCard Pro) list ---- */

void ui_draw_saves(const SaveVmcList *list, int selected, int scroll) {
    clear_list_area();

    if (list->count == 0) {
        put_printf(2, LIST_TOP, C_TEXT, C_BG, COLS, "%s",
                   list->last_error[0]
                       ? list->last_error
                       : "No card images found.");
        return;
    }

    for (int i = 0; i < LIST_VISIBLE; i++) {
        int idx = scroll + i;
        if (idx >= list->count) break;

        const SaveVmc *v = &list->items[idx];
        int row = LIST_TOP + i;

        bool is_sel = (idx == selected);
        u32 bg = is_sel ? C_SEL_BG : C_BG;
        u32 fg = is_sel ? C_SEL_TEXT : C_TEXT;
        fill_row(row, bg);

        const char *tag = v->is_ps1 ? "ps1" : (v->has_ecc ? "ps2" : "mc2");
        unsigned kb = (unsigned)(v->size / 1024ULL);
        const char *label = v->name[0] ? v->name : v->filename;
        /* prefix(1) + serial(10) + sp + title/file(53) + sp + tag(5) + sp + size = 80 */
        put_printf(0, row, fg, bg, COLS,
                   "%s%-10.10s %-53.53s [%-3s] %5u KB",
                   is_sel ? ">" : " ",
                   v->serial[0] ? v->serial : "-",
                   label, tag, kb);
    }
}

/* ---- Memory card games list ---- */

void ui_draw_mcard(const McGameList *list, int selected, int scroll) {
    clear_list_area();

    if (list->count == 0) {
        put_printf(2, LIST_TOP, C_TEXT, C_BG, COLS, "%s",
                   list->last_error[0]
                       ? list->last_error
                       : "No game saves on memory card.");
        return;
    }

    for (int i = 0; i < LIST_VISIBLE; i++) {
        int idx = scroll + i;
        if (idx >= list->count) break;

        const McGame *g = &list->items[idx];
        int row = LIST_TOP + i;

        bool is_sel = (idx == selected);
        u32 bg = is_sel ? C_SEL_BG : C_BG;
        u32 fg = is_sel ? C_SEL_TEXT : C_TEXT;
        fill_row(row, bg);

        unsigned kb = (unsigned)(g->total_size / 1024U);
        const char *label = g->name[0] ? g->name : g->dir;
        /* prefix(1) + serial(12) + sp + name(50) + sp + files(3) + "f"(1) + sp + size = 80 */
        put_printf(0, row, fg, bg, COLS,
                   "%s%-12.12s %-50.50s %3df %5u KB",
                   is_sel ? ">" : " ",
                   g->serial, label, g->file_count, kb);
    }
}

/* ---- Server saves list ---- */

void ui_draw_server(const ServerSaveList *list, int selected, int scroll) {
    clear_list_area();

    if (list->count == 0) {
        put_printf(2, LIST_TOP, C_TEXT, C_BG, COLS, "%s",
                   list->last_error[0]
                       ? list->last_error
                       : "No saves on server.");
        return;
    }

    for (int i = 0; i < LIST_VISIBLE; i++) {
        int idx = scroll + i;
        if (idx >= list->count) break;

        const ServerSave *s = &list->items[idx];
        int row = LIST_TOP + i;

        bool is_sel = (idx == selected);
        u32 bg = is_sel ? C_SEL_BG : C_BG;
        u32 fg = is_sel ? C_SEL_TEXT : C_TEXT;
        fill_row(row, bg);

        /* prefix(1) + sys(3) + sp + L(1) + sp + serial(12) + sp + name(58) = 80 */
        put_printf(0, row, fg, bg, COLS,
                   "%s%-3s %c %-12.12s %-58.58s",
                   is_sel ? ">" : " ",
                   s->is_ps1 ? "ps1" : "ps2",
                   s->local ? 'L' : ' ',
                   s->serial, s->name);
    }
}

/* ---- Downloads list ---- */

static void format_size(uint64_t bytes, char *out, size_t out_size) {
    if (bytes >= 1024ULL * 1024ULL * 1024ULL) {
        snprintf(out, out_size, "%llu.%01llu GB",
                 (unsigned long long)(bytes / (1024ULL * 1024ULL * 1024ULL)),
                 (unsigned long long)((bytes / (1024ULL * 1024ULL * 1024ULL / 10)) % 10));
    } else if (bytes >= 1024ULL * 1024ULL) {
        snprintf(out, out_size, "%llu MB",
                 (unsigned long long)(bytes / (1024ULL * 1024ULL)));
    } else {
        snprintf(out, out_size, "%llu KB",
                 (unsigned long long)(bytes / 1024ULL));
    }
}

void ui_draw_downloads(const DownloadList *list, int selected, int scroll,
                       uint64_t active_done, uint64_t active_total,
                       uint64_t active_bps)
{
    clear_list_area();

    if (list->count == 0) {
        put_printf(2, LIST_TOP, C_TEXT, C_BG, COLS,
                   "No downloads queued.");
    } else {
        int rows_for_list = LIST_VISIBLE - (active_total > 0 ? 1 : 0);
        for (int i = 0; i < rows_for_list; i++) {
            int idx = scroll + i;
            if (idx >= list->count) break;

            const DownloadEntry *e = &list->items[idx];
            int row = LIST_TOP + i;

            bool is_sel = (idx == selected);
            u32 bg = is_sel ? C_SEL_BG : C_BG;
            u32 fg = is_sel ? C_SEL_TEXT : C_TEXT;
            fill_row(row, bg);

            char name[80];
            truncate_to(name, sizeof(name),
                        e->name[0] ? e->name : e->filename);

            unsigned pct = 0;
            if (e->total > 0) pct = (unsigned)((e->offset * 100ULL) / e->total);

            /* prefix(1) + name(62) + 2sp + pct(3) + "%"(1) + 2sp + status(9) = 80 */
            put_printf(0, row, fg, bg, COLS,
                       "%s%-62.62s  %3u%%  %-9s",
                       is_sel ? ">" : " ",
                       name, pct, downloads_status_to_str(e->status));
        }
    }

    if (active_total > 0) {
        char done_s[24], total_s[24];
        format_size(active_done,  done_s,  sizeof(done_s));
        format_size(active_total, total_s, sizeof(total_s));
        unsigned pct = (unsigned)((active_done * 100ULL) / active_total);

        int row = LIST_BOTTOM - 1;
        fill_row(row, C_BG);
        put_printf(0, row, C_ACCENT, C_BG, COLS,
                   "Active: %s / %s (%u%%) @ %llu KB/s",
                   done_s, total_s, pct,
                   (unsigned long long)(active_bps / 1024));
    }
}

/* ---- Config view ---- */

static void cfg_line(int *row, u32 fg, const char *fmt, ...) {
    fill_row(*row, C_BG);
    char buf[200];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    put_string(2, *row, fg, C_BG, buf, COLS - 2);
    (*row)++;
}

void ui_draw_config(const SyncState *state) {
    clear_list_area();

    int row = LIST_TOP;
    cfg_line(&row, C_TEXT_DIM, "Config file:  %s", CONFIG_PATH);
    row++;

    cfg_line(&row, C_TEXT, "server_url = %.60s",
             state->server_url[0] ? state->server_url : "(unset)");
    cfg_line(&row, C_TEXT, "api_key    = %s",
             state->api_key[0] ? "(set)" : "(unset)");
    cfg_line(&row, C_TEXT, "console_id = %s", state->console_id);
    cfg_line(&row, C_TEXT, "net_mode   = %s",
             state->use_static_ip ? "static" : "dhcp");
    if (state->use_static_ip) {
        cfg_line(&row, C_TEXT, "static_ip  = %s", state->static_ip);
        cfg_line(&row, C_TEXT, "netmask    = %s", state->static_netmask);
        cfg_line(&row, C_TEXT, "gateway    = %s", state->static_gateway);
    }
    row++;
    cfg_line(&row, C_TEXT, "network    = %s (%s)",
             (state->net_ready && state->dhcp_ok) ? "ready" : "not ready",
             state->ip[0] ? state->ip : "no ip");
    cfg_line(&row, C_TEXT, "storage    = %s",
             storage_pref_label(state->storage_pref));
    cfg_line(&row, C_TEXT, "backend    = %s",
             storage_backend_label(state));
    cfg_line(&row, C_TEXT, "queue_file = %s",
             state->storage_backend == STORAGE_BACKEND_HDLOADER
                 ? HDL_DOWNLOADS_FILE
                 : roms_downloads_file());
    {
        static const char *mn[] = {"off", "auto", "gen1", "gen2"};
        int s1 = (g_mmce_mode_disp[0] >= 0 && g_mmce_mode_disp[0] < 4) ? g_mmce_mode_disp[0] : 1;
        int s2 = (g_mmce_mode_disp[1] >= 0 && g_mmce_mode_disp[1] < 4) ? g_mmce_mode_disp[1] : 1;
        cfg_line(&row, C_TEXT,
                 "gameid_mode= Slot1:%s  Slot2:%s  (SELECT in a card view cycles)",
                 mn[s1], mn[s2]);
    }
    cfg_line(&row, C_TEXT, "hdd_format = TRIANGLE twice (PS2 APA, wipes disk)");
    row++;
    cfg_line(&row, C_TEXT_DIM,
             "Left/Right=storage  []=MMCE on/off  or edit %s in uLaunchELF.",
             CONFIG_PATH);
}

void ui_draw_message(const char *title, const char *message) {
    fill_screen(C_BG);
    fill_row(0, C_HEADER_BG);
    put_printf(0, 0, C_HEADER_TEXT, C_HEADER_BG, COLS, "== %s ==", title);

    int row = LIST_TOP + 1;
    const char *p = message;
    while (p && *p && row < LIST_BOTTOM) {
        const char *nl = strchr(p, '\n');
        size_t len = nl ? (size_t)(nl - p) : strlen(p);
        if (len > COLS - 2) len = COLS - 2;

        char buf[128];
        if (len >= sizeof(buf)) len = sizeof(buf) - 1;
        memcpy(buf, p, len);
        buf[len] = '\0';
        fill_row(row, C_BG);
        put_string(2, row, C_TEXT, C_BG, buf, COLS - 2);
        row++;

        if (!nl) break;
        p = nl + 1;
    }

    snprintf(g_status, sizeof(g_status), "Press CIRCLE to dismiss");
    draw_status_line();
}
