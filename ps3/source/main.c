/*
 * PS3 Save Sync - Main
 *
 * Syncs PS3 and PS1 saves with the Save Sync server over WiFi.
 *
 * Controller layout:
 *   Up / Down        Navigate save list
 *   Left / Right     Page up / down
 *   Cross   (X)      Smart sync (auto decide upload/download)
 *   Square  (□)      Force upload to server
 *   Triangle(△)      Force download from server
 *   Select           Auto-sync all saves
 *   Circle  (○)      Rescan + rehash saves
 *   Start            Exit
 */

#include "apollo.h"
#include "common.h"
#include "config.h"
#include "debug.h"
#include "hash.h"
#include "network.h"
#include "saves.h"
#include "state.h"
#include "sync.h"
#include "ui.h"

#include <SDL/SDL.h>
#include <io/pad.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

/* ---- Button bitmask IDs (MASK_ prefix avoids collision with padData fields) ---- */
#define MASK_UP       (1U << 0)
#define MASK_DOWN     (1U << 1)
#define MASK_LEFT     (1U << 2)
#define MASK_RIGHT    (1U << 3)
#define MASK_CROSS    (1U << 4)
#define MASK_SQUARE   (1U << 5)
#define MASK_TRIANGLE (1U << 6)
#define MASK_CIRCLE   (1U << 7)
#define MASK_SELECT   (1U << 8)
#define MASK_START    (1U << 9)
#define MASK_L1       (1U << 10)

/* MAX_PADS is already defined in <io/pad.h> as 127; we use a smaller cap */
#define PAD_COUNT    7
#define LIST_VISIBLE 35

static unsigned int read_buttons(void) {
    unsigned int btns = 0;
    padInfo padinfo;
    padData paddata;

    ioPadGetInfo(&padinfo);
    for (int i = 0; i < PAD_COUNT; i++) {
        if (!padinfo.status[i]) continue;
        ioPadGetData(i, &paddata);
        if (paddata.BTN_UP)       btns |= MASK_UP;
        if (paddata.BTN_DOWN)     btns |= MASK_DOWN;
        if (paddata.BTN_LEFT)     btns |= MASK_LEFT;
        if (paddata.BTN_RIGHT)    btns |= MASK_RIGHT;
        if (paddata.BTN_CROSS)    btns |= MASK_CROSS;
        if (paddata.BTN_SQUARE)   btns |= MASK_SQUARE;
        if (paddata.BTN_TRIANGLE) btns |= MASK_TRIANGLE;
        if (paddata.BTN_CIRCLE)   btns |= MASK_CIRCLE;
        if (paddata.BTN_SELECT)   btns |= MASK_SELECT;
        if (paddata.BTN_START)    btns |= MASK_START;
        if (paddata.BTN_L1)       btns |= MASK_L1;
        break;  /* first connected pad only */
    }
    return btns;
}

static void update_scroll(int selected, int *scroll, int count) {
    if (count <= 0) { *scroll = 0; return; }
    if (selected < *scroll) *scroll = selected;
    if (selected >= *scroll + LIST_VISIBLE) *scroll = selected - LIST_VISIBLE + 1;
}

static int g_visible[MAX_TITLES];
static int g_visible_count = 0;
static bool g_show_server_only = true;

static void rebuild_visible(const SyncState *state) {
    int i;
    g_visible_count = 0;
    for (i = 0; i < state->num_titles; i++) {
        if (!g_show_server_only && state->titles[i].server_only) continue;
        g_visible[g_visible_count++] = i;
    }
}

static void rescan(SyncState *state, char *status, size_t status_sz) {
    char ps3_root[PATH_LEN], vmc_root[PATH_LEN];
    apollo_get_ps3_savedata_root(state, ps3_root, sizeof(ps3_root));
    apollo_get_ps1_vmc_root(vmc_root, sizeof(vmc_root));
    saves_scan(state);
    /* Hash all titles (uses cache) */
    for (int i = 0; i < state->num_titles; i++)
        saves_calculate_hash(&state->titles[i]);
    snprintf(status, status_sz, "Scanned %d save(s).", state->num_titles);
}

static void sync_progress_cb(const char *msg) {
    ui_status("%s", msg);
}

int main(void) {
    SyncState state;
    char error_buf[512];
    char status_line[256];
    char savedata_root[PATH_LEN];
    char vmc_root[PATH_LEN];
    bool config_created = false;
    int selected = 0, scroll = 0;
    unsigned int prev_buttons = 0;
    bool redraw = true;

    memset(&state, 0, sizeof(state));
    debug_log_open();
    debug_log("ps3sync starting v%s", APP_VERSION);

    /* --- UI init --- */
    if (!ui_init(error_buf, sizeof(error_buf))) {
        debug_log("ui_init failed: %s", error_buf);
        debug_log_close();
        return 1;
    }

    /* --- Config --- */
    ui_status("Loading config...");
    if (!config_load(&state, &config_created, error_buf, sizeof(error_buf))) {
        debug_log("config error: %s", error_buf);
        ui_draw_message("Save Sync PS3", error_buf, "Press START to exit");
        ioPadInit(PAD_COUNT);
        while (1) {
            SDL_PumpEvents();
            if (read_buttons() & MASK_START) break;
            usleep(100000);
        }
        ioPadEnd();
        ui_shutdown();
        debug_log_close();
        return 1;
    }
    config_load_console_id(&state);
    debug_log("config ok  server=%s user=%s", state.server_url, state.ps3_user);

    /* --- Network --- */
    ui_status("Initializing network...");
    bool has_net = false;
    if (network_init() == 0) {
        ui_status("Checking server...");
        if (network_check_server(&state)) {
            state.network_connected = true;
            has_net = true;
            debug_log("server reachable");
        } else {
            debug_log("server unreachable");
            ui_message("Cannot reach server at:\n%s\n\n"
                       "Check server_url in config.txt.\n\n"
                       "Continuing offline.",
                       state.server_url);
        }
    } else {
        ui_message("Network init failed.\nContinuing offline.");
    }

    /* --- Scan saves --- */
    ui_status("Scanning saves...");
    apollo_get_ps3_savedata_root(&state, savedata_root, sizeof(savedata_root));
    apollo_get_ps1_vmc_root(vmc_root, sizeof(vmc_root));
    saves_scan(&state);
    for (int i = 0; i < state.num_titles; i++)
        saves_calculate_hash(&state.titles[i]);

    if (has_net) {
        ui_status("Checking server saves...");
        network_merge_server_titles(&state);
        ui_status("Fetching game names...");
        network_fetch_names(&state);
    }

    rebuild_visible(&state);
    snprintf(status_line, sizeof(status_line),
             "Found %d save(s). %s",
             state.num_titles,
             has_net ? "Server connected." : "Offline.");

    /* --- Input loop --- */
    ioPadInit(PAD_COUNT);

    while (1) {
        /* Pump SDL events every frame to keep display alive */
        SDL_PumpEvents();

        unsigned int btns = read_buttons();
        unsigned int just = btns & ~prev_buttons;
        prev_buttons = btns;

        /* Exit */
        if (just & MASK_START) {
            debug_log("exit via START");
            break;
        }

        /* Navigation */
        if ((just & MASK_DOWN) && g_visible_count > 0) {
            selected = (selected + 1) % g_visible_count;
            update_scroll(selected, &scroll, g_visible_count);
            redraw = true;
        }
        if ((just & MASK_UP) && g_visible_count > 0) {
            selected = (selected - 1 + g_visible_count) % g_visible_count;
            update_scroll(selected, &scroll, g_visible_count);
            redraw = true;
        }
        if ((just & MASK_RIGHT) && g_visible_count > 0) {
            selected += LIST_VISIBLE;
            if (selected >= g_visible_count) selected = g_visible_count - 1;
            update_scroll(selected, &scroll, g_visible_count);
            redraw = true;
        }
        if ((just & MASK_LEFT) && g_visible_count > 0) {
            selected -= LIST_VISIBLE;
            if (selected < 0) selected = 0;
            update_scroll(selected, &scroll, g_visible_count);
            redraw = true;
        }

        /* L1: toggle server-only filter */
        if (just & MASK_L1) {
            g_show_server_only = !g_show_server_only;
            rebuild_visible(&state);
            if (selected >= g_visible_count)
                selected = g_visible_count > 0 ? g_visible_count - 1 : 0;
            update_scroll(selected, &scroll, g_visible_count);
            redraw = true;
        }

        /* Cross (X): smart sync */
        if ((just & MASK_CROSS) && has_net && g_visible_count > 0) {
            TitleInfo *title = &state.titles[g_visible[selected]];
            ui_status("Analyzing %s...", title->game_code);

            SyncAction action = sync_decide(&state, g_visible[selected]);

            char server_hash[65] = "";
            uint32_t server_size = 0;
            char server_last_sync[32] = "";
            network_get_save_info(&state, title->game_code,
                                  server_hash, &server_size, server_last_sync);

            if (ui_confirm(title, action, server_hash, server_size, server_last_sync)) {
                ui_status("%s %s...",
                          action == SYNC_UPLOAD ? "Uploading" : "Downloading",
                          title->game_code);
                int r = sync_execute(&state, g_visible[selected], action);
                if (r == 0)
                    ui_message("Done! (%s)", title->game_code);
                else
                    ui_message("Failed! (code %d)\n\n"
                               "-2=read/hash error\n"
                               "-3=bundle error\n"
                               "-4=network/server error\n"
                               "-5=write error\n\n"
                               "See %s for details.", r, DEBUG_LOG_FILE);
            }
            redraw = true;
        }

        /* Square (□): force upload */
        if ((just & MASK_SQUARE) && has_net && g_visible_count > 0) {
            TitleInfo *title = &state.titles[g_visible[selected]];
            if (title->server_only) {
                ui_message("This save only exists on the server.\nDownload it first (Triangle).");
            } else {
                char server_hash[65] = "";
                uint32_t server_size = 0;
                char server_last_sync[32] = "";
                network_get_save_info(&state, title->game_code,
                                      server_hash, &server_size, server_last_sync);
                if (ui_confirm(title, SYNC_UPLOAD, server_hash, server_size, server_last_sync)) {
                    ui_status("Uploading %s...", title->game_code);
                    int r = sync_execute(&state, g_visible[selected], SYNC_UPLOAD);
                    if (r == 0) ui_message("Upload OK!");
                    else        ui_message("Upload failed! (code %d)", r);
                }
            }
            redraw = true;
        }

        /* Triangle (△): force download */
        if ((just & MASK_TRIANGLE) && has_net && g_visible_count > 0) {
            TitleInfo *title = &state.titles[g_visible[selected]];
            char server_hash[65] = "";
            uint32_t server_size = 0;
            char server_last_sync[32] = "";
            network_get_save_info(&state, title->game_code,
                                  server_hash, &server_size, server_last_sync);
            if (ui_confirm(title, SYNC_DOWNLOAD, server_hash, server_size, server_last_sync)) {
                ui_status("Downloading %s...", title->game_code);
                int r = sync_execute(&state, g_visible[selected], SYNC_DOWNLOAD);
                if (r == 0) ui_message("Download OK!");
                else        ui_message("Download failed! (code %d)", r);
            }
            redraw = true;
        }

        /* Select: auto-sync all */
        if ((just & MASK_SELECT) && has_net) {
            SyncSummary summary;
            sync_auto_all(&state, &summary, sync_progress_cb);
            ui_message("Auto-sync complete:\n\n"
                       "Uploaded:   %d\n"
                       "Downloaded: %d\n"
                       "Up to date: %d\n"
                       "Conflicts:  %d\n"
                       "Failed:     %d\n\n"
                       "Press Cross to continue.",
                       summary.uploaded, summary.downloaded,
                       summary.up_to_date, summary.conflicts, summary.failed);
            redraw = true;
        }

        /* Circle (○): rescan + rehash */
        if (just & MASK_CIRCLE) {
            ui_status("Rescanning saves...");
            rescan(&state, status_line, sizeof(status_line));
            if (has_net) {
                ui_status("Refreshing server list...");
                network_merge_server_titles(&state);
                network_fetch_names(&state);
            }
            rebuild_visible(&state);
            if (selected >= g_visible_count)
                selected = g_visible_count > 0 ? g_visible_count - 1 : 0;
            update_scroll(selected, &scroll, g_visible_count);
            redraw = true;
        }

        if (redraw) {
            ui_draw_list(&state, g_visible, g_visible_count, selected, scroll,
                         status_line, config_created, g_show_server_only);
            redraw = false;
        }

        usleep(16000);  /* ~60 fps */
    }

    ioPadEnd();
    network_cleanup();
    ui_shutdown();
    debug_log_close();
    return 0;
}
