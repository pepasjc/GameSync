/*
 * downloads.h — pause/resume download manager.
 *
 * Persists per-rom download state across sessions in a key-value text
 * file (DOWNLOADS_FILE), mirroring the format used by state.c so we don't
 * need a second persistence engine.  One active download at a time —
 * other entries sit in the queue with status QUEUED until promoted.
 *
 * Schema (one entry per line in DOWNLOADS_FILE):
 *
 *   <rom_id>=<status>|<offset>|<total>|<filename>|<target_path>|<system>|<name>
 *
 *   status   downloads_status_to_str() / downloads_str_to_status()
 *   offset   bytes already on disk in <target_path>.part
 *   total    full size in bytes (0 = unknown until first connect)
 *   *path*   resolved target path (e.g. /dev_hdd0/PS3ISO/Foo.iso)
 *   *name*   server-canonical name for UI display (may contain spaces)
 *
 * Pipe (`|`) is used as the field separator because filenames + names can
 * contain '=' and ':' but virtually never a literal pipe.
 */

#ifndef PS3SYNC_DOWNLOADS_H
#define PS3SYNC_DOWNLOADS_H

#include "common.h"
#include "roms.h"

#include <stdbool.h>
#include <stdint.h>

#define DOWNLOAD_STATUS_LEN 16
#define DOWNLOAD_MAX 128            /* matches state.c capacity */
#define DOWNLOAD_NAME_LEN 96
#define DOWNLOAD_PATH_LEN PATH_LEN

typedef enum {
    DL_STATUS_QUEUED    = 0,    /* waiting for user to start it */
    DL_STATUS_ACTIVE    = 1,    /* in flight (transient, only set in RAM) */
    DL_STATUS_PAUSED    = 2,    /* user-cancelled mid-flight, .part on disk */
    DL_STATUS_COMPLETED = 3,    /* finished — kept around so user can purge */
    DL_STATUS_ERROR     = 4,    /* network/disk error — retry from offset */
} DownloadStatus;

typedef struct {
    char     rom_id[ROM_ID_LEN];
    char     filename[160];
    char     name[DOWNLOAD_NAME_LEN];
    char     target_path[DOWNLOAD_PATH_LEN];
    char     system[8];
    uint64_t offset;
    uint64_t total;
    DownloadStatus status;
    /* Bundle multi-file state.  ``is_bundle`` flips run_download() into
     * the per-file loop; ``bundle_index`` records how many files of the
     * bundle are already on disk so a paused bundle resumes mid-list
     * rather than restarting at file 0.  ``bundle_count`` is read from
     * the manifest at start time and persisted for the UI. */
    bool     is_bundle;
    int      bundle_index;
    int      bundle_count;
} DownloadEntry;

typedef struct {
    DownloadEntry items[DOWNLOAD_MAX];
    int           count;
} DownloadList;

/* Status string conversion (used for the on-disk schema). */
const char *downloads_status_to_str(DownloadStatus s);
DownloadStatus downloads_str_to_status(const char *s);

/* Load every line of DOWNLOADS_FILE into ``list``.  Missing file → empty
 * list.  Malformed lines are skipped with a debug_log() entry.  Reconciles
 * each entry's offset against the on-disk .part size so a crash mid-write
 * is detected and the offset rolled back. */
bool downloads_load(DownloadList *list);

/* Persist the entire list back to DOWNLOADS_FILE (atomic via rename to
 * avoid leaving a partially-written file if the PS3 powers off mid-save). */
bool downloads_save(const DownloadList *list);

/* Look up a download by rom_id.  Returns NULL if absent. */
DownloadEntry *downloads_find(DownloadList *list, const char *rom_id);

/* Add or update an entry from a catalog row.  Sets status=QUEUED unless an
 * existing entry has progress (then keep PAUSED).  Returns the upserted
 * entry pointer or NULL if list is full. */
DownloadEntry *downloads_upsert_from_catalog(DownloadList *list,
                                             const RomEntry *rom);

/* Remove an entry by rom_id and its .part file (caller decides whether to
 * persist via downloads_save()). */
bool downloads_remove(DownloadList *list, const char *rom_id);

/* Pick the next runnable entry — first QUEUED, else first PAUSED.  Returns
 * NULL when nothing to do. */
DownloadEntry *downloads_next_runnable(DownloadList *list);

#endif /* PS3SYNC_DOWNLOADS_H */
