// GameSync - original Xbox client.
//
// SDL2 + SDL_ttf UI in the shared GameSync design: header bar with a tab
// strip, a list panel with a detail panel beside it, a status banner and a
// footer of controller hints. Dialogs and progress are modal cards drawn
// over a dimmed screen.

#include <SDL.h>
#include <SDL_ttf.h>
#include <hal/debug.h>
#include <hal/video.h>
#include <hal/xbox.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include <xboxkrnl/xboxkrnl.h>

#include "bundle.h"
#include "config.h"
#include "games.h"
#include "network.h"
#include "saves.h"
#include "state.h"
#include "sync.h"
#include "ui.h"

#ifndef APP_VERSION
#define APP_VERSION "dev"
#endif

#define LIST_VISIBLE   9   // rows the list panel shows at once

// Settings rows: 0..10 are config.txt fields, the rest are actions.
#define CFG_ROW_REFRESH_CATALOG 11
#define CFG_ROW_CLEAR_HASHES    12
#define CFG_ROW_RELOAD_CONFIG   13
#define CONFIG_ROWS             14

static XboxSaveList g_list;
static XboxRomList  g_roms;
static XboxInstalledGameList g_installed;
static XboxDriveSpace g_f_space;
static XboxConfig   g_cfg;
static SyncPlan     g_plan;
static int          g_plan_loaded = 0;
static char         g_status[200] = "Comparing saves with the server...";
typedef enum {
    UI_STATUS_INFO_KIND,
    UI_STATUS_BUSY_KIND,
    UI_STATUS_SUCCESS_KIND,
    UI_STATUS_ERROR_KIND,
} StatusKind;
static StatusKind   g_status_kind = UI_STATUS_INFO_KIND;
static char         g_local_ip[16] = "0.0.0.0";
static char         g_server_text[64] = "";

typedef enum {
    TAB_SAVES = 0,
    TAB_GAMES = 1,
    TAB_INSTALLED = 2,
    TAB_CONFIG = 3,
    TAB_COUNT = 4,
} UiTab;
static UiTab g_tab = TAB_SAVES;
static int   g_roms_loaded = 0;
static CatalogSource g_catalog_source = CATALOG_FROM_SERVER;
static int   g_installed_loaded = 0;
static int   g_f_space_loaded = 0;

static void set_status_kind(StatusKind kind, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    g_status_kind = kind;
    vsnprintf(g_status, sizeof(g_status), fmt, ap);
    va_end(ap);
}

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

static const char *fmt_kb(uint64_t bytes, char *buf, size_t buflen)
{
    snprintf(buf, buflen, "%llu KB", (unsigned long long)((bytes + 1023) / 1024));
    return buf;
}

// "512 KB", "37.4 MB", "4.2 GB" - integer maths only.
static const char *fmt_size(uint64_t bytes, char *buf, size_t buflen)
{
    const uint64_t kb = 1024ULL;
    const uint64_t mb = 1024ULL * kb;
    const uint64_t gb = 1024ULL * mb;
    if (bytes >= gb) {
        uint64_t tenths = (bytes * 10ULL + gb / 2) / gb;
        snprintf(buf, buflen, "%llu.%llu GB",
                 (unsigned long long)(tenths / 10),
                 (unsigned long long)(tenths % 10));
    } else if (bytes >= mb) {
        uint64_t tenths = (bytes * 10ULL + mb / 2) / mb;
        if (tenths >= 1000) {
            snprintf(buf, buflen, "%llu MB", (unsigned long long)(tenths / 10));
        } else {
            snprintf(buf, buflen, "%llu.%llu MB",
                     (unsigned long long)(tenths / 10),
                     (unsigned long long)(tenths % 10));
        }
    } else {
        snprintf(buf, buflen, "%llu KB",
                 (unsigned long long)((bytes + kb - 1) / kb));
    }
    return buf;
}

static void short_hash(const char *src, char *out, int out_len)
{
    if (!out || out_len <= 0) return;
    if (!src || !src[0]) {
        snprintf(out, out_len, "n/a");
        return;
    }

    int n = (int)strlen(src);
    if (n <= 20) {
        snprintf(out, out_len, "%s", src);
    } else {
        snprintf(out, out_len, "%.10s...%.6s", src, src + n - 6);
    }
}

// Unix epoch -> "2024-05-01 13:22" (UTC).
static void fmt_timestamp(uint32_t ts, char *out, int out_len)
{
    if (!out || out_len <= 0) return;
    if (ts == 0) {
        snprintf(out, out_len, "n/a");
        return;
    }
    // civil_from_days from Howard Hinnant's public-domain date algorithms,
    // https://howardhinnant.github.io/date_algorithms.html
    uint32_t days = ts / 86400u, rem = ts % 86400u;
    uint32_t z = days + 719468u;
    uint32_t era = z / 146097u;
    uint32_t doe = z - era * 146097u;
    uint32_t yoe = (doe - doe / 1460u + doe / 36524u - doe / 146096u) / 365u;
    uint32_t y = yoe + era * 400u;
    uint32_t doy = doe - (365u * yoe + yoe / 4u - yoe / 100u);
    uint32_t mp = (5u * doy + 2u) / 153u;
    uint32_t d = doy - (153u * mp + 2u) / 5u + 1u;
    uint32_t m = mp < 10u ? mp + 3u : mp - 9u;
    if (m <= 2u) y++;
    snprintf(out, out_len, "%04u-%02u-%02u %02u:%02u",
             (unsigned)y, (unsigned)m, (unsigned)d,
             (unsigned)(rem / 3600u), (unsigned)((rem / 60u) % 60u));
}

// Server ISO timestamps ("2024-05-01T13:22:11.123") -> "2024-05-01 13:22".
static void fmt_server_time(const char *iso, char *out, int out_len)
{
    if (!iso || !iso[0]) {
        snprintf(out, out_len, "n/a");
        return;
    }
    snprintf(out, out_len, "%.16s", iso);
    char *t = strchr(out, 'T');
    if (t) *t = ' ';
}

static void fmt_duration(uint32_t secs, char *out, int out_len)
{
    if (secs >= 3600u) {
        snprintf(out, out_len, "%u:%02u:%02u", (unsigned)(secs / 3600u),
                 (unsigned)((secs / 60u) % 60u), (unsigned)(secs % 60u));
    } else {
        snprintf(out, out_len, "%u:%02u", (unsigned)(secs / 60u),
                 (unsigned)(secs % 60u));
    }
}

// ---------------------------------------------------------------------------
// Status colours and labels
// ---------------------------------------------------------------------------

static uint32_t status_color(TitleStatus s)
{
    switch (s) {
    case TITLE_STATUS_UP_TO_DATE:    return UI_HEX_OK;
    case TITLE_STATUS_NEEDS_UPLOAD:  return UI_HEX_WARN;
    case TITLE_STATUS_NEEDS_DOWNLOAD:return UI_HEX_INFO;
    case TITLE_STATUS_CONFLICT:      return UI_HEX_ERR;
    case TITLE_STATUS_SERVER_ONLY:   return UI_HEX_INFO;
    default:                         return UI_HEX_MUTED;
    }
}

static const char *status_label(TitleStatus s)
{
    switch (s) {
    case TITLE_STATUS_UP_TO_DATE:    return "Synced";
    case TITLE_STATUS_NEEDS_UPLOAD:  return "Upload";
    case TITLE_STATUS_NEEDS_DOWNLOAD:return "Download";
    case TITLE_STATUS_CONFLICT:      return "Conflict";
    case TITLE_STATUS_SERVER_ONLY:   return "Server";
    default:                         return "Local";
    }
}

static const char *status_hint(TitleStatus s)
{
    switch (s) {
    case TITLE_STATUS_UP_TO_DATE:
        return "Same save on the server. Nothing to do.";
    case TITLE_STATUS_NEEDS_UPLOAD:
        return "Only this Xbox changed. A uploads it.";
    case TITLE_STATUS_NEEDS_DOWNLOAD:
        return "The server has a newer save. A downloads it.";
    case TITLE_STATUS_CONFLICT:
        return "Both sides changed. A or Y lets you pick upload or download.";
    case TITLE_STATUS_SERVER_ONLY:
        return "Not on this Xbox yet. A downloads it.";
    default:
        return "Not compared yet. A compares every save with the server.";
    }
}

static uint32_t status_tone(void)
{
    switch (g_status_kind) {
    case UI_STATUS_BUSY_KIND:    return UI_HEX_INFO;
    case UI_STATUS_SUCCESS_KIND: return UI_HEX_OK;
    case UI_STATUS_ERROR_KIND:   return UI_HEX_ERR;
    default:                     return UI_HEX_ACCENT;
    }
}

// ---------------------------------------------------------------------------
// Layout
// ---------------------------------------------------------------------------

#define TABS_Y        70
#define TABS_H        26

#define PANEL_Y       104
#define PANEL_H       282
#define LIST_X        UI_MARGIN_X
#define LIST_W        364
#define DETAIL_X      (LIST_X + LIST_W + 8)
#define DETAIL_W      (UI_W - UI_MARGIN_X - DETAIL_X)

#define LIST_HEAD_H   34
#define ROW_H         26
#define ROWS_Y        (PANEL_Y + LIST_HEAD_H + 6)

#define BANNER_Y      (PANEL_Y + PANEL_H + 8)
#define BANNER_H      28

#define STATUS_PILL_W 64

static const char *tab_section(void)
{
    switch (g_tab) {
    case TAB_GAMES:     return "Game Catalog";
    case TAB_INSTALLED: return "Installed Games";
    case TAB_CONFIG:    return "Settings";
    default:            return "Saves";
    }
}

static int total_rows(void)
{
    if (g_tab == TAB_GAMES) return g_roms_loaded ? g_roms.count : 0;
    if (g_tab == TAB_INSTALLED) {
        return g_installed_loaded ? g_installed.count : 0;
    }
    if (g_tab == TAB_CONFIG) return CONFIG_ROWS;
    int n = g_list.title_count;
    if (g_plan_loaded) n += g_plan.server_only_count;
    return n;
}

// Right-aligned pills; returns the new right edge.
static int pill_left(int right, int y, int h, uint32_t bg, uint32_t fg,
                     const char *label)
{
    int w = ui_pill_w(UI_FONT_TINY, label);
    ui_pill(right - w, y, h, UI_FONT_TINY, bg, fg, label);
    return right - w - 6;
}

static void draw_tab_row(void)
{
    static const char *const labels[] = { "Saves", "Catalog", "Installed", "Settings" };
    // The triggers flank the strip: L = previous tab, R = next (wrapping).
    int lw = ui_button(UI_MARGIN_X, TABS_Y + TABS_H / 2, "L");
    int x = ui_tabs(UI_MARGIN_X + lw + 6, TABS_Y, TABS_H, labels, TAB_COUNT,
                    (int)g_tab);
    ui_button(x + 6, TABS_Y + TABS_H / 2, "R");

    // Context on the right of the strip.
    int right = UI_W - UI_MARGIN_X;
    int py = TABS_Y + 3, ph = TABS_H - 6;
    char buf[64];
    if (g_tab == TAB_SAVES) {
        if (!g_plan_loaded) {
            ui_text_mid(right, TABS_Y, TABS_H, UI_FONT_SMALL, UI_HEX_MUTED,
                        UI_RIGHT, 0, "Not compared yet");
            return;
        }
        if (g_plan.conflict_count) {
            snprintf(buf, sizeof(buf), "%d conflict", g_plan.conflict_count);
            right = pill_left(right, py, ph, UI_HEX_ERR, UI_HEX_INK, buf);
        }
        if (g_plan.download_count + g_plan.server_only_count) {
            snprintf(buf, sizeof(buf), "%d down",
                     g_plan.download_count + g_plan.server_only_count);
            right = pill_left(right, py, ph, UI_HEX_INFO, UI_HEX_INK, buf);
        }
        if (g_plan.upload_count) {
            snprintf(buf, sizeof(buf), "%d up", g_plan.upload_count);
            right = pill_left(right, py, ph, UI_HEX_WARN, UI_HEX_INK, buf);
        }
        snprintf(buf, sizeof(buf), "%d synced", g_plan.up_to_date_count);
        pill_left(right, py, ph, UI_HEX_OK, UI_HEX_INK, buf);
    } else if (g_tab == TAB_GAMES) {
        snprintf(buf, sizeof(buf), "as %s",
                 games_format_name(games_config_format(&g_cfg)));
        right = pill_left(right, py, ph, UI_HEX_PANEL_HI, UI_HEX_DIM, buf);
        if (g_roms_loaded && g_catalog_source == CATALOG_FROM_CACHE_OFFLINE) {
            pill_left(right, py, ph, UI_HEX_WARN, UI_HEX_INK, "cached / offline");
        } else if (g_roms_loaded && g_catalog_source == CATALOG_FROM_CACHE) {
            pill_left(right, py, ph, UI_HEX_PANEL_HI, UI_HEX_DIM, "cached");
        }
    } else if (g_tab == TAB_INSTALLED) {
        if (g_f_space_loaded) {
            char free_sz[24];
            snprintf(buf, sizeof(buf), "F: %s free",
                     fmt_size(g_f_space.free_bytes, free_sz, sizeof(free_sz)));
            pill_left(right, py, ph, UI_HEX_PANEL_HI, UI_HEX_DIM, buf);
        }
    } else if (g_tab == TAB_CONFIG) {
        if (g_server_text[0]) {
            ui_text_mid(right, TABS_Y, TABS_H, UI_FONT_SMALL, UI_HEX_DIM,
                        UI_RIGHT, 170, g_server_text);
        }
    }
}

// Selection bar for a row; returns the text colour to use on it.
static uint32_t row_bg(int selected, int y)
{
    if (!selected) return UI_HEX_TEXT;
    ui_rrect(LIST_X + 6, y, LIST_W - 22, ROW_H - 2, 6, UI_HEX_ACCENT);
    return UI_HEX_INK;
}

// Fixed-width status pill so names line up.
static void status_pill(int x, int y, TitleStatus st, int selected)
{
    uint32_t c = status_color(st);
    ui_rrect(x, y, STATUS_PILL_W, 18, 9, selected ? UI_HEX_INK : c);
    ui_text_mid(x + STATUS_PILL_W / 2, y, 18, UI_FONT_TINY,
                selected ? c : UI_HEX_INK, UI_CENTER, 0, status_label(st));
}

static void row_title(int row_idx, const char **tid, const char **name,
                      TitleStatus *st, uint64_t *size, int *is_local)
{
    *is_local = row_idx < g_list.title_count;
    *st = TITLE_STATUS_UNKNOWN;
    if (*is_local) {
        const XboxSaveTitle *t = &g_list.titles[row_idx];
        *tid = t->title_id;
        *name = t->name[0] ? t->name : t->title_id;
        *size = t->total_size;
        if (g_plan_loaded) *st = sync_plan_status(&g_plan, t->title_id);
    } else {
        int j = row_idx - g_list.title_count;
        *tid  = g_plan.server_only_ids[j];
        *name = g_plan.server_only_names[j][0]
                    ? g_plan.server_only_names[j]
                    : g_plan.server_only_ids[j];
        *size = 0;
        *st = TITLE_STATUS_SERVER_ONLY;
    }
}

static void draw_save_row(int row_idx, int cursor, int y)
{
    int selected = (row_idx == cursor);
    uint32_t fg = row_bg(selected, y);

    const char *tid, *name;
    TitleStatus st;
    uint64_t size;
    int is_local;
    row_title(row_idx, &tid, &name, &st, &size, &is_local);

    status_pill(LIST_X + 12, y + 3, st, selected);

    char size_buf[24];
    if (is_local) fmt_kb(size, size_buf, sizeof(size_buf));
    else snprintf(size_buf, sizeof(size_buf), "server");
    int right = LIST_X + LIST_W - 24;
    int sw = ui_text_mid(right, y, ROW_H - 2, UI_FONT_SMALL,
                         selected ? UI_HEX_INK : UI_HEX_DIM, UI_RIGHT, 0, size_buf);

    int nx = LIST_X + 12 + STATUS_PILL_W + 8;
    ui_text_mid(nx, y, ROW_H - 2, UI_FONT_BODY, fg, UI_LEFT,
                right - sw - 10 - nx, name);
}

static void draw_game_row(int row_idx, int cursor, int y)
{
    if (row_idx < 0 || row_idx >= g_roms.count) return;
    int selected = (row_idx == cursor);
    uint32_t fg = row_bg(selected, y);
    const XboxRomEntry *r = &g_roms.roms[row_idx];

    const char *fmt = r->is_bundle ? "CCI" : "ISO";
    uint32_t pc = r->is_bundle ? UI_HEX_ACCENT : UI_HEX_INFO;
    ui_rrect(LIST_X + 12, y + 3, 38, 18, 9, selected ? UI_HEX_INK : pc);
    ui_text_mid(LIST_X + 31, y + 3, 18, UI_FONT_TINY,
                selected ? pc : UI_HEX_INK, UI_CENTER, 0, fmt);

    char sz[24];
    fmt_size(r->size, sz, sizeof(sz));
    int right = LIST_X + LIST_W - 24;
    int sw = ui_text_mid(right, y, ROW_H - 2, UI_FONT_SMALL,
                         selected ? UI_HEX_INK : UI_HEX_DIM, UI_RIGHT, 0, sz);
    int nx = LIST_X + 58;
    ui_text_mid(nx, y, ROW_H - 2, UI_FONT_BODY, fg, UI_LEFT,
                right - sw - 10 - nx, r->name);
}

static void draw_installed_row(int row_idx, int cursor, int y)
{
    if (row_idx < 0 || row_idx >= g_installed.count) return;
    int selected = (row_idx == cursor);
    uint32_t fg = row_bg(selected, y);
    const XboxInstalledGame *game = &g_installed.games[row_idx];

    // Installed check mark: a green dot keeps the row light.
    ui_circle(LIST_X + 20.5f, y + 12.0f, 4.0f, selected ? UI_HEX_INK : UI_HEX_OK);

    char sz[24];
    fmt_size(game->size, sz, sizeof(sz));
    int right = LIST_X + LIST_W - 24;
    int sw = ui_text_mid(right, y, ROW_H - 2, UI_FONT_SMALL,
                         selected ? UI_HEX_INK : UI_HEX_DIM, UI_RIGHT, 0, sz);
    int nx = LIST_X + 34;
    ui_text_mid(nx, y, ROW_H - 2, UI_FONT_BODY, fg, UI_LEFT,
                right - sw - 10 - nx, game->name);
}

static const char *config_label(int row)
{
    switch (row) {
    case 0: return "Server";
    case 1: return "API key";
    case 2: return "Console ID";
    case 3: return "Network";
    case 4: return "Game format";
    case 5: return "Install to";
    case 6: return "Static IP";
    case 7: return "Netmask";
    case 8: return "Gateway";
    case 9: return "DNS 1";
    case 10:return "DNS 2";
    case CFG_ROW_REFRESH_CATALOG: return "Refresh catalog";
    case CFG_ROW_CLEAR_HASHES:    return "Clear save hash cache";
    default:                      return "Reload config.txt";
    }
}

static int config_is_action(int row)
{
    return row >= CFG_ROW_REFRESH_CATALOG;
}

static const char *config_key(int row)
{
    switch (row) {
    case 0: return "server_url";
    case 1: return "api_key";
    case 2: return "console_id";
    case 3: return "network_mode";
    case 4: return "game_format";
    case 5: return "game_install_dir";
    case 6: return "static_ip";
    case 7: return "static_netmask";
    case 8: return "static_gateway";
    case 9: return "static_dns1";
    case 10:return "static_dns2";
    default:return "action";
    }
}

static const char *config_value(int row)
{
    switch (row) {
    case 0: return g_cfg.server_url;
    case 1: return g_cfg.api_key[0] ? "set (hidden)" : "not set";
    case 2: return g_cfg.console_id;
    case 3: return g_cfg.network_mode;
    case 4: return games_format_name(games_config_format(&g_cfg));
    case 5: return g_cfg.game_install_dir;
    case 6: return g_cfg.static_ip;
    case 7: return g_cfg.static_netmask;
    case 8: return g_cfg.static_gateway;
    case 9: return g_cfg.static_dns1;
    case 10:return g_cfg.static_dns2;
    default:return "";
    }
}

static const char *config_help(int row)
{
    switch (row) {
    case 0: return "Address of the GameSync server, e.g. http://192.168.1.201:8000.";
    case 1: return "Sent as X-API-Key with every request. Never shown on screen.";
    case 2: return "Identifies this Xbox to the server. Created on first run.";
    case 3: return "auto uses the dashboard network settings; dhcp or static force "
                   "one. A cycles, X saves, restart to apply.";
    case 4: return "cci keeps games as compressed CCI images; folder extracts the "
                   "game files. A cycles, X saves.";
    case 5: return "Where downloaded games are installed.";
    case CFG_ROW_REFRESH_CATALOG:
        return "A asks the server to rescan its ROM folder, drops the cached "
               "catalog and downloads the Xbox game list again.";
    case CFG_ROW_CLEAR_HASHES:
        return "A forgets the cached save hashes, so the next compare hashes "
               "every save again. Use it if a save shows the wrong status.";
    case CFG_ROW_RELOAD_CONFIG:
        return "A re-reads config.txt from the disk, dropping unsaved changes.";
    default:return "Used when the network mode is static.";
    }
}

static int config_cyclable(int row)
{
    return row == 3 || row == 4;
}

static void draw_config_row(int row_idx, int cursor, int y)
{
    int selected = (row_idx == cursor);
    uint32_t fg = row_bg(selected, y);
    if (config_is_action(row_idx)) {
        uint32_t ac = selected ? UI_HEX_INK : UI_HEX_ACCENT;
        ui_icon_arrow(LIST_X + 20, y + 12, 9, 1, ac);
        ui_text_mid(LIST_X + 32, y, ROW_H - 2, UI_FONT_BODY, ac, UI_LEFT,
                    LIST_W - 60, config_label(row_idx));
        return;
    }
    ui_text_mid(LIST_X + 16, y, ROW_H - 2, UI_FONT_SMALL,
                selected ? UI_HEX_INK : UI_HEX_DIM, UI_LEFT, 0,
                config_label(row_idx));

    const char *val = config_value(row_idx);
    int vx = LIST_X + 124;
    int vmax = LIST_X + LIST_W - 28 - vx;
    if (config_cyclable(row_idx)) {
        uint32_t ac = selected ? UI_HEX_INK : UI_HEX_ACCENT;
        ui_icon_arrow(vx + 3, y + 12, 9, 0, ac);
        int w = ui_text_mid(vx + 14, y, ROW_H - 2, UI_FONT_BODY, ac, UI_LEFT,
                            0, val);
        ui_icon_arrow(vx + 14 + w + 9, y + 12, 9, 1, ac);
        return;
    }
    if (!val || !val[0]) {
        ui_text_mid(vx, y, ROW_H - 2, UI_FONT_BODY,
                    selected ? UI_HEX_INK : UI_HEX_MUTED, UI_LEFT, vmax, "-");
        return;
    }
    ui_text_mid(vx, y, ROW_H - 2, UI_FONT_BODY, fg, UI_LEFT, vmax, val);
}

static void draw_row(int row_idx, int cursor, int y)
{
    if (g_tab == TAB_GAMES) {
        draw_game_row(row_idx, cursor, y);
    } else if (g_tab == TAB_INSTALLED) {
        draw_installed_row(row_idx, cursor, y);
    } else if (g_tab == TAB_CONFIG) {
        draw_config_row(row_idx, cursor, y);
    } else {
        draw_save_row(row_idx, cursor, y);
    }
}

static const char *list_title(void)
{
    switch (g_tab) {
    case TAB_GAMES:     return "Xbox games on the server";
    case TAB_INSTALLED: return "Games in F:\\Games";
    case TAB_CONFIG:    return "config.txt";
    default:            return "Saves on this Xbox";
    }
}

static const char *empty_message(void)
{
    if (g_tab == TAB_GAMES) {
        return g_roms_loaded ? "No Xbox games in the server catalog."
                             : "The catalog could not be loaded. Try "
                               "Settings > Refresh catalog.";
    }
    if (g_tab == TAB_INSTALLED) {
        return g_installed_loaded ? "No installed games in F:\\Games."
                                  : "Press X to scan installed games.";
    }
    return "No saves on this Xbox or the server.";
}

static void draw_list(int cursor, int scroll)
{
    ui_panel(LIST_X, PANEL_Y, LIST_W, PANEL_H);

    int total = total_rows();
    ui_text_mid(LIST_X + 14, PANEL_Y, LIST_HEAD_H, UI_FONT_SMALL, UI_HEX_DIM,
                UI_LEFT, LIST_W - 110, list_title());
    if (total > 0) {
        char range[32];
        int last = scroll + LIST_VISIBLE;
        if (last > total) last = total;
        snprintf(range, sizeof(range), "%d-%d of %d", scroll + 1, last, total);
        ui_text_mid(LIST_X + LIST_W - 14, PANEL_Y, LIST_HEAD_H, UI_FONT_TINY,
                    UI_HEX_MUTED, UI_RIGHT, 0, range);
    }
    ui_rect(LIST_X + 10, PANEL_Y + LIST_HEAD_H, LIST_W - 20, 1, UI_HEX_LINE);

    if (total == 0) {
        ui_text_wrap(LIST_X + 16, ROWS_Y + 12, UI_FONT_BODY, UI_HEX_DIM,
                     LIST_W - 32, 3, empty_message());
        return;
    }

    int end = scroll + LIST_VISIBLE;
    if (end > total) end = total;
    int y = ROWS_Y;
    for (int i = scroll; i < end; i++) {
        draw_row(i, cursor, y);
        y += ROW_H;
    }
    ui_scrollbar(LIST_X + LIST_W - 11, ROWS_Y, LIST_VISIBLE * ROW_H - 2,
                 scroll, LIST_VISIBLE, total);
}

// ---------------------------------------------------------------------------
// Detail panel
// ---------------------------------------------------------------------------

#define DX   (DETAIL_X + 14)
#define DW   (DETAIL_W - 28)
#define DETAIL_KEY_W 72

static int detail_caption(const char *caption, const char *title)
{
    ui_text(DX, PANEL_Y + 10, UI_FONT_TINY, UI_HEX_MUTED, UI_LEFT, caption);
    int n = ui_text_wrap(DX, PANEL_Y + 26, UI_FONT_BODY, UI_HEX_TEXT, DW, 2, title);
    return PANEL_Y + 26 + (n > 0 ? n : 1) * ui_line_h(UI_FONT_BODY) + 6;
}

static int detail_rule(int y)
{
    ui_rect(DX, y, DW, 1, UI_HEX_LINE);
    return y + 9;
}

static int detail_kv(int y, const char *key, const char *value, uint32_t hex)
{
    ui_text(DX, y, UI_FONT_TINY, UI_HEX_DIM, UI_LEFT, key);
    ui_text_fit(DX + DETAIL_KEY_W, y - 2, UI_FONT_SMALL, hex, UI_LEFT,
                DW - DETAIL_KEY_W,
                value && value[0] ? value : "-");
    return y + 20;
}

static void detail_hint(const char *text)
{
    int lh = ui_line_h(UI_FONT_TINY);
    ui_text_wrap(DX, PANEL_Y + PANEL_H - 10 - 3 * lh, UI_FONT_TINY,
                 UI_HEX_MUTED, DW, 3, text);
}

static void draw_save_detail(int cursor)
{
    if (cursor < 0 || cursor >= total_rows()) {
        detail_caption("SAVE", "Nothing selected");
        detail_hint("Saves live in E:\\UDATA. Start a game once so it creates one.");
        return;
    }
    const char *tid, *name;
    TitleStatus st;
    uint64_t size;
    int is_local;
    row_title(cursor, &tid, &name, &st, &size, &is_local);

    int y = detail_caption("SAVE", name);
    int x = DX;
    x += ui_pill(x, y, 20, UI_FONT_TINY, UI_HEX_XBOX, UI_HEX_INK, "XBOX") + 5;
    ui_pill(x, y, 20, UI_FONT_TINY, status_color(st), UI_HEX_INK, status_label(st));
    y = detail_rule(y + 28);

    y = detail_kv(y, "Title ID", tid, UI_HEX_TEXT);
    if (is_local) {
        const XboxSaveTitle *t = &g_list.titles[cursor];
        char buf[32];
        snprintf(buf, sizeof(buf), "%d", t->file_count);
        y = detail_kv(y, "Files", buf, UI_HEX_TEXT);
        y = detail_kv(y, "Size", fmt_kb(t->total_size, buf, sizeof(buf)), UI_HEX_TEXT);
        fmt_timestamp(t->latest_mtime, buf, sizeof(buf));
        detail_kv(y, "Saved", buf, UI_HEX_TEXT);
    } else {
        detail_kv(y, "Where", "server only", UI_HEX_INFO);
    }
    detail_hint(status_hint(st));
}

static void draw_game_detail(int cursor)
{
    if (!g_roms_loaded || cursor < 0 || cursor >= g_roms.count) {
        detail_caption("GAME", "Game catalog");
        detail_hint("Xbox games the server has in its ROM folder. Settings > "
                    "Refresh catalog reloads the list.");
        return;
    }
    const XboxRomEntry *r = &g_roms.roms[cursor];
    int y = detail_caption("GAME", r->name);
    char sz[24];
    int x = DX;
    x += ui_pill(x, y, 20, UI_FONT_TINY, UI_HEX_XBOX, UI_HEX_INK, "XBOX") + 5;
    x += ui_pill(x, y, 20, UI_FONT_TINY, r->is_bundle ? UI_HEX_ACCENT : UI_HEX_INFO,
                 UI_HEX_INK, r->is_bundle ? "CCI" : "ISO") + 5;
    ui_pill(x, y, 20, UI_FONT_TINY, UI_HEX_PANEL_HI, UI_HEX_DIM,
            fmt_size(r->size, sz, sizeof(sz)));
    y = detail_rule(y + 28);

    y = detail_kv(y, "File", r->filename, UI_HEX_TEXT);
    y = detail_kv(y, "Install as",
                  games_format_name(games_config_format(&g_cfg)), UI_HEX_TEXT);
    detail_kv(y, "Into", g_cfg.game_install_dir, UI_HEX_TEXT);

    char hint[160];
    snprintf(hint, sizeof(hint), "A downloads it and installs it to %s.",
             g_cfg.game_install_dir);
    detail_hint(hint);
}

static void draw_installed_detail(int cursor)
{
    int y;
    if (!g_installed_loaded || cursor < 0 || cursor >= g_installed.count) {
        y = detail_caption("INSTALLED", "Installed games");
    } else {
        const XboxInstalledGame *g = &g_installed.games[cursor];
        y = detail_caption("INSTALLED", g->name);
        char sz[24], buf[32];
        int x = DX;
        x += ui_pill(x, y, 20, UI_FONT_TINY, UI_HEX_OK, UI_HEX_INK, "HDD") + 5;
        ui_pill(x, y, 20, UI_FONT_TINY, UI_HEX_PANEL_HI, UI_HEX_DIM,
                fmt_size(g->size, sz, sizeof(sz)));
        y = detail_rule(y + 28);
        snprintf(buf, sizeof(buf), "%u", (unsigned)g->file_count);
        y = detail_kv(y, "Files", buf, UI_HEX_TEXT);
        snprintf(buf, sizeof(buf), "%u", (unsigned)g->dir_count);
        y = detail_kv(y, "Folders", buf, UI_HEX_TEXT);
        ui_text(DX, y, UI_FONT_TINY, UI_HEX_DIM, UI_LEFT, "Path");
        ui_text_wrap(DX + DETAIL_KEY_W, y - 2, UI_FONT_SMALL, UI_HEX_TEXT,
                     DW - DETAIL_KEY_W, 2, g->path);
    }

    // F: drive usage near the bottom of the panel.
    int by = PANEL_Y + PANEL_H - 82;
    if (g_f_space_loaded && g_f_space.total_bytes > 0) {
        char free_sz[24], total_sz[24], line[64];
        uint64_t used = g_f_space.total_bytes - g_f_space.free_bytes;
        float frac = (float)((double)used / (double)g_f_space.total_bytes);
        ui_text(DX, by, UI_FONT_TINY, UI_HEX_MUTED, UI_LEFT, "F: DRIVE");
        ui_bar(DX, by + 18, DW, 8, frac, frac > 0.9f ? UI_HEX_WARN : UI_HEX_ACCENT);
        snprintf(line, sizeof(line), "%s free of %s",
                 fmt_size(g_f_space.free_bytes, free_sz, sizeof(free_sz)),
                 fmt_size(g_f_space.total_bytes, total_sz, sizeof(total_sz)));
        ui_text(DX, by + 30, UI_FONT_TINY, UI_HEX_DIM, UI_LEFT, line);
    }
    ui_text(DX, PANEL_Y + PANEL_H - 10 - ui_line_h(UI_FONT_TINY), UI_FONT_TINY,
            UI_HEX_MUTED, UI_LEFT,
            g_installed_loaded ? "A uninstalls, X rescans." : "X scans F:\\Games.");
}

static void draw_config_detail(int cursor)
{
    if (cursor < 0 || cursor >= CONFIG_ROWS) cursor = 0;
    int y = detail_caption("SETTING", config_label(cursor));
    ui_pill(DX, y, 20, UI_FONT_TINY, UI_HEX_PANEL_HI, UI_HEX_DIM, config_key(cursor));
    y = detail_rule(y + 28);
    ui_text_wrap(DX, y, UI_FONT_SMALL, UI_HEX_TEXT, DW, 5, config_help(cursor));
    detail_hint(config_is_action(cursor)
                    ? "Runs right away."
                    : "Edit text values in E:\\UDATA\\TDSV0000\\config.txt. "
                      "X saves changes.");
}

static void draw_detail(int cursor)
{
    ui_panel(DETAIL_X, PANEL_Y, DETAIL_W, PANEL_H);
    switch (g_tab) {
    case TAB_GAMES:     draw_game_detail(cursor); break;
    case TAB_INSTALLED: draw_installed_detail(cursor); break;
    case TAB_CONFIG:    draw_config_detail(cursor); break;
    default:            draw_save_detail(cursor); break;
    }
}

// ---------------------------------------------------------------------------
// Whole screen
// ---------------------------------------------------------------------------

static void draw_footer(int cursor)
{
    int config_action_row = (g_tab == TAB_CONFIG) && config_is_action(cursor);
    static const UiHint saves[] = {
        { "A", "Sync" }, { "X", "Sync all" }, { "Y", "Details" },
        { "LR", "Page" }, { "L/R", "Tabs" }, { "START", "Exit" },
    };
    static const UiHint games[] = {
        { "A", "Download" }, { "Y", "Details" },
        { "LR", "Page" }, { "L/R", "Tabs" }, { "START", "Exit" },
    };
    static const UiHint installed[] = {
        { "A", "Uninstall" }, { "X", "Rescan" }, { "Y", "Details" },
        { "LR", "Page" }, { "L/R", "Tabs" }, { "START", "Exit" },
    };
    static const UiHint config[] = {
        { "A", "Change" }, { "X", "Save" },
        { "LR", "Page" }, { "L/R", "Tabs" }, { "START", "Exit" },
    };
    static const UiHint config_action[] = {
        { "A", "Run" }, { "X", "Save" },
        { "LR", "Page" }, { "L/R", "Tabs" }, { "START", "Exit" },
    };
    switch (g_tab) {
    case TAB_GAMES:     ui_footer(games, 5); break;
    case TAB_INSTALLED: ui_footer(installed, 6); break;
    case TAB_CONFIG:
        ui_footer(config_action_row ? config_action : config, 5);
        break;
    default:            ui_footer(saves, 6); break;
    }
}

static void draw_screen(int cursor, int scroll)
{
    ui_clear(UI_HEX_BG);
    ui_header(tab_section(), g_local_ip, UI_HEX_OK);
    draw_tab_row();
    draw_list(cursor, scroll);
    draw_detail(cursor);
    ui_banner(UI_MARGIN_X, BANNER_Y, UI_W - 2 * UI_MARGIN_X, BANNER_H,
              status_tone(), g_status);
    draw_footer(cursor);
}

static void redraw(int cursor, int scroll)
{
    draw_screen(cursor, scroll);
    ui_present();
}

// Right-aligned button hints inside a card's bottom strip.
static void card_actions(int x, int y, int w, int h, const UiHint *hints, int n)
{
    int sy = y + h - 40;
    ui_rect(x, sy, w, 1, UI_HEX_LINE);
    int right = x + w - 16;
    int cy = sy + 20;
    for (int i = n - 1; i >= 0; i--) {
        int lw = ui_text_w(UI_FONT_SMALL, hints[i].label);
        int bw = ui_button_w(hints[i].button);
        ui_text_mid(right - lw, cy - 12, 24, UI_FONT_SMALL, UI_HEX_TEXT, UI_LEFT,
                    0, hints[i].label);
        ui_button(right - lw - 6 - bw, cy, hints[i].button);
        right -= lw + 6 + bw + 22;
    }
}

// Modal "please wait" card for blocking operations; shows g_status.
static void show_busy(int cursor, int scroll, const char *title)
{
    draw_screen(cursor, scroll);
    ui_dim();
    const int w = 420, h = 108;
    const int x = (UI_W - w) / 2, y = 172;
    ui_card(x, y, w, h, title, UI_HEX_INFO);
    ui_text_wrap(x + 20, y + UI_CARD_TITLE_H + 14, UI_FONT_SMALL, UI_HEX_TEXT,
                 w - 40, 2, g_status);
    ui_text(x + 20, y + h - 26, UI_FONT_TINY, UI_HEX_MUTED, UI_LEFT,
            "Please wait...");
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

#define COMPARE_KEY_W 64

// One side of the upload/download comparison.
static void draw_compare_box(int x, int y, int w, int h, const char *caption,
                             int present, const char *size_line,
                             const char *hash, const char *when,
                             const char *extra)
{
    ui_rrect(x, y, w, h, 6, UI_HEX_BG2);
    ui_text(x + 12, y + 9, UI_FONT_TINY, UI_HEX_MUTED, UI_LEFT, caption);
    if (!present) {
        ui_text_fit(x + 12, y + 30, UI_FONT_SMALL, UI_HEX_DIM, UI_LEFT, w - 24,
                    size_line);
        return;
    }
    ui_text_fit(x + 12, y + 28, UI_FONT_SMALL, UI_HEX_TEXT, UI_LEFT, w - 24,
                size_line);
    int ky = y + 52;
    ui_text(x + 12, ky, UI_FONT_TINY, UI_HEX_DIM, UI_LEFT, "Hash");
    ui_text_fit(x + 12 + COMPARE_KEY_W, ky, UI_FONT_TINY, UI_HEX_TEXT,
                UI_LEFT, w - 24 - COMPARE_KEY_W, hash);
    ky += 18;
    ui_text(x + 12, ky, UI_FONT_TINY, UI_HEX_DIM, UI_LEFT, "Saved");
    ui_text_fit(x + 12 + COMPARE_KEY_W, ky, UI_FONT_TINY, UI_HEX_TEXT,
                UI_LEFT, w - 24 - COMPARE_KEY_W, when);
    if (extra) {
        ky += 18;
        ui_text(x + 12, ky, UI_FONT_TINY, UI_HEX_DIM, UI_LEFT, "Uploaded");
        ui_text_fit(x + 12 + COMPARE_KEY_W, ky, UI_FONT_TINY, UI_HEX_TEXT,
                    UI_LEFT, w - 24 - COMPARE_KEY_W, extra);
    }
}

// Modal wait: A confirms (1), B cancels (0). Nothing else closes a dialog.
static int wait_confirm(void)
{
    while (1) {
        ui_pump();
        UiKey k = ui_poll_key();
        if (k == UI_KEY_A) return 1;
        if (k == UI_KEY_B) return 0;
        ui_sleep(20);
    }
}

// Local + server facts about one save, shared by the Details card and the
// upload / download confirmation.
typedef struct {
    char           tid[XBOX_TITLE_ID_LEN + 1];
    const char    *name;
    XboxSaveTitle *local;
    char           local_hash[XBOX_SAVE_HASH_HEX_LEN + 1];
    int            server_ok;      // metadata request succeeded
    NetworkSaveMeta server;
} SaveCompare;

// Hash the local save (cached) and fetch the server's metadata. Returns 0
// when the local side is known; server_ok says whether the server answered.
static int fetch_save_compare(int cursor, int scroll, const char *tid,
                              XboxSaveTitle *local, SaveCompare *c)
{
    memset(c, 0, sizeof(*c));
    snprintf(c->tid, sizeof(c->tid), "%s", tid);
    c->local = local;
    c->name = (local && local->name[0]) ? local->name : c->tid;

    if (local && !state_get_cached_save_hash(local, c->local_hash)) {
        uint8_t raw[32];
        set_status_kind(UI_STATUS_BUSY_KIND, "Computing local hash: %s", tid);
        show_busy(cursor, scroll, "Hashing save");
        if (bundle_compute_save_hash(local, raw, c->local_hash) != 0) {
            set_status_kind(UI_STATUS_ERROR_KIND,
                            "Could not hash local save: %s", tid);
            return -1;
        }
        state_set_cached_save_hash(local, c->local_hash);
    }

    set_status_kind(UI_STATUS_BUSY_KIND, "Fetching server metadata: %s", tid);
    show_busy(cursor, scroll, "Contacting server");
    if (network_get_save_meta(&g_cfg, tid, &c->server) != 0) {
        const char *ne = network_last_error();
        set_status_kind(UI_STATUS_ERROR_KIND, "%s",
                        (ne && ne[0]) ? ne : "Server metadata fetch failed");
        memset(&c->server, 0, sizeof(c->server));
        c->server_ok = 0;
    } else {
        c->server_ok = 1;
    }
    return 0;
}

// The THIS XBOX | SERVER boxes side by side.
static void draw_compare_pair(int x, int y, int w, int h, const SaveCompare *c)
{
    char local_hash_short[40], server_hash_short[40];
    char local_ts[24], server_ts[24], server_up[24];
    const NetworkSaveMeta *server = &c->server;
    int server_has = c->server_ok && server->exists;
    short_hash(c->local_hash, local_hash_short, sizeof(local_hash_short));
    short_hash(server_has ? server->save_hash : "",
               server_hash_short, sizeof(server_hash_short));
    fmt_timestamp(c->local ? c->local->latest_mtime : 0, local_ts, sizeof(local_ts));
    fmt_timestamp(server_has ? server->client_timestamp : 0,
                  server_ts, sizeof(server_ts));
    fmt_server_time(server_has ? server->server_timestamp : "",
                    server_up, sizeof(server_up));

    int bw = (w - 12) / 2;
    char kb[24], local_line[64], server_line[64];
    if (c->local) {
        snprintf(local_line, sizeof(local_line), "%s, %d file(s)",
                 fmt_kb(c->local->total_size, kb, sizeof(kb)),
                 c->local->file_count);
    } else {
        snprintf(local_line, sizeof(local_line), "Not on this Xbox");
    }
    if (!c->server_ok) {
        snprintf(server_line, sizeof(server_line), "Server unreachable");
    } else if (server->exists) {
        snprintf(server_line, sizeof(server_line), "%s, %d file(s)",
                 fmt_kb(server->save_size, kb, sizeof(kb)), server->file_count);
    } else {
        snprintf(server_line, sizeof(server_line), "No save on the server");
    }
    draw_compare_box(x, y, bw, h, "THIS XBOX", c->local != NULL, local_line,
                     local_hash_short, local_ts, NULL);
    draw_compare_box(x + bw + 12, y, bw, h, "SERVER", server_has, server_line,
                     server_hash_short, server_ts, server_up);
}

static void draw_confirm_dialog(int cursor, int scroll, int upload,
                                const SaveCompare *c)
{
    draw_screen(cursor, scroll);
    ui_dim();

    // Downloading over an existing local save is the destructive case.
    uint32_t tone = upload ? UI_HEX_WARN : (c->local ? UI_HEX_ERR : UI_HEX_INFO);
    const int w = 540, h = 300;
    const int x = (UI_W - w) / 2, y = 88;
    ui_card(x, y, w, h, upload ? "Upload to server?" : "Download to this Xbox?",
            tone);

    int cy = y + UI_CARD_TITLE_H + 12;
    ui_text_fit(x + 20, cy, UI_FONT_BODY, UI_HEX_TEXT, UI_LEFT, w - 40, c->name);
    char line[160];
    snprintf(line, sizeof(line), "Title ID %s", c->tid);
    ui_text(x + 20, cy + 24, UI_FONT_TINY, UI_HEX_DIM, UI_LEFT, line);

    int by = cy + 48, bh = 112;
    draw_compare_pair(x + 20, by, w - 40, bh, c);

    const char *warn = upload
        ? "Replaces the server copy. The old one stays in the server history."
        : (c->local ? "Overwrites the save on this Xbox."
                    : "Copies the server save onto this Xbox.");
    ui_text_fit(x + 20, by + bh + 10, UI_FONT_SMALL,
                (!upload && c->local) ? UI_HEX_ERR : UI_HEX_DIM, UI_LEFT, w - 40,
                warn);

    static const UiHint actions[] = { { "B", "Cancel" }, { "A", "Confirm" } };
    card_actions(x, y, w, h, actions, 2);
    ui_present();
}

static int confirm_transfer(int cursor, int scroll, int upload,
                            const SaveCompare *c)
{
    set_status_kind(UI_STATUS_INFO_KIND, "Confirm %s: %s",
                    upload ? "upload" : "download", c->tid);
    draw_confirm_dialog(cursor, scroll, upload, c);
    if (wait_confirm()) return 1;
    set_status_kind(UI_STATUS_INFO_KIND, "%s cancelled: %s",
                    upload ? "Upload" : "Download", c->tid);
    return 0;
}

// ---------------------------------------------------------------------------
// Save details card (Y): the comparison plus the per-save actions that used
// to live on their own buttons.
// ---------------------------------------------------------------------------

typedef enum {
    SAVE_ACT_NONE = -1,
    SAVE_ACT_UPLOAD = 0,
    SAVE_ACT_DOWNLOAD,
    SAVE_ACT_COMPARE,
} SaveAct;

static void draw_save_details(int cursor, int scroll, const SaveCompare *c,
                              TitleStatus st, const SaveAct *acts, int n_acts,
                              int sel)
{
    draw_screen(cursor, scroll);
    ui_dim();
    const int w = 540, h = 356;
    const int x = (UI_W - w) / 2, y = (UI_H - h) / 2;
    ui_card(x, y, w, h, "Save details", status_color(st));

    int cy = y + UI_CARD_TITLE_H + 10;
    ui_text_fit(x + 20, cy, UI_FONT_BODY, UI_HEX_TEXT, UI_LEFT, w - 40, c->name);
    int px = x + 20;
    px += ui_pill(px, cy + 26, 18, UI_FONT_TINY, status_color(st), UI_HEX_INK,
                  status_label(st)) + 8;
    char line[64];
    snprintf(line, sizeof(line), "Title ID %s", c->tid);
    ui_text_mid(px, cy + 26, 18, UI_FONT_TINY, UI_HEX_DIM, UI_LEFT, 0, line);

    int by = cy + 52, bh = 112;
    draw_compare_pair(x + 20, by, w - 40, bh, c);

    int ry = by + bh + 10;
    for (int i = 0; i < n_acts; i++) {
        const char *label =
            acts[i] == SAVE_ACT_UPLOAD   ? "Upload this save to the server" :
            acts[i] == SAVE_ACT_DOWNLOAD ? "Download the server copy" :
                                           "Compare all saves again";
        int on = (i == sel);
        if (on) ui_rrect(x + 16, ry, w - 32, 24, 6, UI_HEX_ACCENT);
        uint32_t fg = on ? UI_HEX_INK : UI_HEX_TEXT;
        ui_icon_arrow(x + 30, ry + 12, 9, 1, on ? UI_HEX_INK : UI_HEX_ACCENT);
        ui_text_mid(x + 42, ry, 24, UI_FONT_SMALL, fg, UI_LEFT, w - 80, label);
        ry += 26;
    }

    static const UiHint hints[] = { { "B", "Close" }, { "A", "Select" } };
    card_actions(x, y, w, h, hints, 2);
    ui_present();
}

static SaveAct save_details_dialog(int cursor, int scroll, const SaveCompare *c,
                                   TitleStatus st)
{
    SaveAct acts[3];
    int n = 0;
    if (c->local) acts[n++] = SAVE_ACT_UPLOAD;
    if (c->server_ok && c->server.exists) acts[n++] = SAVE_ACT_DOWNLOAD;
    acts[n++] = SAVE_ACT_COMPARE;

    int sel = 0;
    draw_save_details(cursor, scroll, c, st, acts, n, sel);
    while (1) {
        ui_pump();
        UiKey k = ui_poll_key();
        int moved = 0;
        if (k == UI_KEY_UP && sel > 0) { sel--; moved = 1; }
        if (k == UI_KEY_DOWN && sel + 1 < n) { sel++; moved = 1; }
        if (k == UI_KEY_A) return acts[sel];
        if (k == UI_KEY_B) return SAVE_ACT_NONE;
        if (moved) draw_save_details(cursor, scroll, c, st, acts, n, sel);
        ui_sleep(20);
    }
}

// ---------------------------------------------------------------------------
// Generic info card (Y on Catalog / Installed)
// ---------------------------------------------------------------------------

typedef struct {
    const char *key;
    const char *value;
} InfoRow;

static void info_card(int cursor, int scroll, const char *title,
                      const char *name, const InfoRow *rows, int n)
{
    draw_screen(cursor, scroll);
    ui_dim();
    const int w = 540, h = 96 + 22 * n + 56;
    const int x = (UI_W - w) / 2, y = (UI_H - h) / 2;
    ui_card(x, y, w, h, title, UI_HEX_ACCENT);
    int cy = y + UI_CARD_TITLE_H + 12;
    int lines = ui_text_wrap(x + 20, cy, UI_FONT_BODY, UI_HEX_TEXT, w - 40, 2, name);
    cy += (lines > 0 ? lines : 1) * ui_line_h(UI_FONT_BODY) + 10;
    for (int i = 0; i < n; i++) {
        ui_text(x + 20, cy + 2, UI_FONT_TINY, UI_HEX_DIM, UI_LEFT, rows[i].key);
        ui_text_fit(x + 110, cy, UI_FONT_SMALL, UI_HEX_TEXT, UI_LEFT, w - 130,
                    rows[i].value && rows[i].value[0] ? rows[i].value : "-");
        cy += 22;
    }
    static const UiHint hints[] = { { "B", "Close" } };
    card_actions(x, y, w, h, hints, 1);
    ui_present();
    while (1) {
        ui_pump();
        UiKey k = ui_poll_key();
        if (k == UI_KEY_B || k == UI_KEY_A || k == UI_KEY_Y) return;
        ui_sleep(20);
    }
}

static void show_game_details(int cursor, int scroll)
{
    if (!g_roms_loaded || cursor < 0 || cursor >= g_roms.count) return;
    const XboxRomEntry *r = &g_roms.roms[cursor];
    char sz[24], into[XBOX_CFG_PATH_LEN + 16];
    snprintf(into, sizeof(into), "%s\\<game>", g_cfg.game_install_dir);
    InfoRow rows[] = {
        { "File", r->filename },
        { "Size", fmt_size(r->size, sz, sizeof(sz)) },
        { "Type", r->is_bundle ? "CCI bundle" : "ISO" },
        { "ROM ID", r->rom_id },
        { "Install as", games_format_name(games_config_format(&g_cfg)) },
        { "Into", into },
    };
    info_card(cursor, scroll, "Game details", r->name, rows, 6);
}

static void show_installed_details(int cursor, int scroll)
{
    if (!g_installed_loaded || cursor < 0 || cursor >= g_installed.count) return;
    const XboxInstalledGame *g = &g_installed.games[cursor];
    char sz[24], files[16], dirs[16];
    snprintf(files, sizeof(files), "%u", (unsigned)g->file_count);
    snprintf(dirs, sizeof(dirs), "%u", (unsigned)g->dir_count);
    InfoRow rows[] = {
        { "Size", fmt_size(g->size, sz, sizeof(sz)) },
        { "Files", files },
        { "Folders", dirs },
        { "Path", g->path },
    };
    info_card(cursor, scroll, "Installed game", g->name, rows, 4);
}

static int confirm_exit(int cursor, int scroll)
{
    draw_screen(cursor, scroll);
    ui_dim();
    const int w = 420, h = 150;
    const int x = (UI_W - w) / 2, y = (UI_H - h) / 2;
    ui_card(x, y, w, h, "Exit GameSync?", UI_HEX_WARN);
    ui_text_wrap(x + 20, y + UI_CARD_TITLE_H + 14, UI_FONT_SMALL, UI_HEX_TEXT,
                 w - 40, 2, "Returns to the dashboard.");
    static const UiHint hints[] = { { "B", "Cancel" }, { "A", "Exit" } };
    card_actions(x, y, w, h, hints, 2);
    ui_present();
    return wait_confirm();
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
    set_status_kind(UI_STATUS_SUCCESS_KIND,
                    "Compared: %d to upload, %d to download, %d new, %d conflict",
                    g_plan.upload_count,
                    g_plan.download_count,
                    g_plan.server_only_count,
                    g_plan.conflict_count);
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

// Rescan E:\UDATA (catches saves made since the last scan), then ask the
// server for a plan. Runs on its own at startup, after Sync all and after
// the hash cache is cleared - there is no separate Compare button.
static int g_compare_pending = 1;

static void compare_all(int *cursor, int *scroll)
{
    g_compare_pending = 0;
    *cursor = 0;
    *scroll = 0;
    set_status_kind(UI_STATUS_BUSY_KIND, "Rescanning E:\\UDATA...");
    show_busy(*cursor, *scroll, "Scanning saves");
    rescan();
    set_status_kind(UI_STATUS_BUSY_KIND, "Fetching sync plan...");
    show_busy(*cursor, *scroll, "Comparing with server");
    refresh_plan();
}

static void clear_hash_cache(void)
{
    if (state_clear_hash_cache() != 0) {
        set_status_kind(UI_STATUS_ERROR_KIND, "Could not clear hash cache");
        return;
    }
    if (g_plan_loaded) { sync_plan_free(&g_plan); g_plan_loaded = 0; }
    g_compare_pending = 1;
    set_status_kind(UI_STATUS_SUCCESS_KIND,
                    "Hash cache cleared; Saves compares again when you open it");
}

typedef struct {
    int cursor;
    int scroll;
    int force;
} CatalogBusyCtx;

static void catalog_progress_cb(int loaded, int total, void *user)
{
    CatalogBusyCtx *ctx = (CatalogBusyCtx *)user;
    if (total > 0) {
        set_status_kind(UI_STATUS_BUSY_KIND, "Loading Xbox ROM catalog... %d/%d",
                        loaded, total);
    } else {
        set_status_kind(UI_STATUS_BUSY_KIND, "Loading Xbox ROM catalog... %d",
                        loaded);
    }
    show_busy(ctx->cursor, ctx->scroll,
              ctx->force ? "Refreshing catalog" : "Loading catalog");
    ui_pump();
}

// Load the catalog through the on-disk cache. ``force`` is Settings >
// Refresh catalog (server rescan + cache wipe + full refetch).
static void load_rom_catalog(int force, int cursor, int scroll)
{
    char err[180] = "";
    CatalogBusyCtx ctx = { cursor, scroll, force };
    set_status_kind(UI_STATUS_BUSY_KIND, force
                        ? "Asking the server to rescan its ROMs..."
                        : "Loading Xbox ROM catalog...");
    show_busy(cursor, scroll, force ? "Refreshing catalog" : "Loading catalog");

    CatalogLoadInfo info;
    int rc = games_load_catalog(&g_cfg, &g_roms, force, catalog_progress_cb,
                                &ctx, &info, err, sizeof(err));
    char prefix[80] = "";
    if (force) {
        if (info.rescan > 0) {
            snprintf(prefix, sizeof(prefix), "Server rescanned (%d ROMs). ",
                     info.rescan_count);
        } else if (info.rescan == 0) {
            snprintf(prefix, sizeof(prefix), "Server did not allow a rescan. ");
        } else {
            snprintf(prefix, sizeof(prefix), "Server rescan failed. ");
        }
    }
    if (rc != 0) {
        // games_load_catalog leaves the list untouched on failure, so an
        // earlier copy stays on screen.
        set_status_kind(UI_STATUS_ERROR_KIND, "%s%s", prefix,
                        err[0] ? err : "ROM catalog failed");
        return;
    }
    g_roms_loaded = 1;
    g_catalog_source = info.source;
    switch (info.source) {
    case CATALOG_FROM_CACHE:
        set_status_kind(UI_STATUS_SUCCESS_KIND,
                        "%sCatalog: %d Xbox game(s), unchanged (cached)",
                        prefix, g_roms.count);
        break;
    case CATALOG_FROM_CACHE_OFFLINE:
        set_status_kind(UI_STATUS_ERROR_KIND,
                        "%sServer unreachable - cached catalog, %d game(s)",
                        prefix, g_roms.count);
        break;
    case CATALOG_UNCACHED:
        set_status_kind(UI_STATUS_SUCCESS_KIND,
                        "%sCatalog: %d Xbox game(s) (server has no cache support)",
                        prefix, g_roms.count);
        break;
    default:
        set_status_kind(UI_STATUS_SUCCESS_KIND, "%sCatalog: %d Xbox game(s)",
                        prefix, g_roms.count);
        break;
    }
}

static void load_installed_games(void)
{
    char err[180] = "";
    set_status_kind(UI_STATUS_BUSY_KIND, "Scanning F:\\Games...");

    if (games_scan_installed(&g_cfg, &g_installed, err, sizeof(err)) != 0) {
        g_installed_loaded = 0;
        if (games_get_f_drive_space(&g_f_space, err, sizeof(err)) == 0) {
            g_f_space_loaded = 1;
        }
        set_status_kind(UI_STATUS_ERROR_KIND, "%s",
                        err[0] ? err : "Installed game scan failed");
        return;
    }

    g_installed_loaded = 1;
    if (games_get_f_drive_space(&g_f_space, err, sizeof(err)) == 0) {
        g_f_space_loaded = 1;
    } else {
        g_f_space_loaded = 0;
    }

    char used[24];
    set_status_kind(UI_STATUS_SUCCESS_KIND,
                    "Installed: %d game(s), %s used",
                    g_installed.count,
                    fmt_size(g_installed.total_size, used, sizeof(used)));
}

// ---------------------------------------------------------------------------
// Game download progress
// ---------------------------------------------------------------------------

// Progress callbacks fire every MB and every few files; repaint at most
// this often so drawing never competes with the transfer.
#define PROGRESS_REDRAW_MS 250

typedef struct {
    int cursor;
    int scroll;
    const char *name;
    uint32_t start_ms;
    uint32_t last_draw_ms;
    int drawn;
} GameRedrawCtx;

static void draw_download_card(const GameRedrawCtx *ctx, const char *msg,
                               uint64_t done, uint64_t total)
{
    draw_screen(ctx->cursor, ctx->scroll);
    ui_dim();
    const int w = 520, h = 214;
    const int x = (UI_W - w) / 2, y = 120;
    ui_card(x, y, w, h, "Downloading game", UI_HEX_ACCENT);

    int cx = x + 20, cw = w - 40;
    int cy = y + UI_CARD_TITLE_H + 12;
    ui_text_fit(cx, cy, UI_FONT_BODY, UI_HEX_TEXT, UI_LEFT, cw, ctx->name);
    ui_text_fit(cx, cy + 24, UI_FONT_SMALL, UI_HEX_DIM, UI_LEFT, cw, msg);

    float frac = total > 0 ? (float)((double)done / (double)total) : 0.0f;
    ui_bar(cx, cy + 52, cw, 14, frac, UI_HEX_ACCENT);

    char a[24], b[24], line[96];
    if (total > 0) {
        snprintf(line, sizeof(line), "%s / %s",
                 fmt_size(done, a, sizeof(a)), fmt_size(total, b, sizeof(b)));
        ui_text(cx, cy + 74, UI_FONT_SMALL, UI_HEX_TEXT, UI_LEFT, line);
        snprintf(line, sizeof(line), "%d%%", (int)(frac * 100.0f));
        ui_text(cx + cw, cy + 74, UI_FONT_SMALL, UI_HEX_ACCENT2, UI_RIGHT, line);
    } else {
        ui_text(cx, cy + 74, UI_FONT_SMALL, UI_HEX_TEXT, UI_LEFT,
                fmt_size(done, a, sizeof(a)));
    }

    uint32_t elapsed_ms = ui_ms() - ctx->start_ms;
    uint32_t secs = elapsed_ms / 1000u;
    char el[16], eta[16];
    fmt_duration(secs, el, sizeof(el));
    if (elapsed_ms > 500 && done > 0) {
        uint64_t bps = done * 1000ULL / elapsed_ms;
        if (total > done && bps > 0) {
            fmt_duration((uint32_t)((total - done) / bps), eta, sizeof(eta));
        } else {
            snprintf(eta, sizeof(eta), "--:--");
        }
        snprintf(line, sizeof(line), "Speed %s/s    Elapsed %s    Remaining %s",
                 fmt_size(bps, a, sizeof(a)), el, eta);
    } else {
        snprintf(line, sizeof(line), "Elapsed %s", el);
    }
    ui_text(cx, cy + 98, UI_FONT_SMALL, UI_HEX_DIM, UI_LEFT, line);

    ui_rect(x, y + h - 36, w, 1, UI_HEX_LINE);
    ui_text_mid(cx, y + h - 36, 36, UI_FONT_TINY, UI_HEX_MUTED, UI_LEFT, 0,
                "Keep the console on until the install finishes.");
    int lw = ui_text_w(UI_FONT_SMALL, "Stop");
    ui_text_mid(x + w - 16 - lw, y + h - 36, 36, UI_FONT_SMALL, UI_HEX_TEXT,
                UI_LEFT, 0, "Stop");
    ui_button(x + w - 16 - lw - 6 - ui_button_w("B"), y + h - 18, "B");
    ui_present();
}

// B on the progress card asks before throwing a multi-GB transfer away.
static int confirm_stop_download(const GameRedrawCtx *ctx)
{
    draw_screen(ctx->cursor, ctx->scroll);
    ui_dim();
    const int w = 440, h = 160;
    const int x = (UI_W - w) / 2, y = (UI_H - h) / 2;
    ui_card(x, y, w, h, "Stop the download?", UI_HEX_WARN);
    ui_text_wrap(x + 20, y + UI_CARD_TITLE_H + 14, UI_FONT_SMALL, UI_HEX_TEXT,
                 w - 40, 3,
                 "The partly installed game is removed (an earlier install "
                 "of it is kept).");
    static const UiHint hints[] = { { "B", "Keep going" }, { "A", "Stop" } };
    card_actions(x, y, w, h, hints, 2);
    ui_present();
    return wait_confirm();
}

static int game_progress_cb(const char *msg, uint64_t done, uint64_t total,
                            void *user)
{
    GameRedrawCtx *ctx = (GameRedrawCtx *)user;
    if (total > 0) {
        set_status_kind(UI_STATUS_BUSY_KIND, "%s %llu/%llu MB",
                        msg,
                        (unsigned long long)(done / (1024 * 1024)),
                        (unsigned long long)(total / (1024 * 1024)));
    } else {
        set_status_kind(UI_STATUS_BUSY_KIND, "%s", msg);
    }

    // Input first, so B is noticed even between repaints.
    ui_pump();
    if (ui_poll_key() == UI_KEY_B) {
        if (confirm_stop_download(ctx)) return 1;
        ctx->drawn = 0;   // repaint the progress card right away
    }

    uint32_t now = ui_ms();
    int final = (total > 0 && done >= total);
    if (ctx->drawn && !final &&
        (uint32_t)(now - ctx->last_draw_ms) < PROGRESS_REDRAW_MS) {
        return 0;
    }
    ctx->drawn = 1;
    ctx->last_draw_ms = now;
    draw_download_card(ctx, msg, done, total);
    return 0;
}

static int confirm_game_download(int cursor, int scroll,
                                 const XboxRomEntry *rom,
                                 XboxGameFormat fmt)
{
    draw_screen(cursor, scroll);
    ui_dim();
    const int w = 500, h = 214;
    const int x = (UI_W - w) / 2, y = 128;
    ui_card(x, y, w, h, "Download game?", UI_HEX_ACCENT);

    int cx = x + 20, cw = w - 40;
    int cy = y + UI_CARD_TITLE_H + 12;
    int n = ui_text_wrap(cx, cy, UI_FONT_BODY, UI_HEX_TEXT, cw, 2, rom->name);
    cy += n * ui_line_h(UI_FONT_BODY) + 6;

    char sz[24], line[200];
    int px = cx;
    px += ui_pill(px, cy, 20, UI_FONT_TINY, UI_HEX_XBOX, UI_HEX_INK, "XBOX") + 5;
    px += ui_pill(px, cy, 20, UI_FONT_TINY, UI_HEX_ACCENT, UI_HEX_INK,
                  games_format_name(fmt)) + 5;
    ui_pill(px, cy, 20, UI_FONT_TINY, UI_HEX_PANEL_HI, UI_HEX_DIM,
            fmt_size(rom->size, sz, sizeof(sz)));
    cy += 30;
    snprintf(line, sizeof(line), "Installs to %s\\<game>", g_cfg.game_install_dir);
    ui_text_fit(cx, cy, UI_FONT_SMALL, UI_HEX_DIM, UI_LEFT, cw, line);
    ui_text_fit(cx, cy + 20, UI_FONT_SMALL, UI_HEX_DIM, UI_LEFT, cw,
                "Overwrites matching files in the game folder.");

    static const UiHint actions[] = { { "B", "Cancel" }, { "A", "Download" } };
    card_actions(x, y, w, h, actions, 2);
    ui_present();

    if (wait_confirm()) return 1;
    set_status_kind(UI_STATUS_INFO_KIND, "Game download cancelled");
    return 0;
}

static void run_game_download(int cursor, int scroll)
{
    if (!g_roms_loaded || g_roms.count <= 0) {
        set_status_kind(UI_STATUS_ERROR_KIND,
                        "No catalog loaded (Settings > Refresh catalog)");
        return;
    }
    if (cursor < 0 || cursor >= g_roms.count) return;

    XboxRomEntry *rom = &g_roms.roms[cursor];
    XboxGameFormat fmt = games_config_format(&g_cfg);
    if (!confirm_game_download(cursor, scroll, rom, fmt)) return;

    char err[180] = "";
    GameRedrawCtx ctx = { cursor, scroll, rom->name, ui_ms(), 0, 0 };
    set_status_kind(UI_STATUS_BUSY_KIND, "Starting game download...");
    draw_download_card(&ctx, "Connecting...", 0, rom->size);
    int rc = games_download_rom(&g_cfg, rom, fmt, game_progress_cb, &ctx,
                                err, sizeof(err));
    if (rc == 0) {
        g_installed_loaded = 0;
        g_f_space_loaded =
            games_get_f_drive_space(&g_f_space, NULL, 0) == 0;
        set_status_kind(UI_STATUS_SUCCESS_KIND, "Game installed: %s", rom->name);
    } else if (rc == GAMES_DOWNLOAD_CANCELLED) {
        g_installed_loaded = 0;
        set_status_kind(UI_STATUS_INFO_KIND, "Download stopped: %s", rom->name);
    } else {
        set_status_kind(UI_STATUS_ERROR_KIND, "%s",
                        err[0] ? err : "Game download failed");
    }
}

static int confirm_game_uninstall(int cursor, int scroll,
                                  const XboxInstalledGame *game)
{
    draw_screen(cursor, scroll);
    ui_dim();
    const int w = 500, h = 226;
    const int x = (UI_W - w) / 2, y = 122;
    ui_card(x, y, w, h, "Uninstall game?", UI_HEX_ERR);

    int cx = x + 20, cw = w - 40;
    int cy = y + UI_CARD_TITLE_H + 12;
    ui_text_fit(cx, cy, UI_FONT_BODY, UI_HEX_TEXT, UI_LEFT, cw, game->name);

    char line[180], size_buf[24];
    snprintf(line, sizeof(line), "%s   %u file(s)",
             fmt_size(game->size, size_buf, sizeof(size_buf)),
             (unsigned)game->file_count);
    ui_text(cx, cy + 26, UI_FONT_SMALL, UI_HEX_DIM, UI_LEFT, line);
    ui_text(cx, cy + 50, UI_FONT_TINY, UI_HEX_MUTED, UI_LEFT, "FOLDER");
    ui_text_wrap(cx + 56, cy + 48, UI_FONT_SMALL, UI_HEX_TEXT, cw - 56, 2,
                 game->path);
    ui_text_fit(cx, cy + 96, UI_FONT_SMALL, UI_HEX_ERR, UI_LEFT, cw,
                "Permanently deletes the installed game folder.");

    static const UiHint actions[] = { { "B", "Cancel" }, { "A", "Delete" } };
    card_actions(x, y, w, h, actions, 2);
    ui_present();

    if (wait_confirm()) return 1;
    set_status_kind(UI_STATUS_INFO_KIND, "Uninstall cancelled");
    return 0;
}

static void run_game_uninstall(int cursor, int scroll)
{
    if (!g_installed_loaded || g_installed.count <= 0) {
        set_status_kind(UI_STATUS_ERROR_KIND, "Scan installed games first (X)");
        return;
    }
    if (cursor < 0 || cursor >= g_installed.count) return;

    XboxInstalledGame game = g_installed.games[cursor];
    if (!confirm_game_uninstall(cursor, scroll, &game)) return;

    char err[180] = "";
    set_status_kind(UI_STATUS_BUSY_KIND, "Uninstalling %s...", game.name);
    show_busy(cursor, scroll, "Uninstalling");

    if (games_uninstall_installed(&g_cfg, &game, err, sizeof(err)) != 0) {
        set_status_kind(UI_STATUS_ERROR_KIND, "%s",
                        err[0] ? err : "Uninstall failed");
        return;
    }

    if (games_scan_installed(&g_cfg, &g_installed, err, sizeof(err)) == 0) {
        g_installed_loaded = 1;
    } else {
        g_installed_loaded = 0;
    }
    if (games_get_f_drive_space(&g_f_space, err, sizeof(err)) == 0) {
        g_f_space_loaded = 1;
    } else {
        g_f_space_loaded = 0;
    }

    set_status_kind(UI_STATUS_SUCCESS_KIND, "Uninstalled: %s", game.name);
}

static void config_cycle_selected(int row)
{
    if (row == 3) {
        if (strcmp(g_cfg.network_mode, "auto") == 0) {
            snprintf(g_cfg.network_mode, sizeof(g_cfg.network_mode), "dhcp");
        } else if (strcmp(g_cfg.network_mode, "dhcp") == 0) {
            snprintf(g_cfg.network_mode, sizeof(g_cfg.network_mode), "static");
        } else {
            snprintf(g_cfg.network_mode, sizeof(g_cfg.network_mode), "auto");
        }
        set_status_kind(UI_STATUS_INFO_KIND,
                        "network_mode=%s (X saves, restart to apply)",
                        g_cfg.network_mode);
    } else if (row == 4) {
        if (games_config_format(&g_cfg) == XBOX_GAME_FORMAT_FOLDER) {
            snprintf(g_cfg.game_format, sizeof(g_cfg.game_format), "cci");
        } else {
            snprintf(g_cfg.game_format, sizeof(g_cfg.game_format), "folder");
        }
        set_status_kind(UI_STATUS_INFO_KIND, "game_format=%s (X saves)",
                        g_cfg.game_format);
    } else {
        set_status_kind(UI_STATUS_INFO_KIND,
                        "Edit text fields in E:\\UDATA\\TDSV0000\\config.txt");
    }
}

static void config_reload(void)
{
    char err[180] = "";
    if (config_load(&g_cfg, err, sizeof(err)) == 0) {
        set_status_kind(UI_STATUS_SUCCESS_KIND, "Config reloaded");
    } else {
        set_status_kind(UI_STATUS_ERROR_KIND, "%s",
                        err[0] ? err : "Config reload failed");
    }
}

static void config_save_now(void)
{
    if (config_save(&g_cfg) == 0) {
        set_status_kind(UI_STATUS_SUCCESS_KIND, "Config saved");
    } else {
        set_status_kind(UI_STATUS_ERROR_KIND, "Config save failed");
    }
}

// ---------------------------------------------------------------------------
// Save transfers
// ---------------------------------------------------------------------------

typedef enum {
    XFER_SMART = 0,
    XFER_UPLOAD,
    XFER_DOWNLOAD,
} XferKind;

static const char *xfer_name(XferKind kind)
{
    return kind == XFER_SMART ? "Smart sync" :
           kind == XFER_UPLOAD ? "Upload" : "Download";
}

// Run one transfer for ``tid`` and update the plan / status line.
static void run_transfer(int cursor, int scroll, XferKind kind,
                         const char *tid_in, XboxSaveTitle *local)
{
    char tid[XBOX_TITLE_ID_LEN + 1];
    snprintf(tid, sizeof(tid), "%s", tid_in);
    TitleStatus prior_status = g_plan_loaded
                                   ? sync_plan_status(&g_plan, tid)
                                   : TITLE_STATUS_UNKNOWN;
    int rc;
    switch (kind) {
    case XFER_SMART:
        set_status_kind(UI_STATUS_BUSY_KIND, "Smart sync in progress: %s", tid);
        show_busy(cursor, scroll, "Syncing");
        rc = sync_one_smart(&g_cfg, &g_list, tid, &g_plan);
        break;
    case XFER_UPLOAD:
        set_status_kind(UI_STATUS_BUSY_KIND, "Uploading %s...", tid);
        show_busy(cursor, scroll, "Uploading");
        rc = sync_one_upload_force(&g_cfg, local);
        break;
    default:
        set_status_kind(UI_STATUS_BUSY_KIND, "Downloading %s...", tid);
        show_busy(cursor, scroll, "Downloading");
        rc = sync_one_download(&g_cfg, &g_list, tid);
        break;
    }
    if (rc == 0) {
        if (kind == XFER_DOWNLOAD ||
            prior_status == TITLE_STATUS_NEEDS_DOWNLOAD ||
            prior_status == TITLE_STATUS_SERVER_ONLY) {
            rescan_local_preserve_plan();
        }
        plan_mark_title_ok(tid);
        set_status_kind(UI_STATUS_SUCCESS_KIND, "%s complete: %s",
                        xfer_name(kind), tid);
    } else {
        const char *ne = network_last_error();
        if (ne && ne[0]) {
            set_status_kind(UI_STATUS_ERROR_KIND, "%s", ne);
        } else {
            set_status_kind(UI_STATUS_ERROR_KIND, "%s failed: %s",
                            xfer_name(kind), tid);
        }
    }
}

// Y on a save (and A on a conflict): details card with upload / download /
// compare-again choices; upload and download still get the confirmation.
static void run_save_details(int *cursor, int *scroll)
{
    const char *tid = NULL;
    XboxSaveTitle *local = NULL;
    if (row_to_title(*cursor, &tid, &local) != 0) {
        set_status_kind(UI_STATUS_ERROR_KIND, "No row selected");
        return;
    }
    SaveCompare c;
    if (fetch_save_compare(*cursor, *scroll, tid, local, &c) != 0) return;
    if (!local) {
        // Server-only rows have their names in the plan.
        int idx = *cursor - g_list.title_count;
        if (g_plan_loaded && idx >= 0 && idx < g_plan.server_only_count &&
            g_plan.server_only_names[idx][0]) {
            c.name = g_plan.server_only_names[idx];
        }
    }
    TitleStatus st = g_plan_loaded ? sync_plan_status(&g_plan, c.tid)
                                   : TITLE_STATUS_UNKNOWN;
    if (!local && g_plan_loaded) st = TITLE_STATUS_SERVER_ONLY;
    if (c.server_ok) {
        set_status_kind(UI_STATUS_INFO_KIND, "Details: %s", c.tid);
    }

    SaveAct act = save_details_dialog(*cursor, *scroll, &c, st);
    switch (act) {
    case SAVE_ACT_UPLOAD:
        if (confirm_transfer(*cursor, *scroll, 1, &c)) {
            run_transfer(*cursor, *scroll, XFER_UPLOAD, c.tid, local);
        }
        break;
    case SAVE_ACT_DOWNLOAD:
        if (confirm_transfer(*cursor, *scroll, 0, &c)) {
            run_transfer(*cursor, *scroll, XFER_DOWNLOAD, c.tid, local);
        }
        break;
    case SAVE_ACT_COMPARE:
        compare_all(cursor, scroll);
        break;
    default:
        set_status_kind(UI_STATUS_INFO_KIND, "Ready.");
        break;
    }
}

// A on a save row: the smart sync the plan suggests.
static void run_save_primary(int *cursor, int *scroll)
{
    if (!g_plan_loaded) {
        compare_all(cursor, scroll);
        return;
    }
    const char *tid = NULL;
    XboxSaveTitle *local = NULL;
    if (row_to_title(*cursor, &tid, &local) != 0) {
        set_status_kind(UI_STATUS_ERROR_KIND, "No row selected");
        return;
    }
    if (sync_plan_status(&g_plan, tid) == TITLE_STATUS_CONFLICT) {
        run_save_details(cursor, scroll);
        return;
    }
    run_transfer(*cursor, *scroll, XFER_SMART, tid, local);
}

// Cursor + scroll forwarded via the user pointer so the progress callback
// can repaint a coherent screen between titles.
typedef struct { int cursor; int scroll; } RedrawCtx;

static void draw_sync_all_card(int cursor, int scroll, const char *msg,
                               int done, int total)
{
    draw_screen(cursor, scroll);
    ui_dim();
    const int w = 500, h = 170;
    const int x = (UI_W - w) / 2, y = 150;
    ui_card(x, y, w, h, "Syncing all saves", UI_HEX_ACCENT);

    int cx = x + 20, cw = w - 40;
    int cy = y + UI_CARD_TITLE_H + 14;
    ui_text_fit(cx, cy, UI_FONT_SMALL, UI_HEX_TEXT, UI_LEFT, cw, msg);
    float frac = total > 0 ? (float)done / (float)total : 0.0f;
    ui_bar(cx, cy + 30, cw, 14, frac, UI_HEX_ACCENT);
    char line[48];
    snprintf(line, sizeof(line), "%d of %d title(s)", done, total);
    ui_text(cx, cy + 52, UI_FONT_SMALL, UI_HEX_DIM, UI_LEFT, line);
    snprintf(line, sizeof(line), "%d%%", (int)(frac * 100.0f));
    ui_text(cx + cw, cy + 52, UI_FONT_SMALL, UI_HEX_ACCENT2, UI_RIGHT, line);
    ui_text(cx, y + h - 28, UI_FONT_TINY, UI_HEX_MUTED, UI_LEFT,
            "Conflicts are skipped; open one with Y to pick a side.");
    ui_present();
}

static void sync_progress_cb(const char *msg, int done, int total,
                             void *user)
{
    RedrawCtx *rc = (RedrawCtx *)user;
    set_status_kind(UI_STATUS_BUSY_KIND, "%s", msg);
    draw_sync_all_card(rc->cursor, rc->scroll, msg, done, total);
    // Keep SDL events drained so the controller stays responsive and
    // the OS doesn't think we're locked.
    ui_pump();
}

static void run_sync_all(int *cursor, int *scroll)
{
    if (!g_plan_loaded) {
        compare_all(cursor, scroll);
        if (!g_plan_loaded) return;
    }
    set_status_kind(UI_STATUS_BUSY_KIND, "Sync all: starting...");
    draw_sync_all_card(*cursor, *scroll, "Starting...", 0,
                       g_plan.upload_count + g_plan.download_count +
                       g_plan.server_only_count);

    RedrawCtx rc = { *cursor, *scroll };
    SyncSummary s;
    sync_run_all(&g_cfg, &g_list, &g_plan, sync_progress_cb, &rc, &s);
    sync_plan_free(&g_plan);
    g_plan_loaded = 0;

    // Compare again so the list shows the new state, then put the summary
    // back on the status line.
    int failures = s.upload_failed + s.download_failed;
    char summary[160];
    snprintf(summary, sizeof(summary),
             "Sync all done: up %d, down %d, skipped %d, conflicts %d, failed %d",
             s.uploaded, s.downloaded, s.up_to_date, s.conflicts, failures);
    compare_all(cursor, scroll);
    set_status_kind(failures ? UI_STATUS_ERROR_KIND : UI_STATUS_SUCCESS_KIND,
                    "%s", summary);
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

// Work a tab does when it is opened: compare saves, load the catalog
// (cache first), scan installed games.
static void enter_tab(int *cursor, int *scroll)
{
    if (g_tab == TAB_SAVES && g_compare_pending) {
        compare_all(cursor, scroll);
    } else if (g_tab == TAB_GAMES && !g_roms_loaded) {
        load_rom_catalog(0, *cursor, *scroll);
    } else if (g_tab == TAB_INSTALLED && !g_installed_loaded) {
        set_status_kind(UI_STATUS_BUSY_KIND, "Scanning F:\\Games...");
        show_busy(*cursor, *scroll, "Scanning");
        load_installed_games();
    }
    clamp_cursor_scroll(cursor, scroll);
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

    // Each tab remembers its own position.
    int tab_cursor[TAB_COUNT] = { 0 };
    int tab_scroll[TAB_COUNT] = { 0 };
    int cursor = 0;
    int scroll = 0;
    enter_tab(&cursor, &scroll);   // compares the saves once at startup
    redraw(cursor, scroll);

    while (1) {
        ui_pump();
        UiKey k = ui_poll_key();

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
        case UI_KEY_LT:
        case UI_KEY_RT: {
            tab_cursor[g_tab] = cursor;
            tab_scroll[g_tab] = scroll;
            int step = (k == UI_KEY_RT) ? 1 : TAB_COUNT - 1;
            g_tab = (UiTab)(((int)g_tab + step) % TAB_COUNT);
            cursor = tab_cursor[g_tab];
            scroll = tab_scroll[g_tab];
            clamp_cursor_scroll(&cursor, &scroll);
            redraw(cursor, scroll);   // show the new tab before any loading
            enter_tab(&cursor, &scroll);
            redraw_needed = 1; break;
        }
        case UI_KEY_START:
            if (confirm_exit(cursor, scroll)) {
                ui_shutdown();
                HalReturnToFirmware(HalQuickRebootRoutine);
            }
            redraw_needed = 1; break;
        case UI_KEY_A:
            if (g_tab == TAB_GAMES) {
                run_game_download(cursor, scroll);
            } else if (g_tab == TAB_INSTALLED) {
                run_game_uninstall(cursor, scroll);
            } else if (g_tab == TAB_CONFIG) {
                if (cursor == CFG_ROW_REFRESH_CATALOG) {
                    load_rom_catalog(1, cursor, scroll);
                    // The Catalog tab starts at the top of the new list.
                    tab_cursor[TAB_GAMES] = 0;
                    tab_scroll[TAB_GAMES] = 0;
                } else if (cursor == CFG_ROW_CLEAR_HASHES) {
                    clear_hash_cache();
                } else if (cursor == CFG_ROW_RELOAD_CONFIG) {
                    config_reload();
                } else {
                    config_cycle_selected(cursor);
                }
            } else {
                run_save_primary(&cursor, &scroll);
            }
            clamp_cursor_scroll(&cursor, &scroll);
            redraw_needed = 1; break;
        case UI_KEY_X:
            if (g_tab == TAB_SAVES) {
                run_sync_all(&cursor, &scroll);
            } else if (g_tab == TAB_INSTALLED) {
                set_status_kind(UI_STATUS_BUSY_KIND, "Scanning F:\\Games...");
                show_busy(cursor, scroll, "Scanning");
                load_installed_games();
            } else if (g_tab == TAB_CONFIG) {
                config_save_now();
            }
            clamp_cursor_scroll(&cursor, &scroll);
            redraw_needed = 1; break;
        case UI_KEY_Y:
            if (g_tab == TAB_SAVES) {
                run_save_details(&cursor, &scroll);
            } else if (g_tab == TAB_GAMES) {
                show_game_details(cursor, scroll);
            } else if (g_tab == TAB_INSTALLED) {
                show_installed_details(cursor, scroll);
            }
            clamp_cursor_scroll(&cursor, &scroll);
            redraw_needed = 1; break;
        // B has nothing to cancel on a main screen; BACK has no sub-tabs to
        // cycle (the catalog only lists Xbox games); WHITE / BLACK are free.
        default: break;
        }

        if (redraw_needed) redraw(cursor, scroll);
        ui_sleep(20);
    }
    return 0;
}
