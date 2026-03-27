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
#include <stdlib.h>
#include <psp2/kernel/processmgr.h>
#include <psp2/ctrl.h>

#include "common.h"
#include "config.h"
#include "saves.h"
#include "network.h"
#include "sync.h"
#include "ui.h"

#define LIST_VISIBLE 20

/* Wait until all buttons are released, then clear prev_buttons.
 * Call this after any blocking UI call (ui_message / ui_confirm) that
 * returns to the main loop, so the button that dismissed the dialog
 * doesn't fire as a 'just pressed' event on the very next frame. */
static void sync_progress(const char *msg) { ui_status("%s", msg); }

static int title_compare(const void *a, const void *b) {
    const TitleInfo *ta = (const TitleInfo *)a;
    const TitleInfo *tb = (const TitleInfo *)b;
    /* Vita native saves before PSP emu saves */
    if (ta->platform != tb->platform)
        return (ta->platform == PLATFORM_VITA) ? -1 : 1;
    return strcasecmp(ta->name, tb->name);
}

static uint32_t drain_buttons(void) {
    SceCtrlData pad;
    do {
        sceCtrlReadBufferPositive2(0, &pad, 1);
        sceKernelDelayThread(16000);
    } while (pad.buttons != 0);
    return 0;
}

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
        ui_term();
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

    if (has_wifi) {
        ui_status("Checking server saves...");
        network_merge_server_titles(&g_state);
    }

    if (has_wifi && g_state.num_titles > 0) {
        ui_status("Fetching game names...");
        network_fetch_names(&g_state);
    }

    if (g_state.num_titles > 1)
        qsort(g_state.titles, g_state.num_titles, sizeof(TitleInfo), title_compare);

    char scan_msg[256];
    snprintf(scan_msg, sizeof(scan_msg), "Found %d save(s).\n\nPress X to continue.",
             g_state.num_titles);
    ui_message(scan_msg);

    if (g_state.num_titles == 0) {
        ui_message("No saves found locally or on the server.\n\n"
                   "Vita saves: ux0:user/00/savedata/\n"
                   "PSP saves:  ux0:pspemu/PSP/SAVEDATA/\n\n"
                   "Check scan_vita and scan_psp_emu in config.txt.\n\n"
                   "Diagnostic log:\n"
                   "ux0:data/vitasync/diag.txt\n"
                   "(read with VitaShell)");
        network_cleanup();
        ui_term();
        sceKernelExitProcess(0);
        return 0;
    }

    /* Main loop */
    SceCtrlData pad;
    bool redraw = true;
    uint32_t prev_buttons = drain_buttons();

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
            char server_last_sync[32] = "";
            network_get_save_info(&g_state, title->game_id, server_hash, &server_size, server_last_sync);

            if (ui_confirm(title, action, server_hash, server_size, server_last_sync)) {
                ui_clear();
                ui_status("%s %s...",
                    action == SYNC_UPLOAD ? "Uploading" : "Downloading",
                    title->game_id);
                int r = sync_execute(&g_state, g_selected, action);
                if (r == 0)
                    ui_message("Done!");
                else
                    ui_message("Failed! (code %d)\n\n"
                               "-2 = can't read save files\n"
                               "-3 = bundle format error\n"
                               "-4 = network/server error\n"
                               "-5 = can't write save files\n\n"
                               "See sync_diag.txt for details.", r);
            }
            prev_buttons = drain_buttons();
            redraw = true;
        }

        /* Square: manual upload */
        if ((just & SCE_CTRL_SQUARE) && has_wifi) {
            TitleInfo *title = &g_state.titles[g_selected];
            if (title->server_only) {
                ui_message("This save only exists on the server.\n\nDownload it first.");
                prev_buttons = drain_buttons();
                redraw = true;
                continue;
            }
            char server_hash[65] = "";
            uint32_t server_size = 0;
            char server_last_sync[32] = "";
            network_get_save_info(&g_state, title->game_id, server_hash, &server_size, server_last_sync);

            if (ui_confirm(title, SYNC_UPLOAD, server_hash, server_size, server_last_sync)) {
                ui_clear();
                ui_status("Uploading %s...", title->game_id);
                int r = sync_execute(&g_state, g_selected, SYNC_UPLOAD);
                if (r == 0) ui_message("Upload OK!");
                else        ui_message("Upload failed! (code %d)\n\n"
                                       "-2=read error  -3=bundle error\n"
                                       "-4=network/server  -5=write error\n\n"
                                       "See sync_diag.txt for details.", r);
            }
            prev_buttons = drain_buttons();
            redraw = true;
        }

        /* Triangle: manual download */
        if ((just & SCE_CTRL_TRIANGLE) && has_wifi) {
            TitleInfo *title = &g_state.titles[g_selected];
            char server_hash[65] = "";
            uint32_t server_size = 0;
            char server_last_sync[32] = "";
            network_get_save_info(&g_state, title->game_id, server_hash, &server_size, server_last_sync);

            if (ui_confirm(title, SYNC_DOWNLOAD, server_hash, server_size, server_last_sync)) {
                ui_clear();
                ui_status("Downloading %s...", title->game_id);
                int r = sync_execute(&g_state, g_selected, SYNC_DOWNLOAD);
                if (r == 0) ui_message("Download OK!");
                else        ui_message("Download failed! (code %d)\n\n"
                                       "-2=read error  -3=bundle error\n"
                                       "-4=network/server  -5=write error\n\n"
                                       "See sync_diag.txt for details.", r);
            }
            prev_buttons = drain_buttons();
            redraw = true;
        }

        /* Select: auto sync all saves */
        if ((just & SCE_CTRL_SELECT) && has_wifi) {
            ui_clear();
            SyncSummary summary;
            sync_auto_all(&g_state, &summary, sync_progress);
            ui_message("Auto sync complete:\n\n"
                       "Uploaded:   %d\n"
                       "Downloaded: %d\n"
                       "Up to date: %d\n"
                       "Conflicts:  %d\n"
                       "Failed:     %d\n\n"
                       "Press X to continue.",
                       summary.uploaded, summary.downloaded,
                       summary.up_to_date, summary.conflicts, summary.failed);
            prev_buttons = drain_buttons();
            redraw = true;
        }

        /* Start: exit */
        if (just & SCE_CTRL_START) {
            network_cleanup();
            ui_term();
            sceKernelExitProcess(0);
            return 0;
        }

        if (redraw) {
            ui_draw_list(&g_state, g_selected, g_scroll);
            redraw = false;
        }

        sceKernelDelayThread(16000);   /* ~60fps */
    }

    network_cleanup();
    return 0;
}
