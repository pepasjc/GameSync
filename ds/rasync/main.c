// RA Sync: runs when a game with RetroAchievements is quit.
//
// nds-bootstrap-ra points the game's quit path at sd:/_nds/ra/rasync.nds and
// saves the real one (TWiLight Menu++) in sd:/_nds/ra/return.txt; quitting
// boots this through Unlaunch in DSi mode.  If unlocks are waiting, or a ROM
// is new, WiFi comes up (DSi mode: WPA2 connections 4-6 work), the unlocks
// go to the GameSync server and sets are fetched; then Unlaunch is told to
// boot the saved quit path and the console restarts into it.

#include <nds.h>
#include <fat.h>
#include <stdio.h>
#include <string.h>

#include "common.h"
#include "config.h"
#include "network.h"
#include "ra.h"

#define RETURN_PATH "sd:/_nds/ra/return.txt"
#define DEFAULT_RETURN "sd:/_nds/TWiLightMenu/main.srldr"

// Unlaunch's auto-load request, as nds-bootstrap writes it before a reboot:
// Unlaunch boots <path> instead of its default once the console restarts.
static void unlaunch_autoload(const char *path) {
    u8 *info = (u8 *)0x02000800;
    memcpy(info, "AutoLoadInfo", 12);
    *(u16 *)(info + 0x0C) = 0x3F0;           // length covered by the CRC
    *(u16 *)(info + 0x0E) = 0;               // CRC, below
    *(u32 *)(info + 0x10) = BIT(0) | BIT(1); // load the path; use the colours
    *(u16 *)(info + 0x14) = 0x7FFF;          // top screen colour
    *(u16 *)(info + 0x16) = 0x7FFF;          // bottom screen colour
    memset(info + 0x18, 0, 0x20 + 0x208 + 0x1C0);
    u16 *name = (u16 *)(info + 0x38);        // UTF-16, 0-terminated
    for (int i = 0; i < 255 && path[i]; i++) name[i] = (u8)path[i];
    *(u16 *)(info + 0x0E) = swiCRC16(0xFFFF, info + 0x10, 0x3F0);
    DC_FlushAll();
}

static void read_return_path(char *out, size_t size) {
    snprintf(out, size, "%s", DEFAULT_RETURN);
    FILE *f = fopen(RETURN_PATH, "rb");
    if (!f) return;
    char buf[256];
    size_t n = fread(buf, 1, sizeof(buf) - 1, f);
    fclose(f);
    buf[n] = '\0';
    buf[strcspn(buf, "\r\n")] = '\0';
    if (buf[0]) snprintf(out, size, "%s", buf);
}

int main(int argc, char *argv[]) {
    consoleDemoInit();
    iprintf("RetroAchievements sync\n\n");

    char return_path[256];
    bool fat_ok = fatInitDefault();
    read_return_path(return_path, sizeof(return_path));

    if (fat_ok) {
        static SyncState state;
        memset(&state, 0, sizeof(state));
        char error[128] = {0};
        int pending = ra_pending_unlocks();
        bool new_roms = ra_has_new_roms();
        if (pending > 0 || new_roms) {
            if (!config_load(&state, error, sizeof(error))) {
                iprintf("Config: %s\n", error);
            } else if (network_init(&state) != 0) {
                iprintf("No WiFi: unlocks stay on the SD\nand go up next time\n");
                for (int i = 0; i < 120; i++) swiWaitForVBlank();
            } else {
                ra_auto_sync(&state);
                network_cleanup();
            }
        } else {
            iprintf("Nothing to sync\n");
        }
    } else {
        iprintf("SD card not readable\n");
    }

    iprintf("\nBack to %s\n", return_path);
    unlaunch_autoload(return_path);
    // No jump target: in DSi mode calico restarts the console, and Unlaunch
    // boots the path above
    return 0;
}
