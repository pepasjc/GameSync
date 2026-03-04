/*
 * Vita Save Sync - UI
 *
 * Uses the VitaSDK debugScreen library for text output.
 * This replaces the custom framebuffer/font renderer.
 *
 * Colors via ANSI escape codes:
 *   \e[38;2;R;G;Bm  = set foreground RGB
 *   \e[48;2;R;G;Bm  = set background RGB
 *   \e[0m           = reset colors
 *   \e[H            = cursor home
 *   \e[2J           = clear screen
 *   \e[row;colH     = set cursor position (1-based)
 */

#include <stdio.h>
#include <stdarg.h>
#include <string.h>

#include <psp2/ctrl.h>
#include <psp2/kernel/threadmgr.h>

#include "debugScreen.h"
#include "ui.h"

/* Foreground color presets (RGB) */
#define FG_WHITE    "\e[38;2;255;255;255m"
#define FG_GREEN    "\e[38;2;0;255;0m"
#define FG_YELLOW   "\e[38;2;255;255;0m"
#define FG_RESET    "\e[0m"
#define BG_BLACK    "\e[48;2;0;0;0m"
#define CLR_SCREEN  "\e[H\e[2J"
#define CLR_LINE    "\e[2K"

/* Screen is 960x544, default font is 8x8 → 120x68 chars, or 2x scaled → 60x34 */
#define STATUS_ROW  33   /* last usable row (0-based, 2x font) */
#define LIST_START  3

void ui_init(void) {
    sceCtrlSetSamplingMode(SCE_CTRL_MODE_DIGITAL);
    psvDebugScreenInit();
    /* Scale font 2x for readability */
    PsvDebugScreenFont *font = psvDebugScreenScaleFont2x(psvDebugScreenGetFont());
    if (font) psvDebugScreenSetFont(font);
    /* Clear to black */
    psvDebugScreenClear(0x000000);
}

void ui_term(void) {
    psvDebugScreenFinish();
}

void ui_clear(void) {
    psvDebugScreenClear(0x000000);
}

/* Move cursor to row,col (0-based) */
static void goto_rc(int row, int col) {
    psvDebugScreenPrintf("\e[%d;%dH", row + 1, col + 1);
}

void ui_status(const char *fmt, ...) {
    char buf[256];
    va_list args;
    va_start(args, fmt);
    vsnprintf(buf, sizeof(buf), fmt, args);
    va_end(args);

    goto_rc(STATUS_ROW, 0);
    psvDebugScreenPuts(CLR_LINE);
    psvDebugScreenPuts(FG_YELLOW);
    psvDebugScreenPuts(buf);
    psvDebugScreenPuts(FG_RESET);
}

void ui_message(const char *fmt, ...) {
    char buf[512];
    va_list args;
    va_start(args, fmt);
    vsnprintf(buf, sizeof(buf), fmt, args);
    va_end(args);

    psvDebugScreenPuts(CLR_SCREEN);
    goto_rc(2, 0);
    psvDebugScreenPuts(FG_WHITE);
    psvDebugScreenPuts(buf);
    psvDebugScreenPuts(FG_RESET);

    goto_rc(STATUS_ROW, 0);
    psvDebugScreenPuts(FG_GREEN "Press X to continue" FG_RESET);

    SceCtrlData pad;
    uint32_t prev = 0;
    while (1) {
        sceCtrlReadBufferPositive2(0, &pad, 1);
        uint32_t just = pad.buttons & ~prev;
        prev = pad.buttons;
        if (just & SCE_CTRL_CROSS) break;
        sceKernelDelayThread(16000);
    }
}

void ui_draw_list(const SyncState *state, int selected, int scroll) {
    psvDebugScreenPuts(CLR_SCREEN);

    /* Header */
    goto_rc(0, 0);
    psvDebugScreenPuts(FG_GREEN);
    psvDebugScreenPrintf("=== Vita Save Sync v%s ===", APP_VERSION);
    psvDebugScreenPuts(FG_RESET);

    /* Sub-header */
    goto_rc(1, 0);
    const char *type_str =
        (state->scan_vita_saves && state->scan_psp_emu_saves) ? "Vita+PSP" :
        state->scan_vita_saves ? "Vita only" : "PSP emu only";
    psvDebugScreenPrintf("%d saves [%s] | X:Sync Sq:Up Tri:Dn Sel:Scan",
                         state->num_titles, type_str);

    /* Separator */
    goto_rc(2, 0);
    psvDebugScreenPuts("------------------------------------------------------------");

    /* List */
    int visible = STATUS_ROW - LIST_START - 2;
    int end = scroll + visible;
    if (end > state->num_titles) end = state->num_titles;

    for (int i = scroll; i < end; i++) {
        const TitleInfo *t = &state->titles[i];
        bool sel = (i == selected);
        goto_rc(LIST_START + (i - scroll), 0);
        psvDebugScreenPuts(CLR_LINE);
        if (sel) psvDebugScreenPuts(FG_YELLOW);
        const char *plat = t->platform == PLATFORM_PSP_EMU ? "PSP" : "VTA";
        psvDebugScreenPrintf("%s %s %-9s  %u files  %u bytes",
                             sel ? ">" : " ", plat, t->game_id,
                             t->file_count, t->total_size);
        if (sel) psvDebugScreenPuts(FG_RESET);
    }

    /* Nav hint */
    goto_rc(STATUS_ROW - 1, 0);
    psvDebugScreenPuts("Up/Dn:nav  L/R:page  Start:exit");
}

bool ui_confirm(const TitleInfo *title, SyncAction action,
                const char *server_hash, uint32_t server_size) {
    psvDebugScreenPuts(CLR_SCREEN);

    const char *action_str =
        action == SYNC_UPLOAD   ? "UPLOAD to server" :
        action == SYNC_DOWNLOAD ? "DOWNLOAD from server" :
        action == SYNC_CONFLICT ? "CONFLICT - choose action" :
        "UP TO DATE";

    goto_rc(1, 0);
    psvDebugScreenPuts(FG_GREEN);
    psvDebugScreenPrintf("Game:    %s (%s)", title->name, title->game_id);
    psvDebugScreenPuts(FG_RESET);

    goto_rc(2, 0);
    psvDebugScreenPrintf("Platform:%s",
                         title->platform == PLATFORM_PSP_EMU ? " PSP (emulated)" : " PS Vita native");

    goto_rc(3, 0);
    psvDebugScreenPuts(FG_YELLOW);
    psvDebugScreenPrintf("Action:  %s", action_str);
    psvDebugScreenPuts(FG_RESET);

    goto_rc(4, 0);
    psvDebugScreenPrintf("Local:   %u bytes  (%d files)", title->total_size, title->file_count);

    goto_rc(5, 0);
    if (server_hash && server_hash[0])
        psvDebugScreenPrintf("Server:  %u bytes", server_size);
    else
        psvDebugScreenPuts("Server:  (no save)");

    if (action == SYNC_UP_TO_DATE) {
        goto_rc(7, 0);
        psvDebugScreenPuts("Already up to date. Press X.");

        SceCtrlData pad;
        uint32_t prev = 0;
        while (1) {
            sceCtrlReadBufferPositive2(0, &pad, 1);
            uint32_t just = pad.buttons & ~prev;
            prev = pad.buttons;
            if (just & (SCE_CTRL_CROSS | SCE_CTRL_CIRCLE)) break;
            sceKernelDelayThread(16000);
        }
        return false;
    }

    goto_rc(7, 0);
    psvDebugScreenPuts(FG_GREEN "X: Confirm  |  O: Cancel" FG_RESET);

    SceCtrlData pad;
    uint32_t prev = 0;
    while (1) {
        sceCtrlReadBufferPositive2(0, &pad, 1);
        uint32_t just = pad.buttons & ~prev;
        prev = pad.buttons;
        if (just & SCE_CTRL_CROSS)  return true;
        if (just & SCE_CTRL_CIRCLE) return false;
        sceKernelDelayThread(16000);
    }
}

void ui_draw_config(const SyncState *state) {
    psvDebugScreenPuts(CLR_SCREEN);
    goto_rc(0, 0);
    psvDebugScreenPuts(FG_GREEN "=== Config ===" FG_RESET);
    goto_rc(2, 0); psvDebugScreenPrintf("Server:     %s", state->server_url);
    goto_rc(3, 0); psvDebugScreenPrintf("API Key:    %s", state->api_key[0] ? "(set)" : "(not set)");
    goto_rc(4, 0); psvDebugScreenPrintf("Console ID: %s", state->console_id);
    goto_rc(5, 0); psvDebugScreenPrintf("WiFi:       %s", state->network_connected ? "Connected" : "Not connected");
    goto_rc(6, 0); psvDebugScreenPrintf("Scan Vita:  %s", state->scan_vita_saves ? "Yes" : "No");
    goto_rc(7, 0); psvDebugScreenPrintf("Scan PSP:   %s", state->scan_psp_emu_saves ? "Yes" : "No");
    goto_rc(9, 0); psvDebugScreenPrintf("Edit config at: %s", CONFIG_PATH);
}
