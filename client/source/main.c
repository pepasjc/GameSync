#include "common.h"
#include "card_spi.h"
#include "config.h"
#include "network.h"
#include "sync.h"
#include "title.h"
#include "ui.h"
#include "update.h"

static AppConfig config;
static TitleInfo titles[MAX_TITLES];
static int title_count = 0;
static int selected = 0;
static int scroll_offset = 0;
static char status[MAX_URL_LEN + 64];

// View filtering: filtered[] maps visible indices -> titles[] indices
static int view_mode = VIEW_ALL;
static int filtered[MAX_TITLES];
static int filtered_count = 0;

#define LIST_VISIBLE 27 // TOP_ROWS(30) - header(2) - footer(1)

static int title_compare(const void *a, const void *b) {
    const TitleInfo *ta = (const TitleInfo *)a;
    const TitleInfo *tb = (const TitleInfo *)b;
    
    // Sort 3DS games before DS games
    if (ta->is_nds != tb->is_nds) {
        return ta->is_nds ? 1 : -1;  // 3DS (is_nds=false) comes first
    }
    
    // Within same type, sort alphabetically
    return strcasecmp(ta->name, tb->name);
}

// Rebuild the filtered index list based on current view_mode
static void rebuild_filter(void) {
    filtered_count = 0;
    for (int i = 0; i < title_count; i++) {
        bool include = false;
        switch (view_mode) {
            case VIEW_3DS: include = !titles[i].is_nds; break;
            case VIEW_NDS: include = titles[i].is_nds; break;
            default:       include = true; break;
        }
        if (include)
            filtered[filtered_count++] = i;
    }
    // Reset selection
    selected = 0;
    scroll_offset = 0;
}

// Build a temporary TitleInfo array from filtered indices for drawing
// (ui_draw_title_list expects a contiguous array)
static TitleInfo filtered_titles[MAX_TITLES];

static void build_filtered_titles(void) {
    for (int i = 0; i < filtered_count; i++)
        filtered_titles[i] = titles[filtered[i]];
}

// Get the actual title index for the current selection
static int sel_title_idx(void) {
    if (selected >= 0 && selected < filtered_count)
        return filtered[selected];
    return -1;
}

// Count how many titles are marked (across ALL titles, not just filtered)
static int count_marked(void) {
    int count = 0;
    for (int i = 0; i < title_count; i++)
        if (titles[i].marked) count++;
    return count;
}

// Clear all marks
static void clear_marks(void) {
    for (int i = 0; i < title_count; i++)
        titles[i].marked = false;
}

static void scan_titles(void) {
    ui_draw_message("Scanning titles...");
    title_count = titles_scan(titles, MAX_TITLES, config.nds_dir);

    // Fetch game names from server
    if (title_count > 0) {
        ui_draw_message("Fetching game names...");
        titles_fetch_names(&config, titles, title_count);
        qsort(titles, title_count, sizeof(TitleInfo), title_compare);
    }

    rebuild_filter();
}

// Clamp scroll so the selected item is always visible
static void update_scroll(void) {
    if (selected < scroll_offset)
        scroll_offset = selected;
    if (selected >= scroll_offset + LIST_VISIBLE)
        scroll_offset = selected - LIST_VISIBLE + 1;
}

// Progress callback for sync operations
static void sync_progress(const char *message) {
    ui_update_progress(message);
    gfxFlushBuffers();
    gfxSwapBuffers();
    gspWaitForVBlank();
}

// Update progress callback
static void update_progress_cb(int pct) {
    static int last_pct = -1;
    if (pct != last_pct) {
        char msg[64];
        snprintf(msg, sizeof(msg), "Progress: %d%%", pct);
        ui_update_progress(msg);
        gfxFlushBuffers();
        gfxSwapBuffers();
        gspWaitForVBlank();
        last_pct = pct;
    }
    // Reset for next use when complete
    if (pct >= 100) last_pct = -1;
}

int main(int argc, char *argv[]) {
    (void)argc; (void)argv;

    // Initialize services
    gfxInitDefault();
    ui_init();
    amInit();
    fsInit();
    psInit();  // For random number generation (console ID)
    card_spi_init();  // For NDS cartridge SPI save access

    ui_draw_message("Loading config...");

    char config_error[512];
    if (!config_load(&config, config_error, sizeof(config_error))) {
        char msg[640];
        snprintf(msg, sizeof(msg),
            "\x1b[31mConfig error:\x1b[0m\n\n%s\n\n"
            "Expected file at:\n"
            "  %s\n\n"
            "With contents:\n"
            "  server_url=http://<pc-ip>:8000\n"
            "  api_key=<your-key>\n\n"
            "Press START to exit.",
            config_error, CONFIG_PATH);
        ui_draw_message(msg);

        while (aptMainLoop()) {
            hidScanInput();
            if (hidKeysDown() & KEY_START) break;
            gfxFlushBuffers();
            gfxSwapBuffers();
            gspWaitForVBlank();
        }

        fsExit();
        amExit();
        gfxExit();
        return 0;
    }

    // Initialize network
    if (!network_init()) {
        ui_draw_message(
            "\x1b[31mFailed to init network!\x1b[0m\n\n"
            "Make sure WiFi is enabled.\n\n"
            "Press START to exit.");

        while (aptMainLoop()) {
            hidScanInput();
            if (hidKeysDown() & KEY_START) break;
            gfxFlushBuffers();
            gfxSwapBuffers();
            gspWaitForVBlank();
        }

        fsExit();
        amExit();
        gfxExit();
        return 0;
    }

    // Initial title scan
    scan_titles();

    snprintf(status, sizeof(status), "Server: %.200s", config.server_url);
    // Draw to both buffers to prevent flicker
    build_filtered_titles();
    for (int buf = 0; buf < 2; buf++) {
        ui_draw_title_list(filtered_titles, filtered_count, selected, scroll_offset, view_mode);
        ui_draw_status(status);
        gfxFlushBuffers();
        gfxSwapBuffers();
        gspWaitForVBlank();
    }

    // Main loop
    while (aptMainLoop()) {
        hidScanInput();
        u32 kDown = hidKeysDown();

        bool redraw = false;

        if (kDown & KEY_START)
            break;

        if (kDown & KEY_DOWN && filtered_count > 0) {
            selected = (selected + 1) % filtered_count;
            update_scroll();
            redraw = true;
        }

        if (kDown & KEY_UP && filtered_count > 0) {
            selected = (selected - 1 + filtered_count) % filtered_count;
            update_scroll();
            redraw = true;
        }

        // Page down
        if (kDown & KEY_RIGHT && filtered_count > 0) {
            selected += LIST_VISIBLE;
            if (selected >= filtered_count) selected = filtered_count - 1;
            update_scroll();
            redraw = true;
        }

        // Page up
        if (kDown & KEY_LEFT && filtered_count > 0) {
            selected -= LIST_VISIBLE;
            if (selected < 0) selected = 0;
            update_scroll();
            redraw = true;
        }

        // R button - cycle view mode (All -> 3DS -> NDS -> All)
        if (kDown & KEY_R) {
            view_mode = (view_mode + 1) % 3;
            rebuild_filter();
            const char *names[] = {"All", "3DS", "NDS"};
            snprintf(status, sizeof(status), "View: %s (%d title(s))", names[view_mode], filtered_count);
            redraw = true;
        }

        // Y button - show history
        if (kDown & KEY_Y && filtered_count > 0) {
            int idx = sel_title_idx();
            if (idx >= 0) {
                ui_draw_message("Loading history...");

                HistoryVersion versions[MAX_HISTORY_VERSIONS];
                int count = sync_get_history(&config, titles[idx].title_id_hex, versions, MAX_HISTORY_VERSIONS);

                if (count < 0) {
                    snprintf(status, sizeof(status), "Failed to load history");
                } else if (count == 0) {
                    snprintf(status, sizeof(status), "No history available");
                } else {
                    char *selected_ts = ui_show_history(&titles[idx], versions, count);
                    if (selected_ts) {
                        ui_draw_message("Downloading version...");
                        SyncResult res = sync_download_history(&config, &titles[idx], selected_ts, sync_progress);
                        if (res == SYNC_OK) {
                            snprintf(status, sizeof(status), "Restored: %.40s", titles[idx].name);
                            titles[idx].in_conflict = false;
                        } else {
                            snprintf(status, sizeof(status), "\x1b[31mRestore failed\x1b[0m: %s",
                                sync_result_str(res));
                        }
                        free(selected_ts);
                    } else {
                        snprintf(status, sizeof(status), "History cancelled");
                    }
                }
            }
            redraw = true;
        }

        if (kDown & KEY_A && filtered_count > 0) {
            int marked = count_marked();
            if (marked > 0) {
                // Batch upload all marked titles
                char confirm[128];
                snprintf(confirm, sizeof(confirm),
                    "Upload %d marked title(s)?\n\n"
                    "Press A to confirm, B to cancel", marked);
                ui_draw_message(confirm);
                bool go = false;
                while (aptMainLoop()) {
                    hidScanInput();
                    u32 k = hidKeysDown();
                    if (k & KEY_A) { go = true; break; }
                    if (k & KEY_B) break;
                    gfxFlushBuffers(); gfxSwapBuffers(); gspWaitForVBlank();
                }
                if (go) {
                    int ok_count = 0, fail_count = 0;
                    for (int i = 0; i < title_count; i++) {
                        if (!titles[i].marked) continue;
                        char msg[128];
                        snprintf(msg, sizeof(msg), "Uploading %d/%d: %.30s",
                            ok_count + fail_count + 1, marked, titles[i].name);
                        sync_progress(msg);
                        SyncResult res = sync_title(&config, &titles[i], sync_progress);
                        if (res == SYNC_OK) {
                            ok_count++;
                            titles[i].in_conflict = false;
                        } else {
                            fail_count++;
                        }
                    }
                    clear_marks();
                    snprintf(status, sizeof(status), "Batch upload: %d OK, %d failed",
                        ok_count, fail_count);
                } else {
                    snprintf(status, sizeof(status), "Batch upload cancelled");
                }
            } else {
                // Smart sync single title
                int idx = sel_title_idx();
                if (idx >= 0) {
                    ui_draw_message("Analyzing sync...");
                    SaveDetails details;
                    if (sync_get_save_details(&config, &titles[idx], &details)) {
                        SyncAction suggested = sync_decide(&details);
                        SyncAction chosen = ui_confirm_smart_sync(&titles[idx], &details, suggested);

                        if (chosen == SYNC_ACTION_UPLOAD) {
                            ui_draw_message("Uploading...");
                            SyncResult res = sync_title(&config, &titles[idx], sync_progress);
                            if (res == SYNC_OK) {
                                snprintf(status, sizeof(status), "Uploaded: %.40s", titles[idx].name);
                                titles[idx].in_conflict = false;
                            } else {
                                snprintf(status, sizeof(status), "\x1b[31mUpload failed\x1b[0m: %s",
                                    sync_result_str(res));
                            }
                        } else if (chosen == SYNC_ACTION_DOWNLOAD) {
                            ui_draw_message("Downloading...");
                            SyncResult res = sync_download_title(&config, &titles[idx], sync_progress);
                            if (res == SYNC_OK) {
                                snprintf(status, sizeof(status), "Downloaded: %.40s", titles[idx].name);
                                titles[idx].in_conflict = false;
                            } else {
                                snprintf(status, sizeof(status), "\x1b[31mDownload failed\x1b[0m: %s",
                                    sync_result_str(res));
                            }
                        } else if (chosen == SYNC_ACTION_UP_TO_DATE) {
                            snprintf(status, sizeof(status), "Up to date: %.40s", titles[idx].name);
                        } else {
                            snprintf(status, sizeof(status), "Sync cancelled");
                        }
                    } else {
                        snprintf(status, sizeof(status), "Failed to load save details");
                    }
                }
            }
            redraw = true;
        }

        if (kDown & KEY_X && title_count > 0) {
            // Clear all conflict flags before sync
            for (int i = 0; i < title_count; i++)
                titles[i].in_conflict = false;

            SyncSummary summary;
            bool ok = sync_all(&config, titles, title_count, sync_progress, &summary);
            if (ok) {
                // Mark conflicting titles in our list
                for (int i = 0; i < summary.conflicts && i < MAX_CONFLICT_DISPLAY; i++) {
                    for (int j = 0; j < title_count; j++) {
                        if (strcmp(titles[j].title_id_hex, summary.conflict_titles[i]) == 0) {
                            titles[j].in_conflict = true;
                            break;
                        }
                    }
                }

                if (summary.conflicts > 0) {
                    // Auto-mark conflicting titles for batch resolve
                    for (int i = 0; i < title_count; i++) {
                        if (titles[i].in_conflict)
                            titles[i].marked = true;
                    }

                    // Show conflict details - use game names
                    char conflict_msg[512];
                    int pos = snprintf(conflict_msg, sizeof(conflict_msg),
                        "\x1b[33mSync completed with %d conflict(s):\x1b[0m\n\n",
                        summary.conflicts);

                    // List conflicting titles by name
                    for (int i = 0; i < title_count && pos < (int)sizeof(conflict_msg) - 50; i++) {
                        if (titles[i].in_conflict) {
                            pos += snprintf(conflict_msg + pos, sizeof(conflict_msg) - pos,
                                "  %.35s\n", titles[i].name);
                        }
                    }
                    if (summary.conflicts > MAX_CONFLICT_DISPLAY) {
                        pos += snprintf(conflict_msg + pos, sizeof(conflict_msg) - pos,
                            "  ...and %d more\n", summary.conflicts - MAX_CONFLICT_DISPLAY);
                    }

                    snprintf(conflict_msg + pos, sizeof(conflict_msg) - pos,
                        "\nConflicts \x1b[32mmarked\x1b[0m for batch resolve.\n"
                        "Press B to download all, or\n"
                        "resolve individually.\n\n"
                        "Press any button to continue.");

                    ui_draw_message(conflict_msg);
                    // Wait for any button press
                    while (aptMainLoop()) {
                        hidScanInput();
                        if (hidKeysDown()) break;
                        gfxFlushBuffers();
                        gfxSwapBuffers();
                        gspWaitForVBlank();
                    }

                    snprintf(status, sizeof(status),
                        "Up:%d Dn:%d OK:%d \x1b[33mConflict:%d\x1b[0m Fail:%d",
                        summary.uploaded, summary.downloaded, summary.up_to_date,
                        summary.conflicts, summary.failed);
                } else {
                    snprintf(status, sizeof(status),
                        "Up:%d Dn:%d OK:%d Fail:%d",
                        summary.uploaded, summary.downloaded, summary.up_to_date,
                        summary.failed);
                }
            } else {
                snprintf(status, sizeof(status), "\x1b[31mSync failed!\x1b[0m Check server.");
            }
            rebuild_filter();
            redraw = true;
        }

        // SELECT button - toggle mark on current item
        if (kDown & KEY_SELECT && filtered_count > 0) {
            int idx = sel_title_idx();
            if (idx >= 0) {
                titles[idx].marked = !titles[idx].marked;
                int mc = count_marked();
                if (mc > 0)
                    snprintf(status, sizeof(status), "%d title(s) marked", mc);
                else
                    snprintf(status, sizeof(status), "Marks cleared");
            }
            redraw = true;
        }

        // L button - config editor (includes rescan + update options)
        if (kDown & KEY_L) {
            int result = ui_show_config_editor(&config);
            if (result == CONFIG_RESULT_RESCAN) {
                scan_titles();
                snprintf(status, sizeof(status), "Rescanned. %d title(s) found.", title_count);
            } else if (result == CONFIG_RESULT_SAVED) {
                snprintf(status, sizeof(status), "Config saved. Server: %.30s", config.server_url);
            } else if (result == CONFIG_RESULT_UPDATE) {
                // Check for updates (moved from SELECT)
                ui_draw_message("Checking for updates...");

                UpdateInfo update_info;
                if (!update_check(&config, &update_info)) {
                    snprintf(status, sizeof(status), "Update check failed");
                } else if (!update_info.available) {
                    snprintf(status, sizeof(status), "You have the latest version (%s)", APP_VERSION);
                } else {
                    char confirm_msg[512];
                    snprintf(confirm_msg, sizeof(confirm_msg),
                        "\x1b[33mUpdate available!\x1b[0m\n\n"
                        "Current: %s\n"
                        "Latest:  %s\n"
                        "Size:    %lu KB\n\n"
                        "Press A to download and install\n"
                        "Press B to cancel",
                        APP_VERSION,
                        update_info.latest_version,
                        (unsigned long)(update_info.file_size / 1024));
                    ui_draw_message(confirm_msg);

                    bool do_update = false;
                    while (aptMainLoop()) {
                        hidScanInput();
                        u32 k = hidKeysDown();
                        if (k & KEY_A) { do_update = true; break; }
                        if (k & KEY_B) { break; }
                        gfxFlushBuffers();
                        gfxSwapBuffers();
                        gspWaitForVBlank();
                    }

                    if (do_update) {
                        ui_draw_message("Downloading update...");
                        if (!update_download(&config, update_info.download_url, update_progress_cb)) {
                            snprintf(status, sizeof(status), "\x1b[31mDownload failed!\x1b[0m");
                        } else {
                            ui_draw_message("Installing update...\n\nPlease wait, do not power off.");
                            char install_error[128] = {0};
                            if (!update_install(update_progress_cb, install_error, sizeof(install_error))) {
                                char errmsg[256];
                                snprintf(errmsg, sizeof(errmsg),
                                    "\x1b[31mInstall failed:\x1b[0m\n\n%s\n\n"
                                    "Press any button to continue.",
                                    install_error);
                                ui_draw_message(errmsg);
                                while (aptMainLoop()) {
                                    hidScanInput();
                                    if (hidKeysDown()) break;
                                    gfxFlushBuffers();
                                    gfxSwapBuffers();
                                    gspWaitForVBlank();
                                }
                                snprintf(status, sizeof(status), "Install failed");
                            } else {
                                ui_draw_message(
                                    "\x1b[32mUpdate installed!\x1b[0m\n\n"
                                    "Restarting application...");
                                svcSleepThread(1500000000LL);
                                update_relaunch();

                                ui_draw_message(
                                    "\x1b[32mUpdate installed!\x1b[0m\n\n"
                                    "Please restart the application\n"
                                    "to use the new version.\n\n"
                                    "Press START to exit.");
                                while (aptMainLoop()) {
                                    hidScanInput();
                                    if (hidKeysDown() & KEY_START) break;
                                    gfxFlushBuffers();
                                    gfxSwapBuffers();
                                    gspWaitForVBlank();
                                }
                                goto cleanup;
                            }
                        }
                    } else {
                        snprintf(status, sizeof(status), "Update cancelled");
                    }
                }
            } else {
                snprintf(status, sizeof(status), "Config unchanged");
            }
            redraw = true;
        }

        if (redraw) {
            build_filtered_titles();
            // Draw to both buffers to prevent flicker with double buffering
            for (int buf = 0; buf < 2; buf++) {
                ui_draw_title_list(filtered_titles, filtered_count, selected, scroll_offset, view_mode);
                ui_draw_status(status);
                gfxFlushBuffers();
                gfxSwapBuffers();
                gspWaitForVBlank();
            }
        } else {
            // No redraw needed, just wait for next frame
            gspWaitForVBlank();
        }
    }

cleanup:
    // Cleanup
    network_exit();
    card_spi_exit();
    psExit();
    fsExit();
    amExit();
    gfxExit();
    return 0;
}
