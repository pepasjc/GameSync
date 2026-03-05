/*
 * PSP Save Sync - Main
 *
 * Syncs PSP SAVEDATA saves with the Save Sync server over WiFi.
 * Uses the 3DSS v3 bundle format (string title_id for PSP product codes).
 *
 * Build requirements:
 *   - pspdev toolchain (https://github.com/pspdev/pspdev)
 *   - zlib port for PSP (psp-zlib)
 *
 * Install pspdev:
 *   https://github.com/pspdev/pspdev#installation
 *
 * Build:
 *   export PSPDEV=/usr/local/pspdev
 *   export PSPSDK=$PSPDEV/psp/sdk
 *   export PATH=$PATH:$PSPDEV/bin
 *   make
 *
 * The EBOOT.PBP goes into ms0:/PSP/GAME/pspsync/EBOOT.PBP
 * Config file:   ms0:/PSP/GAME/pspsync/config.txt
 * Sync state:    ms0:/PSP/GAME/pspsync/state.dat
 */

#include <pspkernel.h>
#include <pspdebug.h>
#include <pspctrl.h>
#include <psppower.h>
#include <stdio.h>
#include <string.h>

#include "common.h"
#include "config.h"
#include "saves.h"
#include "network.h"
#include "sync.h"
#include "ui.h"

PSP_MODULE_INFO("PSP Save Sync", 0, 1, 0);
PSP_MAIN_THREAD_ATTR(THREAD_ATTR_USER | THREAD_ATTR_VFPU);
PSP_HEAP_SIZE_KB(4096);

/* Kernel callback for EXIT button */
static int exit_callback(int arg1, int arg2, void *common) {
    (void)arg1; (void)arg2; (void)common;
    sceKernelExitGame();
    return 0;
}

static int callback_thread(SceSize args, void *argp) {
    (void)args; (void)argp;
    int cbid = sceKernelCreateCallback("Exit Callback", exit_callback, NULL);
    sceKernelRegisterExitCallback(cbid);
    sceKernelSleepThreadCB();
    return 0;
}

static void setup_callbacks(void) {
    int thid = sceKernelCreateThread("update_thread", callback_thread,
                                     0x11, 0xFA0, 0, NULL);
    if (thid >= 0)
        sceKernelStartThread(thid, 0, NULL);
}

#define LIST_VISIBLE 20

static SyncState g_state;
static int g_selected = 0;
static int g_scroll = 0;

static void update_scroll(void) {
    if (g_selected < g_scroll)
        g_scroll = g_selected;
    if (g_selected >= g_scroll + LIST_VISIBLE)
        g_scroll = g_selected - LIST_VISIBLE + 1;
}

int main(int argc, char *argv[]) {
    (void)argc; (void)argv;

    setup_callbacks();
    ui_init();

    /* Power management: prevent sleep during sync */
    // scePowerSetClockFrequency(333, 333, 166); // Deprecated

    memset(&g_state, 0, sizeof(SyncState));
    g_state.wifi_ap_index = 0;

    /* Load config */
    char err_buf[256];
    ui_clear();
    ui_status("Loading config...");

    if (!config_load(&g_state, err_buf, sizeof(err_buf))) {
        ui_clear();
        pspDebugScreenPrintf("Config error:\n%s\n\n", err_buf);
        pspDebugScreenPrintf("Create config.txt in:\n%s\n\n", SYNC_STATE_DIR);
        pspDebugScreenPrintf("Format:\n");
        pspDebugScreenPrintf("server_url=http://192.168.1.100:8000\n");
        pspDebugScreenPrintf("api_key=your-key\n");
        pspDebugScreenPrintf("wifi_ap=0\n\n");
        pspDebugScreenPrintf("Press HOME to exit\n");
        sceKernelSleepThread();
        return 0;
    }

    config_load_console_id(&g_state);

    /* Initialize network */
    ui_clear();
    ui_status("Initializing network...");
    int net_init = network_init();
    bool has_wifi = false;

    if (net_init == 0) {
        ui_status("Connecting to WiFi (AP %d)...", g_state.wifi_ap_index);
        if (network_connect_ap(g_state.wifi_ap_index) == 0) {
            ui_status("WiFi connected. Checking server...");
            has_wifi = network_check_server(&g_state);
            if (!has_wifi) {
                ui_message("Cannot reach server at:\n%s\n\nContinuing offline.",
                           g_state.server_url);
            }
        } else {
            ui_message("WiFi connection failed.\nCheck AP index in config.txt.\n\nContinuing offline.");
        }
    } else {
        /* Pause so pspDebugScreenPrintf output from network_init() stays visible */
        pspDebugScreenPrintf("\n--- network_init returned 0x%08X ---\nPress X to continue\n", net_init);
        {
            SceCtrlData pad;
            uint32_t prev = 0;
            while (1) {
                sceCtrlReadBufferPositive(&pad, 1);
                uint32_t just = pad.Buttons & ~prev;
                prev = pad.Buttons;
                if (just & PSP_CTRL_CROSS) break;
                sceKernelDelayThread(16000);
            }
        }
        ui_message("Network init failed\n0x%08X (%d)\nContinuing offline.", net_init, net_init);
    }

    /* Scan saves */
    ui_clear();
    ui_status("Scanning PSP/SAVEDATA...");
    saves_scan(&g_state);

    if (has_wifi && g_state.num_titles > 0) {
        ui_status("Fetching game names...");
        network_fetch_names(&g_state);
    }

    ui_message("Found %d save(s).\n\nPress X to continue.", g_state.num_titles);

    if (g_state.num_titles == 0) {
        ui_clear();
        pspDebugScreenPrintf("No PSP saves found in:\n%s\n\n", SAVEDATA_PATH);
        pspDebugScreenPrintf("Press HOME to exit\n");
        sceKernelSleepThread();
        return 0;
    }

    /* Main loop */
    SceCtrlData pad;
    bool redraw = true;
    uint32_t prev_buttons = 0;

    /* Drain any buttons held during startup/scan before entering the loop. */
    do { sceCtrlReadBufferPositive(&pad, 1); sceKernelDelayThread(16000); }
    while (pad.Buttons != 0);

    while (1) {
        sceCtrlReadBufferPositive(&pad, 1);
        uint32_t pressed = pad.Buttons;
        uint32_t just_pressed = pressed & ~prev_buttons;
        prev_buttons = pressed;

        /* Navigate list */
        if (just_pressed & PSP_CTRL_DOWN) {
            g_selected = (g_selected + 1) % g_state.num_titles;
            update_scroll();
            redraw = true;
        }
        if (just_pressed & PSP_CTRL_UP) {
            g_selected = (g_selected - 1 + g_state.num_titles) % g_state.num_titles;
            update_scroll();
            redraw = true;
        }
        if (just_pressed & PSP_CTRL_RIGHT) {
            g_selected = g_selected + LIST_VISIBLE;
            if (g_selected >= g_state.num_titles)
                g_selected = g_state.num_titles - 1;
            update_scroll();
            redraw = true;
        }
        if (just_pressed & PSP_CTRL_LEFT) {
            g_selected = g_selected - LIST_VISIBLE;
            if (g_selected < 0) g_selected = 0;
            update_scroll();
            redraw = true;
        }

        /* X button: smart sync */
        if (just_pressed & PSP_CTRL_CROSS && has_wifi) {
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
                    ui_message("Success!");
                else
                    ui_message("Failed! (error %d)", r);
            }
            prev_buttons = 0;
            redraw = true;
        }

        /* Square button: manual upload */
        if (just_pressed & PSP_CTRL_SQUARE && has_wifi) {
            TitleInfo *title = &g_state.titles[g_selected];
            char server_hash[65] = "";
            uint32_t server_size = 0;
            network_get_save_info(&g_state, title->game_id, server_hash, &server_size);

            if (ui_confirm(title, SYNC_UPLOAD, server_hash, server_size)) {
                ui_clear();
                ui_status("Uploading %s...", title->game_id);
                int r = sync_execute(&g_state, g_selected, SYNC_UPLOAD);
                if (r == 0)
                    ui_message("Upload OK!");
                else
                    ui_message("Upload failed! (error %d)", r);
            }
            prev_buttons = 0;
            redraw = true;
        }

        /* Triangle button: manual download */
        if (just_pressed & PSP_CTRL_TRIANGLE && has_wifi) {
            TitleInfo *title = &g_state.titles[g_selected];
            char server_hash[65] = "";
            uint32_t server_size = 0;
            network_get_save_info(&g_state, title->game_id, server_hash, &server_size);

            if (ui_confirm(title, SYNC_DOWNLOAD, server_hash, server_size)) {
                ui_clear();
                ui_status("Downloading %s...", title->game_id);
                int r = sync_execute(&g_state, g_selected, SYNC_DOWNLOAD);
                if (r == 0)
                    ui_message("Download OK!");
                else
                    ui_message("Download failed! (error %d)", r);
            }
            prev_buttons = 0;
            redraw = true;
        }

        /* Select: scan all saves */
        if (just_pressed & PSP_CTRL_SELECT && has_wifi) {
            ui_clear();
            ui_status("Scanning all saves...");
            SyncSummary summary;
            sync_scan_all(&g_state, &summary);
            ui_message("Scan complete:\n"
                       "Up to date: %d\n"
                       "Need upload: %d\n"
                       "Need download: %d\n"
                       "Conflicts: %d\n"
                       "Failed: %d\n\n"
                       "Press X to continue.",
                       summary.up_to_date, summary.uploaded,
                       summary.downloaded, summary.conflicts, summary.failed);
            prev_buttons = 0;
            redraw = true;
        }

        if (redraw) {
            ui_clear();
            ui_draw_list(&g_state, g_selected, g_scroll);
            redraw = false;
        }

        sceKernelDelayThread(16000);  /* ~60fps */
    }

    network_disconnect();
    return 0;
}
