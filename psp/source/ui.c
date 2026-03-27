/*
 * PSP Save Sync - UI (using pspDebugScreen)
 *
 * Simple text-based UI using PSP's debug screen.
 * A proper graphical UI using gu/libpspgum could be added later.
 */

#include <stdio.h>
#include <stdarg.h>
#include <string.h>

#include <pspdebug.h>
#include <pspctrl.h>
#include <pspkernel.h>

#include "ui.h"

/* PSP screen is 480x272, debug screen is 60 cols x ~34 rows at default font */
#define SCREEN_COLS     60
#define LIST_START_ROW  3
#define STATUS_ROW      33

void ui_init(void) {
    pspDebugScreenInit();
    pspDebugScreenClear();
}

void ui_clear(void) {
    pspDebugScreenClear();
}

void ui_status(const char *fmt, ...) {
    char buf[256];
    va_list args;
    va_start(args, fmt);
    vsnprintf(buf, sizeof(buf), fmt, args);
    va_end(args);
    pspDebugScreenSetXY(0, STATUS_ROW);
    pspDebugScreenPrintf("%s\n", buf);
}

/* Wait until no buttons are held, then return 0.
 * Call this before starting any new input loop to guarantee a clean state. */
static void drain_buttons(void) {
    SceCtrlData pad;
    do {
        sceCtrlReadBufferPositive(&pad, 1);
        sceKernelDelayThread(16000);
    } while (pad.Buttons != 0);
}

void ui_message(const char *fmt, ...) {
    char buf[512];
    va_list args;
    va_start(args, fmt);
    vsnprintf(buf, sizeof(buf), fmt, args);
    va_end(args);
    pspDebugScreenClear();
    pspDebugScreenSetXY(0, 1);
    pspDebugScreenPrintf("%s", buf);

    pspDebugScreenPrintf("\n\nPress X to continue\n");

    drain_buttons();

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

void ui_draw_list(const SyncState *state, int selected, int scroll) {
    pspDebugScreenSetXY(0, 0);
    pspDebugScreenPrintf("=== PSP Save Sync v%s ===", APP_VERSION);

    pspDebugScreenSetXY(0, 1);
    pspDebugScreenPrintf("%d saves | X:Sync Sq:Upload Tri:Download Sel:ScanAll",
                         state->num_titles);

    pspDebugScreenSetXY(0, 2);
    pspDebugScreenPrintf("-----------------------------------------------");

    int visible = 28;  /* rows available for list */
    int end = scroll + visible;
    if (end > state->num_titles) end = state->num_titles;

    for (int i = scroll; i < end; i++) {
        pspDebugScreenSetXY(0, LIST_START_ROW + (i - scroll));
        const TitleInfo *t = &state->titles[i];

        const char *cursor = (i == selected) ? ">" : " ";
        const char *plat = t->is_psx ? "PS1" : "PSP";
        const char *display = (t->name[0] && strcmp(t->name, t->game_id) != 0)
                              ? t->name : t->game_id;
        char line[56];
        snprintf(line, sizeof(line), "%s %-4s %s", cursor, plat, display);
        pspDebugScreenPrintf("%-55s", line);
    }

    /* Controls reminder */
    pspDebugScreenSetXY(0, LIST_START_ROW + visible + 1);
    pspDebugScreenPrintf("Up/Down: navigate | L/R: page | HOME: exit");
}

bool ui_confirm(const TitleInfo *title, SyncAction action,
                const char *server_hash, uint32_t server_size,
                const char *server_last_sync) {
    pspDebugScreenClear();
    pspDebugScreenSetXY(0, 1);

    const char *action_str =
        action == SYNC_UPLOAD   ? "UPLOAD to server" :
        action == SYNC_DOWNLOAD ? "DOWNLOAD from server" :
        action == SYNC_CONFLICT ? "CONFLICT - manual decision needed" :
        "UP TO DATE";

    pspDebugScreenPrintf("Game:   %s (%s)\n\n", title->name, title->game_id);
    pspDebugScreenPrintf("Action: %s\n\n", action_str);
    pspDebugScreenPrintf("Local:  %u bytes\n", title->total_size);
    if (server_hash && server_hash[0]) {
        pspDebugScreenPrintf("Server: %u bytes\n", server_size);
        if (server_last_sync && server_last_sync[0]) {
            /* Format "2024-01-15T14:30:00..." -> "2024-01-15 14:30" */
            char date_str[20] = "";
            if (strlen(server_last_sync) >= 16 && server_last_sync[10] == 'T')
                snprintf(date_str, sizeof(date_str), "%.10s %.5s",
                         server_last_sync, server_last_sync + 11);
            else
                snprintf(date_str, sizeof(date_str), "%.16s", server_last_sync);
            pspDebugScreenPrintf("Date:   %s\n", date_str);
        }
    } else {
        pspDebugScreenPrintf("Server: (no save)\n");
    }

    /* Always drain before starting input so a held button from the previous
     * screen doesn't immediately trigger a choice here. */
    drain_buttons();

    if (action == SYNC_UP_TO_DATE) {
        pspDebugScreenPrintf("\nAlready up to date. Press X.\n");
        SceCtrlData pad;
        uint32_t prev = 0;
        while (1) {
            sceCtrlReadBufferPositive(&pad, 1);
            uint32_t just = pad.Buttons & ~prev;
            prev = pad.Buttons;
            if (just & (PSP_CTRL_CROSS | PSP_CTRL_CIRCLE)) break;
            sceKernelDelayThread(16000);
        }
        return false;
    }

    pspDebugScreenPrintf("\nX: Confirm | O: Cancel\n");

    SceCtrlData pad;
    uint32_t prev = 0;
    while (1) {
        sceCtrlReadBufferPositive(&pad, 1);
        uint32_t just = pad.Buttons & ~prev;
        prev = pad.Buttons;
        if (just & PSP_CTRL_CROSS)  return true;
        if (just & PSP_CTRL_CIRCLE) return false;
        sceKernelDelayThread(16000);
    }
}

void ui_draw_config(const SyncState *state) {
    pspDebugScreenSetXY(0, 0);
    pspDebugScreenPrintf("=== Config ===\n\n");
    pspDebugScreenPrintf("Server:     %s\n", state->server_url);
    pspDebugScreenPrintf("API Key:    %s\n",
        state->api_key[0] ? "(set)" : "(not set)");
    pspDebugScreenPrintf("WiFi AP:    %d\n", state->wifi_ap_index);
    pspDebugScreenPrintf("Console ID: %s\n", state->console_id);
    pspDebugScreenPrintf("WiFi:       %s\n",
        state->wifi_connected ? "Connected" : "Not connected");
    pspDebugScreenPrintf("\nEdit config.txt at:\n%s\n", CONFIG_PATH);
}
