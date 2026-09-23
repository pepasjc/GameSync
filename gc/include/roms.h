#ifndef GCSYNC_ROMS_H
#define GCSYNC_ROMS_H

#include "common.h"

/*
 * GameCube ROM catalog client.  Fetches the server catalog (system=GC) and
 * installs ISOs into a single flat folder the loaders read:
 *
 *   sd:/games/<name>.iso        (Swiss / GC Loader / FlippyDrive convention)
 *
 * RVZ sources are requested with ?extract=rvz so the server hands back a
 * mountable .iso (loaders don't all read RVZ).
 */

#define ROM_CATALOG_MAX 2048
#define ROM_ID_LEN      96

typedef struct {
    char     rom_id[ROM_ID_LEN];
    char     filename[160];
    char     name[MAX_TITLE_LEN];
    char     system[8];          /* "GC" */
    uint64_t size;
    /* Server extract hint. RVZ entries advertise a format we pass back as
     * ?extract=<fmt> to get an ISO. Empty = raw download. */
    char     extract_format[8];
} RomEntry;

typedef struct {
    RomEntry items[ROM_CATALOG_MAX];
    int      count;
    char     last_error[128];
} RomCatalog;

bool roms_fetch_catalog(const SyncState *state,
                        const char *system_code,
                        char *scratch_buf, uint32_t scratch_buf_size,
                        RomCatalog *catalog);

/* ?extract= value for this entry (whatever the server advertised). */
const char *roms_preferred_extract_format(const RomEntry *rom);

/* Target/storage config (set once at boot). */
void roms_set_target(const char *sd_root, const char *games_folder);
const char *roms_downloads_file(void);
const char *roms_games_dir(char *out, size_t out_size);   /* "sd:/games" */
void roms_ensure_target_dirs(void);
void roms_mkdir_p(const char *path);

/* On-disk path for an entry: sd:/games/<name>.iso (or original ext if raw). */
bool roms_resolve_target_path(const RomEntry *rom, char *out_path, size_t out_size);

/* Local installed-ISO scan of sd:/games (iso, gcm, rvz, ciso, gcz). */
#define LOCAL_ROMS_MAX 1024

typedef struct {
    char     name[MAX_TITLE_LEN];
    char     filename[200];
    char     path[260];
    uint64_t size;
} LocalRom;

typedef struct {
    LocalRom items[LOCAL_ROMS_MAX];
    int      count;
    char     last_error[128];
} LocalRomList;

void roms_scan_local(LocalRomList *out);

#endif /* GCSYNC_ROMS_H */
