// Save enumeration on the Xbox HDD.
//
// Original Xbox stores game saves under:
//   E:\UDATA\<TitleID>\<SaveName>\<files...>
//
// where <TitleID> is the 8-char hex Title ID baked into the XBE certificate
// (and the directory under UDATA on the FATX partition). A single title can
// have multiple <SaveName> subdirectories - these are slot-style save folders
// (e.g. "Halo Profile", "AutoSave", etc.). Each contains arbitrary game-
// specific files plus the standard SaveImage.xbx / SaveMeta.xbx pair.
//
// This module bundles the entire <TitleID> directory tree as one logical
// "save" so the server stores the full multi-slot state atomically - same
// granularity the 3DS and PS3 clients use.

#pragma once

#include <stdint.h>

#define XBOX_TITLE_ID_LEN          8
#define XBOX_PATH_MAX              260   // Win32 MAX_PATH
#define XBOX_MAX_TITLES            256
#define XBOX_MAX_FILES_PER_TITLE   128

typedef struct {
    /* path relative to E:\UDATA\<title_id>\  (use C-style comment so the
       trailing backslash does not trigger C99 line-continuation that would
       swallow the next line). */
    char     relative_path[XBOX_PATH_MAX];
    uint32_t file_size;  // truncated; saves >4 GiB are not realistic
} XboxSaveFile;

#define XBOX_NAME_MAX 64

typedef struct {
    char         title_id[XBOX_TITLE_ID_LEN + 1]; // 8 hex chars (uppercase) + NUL
    char         name[XBOX_NAME_MAX];             // resolved game name; "" if unknown
    int          file_count;
    uint32_t     total_size;
    XboxSaveFile files[XBOX_MAX_FILES_PER_TITLE];
} XboxSaveTitle;

typedef struct {
    int           title_count;
    int           total_files;
    uint32_t      total_bytes;
    XboxSaveTitle titles[XBOX_MAX_TITLES];
} XboxSaveList;

// Mount E: drive if not already mounted. Returns 0 on success, negative on
// failure (HDD not present, partition unreadable, etc.).
int saves_init(void);

// Returns nonzero for retail-style original Xbox game Title IDs. Known game
// IDs are 8 hex chars whose first two bytes are uppercase ASCII publisher
// letters (e.g. 4D530064 == "MS..."). This excludes dashboard/system junk
// such as 00000000 and FEFEFEFE.
int saves_is_game_title_id(const char *name);

// Enumerate every UDATA\<TitleID> directory and recursively walk its contents.
// Filters out non-game directory names so reserved/system entries are skipped.
// Returns the number of titles populated in ``out`` (also stored in
// ``out->title_count``). Files in excess of XBOX_MAX_FILES_PER_TITLE are
// silently dropped - bump the limit if real saves push past it.
int saves_scan(XboxSaveList *out);
