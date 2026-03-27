#include "ui.h"
#include "sync.h"

static PrintConsole top_screen;
static PrintConsole bottom_screen;

#define TOP_ROWS    30
#define TOP_COLS    50
#define LIST_ROWS   (TOP_ROWS - 3) // Reserve top 2 for header, bottom 1 for count

void ui_init(void) {
    consoleInit(GFX_TOP, &top_screen);
    consoleInit(GFX_BOTTOM, &bottom_screen);
}

void ui_reinit(void) {
    consoleInit(GFX_TOP, &top_screen);
    consoleInit(GFX_BOTTOM, &bottom_screen);
}

static const char *media_type_str(const TitleInfo *t) {
    if (t->is_nds && t->media_type == MEDIATYPE_GAME_CARD) return "Cart";
    if (t->is_nds) return "NDS";
    switch (t->media_type) {
        case MEDIATYPE_SD:        return "3DS";
        case MEDIATYPE_GAME_CARD: return "Card";
        default:                  return "?";
    }
}

static const char *view_mode_str(int view_mode) {
    switch (view_mode) {
        case VIEW_3DS: return "[3DS]";
        case VIEW_NDS: return "[NDS]";
        default:       return "[All]";
    }
}

void ui_draw_title_list(const TitleInfo *titles, int count, int selected, int scroll_offset, int view_mode) {
    consoleSelect(&top_screen);

    // Header (line 1) - pad to full width to overwrite without clearing
    printf("\x1b[1;1H\x1b[36m--- Save Sync v%s %s ---\x1b[0m%-*s",
        APP_VERSION, view_mode_str(view_mode), TOP_COLS - 24, "");

    if (count == 0) {
        printf("\x1b[3;1H  No titles with save data found.%-*s", TOP_COLS - 34, "");
        printf("\x1b[4;1H  Make sure you have games installed.%-*s", TOP_COLS - 38, "");
        // Blank remaining lines with spaces
        for (int i = 5; i <= TOP_ROWS; i++) {
            printf("\x1b[%d;1H%-*s", i, TOP_COLS, "");
        }
        return;
    }

    // Title list (scrollable) - starts at line 3
    for (int i = 0; i < LIST_ROWS; i++) {
        int row = 3 + i;  // Start at line 3
        int idx = scroll_offset + i;

        printf("\x1b[%d;1H", row);  // Position cursor

        if (idx >= count) {
            // Blank line - overwrite with spaces
            printf("%-*s", TOP_COLS, "");
            continue;
        }

        const TitleInfo *t = &titles[idx];
        const char *cursor = (idx == selected) ? ">" : " ";
        const char *mark = t->marked ? "*" : " ";

        // Color: red for conflict, cyan for cartridge, magenta for NDS,
        // yellow for selected, white otherwise
        const char *color;
        if (t->in_conflict) {
            color = "\x1b[31m";  // Red for conflict
        } else if (t->marked) {
            color = "\x1b[32m";  // Green for marked
        } else if (t->media_type == MEDIATYPE_GAME_CARD) {
            color = "\x1b[36m";  // Cyan for cartridge (manual sync only)
        } else if (t->is_nds) {
            color = "\x1b[35m";  // Magenta for NDS games on SD
        } else if (idx == selected) {
            color = "\x1b[33m";  // Yellow for selected
        } else {
            color = "\x1b[0m";
        }

        // Format line and pad to full width
        char line[TOP_COLS + 1];
        snprintf(line, sizeof(line), "%s%s %-4s %.41s",
            cursor, mark,
            media_type_str(t),
            t->name);

        printf("%s%-*s\x1b[0m", color, TOP_COLS, line);
    }

    // Footer with count (last row) - pad to full width
    char footer[TOP_COLS + 1];
    snprintf(footer, sizeof(footer), " %d title(s) | D-Pad: navigate", count);
    printf("\x1b[%d;1H\x1b[90m%-*s\x1b[0m", TOP_ROWS, TOP_COLS, footer);
}

#define BOT_COLS 40  // Bottom screen width

void ui_draw_status(const char *status_line) {
    consoleSelect(&bottom_screen);

    // Overwrite each line - pad to full width instead of clearing
    printf("\x1b[1;1H\x1b[36mActions:\x1b[0m%-*s", BOT_COLS - 8, "");
    printf("\x1b[2;1H A - Smart Sync | X - Sync All%-*s", BOT_COLS - 31, "");
    printf("\x1b[3;1H Y - History | SELECT - Mark%-*s", BOT_COLS - 28, "");
    printf("\x1b[4;1H R - Switch tab | L - Config%-*s", BOT_COLS - 30, "");
    printf("\x1b[5;1H START - Exit%-*s", BOT_COLS - 15, "");
    printf("\x1b[6;1H%-*s", BOT_COLS, "");
    printf("\x1b[7;1H\x1b[36mCyan\x1b[0m=cart \x1b[35mMag\x1b[0m=NDS \x1b[32mGrn\x1b[0m=mark%-*s", BOT_COLS - 26, "");
    printf("\x1b[8;1H%-*s", BOT_COLS, "");
    printf("\x1b[9;1H%-*s", BOT_COLS, "");
    printf("\x1b[10;1H%-*s", BOT_COLS, "");
    printf("\x1b[11;1H%-*s", BOT_COLS, "");

    char status_padded[BOT_COLS + 1];
    snprintf(status_padded, sizeof(status_padded), "%s", status_line ? status_line : "Ready.");
    printf("\x1b[12;1H\x1b[90m%-*s\x1b[0m", BOT_COLS, status_padded);
}

void ui_draw_message(const char *msg) {
    consoleSelect(&bottom_screen);
    consoleClear();
    printf("\x1b[1;1H%s\n", msg);
}

void ui_update_progress(const char *msg) {
    // Lightweight update: just overwrite line 1, pad to full width
    consoleSelect(&bottom_screen);
    printf("\x1b[1;1H%-*s", BOT_COLS, msg);
}

void ui_clear(void) {
    consoleSelect(&top_screen);
    consoleClear();
    consoleSelect(&bottom_screen);
    consoleClear();
}

// Format size in human-readable form
static void format_size(u32 bytes, char *out, int out_size) {
    if (bytes >= 1024 * 1024) {
        snprintf(out, out_size, "%.1f MB", bytes / (1024.0 * 1024.0));
    } else if (bytes >= 1024) {
        snprintf(out, out_size, "%.1f KB", bytes / 1024.0);
    } else {
        snprintf(out, out_size, "%lu B", (unsigned long)bytes);
    }
}

// Format date from ISO 8601 (YYYY-MM-DDTHH:MM:SS) to readable form
static void format_date(const char *iso, char *out, int out_size) {
    // Extract date and time parts
    if (strlen(iso) >= 16 && iso[10] == 'T') {
        snprintf(out, out_size, "%.10s %.5s", iso, iso + 11);
    } else if (strlen(iso) > 0) {
        snprintf(out, out_size, "%.19s", iso);
    } else {
        snprintf(out, out_size, "N/A");
    }
}

// Draw save details on top screen, returns current row for additional content
static int draw_save_details(const TitleInfo *title, const SaveDetails *details) {
    consoleSelect(&top_screen);
    consoleClear();

    int row = 1;

    // Title name header (truncate if too long)
    printf("\x1b[%d;1H\x1b[36m--- %.44s ---\x1b[0m", row++, title->name);
    row++;

    // Title ID
    printf("\x1b[%d;1H Title ID: %s", row++, title->title_id_hex);

    // Media type
    const char *media;
    if (title->is_nds)
        media = "NDS (nds-bootstrap)";
    else if (title->media_type == MEDIATYPE_SD)
        media = "SD Card";
    else if (title->media_type == MEDIATYPE_GAME_CARD)
        media = "Game Card";
    else
        media = "Unknown";
    printf("\x1b[%d;1H Media:    %s", row++, media);
    row++;

    // Local save info
    printf("\x1b[%d;1H\x1b[33m-- Local Save --\x1b[0m", row++);
    if (details->local_exists) {
        char size_str[32];
        format_size(details->local_size, size_str, sizeof(size_str));
        printf("\x1b[%d;1H Files: %d | Size: %s", row++, details->local_file_count, size_str);
        printf("\x1b[%d;1H Hash:  %.32s...", row++, details->local_hash);
    } else {
        printf("\x1b[%d;1H No local save data", row++);
    }
    row++;

    // Server save info
    printf("\x1b[%d;1H\x1b[33m-- Server Save --\x1b[0m", row++);
    if (details->server_exists) {
        char size_str[32];
        format_size(details->server_size, size_str, sizeof(size_str));
        printf("\x1b[%d;1H Files: %d | Size: %s", row++, details->server_file_count, size_str);
        printf("\x1b[%d;1H Hash:  %.32s...", row++, details->server_hash);

        char date_str[32];
        format_date(details->server_last_sync, date_str, sizeof(date_str));
        printf("\x1b[%d;1H Last sync: %s", row++, date_str);

        if (details->server_console_id[0]) {
            printf("\x1b[%d;1H From console: %.16s", row++, details->server_console_id);
        }
    } else {
        printf("\x1b[%d;1H Not yet uploaded to server", row++);
    }
    row++;

    // Sync status
    printf("\x1b[%d;1H\x1b[33m-- Sync Status --\x1b[0m", row++);
    if (details->is_synced) {
        printf("\x1b[%d;1H\x1b[32m Synced (hashes match)\x1b[0m", row++);
    } else if (details->local_exists && details->server_exists) {
        printf("\x1b[%d;1H\x1b[31m Out of sync (different hashes)\x1b[0m", row++);
    } else if (details->local_exists && !details->server_exists) {
        printf("\x1b[%d;1H\x1b[33m Local only (not uploaded)\x1b[0m", row++);
    } else if (!details->local_exists && details->server_exists) {
        printf("\x1b[%d;1H\x1b[33m Server only (not downloaded)\x1b[0m", row++);
    } else {
        printf("\x1b[%d;1H\x1b[90m No save data\x1b[0m", row++);
    }

    if (details->has_last_synced) {
        printf("\x1b[%d;1H Last synced: %.32s...", row++, details->last_synced_hash);
    }

    return row;
}

void ui_show_save_details(const TitleInfo *title, const SaveDetails *details) {
    draw_save_details(title, details);

    // Footer
    printf("\x1b[%d;1H\x1b[90m Press B to close\x1b[0m", TOP_ROWS);

    // Draw to both buffers to prevent flicker
    gfxFlushBuffers();
    gfxSwapBuffers();
    gspWaitForVBlank();

    // Wait for B button
    while (aptMainLoop()) {
        hidScanInput();
        u32 kDown = hidKeysDown();
        if (kDown & KEY_B) break;
        gfxFlushBuffers();
        gfxSwapBuffers();
        gspWaitForVBlank();
    }
}

bool ui_confirm_sync(const TitleInfo *title, const SaveDetails *details, bool is_upload) {
    int row = draw_save_details(title, details);
    row++;

    // Action description
    if (is_upload) {
        printf("\x1b[%d;1H\x1b[33;1m >> UPLOAD: local -> server\x1b[0m", row++);
    } else {
        printf("\x1b[%d;1H\x1b[33;1m >> DOWNLOAD: server -> local\x1b[0m", row++);
    }

    // Footer
    printf("\x1b[%d;1H\x1b[90m A: Confirm | B: Cancel\x1b[0m", TOP_ROWS);

    // Draw to both buffers to prevent flicker
    gfxFlushBuffers();
    gfxSwapBuffers();
    gspWaitForVBlank();

    // Wait for A (confirm) or B (cancel)
    while (aptMainLoop()) {
        hidScanInput();
        u32 kDown = hidKeysDown();
        if (kDown & KEY_A) return true;
        if (kDown & KEY_B) return false;
        gfxFlushBuffers();
        gfxSwapBuffers();
        gspWaitForVBlank();
    }
    return false;
}

SyncAction ui_confirm_smart_sync(const TitleInfo *title, const SaveDetails *details, SyncAction suggested) {
    consoleSelect(&top_screen);
    consoleClear();

    int row = 1;

    // Title
    printf("\x1b[%d;1H\x1b[36m--- Smart Sync: %.44s ---\x1b[0m", row++, title->name);
    row++;

    // Local info
    printf("\x1b[%d;1H\x1b[33m-- Local --\x1b[0m", row++);
    if (details->local_exists) {
        char size_str[32];
        format_size(details->local_size, size_str, sizeof(size_str));
        printf("\x1b[%d;1H Size: %s", row++, size_str);
        printf("\x1b[%d;1H Hash: %.32s...", row++, details->local_hash);
    } else {
        printf("\x1b[%d;1H No local save", row++);
    }
    row++;

    // Server info
    printf("\x1b[%d;1H\x1b[33m-- Server --\x1b[0m", row++);
    if (details->server_exists) {
        char size_str[32];
        format_size(details->server_size, size_str, sizeof(size_str));
        printf("\x1b[%d;1H Size: %s", row++, size_str);
        printf("\x1b[%d;1H Hash: %.32s...", row++, details->server_hash);
    } else {
        printf("\x1b[%d;1H No server save", row++);
    }
    row++;

    // Show sync history
    if (details->has_last_synced) {
        printf("\x1b[%d;1H\x1b[33m-- Last Synced --\x1b[0m", row++);
        printf("\x1b[%d;1H Hash: %.32s...", row++, details->last_synced_hash);
        row++;
    }

    // Show suggested action
    printf("\x1b[%d;1H\x1b[36m-- Suggested Action --\x1b[0m", row++);
    switch (suggested) {
        case SYNC_ACTION_UP_TO_DATE:
            printf("\x1b[%d;1H\x1b[32m Already in sync!\x1b[0m", row++);
            printf("\x1b[%d;1H\x1b[90m Hashes match\x1b[0m", row++);
            break;
        case SYNC_ACTION_UPLOAD:
            if (details->has_last_synced) {
                printf("\x1b[%d;1H\x1b[32m >> UPLOAD (local changed)\x1b[0m", row++);
            } else {
                printf("\x1b[%d;1H\x1b[32m >> UPLOAD\x1b[0m", row++);
            }
            break;
        case SYNC_ACTION_DOWNLOAD:
            if (details->has_last_synced) {
                printf("\x1b[%d;1H\x1b[32m >> DOWNLOAD (server changed)\x1b[0m", row++);
            } else {
                printf("\x1b[%d;1H\x1b[32m >> DOWNLOAD\x1b[0m", row++);
            }
            break;
        case SYNC_ACTION_CONFLICT:
            printf("\x1b[%d;1H\x1b[31m !! CONFLICT !!\x1b[0m", row++);
            printf("\x1b[%d;1H Both local and server\x1b[0m", row++);
            printf("\x1b[%d;1H have changed.\x1b[0m", row++);
            break;
    }

    // Footer buttons
    printf("\x1b[%d;1H\x1b[90m----------------------------------------\x1b[0m", TOP_ROWS - 1);
    if (suggested == SYNC_ACTION_CONFLICT) {
        printf("\x1b[%d;1H\x1b[90m R:Upload L:Download B:Cancel\x1b[0m", TOP_ROWS);
    } else if (suggested == SYNC_ACTION_UP_TO_DATE) {
        printf("\x1b[%d;1H\x1b[90m A:OK B:Cancel\x1b[0m", TOP_ROWS);
    } else {
        printf("\x1b[%d;1H\x1b[90m A:Confirm B:Cancel\x1b[0m", TOP_ROWS);
    }

    // Draw to both buffers
    gfxFlushBuffers();
    gfxSwapBuffers();
    gspWaitForVBlank();

    // Wait for input
    while (aptMainLoop()) {
        hidScanInput();
        u32 kDown = hidKeysDown();

        if (suggested == SYNC_ACTION_CONFLICT) {
            if (kDown & KEY_R) return SYNC_ACTION_UPLOAD;
            if (kDown & KEY_L) return SYNC_ACTION_DOWNLOAD;
            if (kDown & KEY_B) return SYNC_ACTION_UP_TO_DATE;
        } else if (suggested == SYNC_ACTION_UP_TO_DATE) {
            if (kDown & KEY_A) return SYNC_ACTION_UP_TO_DATE;
            if (kDown & KEY_B) return SYNC_ACTION_UP_TO_DATE;
        } else {
            if (kDown & KEY_A) return suggested;
            if (kDown & KEY_B) return SYNC_ACTION_UP_TO_DATE;
        }

        gfxFlushBuffers();
        gfxSwapBuffers();
        gspWaitForVBlank();
    }

    return SYNC_ACTION_UP_TO_DATE;
}

#include "config.h"

// Draw config editor menu
static void draw_config_menu(const AppConfig *config, int selected) {
    consoleSelect(&top_screen);

    int row = 1;

    printf("\x1b[%d;1H\x1b[36m%-*s\x1b[0m", row++, TOP_COLS, "--- Configuration ---");
    printf("\x1b[%d;1H%-*s", row++, TOP_COLS, "");

    // Menu items
    const char *items[] = {
        "Server URL",
        "API Key",
        "NDS ROM Directory",
        "Rescan Titles",
        "Check for Updates",
        "Save & Exit",
        "Cancel"
    };
    const int item_count = 7;

    for (int i = 0; i < item_count; i++) {
        const char *cursor = (i == selected) ? ">" : " ";
        const char *color = (i == selected) ? "\x1b[33m" : "\x1b[0m";

        char line[TOP_COLS + 1];
        snprintf(line, sizeof(line), "%s %s", cursor, items[i]);
        printf("\x1b[%d;1H%s%-*s\x1b[0m", row++, color, TOP_COLS, line);

        // Show current value for editable items
        if (i == 0) {
            char val[TOP_COLS + 1];
            snprintf(val, sizeof(val), "   %.44s", config->server_url);
            printf("\x1b[%d;1H\x1b[90m%-*s\x1b[0m", row++, TOP_COLS, val);
        } else if (i == 1) {
            char val[TOP_COLS + 1];
            int len = strlen(config->api_key);
            if (len > 4) {
                snprintf(val, sizeof(val), "   %.4s****", config->api_key);
            } else {
                snprintf(val, sizeof(val), "   (not set)");
            }
            printf("\x1b[%d;1H\x1b[90m%-*s\x1b[0m", row++, TOP_COLS, val);
        } else if (i == 2) {
            char val[TOP_COLS + 1];
            if (config->nds_dir[0]) {
                snprintf(val, sizeof(val), "   %.44s", config->nds_dir);
            } else {
                snprintf(val, sizeof(val), "   (not set)");
            }
            printf("\x1b[%d;1H\x1b[90m%-*s\x1b[0m", row++, TOP_COLS, val);
        }
        printf("\x1b[%d;1H%-*s", row++, TOP_COLS, "");
    }

    printf("\x1b[%d;1H%-*s", row++, TOP_COLS, "");

    char cid[TOP_COLS + 1];
    snprintf(cid, sizeof(cid), "Console ID: %s", config->console_id);
    printf("\x1b[%d;1H\x1b[90m%-*s\x1b[0m", row++, TOP_COLS, cid);

    // Blank remaining lines
    while (row < TOP_ROWS) {
        printf("\x1b[%d;1H%-*s", row++, TOP_COLS, "");
    }

    // Footer
    printf("\x1b[%d;1H\x1b[90m%-*s\x1b[0m", TOP_ROWS, TOP_COLS, " A: Select | D-Pad: Navigate");
}

int ui_show_config_editor(AppConfig *config) {
    // Make a working copy
    AppConfig working;
    memcpy(&working, config, sizeof(AppConfig));

    int selected = 0;
    int result = CONFIG_RESULT_UNCHANGED;
    bool changed = false;
    const int item_count = 7;
    bool redraw = true;

    while (aptMainLoop()) {
        hidScanInput();
        u32 kDown = hidKeysDown();

        if (kDown & KEY_UP) {
            selected = (selected - 1 + item_count) % item_count;
            redraw = true;
        }
        if (kDown & KEY_DOWN) {
            selected = (selected + 1) % item_count;
            redraw = true;
        }
        if (kDown & KEY_B) {
            break;
        }
        if (kDown & KEY_A) {
            if (selected == 0) {
                if (config_edit_field("http://192.168.1.100:8000", working.server_url, MAX_URL_LEN))
                    changed = true;
                redraw = true;
            } else if (selected == 1) {
                if (config_edit_field("your-api-key", working.api_key, MAX_API_KEY_LEN))
                    changed = true;
                redraw = true;
            } else if (selected == 2) {
                if (config_edit_field("sdmc:/roms/nds", working.nds_dir, MAX_PATH_LEN))
                    changed = true;
                redraw = true;
            } else if (selected == 3) {
                result = CONFIG_RESULT_RESCAN;
                if (changed) {
                    memcpy(config, &working, sizeof(AppConfig));
                    config_save(config);
                }
                break;
            } else if (selected == 4) {
                result = CONFIG_RESULT_UPDATE;
                if (changed) {
                    memcpy(config, &working, sizeof(AppConfig));
                    config_save(config);
                }
                break;
            } else if (selected == 5) {
                if (changed) {
                    memcpy(config, &working, sizeof(AppConfig));
                    config_save(config);
                    result = CONFIG_RESULT_SAVED;
                }
                break;
            } else if (selected == 6) {
                break;
            }
        }

        if (redraw) {
            // Draw to both buffers to prevent flicker
            for (int buf = 0; buf < 2; buf++) {
                draw_config_menu(&working, selected);
                gfxFlushBuffers();
                gfxSwapBuffers();
                gspWaitForVBlank();
            }
            redraw = false;
        } else {
            gspWaitForVBlank();
        }
    }

    return result;
}

char *ui_show_history(const TitleInfo *title, HistoryVersion *versions, int version_count) {
    if (version_count == 0) {
        consoleSelect(&top_screen);
        consoleClear();
        printf("\x1b[1;1H\x1b[36m--- History ---\x1b[0m\n\n");
        printf("No previous versions found.\n\n");
        printf("Press B to go back\n");

        while (aptMainLoop()) {
            hidScanInput();
            u32 kDown = hidKeysDown();
            if (kDown & KEY_B) break;
            gfxFlushBuffers();
            gfxSwapBuffers();
            gspWaitForVBlank();
        }
        return NULL;
    }

    int selected = 0;
    int scroll_offset = 0;
    #define HISTORY_VISIBLE 20

    while (aptMainLoop()) {
        consoleSelect(&top_screen);
        consoleClear();

        printf("\x1b[1;1H\x1b[36m--- History: %.35s ---\x1b[0m\n\n", title->name);

        // Scroll handling
        if (selected < scroll_offset) scroll_offset = selected;
        if (selected >= scroll_offset + HISTORY_VISIBLE) scroll_offset = selected - HISTORY_VISIBLE + 1;

        int row = 3;
        for (int i = scroll_offset; i < version_count && row < 3 + HISTORY_VISIBLE; i++) {
            char cursor = (i == selected) ? '>' : ' ';
            char size_str[16];
            if (versions[i].size >= 1024 * 1024) {
                snprintf(size_str, sizeof(size_str), "%.1fMB", versions[i].size / (1024.0 * 1024.0));
            } else if (versions[i].size >= 1024) {
                snprintf(size_str, sizeof(size_str), "%.1fKB", versions[i].size / 1024.0);
            } else {
                snprintf(size_str, sizeof(size_str), "%luB", (unsigned long)versions[i].size);
            }

            // Format timestamp - extract date and time
            char date_str[32] = "";
            if (strlen(versions[i].timestamp) >= 19) {
                snprintf(date_str, sizeof(date_str), "%.10s %.8s", versions[i].timestamp, versions[i].timestamp + 11);
            }

            printf("\x1b[%d;1H%c %-10s %s (%d files)\n", row++, cursor, size_str, date_str, versions[i].file_count);
        }

        // Footer
        printf("\x1b[%d;1H\x1b[90m%d version(s) | A:Download B:Cancel\x1b[0m", TOP_ROWS, version_count);

        gfxFlushBuffers();
        gfxSwapBuffers();
        gspWaitForVBlank();

        hidScanInput();
        u32 kDown = hidKeysDown();

        if (kDown & KEY_UP && version_count > 0) {
            selected = (selected - 1 + version_count) % version_count;
        }
        if (kDown & KEY_DOWN && version_count > 0) {
            selected = (selected + 1) % version_count;
        }
        if (kDown & KEY_LEFT) {
            selected -= HISTORY_VISIBLE;
            if (selected < 0) selected = 0;
        }
        if (kDown & KEY_RIGHT) {
            selected += HISTORY_VISIBLE;
            if (selected >= version_count) selected = version_count - 1;
        }
        if (kDown & KEY_B) {
            return NULL;
        }
        if (kDown & KEY_A) {
            // Copy the selected timestamp
            char *result = (char *)malloc(32);
            if (result) {
                strncpy(result, versions[selected].timestamp, 31);
                result[31] = '\0';
            }
            return result;
        }
    }

    return NULL;
}
