#ifndef PS3SYNC_SYNC_H
#define PS3SYNC_SYNC_H

#include "common.h"

typedef enum {
    SYNC_UP_TO_DATE = 0,
    SYNC_UPLOAD,
    SYNC_DOWNLOAD,
    SYNC_CONFLICT,
    SYNC_FAILED,
} SyncAction;

typedef struct {
    int uploaded;
    int downloaded;
    int up_to_date;
    int conflicts;
    int skipped;
    int failed;
} SyncSummary;

typedef void (*SyncProgressFn)(const char *msg);

/* Decide what action to take for titles[title_idx].
 * Queries the server and compares hashes. */
SyncAction sync_decide(const SyncState *state, int title_idx);

/* Execute a sync action for titles[title_idx].
 * Returns 0 on success, negative on failure:
 *   -2 = hash/read error
 *   -3 = bundle format error
 *   -4 = network/server error
 *   -5 = write error
 *   -6 = PS3 Apollo export zip missing/invalid
 *   -7 = PS3 server-only save requires a local slot */
int sync_execute(SyncState *state, int title_idx, SyncAction action);

/* Refresh list statuses using the same whole-save sync plan logic. */
void sync_refresh_statuses(SyncState *state, SyncProgressFn progress);

/* Auto-sync all titles using the server sync plan. */
void sync_auto_all(SyncState *state, SyncSummary *summary, SyncProgressFn progress);

#endif /* PS3SYNC_SYNC_H */
