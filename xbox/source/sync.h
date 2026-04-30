// Sync helpers used by the interactive UI.

#ifndef XBOX_SYNC_H
#define XBOX_SYNC_H

#include "config.h"
#include "network.h"
#include "saves.h"

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

// Single-character glyph for a status (used in list rendering).
char sync_status_glyph(TitleStatus s);

// Lookup a title's status in a fetched plan. Returns TITLE_STATUS_UNKNOWN
// if not present in any of the plan's lists.
TitleStatus sync_plan_status(const SyncPlan *p, const char *title_id);

// Hash all local titles, ask the server for a sync plan, return it. Caller
// must release with sync_plan_free(). On failure returns negative.
int sync_compute_plan(const XboxConfig *cfg,
                      const XboxSaveList *list,
                      SyncPlan *out_plan);

// Sync a single title, choosing the action based on the cached plan
// (smart sync). Returns 0 on success, negative on failure.
int sync_one_smart(const XboxConfig *cfg,
                   XboxSaveList *list,
                   const char *title_id,
                   const SyncPlan *plan);

// Upload a single title.
int sync_one_upload(const XboxConfig *cfg, XboxSaveTitle *t);

// Force-upload a single title after an explicit confirmation prompt.
int sync_one_upload_force(const XboxConfig *cfg, XboxSaveTitle *t);

// Force-download a single title.
int sync_one_download(const XboxConfig *cfg, XboxSaveList *list,
                      const char *title_id);

// Progress callback fired between titles so the UI can repaint a status
// line without staring at a blank screen during long batch syncs.
//   ``msg``   short human-readable line ("Uploaded 54430006 (1/3) OK")
//   ``done``  number of titles processed so far
//   ``total`` total titles to process
typedef void (*SyncProgressFn)(const char *msg, int done, int total,
                               void *user);

// Run the full plan: upload everything in `upload`, download everything in
// `download` and `server_only`, skip conflicts. ``cb`` may be NULL.
int sync_run_all(const XboxConfig *cfg,
                 XboxSaveList *list,
                 const SyncPlan *plan,
                 SyncProgressFn cb,
                 void *user,
                 SyncSummary *out);

#endif // XBOX_SYNC_H
