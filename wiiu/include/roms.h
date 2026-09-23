#ifndef WIIUSYNC_ROMS_H
#define WIIUSYNC_ROMS_H

#include "common.h"

/*
 * ROM catalog client.  Three systems are downloadable on a Wii U:
 *
 *   GC   — Nintendont layout:  <root>/games/<GAMEID6>/game.iso
 *          The catalog does not expose the disc's game code, so the download
 *          lands on a temporary name and is moved into place once the ISO
 *          header is readable (see roms_gameid_from_iso).
 *
 *   WII  — USB-loader layout:  <root>/wbfs/<Name> [ID6]/<ID6>.wbfs (+ .wbf1)
 *          The server converts RVZ -> ISO -> split WBFS and advertises the
 *          parts through /roms/{id}/wbfs-manifest; each part becomes its own
 *          resumable download entry.
 *
 *   WIIU — WUP layout:         <root>/install/<Name>/{*.app,*.h3,title.*}
 *          Server-side these are bundle entries; each file is fetched
 *          individually through /roms/{id}/file/<rel> so a 15 GB title never
 *          has to be zipped or unzipped on the console.  Once complete the
 *          folder is handed to MCP (see install.h), which writes it to NAND
 *          or the console's WFS USB drive.
 *
 * <root> is the SD card or a FAT32 USB drive, per ``rom_storage`` in the
 * config — see roms_storage_root().  App data always stays on SD.
 */

#define ROM_CATALOG_MAX 2048
#define ROM_ID_LEN      96

/* A Wii U game has at most one update and one DLC title in practice; 4 leaves
 * room for a demo and a second DLC without growing RomEntry much (the catalog
 * array is 2048 entries, so every byte here costs 2 KB). */
#define WIIU_RELATED_MAX 4

typedef struct {
    char     rom_id[ROM_ID_LEN];
    char     filename[160];
    char     name[MAX_TITLE_LEN];
    char     system[8];          /* "GC" / "WII" / "WIIU" */
    uint64_t size;
    /* Server extract hint. RVZ entries advertise a format we pass back as
     * ?extract=<fmt> to get an ISO. Empty = raw download. */
    char     extract_format[8];
    /* Bundle entries (Wii U WUP folders) are downloaded file-by-file rather
     * than as one blob — see network_fetch_bundle_manifest. */
    bool     is_bundle;
    /* Wii U grouping.  A game's update (0005000E...) and DLC (0005000C...)
     * are separate titles sharing the base game's low word; the server tags
     * each row and lists the others so one action covers the whole set.
     * ``content_type`` is "game"/"update"/"dlc"/"demo", empty otherwise. */
    char     content_type[8];
    char     related[WIIU_RELATED_MAX][ROM_ID_LEN];
    int      related_count;
} RomEntry;

typedef struct {
    RomEntry items[ROM_CATALOG_MAX];
    int      count;
    char     system[8];          /* the system this catalog was fetched for */
    char     last_error[160];
} RomCatalog;

bool roms_fetch_catalog(const SyncState *state,
                        const char *system_code,
                        char *scratch_buf, uint32_t scratch_buf_size,
                        RomCatalog *catalog);

/* ?extract= value for this entry (whatever the server advertised). */
const char *roms_preferred_extract_format(const RomEntry *rom);

/* Target/storage config (set at boot and whenever the folders change). */
void roms_set_target(const SyncState *state);
const char *roms_downloads_file(void);

/* Root that game downloads are written under: the SD card, or "usb:" when
 * the user picked USB storage AND a FAT32 drive mounted.  Selecting USB with
 * no drive attached silently stays on SD — roms_storage_is_usb() reports
 * which one is actually in effect so the UI can say so. */
const char *roms_storage_root(void);
bool roms_storage_is_usb(void);

const char *roms_games_dir(char *out, size_t out_size);   /* "<root>/games"   */
const char *roms_wbfs_dir(char *out, size_t out_size);    /* "<root>/wbfs"    */
const char *roms_install_dir(char *out, size_t out_size); /* "<root>/install" */
void roms_ensure_target_dirs(void);
void roms_mkdir_p(const char *path);

/* Per-title WUP staging folder: <install>/<TITLE_ID>.
 * Named after the id (not the game name) to match NUSspli / WUP Installer —
 * MCP would not install our game-name folders, whose paths carried spaces and
 * parentheses, while byte-identical id-named sets installed fine. */
void roms_wup_game_dir(const char *title_id, const char *name,
                       char *out, size_t out_size);

/* True for a bare 16-hex Wii U title id ("0005000010110100"). */
bool roms_is_wiiu_title_id(const char *s);

/* True when ``dir`` holds a title.tmd — i.e. a complete WUP set ready for
 * MCP.  A folder mid-download does not have one yet. */
bool roms_is_wup_dir(const char *dir);

/* Staging path for a GC download: <games>/_dl/<sanitised rom_id>.iso.
 * roms_install_gc_iso() moves it to <games>/<GAMEID6>/game.iso afterwards. */
bool roms_resolve_gc_staging_path(const RomEntry *rom, char *out, size_t out_size);

/* Read the 6-char game id from a GameCube ISO header (bytes 0-5), verifying
 * the 0xC2339F3D magic at 0x1C.  Returns true on success. */
bool roms_gameid_from_iso(const char *iso_path, char *out_id6, size_t out_size);

/* Move a completed staging ISO into Nintendont's <games>/<GAMEID6>/game.iso.
 * ``msg`` receives a result line. Returns 0 on success. */
int  roms_install_gc_iso(const char *staging_path, char *msg, size_t msg_size);

/* Directory a Wii game's split WBFS parts belong in:
 * <wbfs>/<Name> [<ID6>].  Created on demand. */
void roms_wbfs_game_dir(const char *name, const char *id6,
                        char *out, size_t out_size);

/* ---- installed games on SD ---- */

#define LOCAL_ROMS_MAX 1024

typedef struct {
    char     name[MAX_TITLE_LEN];
    char     filename[200];
    char     path[SAVE_DIR_LEN];
    char     system[8];
    /* Wii U only: the staged title id, kept separately so the display name
     * can be replaced with the catalog's without losing the identifier the
     * install path and ordering depend on. */
    char     title_id[24];
    uint64_t size;
} LocalRom;

typedef struct {
    LocalRom items[LOCAL_ROMS_MAX];
    int      count;
    char     last_error[160];
} LocalRomList;

/* Walk <games>/<GAMEID6>/game.iso (Nintendont), the .wbfs files under
 * <wbfs>/<dir>/, and the WUP folders under <install>/<dir>/. */
void roms_scan_local(LocalRomList *out);

/* Only the WUP folders under <install>/ — what the install view lists. */
void roms_scan_installable(LocalRomList *out);

#endif /* WIIUSYNC_ROMS_H */
