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
#define GAME_ID_LEN     32      /* up to 31 chars + null: ULUS10272DATA00\0 */
#define SAVE_DIR_LEN    260
#define MAX_FILES       32      /* max files per PSP save directory */
#define MAX_FILE_LEN    64      /* max relative path length */
#define MAX_FILE_SIZE   (8 * 1024 * 1024)  /* 8MB max save size */

/* PSP Memory Stick paths */
#define SAVEDATA_PATH   "ms0:/PSP/SAVEDATA"
#define CONFIG_PATH     "ms0:/PSP/GAME/pspsync/config.txt"
#define SYNC_STATE_DIR  "ms0:/PSP/GAME/pspsync"
#define STATE_FILE      "ms0:/PSP/GAME/pspsync/state.dat"
#define HASH_CACHE_FILE "ms0:/PSP/GAME/pspsync/hash_cache.dat"
#define CONSOLE_ID_FILE "ms0:/PSP/GAME/pspsync/console_id.txt"

typedef struct {
    char game_id[GAME_ID_LEN];          /* PSP product code e.g. ULUS10272 */
    char name[MAX_TITLE_LEN];           /* game name from database */
    bool is_psx;                        /* true if PSX classic */
    bool server_only;                   /* true if listed from server but not yet local */
    char save_dir[SAVE_DIR_LEN];        /* full path to save directory */
    uint8_t hash[32];                   /* SHA-256 of all save data */
    bool hash_calculated;
    bool on_server;                     /* true if server has a save for this game */
    uint32_t total_size;                /* total save size in bytes */
    int file_count;                     /* number of files in save dir */
} TitleInfo;

typedef struct {
    TitleInfo titles[MAX_TITLES];
    int num_titles;

    char server_url[256];
    char api_key[128];
    char console_id[32];

    /* WiFi: PSP uses its own network interface */
    /* connect via sceNetApctlConnect with access point index */
    int wifi_ap_index;   /* 0-2 for PSP saved APs */

    bool wifi_connected;
} SyncState;

typedef enum {
    SYNC_UPLOAD = 0,
    SYNC_DOWNLOAD,
    SYNC_UP_TO_DATE,
    SYNC_CONFLICT,
    SYNC_FAILED,
} SyncAction;

#endif /* COMMON_H */
