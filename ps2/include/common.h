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
#define GAME_ID_LEN     32      /* PS2 serial: SLUS_213.71 + null */
#define SAVE_DIR_LEN    260
#define MAX_FILE_SIZE   (16 * 1024 * 1024)

/*
 * Storage layout.
 *
 * Config + console identity live on the memory card. ROM ISOs and the
 * download queue normally live on the detected mass-storage root at OPL's
 * expected layout. That root can be USB, or an internal HDD on a PS2 fat
 * model formatted as FAT/exFAT through BDM:
 *   mass:/DVD/<SERIAL>.<title>.iso  - DVD games (>750 MB)
 *   mass:/CD/<SERIAL>.<title>.iso   - CD games (<=750 MB)
 *
 * Classic internal HDD mode uses APA/HDLoader partitions instead:
 *   hdd0:PP.<SERIAL>..<TITLE>        - one APA game partition
 * The queue for that mode is kept on the memory card because APA does not
 * expose a generic file tree.
 */
#define APP_MC_DIR          "mc0:/3DSSYNC"
#define CONFIG_PATH         APP_MC_DIR "/CONFIG.TXT"
#define CONSOLE_ID_FILE     APP_MC_DIR "/CONSOLEID.TXT"
#define HDL_DOWNLOADS_FILE  APP_MC_DIR "/HDL_DOWNLOADS.DAT"

#define STORAGE_DEFAULT_ROOT    "mass:"
#define STORAGE_DATA_SUBDIR     "/3dssync"
#define DOWNLOADS_FILE          STORAGE_DEFAULT_ROOT STORAGE_DATA_SUBDIR "/downloads.dat"

/* Backwards-compatible aliases for older PS2 client code paths. */
#define USB_DEFAULT_ROOT        STORAGE_DEFAULT_ROOT
#define USB_DATA_SUBDIR         STORAGE_DATA_SUBDIR

/* Top-level views.  START cycles in order. */
typedef enum {
    APP_VIEW_ROMS      = 0,   /* server catalog (HTTP) */
    APP_VIEW_LOCAL     = 1,   /* installed ISOs or HDL partitions */
    APP_VIEW_DOWNLOADS = 2,   /* download queue */
    APP_VIEW_SAVES     = 3,   /* VMC / MemCard Pro card-image sync */
    APP_VIEW_MCARD     = 4,   /* physical memory card slot 1 (mc0:) */
    APP_VIEW_MCARD2    = 5,   /* physical memory card slot 2 (mc1:) */
    APP_VIEW_SERVER    = 6,   /* all PS1/PS2 saves on the server */
    APP_VIEW_CONFIG    = 7,
    APP_VIEW_COUNT     = 8,
} AppView;

typedef enum {
    STORAGE_PREF_AUTO = 0,
    STORAGE_PREF_USB  = 1,
    STORAGE_PREF_HDD  = 2,
} StoragePreference;

typedef enum {
    STORAGE_BACKEND_NONE      = 0,
    STORAGE_BACKEND_MASS      = 1,   /* USB or BDM FAT/exFAT, exposed as mass: */
    STORAGE_BACKEND_HDLOADER  = 2,   /* APA/HDLoader partitions on hdd0: */
} StorageBackend;

typedef struct {
    char server_url[256];
    char api_key[128];
    char console_id[32];

    /* Network state */
    bool net_ready;          /* IRX modules loaded + NetMan up */
    bool dhcp_ok;            /* IP address ready (static or DHCP) */
    char ip[16];
    char netmask[16];
    char gateway[16];

    /* Configured network mode */
    bool use_static_ip;
    char static_ip[16];
    char static_netmask[16];
    char static_gateway[16];

    StoragePreference storage_pref; /* auto, usb, or hdd */
    StorageBackend    storage_backend;

    /* GameID device per memory-card slot: 0=off,1=auto,2=gen1,3=gen2.
     * Persisted so the gen1/MCP2 choice survives relaunch. */
    int               mmce_mode[2];

    /* Detected install target. usb_ready is kept as the legacy "storage
     * ready" flag; usb_root is "mass:", "mass1:", etc. or "hdd0:hdl". */
    bool usb_ready;
    char usb_root[16];
} SyncState;

#endif /* COMMON_H */
