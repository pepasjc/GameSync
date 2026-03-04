/*
 * Vita Save Sync - Main
 *
 * Syncs PS Vita and PSP-emulated saves with the Save Sync server over WiFi.
 * Uses the 3DSS v3 bundle format (ASCII title_id for product codes).
 *
 * Build requirements:
 *   - VitaSDK toolchain (https://vitasdk.org/)
 *   - zlib (psp2-zlib, available in vitasdk)
 *
 * Build:
 *   mkdir build && cd build
 *   cmake .. && make
 *
 * Install:
 *   The vitasync.vpk can be installed via VitaShell.
 *
 * Config file (created by user):
 *   ux0:data/vitasync/config.txt
 *   server_url=http://192.168.1.100:8000
 *   api_key=your-secret-key
 *   scan_vita=1
 *   scan_psp_emu=1
 *
 * WiFi: the Vita OS manages WiFi. Connect via Settings > Network > WiFi
 * before launching this app.
 */

#include <string.h>
#include <stdio.h>
#include <psp2/kernel/processmgr.h>
#include <psp2/ctrl.h>

#include "common.h"
#include "config.h"
#include "saves.h"
#include "network.h"
#include "sync.h"
#include "ui.h"

#define LIST_VISIBLE 20

static SyncState g_state;
static int g_selected = 0;
static int g_scroll   = 0;

static void update_scroll(void) {
    if (g_selected < g_scroll)
        g_scroll = g_selected;
    if (g_selected >= g_scroll + LIST_VISIBLE)
        g_scroll = g_selected - LIST_VISIBLE + 1;
}

int main(void) {
    ui_init();

    memset(&g_state, 0, sizeof(SyncState));
    g_state.scan_vita_saves    = true;
    g_state.scan_psp_emu_saves = true;

    /* Load config */
    char err_buf[512];
    ui_clear();
    ui_status("Loading config...");

    if (!config_load(&g_state, err_buf, sizeof(err_buf))) {
        ui_message("Config error:\n\n%s\n\nEdit %s\n\nFormat:\n"
                   "server_url=http://host:8000\napi_key=key",
                   err_buf, CONFIG_PATH);
        sceKernelExitProcess(0);
        return 0;
    }

    config_load_console_id(&g_state);

    /* Initialize network */
    ui_clear();
    ui_status("Initializing network...");
    bool has_wifi = false;

    if (network_init() == 0) {
        ui_status("Checking WiFi connection...");
        if (network_connect() == 0) {
            ui_status("WiFi connected. Checking server...");
            has_wifi = network_check_server(&g_state);
            g_state.network_connected = has_wifi;
            if (!has_wifi) {
                ui_message("Cannot reach server at:\n%s\n\n"
                           "Check server_url in config.txt.\n\nContinuing offline.",
                           g_state.server_url);
            }
        } else {
            ui_message("WiFi not connected.\n\n"
                       "Go to Settings > Network > WiFi\nand connect before launching.\n\n"
                       "Continuing offline.");
        }
    } else {
        ui_message("Network init failed.\nContinuing offline.");
    }

    /* Scan saves */
    ui_clear();
    ui_status("Scanning saves...");
    saves_scan(&g_state);

    char scan_msg[256];
    snprintf(scan_msg, sizeof(scan_msg), "Found %d save(s).\n\nPress X to continue.",
             g_state.num_titles);
    ui_message(scan_msg);

    if (g_state.num_titles == 0) {
        ui_message("No saves found.\n\n"
                   "Vita saves: ux0:user/00/savedata/\n"
                   "PSP saves:  ux0:pspemu/PSP/SAVEDATA/\n\n"
                   "Check scan_vita and scan_psp_emu in config.txt.");
        network_cleanup();
        sceKernelExitProcess(0);
        return 0;
    }

    /* Main loop */
    SceCtrlData pad;
    bool redraw = true;
    uint32_t prev_buttons = 0;

    while (1) {
        sceCtrlReadBufferPositive2(0, &pad, 1);
        uint32_t just = pad.buttons & ~prev_buttons;
        prev_buttons = pad.buttons;

        /* Navigate list */
        if (just & SCE_CTRL_DOWN) {
            g_selected = (g_selected + 1) % g_state.num_titles;
            update_scroll();
            redraw = true;
        }
        if (just & SCE_CTRL_UP) {
            g_selected = (g_selected - 1 + g_state.num_titles) % g_state.num_titles;
            update_scroll();
            redraw = true;
        }
        if (just & SCE_CTRL_RIGHT) {
            g_selected = g_selected + LIST_VISIBLE;
            if (g_selected >= g_state.num_titles)
                g_selected = g_state.num_titles - 1;
            update_scroll();
            redraw = true;
        }
        if (just & SCE_CTRL_LEFT) {
            g_selected = g_selected - LIST_VISIBLE;
            if (g_selected < 0) g_selected = 0;
            update_scroll();
            redraw = true;
        }

        /* X: smart sync */
        if ((just & SCE_CTRL_CROSS) && has_wifi) {
            TitleInfo *title = &g_state.titles[g_selected];
            ui_clear();
            ui_status("Analyzing %s...", title->game_id);

            SyncAction action = sync_decide(&g_state, g_selected);

            char server_hash[65] = "";
            uint32_t server_size = 0;
            network_get_save_info(&g_state, title->game_id, server_hash, &server_size);

            if (ui_confirm(title, action, server_hash, server_size)) {
                ui_clear();
                ui_status("%s %s...",
                    action == SYNC_UPLOAD ? "Uploading" : "Downloading",
                    title->game_id);
                int r = sync_execute(&g_state, g_selected, action);
                if (r == 0)
                    ui_message("Done!");
                else
                    ui_message("Failed! (code %d)", r);
            }
            redraw = true;
        }

        /* Square: manual upload */
        if ((just & SCE_CTRL_SQUARE) && has_wifi) {
            TitleInfo *title = &g_state.titles[g_selected];
            char server_hash[65] = "";
            uint32_t server_size = 0;
            network_get_save_info(&g_state, title->game_id, server_hash, &server_size);

            if (ui_confirm(title, SYNC_UPLOAD, server_hash, server_size)) {
                ui_clear();
                ui_status("Uploading %s...", title->game_id);
                int r = sync_execute(&g_state, g_selected, SYNC_UPLOAD);
                if (r == 0) ui_message("Upload OK!");
                else        ui_message("Upload failed! (code %d)", r);
            }
            redraw = true;
        }

        /* Triangle: manual download */
        if ((just & SCE_CTRL_TRIANGLE) && has_wifi) {
            TitleInfo *title = &g_state.titles[g_selected];
            char server_hash[65] = "";
            uint32_t server_size = 0;
            network_get_save_info(&g_state, title->game_id, server_hash, &server_size);

            if (ui_confirm(title, SYNC_DOWNLOAD, server_hash, server_size)) {
                ui_clear();
                ui_status("Downloading %s...", title->game_id);
                int r = sync_execute(&g_state, g_selected, SYNC_DOWNLOAD);
                if (r == 0) ui_message("Download OK!");
                else        ui_message("Download failed! (code %d)", r);
            }
            redraw = true;
        }

        /* Select: scan all and show summary */
        if ((just & SCE_CTRL_SELECT) && has_wifi) {
            ui_clear();
            ui_status("Scanning all saves...");
            SyncSummary summary;
            sync_scan_all(&g_state, &summary);
            ui_message("Scan complete:\n\n"
                       "Up to date:    %d\n"
                       "Need upload:   %d\n"
                       "Need download: %d\n"
                       "Conflicts:     %d\n"
                       "Failed:        %d\n\n"
                       "Press X to continue.",
                       summary.up_to_date, summary.uploaded,
                       summary.downloaded, summary.conflicts, summary.failed);
            redraw = true;
        }

        /* Start: exit */
        if (just & SCE_CTRL_START) {
            network_cleanup();
            sceKernelExitProcess(0);
            return 0;
        }

        if (redraw) {
            ui_clear();
            ui_draw_list(&g_state, g_selected, g_scroll);
            redraw = false;
        }

        sceKernelDelayThread(16000);   /* ~60fps */
    }

    network_cleanup();
    return 0;
}
