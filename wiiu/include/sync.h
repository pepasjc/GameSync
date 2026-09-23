#ifndef WIIUSYNC_SYNC_H
#define WIIUSYNC_SYNC_H

#include "common.h"
#include "savetree.h"
#include "wiiunet.h"

/*
 * Three-way-hash smart sync for the vWii / Wii U save trees, ported from the
 * Xbox client.  GameCube saves do NOT go through here — they use the explicit
 * per-save raw endpoints in gcsaves.c.
 */

typedef enum {
    TITLE_STATUS_UNKNOWN = 0,
    TITLE_STATUS_UP_TO_DATE,
    TITLE_STATUS_NEEDS_UPLOAD,
    TITLE_STATUS_NEEDS_DOWNLOAD,
    TITLE_STATUS_CONFLICT,
    TITLE_STATUS_SERVER_ONLY,
} TitleStatus;

typedef struct {
    int uploaded;
    int downloaded;
    int conflicts;
    int up_to_date;
    int upload_failed;
    int download_failed;
} SyncSummary;

char sync_status_glyph(TitleStatus s);
const char *sync_status_text(TitleStatus s);

TitleStatus sync_plan_status(const SyncPlan *p, const char *title_id);

/* Hash all local titles and ask the server for a plan.  Caller releases with
 * sync_plan_free(). */
int sync_compute_plan(const SyncState *state,
                      const SaveTitleList *list,
                      const char *const *platforms, int platform_count,
                      SyncPlan *out_plan);

int sync_one_upload(const SyncState *state, SaveTitle *t);
int sync_one_upload_force(const SyncState *state, SaveTitle *t);
int sync_one_download(const SyncState *state, SaveTitleList *list,
                      const char *title_id);

/* Act on the cached plan for one title. */
int sync_one_smart(const SyncState *state, SaveTitleList *list,
                   const char *title_id, const SyncPlan *plan);

/* Progress callback fired between titles so the UI can repaint. */
typedef void (*SyncProgressFn)(const char *msg, int done, int total, void *user);

/* Run the full plan: upload everything in `upload`, download everything in
 * `download` and `server_only`, skip conflicts. */
int sync_run_all(const SyncState *state,
                 SaveTitleList *list,
                 const SyncPlan *plan,
                 SyncProgressFn cb,
                 void *user,
                 SyncSummary *out);

/* Result line from the last sync_* call (backup path, HTTP error, ...). */
const char *sync_last_message(void);

#endif /* WIIUSYNC_SYNC_H */
