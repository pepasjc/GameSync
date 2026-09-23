#ifndef WIIUSYNC_DOWNLOADS_H
#define WIIUSYNC_DOWNLOADS_H

#include "common.h"
#include "roms.h"

/*
 * Pause/resume download manager — flat key=value file under the app data dir,
 * in-RAM mirror, atomic save via tmp+rename.  Same shape as the PS2/PSP/GC
 * clients, with two Wii U additions:
 *
 *   url_path  explicit server path.  Wii WBFS split parts live at
 *             /api/v1/roms/{id}/wbfs/{file} and can't be addressed by rom_id
 *             alone, so a queued part carries its own path.
 *   install   post-completion action.  GC ISOs land on a staging name and are
 *             moved to <games>/<GAMEID6>/game.iso once the header is readable.
 */

#define DOWNLOAD_MAX        128
#define DOWNLOAD_KEY_LEN    192
#define DOWNLOAD_NAME_LEN   128
#define DOWNLOAD_PATH_LEN   384
#define DOWNLOAD_URL_LEN    256
#define DOWNLOAD_STATUS_LEN 16

typedef enum {
    DL_STATUS_QUEUED    = 0,
    DL_STATUS_ACTIVE    = 1,
    DL_STATUS_PAUSED    = 2,
    DL_STATUS_COMPLETED = 3,
    DL_STATUS_ERROR     = 4,
} DownloadStatus;

typedef enum {
    DL_INSTALL_NONE   = 0,
    DL_INSTALL_GC_ISO = 1,   /* move to <games>/<GAMEID6>/game.iso */
} DownloadInstall;

typedef struct {
    char     key[DOWNLOAD_KEY_LEN];      /* rom_id, or "<rom_id>#<part>" */
    char     rom_id[ROM_ID_LEN];
    char     url_path[DOWNLOAD_URL_LEN]; /* "" = derive from rom_id + extract */
    char     filename[160];
    char     name[DOWNLOAD_NAME_LEN];
    char     target_path[DOWNLOAD_PATH_LEN];
    char     system[8];
    uint64_t offset;
    uint64_t total;
    DownloadStatus  status;
    DownloadInstall install;
    char     extract_format[8];
} DownloadEntry;

typedef struct {
    DownloadEntry items[DOWNLOAD_MAX];
    int           count;
} DownloadList;

const char *downloads_status_to_str(DownloadStatus s);
DownloadStatus downloads_str_to_status(const char *s);

bool downloads_load(DownloadList *list);
bool downloads_save(const DownloadList *list);

DownloadEntry *downloads_find(DownloadList *list, const char *key);

/* Queue a GameCube ROM: staging target + GC-ISO install action. */
DownloadEntry *downloads_upsert_gc(DownloadList *list, const RomEntry *rom);

/* Queue one split WBFS part of a Wii ROM. */
DownloadEntry *downloads_upsert_wbfs_part(DownloadList *list,
                                          const RomEntry *rom,
                                          const char *part_name,
                                          uint64_t part_size,
                                          const char *game_dir);

/* Queue one file of a Wii U WUP bundle (.app / .h3 / title.*).  ``game_dir``
 * is the per-title folder under <install>/; the file keeps its bundle-relative
 * name so the finished folder is exactly what MCP expects. */
DownloadEntry *downloads_upsert_wup_file(DownloadList *list,
                                         const RomEntry *rom,
                                         const char *rel_name,
                                         uint64_t file_size,
                                         const char *game_dir);

bool downloads_remove(DownloadList *list, const char *key);
DownloadEntry *downloads_next_runnable(DownloadList *list);

#endif /* WIIUSYNC_DOWNLOADS_H */
