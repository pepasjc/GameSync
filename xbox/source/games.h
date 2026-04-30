#ifndef XBOX_GAMES_H
#define XBOX_GAMES_H

#include <stdint.h>

#include "config.h"

#define XBOX_ROM_ID_MAX     128
#define XBOX_ROM_NAME_MAX   96
#define XBOX_ROM_FILE_MAX   96
#define XBOX_MAX_ROMS       512

typedef enum {
    XBOX_GAME_FORMAT_CCI = 0,
    XBOX_GAME_FORMAT_FOLDER = 1,
} XboxGameFormat;

typedef struct {
    char     rom_id[XBOX_ROM_ID_MAX];
    char     name[XBOX_ROM_NAME_MAX];
    char     filename[XBOX_ROM_FILE_MAX];
    uint64_t size;
    int      is_bundle;
} XboxRomEntry;

typedef struct {
    int count;
    XboxRomEntry roms[XBOX_MAX_ROMS];
} XboxRomList;

typedef void (*GameProgressFn)(const char *msg,
                               uint64_t done,
                               uint64_t total,
                               void *user);

XboxGameFormat games_config_format(const XboxConfig *cfg);
const char *games_format_name(XboxGameFormat fmt);

int games_mount_target(const XboxConfig *cfg, char *err, int err_len);
int games_fetch_catalog(const XboxConfig *cfg, XboxRomList *out,
                        char *err, int err_len);
int games_download_rom(const XboxConfig *cfg,
                       const XboxRomEntry *rom,
                       XboxGameFormat fmt,
                       GameProgressFn progress,
                       void *progress_user,
                       char *err,
                       int err_len);

#endif // XBOX_GAMES_H
