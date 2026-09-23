/*
 * roms.h — ROM catalog + target-path policy for the Vita client.
 *
 * Same JSON shape and pagination contract as the PSP client's roms.h;
 * the routing rules target Adrenaline's PSP tree on the Vita instead
 * of a Memory Stick:
 *
 *   PSP CHD/ISO/CSO  → <pspemu_root>/ISO/<filename>          (CHD is
 *                                                            converted to
 *                                                            CSO server-side)
 *   PS1 (any)        → <pspemu_root>/PSP/GAME/<gameid>/EBOOT.PBP
 *                                                            (server converts
 *                                                            via pop-fe)
 *
 * Both routes ride the server's existing ``?extract=`` flow (cso for
 * PSP, eboot for PS1) so the client always downloads one file.
 * ``pspemu_root`` defaults to ux0:pspemu and comes from config.txt.
 */

#ifndef VITASYNC_ROMS_H
#define VITASYNC_ROMS_H

#include "common.h"

#include <stdbool.h>
#include <stdint.h>

/* Catalog cap.  A full PS1 Redump set is ~3000 titles and PSP ~1500;
 * each RomEntry is ~450 B so 4096 entries is ~1.8 MB of BSS, trivial
 * next to the Vita's default 112 MB app budget. */
#define ROM_CATALOG_MAX 4096

/* Server-side rom_id (``PSP_ULUS10272``, ``PS1_BUNDLE_xxx``). */
#define ROM_ID_LEN 96

typedef struct {
    char     rom_id[ROM_ID_LEN];
    char     filename[160];
    char     name[MAX_TITLE_LEN];
    char     system[8];          /* "PSP" / "PS1" */
    /* Catalog title_id — the disc serial for PS1/PSP (SLUS01279,
     * ULUS10272).  Preferred EBOOT folder name because it matches how
     * the PSP emulator files the game's save and how the server keys
     * it, so a game and its save line up without guessing. */
    char     title_id[GAME_ID_LEN];
    uint64_t size;
    bool     is_bundle;
    int      file_count;
    /* Server-advertised default conversion.  Informational only — the
     * client picks its own format per system (see
     * roms_preferred_extract_format). */
    char     extract_format[8];
    /* Multi-disc PS1 grouping (server-computed, 1-based).  Disc 2+
     * rows are dropped at parse time: downloading disc 1 yields one
     * multi-disc EBOOT.PBP and POPS swaps discs in-game.  Single-disc
     * games get (1, 1). */
    int      disc_index;
    int      disc_total;
} RomEntry;

typedef struct {
    RomEntry items[ROM_CATALOG_MAX];
    int      count;
    char     last_error[128];
} RomCatalog;

/* Fetch + parse a system catalog.  Pages 500 entries at a time until
 * ``has_more=false`` or ROM_CATALOG_MAX is hit. */
bool roms_fetch_catalog(const SyncState *state,
                        const char *system_code,
                        char *scratch_buf, uint32_t scratch_buf_size,
                        RomCatalog *catalog);

/* ``?extract=`` value to request for a catalog row:
 *   PS1  → "eboot"  (always; the raw disc image is useless to POPS)
 *   PSP  → "cso" for CHD sources, "" (raw) for ISO/CSO/PBP already on
 *          disk in a playable shape
 *   else → server hint as-is */
const char *roms_preferred_extract_format(const RomEntry *rom);

/* True when the server can't produce something the PSP emulator can
 * run for this row (PS1 folder bundles have no EBOOT route).  The UI
 * shows these but refuses to queue them. */
bool roms_entry_unsupported(const RomEntry *rom);

/* Set the Adrenaline root (``ux0:pspemu``) that target paths hang off.
 * Call once after config load, before any roms_resolve_* call. */
void roms_set_pspemu_root(const char *root);

/* On-disk target for a single-file ROM entry:
 *   PSP → <root>/ISO/<filename>            (.cso when converting CHD)
 *   PS1 → <root>/PSP/GAME/<gameid>/EBOOT.PBP
 *   else → ux0:data/vitasync/downloads/<filename>
 * <gameid> is the catalog title_id, then a serial found in the
 * filename, then a sanitised name.  Returns false if out_size is too
 * small. */
bool roms_resolve_target_path(const RomEntry *rom,
                              char *out_path, size_t out_size);

/* Make sure the ROM target dirs exist. */
void roms_ensure_target_dirs(void);

/* Recursive mkdir.  Exposed so main.c can create the per-game
 * PSP/GAME/<id>/ folder before opening the .part file inside it. */
void roms_mkdir_p(const char *path);

#endif /* VITASYNC_ROMS_H */
