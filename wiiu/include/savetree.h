#ifndef WIIUSYNC_SAVETREE_H
#define WIIUSYNC_SAVETREE_H

#include "common.h"

/*
 * A "save" for the vWii / Wii U families is a whole directory tree:
 *
 *   vWii   storage_slccmpt01:/title/00010000/<tidlo>/data/       (minus nocopy/)
 *   Wii U  storage_mlc01:/usr/save/00050000/<tidlo>/user/        (common/ + <persistentId>/)
 *
 * Both are bundled as one logical save (3DSS v5), the same granularity the
 * 3DS / PS3 / Xbox clients use.  This module is the shared enumeration +
 * ownership layer; vwiisaves.c and wiiusaves.c only supply the roots.
 */

#define SAVE_MAX_TITLES   256
#define SAVE_PATH_MAX     256
#define SAVE_NAME_MAX     96

/* Hard cap so a pathological tree can't exhaust memory. */
#define SAVE_MAX_FILES_PER_TITLE 4096

typedef struct {
    char     relative_path[SAVE_PATH_MAX];   /* relative to SaveTitle.root, '/'-separated */
    uint32_t file_size;
    uint32_t mtime;
} SaveFile;

typedef struct {
    char      title_id[TITLE_ID_LEN];   /* server key */
    char      name[SAVE_NAME_MAX];      /* display name ("" if unknown) */
    /* Server-side name hint, "WIIU_ARDE" form, from meta.xml's
     * <product_code>.  No DAT can name a save from a 16-hex Wii U title id
     * (its low word is not the product code), so without this every client
     * lists the save as raw hex.  Empty for vWii, whose WII_<code> id the
     * server already resolves. */
    char      game_code[16];
    char      root[SAVE_DIR_LEN];       /* absolute dir the relative paths hang off */
    int       file_count;
    int       file_cap;
    uint32_t  total_size;
    uint32_t  latest_mtime;
    SaveFile *files;                    /* heap array, file_count entries */
    /* Non-empty when the tree could not be read.  Such a title is still
     * listed (dropping it silently is how a missing save turns into a
     * mystery) but must never be uploaded — a partial read would overwrite
     * a good server copy with an incomplete one. */
    char      error[80];
} SaveTitle;

typedef struct {
    int       title_count;
    SaveTitle titles[SAVE_MAX_TITLES];
    char      last_error[160];
} SaveTitleList;

/* Recursively enumerate ``title->root`` into ``title->files``.  Directory
 * names listed in ``exclude`` (NULL-terminated array, matched against the
 * first path component) are skipped.  Returns 0 on success. */
int  savetree_scan(SaveTitle *title, const char *const *exclude);

/* Release the heap-allocated file arrays. */
void savetree_free_title(SaveTitle *title);
void savetree_free_list(SaveTitleList *list);

/* Find a title by id.  NULL when absent. */
SaveTitle *savetree_find(SaveTitleList *list, const char *title_id);

/* mkdir -p for an absolute path (all components created). */
int  savetree_mkdir_p(const char *path);

/* Recursive copy of ``src`` dir into ``dst`` dir (used for pre-restore
 * backups).  Returns 0 on success. */
int  savetree_copy_dir(const char *src, const char *dst);

#endif /* WIIUSYNC_SAVETREE_H */
