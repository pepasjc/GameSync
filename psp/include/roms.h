/*
 * roms.h — ROM catalog + target-path policy for the PSP client.
 *
 * Mirrors the PS3 client's roms.h almost 1:1 — same JSON shape, same
 * pagination contract — but with PSP-targeted routing rules:
 *
 *   PSP CHD/ISO/CSO  → ms0:/ISO/<filename>           (server is asked to
 *                                                    convert CHD to CSO)
 *   PS1 (any)        → ms0:/PSP/GAME/<gameid>/EBOOT.PBP
 *                                                   (server pre-converts
 *                                                    via popstation)
 *
 * Both routes use the existing extract_format flow on the server
 * (?extract=cso for PSP, ?extract=eboot for PS1) so the PSP client
 * downloads a single output file rather than a multi-file bundle.
 */

#ifndef PSPSYNC_ROMS_H
#define PSPSYNC_ROMS_H

#include "common.h"

#include <stdbool.h>
#include <stdint.h>

/* Catalog cap.  Sized for real-world libraries (PS1 Redump full set is
 * ~3000 titles, PSP libraries ~1500).  Each RomEntry is ~420 B so 4096
 * = ~1.7 MB BSS.  PSP slim has 64 MB total, base has 32 MB; even on a
 * base PSP this is comfortable next to the 16 MB heap. */
#define ROM_CATALOG_MAX 4096

/* Max length of a server-side rom_id.  Look like ``PS1_BUNDLE_xxx`` or
 * ``PSP_BLUS30443`` — 96 covers everything. */
#define ROM_ID_LEN 96

#define ROM_BUNDLE_FILE_MAX 32

typedef struct {
    char     name[160];          /* relative path inside the bundle */
    uint64_t size;
} RomBundleFile;

typedef struct {
    char     rom_id[ROM_ID_LEN];
    char     filename[160];
    char     name[MAX_TITLE_LEN];
    char     system[8];          /* "PSP" / "PS1" */
    uint64_t size;
    bool     is_bundle;
    int      file_count;
    /* Server-advertised conversion hint.  PSP catalog rows that are
     * CHDs come back with extract_format="psp" + extract_formats=
     * ["iso","cso"], so the client always picks "cso" to save space.
     * PS1 rows advertise "eboot" so the client downloads an EBOOT.PBP. */
    char     extract_format[8];
    /* Multi-disc PS1 grouping (server-computed).  Multi-disc games
     * arrive as N rows sharing ``title_id``; the server tags each row
     * with its disc index (1-based) and the total disc count.  We keep
     * disc 1 (so the UI can show "Final Fantasy VII (3 discs)") and
     * drop disc 2+ at parse time — a single multi-disc EBOOT.PBP is
     * generated when the user picks disc 1, and POPS handles in-game
     * disc swapping.  Single-disc games get (1, 1). */
    int      disc_index;
    int      disc_total;
} RomEntry;

typedef struct {
    RomEntry items[ROM_CATALOG_MAX];
    int      count;
    char     last_error[128];
} RomCatalog;

typedef struct {
    RomBundleFile files[ROM_BUNDLE_FILE_MAX];
    int           count;
    uint64_t      total_size;
    char          last_error[128];
} RomBundleManifest;

/* Fetch + parse a system catalog.  Loops paged requests of 500 entries
 * each until ``has_more=false`` or ROM_CATALOG_MAX is hit. */
bool roms_fetch_catalog(const SyncState *state,
                        const char *system_code,
                        char *scratch_buf, uint32_t scratch_buf_size,
                        RomCatalog *catalog);

/* Pick the ``?extract=`` value the PSP client should request for a
 * catalog row (e.g. PS1 → "eboot", PSP CHD → "cso").  Returns the
 * server-advertised extract_format if set, with the special-case that
 * PSP CHDs always pick "cso" over the default "iso" so the PSP saves
 * Memory Stick space.  Empty string = raw download (no extract). */
const char *roms_preferred_extract_format(const RomEntry *rom);

/* Compute on-disk target path for a single-file ROM entry.
 *
 *   PSP (any)  → ms0:/ISO/<filename>     (.cso, .iso, .pbp)
 *   PS1 (any)  → ms0:/PSP/GAME/<gameid>/EBOOT.PBP
 *   else       → ms0:/PSP/GAME/pspsync/downloads/<filename>
 *
 * For PS1 entries the gameid is derived from the disc serial in the
 * filename / catalog name; falls back to the rom_id slug if no serial
 * is detectable.  Returns false if out_size is too small. */
bool roms_resolve_target_path(const RomEntry *rom,
                              char *out_path, size_t out_size);

/* Per-file routing inside a bundle.  Currently only meaningful for
 * future PSP catalog bundles (none today); included for parity with
 * the PS3 client and for later extension. */
bool roms_resolve_bundle_file_target(const char *system,
                                     const char *game_name,
                                     const char *bundle_file_name,
                                     char *out_path, size_t out_size);

/* Manifest fetch — used when an entry is a bundle. */
bool roms_fetch_bundle_manifest(const SyncState *state,
                                const char *rom_id,
                                char *scratch_buf, uint32_t scratch_buf_size,
                                RomBundleManifest *manifest);

/* Make sure the ROM target dirs exist on the Memory Stick. */
void roms_ensure_target_dirs(void);

/* Recursive mkdir helper — exposed so main.c can pre-create the
 * per-game ms0:/PSP/GAME/<id>/ directory before opening a .part file
 * inside it. */
void roms_mkdir_p(const char *path);

#endif /* PSPSYNC_ROMS_H */
