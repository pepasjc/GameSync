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
static int config_selected = 0;
static bool focus_on_config = false;  // false = saves list, true = config menu

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
    bool has_wifi = (network_init(&state) == 0);
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

    // No saves is fine: the game catalog can still install games

    // Main loop
    bool redraw = true;

    while(pmMainLoop()) {
        swiWaitForVBlank();
        scanKeys();
        int pressed = keysDown();

        if (pressed & KEY_START)
            break;

        // L button - toggle focus
        if (pressed & KEY_L) {
            focus_on_config = !focus_on_config;
            redraw = true;
        }

        if (pressed & KEY_DOWN) {
            if (focus_on_config) {
                config_selected = (config_selected + 1) % MENU_ITEMS;
                redraw = true;
            } else if (state.num_titles > 0) {
                selected = (selected + 1) % state.num_titles;
                update_scroll();
                redraw = true;
            }
        }

        if (pressed & KEY_UP) {
            if (focus_on_config) {
                config_selected = (config_selected - 1 + MENU_ITEMS) % MENU_ITEMS;
                redraw = true;
            } else if (state.num_titles > 0) {
                selected = (selected - 1 + state.num_titles) % state.num_titles;
                update_scroll();
                redraw = true;
            }
        }

        // Page down with RIGHT (only for saves list)
        if (pressed & KEY_RIGHT && !focus_on_config && state.num_titles > 0) {
            selected += LIST_VISIBLE;
            if (selected >= state.num_titles) selected = state.num_titles - 1;
            update_scroll();
            redraw = true;
        }

        // Page up with LEFT (only for saves list)
        if (pressed & KEY_LEFT && !focus_on_config && state.num_titles > 0) {
            selected -= LIST_VISIBLE;
            if (selected < 0) selected = 0;
            update_scroll();
            redraw = true;
        }

        // A button - handle config actions or save operations
        if (pressed & KEY_A) {
            if (focus_on_config) {
                // Handle config menu actions
                if (config_selected == 0) {
                    // Edit Server URL
                    if (config_edit_field("Server URL (e.g. http://192.168.1.100:8000)", state.server_url, sizeof(state.server_url))) {
                        config_save(&state);
                    }
                    redraw = true;
                } else if (config_selected == 1) {
                    // Edit API Key
                    if (config_edit_field("API key", state.api_key, sizeof(state.api_key))) {
                        config_save(&state);
                    }
                    redraw = true;
                } else if (config_selected == 2) {
                    // Edit WiFi SSID
                    if (config_edit_field("WiFi SSID", state.wifi_ssid, sizeof(state.wifi_ssid))) {
                        config_save(&state);
                    }
                    redraw = true;
                } else if (config_selected == 3) {
                    // Edit WiFi WEP Key
                    if (config_edit_field("WiFi WEP key (5, 13 or 16 characters)", state.wifi_wep_key,
                                          sizeof(state.wifi_wep_key))) {
                        config_save(&state);
                    }
                    redraw = true;
                } else if (config_selected == 4) {
                    // Rescan Saves
                    ui_task_begin("Rescan saves", "Scanning saves");
                    saves_scan(&state);
                    selected = 0;
                    scroll_offset = 0;
                    redraw = true;
                } else if (config_selected == 5) {
                    // Connect WiFi
                    ui_task_begin("Connect WiFi", "Connecting WiFi");
                    has_wifi = (network_init(&state) == 0);
                    theme_wifi = has_wifi;
                    if (!has_wifi) {
                        ui_task_end(KIND_ERROR, "WiFi connection failed", "", HINTS_ANY, 0);
                    }
                    redraw = true;
                } else if (config_selected == 6) {
                    // Check for updates
                    if (!has_wifi) {
                        ui_message("Updates", "WiFi required", "Use Connect WiFi in the menu first.",
                                   KIND_ERROR, HINTS_ANY, 0);
                    } else {
                        check_updates();
                    }
                    redraw = true;
                } else if (config_selected == 7) {
                    // RetroAchievements (nds-bootstrap-ra)
                    ra_menu(&state, has_wifi);
                    redraw = true;
                } else if (config_selected == 8) {
                    catalog_screen(&state, has_wifi);
                    redraw = true;
                }
                continue;
            }
        }

        // SELECT - game catalog
        if (pressed & KEY_SELECT) {
            catalog_screen(&state, has_wifi);
            redraw = true;
            continue;
        }

        // Y button - show save details (only when focused on saves)
        if (pressed & KEY_Y && !focus_on_config && state.num_titles > 0) {
            Title *title = &state.titles[selected];

            ui_task_begin("Save details", "Loading details");

            // Ensure hash is calculated
            if (saves_ensure_hash(title) == 0) {
                ui_show_save_details(title);
            } else {
                ui_task_end(KIND_ERROR, "Failed to calculate hash!", title->game_name, HINTS_ANY, 0);
            }

            redraw = true;
        }

        // A button - smart sync (only when focused on saves)
        if (pressed & KEY_A && !focus_on_config && state.num_titles > 0 && has_wifi) {
            Title *title = &state.titles[selected];

            ui_task_begin("Smart Sync", "Analyzing sync");

            // Force fresh hash calculation
            title->hash_calculated = false;

            SyncDecision decision;
            if (sync_decide(&state, selected, &decision) != 0) {
                ui_task_end(KIND_ERROR, "Failed to check sync!", title->game_name, HINTS_BACK_B, KEY_B);
                redraw = true;
                continue;
            }
            title->on_server = decision.server_hash[0] != '\0';

            // Show decision and get user confirmation
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

            redraw = true;
        }

        // R button - manual upload (only when focused on saves)
        if (pressed & KEY_R && !focus_on_config && state.num_titles > 0 && has_wifi) {
            Title *title = &state.titles[selected];

            ui_task_begin("Upload", "Checking server");

            char title_id_hex[17];
            snprintf(title_id_hex, sizeof(title_id_hex), "%02X%02X%02X%02X%02X%02X%02X%02X",
                title->title_id[0], title->title_id[1], title->title_id[2], title->title_id[3],
                title->title_id[4], title->title_id[5], title->title_id[6], title->title_id[7]);

            title->hash_calculated = false;

            char server_hash[65] = "";
            size_t server_size = 0;
            network_get_save_info(&state, title_id_hex, server_hash, &server_size);

            if (ui_confirm_sync(title, server_hash, server_size, true)) {
                ui_task_begin("Upload", "Uploading...");

                int result = network_upload(&state, selected);
                if (result == 0) {
                    // Clear red highlight after successful upload
                    title->scanned = true;
                    title->scan_result = SYNC_UP_TO_DATE;
                    title->on_server = true;
                    // Save state after manual upload
                    if (title->hash_calculated) {
                        char hash_hex[65];
                        for (int i = 0; i < 32; i++)
                            sprintf(&hash_hex[i*2], "%02x", title->hash[i]);
                        hash_hex[64] = '\0';
                        sync_save_last_hash(title_id_hex, hash_hex);
                    }
                    ui_task_end(KIND_OK, "Upload successful!", title->game_name, HINTS_BACK_B, KEY_B);
                } else {
                    ui_task_end(KIND_ERROR, "Upload failed!", title->game_name, HINTS_BACK_B, KEY_B);
                }
            } else {
                ui_message("Upload", "Upload cancelled", title->game_name, KIND_INFO, HINTS_BACK_B, KEY_B);
            }

            redraw = true;
        }

        // X button - scan all saves (check sync status only)
        if (pressed & KEY_X && !focus_on_config && state.num_titles > 0 && has_wifi) {
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

            redraw = true;
        }

        if (redraw) {
            view_main_top(&ui_top, &state, selected, focus_on_config, config_selected, has_wifi);
            view_save_list(&ui_bottom, &state, selected, scroll_offset, !focus_on_config, has_wifi);
            ui_present(&ui_top);
            ui_present(&ui_bottom);
            redraw = false;
        }
    }

    // Disconnect WiFi before exit to allow other games to initialize it cleanly
    // This may help avoid the nds-bootstrap issue where games won't load after WiFi apps
    network_cleanup();

    return 0;
}
