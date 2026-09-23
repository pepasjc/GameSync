#ifndef WIIUSYNC_GCSAVES_H
#define WIIUSYNC_GCSAVES_H

#include "common.h"
#include "vmcfs.h"

#include <stddef.h>
#include <stdint.h>

/*
 * GameCube save sync through Nintendont's virtual memory cards.
 *
 * Nintendont keeps standard GC card images on SD under sd:/saves — either one
 * per game ("GALE.raw") or a shared "ninmem.raw" — so the same vmcfs
 * parser and the same server endpoints as the GameCube client apply:
 *
 *   POST /api/v1/saves/GC_<code>/gc-card?format=gci   — body = GCI
 *   GET  /api/v1/saves/GC_<code>/gc-card?format=gci   — returns the GCI
 *   POST /api/v1/saves/gc-vmc/import                  — whole card image
 *
 * A save written here round-trips to the GameCube client, Dolphin and the
 * Android app unchanged.
 */

#define GC_BLOCK_SIZE  8192
#define GCSAVES_GCI_MAX (2 * 1024 * 1024)   /* per-save GCI cap */

/* ---- card images found on SD ---- */

#define GCSAVES_MAX_CARDS 64

typedef struct {
    char     path[SAVE_DIR_LEN];
    char     filename[128];
    uint32_t size;
} SaveVmc;

typedef struct {
    SaveVmc items[GCSAVES_MAX_CARDS];
    int     count;
    char    last_error[160];
} SaveVmcList;

/* Scan the configured Nintendont saves folder (plus a couple of conventional
 * fallbacks) for anything that passes the card-image size gate. */
void gcsaves_scan_cards(const SyncState *state, SaveVmcList *out);

/* ---- per-save transfer ---- */

/* Upload one save out of an opened card image. */
int  gcsaves_upload_save(const SyncState *state, VmcfsCard *card, int idx,
                         char *msg, size_t msg_size);

/* Download a game's GCI from the server and write it into the card image. */
int  gcsaves_restore_save(const SyncState *state, VmcfsCard *card,
                          const char *title_id, char *msg, size_t msg_size);

/* Upload one whole card image to POST /saves/gc-vmc/import (the server splits
 * it into per-game saves). */
int  gcsaves_import_card(const SyncState *state, const SaveVmc *vmc,
                         char *msg, size_t msg_size);

/* ---- server saves ---- */

#define GCSAVES_MAX_SERVER 512

typedef struct {
    char     title_id[16];    /* "GC_GZLE" */
    char     name[64];        /* game name (or title_id when unknown) */
    uint32_t timestamp;
    bool     local;           /* present in a scanned card image */
} ServerSave;

typedef struct {
    ServerSave items[GCSAVES_MAX_SERVER];
    int        count;
    char       last_error[160];
} ServerSaveList;

/* GET /titles?console_type=GC */
void gcsaves_fetch_server(const SyncState *state,
                          char *scratch, uint32_t scratch_size,
                          ServerSaveList *out);

/* Download every server GC save as a GCI into <sd>/3dssync/gci/<title_id>.gci.
 * Returns the number pulled, or negative on error. */
int  gcsaves_pull_all(const SyncState *state, const ServerSaveList *server,
                      char *msg, size_t msg_size);

/* Build "GC_<gamecode>" from a 4-char game code (also used by vmcfs.c). */
void saves_title_id_from_gamecode(const char *gamecode, char *out, size_t out_size);

#endif /* WIIUSYNC_GCSAVES_H */
