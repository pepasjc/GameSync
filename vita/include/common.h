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
#define MAX_FILES       64      /* max files per save directory */
#define MAX_FILE_LEN    128     /* max relative path length */
#define MAX_FILE_SIZE   (16 * 1024 * 1024)  /* 16MB max */

/* PS Vita save paths */
#define VITA_SAVEDATA_PATH  "ux0:user/00/savedata"
#define PSP_SAVEDATA_PATH   "ux0:pspemu/PSP/SAVEDATA"
#define CONFIG_PATH         "ux0:data/vitasync/config.txt"
#define SYNC_DATA_DIR       "ux0:data/vitasync"
#define STATE_FILE          "ux0:data/vitasync/state.dat"
#define HASH_CACHE_FILE     "ux0:data/vitasync/hash_cache.dat"
#define CONSOLE_ID_FILE     "ux0:data/vitasync/console_id.txt"
#define DOWNLOADS_FILE      "ux0:data/vitasync/downloads.dat"

/* ROM download targets.  The PSP emulator on a Vita (Adrenaline) reads
 * its ISO/CSO images and PS1 EBOOTs from the same tree the PSP itself
 * uses, rooted at ``pspemu_root`` (default ux0:pspemu, configurable
 * because Adrenaline can be pointed at ur0:/uma0:):
 *   PSP CSO/ISO → <root>/ISO/<filename>
 *   PS1 EBOOT   → <root>/PSP/GAME/<gameid>/EBOOT.PBP
 * Anything else lands in the app's own downloads folder so an unknown
 * system never gets written somewhere the emulator would misread. */
#define PSPEMU_ROOT_DEFAULT     "ux0:pspemu"
#define ROM_TARGET_FALLBACK_DIR "ux0:data/vitasync/downloads"

/* Top-level views the user cycles through with START.  Each view has
 * its own input-dispatch block in main.c. */
typedef enum {
    APP_VIEW_SAVES     = 0,
    APP_VIEW_ROMS      = 1,
    APP_VIEW_DOWNLOADS = 2,
    APP_VIEW_COUNT     = 3,
} AppView;

typedef enum {
    PLATFORM_VITA = 0,  /* native PS Vita save */
    PLATFORM_PSP_EMU,   /* PSP emulation save on Vita */
} Platform;

typedef struct {
    char game_id[GAME_ID_LEN];          /* product code: PCSE00082 or ULUS10272 */
    char name[MAX_TITLE_LEN];           /* game name */
    char save_dir[SAVE_DIR_LEN];        /* full path to save directory */
    Platform platform;
    bool is_psx;                        /* true if PSX classic (within PSP emu) */
    bool server_only;                   /* true if listed from server but not yet local */
    uint8_t hash[32];                   /* SHA-256 of all save data */
    bool hash_calculated;
    bool on_server;
    uint32_t total_size;
    int file_count;
} TitleInfo;

typedef struct {
    TitleInfo titles[MAX_TITLES];
    int num_titles;

    char server_url[256];
    char api_key[128];
    char console_id[64];

    bool network_connected;
    bool server_reachable;

    bool scan_vita_saves;    /* true = scan native Vita saves */
    bool scan_psp_emu_saves; /* true = scan PSP emu saves */

    /* Where Adrenaline keeps its PSP tree (ISO/, PSP/GAME/).  ROM
     * downloads are routed under here. */
    char pspemu_root[64];
} SyncState;

typedef enum {
    SYNC_UPLOAD = 0,
    SYNC_DOWNLOAD,
    SYNC_UP_TO_DATE,
    SYNC_CONFLICT,
    SYNC_FAILED,
} SyncAction;

#endif /* COMMON_H */
