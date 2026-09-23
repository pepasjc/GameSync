#ifndef COMMON_H
#define COMMON_H

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

#ifndef APP_VERSION
#define APP_VERSION "dev"
#endif

#define MAX_TITLES      256
#define MAX_TITLE_LEN   128
/* GC title id on the server is "GC_" + 4-char game code (e.g. "GC_GM4E"). */
#define GAME_ID_LEN     16
#define SAVE_DIR_LEN    320
#define MAX_FILE_SIZE   (16 * 1024 * 1024)

/*
 * Storage layout.
 *
 * Config + console identity live on the SD card (the same FAT volume used for
 * ROM installs), so a single removable card carries everything:
 *   sd:/3dssync/config.txt
 *   sd:/3dssync/consoleid.txt
 *   sd:/3dssync/downloads.dat
 *
 * ROM ISOs are written to a configurable folder (default /games) in the flat
 * layout Swiss / GC Loader / FlippyDrive read:
 *   sd:/games/<SERIAL>.<title>.iso
 */
#define SD_MOUNT_NAME       "sd"
#define SD_ROOT             "sd:"
#define APP_DATA_SUBDIR     "/3dssync"
#define CONFIG_PATH         SD_ROOT APP_DATA_SUBDIR "/config.txt"
#define CONSOLE_ID_PATH     SD_ROOT APP_DATA_SUBDIR "/consoleid.txt"
#define DOWNLOADS_FILE      SD_ROOT APP_DATA_SUBDIR "/downloads.dat"
#define DEFAULT_GAMES_DIR   "/games"

/* Top-level views.  L/R triggers cycle in order. */
typedef enum {
    APP_VIEW_ROMS      = 0,   /* server catalog (HTTP) */
    APP_VIEW_LOCAL     = 1,   /* installed ISOs on SD */
    APP_VIEW_DOWNLOADS = 2,   /* download queue */
    APP_VIEW_SAVES     = 3,   /* VMC / full-card images on SD */
    APP_VIEW_CARDA     = 4,   /* physical memory card slot A (EXI0) */
    APP_VIEW_CARDB     = 5,   /* physical memory card slot B (EXI1) */
    APP_VIEW_SERVER    = 6,   /* all GC saves on the server */
    APP_VIEW_CONFIG    = 7,
    APP_VIEW_COUNT     = 8,
} AppView;

/*
 * SD device used for ROM installs + app data.
 *
 *   SP2     — SD2SP2 in Serial Port 2 (EXI channel 2).  __io_gcsd2.
 *   GECKO_A — SD Gecko / SDLoadr in memory-card slot A (EXI channel 0). __io_gcsda.
 *   GECKO_B — SD Gecko in memory-card slot B (EXI channel 1).            __io_gcsdb.
 *
 * The Broadband Adapter sits on EXI0 device 2, so it coexists with a memory
 * card in slot A but NOT with an SD Gecko in slot A (same EXI device slot).
 */
typedef enum {
    SD_DEV_SP2     = 0,
    SD_DEV_GECKO_A = 1,
    SD_DEV_GECKO_B = 2,
    SD_DEV_COUNT   = 3,
} SdDevice;

/* GameID device per memory-card slot: 0=off, 1=on (MemCard Pro GC). */
typedef enum {
    GID_OFF = 0,
    GID_ON  = 1,
} GameIdMode;

typedef struct {
    char server_url[256];
    char api_key[128];
    char console_id[32];

    /* Network state (filled by network_init). */
    bool net_ready;
    bool dhcp_ok;
    char ip[16];
    char netmask[16];
    char gateway[16];

    /* Configured network mode. */
    bool use_static_ip;
    char static_ip[16];
    char static_netmask[16];
    char static_gateway[16];

    /* ROM/VMC storage. */
    SdDevice sd_device;          /* preferred SD device */
    char     games_folder[64];   /* ROM install folder, default "/games" */
    bool     sd_ready;           /* a FAT SD volume is mounted */
    char     sd_root[8];         /* "sd:" once mounted */

    /* GameID per memory-card slot (A=0, B=1). Persisted to config. */
    int      mmce_mode[2];
} SyncState;

const char *sd_device_to_str(SdDevice dev);

#endif /* COMMON_H */
