#include "views.h"
#include "ui_log.h"
#include <stdio.h>
#include <string.h>
#include <strings.h>

#ifndef APP_VERSION
#define APP_VERSION "dev"
#endif

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

void view_format_time(unsigned seconds, char *out, size_t size) {
    if (seconds > 99 * 3600 + 3599) seconds = 99 * 3600 + 3599;
    if (seconds >= 3600)
        snprintf(out, size, "%u:%02u:%02u", seconds / 3600, (seconds / 60) % 60, seconds % 60);
    else
        snprintf(out, size, "%u:%02u", seconds / 60, seconds % 60);
}

static void title_id_hex(const Title *t, char out[17]) {
    snprintf(out, 17, "%02X%02X%02X%02X%02X%02X%02X%02X",
             t->title_id[0], t->title_id[1], t->title_id[2], t->title_id[3],
             t->title_id[4], t->title_id[5], t->title_id[6], t->title_id[7]);
}

static void hash_hex(const uint8_t *hash, int bytes, char *out) {
    for (int i = 0; i < bytes; i++) snprintf(out + i * 2, 3, "%02x", hash[i]);
}

// Sync status of a save as shown in the list and the details
static Color status_color(const Title *t) {
    if (!t->scanned) return C_TEXT_FAINT;
    switch (t->scan_result) {
        case SYNC_UP_TO_DATE: return C_OK;
        case SYNC_UPLOAD: return C_WARN;
        case SYNC_DOWNLOAD: return C_INFO;
        default: return C_ERR;
    }
}

static const char *status_label(const Title *t) {
    if (!t->scanned) return "NOT CHECKED";
    switch (t->scan_result) {
        case SYNC_UP_TO_DATE: return "UP TO DATE";
        case SYNC_UPLOAD: return "NEEDS UPLOAD";
        case SYNC_DOWNLOAD: return "NEEDS DOWNLOAD";
        default: return "CONFLICT";
    }
}

static int dpad_hint(Surface *s, int x, int y, const char *button, const char *text) {
    x += theme_button(s, x, y, button) + 4;
    return gfx_text(s, &font_regular, x, y - 1, C_TEXT_DIM, text);
}

// Round icon badge used by the dialogs
static void badge(Surface *s, int x, int y, const Icon *icon, Color c) {
    gfx_round_rect(s, x, y, 28, 28, 14, gfx_mix(C_CARD, c, 60), 256);
    gfx_round_rect(s, x + 3, y + 3, 22, 22, 11, c, 256);
    gfx_icon(s, icon, x + 14 - icon->w / 2, y + 14 - icon->h / 2, C_ON_ACCENT);
}

static const Hint hints_close[] = { { "A", "Close" }, { NULL, NULL } };

// ---------------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------------

const char *const view_tab_names[TAB_COUNT] = { "Saves", "Catalog", "Settings" };

void view_tab_header(Surface *s, int active) {
    theme_tabs(s, view_tab_names, TAB_COUNT, active);
}

// Footer of the top screen on every tab without screen-specific hints
static const Hint hints_tabs[] = { { "L/R", "Tabs" }, { "START", "Exit" }, { NULL, NULL } };

// ---------------------------------------------------------------------------
// Settings tab
// ---------------------------------------------------------------------------

static const struct {
    const char *label;
    const Icon *icon;
    const char *help;
} menu_items[MENU_ITEMS] = {
    { "Server URL", &icon_link, "Address of the GameSync server, e.g. http://192.168.1.100:8000." },
    { "API Key", &icon_key, "The API key of the server (SYNC_API_KEY)." },
    { "WiFi SSID", &icon_wifi,
      "DS / DS Lite: the network to join (WEP only). Leave blank to use the firmware WiFi settings." },
    { "WiFi WEP Key", &icon_key, "5, 13 or 16 characters. The DS WiFi chip only speaks WEP." },
    { "Rescan Saves", &icon_refresh, "Look for save files on the card again." },
    { "Connect WiFi", &icon_wifi, "Connect to WiFi again, e.g. after changing the settings above." },
    { "Check Updates", &icon_box, "Look for a newer GameSync through the server and install it." },
    { "Achievement Sets", &icon_trophy,
      "Save the RetroAchievements set of every ROM in the ROM folder for nds-bootstrap-ra. "
      "Hold B to stop." },
    { "Refresh Catalog", &icon_sd,
      "Ask the server to rescan its ROMs, clear the catalog cache on the SD and download the game "
      "list again." },
};

static void menu_value(const SyncState *st, int i, bool has_wifi, const SettingsInfo *info, char *out,
                       size_t size, Color *c) {
    *c = C_TEXT_DIM;
    out[0] = '\0';
    switch (i) {
        case SET_SERVER_URL:
            snprintf(out, size, "%.60s", st->server_url[0] ? st->server_url : "not set");
            break;
        case SET_API_KEY:
            if (strlen(st->api_key) > 4) snprintf(out, size, "%.4s****", st->api_key);
            else snprintf(out, size, "not set");
            break;
        case SET_WIFI_SSID:
            snprintf(out, size, "%s", st->wifi_ssid[0] ? st->wifi_ssid : "not set");
            break;
        case SET_WEP_KEY:
            if (st->wifi_wep_key[0]) snprintf(out, size, "%d chars", (int)strlen(st->wifi_wep_key));
            else snprintf(out, size, "not set");
            break;
        case SET_RESCAN:
            snprintf(out, size, "%d saves", st->num_titles);
            break;
        case SET_WIFI:
            snprintf(out, size, "%s", has_wifi ? "Connected" : "Offline");
            *c = has_wifi ? C_OK : C_ERR;
            break;
        case SET_UPDATES:
            snprintf(out, size, "v%s", APP_VERSION);
            break;
        case SET_RA:
            snprintf(out, size, "%s/_nds/ra", info && info->ra_root ? info->ra_root : "sd:");
            break;
        case SET_CATALOG:
            snprintf(out, size, "%s", info && info->catalog ? info->catalog : "");
            break;
    }
    if (!strcmp(out, "not set")) *c = C_TEXT_FAINT;
}

static void draw_menu(Surface *s, const SyncState *st, int sel, bool has_wifi, const SettingsInfo *info) {
    int y = CONTENT_Y + 3;
    for (int i = 0; i < MENU_ITEMS; i++) {
        if (i == SET_SERVER_URL || i == SET_RESCAN) {
            bool conn = (i == SET_SERVER_URL);
            gfx_text(s, &font_mono, 10, y + 2, C_TEXT_FAINT, conn ? "CONNECTION" : "TOOLS");
            gfx_hline(s, conn ? 64 : 44, y + 5, s->w - (conn ? 74 : 54), C_CARD_LINE);
            y += 11;
        }
        bool hot = (i == sel);
        theme_row(s, 5, y, s->w - 10, 13, hot, true);
        Color icon_c = hot ? C_ON_ACCENT : (i == SET_RA ? C_GOLD : C_ACCENT);
        gfx_icon(s, menu_items[i].icon, 11 + (11 - menu_items[i].icon->w) / 2,
                 y + 7 - menu_items[i].icon->h / 2, icon_c);
        int lx = gfx_text(s, hot ? &font_bold : &font_regular, 26, y, hot ? C_ON_ACCENT : C_TEXT,
                          menu_items[i].label);
        char val[64];
        Color vc;
        menu_value(st, i, has_wifi, info, val, sizeof(val), &vc);
        if (hot) vc = C_ON_ACCENT;
        int room = s->w - 14 - (lx + 10);
        int vw = gfx_text_width(&font_regular, val);
        if (vw > room) vw = room;
        gfx_text_fit(s, &font_regular, s->w - 12 - vw, y, room, vc, val);
        y += 13;
    }
}

// Server address and online/offline badge along the bottom of a top screen
static void server_strip(Surface *s, const SyncState *st, bool has_wifi) {
    int sy = CONTENT_Y + CONTENT_H - 23;
    gfx_round_rect(s, 6, sy, s->w - 12, 19, 5, C_INSET, 256);
    gfx_icon(s, &icon_link, 12, sy + 6, C_TEXT_DIM);
    const char *pill = has_wifi ? "ONLINE" : "OFFLINE";
    int pw = theme_pill_width(pill);
    gfx_text_fit(s, &font_regular, 26, sy + 3, s->w - 12 - pw - 32, C_TEXT_DIM,
                 st->server_url[0] ? st->server_url : "No server set");
    theme_pill(s, s->w - 12 - pw - 4, sy + 4, pill, has_wifi ? C_OK : C_ERR, C_ON_ACCENT);
}

void view_settings_top(Surface *s, const SyncState *st, int sel, bool has_wifi, const SettingsInfo *info) {
    theme_background(s);
    view_tab_header(s, TAB_SETTINGS);
    if (sel < 0 || sel >= MENU_ITEMS) sel = 0;

    int x = 6, y = 25, w = s->w - 12;
    int help_lines = gfx_text_wrap(NULL, &font_regular, 0, 0, w - 20, 13, 5, 0, menu_items[sel].help);
    bool cache_row = (sel == SET_CATALOG && info && info->cache_dir);
    int h = 22 + help_lines * 13 + 6 + 13 + (cache_row ? 13 : 0) + 6;
    theme_card(s, x, y, w, h, menu_items[sel].label, sel == SET_RA ? C_GOLD : C_ACCENT);
    int cy = y + 22;
    gfx_text_wrap(s, &font_regular, x + 10, cy, w - 20, 13, 5, C_TEXT, menu_items[sel].help);
    cy += help_lines * 13 + 3;
    gfx_hline(s, x + 10, cy, w - 20, C_CARD_LINE);
    cy += 3;
    char val[64];
    Color vc;
    menu_value(st, sel, has_wifi, info, val, sizeof(val), &vc);
    const char *key = sel <= SET_WEP_KEY ? "Value"
                      : sel == SET_RA    ? "Sets folder"
                      : sel == SET_CATALOG ? "Cache"
                                           : "Now";
    theme_kv(s, x + 10, cy, w - 20, key, val, vc == C_TEXT_DIM ? C_TEXT : vc);
    if (cache_row) {
        cy += 13;
        theme_kv(s, x + 10, cy, w - 20, "Folder", info->cache_dir, C_TEXT_DIM);
    }

    server_strip(s, st, has_wifi);
    theme_footer(s, hints_tabs);
}

void view_settings_list(Surface *s, const SyncState *st, int sel, bool has_wifi, const SettingsInfo *info) {
    theme_background(s);
    theme_toolbar(s, "Settings & tools", NULL);
    draw_menu(s, st, sel, has_wifi, info);
    static const Hint edit[] = { { "A", "Edit" }, { "UD", "Move" }, { NULL, NULL } };
    static const Hint run[] = { { "A", "Run" }, { "UD", "Move" }, { NULL, NULL } };
    theme_footer(s, sel <= SET_WEP_KEY ? edit : run);
}

// ---------------------------------------------------------------------------
// Saves tab: top
// ---------------------------------------------------------------------------

static void draw_details(Surface *s, const SyncState *st, int sel, bool has_wifi) {
    if (st->num_titles == 0) {
        theme_card(s, 8, 32, s->w - 16, 74, "No saves found", C_WARN);
        gfx_text_wrap(s, &font_regular, 18, 56, s->w - 36, 13, 4, C_TEXT_DIM,
                      "No save files were found on this card. The Catalog tab (R) can still "
                      "install games.");
    } else {
        const Title *t = &st->titles[sel];
        int x = 6, y = 25, w = s->w - 12;
        // Measure first so the card fits its content
        int name_lines = gfx_text_wrap(NULL, &font_bold, 0, 0, w - 20, 13, 2, 0, t->game_name);
        int h = 8 + name_lines * 13 + 4 + 13 + 13 + 6 + 3 * 13 + 6;
        theme_card(s, x, y, w, h, NULL, 0);
        int cy = y + 6;
        gfx_text_wrap(s, &font_bold, x + 10, cy, w - 20, 13, 2, C_TEXT, t->game_name);
        cy += name_lines * 13 + 4;

        Color sc = status_color(t);
        int px = x + 10;
        if (t->scanned) px += theme_pill(s, px, cy, status_label(t), sc, C_ON_ACCENT) + 4;
        else px += theme_pill_outline(s, px, cy, status_label(t), C_TEXT_FAINT) + 4;
        if (t->on_server) px += theme_pill_outline(s, px, cy, "ON SERVER", C_ACCENT) + 4;
        if (t->is_cartridge) theme_pill_outline(s, px, cy, "CARTRIDGE", C_INFO);
        cy += 13 + 1;

        const char *what;
        if (!t->scanned) what = has_wifi ? "Not checked yet: X scans all saves." : "Offline: connect WiFi to check.";
        else if (t->scan_result == SYNC_UP_TO_DATE) what = "Same as the server.";
        else if (t->scan_result == SYNC_UPLOAD) what = "Changed on this DS: A uploads it.";
        else if (t->scan_result == SYNC_DOWNLOAD) what = "Newer on the server: A downloads it.";
        else what = "Changed on both sides: A lets you pick.";
        gfx_text_fit(s, &font_regular, x + 10, cy, w - 20, C_TEXT_DIM, what);
        cy += 13 + 3;
        gfx_hline(s, x + 10, cy, w - 20, C_CARD_LINE);
        cy += 3;

        char buf[48], tid[17];
        cat_format_size(t->save_size, buf, sizeof(buf));
        theme_kv(s, x + 10, cy, w - 20, "Save size", buf, C_TEXT);
        cy += 13;
        title_id_hex(t, tid);
        theme_kv(s, x + 10, cy, w - 20, "Title ID", tid, C_TEXT);
        cy += 13;
        int kx = gfx_text(s, &font_regular, x + 10, cy, C_TEXT_DIM, "File");
        gfx_text_fit_left(s, &font_mono, kx + 10, cy + 3, x + w - 10 - (kx + 10), C_TEXT, t->save_path);
    }

    server_strip(s, st, has_wifi);
}

void view_saves_top(Surface *s, const SyncState *state, int selected, bool has_wifi) {
    theme_background(s);
    view_tab_header(s, TAB_SAVES);
    draw_details(s, state, selected, has_wifi);
    theme_footer(s, hints_tabs);
}

// ---------------------------------------------------------------------------
// Saves tab: save list
// ---------------------------------------------------------------------------

static void list_counts(Surface *s, const SyncState *st) {
    int n[4] = { 0 }, scanned = 0;
    for (int i = 0; i < st->num_titles; i++) {
        if (!st->titles[i].scanned) continue;
        scanned++;
        SyncAction a = st->titles[i].scan_result;
        n[a == SYNC_UP_TO_DATE ? 0 : a == SYNC_UPLOAD ? 1 : a == SYNC_DOWNLOAD ? 2 : 3]++;
    }
    char buf[16];
    if (!scanned) {
        snprintf(buf, sizeof(buf), "%d", st->num_titles);
        gfx_text_right(s, &font_regular, s->w - 8, 4, C_TEXT_DIM, buf);
        return;
    }
    static const Color colors[4] = { C_OK, C_WARN, C_INFO, C_ERR };
    int x = s->w - 8;
    for (int k = 3; k >= 0; k--) {
        if (!n[k]) continue;
        snprintf(buf, sizeof(buf), "%d", n[k]);
        x -= gfx_text_width(&font_regular, buf);
        gfx_text(s, &font_regular, x, 4, C_TEXT, buf);
        x -= 9;
        theme_status_dot(s, x + 3, 10, colors[k]);
        x -= 6;
    }
}

void view_save_list(Surface *s, const SyncState *st, int selected, int scroll, bool has_wifi) {
    theme_background(s);
    theme_toolbar(s, "Saves", NULL);
    list_counts(s, st);

    if (st->num_titles == 0) {
        int cy = CONTENT_Y + 36;
        gfx_round_rect(s, s->w / 2 - 16, cy, 32, 32, 16, C_CARD, 256);
        gfx_icon_scaled(s, &icon_sd, s->w / 2 - 8, cy + 7, 2, C_TEXT_FAINT);
        gfx_text_center(s, &font_bold, s->w / 2, cy + 40, C_TEXT, "No saves found");
        gfx_text_center(s, &font_regular, s->w / 2, cy + 55, C_TEXT_DIM, "Press R to browse the");
        gfx_text_center(s, &font_regular, s->w / 2, cy + 68, C_TEXT_DIM, "Game Catalog");
    }

    for (int r = 0; r < SAVE_ROWS; r++) {
        int i = scroll + r;
        if (i >= st->num_titles) break;
        const Title *t = &st->titles[i];
        int y = CONTENT_Y + 1 + r * ROW_H;
        bool hot = (i == selected);
        theme_row(s, 4, y, s->w - 14, ROW_H - 1, hot, true);
        Color dc = status_color(t);
        if (hot) {
            gfx_round_rect(s, 9, y + 3, 7, 7, 3, C_ON_ACCENT, 256);
            gfx_round_rect(s, 10, y + 4, 5, 5, 2, dc, 256);
        } else {
            theme_status_dot(s, 12, y + 6, dc);
        }
        int right = s->w - 14;
        if (t->on_server) {
            gfx_icon(s, &icon_cloud, right - 14, y + 4, hot ? C_ON_ACCENT : gfx_mix(C_BG, C_TEXT_FAINT, 200));
            right -= 16;
        }
        gfx_text_fit(s, hot ? &font_bold : &font_regular, 21, y, right - 4 - 21,
                     hot ? C_ON_ACCENT : (t->scanned && t->scan_result != SYNC_UP_TO_DATE ? dc : C_TEXT),
                     t->game_name);
    }
    theme_scrollbar(s, s->w - 7, CONTENT_Y + 2, SAVE_ROWS * ROW_H - 2, scroll, SAVE_ROWS, st->num_titles);

    if (has_wifi && st->num_titles > 0) {
        static const Hint h[] = { { "A", "Sync" }, { "X", "Scan all" }, { "Y", "Info" }, { "LR", "Page" }, { NULL, NULL } };
        theme_footer(s, h);
    } else if (st->num_titles > 0) {
        static const Hint h[] = { { "Y", "Info" }, { "LR", "Page" }, { NULL, NULL } };
        theme_footer(s, h);
        gfx_text_right(s, &font_regular, s->w - 8, s->h - FOOTER_H + 2, C_ERR, "Offline");
    } else {
        theme_footer(s, NULL);
    }
}

// ---------------------------------------------------------------------------
// Save details
// ---------------------------------------------------------------------------

void view_save_details(Surface *s, const Title *t) {
    theme_background(s);
    theme_toolbar(s, "Save details", NULL);
    int x = 6, y = 24, w = s->w - 12;
    int lines = gfx_text_wrap(NULL, &font_bold, 0, 0, w - 20, 13, 2, 0, t->game_name);
    theme_card(s, x, y, w, lines * 13 + 101, NULL, 0);
    int cy = y + 5;
    gfx_text_wrap(s, &font_bold, x + 10, cy, w - 20, 13, 2, C_TEXT, t->game_name);
    cy += lines * 13 + 3;

    char buf[48], tid[17];
    char kb[24];
    cat_format_size(t->save_size, kb, sizeof(kb));
    snprintf(buf, sizeof(buf), "%s (%lu bytes)", kb, (unsigned long)t->save_size);
    theme_kv(s, x + 10, cy, w - 20, "Size", buf, C_TEXT);
    cy += 13;
    title_id_hex(t, tid);
    theme_kv(s, x + 10, cy, w - 20, "Title ID", tid, C_TEXT);
    cy += 15;

    gfx_text(s, &font_mono, x + 10, cy, C_TEXT_FAINT, "SAVE FILE");
    cy += 10;
    int cols = (w - 20) / 5;
    int len = (int)strlen(t->save_path);
    for (int i = 0; i < 2 && i * cols < len; i++) {
        if (i == 1 && len > 2 * cols) {
            // Keep the end of the path, it holds the file name
            gfx_text_fit_left(s, &font_mono, x + 10, cy + i * 9, w - 20, C_TEXT, t->save_path + cols);
        } else {
            gfx_text_n(s, &font_mono, x + 10, cy + i * 9, C_TEXT, t->save_path + i * cols, cols);
        }
    }
    cy += 21;

    gfx_text(s, &font_mono, x + 10, cy, C_TEXT_FAINT, "SHA-256");
    cy += 10;
    if (t->hash_calculated) {
        char hex[65];
        hash_hex(t->hash, 32, hex);
        gfx_text_n(s, &font_mono, x + 10, cy, C_ACCENT_HI, hex, 32);
        gfx_text_n(s, &font_mono, x + 10, cy + 9, C_ACCENT_HI, hex + 32, 32);
    } else {
        gfx_text(s, &font_mono, x + 10, cy, C_TEXT_DIM, "not calculated");
    }
    static const Hint h[] = { { "B", "Close" }, { NULL, NULL } };
    theme_footer(s, h);
}

// ---------------------------------------------------------------------------
// Sync dialogs
// ---------------------------------------------------------------------------

static void compare_card(Surface *s, int x, int y, int w, const char *title, Color c, bool has,
                         uint32_t size, const char *hash) {
    theme_card(s, x, y, w, 70, title, c);
    if (!has) {
        gfx_text(s, &font_regular, x + 10, y + 30, C_TEXT_FAINT, "No save");
        return;
    }
    char buf[24];
    cat_format_size(size, buf, sizeof(buf));
    gfx_text(s, &font_bold, x + 10, y + 23, C_TEXT, buf);
    gfx_text(s, &font_mono, x + 10, y + 41, C_TEXT_FAINT, "SHA-256");
    if (hash[0]) {
        gfx_text(s, &font_mono, x + 10, y + 51, C_TEXT_DIM, hash);
        gfx_text(s, &font_mono, x + 10 + 16 * 5, y + 51, C_TEXT_FAINT, "..");
    } else {
        gfx_text(s, &font_mono, x + 10, y + 51, C_TEXT_FAINT, "not calculated");
    }
}

void view_sync_compare(Surface *s, const CompareView *v) {
    theme_background(s);
    theme_header(s, v->heading);
    gfx_text_fit(s, &font_bold, 8, CONTENT_Y + 5, s->w - 16, C_TEXT, v->game);

    Color lc = C_TEXT_FAINT, sc = C_TEXT_FAINT;
    switch (v->action) {
        case SYNC_UP_TO_DATE: lc = sc = C_OK; break;
        case SYNC_UPLOAD: lc = C_WARN; break;
        case SYNC_DOWNLOAD: sc = C_INFO; break;
        case SYNC_CONFLICT: lc = sc = C_ERR; break;
    }
    int cw = (s->w - 12 - 6) / 2;
    compare_card(s, 6, 45, cw, "This DS", lc, v->has_local, v->local_size, v->local_hash);
    compare_card(s, 6 + cw + 6, 45, cw, "Server", sc, v->has_server, v->server_size, v->server_hash);

    // Middle arrow showing the direction
    int mx = s->w / 2, my = 45 + 35;
    if (v->action == SYNC_UPLOAD || v->action == SYNC_DOWNLOAD) {
        gfx_round_rect(s, mx - 8, my - 8, 16, 16, 8, C_BG, 256);
        Color c = v->action == SYNC_UPLOAD ? C_WARN : C_INFO;
        gfx_round_rect(s, mx - 7, my - 7, 14, 14, 7, c, 256);
        // Right for upload (DS -> server), left for download
        gfx_icon(s, v->action == SYNC_UPLOAD ? &icon_right : &icon_left, mx - 4, my - 4, C_ON_ACCENT);
    }

    // Last sync strip
    int y = 124;
    gfx_round_rect(s, 6, y, s->w - 12, 32, 5, C_INSET, 256);
    gfx_text(s, &font_mono, 14, y + 5, C_TEXT_FAINT, "LAST SYNC FROM THIS DS");
    if (v->last_hash[0]) {
        int x = gfx_text(s, &font_mono, 14, y + 18, C_TEXT_DIM, v->last_hash);
        gfx_text(s, &font_mono, x, y + 18, C_TEXT_FAINT, "..");
    } else {
        gfx_text(s, &font_regular, 14, y + 15, C_TEXT_FAINT, "Never synced on this DS");
    }
    theme_footer(s, NULL);
}

void view_sync_action(Surface *s, const char *game, SyncAction action, bool has_last) {
    theme_background(s);
    theme_toolbar(s, "Smart Sync", NULL);

    const Icon *icon = &icon_check;
    Color c = C_OK;
    const char *heading = "Already in sync";
    const char *text = "The save on this DS matches the server.";
    static const Hint ok[] = { { "A", "OK" }, { NULL, NULL } };
    static const Hint up[] = { { "A", "Upload" }, { "B", "Cancel" }, { NULL, NULL } };
    static const Hint down[] = { { "A", "Download" }, { "X", "Upload instead" }, { "B", "Cancel" }, { NULL, NULL } };
    static const Hint conflict[] = { { "X", "Upload" }, { "Y", "Download" }, { "B", "Cancel" }, { NULL, NULL } };
    const Hint *hints = ok;

    switch (action) {
        case SYNC_UP_TO_DATE:
            break;
        case SYNC_UPLOAD:
            icon = &icon_up;
            c = C_WARN;
            heading = "Upload to server";
            text = has_last ? "This DS's save changed since the last sync. Send it to the server?"
                            : "Send this DS's save to the server?";
            hints = up;
            break;
        case SYNC_DOWNLOAD:
            icon = &icon_down;
            c = C_INFO;
            heading = "Download from server";
            text = has_last ? "The server has a newer save from another device. Copy it to this DS? "
                              "(X sends this DS's save to the server instead.)"
                            : "Replace this DS's save with the server's copy? "
                              "(X sends this DS's save to the server instead.)";
            hints = down;
            break;
        case SYNC_CONFLICT:
            icon = &icon_conflict;
            c = C_ERR;
            heading = "Conflict";
            text = "Both saves changed since the last sync. X keeps this DS's save (upload), "
                   "Y keeps the server's (download).";
            hints = conflict;
            break;
    }

    int x = 8, y = 32, w = s->w - 16, h = 120;
    theme_card(s, x, y, w, h, NULL, 0);
    badge(s, x + 10, y + 10, icon, c);
    gfx_text_fit(s, &font_bold, x + 46, y + 11, w - 56, c, heading);
    gfx_text_fit(s, &font_regular, x + 46, y + 25, w - 56, C_TEXT_DIM, game);
    gfx_hline(s, x + 10, y + 46, w - 20, C_CARD_LINE);
    gfx_text_wrap(s, &font_regular, x + 10, y + 52, w - 20, 13, 5, C_TEXT, text);
    theme_footer(s, hints);
}

void view_summary(Surface *s, const char *toolbar, const char *title, UiKind kind,
                  const SummaryRow *rows, int nrows, const char *note, const Hint *hints) {
    theme_background(s);
    theme_toolbar(s, toolbar, NULL);
    int x = 8, w = s->w - 16;
    int note_lines = note && note[0] ? gfx_text_wrap(NULL, &font_regular, 0, 0, w - 20, 12, 3, 0, note) : 0;
    int h = 24 + nrows * 14 + (note_lines ? note_lines * 12 + 8 : 0) + 4;
    int y = CONTENT_Y + (CONTENT_H - h) / 2;
    if (y < CONTENT_Y + 3) y = CONTENT_Y + 3;
    theme_card(s, x, y, w, h, title, theme_kind_color(kind));
    int cy = y + 23;
    for (int i = 0; i < nrows; i++) {
        theme_status_dot(s, x + 15, cy + 6, rows[i].color);
        gfx_text(s, &font_regular, x + 24, cy, C_TEXT, rows[i].label);
        gfx_text_right(s, &font_bold, x + w - 12, cy, rows[i].color, rows[i].value);
        cy += 14;
    }
    if (note_lines) {
        gfx_hline(s, x + 10, cy + 2, w - 20, C_CARD_LINE);
        gfx_text_wrap(s, &font_regular, x + 10, cy + 6, w - 20, 12, 3, C_TEXT_DIM, note);
    }
    theme_footer(s, hints);
}

// ---------------------------------------------------------------------------
// Task screen
// ---------------------------------------------------------------------------

void view_task_log(Surface *s) {
    Surface c = *s;
    gfx_clip(&c, TASK_LOG_X, TASK_LOG_Y, TASK_LOG_W, TASK_LOG_H);
    theme_background(&c);
    gfx_round_frame(&c, TASK_LOG_X, TASK_LOG_Y, TASK_LOG_W, TASK_LOG_H, 5, C_CARD_LINE, C_INSET);
    ui_log_set_columns((TASK_LOG_W - 12) / 5);
    ui_log_draw(&c, TASK_LOG_X + 6, TASK_LOG_Y + 5, TASK_LOG_W - 12, TASK_LOG_H - 9);
}

void view_task(Surface *s, const TaskView *v) {
    theme_background(s);
    theme_toolbar(s, v->title, NULL);
    Color kc = theme_kind_color(v->kind);
    int x = 6, y = 24, w = s->w - 12, h = 36;
    theme_card(s, x, y, w, h, NULL, 0);
    gfx_round_rect(s, x + 6, y + 6, 3, h - 12, 1, kc, 256);

    bool bar = v->total > 0 && !v->finished;
    int dw = v->detail && v->detail[0] && bar ? gfx_text_width(&font_regular, v->detail) : 0;
    gfx_text_fit(s, &font_bold, x + 15, y + 4, w - 25 - (dw ? dw + 8 : 0),
                 v->finished ? kc : C_TEXT, v->status ? v->status : "");
    if (bar) {
        if (dw) gfx_text(s, &font_regular, x + w - 10 - dw, y + 4, C_TEXT_DIM, v->detail);
        theme_progress(s, x + 15, y + 22, w - 25, 7, v->done, v->total, kc);
    } else if (v->detail && v->detail[0]) {
        gfx_text_fit(s, &font_regular, x + 15, y + 19, w - 25, C_TEXT_DIM, v->detail);
    }

    view_task_log(s);
    theme_footer(s, v->hints);
}

// ---------------------------------------------------------------------------
// Splash
// ---------------------------------------------------------------------------

void view_splash(Surface *s, const char *status) {
    theme_background(s);
    int cx = s->w / 2;
    // Soft glow behind the badge
    gfx_round_rect(s, cx - 34, 30, 68, 68, 30, C_ACCENT, 18);
    gfx_round_rect(s, cx - 28, 36, 56, 56, 26, C_ACCENT, 26);
    gfx_round_rect(s, cx - 22, 42, 44, 44, 11, C_ACCENT, 256);
    gfx_icon_scaled(s, &icon_logo, cx - 15, 49, 3, C_ON_ACCENT);

    const char *name = "GameSync";
    int tw = gfx_text_width(&font_bold, name) * 2;
    gfx_text_scaled(s, &font_bold, cx - tw / 2, 96, 2, C_TEXT, name);
    gfx_text_center(s, &font_regular, cx, 126, C_TEXT_DIM, "Save sync for Nintendo DS");
    char ver[24];
    snprintf(ver, sizeof(ver), "v%s", APP_VERSION);
    int pw = theme_pill_width(ver);
    theme_pill(s, cx - pw / 2, 143, ver, C_CARD_LINE, C_TEXT);
    if (status && status[0]) gfx_text_center(s, &font_regular, cx, 170, C_TEXT_FAINT, status);
}

// ---------------------------------------------------------------------------
// Editor
// ---------------------------------------------------------------------------

void view_editor(Surface *s, const char *hint, const char *text, int len, int cursor,
                 const char *charset, int charset_len) {
    theme_background(s);
    theme_toolbar(s, "Edit", NULL);
    int x = 6, y = 24, w = s->w - 12;
    // Field: monospace, wrapped every `cols` characters
    int hint_lines = gfx_text_wrap(NULL, &font_regular, 0, 0, w - 70, 13, 2, 0, hint);
    int cols = (w - 28) / 5;
    int rows = (len + 1 + cols - 1) / cols;
    if (rows < 1) rows = 1;
    if (rows > 5) rows = 5;
    int fh = rows * 11 + 8;
    theme_card(s, x, y, w, hint_lines * 13 + fh + 57, NULL, 0);

    gfx_text_wrap(s, &font_regular, x + 10, y + 5, w - 70, 13, 2, C_TEXT_DIM, hint);
    char count[16];
    snprintf(count, sizeof(count), "%d", len);
    gfx_text_right(s, &font_mono, x + w - 10, y + 8, C_TEXT_FAINT, count);

    int fy = y + 8 + hint_lines * 13;
    int first_row = 0;
    int cursor_row = cursor / cols;
    if (cursor_row >= rows) first_row = cursor_row - rows + 1;
    gfx_round_frame(s, x + 8, fy, w - 16, fh, 4, C_ACCENT, C_INSET);
    for (int r = 0; r < rows; r++) {
        int start = (first_row + r) * cols;
        for (int i = 0; i < cols && start + i <= len; i++) {
            int idx = start + i;
            int cx = x + 14 + i * 5, cy = fy + 4 + r * 11;
            if (idx == cursor) {
                gfx_fill(s, cx - 1, cy - 1, 6, 10, C_ACCENT);
                if (idx < len) {
                    char ch[2] = { text[idx], 0 };
                    gfx_text(s, &font_mono, cx, cy, C_ON_ACCENT, ch);
                }
            } else if (idx < len) {
                if (text[idx] == ' ') gfx_fill(s, cx + 2, cy + 4, 1, 1, C_TEXT_FAINT);
                char ch[2] = { text[idx], 0 };
                gfx_text(s, &font_mono, cx, cy, C_TEXT, ch);
            }
        }
    }

    // Character wheel: Up/Down step through the charset
    int wy = fy + fh + 8;
    int current = 0;
    char cur = cursor < len ? text[cursor] : 'a';
    for (int i = 0; i < charset_len; i++) {
        if (charset[i] == cur) {
            current = i;
            break;
        }
    }
    int cx = s->w / 2;
    for (int k = -6; k <= 6; k++) {
        int idx = ((current + k) % charset_len + charset_len) % charset_len;
        int px = cx + k * 14 - 6;
        char ch[2] = { charset[idx], 0 };
        if (k == 0) {
            gfx_round_rect(s, px - 2, wy - 2, 16, 15, 4, cursor < len ? C_ACCENT : C_CARD_LINE, 256);
            if (ch[0] == ' ') gfx_text(s, &font_mono, px + 1, wy + 3, C_ON_ACCENT, "SP");
            else gfx_text(s, &font_bold, px + 6 - gfx_text_width(&font_bold, ch) / 2, wy - 1,
                          cursor < len ? C_ON_ACCENT : C_TEXT, ch);
        } else {
            int d = k < 0 ? -k : k;
            Color c = d == 1 ? C_TEXT_DIM : C_TEXT_FAINT;
            if (d >= 6) c = gfx_mix(C_CARD, C_TEXT_FAINT, 120);
            if (ch[0] == ' ') gfx_text(s, &font_mono, px + 1, wy + 3, c, "SP");
            else gfx_text(s, &font_regular, px + 6 - gfx_text_width(&font_regular, ch) / 2, wy - 1, c, ch);
        }
    }
    gfx_icon(s, &icon_down, x + 12, wy + 3, C_TEXT_FAINT);
    gfx_icon(s, &icon_up, x + w - 19, wy + 3, C_TEXT_FAINT);

    int hy = wy + 20;
    int hx = dpad_hint(s, x + 12, hy, "LR", "Move cursor");
    dpad_hint(s, hx + 14, hy, "UD", cursor < len ? "Change letter" : "Add a letter");

    static const Hint h[] = { { "A", "Save" }, { "B", "Cancel" }, { "X", "Delete" }, { "Y", "Insert" }, { NULL, NULL } };
    theme_footer(s, h);
}

// ---------------------------------------------------------------------------
// Game catalog
// ---------------------------------------------------------------------------

static void ra_badge_text(const CatEntry *e, char *out, size_t size) {
    if (!cat_entry_has_ra(e)) out[0] = '\0';
    else if (e->ra_title_only) snprintf(out, size, "RA?");
    else if (e->ra_achievements > 999) snprintf(out, size, "RA 999");
    else snprintf(out, size, "RA %d", e->ra_achievements);
}

void view_catalog_details(Surface *s, const CatalogView *v) {
    theme_background(s);
    view_tab_header(s, TAB_CATALOG);
    int x = 6, y = 25, w = s->w - 12;
    const CatEntry *e = v->current;
    if (!e) {
        theme_card(s, x, y, w, 60, v->error[0] ? "Catalog unavailable" : "No game selected",
                   v->error[0] ? C_ERR : C_ACCENT);
        gfx_text_wrap(s, &font_regular, x + 10, y + 24, w - 20, 13, 2, C_TEXT_DIM,
                      v->error[0] ? v->error : "Pick a game in the list below.");
    } else {
        const char *name = e->name[0] ? e->name : e->filename;
        int lines = gfx_text_wrap(NULL, &font_bold, 0, 0, w - 20, 13, 3, 0, name);
        int h = 6 + lines * 13 + 4 + 13 + 4 + 15 + 4 + 42 + 4;
        theme_card(s, x, y, w, h, NULL, 0);
        int cy = y + 6;
        gfx_text_wrap(s, &font_bold, x + 10, cy, w - 20, 13, 3, C_TEXT, name);
        cy += lines * 13 + 4;

        char size[16];
        cat_format_size(e->size, size, sizeof(size));
        int px = x + 10;
        px += theme_pill(s, px, cy, size, C_CARD_LINE, C_TEXT) + 4;
        const char *ext = strrchr(e->filename, '.');
        if (ext && strcasecmp(ext, ".zip") == 0) px += theme_pill_outline(s, px, cy, "ZIP", C_TEXT_DIM) + 4;
        if (v->current_installed) theme_pill(s, px, cy, "ON SD", C_OK, C_ON_ACCENT);
        else theme_pill_outline(s, px, cy, "NOT ON SD", C_TEXT_FAINT);
        cy += 13 + 4;

        char ra[48];
        Color rc = C_GOLD;
        if (cat_entry_has_ra(e) && e->ra_title_only) snprintf(ra, sizeof(ra), "%d achievements? (name match)", e->ra_achievements);
        else if (cat_entry_has_ra(e)) snprintf(ra, sizeof(ra), "%d achievements", e->ra_achievements);
        else if (e->ra_game_id) { snprintf(ra, sizeof(ra), "On RA, no achievements"); rc = C_TEXT_DIM; }
        else { snprintf(ra, sizeof(ra), "No RetroAchievements"); rc = C_TEXT_FAINT; }
        gfx_icon(s, &icon_trophy, x + 10, cy + 3, rc);
        gfx_text_fit(s, &font_regular, x + 24, cy, w - 34, rc, ra);
        cy += 15 + 4;

        gfx_hline(s, x + 10, cy - 2, w - 20, C_CARD_LINE);
        gfx_text(s, &font_mono, x + 10, cy + 2, C_TEXT_FAINT, "SERVER FILE");
        gfx_text_fit(s, &font_mono, x + 10, cy + 12, w - 20, C_TEXT_DIM, e->filename);
        gfx_text(s, &font_mono, x + 10, cy + 22, C_TEXT_FAINT, "INSTALLS TO");
        gfx_text_fit_left(s, &font_mono, x + 10, cy + 32, w - 20, C_TEXT_DIM, v->rom_dir);
    }

    Hint h[5];
    int n = 0;
    h[n++] = (Hint){ "X", v->ra_only ? "All games" : "RA only" };
    h[n++] = (Hint){ "Y", "Search" };
    if (v->search[0]) h[n++] = (Hint){ "B", "Clear" };
    if (v->nsystems > 1) h[n++] = (Hint){ "SELECT", "System" };
    h[n] = (Hint){ NULL, NULL };
    theme_footer(s, h);
}

static void catalog_toolbar(Surface *s, const CatalogView *v) {
    theme_toolbar(s, "", NULL);
    int x = 14;
    if (v->systems && v->nsystems > 1) x += theme_segments(s, x, 5, v->systems, v->nsystems, v->sys) + 2;
    else x += theme_pill(s, x, 5, v->system ? v->system : "NDS", C_ACCENT, C_ON_ACCENT) + 4;
    if (v->offline) x += theme_pill(s, x, 5, "OFFLINE", C_WARN, C_ON_ACCENT) + 4;
    if (v->ra_only) x += theme_pill(s, x, 5, "RA ONLY", C_GOLD, C_ON_ACCENT) + 4;
    char pos[24] = "";
    if (v->total > 0) snprintf(pos, sizeof(pos), "%d/%d", v->selected + 1, v->total);
    int pw = gfx_text_width(&font_regular, pos);
    if (pos[0]) gfx_text(s, &font_regular, s->w - 7 - pw, 4, C_TEXT_DIM, pos);
    if (v->search[0]) {
        gfx_icon(s, &icon_search, x + 2, 6, C_ACCENT_HI);
        gfx_text_fit(s, &font_regular, x + 14, 4, s->w - 7 - pw - 8 - (x + 14), C_ACCENT_HI, v->search);
    }
}

void view_catalog_strip(Surface *s, const CatalogView *v) {
    int y = CONTENT_Y + CAT_ROWS * ROW_H + 1;
    Surface c = *s;
    gfx_clip(&c, 0, y, s->w, 15);
    theme_background(&c);
    gfx_hline(&c, 8, y, s->w - 16, C_CARD_LINE);
    if (v->loading) {
        gfx_text_center(&c, &font_regular, s->w / 2, y + 1, C_ACCENT_HI, v->searching ? "Searching..." : "Loading...");
    } else if (v->error[0]) {
        gfx_text_fit(&c, &font_regular, 8, y + 1, s->w - 16, C_ERR, v->error);
    } else if (v->filter_ignored) {
        gfx_text_fit(&c, &font_regular, 8, y + 1, s->w - 16, C_ERR, "Server can't filter RA: update it");
    } else if (v->notice && v->notice[0]) {
        gfx_text_fit(&c, &font_regular, 8, y + 1, s->w - 16, v->offline ? C_WARN : C_ACCENT_HI, v->notice);
    } else {
        int x = 8;
        gfx_icon(&c, &icon_check, x, y + 5, C_OK);
        x = gfx_text(&c, &font_regular, x + 11, y + 1, C_TEXT_FAINT, "on SD") + 10;
        x += theme_pill(&c, x, y + 3, "RA", C_GOLD, C_ON_ACCENT) + 4;
        gfx_text(&c, &font_regular, x, y + 1, C_TEXT_FAINT, "achievements");
    }
}

void view_catalog_list(Surface *s, const CatalogView *v) {
    theme_background(s);
    catalog_toolbar(s, v);

    if (v->error[0] && v->nrows == 0) {
        int cy = CONTENT_Y + 30;
        gfx_text_center(s, &font_bold, s->w / 2, cy, C_ERR, "Couldn't load the catalog");
        gfx_text_wrap(s, &font_regular, 16, cy + 18, s->w - 32, 13, 3, C_TEXT_DIM, v->error);
        static const Hint h[] = { { "A", "Try again" }, { "L/R", "Tabs" }, { NULL, NULL } };
        theme_footer(s, h);
        return;
    }
    if (v->total == 0 && !v->error[0]) {
        int cy = CONTENT_Y + 34;
        const char *a, *b;
        if (v->search[0]) { a = "No games match the search."; b = "Y: new search   B: clear"; }
        else if (v->ra_only) { a = "No games with achievements."; b = "X: show all games"; }
        else { a = "No games on the server."; b = ""; }
        gfx_icon_scaled(s, &icon_search, s->w / 2 - 9, cy, 2, C_TEXT_FAINT);
        gfx_text_center(s, &font_bold, s->w / 2, cy + 26, C_TEXT, a);
        gfx_text_center(s, &font_regular, s->w / 2, cy + 41, C_TEXT_DIM, b);
        static const Hint h[] = { { "L/R", "Tabs" }, { NULL, NULL } };
        theme_footer(s, h);
        return;
    }

    for (int r = 0; r < v->nrows; r++) {
        int index = v->scroll + r;
        int y = CONTENT_Y + 1 + r * ROW_H;
        const CatEntry *e = v->rows[r].entry;
        bool hot = (index == v->selected);
        theme_row(s, 4, y, s->w - 14, ROW_H - 1, hot, true);
        Color fg = hot ? C_ON_ACCENT : C_TEXT;
        if (!e) {
            gfx_text(s, &font_regular, 22, y, hot ? C_ON_ACCENT : C_TEXT_FAINT, "...");
            continue;
        }
        if (v->rows[r].installed) gfx_icon(s, &icon_check, 9, y + 4, hot ? C_ON_ACCENT : C_OK);
        int right = s->w - 14;
        char tag[12];
        ra_badge_text(e, tag, sizeof(tag));
        if (tag[0]) {
            int tw = theme_pill_width(tag);
            if (e->ra_title_only) theme_pill_outline(s, right - 3 - tw, y + 1, tag, hot ? C_ON_ACCENT : C_GOLD);
            else theme_pill(s, right - 3 - tw, y + 1, tag, hot ? C_ON_ACCENT : C_GOLD, hot ? C_GOLD : C_ON_ACCENT);
            right -= tw + 6;
        }
        gfx_text_fit(s, hot ? &font_bold : &font_regular, 21, y, right - 21,
                     v->rows[r].installed && !hot ? gfx_mix(C_TEXT, C_OK, 110) : fg,
                     e->name[0] ? e->name : e->filename);
    }
    if (v->total > 0)
        theme_scrollbar(s, s->w - 7, CONTENT_Y + 2, CAT_ROWS * ROW_H - 2, v->scroll, CAT_ROWS, v->total);
    view_catalog_strip(s, v);

    static const Hint h[] = { { "A", "Install" }, { "UD", "Move" }, { "LR", "Page" }, { "L/R", "Tabs" }, { NULL, NULL } };
    theme_footer(s, h);
}

void view_install_confirm(Surface *s, const CatEntry *e, const char *rom_dir, const char *target, bool exists) {
    theme_background(s);
    theme_toolbar(s, "Install", NULL);
    int x = 6, y = 24, w = s->w - 12;
    theme_card(s, x, y, w, 148, "Install this game?", C_ACCENT);
    int cy = y + 23;
    const char *name = e->name[0] ? e->name : e->filename;
    cy += gfx_text_wrap(s, &font_bold, x + 10, cy, w - 20, 13, 2, C_TEXT, name) * 13 + 3;
    char size[16];
    cat_format_size(e->size, size, sizeof(size));
    theme_kv(s, x + 10, cy, w - 20, "Download size", size, C_TEXT);
    cy += 15;
    gfx_text(s, &font_mono, x + 10, cy, C_TEXT_FAINT, "SAVED AS");
    char path[400];
    snprintf(path, sizeof(path), "%s/%s", rom_dir, target);
    int cols = (w - 20) / 5;
    int len = (int)strlen(path);
    gfx_text_n(s, &font_mono, x + 10, cy + 10, C_TEXT, path, cols);
    if (len > cols) gfx_text_fit_left(s, &font_mono, x + 10, cy + 19, w - 20, C_TEXT, path + cols);
    cy += len > cols ? 32 : 23;
    if (cat_entry_has_ra(e)) {
        gfx_icon(s, &icon_trophy, x + 10, cy + 3, C_GOLD);
        gfx_text(s, &font_regular, x + 24, cy, C_GOLD, "Achievement set is saved too");
        cy += 14;
    }
    if (exists) {
        gfx_icon(s, &icon_conflict, x + 10, cy + 2, C_ERR);
        gfx_text(s, &font_regular, x + 24, cy, C_ERR, "Already on the SD: replace it?");
    }
    static const Hint h[] = { { "A", "Install" }, { "B", "Cancel" }, { NULL, NULL } };
    theme_footer(s, h);
}

void view_install(Surface *s, const InstallView *v) {
    theme_background(s);
    theme_toolbar(s, v->finished ? "Install finished" : "Installing", NULL);
    int x = 6, y = 24, w = s->w - 12;
    theme_card(s, x, y, w, 82, NULL, 0);
    gfx_text_fit(s, &font_bold, x + 10, y + 5, w - 20, C_TEXT, v->name);

    char a[16], b[16], buf[64];
    int by = y + 22;
    if (!v->started) {
        gfx_text(s, &font_regular, x + 10, by - 1, C_TEXT_DIM, "Connecting...");
        theme_progress(s, x + 10, by + 14, w - 20, 9, 0, 0, C_ACCENT);
    } else {
        unsigned pct = v->total ? (unsigned)((uint64_t)v->done * 100 / v->total) : 0;
        cat_format_size(v->done, a, sizeof(a));
        if (v->total) {
            cat_format_size(v->total, b, sizeof(b));
            snprintf(buf, sizeof(buf), "%s of %s", a, b);
        } else {
            snprintf(buf, sizeof(buf), "%s", a);
        }
        gfx_text(s, &font_regular, x + 10, by - 1, C_TEXT_DIM, buf);
        if (v->total) {
            snprintf(buf, sizeof(buf), "%u%%", pct);
            gfx_text_right(s, &font_bold, x + w - 10, by - 1, v->finished ? theme_kind_color(v->kind) : C_ACCENT_HI, buf);
        }
        theme_progress(s, x + 10, by + 14, w - 20, 9, v->done, v->total ? v->total : 1,
                       v->finished ? theme_kind_color(v->kind) : C_ACCENT);
    }

    // Speed and time
    int ry = by + 28;
    snprintf(buf, sizeof(buf), "%lu KB/s", (unsigned long)((v->speed ? v->speed : v->avg) / 1024));
    gfx_text(s, &font_mono, x + 10, ry + 3, C_TEXT_FAINT, "SPEED");
    int ex = gfx_text(s, &font_regular, x + 44, ry, C_TEXT, v->started ? buf : "-");
    if (v->started) {
        snprintf(buf, sizeof(buf), "avg %lu KB/s", (unsigned long)(v->avg / 1024));
        gfx_text(s, &font_regular, ex + 8, ry, C_TEXT_DIM, buf);
    }
    ry += 13;
    view_format_time(v->elapsed, a, sizeof(a));
    gfx_text(s, &font_mono, x + 10, ry + 3, C_TEXT_FAINT, "TIME");
    ex = gfx_text(s, &font_regular, x + 44, ry, C_TEXT, a);
    if (v->left != ~0u && !v->finished) {
        view_format_time(v->left, b, sizeof(b));
        snprintf(buf, sizeof(buf), "%s left", b);
        gfx_text(s, &font_regular, ex + 8, ry, C_TEXT_DIM, buf);
    }

    if (v->finished) {
        int fy = y + 86;
        int n = 0;
        while (n < 3 && v->lines[n]) n++;
        int h = 22 + n * 12 + 3;
        theme_card(s, x, fy, w, h, v->result, theme_kind_color(v->kind));
        for (int i = 0; i < n; i++)
            gfx_text_fit(s, &font_regular, x + 10, fy + 21 + i * 12, w - 20, v->line_colors[i], v->lines[i]);
        theme_footer(s, hints_close);
    } else {
        static const Hint h[] = { { "B", "Hold to cancel" }, { NULL, NULL } };
        theme_footer(s, h);
    }
}
