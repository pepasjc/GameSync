#ifndef WIIUSYNC_COMMON_H
#define WIIUSYNC_COMMON_H

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

#ifndef APP_VERSION
#define APP_VERSION "dev"
#endif

/*
 * Save Sync Wii U client (Aroma homebrew).
 *
 * Three save families live side by side:
 *   1. GameCube saves inside Nintendont virtual memory-card images on SD
 *      (standard GC card images -> reuses the GC client's vmcfs + the
 *      server's gc-card / gc-vmc endpoints, so a save round-trips to the
 *      GameCube client, Dolphin and the Android app unchanged).
 *   2. vWii saves on SLC NAND     (/title/00010000/<tidlo>/data/)
 *   3. Wii U saves on MLC         (/usr/save/00050000/<tidlo>/user/)
 *
 * (2) and (3) are multi-file directory trees, so they use the streaming
 * 3DSS v5 bundle + three-way-hash smart sync (same as the Xbox client),
 * not the GC-style per-save raw endpoints.
 */

#define MAX_TITLE_LEN   128
#define SAVE_DIR_LEN    320

/* Server title_id buffers.  GC uses "GC_<code>", vWii "WII_<code>", Wii U the
 * native 16-hex title id — 64 chars + NUL covers the v5 bundle field. */
#define TITLE_ID_LEN    65

/*
 * Storage layout on the SD card (fs:/vol/external01):
 *   3dssync/config.txt
 *   3dssync/consoleid.txt
 *   3dssync/downloads.dat
 *   3dssync/state/<title_id>.txt        (last-synced hash)
 *   3dssync/hashcache/<title_id>.txt    (local save hash cache)
 *   3dssync/backup/<title_id>/...       (pre-restore vWii/Wii U backup)
 */
#define SD_ROOT_DEFAULT     "fs:/vol/external01"
#define APP_DATA_SUBDIR     "/3dssync"

/*
 * Downloaded games may live on the SD card or on a FAT32 USB drive.  App data
 * (config, state, hash cache) always stays on SD — it is small, and the SD is
 * the only storage guaranteed to be present.
 *
 * The FAT32 USB root is a devoptab libmocha registers for us when
 * natives_mount_usb_fat() succeeds.  It is NOT the same device as
 * ``storage_usb01:``: that one is the console's own WFS-formatted drive,
 * which holds Wii U *saves* and cannot carry a /games or /wbfs folder.
 */
#define USB_ROOT_DEFAULT    "usb:"
#define USB_FAT_NAME        "usb"

#define DEFAULT_NIN_SAVES_DIR "/saves"    /* Nintendont memcard images */
#define DEFAULT_GAMES_DIR     "/games"    /* Nintendont GC ISOs */
#define DEFAULT_WBFS_DIR      "/wbfs"     /* USB-loader Wii games */
#define DEFAULT_INSTALL_DIR   "/install"  /* WUP folders for MCP install */

/* Top-level views.  L/R cycle in order. */
typedef enum {
    APP_VIEW_ROMS      = 0,   /* server catalog (GC / WII toggle) */
    APP_VIEW_LOCAL     = 1,   /* installed games on SD */
    APP_VIEW_DOWNLOADS = 2,   /* download queue */
    APP_VIEW_GCCARDS   = 3,   /* Nintendont memcard images + their saves */
    APP_VIEW_SERVER    = 4,   /* all GC saves on the server */
    APP_VIEW_VWII      = 5,   /* vWii NAND saves (smart sync) */
    APP_VIEW_WIIU      = 6,   /* Wii U MLC saves (smart sync) */
    APP_VIEW_CONFIG    = 7,
    APP_VIEW_COUNT     = 8,
} AppView;

typedef struct {
    char server_url[256];
    char api_key[128];
    char console_id[32];

    /* Network state (filled by network_init). */
    bool net_ready;
    char ip[16];

    /* Storage. */
    bool sd_ready;
    char sd_root[64];             /* "fs:/vol/external01" once mounted */
    char nin_saves_dir[96];       /* Nintendont memcard folder, "/saves" */
    char games_dir[96];           /* GC ISO folder, "/games" */
    char wbfs_dir[96];            /* Wii WBFS folder, "/wbfs" */
    char install_dir[96];         /* WUP staging folder, "/install" */

    /* Where downloaded games land: "sd" or "usb".  App data ignores this and
     * always uses sd_root.  Selecting "usb" with no FAT32 drive attached
     * falls back to SD rather than failing every download — usb_fat_ready
     * says which one is actually in use. */
    char rom_storage[8];
    bool usb_fat_ready;           /* FAT32 USB mounted as "usb:" */
    char usb_fat_error[96];

    /* Where MCP installs a Wii U title: "mlc" (internal NAND) or "usb"
     * (the console's own WFS-formatted USB drive).  Independent of
     * rom_storage, which only says where the WUP folder was staged. */
    char install_target[8];

    /* Smart-sync toggles. */
    bool sync_vwii;
    bool sync_wiiu;

    /* How to leave the app.  This console (Health & Safety wrapper title)
     * black-screens on exit, so the strategy is runtime-selectable to make
     * the cause bisectable without a rebuild per attempt:
     *   full     tear everything down, then SYSLaunchMenu   (default)
     *   minimal  no teardown at all, then SYSLaunchMenu
     *   relaunch tear down, then SYSRelaunchTitle(0, NULL)
     *   none     tear down, issue no launch request at all  */
    char exit_mode[16];

    /* libmocha / FSA state (filled by wiiusaves_init / vwiisaves_init). */
    bool mocha_ok;
    bool slc_mounted;
    bool mlc_mounted;
    bool usb_mounted;      /* optional — only when a USB drive is attached */
    char mocha_error[96];
} SyncState;

/* Absolute path helper: "<sd_root><rel>" (rel starts with '/'). */
const char *sdpath(const SyncState *state, const char *rel,
                   char *out, size_t out_size);

#endif /* WIIUSYNC_COMMON_H */
