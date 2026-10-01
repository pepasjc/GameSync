#include <nds.h>
#include <stdio.h>
#include <fat.h>
#include "common.h"
#include "config.h"
#include "saves.h"
#include "network.h"
#include "sync.h"
#include "ui.h"
#include "views.h"
#include "update.h"
#include "ra.h"
#include "catalog.h"

#define LIST_VISIBLE SAVE_ROWS  // Visible titles on screen

static SyncState state;
static int selected = 0;
static int scroll_offset = 0;
static int settings_selected = 0;
static int tab = TAB_SAVES;           // L/R cycle Saves | Catalog | Settings
static bool has_wifi = false;

static void update_scroll(void) {
    if (selected < scroll_offset)
        scroll_offset = selected;
    if (selected >= scroll_offset + LIST_VISIBLE)
        scroll_offset = selected - LIST_VISIBLE + 1;
}

static void splash(const char *status) {
    view_splash(&ui_top, status);
    ui_present(&ui_top);
}

static void scan_progress(int done, int total, const char *name) {
    char detail[24];
    snprintf(detail, sizeof(detail), "%d / %d", done + 1, total);
    ui_task_progress("Checking saves", detail, (uint32_t)done, (uint32_t)total);
}

static void check_updates(void) {
    ui_task_begin("Updates", "Checking for updates");

    UpdateInfo update_info;
    if (!update_check(&state, &update_info)) {
        ui_task_end(KIND_ERROR, "Update check failed", "", HINTS_ANY, 0);
        return;
    }

    char detail[48];
    if (!update_info.available) {
        snprintf(detail, sizeof(detail), "You have the latest version (%s)", APP_VERSION);
        ui_task_end(KIND_OK, "Up to date", detail, HINTS_ANY, 0);
        return;
    }

    char size[24];
    snprintf(size, sizeof(size), "%zu KB", update_info.file_size / 1024);
    SummaryRow rows[] = {
        { "Current", APP_VERSION, C_TEXT_DIM },
        { "Latest", update_info.latest_version, C_OK },
        { "Size", size, C_TEXT_DIM },
    };
    static const Hint hints[] = { { "A", "Download & install" }, { "B", "Cancel" }, { NULL, NULL } };
    view_summary(&ui_bottom, "Updates", "Update available!", KIND_OK, rows, 3, NULL, hints);
    ui_present(&ui_bottom);
    if (!(ui_wait(KEY_A | KEY_B) & KEY_A)) return;

    ui_task_begin("Updates", "Downloading update");
    if (!update_download(&state, update_info.download_url, NULL))
        ui_task_end(KIND_ERROR, "Download failed", "", HINTS_ANY, 0);
    else
        ui_task_end(KIND_OK, "Update ready!", "Restart to apply", HINTS_ANY, 0);
}

// ---------------------------------------------------------------------------
// Saves tab
// ---------------------------------------------------------------------------

static void smart_sync(void) {
    Title *title = &state.titles[selected];

    ui_task_begin("Smart Sync", "Analyzing sync");

    // Force fresh hash calculation
    title->hash_calculated = false;

    SyncDecision decision;
    if (sync_decide(&state, selected, &decision) != 0) {
        ui_task_end(KIND_ERROR, "Failed to check sync!", title->game_name, HINTS_BACK_B, KEY_B);
        return;
    }
    title->on_server = decision.server_hash[0] != '\0';

    // Show decision and get user confirmation (A suggested action, X/Y the
    // other direction where it makes sense, B cancel)
    SyncAction chosen = ui_confirm_smart_sync(title, &decision);

    if (chosen == SYNC_UPLOAD || chosen == SYNC_DOWNLOAD) {
        bool up = (chosen == SYNC_UPLOAD);
        ui_task_begin(up ? "Upload" : "Download", up ? "Uploading..." : "Downloading...");

        int result = sync_execute(&state, selected, chosen);
        if (result == 0) {
            // Clear red highlight after successful sync
            title->scanned = true;
            title->scan_result = SYNC_UP_TO_DATE;
            title->on_server = true;
            ui_task_end(KIND_OK, up ? "Upload successful!" : "Download successful!",
                        title->game_name, HINTS_BACK_B, KEY_B);
        } else {
            ui_task_end(KIND_ERROR, up ? "Upload failed!" : "Download failed!",
                        title->game_name, HINTS_BACK_B, KEY_B);
        }
    } else if (chosen == SYNC_UP_TO_DATE && decision.action == SYNC_UP_TO_DATE) {
        // Write state file if missing for up-to-date saves
        if (!decision.has_last_synced && title->hash_calculated) {
            sync_execute(&state, selected, SYNC_UP_TO_DATE);
        }
    }
}

static void scan_all(void) {
    ui_task_begin("Scan all", "Checking saves");

    SyncSummary summary;
    sync_scan_all(&state, &summary, scan_progress);

    char n[5][12];
    snprintf(n[0], sizeof(n[0]), "%d", summary.up_to_date);
    snprintf(n[1], sizeof(n[1]), "%d", summary.uploaded);
    snprintf(n[2], sizeof(n[2]), "%d", summary.downloaded);
    snprintf(n[3], sizeof(n[3]), "%d", summary.conflicts);
    snprintf(n[4], sizeof(n[4]), "%d", summary.failed);
    SummaryRow rows[] = {
        { "Up to date", n[0], C_OK },
        { "Need upload", n[1], C_WARN },
        { "Need download", n[2], C_INFO },
        { "Conflicts", n[3], C_ERR },
        { "Failed", n[4], summary.failed ? C_ERR : C_TEXT_FAINT },
    };
    static const Hint ok[] = { { "A", "OK" }, { NULL, NULL } };
    view_summary(&ui_bottom, "Scan all", "Scan complete", KIND_OK, rows, 5,
                 "Out-of-sync saves are marked in the list: amber = upload, "
                 "blue = download, red = conflict.", ok);
    ui_present(&ui_bottom);
    ui_wait(0);
}

static void saves_input(int down, int rep) {
    int n = state.num_titles;
    if (n > 0) {
        if (rep & KEY_DOWN) selected = (selected + 1) % n;
        if (rep & KEY_UP) selected = (selected - 1 + n) % n;
        if (rep & KEY_RIGHT) {
            selected += LIST_VISIBLE;
            if (selected >= n) selected = n - 1;
        }
        if (rep & KEY_LEFT) {
            selected -= LIST_VISIBLE;
            if (selected < 0) selected = 0;
        }
        update_scroll();
    }
    if (n == 0) return;

    if (down & KEY_A) {
        if (has_wifi) smart_sync();
        else ui_message("Smart Sync", "WiFi required", "Use Connect WiFi in Settings first.", KIND_ERROR,
                        HINTS_ANY, 0);
    } else if (down & KEY_X) {
        if (has_wifi) scan_all();
        else ui_message("Scan all", "WiFi required", "Use Connect WiFi in Settings first.", KIND_ERROR,
                        HINTS_ANY, 0);
    } else if (down & KEY_Y) {
        Title *title = &state.titles[selected];
        ui_task_begin("Save details", "Loading details");
        // Ensure hash is calculated
        if (saves_ensure_hash(title) == 0) {
            ui_show_save_details(title);
        } else {
            ui_task_end(KIND_ERROR, "Failed to calculate hash!", title->game_name, HINTS_ANY, 0);
        }
    }
}

// ---------------------------------------------------------------------------
// Settings tab
// ---------------------------------------------------------------------------

static void edit_setting(const char *hint, char *field, int size) {
    if (config_edit_field(hint, field, size)) config_save(&state);
}

static void settings_input(int down, int rep) {
    if (rep & KEY_DOWN) settings_selected = (settings_selected + 1) % MENU_ITEMS;
    if (rep & KEY_UP) settings_selected = (settings_selected - 1 + MENU_ITEMS) % MENU_ITEMS;
    // The whole list is one page
    if (rep & KEY_RIGHT) settings_selected = MENU_ITEMS - 1;
    if (rep & KEY_LEFT) settings_selected = 0;
    if (!(down & KEY_A)) return;

    switch (settings_selected) {
        case SET_SERVER_URL:
            edit_setting("Server URL (e.g. http://192.168.1.100:8000)", state.server_url, sizeof(state.server_url));
            break;
        case SET_API_KEY:
            edit_setting("API key", state.api_key, sizeof(state.api_key));
            break;
        case SET_WIFI_SSID:
            edit_setting("WiFi SSID", state.wifi_ssid, sizeof(state.wifi_ssid));
            break;
        case SET_WEP_KEY:
            edit_setting("WiFi WEP key (5, 13 or 16 characters)", state.wifi_wep_key, sizeof(state.wifi_wep_key));
            break;
        case SET_RESCAN:
            ui_task_begin("Rescan saves", "Scanning saves");
            saves_scan(&state);
            selected = 0;
            scroll_offset = 0;
            break;
        case SET_WIFI:
            ui_task_begin("Connect WiFi", "Connecting WiFi");
            has_wifi = (network_init(&state) == 0);
            theme_wifi = has_wifi;
            if (!has_wifi) ui_task_end(KIND_ERROR, "WiFi connection failed", "", HINTS_ANY, 0);
            break;
        case SET_UPDATES:
            if (!has_wifi) {
                ui_message("Updates", "WiFi required", "Use Connect WiFi first.", KIND_ERROR, HINTS_ANY, 0);
            } else {
                check_updates();
            }
            break;
        case SET_RA:
            ra_update_sets_ui(&state, has_wifi);
            break;
        case SET_CATALOG:
            catalog_refresh(&state, has_wifi);
            break;
    }
}

// ---------------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------------

static void draw_tab(void) {
    if (tab == TAB_CATALOG) {
        catalog_tab_draw();
        return;
    }
    if (tab == TAB_SETTINGS) {
        SettingsInfo info = { ra_sd_root(), catalog_status(), catalog_cache_dir() };
        view_settings_top(&ui_top, &state, settings_selected, has_wifi, &info);
        view_settings_list(&ui_bottom, &state, settings_selected, has_wifi, &info);
    } else {
        view_saves_top(&ui_top, &state, selected, has_wifi);
        view_save_list(&ui_bottom, &state, selected, scroll_offset, has_wifi);
    }
    ui_present(&ui_top);
    ui_present(&ui_bottom);
}

// L/R: previous/next tab, wrapping
static void switch_tab(int dir) {
    int next = (tab + dir + TAB_COUNT) % TAB_COUNT;
    if (next == TAB_CATALOG && !catalog_tab_enter(&state, has_wifi))
        next = (next + dir + TAB_COUNT) % TAB_COUNT;  // out of memory: skip it
    tab = next;
}

int main(int argc, char *argv[]) {
    // argv[0] is the executable path (provided by homebrew loader)
    const char *self_path = (argc > 0 && argv && argv[0]) ? argv[0] : NULL;

    ui_init();
    splash("Starting up...");

    // Initialize FAT first
    if (!fatInitDefault()) {
        ui_message("Starting up", "Storage not found",
                   "FAT init failed! Make sure the SD card / flashcard is inserted.",
                   KIND_ERROR, HINTS_EXIT, KEY_START);
        return 0;
    }

    // Initialize config
    memset(&state, 0, sizeof(SyncState));

    // Load config from same path as 3DS client
    char config_error[256];
    if (!config_load(&state, config_error, sizeof(config_error))) {
        ui_message("Config setup", "Setup needed", config_error, KIND_WARN, HINTS_EXIT, KEY_START);
        return 0;
    }

    // Initialize network (optional - continue if fails)
    splash("Connecting WiFi...");
    ui_task_begin("Starting up", "Connecting WiFi");
    has_wifi = (network_init(&state) == 0);
    theme_wifi = has_wifi;
    if (!has_wifi) {
        static const Hint hints[] = { { "A", "Continue" }, { NULL, NULL } };
        ui_task_end(KIND_WARN, "WiFi unavailable", "Upload/download disabled", hints, KEY_A);
        ui_task_begin("Starting up", "Checking for a pending update");
    }

    // Check for pending update before continuing
    if (update_apply_pending(self_path)) {
        ui_task_end(KIND_OK, "Update applied", "Restart GameSync to finish", HINTS_EXIT, KEY_START);
        return 0;
    }

    // Scan for saves
    splash("Scanning saves...");
    ui_task_status("Scanning saves", "");
    saves_scan(&state);

    char found[32];
    snprintf(found, sizeof(found), "Found %d saves!", state.num_titles);
    static const Hint cont[] = { { "A", "Continue" }, { NULL, NULL } };
    ui_task_end(KIND_OK, found, "", cont, KEY_A);

    // No saves is fine: the Catalog tab can still install games

    // D-pad repeat on every list: first repeat after 20 frames, then every 4
    keysSetRepeat(20, 4);
    draw_tab();

    while (pmMainLoop()) {
        swiWaitForVBlank();
        scanKeys();
        int down = keysDown();
        int rep = keysDownRepeat() & (KEY_UP | KEY_DOWN | KEY_LEFT | KEY_RIGHT);
        if (!down && !rep) continue;

        if (down & KEY_START) {
            if (ui_confirm_exit()) break;
        } else if (down & (KEY_L | KEY_R)) {
            switch_tab((down & KEY_R) ? 1 : -1);
        } else if (tab == TAB_SAVES) {
            saves_input(down, rep);
        } else if (tab == TAB_CATALOG) {
            catalog_tab_input(down, rep, has_wifi);
        } else {
            settings_input(down, rep);
        }
        draw_tab();
    }

    catalog_shutdown();

    // Disconnect WiFi before exit to allow other games to initialize it cleanly
    // This may help avoid the nds-bootstrap issue where games won't load after WiFi apps
    network_cleanup();

    return 0;
}
