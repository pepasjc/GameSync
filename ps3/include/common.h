#ifndef PS3SYNC_COMMON_H
#define PS3SYNC_COMMON_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifndef APP_VERSION
#define APP_VERSION "dev"
#endif

#define MAX_TITLES 256
#define MAX_TITLE_LEN 128
#define GAME_ID_LEN 64
#define PATH_LEN 512

#define APP_ID "3DSSYNC00"
#define APP_DIR "/dev_hdd0/game/3DSSYNC00/USRDIR"
#define CONFIG_PATH "/dev_hdd0/game/3DSSYNC00/USRDIR/config.txt"
#define STATE_FILE "/dev_hdd0/game/3DSSYNC00/USRDIR/state.dat"
#define HASH_CACHE_FILE "/dev_hdd0/game/3DSSYNC00/USRDIR/hash_cache.dat"
#define DEBUG_LOG_FILE "/dev_hdd0/game/3DSSYNC00/USRDIR/debug.log"

typedef enum {
    SAVE_KIND_PS3     = 0,  /* PS3 Blu-ray or PSN (BLUS, NPUB, etc.) */
    SAVE_KIND_PS1_VM1 = 1,  /* PS1 .vm1 virtual memory card file */
    SAVE_KIND_PS1     = 2,  /* PS1 Classic save in savedata dir */
    SAVE_KIND_PSP     = 3,  /* PSP save (skip) */
    SAVE_KIND_PS2     = 4,  /* PS2 Classic save (skip) */
} SaveKind;

typedef struct {
    char title_id[GAME_ID_LEN];   /* full dir name e.g. BCUS98233AUTOSAVE */
    char game_code[16];           /* server ID e.g. BCUS98233 or SLUS12345 */
    char name[MAX_TITLE_LEN];
    char local_path[PATH_LEN];
    SaveKind kind;
    uint8_t hash[32];
    bool hash_calculated;
    bool server_only;             /* exists only on server, not locally */
    bool on_server;               /* save exists on server */
    uint32_t total_size;
    int file_count;
} TitleInfo;

typedef struct {
    TitleInfo titles[MAX_TITLES];
    int num_titles;

    char server_url[256];
    char api_key[128];
    char console_id[32];
    char ps3_user[16];

    bool scan_ps3;
    bool scan_ps1;
    bool network_connected;
} SyncState;

#endif /* PS3SYNC_COMMON_H */
