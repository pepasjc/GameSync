#include "ui.h"
#include "saves.h"
#include "config.h"
#include "sync.h"
#include <stdio.h>
#include <string.h>

void ui_show_save_details(Title *title) {
    consoleClear();
    iprintf("=== Save Details ===\n\n");
    
    iprintf("Game: %s\n", title->game_name);
    iprintf("Size: %lu KB\n", (unsigned long)(title->save_size / 1024));
    iprintf("Path: %s\n\n", title->save_path);
    
    // Show hash if calculated
    if (title->hash_calculated) {
        iprintf("Hash:\n");
        for (int i = 0; i < 32; i++) {
            iprintf("%02x", title->hash[i]);
            if (i % 16 == 15) iprintf("\n");
        }
    } else {
        iprintf("Hash: Not calculated\n");
    }
    
    iprintf("\nPress any button\n");
    
    while(pmMainLoop()) {
        swiWaitForVBlank();
        scanKeys();
        if(keysDown()) break;
    }
}

bool ui_confirm_sync(Title *title, const char *server_hash, size_t server_size, bool is_upload) {
    consoleClear();
    
    // Ensure local hash is calculated
    if (!title->hash_calculated) {
        iprintf("Calculating hash...\n");
        if (saves_ensure_hash(title) != 0) {
            iprintf("Failed to calculate hash!\n");
            iprintf("\nPress any button\n");
            while(pmMainLoop()) {
                swiWaitForVBlank();
                scanKeys();
                if(keysDown()) break;
            }
            return false;
        }
    }
    
    // Show confirmation dialog
    iprintf("=== %s Confirmation ===\n\n", is_upload ? "Upload" : "Download");
    iprintf("Game: %.25s\n\n", title->game_name);
    
    // Local save info
    iprintf("Local Save:\n");
    iprintf("  Size: %lu bytes\n", (unsigned long)title->save_size);
    iprintf("  Hash: ");
    for (int i = 0; i < 8; i++) iprintf("%02x", title->hash[i]);
    iprintf("...\n\n");
    
    // Server save info
    if (server_hash && server_hash[0] != '\0') {
        iprintf("Server Save:\n");
        iprintf("  Size: %lu bytes\n", (unsigned long)server_size);
        iprintf("  Hash: %.16s...\n\n", server_hash);
        
        // Check if they match - convert local hash to hex string
        char local_hash_str[65];
        for (int i = 0; i < 32; i++) {
            sprintf(&local_hash_str[i*2], "%02x", title->hash[i]);
        }
        local_hash_str[64] = '\0';
        
        if (strncmp(local_hash_str, server_hash, 64) == 0) {
            iprintf("Status: Match (up to date)\n\n");
        } else {
            iprintf("Status: Different\n\n");
        }
    } else {
        iprintf("Server Save: Not found\n\n");
    }
    
    if (is_upload) {
        iprintf("Upload local save to server?\n\n");
    } else {
        iprintf("Download server save to local?\n\n");
    }
    
    iprintf("A = Confirm, B = Cancel\n");
    
    // Wait for input
    while(pmMainLoop()) {
        swiWaitForVBlank();
        scanKeys();
        int pressed = keysDown();
        
        if (pressed & KEY_A) return true;
        if (pressed & KEY_B) return false;
    }
    
    return false;
}

SyncAction ui_confirm_smart_sync(Title *title, SyncDecision *decision) {
    consoleClear();

    iprintf("=== Smart Sync ===\n\n");
    iprintf("Game: %.25s\n\n", title->game_name);

    // Show local info
    iprintf("-- Local --\n");
    if (title->save_size > 0) {
        iprintf("Size: %lu bytes\n", (unsigned long)title->save_size);
        if (title->hash_calculated) {
            iprintf("Hash: ");
            for (int i = 0; i < 8; i++) iprintf("%02x", title->hash[i]);
            iprintf("...\n");
        } else {
            iprintf("Hash: (not calculated)\n");
        }
    } else {
        iprintf("No local save\n");
    }
    iprintf("\n");

    // Show server info
    iprintf("-- Server --\n");
    if (decision->server_hash[0]) {
        iprintf("Size: %lu bytes\n", (unsigned long)decision->server_size);
        iprintf("Hash: %.16s...\n", decision->server_hash);
    } else {
        iprintf("No server save\n");
    }
    iprintf("\n");

    // Show last synced
    if (decision->has_last_synced) {
        iprintf("-- Last Synced --\n");
        iprintf("Hash: %.16s...\n", decision->last_synced_hash);
        iprintf("\n");
    }

    // Show suggested action
    switch (decision->action) {
        case SYNC_UP_TO_DATE:
            iprintf("-- Suggested --\n");
            iprintf("\x1b[32m Already in sync!\x1b[0m\n");
            iprintf("\nPress any button\n");
            while (pmMainLoop()) {
                swiWaitForVBlank();
                scanKeys();
                if (keysDown()) break;
            }
            iprintf("\x1b[0m");
            return SYNC_UP_TO_DATE;

        case SYNC_UPLOAD:
            iprintf("-- Suggested --\n");
            if (decision->has_last_synced)
                iprintf("\x1b[32m>> UPLOAD (local changed)\x1b[0m\n");
            else
                iprintf("\x1b[32m>> UPLOAD\x1b[0m\n");

            iprintf("\nA=Upload  B=Cancel\n");

            while (pmMainLoop()) {
                swiWaitForVBlank();
                scanKeys();
                int pressed = keysDown();
                if (pressed & KEY_A) { iprintf("\x1b[0m"); return SYNC_UPLOAD; }
                if (pressed & KEY_B) { iprintf("\x1b[0m"); return SYNC_UP_TO_DATE; }
            }
            iprintf("\x1b[0m");
            return SYNC_UP_TO_DATE;

        case SYNC_DOWNLOAD:
            iprintf("-- Suggested --\n");
            if (decision->has_last_synced)
                iprintf("\x1b[32m>> DOWNLOAD (server changed)\x1b[0m\n");
            else
                iprintf("\x1b[32m>> DOWNLOAD\x1b[0m\n");

            iprintf("\nA=Download  B=Cancel\n");

            while (pmMainLoop()) {
                swiWaitForVBlank();
                scanKeys();
                int pressed = keysDown();
                if (pressed & KEY_A) { iprintf("\x1b[0m"); return SYNC_DOWNLOAD; }
                if (pressed & KEY_B) { iprintf("\x1b[0m"); return SYNC_UP_TO_DATE; }
            }
            iprintf("\x1b[0m");
            return SYNC_UP_TO_DATE;

        case SYNC_CONFLICT:
            iprintf("-- Suggested --\n");
            iprintf("\x1b[31m!! CONFLICT !!\x1b[0m\n");
            iprintf("Both changed.\n");

            iprintf("\nR=Force Upload\n");
            iprintf("L=Force Download\n");
            iprintf("B=Cancel\n");

            while (pmMainLoop()) {
                swiWaitForVBlank();
                scanKeys();
                int pressed = keysDown();
                if (pressed & KEY_R) { iprintf("\x1b[0m"); return SYNC_UPLOAD; }
                if (pressed & KEY_L) { iprintf("\x1b[0m"); return SYNC_DOWNLOAD; }
                if (pressed & KEY_B) { iprintf("\x1b[0m"); return SYNC_UP_TO_DATE; }
            }
            iprintf("\x1b[0m");
            return SYNC_UP_TO_DATE;
    }

    iprintf("\x1b[0m");
    return SYNC_UP_TO_DATE;
}

// Draw config menu on current console
void ui_draw_config(const SyncState *state, int selected, bool focused, bool has_wifi) {
    const char *focus_indicator = focused ? "[ACTIVE]" : "[Press L]";
    iprintf("=== Configuration %s ===\n\n", focus_indicator);

    const char *items[] = {
        "Server URL",
        "API Key",
        "WiFi SSID",
        "WiFi WEP Key",
        "Rescan Saves",
        "Connect WiFi",
        "Check Updates"
    };
    const int item_count = 7;

    for (int i = 0; i < item_count; i++) {
        char cursor = (focused && i == selected) ? '>' : ' ';
        iprintf("%c %s\n", cursor, items[i]);

        if (i == 0) {
            char val[30];
            if (state->server_url[0]) {
                snprintf(val, sizeof(val), "%.28s", state->server_url);
            } else {
                snprintf(val, sizeof(val), "(not set)");
            }
            iprintf("   %s\n", val);
        } else if (i == 1) {
            int len = strlen(state->api_key);
            if (len > 4) {
                iprintf("   %.4s****\n", state->api_key);
            } else {
                iprintf("   (not set)\n");
            }
        } else if (i == 2) {
            char val[30];
            if (state->wifi_ssid[0]) {
                snprintf(val, sizeof(val), "%.28s", state->wifi_ssid);
            } else {
                snprintf(val, sizeof(val), "(not set)");
            }
            iprintf("   %s\n", val);
        } else if (i == 3) {
            int len = strlen(state->wifi_wep_key);
            if (len > 0) {
                iprintf("   (%d chars)\n", len);
            } else {
                iprintf("   (not set)\n");
            }
        }
    }

    // Draw button hints at bottom
    iprintf("\n");
    if (focused) {
        iprintf("A:Edit/Action L:Back START:Exit\n");
    } else if (has_wifi) {
        iprintf("A:Smart Sync X:Scan R:UL\n");
        iprintf("Y:Info L:Config START:Exit\n");
    } else {
        iprintf("Y:Info L:Config START:Exit\n");
    }
}
