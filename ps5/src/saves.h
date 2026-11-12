/*
 * GameSync PS5 client - the emulator saves on this console.
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
#ifndef GAMESYNC_SAVES_H
#define GAMESYNC_SAVES_H

#include "bundle.h"
#include "config.h"
#include "naming.h"

#include <stdbool.h>
#include <stdint.h>

typedef enum
{
    SAVE_SRM,        /* RetroArch save RAM, synced through /saves/{id}/raw */
    SAVE_PS1_CARD,   /* one 128 KB PS1 card per game, /saves/{id}/ps1-card */
    SAVE_PS2_FOLDER, /* one folder on a shared PS2 card, /saves/{id}/ps2-files */
    SAVE_GCI,        /* a Dolphin GameCube .gci, /saves/{id}/gc-card?format=gci */
    SAVE_FOLDER,     /* a save folder (Wii, Switch) as a 3DSS bundle, /saves/{id} */
    SAVE_XBOX,       /* E:\UDATA\<id> inside XPSemu's disk image (path), a 3DSS bundle */
} SaveKind;

typedef enum
{
    ST_UNKNOWN,     /* not compared with the server yet */
    ST_SYNCED,
    ST_UPLOAD,      /* only this console changed it */
    ST_DOWNLOAD,    /* only the server changed it */
    ST_CONFLICT,    /* both changed, or never synced and they differ */
    ST_LOCAL_ONLY,  /* not on the server yet: upload */
    ST_SERVER_ONLY, /* not here yet: download */
    ST_NONE,        /* neither side has a save (a ROM without one) */
    ST_ERROR,
} SaveStatus;

typedef struct
{
    Emulator emu;
    SaveKind kind;
    char system[12];
    char title_id[64];
    char name[128];
    char rom_file[256];     /* RetroArch: the ROM's file name (its id comes from it); PS5SX2: the game file's stem */
    char path[PATH_LEN];    /* .srm / .mcd / the shared .ps2 card */
    char ps2_dir[33];       /* SAVE_PS2_FOLDER: the folder on the card */
    bool path_is_dir;       /* SAVE_GCI not here yet: path is the card folder it goes to */
    bool local;
    char local_hash[65];
    uint64_t local_size;
    bool server;
    char server_hash[65];
    uint64_t server_size;
    bool installed;         /* the game came from the catalog (installed.txt) */
    SaveStatus status;
    char message[96];
} SaveEntry;

typedef struct
{
    SaveEntry *items;
    int count;
    int cap;
    int ps2_cards;          /* PS5SX2 cards looked at */
    char warning[160];      /* a problem worth showing, "" if none */
} SaveList;

void saves_free(SaveList *list);
SaveEntry *saves_add(SaveList *list);
SaveEntry *saves_find(SaveList *list, const char *title_id);

/* Scans every emulator's folders. RetroArch entries without a cached title id
 * keep title_id "" until sync_resolve_ids() fills them. */
void saves_scan(const Config *config, const KvStore *ids, const KvStore *installed,
                SaveList *list);

/* Recomputes local_hash/local_size/local for one entry. */
void saves_hash_local(SaveEntry *entry);

/* The biggest Xbox save read out of XPSemu's disk (it is held in memory). */
#define XBOX_SAVE_MAX (128ull << 20)
/* An Xbox save's files out of XPSemu's disk image, in bundle order. */
bool saves_xbox_tree(const SaveEntry *entry, SaveTree *tree, char *err, size_t err_size);

/* ProsperoEden's save folder for the active profile:
 * <eden>/user/nand/user/save/0000000000000000/<USER ID>; false when no
 * profile is known yet (no game started). */
bool saves_eden_user_dir(const Config *config, char *out, size_t size);
/* Porpoise's GameCube card folder for a game code's region (code[3]). */
void saves_porpoise_card_dir(const Config *config, const char *code, char *out, size_t size);
/* PS5CEMU's save folder for a Wii U (00050000...) or 3DS (00040000...) title
 * id: Cemu's mlc user/ folder or Azahar's data/00000001; false otherwise. */
bool saves_cemu_dir(const Config *config, const char *title_id, char *out, size_t size);
/* A PSP save slot folder name (ULUS10551DATA00): a product code, then
 * letters and digits; not a PS1 disc prefix (a PSone Classic). */
bool saves_is_psp_slot(const char *name);
/* Porpoise's Wii save folder for a WII_<CODE> id; false for a bad id. */
bool saves_porpoise_wii_dir(const Config *config, const char *title_id, char *out, size_t size);


/* SwanStationPS5's Memory Card 1 Type (core option
 * swanstation_MemoryCards_Card1Type), which decides the card's file name. */
typedef enum
{
    SS_CARD_LIBRETRO, /* default: <serial>_1.mcd, or <game file>_1.mcd without a serial */
    SS_CARD_SERIAL,   /* PerGame: <serial>_1.mcd */
    SS_CARD_TITLE,    /* PerGameTitle: <game title>_1.mcd */
    SS_CARD_SHARED,   /* one card for every game: not synced */
    SS_CARD_NONE,
} SsCardMode;

typedef struct
{
    char user_dir[PATH_LEN];  /* the active profile's folder */
    char saves_dir[PATH_LEN]; /* its saves/ */
    SsCardMode mode;          /* the profile's setting */
} SsInfo;

void saves_swanstation_info(const Config *config, SsInfo *info);
/* The mode a game runs with: its game-settings/<serial>.ini, else the profile's. */
SsCardMode saves_swanstation_mode_for(const SsInfo *info, const char *serial);
const char *saves_swanstation_mode_name(SsCardMode mode);
/* Where SwanStation will look for this game's card. game_stem is the game
 * file's name without extension (what the title mode names it after), or NULL. */
/* A card named after one disc of a multi-disc game, <from_stem>_1.mcd, moved
 * to the name SwanStation's title mode reads for the game's playlist,
 * <to_stem>_1.mcd (a copy of it set aside as .bak; two different cards are
 * left alone). True when something moved. */
bool saves_swanstation_rename_card(const Config *config, const char *from_stem, const char *to_stem);
void saves_swanstation_card_path(const Config *config, const char *serial, const char *game_stem,
                                 char *out, size_t size);

/* PS5SX2 per-game cards (ps2_cards=per_game). stem is the game file's name
 * without extension. The game's card is <card folder>/<card> where <card> is what
 * settings/<stem>.ini sets as MemoryCards/Slot1_Filename, or <stem>.ps2 when
 * that's unset or a shared card (written into the ini when assign). */
/* PS5SX2's card folder: <dir>/memcards, or <dir> when cards sit there and
 * memcards/ doesn't exist; memcards/ is made when create and neither has any. */
void saves_ps5sx2_card_dir(const Config *config, bool create, char *out, size_t size);
bool saves_ps5sx2_game_card(const Config *config, const char *stem, bool assign, char *out,
                            size_t size);
/* PS5SX2's own record of a game image's serial (its chd-serials.txt cache),
 * compact ("SLUS20488"); and the other way, the stem of the game file in
 * that cache with a serial. false when PS5SX2 hasn't read the game yet. */
bool saves_ps5sx2_serial(const Config *config, const char *game_path, char *out, size_t size);
bool saves_ps5sx2_game_for_serial(const Config *config, const char *serial, char *stem,
                                  size_t size);
/* Gives an installed game its own card: assigned in its settings, made when
 * missing, and holding its save (serial) copied from a shared card when it
 * has none yet. True when something changed. */
bool saves_ps5sx2_setup_game(const Config *config, const char *stem, const char *serial);

/* After a card is replaced: sets aside the game's quick-resume state
 * (<user>/states/<game id>.resume), which carries the old card and would
 * load it over the new one. Returns how many were moved. */
int saves_swanstation_drop_resume(const Config *config, const char *card_path, const char *serial);

typedef enum
{
    SS_FIX_NONE,        /* nothing to do */
    SS_FIX_CHANGED,     /* a card was renamed / set aside, or serial.txt written */
    SS_FIX_NEED_SERVER, /* two different cards: call again with the server's hash */
    SS_FIX_CONFLICT,    /* two different cards, neither the server's: left alone */
} SsFixResult;

/* For a PS1 game (its file, and its serial from the catalog): makes the card
 * carry the name SwanStation's current mode reads - writing serial.txt in
 * Libretro mode, renaming a card filed under the other name, or setting
 * aside a copy of the card in use or of the server's save. server_hash is
 * the server's ps1-card hash, or NULL when not fetched yet. */
SsFixResult saves_swanstation_fix(const Config *config, const char *game_path, const char *serial,
                                  const char *server_hash);

const char *save_status_text(SaveStatus status);

#endif
