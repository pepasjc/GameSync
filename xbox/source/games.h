#ifndef XBOX_GAMES_H
#define XBOX_GAMES_H

#include <stdint.h>

#include "catalog_cache.h"
#include "config.h"

#define XBOX_INSTALLED_NAME_MAX 96
#define XBOX_INSTALLED_PATH_MAX 260
#define XBOX_MAX_INSTALLED_GAMES 256

typedef enum {
    XBOX_GAME_FORMAT_CCI = 0,
    XBOX_GAME_FORMAT_FOLDER = 1,
} XboxGameFormat;

typedef struct {
    char     name[XBOX_INSTALLED_NAME_MAX];
    char     path[XBOX_INSTALLED_PATH_MAX];
    uint64_t size;
    uint32_t file_count;
    uint32_t dir_count;
} XboxInstalledGame;

typedef struct {
    int count;
    uint64_t total_size;
    XboxInstalledGame games[XBOX_MAX_INSTALLED_GAMES];
} XboxInstalledGameList;

typedef struct {
    uint64_t free_bytes;
    uint64_t total_bytes;
} XboxDriveSpace;

// Download progress. Return non-zero to cancel the transfer.
typedef int (*GameProgressFn)(const char *msg,
                              uint64_t done,
                              uint64_t total,
                              void *user);

// Where the rows of the last games_load_catalog() came from.
typedef enum {
    CATALOG_FROM_SERVER = 0,     // fetched now (cache refreshed)
    CATALOG_FROM_CACHE,          // fingerprint unchanged, read from disk
    CATALOG_FROM_CACHE_OFFLINE,  // server unreachable, last cached copy
    CATALOG_UNCACHED,            // server has no fingerprints route
} CatalogSource;

typedef struct {
    CatalogSource source;
    // Only set when ``force``: 1 = the server rescanned (rescan_count rows),
    // 0 = the server refused or does not offer a rescan, -1 = it failed.
    int rescan;
    int rescan_count;
} CatalogLoadInfo;

// Catalog fetch progress: rows received so far and the expected total
// (0 when unknown).
typedef void (*CatalogProgressFn)(int loaded, int total, void *user);

// Catalog cache file, next to config.txt.
#define XBOX_CATALOG_CACHE_PATH "E:\\UDATA\\TDSV0000\\catalog_cache.txt"

XboxGameFormat games_config_format(const XboxConfig *cfg);
const char *games_format_name(XboxGameFormat fmt);

int games_mount_target(const XboxConfig *cfg, char *err, int err_len);
// Load the Xbox ROM catalog into ``out`` through the on-disk cache (see
// catalog_cache.h). ``force`` is Settings > Refresh catalog: ask the server
// to rescan, wipe the cache and refetch. On failure ``out`` is untouched.
int games_load_catalog(const XboxConfig *cfg, XboxRomList *out, int force,
                       CatalogProgressFn progress, void *progress_user,
                       CatalogLoadInfo *info, char *err, int err_len);
// Returned by games_download_rom when the progress callback cancelled it.
// A game folder the download created is removed again.
#define GAMES_DOWNLOAD_CANCELLED (-2)

int games_download_rom(const XboxConfig *cfg,
                       const XboxRomEntry *rom,
                       XboxGameFormat fmt,
                       GameProgressFn progress,
                       void *progress_user,
                       char *err,
                       int err_len);
int games_scan_installed(const XboxConfig *cfg,
                         XboxInstalledGameList *out,
                         char *err,
                         int err_len);
int games_uninstall_installed(const XboxConfig *cfg,
                              const XboxInstalledGame *game,
                              char *err,
                              int err_len);
int games_get_f_drive_space(XboxDriveSpace *out, char *err, int err_len);

#endif // XBOX_GAMES_H
