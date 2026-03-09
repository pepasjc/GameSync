#ifndef COMMON_H
#define COMMON_H

#include <3ds.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

// APP_VERSION is defined by the Makefile from the root VERSION file

// Max values
#define MAX_TITLES        256
#define MAX_PATH_LEN      256
#define MAX_URL_LEN       256
#define MAX_API_KEY_LEN   128

// Config file location on SD card
#define CONFIG_PATH "sdmc:/3ds/3dssync/config.txt"
#define BACKUP_DIR  "sdmc:/3ds/3dssync/backups"

// Title info for display and sync
typedef struct {
    u64 title_id;
    FS_MediaType media_type;
    char title_id_hex[17]; // 16 hex chars + null
    char product_code[16]; // Product code from AM (e.g., CTR-P-BRBE) or NDS game code
    char name[64];         // Game name (from server lookup or product code)
    bool has_save_data;
    bool in_conflict;      // Set after sync if this title has a conflict
    bool is_nds;           // NDS game (via nds-bootstrap on SD) vs 3DS title
    bool marked;           // User-selected for batch operations
    char sav_path[MAX_PATH_LEN]; // NDS only: path to .sav file on SD card
} TitleInfo;

// Console ID file location
#define CONSOLE_ID_PATH  "sdmc:/3ds/3dssync/console_id.txt"
#define HASH_CACHE_FILE  "sdmc:/3ds/3dssync/hash_cache.dat"

// App configuration loaded from SD card
typedef struct {
    char server_url[MAX_URL_LEN];
    char api_key[MAX_API_KEY_LEN];
    char console_id[17];  // 16 hex chars + null (generated on first run)
    char nds_dir[MAX_PATH_LEN]; // NDS ROM directory on SD (e.g., "sdmc:/roms/nds")
} AppConfig;

#endif // COMMON_H
