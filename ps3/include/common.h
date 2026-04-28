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
#define GAMES_CONF_PATH "/dev_hdd0/game/3DSSYNC00/USRDIR/games.conf"
#define DOWNLOADS_FILE "/dev_hdd0/game/3DSSYNC00/USRDIR/downloads.dat"

/* ROM download target directories.  PS3 ISOs are mounted by webMAN/MultiMAN
 * from /dev_hdd0/PS3ISO; PSN packages live in /dev_hdd0/packages so the
 * built-in package installer can pick them up; .rap activation files for
 * PSN content go to /dev_hdd0/exdata where the firmware reads them. */
#define ROM_TARGET_ISO_DIR    "/dev_hdd0/PS3ISO"
#define ROM_TARGET_PKG_DIR    "/dev_hdd0/packages"
#define ROM_TARGET_EXDATA_DIR "/dev_hdd0/exdata"
/* Fallback for any other ROM kind we cannot place automatically */
#define ROM_TARGET_FALLBACK_DIR "/dev_hdd0/game/3DSSYNC00/USRDIR/downloads"

/* Top-level views the user can cycle between with SELECT.  Each view runs
 * its own input-dispatch block in main.c.  Adding a new view = add an enum
 * entry + a render branch + an input branch.  No scene framework — keep it
 * small. */
typedef enum {
    APP_VIEW_SAVES     = 0,
    APP_VIEW_ROMS      = 1,
    APP_VIEW_DOWNLOADS = 2,
    APP_VIEW_COUNT     = 3,
} AppView;

typedef enum {
    SAVE_KIND_PS3     = 0,
    SAVE_KIND_PS1_VM1 = 1,
    SAVE_KIND_PS1     = 2,
    SAVE_KIND_PSP     = 3,
    SAVE_KIND_PS2     = 4,
} SaveKind;

typedef enum {
    TITLE_STATUS_UNKNOWN     = 0,  /* exists both sides, not yet compared */
    TITLE_STATUS_LOCAL_ONLY  = 1,  /* local, not on server */
    TITLE_STATUS_SERVER_ONLY = 2,  /* server only */
    TITLE_STATUS_SYNCED      = 3,  /* hashes match */
    TITLE_STATUS_UPLOAD      = 4,  /* local changed, server unchanged */
    TITLE_STATUS_DOWNLOAD    = 5,  /* server changed, local unchanged */
    TITLE_STATUS_CONFLICT    = 6,  /* both changed */
} TitleStatus;

typedef struct {
    char title_id[GAME_ID_LEN];   /* full dir name e.g. BCUS98233AUTOSAVE */
    char game_code[16];           /* server ID e.g. BCUS98233 or SLUS12345 */
    char name[MAX_TITLE_LEN];
    char local_path[PATH_LEN];
    char upload_path[PATH_LEN];   /* auxiliary upload source (USB/export) when no HDD save is present */
    SaveKind kind;
    int ps1_slot_index;           /* shared-card slot index for parsed PS1 entries; -1 otherwise */
    bool ps1_shared_card;         /* true when entry comes from a multi-save/shared PS1 card */
    uint8_t hash[32];
    bool hash_calculated;
    bool upload_is_zip;
    bool server_only;
    bool on_server;
    bool server_meta_loaded;
    TitleStatus status;
    uint32_t total_size;
    uint32_t server_size;
    int file_count;
    uint32_t hash_total_size;
    int hash_file_count;
    char server_hash[65];
} TitleInfo;

typedef struct {
    TitleInfo titles[MAX_TITLES];
    int num_titles;

    char server_url[256];
    char api_key[128];
    char console_id[32];
    char ps3_user[16];
    char savedata_root[PATH_LEN];  /* active user savedata dir */
    int  selected_user;            /* 1-16; 0 = auto-detect */

    bool scan_ps3;
    bool scan_ps1;
    bool network_connected;
} SyncState;

/* Global callback pumped during long operations (zlib, SHA-256, file I/O)
 * to prevent the PS3 Lv2 kernel from force-killing the app.
 * Set once in main.c; modules call pump_callbacks() at strategic points. */
typedef void (*PumpCallbackFn)(void);
extern PumpCallbackFn g_pump_callback;

static inline void pump_callbacks(void) {
    if (g_pump_callback) g_pump_callback();
}

#endif /* PS3SYNC_COMMON_H */
