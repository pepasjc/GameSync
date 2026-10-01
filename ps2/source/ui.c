/*
 * ui.c — GameSync screens for the PS2 client.
 *
 * Layout on the 640x448 canvas (see gui.h; PAL is centred):
 *
 *     0..44    header: logo, "GameSync • <screen>", version, network dot
 *    50..74    view tabs, flanked by the L1 / R1 glyphs that cycle them
 *    82..370   content: list card (left) + detail card (right), or the
 *              settings cards
 *   376..404   status banner (info / working / error)
 *   414..448   footer: button glyph pills for the current view
 *
 * Long-running work (downloads) draws a modal card over whatever view is
 * active; confirmations, action menus and details cards do the same with a
 * dimmed backdrop.  Views with sub-tabs (memory card slot, server sync
 * source) show them as SELECT + chips in the list header.
 */

#include "ui.h"
#include "gui.h"

#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

/* ---- Geometry ---- */

#define TABS_Y      50
#define TABS_H      24

#define CONTENT_Y   82
#define CONTENT_H   288

#define LIST_X      GUI_MARGIN
#define LIST_W      372
#define LIST_HEAD_H 30
#define ROW_H       24
#define LIST_ROWS   ((CONTENT_H - LIST_HEAD_H - 8) / ROW_H)

#define DETAIL_X    (LIST_X + LIST_W + 10)
#define DETAIL_W    (GUI_W - GUI_MARGIN - DETAIL_X)

#define BANNER_Y    376
#define BANNER_H    28

#define KV_LABEL_W  66
#define KV_ROW_H    22

/* ---- State ---- */

enum { STATUS_NONE, STATUS_INFO, STATUS_ERROR };

static char    g_status[256];
static int     g_status_kind = STATUS_NONE;
static int     g_server_source = 0;            /* 0 VMC, 1 slot 1, 2 slot 2 */
static char    g_catalog_badge[16];
static char    g_cache_desc[64] = "not used";
static int     g_mmce_mode_disp[2] = {1, 1};   /* per slot: 0 off,1 auto,2 gen1,3 gen2 */
static const LocalRomList *g_ctx_local;
static const DownloadList *g_ctx_downloads;

static const char *const g_mmce_names[4] = {"off", "auto", "gen1", "gen2"};

/* Boot log: last lines shown on the splash. */
#define BOOT_LINES     11
#define BOOT_LINE_LEN  96
static char  g_boot_log[BOOT_LINES][BOOT_LINE_LEN];
static int   g_boot_count;
static bool  g_booting;

static const char *const g_source_labels[3] = { "VMC", "Slot 1", "Slot 2" };

void ui_set_server_source(int source) {
    g_server_source = (source >= 0 && source < 3) ? source : 0;
}

void ui_set_catalog_info(const char *badge, const char *cache_desc) {
    snprintf(g_catalog_badge, sizeof(g_catalog_badge), "%s", badge ? badge : "");
    if (cache_desc) snprintf(g_cache_desc, sizeof(g_cache_desc), "%s", cache_desc);
}

void ui_set_mmce(int port, int mode) {
    if (port < 0 || port > 1) return;
    g_mmce_mode_disp[port] = mode;
}

void ui_set_context(const LocalRomList *local, const DownloadList *downloads) {
    g_ctx_local = local;
    g_ctx_downloads = downloads;
}

static const char *mmce_name(int port) {
    int m = g_mmce_mode_disp[port];
    return (m >= 0 && m < 4) ? g_mmce_names[m] : "auto";
}

/* ---- Small formatting helpers ---- */

static void format_size(uint64_t bytes, char *out, size_t out_size) {
    if (bytes >= 1024ULL * 1024ULL * 1024ULL) {
        snprintf(out, out_size, "%llu.%llu GB",
                 (unsigned long long)(bytes / (1024ULL * 1024ULL * 1024ULL)),
                 (unsigned long long)((bytes / (1024ULL * 1024ULL * 1024ULL / 10)) % 10));
    } else if (bytes >= 1024ULL * 1024ULL) {
        snprintf(out, out_size, "%llu MB",
                 (unsigned long long)(bytes / (1024ULL * 1024ULL)));
    } else {
        snprintf(out, out_size, "%llu KB",
                 (unsigned long long)((bytes + 1023) / 1024ULL));
    }
}

static void format_rate(uint64_t bps, char *out, size_t out_size) {
    if (bps >= 1024ULL * 1024ULL) {
        snprintf(out, out_size, "%llu.%llu MB/s",
                 (unsigned long long)(bps / (1024ULL * 1024ULL)),
                 (unsigned long long)((bps * 10 / (1024ULL * 1024ULL)) % 10));
    } else {
        snprintf(out, out_size, "%llu KB/s", (unsigned long long)(bps / 1024ULL));
    }
}

static void format_duration(uint32_t secs, char *out, size_t out_size) {
    if (secs >= 3600)
        snprintf(out, out_size, "%lu:%02lu:%02lu", (unsigned long)(secs / 3600),
                 (unsigned long)((secs / 60) % 60), (unsigned long)(secs % 60));
    else
        snprintf(out, out_size, "%lu:%02lu", (unsigned long)(secs / 60),
                 (unsigned long)(secs % 60));
}

/* Serials come as SLUS-21371, SLUS_213.71 or SLUS21371 depending on the
 * source; compare them on letters and digits only. */
static bool serial_eq(const char *a, const char *b) {
    if (!a || !b || !*a || !*b) return false;
    for (;;) {
        while (*a && !((*a >= 'A' && *a <= 'Z') || (*a >= 'a' && *a <= 'z') ||
                       (*a >= '0' && *a <= '9'))) a++;
        while (*b && !((*b >= 'A' && *b <= 'Z') || (*b >= 'a' && *b <= 'z') ||
                       (*b >= '0' && *b <= '9'))) b++;
        if (!*a || !*b) return !*a && !*b;
        char ca = (*a >= 'a' && *a <= 'z') ? (char)(*a - 32) : *a;
        char cb = (*b >= 'a' && *b <= 'z') ? (char)(*b - 32) : *b;
        if (ca != cb) return false;
        a++;
        b++;
    }
}

static bool rom_installed(const RomEntry *r) {
    if (!g_ctx_local || !r->serial[0]) return false;
    for (int i = 0; i < g_ctx_local->count; i++)
        if (serial_eq(g_ctx_local->items[i].serial, r->serial)) return true;
    return false;
}

static const DownloadEntry *rom_download(const RomEntry *r) {
    if (!g_ctx_downloads) return NULL;
    for (int i = 0; i < g_ctx_downloads->count; i++)
        if (strcmp(g_ctx_downloads->items[i].rom_id, r->rom_id) == 0)
            return &g_ctx_downloads->items[i];
    return NULL;
}

static uint32_t dl_status_hex(DownloadStatus s) {
    switch (s) {
        case DL_STATUS_ACTIVE:    return HEX_INFO;
        case DL_STATUS_PAUSED:    return HEX_WARN;
        case DL_STATUS_COMPLETED: return HEX_OK;
        case DL_STATUS_ERROR:     return HEX_ERR;
        case DL_STATUS_QUEUED:
        default:                  return HEX_MUTED;
    }
}

static const char *dl_status_label(DownloadStatus s) {
    switch (s) {
        case DL_STATUS_ACTIVE:    return "Active";
        case DL_STATUS_PAUSED:    return "Paused";
        case DL_STATUS_COMPLETED: return "Done";
        case DL_STATUS_ERROR:     return "Error";
        case DL_STATUS_QUEUED:
        default:                  return "Queued";
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
    if (!state || !state->usb_ready) return "not ready";
    switch (state->storage_backend) {
        case STORAGE_BACKEND_HDLOADER: return "hdd0: (HDLoader)";
        case STORAGE_BACKEND_MASS:     return state->usb_root;
        case STORAGE_BACKEND_NONE:
        default:                       return "not ready";
    }
}

/* ---- Views: titles, tabs, hints ---- */

static const char *const g_tab_labels[APP_VIEW_COUNT] = {
    "Catalog", "Installed", "Downloads", "VMC", "Memory Card", "Server", "Settings",
};

static const char *view_title(AppView v) {
    switch (v) {
        case APP_VIEW_ROMS:      return "Game Catalog";
        case APP_VIEW_LOCAL:     return "Installed Games";
        case APP_VIEW_DOWNLOADS: return "Downloads";
        case APP_VIEW_SAVES:     return "Virtual Memory Cards";
        case APP_VIEW_MCARD:     return "Memory Card";
        case APP_VIEW_SERVER:    return "Server Saves";
        case APP_VIEW_CONFIG:    return "Settings";
        default:                 return "";
    }
}

static void draw_footer(AppView v) {
    static const GuiHint roms[] = {
        {"X", "Install"}, {"S", "Queue"}, {"T", "Details"},
        {"LR", "Page"}, {"START", "Exit"},
    };
    static const GuiHint local[] = {
        {"X", "Delete"}, {"S", "Rescan"}, {"T", "Details"}, {"LR", "Page"}, {"START", "Exit"},
    };
    static const GuiHint downloads[] = {
        {"X", "Start / resume"}, {"S", "Remove"}, {"T", "Details"}, {"LR", "Page"},
        {"START", "Exit"},
    };
    static const GuiHint vmc[] = {
        {"X", "Actions"}, {"S", "Pull all"}, {"T", "Details"}, {"LR", "Page"}, {"START", "Exit"},
    };
    static const GuiHint mcard[] = {
        {"X", "Actions"}, {"S", "Upload all"}, {"T", "Details"},
        {"SELECT", "Slot"}, {"START", "Exit"},
    };
    static const GuiHint server[] = {
        {"X", "Actions"}, {"S", "Sync all"}, {"T", "Details"},
        {"SELECT", "Source"}, {"START", "Exit"},
    };
    static const GuiHint config[] = {
        {"UD", "Select"}, {"LR", "Change"}, {"X", "Apply"}, {"START", "Exit"},
    };
#define HINTS(a) gui_footer(a, (int)(sizeof(a) / sizeof(a[0])))
    switch (v) {
        case APP_VIEW_ROMS:      HINTS(roms);      break;
        case APP_VIEW_LOCAL:     HINTS(local);     break;
        case APP_VIEW_DOWNLOADS: HINTS(downloads); break;
        case APP_VIEW_SAVES:     HINTS(vmc);       break;
        case APP_VIEW_MCARD:     HINTS(mcard);     break;
        case APP_VIEW_SERVER:    HINTS(server);    break;
        case APP_VIEW_CONFIG:    HINTS(config);    break;
        default:                 gui_footer(NULL, 0); break;
    }
#undef HINTS
}

static void draw_status_banner(void) {
    float x = GUI_MARGIN, y = BANNER_Y, w = GUI_W - 2 * GUI_MARGIN, h = BANNER_H;
    const char *line = g_status[0] ? g_status : "Ready";
    uint32_t tone = HEX_ACCENT;
    if (g_status_kind == STATUS_ERROR) tone = HEX_ERR;
    else if (!g_status[0]) tone = HEX_MUTED;
    else {
        size_t n = strlen(g_status);
        if (n >= 3 && strcmp(g_status + n - 3, "...") == 0) tone = HEX_INFO;   /* working */
    }

    gui_rrect(x, y, w, h, 6, g_status_kind == STATUS_ERROR ? 0x3A1F25 : HEX_BG2);
    gui_rrect(x, y, 4, h, 2, tone);
    gui_circle(x + 18, y + h / 2, 4, tone);
    gui_text_mid(x + 30, y, h, FONT_SMALL,
                 g_status_kind == STATUS_ERROR ? 0xFFD7D5 : (g_status[0] ? HEX_TEXT : HEX_DIM),
                 GUI_LEFT, w - 40, line);
}

/* ---- List card ---- */

/* Sub-tab chips led by the SELECT glyph that cycles them; returns the width. */
static float subtabs(float x, float cy, const char *const *labels, int count, int active) {
    float x0 = x;
    x += gui_button(x, cy, "SELECT") + 6;
    float h = 20, y = cy - h / 2, pad = 8, total = 4;
    for (int i = 0; i < count; i++) total += gui_text_w(FONT_TINY, labels[i]) + pad * 2;
    gui_rrect(x, y, total, h, h / 2, HEX_BG);
    float cx = x + 2;
    for (int i = 0; i < count; i++) {
        float w = gui_text_w(FONT_TINY, labels[i]) + pad * 2;
        if (i == active) gui_rrect(cx, y + 2, w, h - 4, (h - 4) / 2, HEX_ACCENT2);
        gui_text_mid(cx + w / 2, y, h, FONT_TINY, i == active ? HEX_INK : HEX_DIM,
                     GUI_CENTER, 0, labels[i]);
        cx += w;
    }
    return x + total - x0;
}

/* List card header: a title, or (chips != NULL) the view's sub-tabs. */
static void list_frame_ex(const char *title, const char *const *chips, int nchips, int active,
                          const char *badge, uint32_t badge_hex, int count, int scroll)
{
    gui_panel(LIST_X, CONTENT_Y, LIST_W, CONTENT_H);
    float x = LIST_X + 14;
    if (chips)
        x += subtabs(x, CONTENT_Y + 2 + LIST_HEAD_H / 2.0f, chips, nchips, active);
    else
        x += gui_text_mid(x, CONTENT_Y + 2, LIST_HEAD_H, FONT_SMALL, HEX_DIM, GUI_LEFT, 200, title);
    if (badge && *badge)
        gui_pill(x + 8, CONTENT_Y + 8, 18, FONT_TINY, badge_hex, HEX_INK, badge);
    if (count > 0) {
        int last = scroll + LIST_ROWS;
        if (last > count) last = count;
        char buf[48];
        snprintf(buf, sizeof(buf), "%d-%d of %d", scroll + 1, last, count);
        gui_text_mid(LIST_X + LIST_W - 14, CONTENT_Y + 2, LIST_HEAD_H, FONT_TINY, HEX_MUTED,
                     GUI_RIGHT, 0, buf);
    }
    gui_rect(LIST_X + 10, CONTENT_Y + LIST_HEAD_H, LIST_W - 20, 1, HEX_LINE);
    gui_scrollbar(LIST_X + LIST_W - 8, CONTENT_Y + LIST_HEAD_H + 6,
                  LIST_ROWS * ROW_H, scroll, LIST_ROWS, count);
}

static void list_frame(const char *title, const char *badge, uint32_t badge_hex,
                       int count, int scroll)
{
    list_frame_ex(title, NULL, 0, 0, badge, badge_hex, count, scroll);
}

static void list_empty(const char *message) {
    float cy = CONTENT_Y + LIST_HEAD_H + 70;
    gui_ring(LIST_X + LIST_W / 2, cy, 16, 3, HEX_LINE, HEX_PANEL);
    gui_rect(LIST_X + LIST_W / 2 - 1.5f, cy - 7, 3, 9, HEX_MUTED);
    gui_rect(LIST_X + LIST_W / 2 - 1.5f, cy + 4, 3, 3, HEX_MUTED);
    char lines[3][GUI_WRAP_LINE];
    int n = gui_wrap(message, FONT_SMALL, LIST_W - 60, lines, 3);
    for (int i = 0; i < n; i++)
        gui_text(LIST_X + LIST_W / 2, cy + 30 + i * gui_line_h(FONT_SMALL), FONT_SMALL,
                 HEX_DIM, GUI_CENTER, lines[i]);
}

typedef struct {
    float x, y, w;       /* text area inside the row */
    uint32_t fg, sub;    /* main / secondary text colours for this row */
    bool selected;
} Row;

/* Row slot i of the visible window; draws the selection bar. */
static Row list_row(int i, bool selected) {
    Row r;
    float y = CONTENT_Y + LIST_HEAD_H + 6 + i * ROW_H;
    if (selected) gui_rrect(LIST_X + 6, y, LIST_W - 20, ROW_H - 2, 6, HEX_ACCENT);
    r.x = LIST_X + 14;
    r.y = y;
    r.w = LIST_W - 36;
    r.fg = selected ? HEX_INK : HEX_TEXT;
    r.sub = selected ? 0x14423E : HEX_DIM;
    r.selected = selected;
    return r;
}

/* Left-aligned system/type tag; returns width used (incl. gap). */
static float row_tag(const Row *r, const char *label, uint32_t hex) {
    float w = gui_pill(r->x, r->y + 3, ROW_H - 8, FONT_TINY,
                       r->selected ? HEX_INK : hex, r->selected ? hex : HEX_INK, label);
    return w + 8;
}

/* Right-aligned secondary text; returns its width (incl. gap). */
static float row_right(const Row *r, const char *text) {
    if (!text || !*text) return 0;
    float w = gui_text_mid(r->x + r->w, r->y, ROW_H - 2, FONT_SMALL, r->sub, GUI_RIGHT, 0, text);
    return w + 10;
}

/* ---- Detail card ---- */

typedef struct {
    float x, y, w;
} Detail;

/* Card with the item name wrapped over up to 2 lines; returns the cursor. */
static Detail detail_begin(const char *heading, const char *name) {
    Detail d;
    gui_panel(DETAIL_X, CONTENT_Y, DETAIL_W, CONTENT_H);
    d.x = DETAIL_X + 14;
    d.w = DETAIL_W - 28;
    d.y = CONTENT_Y + 10;
    gui_text(d.x, d.y, FONT_TINY, HEX_MUTED, GUI_LEFT, heading);
    d.y += gui_line_h(FONT_TINY) + 2;
    if (name && *name) {
        int n = gui_text_wrap(d.x, d.y, FONT_BODY, HEX_TEXT, d.w, 3, name);
        d.y += n * gui_line_h(FONT_BODY) + 6;
    }
    return d;
}

static void detail_pills(Detail *d, const char *const *labels, const uint32_t *hexes, int n) {
    float x = d->x;
    for (int i = 0; i < n; i++) {
        if (!labels[i] || !*labels[i]) continue;
        float w = gui_pill_w(FONT_TINY, labels[i]);
        if (x + w > d->x + d->w) break;
        x += gui_pill(x, d->y, 20, FONT_TINY, hexes[i], HEX_INK, labels[i]) + 6;
    }
    d->y += 30;
}

static void detail_rule(Detail *d) {
    gui_rect(d->x, d->y, d->w, 1, HEX_LINE);
    d->y += 8;
}

static void detail_kv(Detail *d, const char *label, const char *value, uint32_t hex) {
    gui_text_mid(d->x, d->y, KV_ROW_H, FONT_TINY, HEX_DIM, GUI_LEFT, KV_LABEL_W - 4, label);
    gui_text_mid(d->x + KV_LABEL_W, d->y, KV_ROW_H, FONT_SMALL, hex, GUI_LEFT,
                 d->w - KV_LABEL_W, value && *value ? value : "-");
    d->y += KV_ROW_H;
}

/* Value that may need several lines (paths, URLs). */
static void detail_kv_wrap(Detail *d, const char *label, const char *value) {
    gui_text_mid(d->x, d->y, KV_ROW_H, FONT_TINY, HEX_DIM, GUI_LEFT, KV_LABEL_W - 4, label);
    char lines[3][GUI_WRAP_LINE];
    int n = gui_wrap(value && *value ? value : "-", FONT_SMALL, d->w - KV_LABEL_W, lines, 3);
    for (int i = 0; i < n; i++)
        gui_text_mid(d->x + KV_LABEL_W, d->y + i * 18, KV_ROW_H, FONT_SMALL, HEX_TEXT,
                     GUI_LEFT, 0, lines[i]);
    d->y += KV_ROW_H + (n > 1 ? (n - 1) * 18 : 0);
}

static void detail_note(Detail *d, const char *text, uint32_t hex) {
    float bottom = CONTENT_Y + CONTENT_H - 12;
    char lines[3][GUI_WRAP_LINE];
    int n = gui_wrap(text, FONT_TINY, d->w, lines, 3);
    int lh = gui_line_h(FONT_TINY);
    float y = bottom - n * lh;
    if (y < d->y) y = d->y;
    for (int i = 0; i < n; i++) gui_text(d->x, y + i * lh, FONT_TINY, hex, GUI_LEFT, lines[i]);
}

static void detail_empty(const char *heading) {
    Detail d = detail_begin(heading, NULL);
    gui_text_wrap(d.x, d.y + 6, FONT_SMALL, HEX_MUTED, d.w, 3, "Nothing selected.");
}

/* ---- Boot splash ---- */

static void draw_logo(float cx, float y) {
    float tw = gui_text_w(FONT_HERO, "GameSync");
    float mark = 40, gap = 14;
    float x = cx - (mark + gap + tw) / 2;
    gui_rrect(x, y, mark, mark, 11, HEX_ACCENT);
    gui_rrect(x + 11, y + 11, 18, 18, 5, HEX_BG);
    gui_text_mid(x + mark + gap, y - 4, mark + 8, FONT_HERO, HEX_TEXT, GUI_LEFT, 0, "GameSync");
}

static uint32_t log_line_hex(const char *s) {
    if (strstr(s, "fail") || strstr(s, "FAIL") || strstr(s, "error") || strstr(s, "ERR"))
        return HEX_ERR;
    if (strstr(s, "WARN") || strstr(s, "timed out") || strstr(s, "unavailable") ||
        strstr(s, "not ready") || strstr(s, "no "))
        return HEX_WARN;
    if (strncmp(s, "BOOT:", 5) == 0) return HEX_TEXT;
    return HEX_DIM;
}

static void draw_boot(void) {
    gui_begin();
    gui_vgrad(0, -32, GUI_W, GUI_H + 64, HEX_BG, 0x0B1118);
    draw_logo(GUI_W / 2, 52);
    gui_text(GUI_W / 2, 104, FONT_SMALL, HEX_DIM, GUI_CENTER,
             "Save sync and game installer for PlayStation 2  -  v" APP_VERSION);

    float cx = 72, cw = GUI_W - 144, cy = 146, ch = 262;
    gui_card(cx, cy, cw, ch, "Starting up", HEX_ACCENT);
    float y = cy + 38;
    int lh = gui_line_h(FONT_TINY) + 1;
    for (int i = 0; i < g_boot_count; i++) {
        const char *s = g_boot_log[i];
        bool sub = (s[0] == ' ');
        while (*s == ' ') s++;
        uint32_t hex = log_line_hex(s);
        if (i == g_boot_count - 1) gui_circle(cx + 16, y + lh / 2, 3, HEX_ACCENT);
        gui_text_fit(cx + (sub ? 36 : 26), y, FONT_TINY, hex, GUI_LEFT, cw - 52, s);
        y += lh;
    }
    gui_end(true);
}

void ui_init(void) {
    gui_init();
    g_status[0] = '\0';
    g_status_kind = STATUS_NONE;
    g_booting = true;
    g_boot_count = 0;
    draw_boot();
}

void ui_log(const char *fmt, ...) {
    char buf[BOOT_LINE_LEN];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    size_t n = strlen(buf);
    while (n > 0 && (buf[n - 1] == '\n' || buf[n - 1] == '\r')) buf[--n] = '\0';
    if (n == 0) return;

    if (g_boot_count == BOOT_LINES) {
        memmove(g_boot_log[0], g_boot_log[1], sizeof(g_boot_log[0]) * (BOOT_LINES - 1));
        g_boot_count--;
    }
    snprintf(g_boot_log[g_boot_count++], BOOT_LINE_LEN, "%s", buf);

    /* After boot the log is only kept (e.g. config_save's memory card
     * probe) — repainting the splash would cover the running UI. */
    if (g_booting) draw_boot();
}

void ui_boot_done(void) {
    g_booting = false;
}

/* ---- Frame API ---- */

uint32_t ui_ms(void) {
    return gui_ms();
}

int ui_list_visible(void) {
    return LIST_ROWS;
}

void ui_begin(void) {
    gui_begin();
    gui_vgrad(0, GUI_HEADER_H, GUI_W, GUI_FOOTER_Y - GUI_HEADER_H, HEX_BG, 0x121C27);
}

void ui_flush(void) {
    gui_end(true);
}

void ui_flush_nowait(void) {
    gui_end(false);
}

void ui_status(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(g_status, sizeof(g_status), fmt, ap);
    va_end(ap);
    g_status_kind = STATUS_INFO;
}

void ui_error(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(g_status, sizeof(g_status), fmt, ap);
    va_end(ap);
    g_status_kind = STATUS_ERROR;
}

/* ---- Header ---- */

void ui_draw_header(const SyncState *state, AppView view) {
    char right[48];
    bool online = state->net_ready && state->dhcp_ok;
    snprintf(right, sizeof(right), "%s", state->ip[0] ? state->ip : "offline");
    gui_header(view_title(view), right, online ? HEX_OK : HEX_ERR);

    /* Tabs centred between the L1 / R1 glyphs that cycle them. */
    float tabs_w = 4;
    for (int i = 0; i < APP_VIEW_COUNT; i++) tabs_w += gui_text_w(FONT_SMALL, g_tab_labels[i]) + 20;
    float l1 = gui_button_w("L1"), r1 = gui_button_w("R1");
    float total = l1 + 8 + tabs_w + 8 + r1;
    float x = (GUI_W - total) / 2;
    float cy = TABS_Y + TABS_H / 2.0f;
    gui_button(x, cy, "L1");
    gui_tabs(x + l1 + 8, TABS_Y, TABS_H, g_tab_labels, APP_VIEW_COUNT, (int)view);
    gui_button(x + l1 + 8 + tabs_w + 8, cy, "R1");

    draw_status_banner();
    draw_footer(view);
}

/* ---- Game catalog ---- */

void ui_draw_roms(const RomCatalog *catalog, int selected, int scroll) {
    list_frame("PS2 games on the server", g_catalog_badge[0] ? g_catalog_badge : NULL,
               HEX_WARN, catalog->count, scroll);

    if (catalog->count == 0) {
        list_empty(catalog->last_error[0] ? catalog->last_error
                                          : "No games yet. Settings > Refresh catalog fetches it.");
        detail_empty("GAME");
        return;
    }

    for (int i = 0; i < LIST_ROWS; i++) {
        int idx = scroll + i;
        if (idx >= catalog->count) break;
        const RomEntry *r = &catalog->items[idx];
        Row row = list_row(i, idx == selected);

        float x = row.x;
        bool installed = rom_installed(r);
        const DownloadEntry *dl = rom_download(r);
        if (installed)
            gui_icon_check(x + 6, row.y + ROW_H / 2 - 1, 12, row.selected ? HEX_INK : HEX_OK);
        else if (dl && dl->status != DL_STATUS_COMPLETED)
            gui_circle(x + 6, row.y + ROW_H / 2 - 1, 3.5f,
                       row.selected ? HEX_INK : dl_status_hex(dl->status));
        else
            gui_circle(x + 6, row.y + ROW_H / 2 - 1, 2, row.selected ? HEX_INK : HEX_MUTED);
        x += 18;

        char size[24];
        format_size(r->size, size, sizeof(size));
        float rw = row_right(&row, size);
        gui_text_mid(x, row.y, ROW_H - 2, FONT_BODY, row.fg, GUI_LEFT,
                     row.x + row.w - rw - x, r->name[0] ? r->name : r->filename);
    }

    if (selected < 0 || selected >= catalog->count) { detail_empty("GAME"); return; }
    const RomEntry *r = &catalog->items[selected];
    Detail d = detail_begin("GAME", r->name[0] ? r->name : r->filename);

    char size[24];
    format_size(r->size, size, sizeof(size));
    bool installed = rom_installed(r);
    const DownloadEntry *dl = rom_download(r);
    const char *pills[4] = { "PS2", r->is_cd ? "CD" : "DVD", size,
                             installed ? "Installed" : (dl ? dl_status_label(dl->status) : NULL) };
    uint32_t hexes[4] = { HEX_PS2, HEX_ACCENT2, HEX_DIM,
                          installed ? HEX_OK : (dl ? dl_status_hex(dl->status) : HEX_DIM) };
    detail_pills(&d, pills, hexes, 4);
    detail_rule(&d);
    detail_kv(&d, "Serial", r->serial, HEX_TEXT);
    detail_kv_wrap(&d, "File", r->filename);
    detail_kv(&d, "Format", r->extract_format[0] ? r->extract_format : "iso", HEX_TEXT);
    if (r->is_bundle) {
        char files[16];
        snprintf(files, sizeof(files), "%d", r->file_count);
        detail_kv(&d, "Files", files, HEX_TEXT);
    }
    detail_note(&d, installed ? "Already on this console. Cross downloads it again."
                              : "Cross installs it now, Square adds it to the queue.",
                HEX_MUTED);
}

/* ---- Installed games ---- */

void ui_draw_local(const LocalRomList *list, int selected, int scroll) {
    list_frame("Installed on this console", NULL, 0, list->count, scroll);

    if (list->count == 0) {
        list_empty(list->last_error[0] ? list->last_error : "No games installed yet.");
        detail_empty("INSTALLED GAME");
        return;
    }

    for (int i = 0; i < LIST_ROWS; i++) {
        int idx = scroll + i;
        if (idx >= list->count) break;
        const LocalRom *r = &list->items[idx];
        Row row = list_row(i, idx == selected);
        float x = row.x + row_tag(&row, r->is_cd ? "CD" : "DVD", HEX_ACCENT2);
        char size[24];
        format_size(r->size, size, sizeof(size));
        float rw = row_right(&row, size);
        gui_text_mid(x, row.y, ROW_H - 2, FONT_BODY, row.fg, GUI_LEFT,
                     row.x + row.w - rw - x, r->name[0] ? r->name : r->filename);
    }

    if (selected < 0 || selected >= list->count) { detail_empty("INSTALLED GAME"); return; }
    const LocalRom *r = &list->items[selected];
    Detail d = detail_begin("INSTALLED GAME", r->name[0] ? r->name : r->filename);
    char size[24];
    format_size(r->size, size, sizeof(size));
    const char *pills[3] = { "PS2", r->is_cd ? "CD" : "DVD", size };
    uint32_t hexes[3] = { HEX_PS2, HEX_ACCENT2, HEX_DIM };
    detail_pills(&d, pills, hexes, 3);
    detail_rule(&d);
    detail_kv(&d, "Serial", r->serial, HEX_TEXT);
    detail_kv_wrap(&d, "Location", r->path);
    detail_note(&d, "Cross deletes this game from the console (asks first).", HEX_MUTED);
}

/* ---- Virtual memory cards ---- */

static const char *vmc_tag(const SaveVmc *v) {
    return v->is_ps1 ? "PS1" : (v->has_ecc ? "PS2" : "MC2");
}

void ui_draw_saves(const SaveVmcList *list, int selected, int scroll) {
    list_frame("Card images in VMC/", NULL, 0, list->count, scroll);

    if (list->count == 0) {
        list_empty(list->last_error[0] ? list->last_error : "No card images found.");
        detail_empty("CARD IMAGE");
        return;
    }

    for (int i = 0; i < LIST_ROWS; i++) {
        int idx = scroll + i;
        if (idx >= list->count) break;
        const SaveVmc *v = &list->items[idx];
        Row row = list_row(i, idx == selected);
        float x = row.x + row_tag(&row, vmc_tag(v), v->is_ps1 ? HEX_PS1 : HEX_PS2);
        char size[24];
        format_size(v->size, size, sizeof(size));
        float rw = row_right(&row, size);
        gui_text_mid(x, row.y, ROW_H - 2, FONT_BODY, row.fg, GUI_LEFT,
                     row.x + row.w - rw - x, v->name[0] ? v->name : v->filename);
    }

    if (selected < 0 || selected >= list->count) { detail_empty("CARD IMAGE"); return; }
    const SaveVmc *v = &list->items[selected];
    Detail d = detail_begin("CARD IMAGE", v->name[0] ? v->name : v->filename);
    char size[24];
    format_size(v->size, size, sizeof(size));
    const char *pills[2] = { vmc_tag(v), size };
    uint32_t hexes[2] = { v->is_ps1 ? HEX_PS1 : HEX_PS2, HEX_DIM };
    detail_pills(&d, pills, hexes, 2);
    detail_rule(&d);
    detail_kv(&d, "Serial", v->serial[0] ? v->serial : "unknown", HEX_TEXT);
    detail_kv_wrap(&d, "File", v->filename);
    detail_kv(&d, "Format", v->is_ps1 ? "PS1 card" : (v->has_ecc ? "PS2 card (ECC)" : "PS2 card"),
              HEX_TEXT);
    detail_note(&d, "Cross: upload this card (split per game) or rescan. Square pulls all "
                    "server saves into VMC/.", HEX_MUTED);
}

/* ---- Physical memory card ---- */

void ui_draw_mcard(const McGameList *list, int selected, int scroll) {
    int port = list->port == 1 ? 1 : 0;
    static const char *const slots[2] = { "Slot 1", "Slot 2" };
    list_frame_ex(NULL, slots, 2, port,
                  list->count > 0 ? (list->is_ps1 ? "PS1" : "PS2") : NULL,
                  list->is_ps1 ? HEX_PS1 : HEX_PS2, list->count, scroll);

    if (list->count == 0) {
        list_empty(list->last_error[0] ? list->last_error : "No game saves on this card.");
    } else {
        for (int i = 0; i < LIST_ROWS; i++) {
            int idx = scroll + i;
            if (idx >= list->count) break;
            const McGame *g = &list->items[idx];
            Row row = list_row(i, idx == selected);
            char size[24];
            format_size(g->total_size, size, sizeof(size));
            float rw = row_right(&row, size);
            float x = row.x;
            const char *label = g->name[0] ? g->name : (g->serial[0] ? g->serial : g->dir);
            gui_text_mid(x, row.y, ROW_H - 2, FONT_BODY, row.fg, GUI_LEFT,
                         row.x + row.w - rw - x, label);
        }
    }

    Detail d;
    if (list->count > 0 && selected >= 0 && selected < list->count) {
        const McGame *g = &list->items[selected];
        d = detail_begin("SAVE", g->name[0] ? g->name : (g->serial[0] ? g->serial : g->dir));
        char size[24], files[16];
        format_size(g->total_size, size, sizeof(size));
        snprintf(files, sizeof(files), "%d", g->file_count);
        const char *pills[2] = { list->is_ps1 ? "PS1" : "PS2", size };
        uint32_t hexes[2] = { list->is_ps1 ? HEX_PS1 : HEX_PS2, HEX_DIM };
        detail_pills(&d, pills, hexes, 2);
        detail_rule(&d);
        detail_kv(&d, "Serial", g->serial[0] ? g->serial : "unknown", HEX_TEXT);
        detail_kv(&d, "Folder", g->dir, HEX_TEXT);
        detail_kv(&d, "Files", files, HEX_TEXT);
    } else {
        d = detail_begin("SAVE", NULL);
    }
    detail_kv(&d, "GameID", mmce_name(port),
              g_mmce_mode_disp[port] == 0 ? HEX_MUTED : HEX_ACCENT2);
    detail_note(&d, "Cross: upload, restore, switch a MemCard Pro / SD2PSX to this game, "
                    "or rescan the card.", HEX_MUTED);
}

/* ---- Server saves ---- */

void ui_draw_server(const ServerSaveList *list, int selected, int scroll) {
    list_frame_ex(NULL, g_source_labels, 3, g_server_source, NULL, 0, list->count, scroll);

    if (list->count == 0) {
        list_empty(list->last_error[0] ? list->last_error
                                       : "No saves on the server. Cross > Refresh to retry.");
        detail_empty("SERVER SAVE");
        return;
    }

    for (int i = 0; i < LIST_ROWS; i++) {
        int idx = scroll + i;
        if (idx >= list->count) break;
        const ServerSave *s = &list->items[idx];
        Row row = list_row(i, idx == selected);
        float x = row.x + row_tag(&row, s->is_ps1 ? "PS1" : "PS2", s->is_ps1 ? HEX_PS1 : HEX_PS2);
        float rw = 0;
        if (s->local) {
            gui_icon_check(row.x + row.w - 6, row.y + ROW_H / 2 - 1, 12,
                           row.selected ? HEX_INK : HEX_OK);
            rw = 22;
        }
        gui_text_mid(x, row.y, ROW_H - 2, FONT_BODY, row.fg, GUI_LEFT,
                     row.x + row.w - rw - x, s->name[0] ? s->name : s->serial);
    }

    if (selected < 0 || selected >= list->count) { detail_empty("SERVER SAVE"); return; }
    const ServerSave *s = &list->items[selected];
    Detail d = detail_begin("SERVER SAVE", s->name[0] ? s->name : s->serial);
    const char *pills[2] = { s->is_ps1 ? "PS1" : "PS2", s->local ? "On a card" : "Server only" };
    uint32_t hexes[2] = { s->is_ps1 ? HEX_PS1 : HEX_PS2, s->local ? HEX_OK : HEX_INFO };
    detail_pills(&d, pills, hexes, 2);
    detail_rule(&d);
    detail_kv(&d, "Serial", s->serial, HEX_TEXT);
    if (s->timestamp) {
        time_t t = (time_t)s->timestamp;
        struct tm *tm = gmtime(&t);
        char when[32];
        if (tm) strftime(when, sizeof(when), "%Y-%m-%d %H:%M", tm);
        else snprintf(when, sizeof(when), "%lu", (unsigned long)s->timestamp);
        detail_kv(&d, "Saved", when, HEX_TEXT);
    }
    detail_kv(&d, "Sync with", g_source_labels[g_server_source], HEX_ACCENT2);
    detail_note(&d, "Cross: download to / upload from the source. SELECT changes "
                    "the source.", HEX_MUTED);
}

/* ---- Downloads ---- */

void ui_draw_downloads(const DownloadList *list, int selected, int scroll) {
    list_frame("Download queue", NULL, 0, list->count, scroll);

    if (list->count == 0) {
        list_empty("No downloads queued. Queue games from the catalog with Square.");
        detail_empty("DOWNLOAD");
        return;
    }

    for (int i = 0; i < LIST_ROWS; i++) {
        int idx = scroll + i;
        if (idx >= list->count) break;
        const DownloadEntry *e = &list->items[idx];
        Row row = list_row(i, idx == selected);

        const char *st = dl_status_label(e->status);
        float pw = gui_pill_w(FONT_TINY, st);
        float col_w = gui_pill_w(FONT_TINY, "Queued");
        gui_pill(row.x + row.w - pw, row.y + 3, ROW_H - 8, FONT_TINY,
                 row.selected ? HEX_INK : dl_status_hex(e->status),
                 row.selected ? dl_status_hex(e->status) : HEX_INK, st);

        float frac = e->total > 0 ? (float)((double)e->offset / (double)e->total) : 0;
        float bx = row.x + row.w - col_w - 70;
        gui_bar(bx, row.y + ROW_H / 2 - 4, 58, 6, frac,
                row.selected ? HEX_INK : (e->status == DL_STATUS_COMPLETED ? HEX_OK : HEX_ACCENT));
        gui_text_mid(row.x, row.y, ROW_H - 2, FONT_BODY, row.fg, GUI_LEFT, bx - row.x - 10,
                     e->name[0] ? e->name : e->filename);
    }

    if (selected < 0 || selected >= list->count) { detail_empty("DOWNLOAD"); return; }
    const DownloadEntry *e = &list->items[selected];
    Detail d = detail_begin("DOWNLOAD", e->name[0] ? e->name : e->filename);
    const char *pills[2] = { dl_status_label(e->status), e->is_cd ? "CD" : "DVD" };
    uint32_t hexes[2] = { dl_status_hex(e->status), HEX_ACCENT2 };
    detail_pills(&d, pills, hexes, 2);

    float frac = e->total > 0 ? (float)((double)e->offset / (double)e->total) : 0;
    gui_bar(d.x, d.y, d.w, 10, frac, e->status == DL_STATUS_COMPLETED ? HEX_OK : HEX_ACCENT);
    d.y += 16;
    char done_s[24], total_s[24], line[64];
    format_size(e->offset, done_s, sizeof(done_s));
    format_size(e->total, total_s, sizeof(total_s));
    snprintf(line, sizeof(line), "%s / %s", done_s, total_s);
    gui_text(d.x, d.y, FONT_SMALL, HEX_DIM, GUI_LEFT, line);
    gui_textf(d.x + d.w, d.y, FONT_SMALL, HEX_ACCENT2, GUI_RIGHT, "%u%%",
              (unsigned)(frac * 100.0f));
    d.y += gui_line_h(FONT_SMALL) + 8;
    detail_rule(&d);
    detail_kv(&d, "Serial", e->serial, HEX_TEXT);
    detail_kv_wrap(&d, "Target", e->target_path);
}

/* ---- Settings ---- */

static void cfg_row(float x, float *y, float w, const char *label, const char *value, uint32_t hex) {
    gui_text_mid(x, *y, KV_ROW_H, FONT_TINY, HEX_DIM, GUI_LEFT, 96, label);
    gui_text_mid(x + 100, *y, KV_ROW_H, FONT_SMALL, hex, GUI_LEFT, w - 100,
                 value && *value ? value : "-");
    *y += KV_ROW_H;
}

/* One focusable Settings row.  Value rows show "< value >" (Left/Right or
 * CROSS change it); action rows show the button that runs them. */
static void cfg_item(float x, float *y, float w, bool focused, const char *label,
                     const char *value, uint32_t value_hex, bool danger)
{
    float h = 26;
    if (focused) gui_rrect(x - 6, *y, w + 12, h, 6, danger ? 0x3A2A16 : HEX_PANEL_HI);
    if (focused) gui_rrect(x - 6, *y, 4, h, 2, danger ? HEX_WARN : HEX_ACCENT);
    gui_text_mid(x + 2, *y, h, FONT_SMALL, danger ? HEX_WARN : (focused ? HEX_TEXT : HEX_DIM),
                 GUI_LEFT, 130, label);
    float vx = x + 132, vw = w - 132;
    if (value) {
        if (focused) {
            gui_icon_arrow(vx + 6, *y + h / 2, 7, false, HEX_ACCENT2);
            gui_icon_arrow(vx + vw - 6, *y + h / 2, 7, true, HEX_ACCENT2);
        }
        gui_text_mid(vx + vw / 2, *y, h, FONT_SMALL, value_hex, GUI_CENTER, vw - 28, value);
    } else if (focused) {
        float bw = gui_button_w("X");
        gui_button(vx + vw - bw - 4, *y + h / 2, "X");
    }
    *y += h + 2;
}

void ui_draw_config(const SyncState *state, int selected_row) {
    float gap = 10;
    float w = (GUI_W - 2 * GUI_MARGIN - gap) / 2;
    float lx = GUI_MARGIN, rx = GUI_MARGIN + w + gap;
    float top = CONTENT_Y + 2, h = CONTENT_H - 4;

    /* Left: server and network */
    gui_card(lx, top, w, h, "Server & network", HEX_ACCENT);
    float x = lx + 14, y = top + 40, iw = w - 28;
    cfg_row(x, &y, iw, "Server", state->server_url[0] ? state->server_url : "(not set)",
            state->server_url[0] ? HEX_TEXT : HEX_WARN);
    cfg_row(x, &y, iw, "API key", state->api_key[0] ? "set" : "not set",
            state->api_key[0] ? HEX_TEXT : HEX_WARN);
    cfg_row(x, &y, iw, "Console ID", state->console_id, HEX_TEXT);
    cfg_row(x, &y, iw, "Mode", state->use_static_ip ? "static IP" : "DHCP", HEX_TEXT);
    if (state->use_static_ip) {
        cfg_row(x, &y, iw, "Static IP", state->static_ip, HEX_TEXT);
        cfg_row(x, &y, iw, "Netmask", state->static_netmask, HEX_TEXT);
        cfg_row(x, &y, iw, "Gateway", state->static_gateway, HEX_TEXT);
    }
    bool online = state->net_ready && state->dhcp_ok;
    char net[48];
    snprintf(net, sizeof(net), "%s (%s)", online ? "ready" : "not ready",
             state->ip[0] ? state->ip : "no ip");
    cfg_row(x, &y, iw, "Network", net, online ? HEX_OK : HEX_ERR);
    gui_text_wrap(x, top + h - 44, FONT_TINY, HEX_MUTED, iw, 2,
                  "Edit " CONFIG_PATH " in uLaunchELF to change these.");

    /* Right: the editable settings, then where things are stored. */
    gui_card(rx, top, w, h, "Storage & devices", HEX_INFO);
    x = rx + 14;
    y = top + 38;
    iw = w - 28;

    cfg_item(x, &y, iw, selected_row == CFG_ROW_STORAGE, "Install to",
             storage_pref_label(state->storage_pref), HEX_TEXT, false);
    cfg_item(x, &y, iw, selected_row == CFG_ROW_GAMEID1, "GameID slot 1", mmce_name(0),
             g_mmce_mode_disp[0] == 0 ? HEX_MUTED : HEX_ACCENT2, false);
    cfg_item(x, &y, iw, selected_row == CFG_ROW_GAMEID2, "GameID slot 2", mmce_name(1),
             g_mmce_mode_disp[1] == 0 ? HEX_MUTED : HEX_ACCENT2, false);
    cfg_item(x, &y, iw, selected_row == CFG_ROW_REFRESH, "Refresh catalog", NULL, 0, false);
    cfg_item(x, &y, iw, selected_row == CFG_ROW_FORMAT, "Format internal HDD", NULL, 0, true);

    y += 4;
    gui_rect(x, y, iw, 1, HEX_LINE);
    y += 6;
    cfg_row(x, &y, iw, "Active", storage_backend_label(state),
            state->usb_ready ? HEX_OK : HEX_WARN);
    cfg_row(x, &y, iw, "Queue file",
            state->storage_backend == STORAGE_BACKEND_HDLOADER ? HDL_DOWNLOADS_FILE
                                                               : roms_downloads_file(),
            HEX_TEXT);
    cfg_row(x, &y, iw, "Catalog cache", g_cache_desc, HEX_TEXT);

    const char *help = "";
    switch (selected_row) {
        case CFG_ROW_STORAGE: help = "Saved at once; relaunch to apply."; break;
        case CFG_ROW_GAMEID1:
        case CFG_ROW_GAMEID2: help = "MemCard Pro / SD2PSX: off, auto, gen1, gen2."; break;
        case CFG_ROW_REFRESH: help = "Rescans the server and refetches the catalog."; break;
        case CFG_ROW_FORMAT:  help = "Erases the internal HDD for OPL (asks twice)."; break;
        default: break;
    }
    gui_text_fit(x, top + h - 18, FONT_TINY, HEX_MUTED, GUI_LEFT, iw, help);
}

/* ---- Modal cards ---- */

void ui_draw_transfer(const char *name, const char *target,
                      uint64_t done, uint64_t total, uint64_t bps,
                      uint32_t elapsed_ms)
{
    gui_dim();
    float w = 460, h = 206;
    float x = (GUI_W - w) / 2, y = (GUI_H - h) / 2 - 6;
    gui_card(x, y, w, h, "Downloading", HEX_ACCENT);

    float ix = x + 20, iw = w - 40, iy = y + 42;
    gui_text_fit(ix, iy, FONT_BODY, HEX_TEXT, GUI_LEFT, iw, name && *name ? name : "Game");
    iy += gui_line_h(FONT_BODY) + 2;
    if (target && *target) {
        char where[96];
        snprintf(where, sizeof(where), "Writing to %s", target);
        gui_text_fit(ix, iy, FONT_SMALL, HEX_DIM, GUI_LEFT, iw, where);
    }
    iy += gui_line_h(FONT_SMALL) + 10;

    float frac = total > 0 ? (float)((double)done / (double)total) : 0;
    gui_bar(ix, iy, iw, 14, frac, HEX_ACCENT);
    iy += 22;

    char done_s[24], total_s[24], line[96];
    format_size(done, done_s, sizeof(done_s));
    format_size(total, total_s, sizeof(total_s));
    snprintf(line, sizeof(line), "%s / %s", done_s, total_s);
    gui_text(ix, iy, FONT_SMALL, HEX_TEXT, GUI_LEFT, line);
    gui_textf(ix + iw, iy, FONT_SMALL, HEX_ACCENT2, GUI_RIGHT, "%u%%", (unsigned)(frac * 100.0f));
    iy += gui_line_h(FONT_SMALL) + 4;

    char rate[24], elapsed[16], remain[16];
    format_rate(bps, rate, sizeof(rate));
    format_duration(elapsed_ms / 1000, elapsed, sizeof(elapsed));
    if (bps > 0 && total > done)
        format_duration((uint32_t)((total - done) / bps), remain, sizeof(remain));
    else
        snprintf(remain, sizeof(remain), "--:--");
    snprintf(line, sizeof(line), "Speed %s    Elapsed %s    Remaining %s", rate, elapsed, remain);
    gui_text(ix, iy, FONT_SMALL, HEX_DIM, GUI_LEFT, line);

    float fy = y + h - 30;
    gui_rect(x + 1, fy - 6, w - 2, 1, HEX_LINE);
    float bx = ix;
    bx += gui_text_mid(bx, fy - 4, 24, FONT_SMALL, HEX_DIM, GUI_LEFT, 0, "Hold ") ;
    bx += gui_button(bx + 2, fy + 8, "O") + 6;
    gui_text_mid(bx, fy - 4, 24, FONT_SMALL, HEX_DIM, GUI_LEFT, 0, "to pause");
}

static void draw_dialog(const char *title, const char *message, uint32_t tone,
                        const GuiHint *hints, int nhints)
{
    char lines[8][GUI_WRAP_LINE];
    float w = 440;
    int n = gui_wrap(message, FONT_BODY, w - 48, lines, 8);
    int lh = gui_line_h(FONT_BODY);
    float h = 30 + 18 + n * lh + 18 + 36;
    float x = (GUI_W - w) / 2, y = (GUI_H - h) / 2;
    gui_card(x, y, w, h, title, tone);
    for (int i = 0; i < n; i++)
        gui_text(x + 24, y + 30 + 16 + i * lh, FONT_BODY, HEX_TEXT, GUI_LEFT, lines[i]);

    float fy = y + h - 34;
    gui_rect(x + 1, fy, w - 2, 1, HEX_LINE);
    float total = 0;
    for (int i = 0; i < nhints; i++)
        total += gui_button_w(hints[i].button) + 6 + gui_text_w(FONT_SMALL, hints[i].label) +
                 (i ? 24 : 0);
    float bx = x + w - 20 - total, cy = fy + 17;
    for (int i = 0; i < nhints; i++) {
        if (i) bx += 24;
        bx += gui_button(bx, cy, hints[i].button) + 6;
        bx += gui_text_mid(bx, cy - 12, 24, FONT_SMALL, HEX_TEXT, GUI_LEFT, 0, hints[i].label);
    }
}

void ui_draw_confirm(const char *title, const char *message) {
    static const GuiHint hints[] = { {"O", "Cancel"}, {"X", "Confirm"} };
    gui_dim();
    draw_dialog(title, message, HEX_WARN, hints, 2);
}

void ui_draw_info(const char *title, const char *message) {
    static const GuiHint hints[] = { {"O", "Close"} };
    gui_dim();
    draw_dialog(title, message, HEX_INFO, hints, 1);
}

void ui_draw_menu(const char *title, const char *const *items, int count, int selected) {
    static const GuiHint hints[] = { {"O", "Cancel"}, {"X", "Select"} };
    gui_dim();
    if (count > 8) count = 8;
    float w = 400, row = 28;
    float h = 30 + 10 + count * row + 10 + 36;
    float x = (GUI_W - w) / 2, y = (GUI_H - h) / 2;
    gui_card(x, y, w, h, title, HEX_ACCENT);
    float ry = y + 30 + 10;
    for (int i = 0; i < count; i++) {
        bool on = (i == selected);
        if (on) gui_rrect(x + 12, ry + 1, w - 24, row - 2, 6, HEX_ACCENT);
        gui_text_mid(x + 26, ry, row, FONT_BODY, on ? HEX_INK : HEX_TEXT, GUI_LEFT, w - 52,
                     items[i]);
        ry += row;
    }

    float fy = y + h - 34;
    gui_rect(x + 1, fy, w - 2, 1, HEX_LINE);
    float total = 0;
    for (int i = 0; i < 2; i++)
        total += gui_button_w(hints[i].button) + 6 + gui_text_w(FONT_SMALL, hints[i].label) +
                 (i ? 24 : 0);
    float bx = x + w - 20 - total, cy = fy + 17;
    for (int i = 0; i < 2; i++) {
        if (i) bx += 24;
        bx += gui_button(bx, cy, hints[i].button) + 6;
        bx += gui_text_mid(bx, cy - 12, 24, FONT_SMALL, HEX_TEXT, GUI_LEFT, 0, hints[i].label);
    }
}

void ui_draw_message(const char *title, const char *message) {
    static const GuiHint hints[] = { {"O", "Exit"} };
    gui_begin();
    gui_vgrad(0, -32, GUI_W, GUI_H + 64, HEX_BG, 0x0B1118);
    draw_logo(GUI_W / 2, 40);
    draw_dialog(title, message, HEX_ERR, hints, 1);
    gui_end(true);
}
