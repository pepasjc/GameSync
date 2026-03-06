#include <nds.h>
#include <stdio.h>
#include <fat.h>
#include "common.h"
#include "config.h"
#include "saves.h"
#include "network.h"
#include "sync.h"
#include "ui.h"
#include "update.h"

#define LIST_VISIBLE 20  // Visible titles on screen

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

int main(int argc, char *argv[]) {
    // argv[0] is the executable path (provided by homebrew loader)
    const char *self_path = (argc > 0 && argv && argv[0]) ? argv[0] : NULL;
    // Initialize FAT first
    if (!fatInitDefault()) {
        consoleDemoInit();
        iprintf("FAT init failed!\n");
        iprintf("Make sure SD/flashcard\nis inserted.\n\n");
        iprintf("Press START to exit\n");
        
        while(pmMainLoop()) {
            swiWaitForVBlank();
            scanKeys();
            if(keysDown() & KEY_START) break;
        }
        return 0;
    }
    
    consoleDemoInit();
    
    // Initialize config
    memset(&state, 0, sizeof(SyncState));
    
    // Load config from same path as 3DS client
    char config_error[256];
    if (!config_load(&state, config_error, sizeof(config_error))) {
        consoleClear();
        iprintf("=== Config Setup ===\n\n");
        iprintf("%s\n\n", config_error);
        iprintf("Press START to exit\n");
        
        while(pmMainLoop()) {
            swiWaitForVBlank();
            scanKeys();
            if(keysDown() & KEY_START) break;
        }
        return 0;
    }
    
    // Initialize network (optional - continue if fails)
    iprintf("Initializing network...\n");
    bool has_wifi = (network_init(&state) == 0);
    if (!has_wifi) {
        iprintf("\nWiFi unavailable\n");
        iprintf("Upload/download disabled\n\n");
        iprintf("Press A to continue\n");
        
        while(pmMainLoop()) {
            swiWaitForVBlank();
            scanKeys();
            if(keysDown() & KEY_A) break;
        }
    }
    
    // Check for pending update before continuing
    if (update_apply_pending(self_path)) {
        iprintf("\nPress START to exit\n");
        while(pmMainLoop()) {
            swiWaitForVBlank();
            scanKeys();
            if(keysDown() & KEY_START) break;
        }
        return 0;
    }
    
    // Scan for saves
    consoleClear();
    iprintf("Scanning saves...\n\n");
    saves_scan(&state);
    
    iprintf("\nFound %d saves!\n", state.num_titles);
    iprintf("\nPress A to continue\n");
    
    while(pmMainLoop()) {
        swiWaitForVBlank();
        scanKeys();
        if(keysDown() & KEY_A) break;
    }
    
    // Set up dual screen mode
    videoSetMode(MODE_0_2D);
    videoSetModeSub(MODE_0_2D);
    
    vramSetBankA(VRAM_A_MAIN_BG);
    vramSetBankC(VRAM_C_SUB_BG);
    
    PrintConsole topScreen;
    PrintConsole bottomScreen;
    
    consoleInit(&topScreen, 3, BgType_Text4bpp, BgSize_T_256x256, 31, 0, true, true);
    consoleInit(&bottomScreen, 3, BgType_Text4bpp, BgSize_T_256x256, 31, 0, false, true);
    
    if (state.num_titles == 0) {
        consoleSelect(&bottomScreen);
        iprintf("No saves found!\n\n");
        iprintf("Press START to exit\n");
        
        while(pmMainLoop()) {
            swiWaitForVBlank();
            scanKeys();
            if(keysDown() & KEY_START) break;
        }
        return 0;
    }
    
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
                config_selected = (config_selected + 1) % 7;
                redraw = true;
            } else if (state.num_titles > 0) {
                selected = (selected + 1) % state.num_titles;
                update_scroll();
                redraw = true;
            }
        }
        
        if (pressed & KEY_UP) {
            if (focus_on_config) {
                config_selected = (config_selected - 1 + 7) % 7;
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
                    if (config_edit_field("http://192.168.1.100:8000", state.server_url, sizeof(state.server_url))) {
                        config_save(&state);
                    }
                    redraw = true;
                } else if (config_selected == 1) {
                    // Edit API Key
                    if (config_edit_field("your-api-key", state.api_key, sizeof(state.api_key))) {
                        config_save(&state);
                    }
                    redraw = true;
                } else if (config_selected == 2) {
                    // Edit WiFi SSID
                    if (config_edit_field("wifi-ssid", state.wifi_ssid, sizeof(state.wifi_ssid))) {
                        config_save(&state);
                    }
                    redraw = true;
                } else if (config_selected == 3) {
                    // Edit WiFi WEP Key
                    if (config_edit_field("wifi-key", state.wifi_wep_key, sizeof(state.wifi_wep_key))) {
                        config_save(&state);
                    }
                    redraw = true;
                } else if (config_selected == 4) {
                    // Rescan Saves
                    consoleSelect(&bottomScreen);
                    consoleClear();
                    iprintf("Rescanning saves...\n\n");
                    saves_scan(&state);
                    selected = 0;
                    scroll_offset = 0;
                    redraw = true;
                } else if (config_selected == 5) {
                    // Connect WiFi
                    consoleSelect(&bottomScreen);
                    consoleClear();
                    iprintf("Connecting WiFi...\n\n");
                    has_wifi = (network_init(&state) == 0);
                    if (!has_wifi) {
                        iprintf("WiFi connection failed\n");
                        iprintf("Press any button\n");
                        while(pmMainLoop()) {
                            swiWaitForVBlank();
                            scanKeys();
                            if(keysDown()) break;
                        }
                    }
                    redraw = true;
                } else if (config_selected == 6) {
                    // Check for updates
                    if (!has_wifi) {
                        consoleSelect(&bottomScreen);
                        consoleClear();
                        iprintf("WiFi required for updates\n");
                        iprintf("Press any button\n");
                        while(pmMainLoop()) {
                            swiWaitForVBlank();
                            scanKeys();
                            if(keysDown()) break;
                        }
                        redraw = true;
                        continue;
                    }
                    
                    consoleSelect(&bottomScreen);
                    consoleClear();
                    iprintf("Checking for updates...\n\n");
                    
                    UpdateInfo update_info;
                    if (!update_check(&state, &update_info)) {
                        iprintf("Update check failed\n");
                        iprintf("Press any button\n");
                        while(pmMainLoop()) {
                            swiWaitForVBlank();
                            scanKeys();
                            if(keysDown()) break;
                        }
                        redraw = true;
                        continue;
                    }
                    
                    if (!update_info.available) {
                        iprintf("You have the latest\n");
                        iprintf("version (%s)\n\n", APP_VERSION);
                        iprintf("Press any button\n");
                        while(pmMainLoop()) {
                            swiWaitForVBlank();
                            scanKeys();
                            if(keysDown()) break;
                        }
                        redraw = true;
                        continue;
                    }
                    
                    // Show update available
                    consoleClear();
                    iprintf("Update available!\n\n");
                    iprintf("Current: %s\n", APP_VERSION);
                    iprintf("Latest:  %s\n\n", update_info.latest_version);
                    iprintf("Size: %zu KB\n\n", update_info.file_size / 1024);
                    iprintf("A: Download & Install\n");
                    iprintf("B: Cancel\n");
                    
                    bool do_update = false;
                    while(pmMainLoop()) {
                        swiWaitForVBlank();
                        scanKeys();
                        int k = keysDown();
                        if (k & KEY_A) { do_update = true; break; }
                        if (k & KEY_B) break;
                    }
                    
                    if (do_update) {
                        consoleClear();
                        iprintf("Downloading...\n\n");
                        
                        if (!update_download(&state, update_info.download_url, NULL)) {
                            iprintf("\nDownload failed\n");
                        } else {
                            iprintf("\nUpdate ready!\n");
                            iprintf("Restart to apply\n");
                        }
                        
                        iprintf("\nPress any button\n");
                        while(pmMainLoop()) {
                            swiWaitForVBlank();
                            scanKeys();
                            if(keysDown()) break;
                        }
                    }
                    redraw = true;
                }
                continue;
            }
        }
        
        // Y button - show save details (only when focused on saves)
        if (pressed & KEY_Y && !focus_on_config && state.num_titles > 0) {
            consoleSelect(&bottomScreen);
            Title *title = &state.titles[selected];
            
            consoleClear();
            iprintf("Loading details...\n");
            
            // Ensure hash is calculated
            if (saves_ensure_hash(title) == 0) {
                ui_show_save_details(title);
            } else {
                iprintf("Failed to calculate hash!\n");
                iprintf("\nPress any button\n");
                
                while(pmMainLoop()) {
                    swiWaitForVBlank();
                    scanKeys();
                    if(keysDown()) break;
                }
            }
            
            redraw = true;
        }
        
        // A button - smart sync (only when focused on saves)
        if (pressed & KEY_A && !focus_on_config && state.num_titles > 0 && has_wifi) {
            consoleSelect(&bottomScreen);
            Title *title = &state.titles[selected];

            consoleClear();
            iprintf("Analyzing sync...\n");

            // Force fresh hash calculation
            title->hash_calculated = false;

            SyncDecision decision;
            if (sync_decide(&state, selected, &decision) != 0) {
                iprintf("\nFailed to check sync!\n");
                iprintf("Press B to go back\n");
                while(pmMainLoop()) {
                    swiWaitForVBlank();
                    scanKeys();
                    if(keysDown() & KEY_B) break;
                }
                redraw = true;
                continue;
            }

            // Show decision and get user confirmation
            SyncAction chosen = ui_confirm_smart_sync(title, &decision);

            if (chosen == SYNC_UPLOAD || chosen == SYNC_DOWNLOAD) {
                consoleClear();
                iprintf("%s...\n\n", chosen == SYNC_UPLOAD ? "Uploading" : "Downloading");

                int result = sync_execute(&state, selected, chosen);
                if (result == 0) {
                    iprintf("\nSuccess!\n");
                    // Clear red highlight after successful sync
                    title->scanned = true;
                    title->scan_result = SYNC_UP_TO_DATE;
                } else {
                    iprintf("\nFailed!\n");
                }

                iprintf("Press B to go back\n");
                while(pmMainLoop()) {
                    swiWaitForVBlank();
                    scanKeys();
                    if(keysDown() & KEY_B) break;
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
            consoleSelect(&bottomScreen);
            Title *title = &state.titles[selected];

            consoleClear();
            iprintf("Checking server...\n");

            char title_id_hex[17];
            snprintf(title_id_hex, sizeof(title_id_hex), "%02X%02X%02X%02X%02X%02X%02X%02X",
                title->title_id[0], title->title_id[1], title->title_id[2], title->title_id[3],
                title->title_id[4], title->title_id[5], title->title_id[6], title->title_id[7]);

            title->hash_calculated = false;

            char server_hash[65] = "";
            size_t server_size = 0;
            network_get_save_info(&state, title_id_hex, server_hash, &server_size);

            if (ui_confirm_sync(title, server_hash, server_size, true)) {
                consoleClear();
                iprintf("Uploading...\n\n");

                int result = network_upload(&state, selected);
                if (result == 0) {
                    iprintf("\nUpload successful!\n");
                    // Clear red highlight after successful upload
                    title->scanned = true;
                    title->scan_result = SYNC_UP_TO_DATE;
                    // Save state after manual upload
                    if (title->hash_calculated) {
                        char hash_hex[65];
                        for (int i = 0; i < 32; i++)
                            sprintf(&hash_hex[i*2], "%02x", title->hash[i]);
                        hash_hex[64] = '\0';
                        sync_save_last_hash(title_id_hex, hash_hex);
                    }
                } else {
                    iprintf("\nUpload failed!\n");
                }

                iprintf("Press B to go back\n");
            } else {
                consoleClear();
                iprintf("Upload cancelled\n");
                iprintf("Press B to go back\n");
            }

            while(pmMainLoop()) {
                swiWaitForVBlank();
                scanKeys();
                if(keysDown() & KEY_B) break;
            }

            redraw = true;
        }

        // X button - scan all saves (check sync status only)
        if (pressed & KEY_X && !focus_on_config && state.num_titles > 0 && has_wifi) {
            consoleSelect(&bottomScreen);
            consoleClear();
            iprintf("=== Scan All ===\n\n");
            iprintf("Scanning %d saves...\n\n", state.num_titles);

            SyncSummary summary;
            sync_scan_all(&state, &summary);

            consoleClear();
            iprintf("=== Scan Complete ===\n\n");
            iprintf("Up to date:    %d\n", summary.up_to_date);
            iprintf("Need upload:   %d\n", summary.uploaded);
            iprintf("Need download: %d\n", summary.downloaded);
            iprintf("Conflicts:     %d\n", summary.conflicts);
            iprintf("Failed:        %d\n", summary.failed);
            iprintf("\nOut-of-sync saves are\n");
            iprintf("highlighted in red.\n");
            iprintf("\nPress any button\n");

            while(pmMainLoop()) {
                swiWaitForVBlank();
                scanKeys();
                if(keysDown()) break;
            }

            redraw = true;
        }
        
        if (redraw) {
            // Reinit consoles to reset color state (consoleClear doesn't reset colors)
            consoleInit(&topScreen, 3, BgType_Text4bpp, BgSize_T_256x256, 31, 0, true, true);
            consoleInit(&bottomScreen, 3, BgType_Text4bpp, BgSize_T_256x256, 31, 0, false, true);

            // Draw config on top screen
            consoleSelect(&topScreen);
            ui_draw_config(&state, config_selected, focus_on_config, has_wifi);
            
            // Draw saves list on bottom screen
            consoleSelect(&bottomScreen);
            iprintf("=== NDS Save Sync v%s ===\n", APP_VERSION);
            iprintf("Found %d saves\n\n", state.num_titles);
            
            // Display visible titles
            int start = scroll_offset;
            int end = (scroll_offset + LIST_VISIBLE < state.num_titles) ? 
                      scroll_offset + LIST_VISIBLE : state.num_titles;
            
            for (int i = start; i < end; i++) {
                // Apply color based on scan status
                if (state.titles[i].scanned) {
                    if (state.titles[i].scan_result != SYNC_UP_TO_DATE) {
                        iprintf("\x1b[31m");  // Red for out-of-sync
                    }
                }

                if (i == selected) {
                    iprintf("> ");
                } else {
                    iprintf("  ");
                }

                // Truncate long names
                char name[25];
                strncpy(name, state.titles[i].game_name, 24);
                name[24] = '\0';

                // Show server status indicator
                char status = state.titles[i].on_server ? 'S' : ' ';
                iprintf("%-24s [%c]", name, status);

                // Reset color
                if (state.titles[i].scanned) {
                    iprintf("\x1b[0m");
                }
                iprintf("\n");
            }
            
            
            redraw = false;
        }
    }

    // Disconnect WiFi before exit to allow other games to initialize it cleanly
    // This may help avoid the nds-bootstrap issue where games won't load after WiFi apps
    network_cleanup();
    
    return 0;
}
