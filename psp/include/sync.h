#ifndef SYNC_H
#define SYNC_H

#include "common.h"

typedef struct {
    int uploaded;
    int downloaded;
    int up_to_date;
    int conflicts;
    int failed;
} SyncSummary;

/* Check server for sync status of a single game.
 * Compares local hash with server hash and last_synced_hash.
 * Returns the recommended action. */
SyncAction sync_decide(const SyncState *state, int title_idx);

/* Execute a sync action (upload or download) for one title.
 * Returns 0 on success. */
int sync_execute(SyncState *state, int title_idx, SyncAction action);

/* Scan all titles and determine sync status. Updates title->scanned flags.
 * summary: optional output summary. */
void sync_scan_all(SyncState *state, SyncSummary *summary);

/* Progress callback type (receives a formatted message string). */
typedef void (*SyncProgressFn)(const char *msg);

/* Auto sync all titles using the /api/v1/sync batch endpoint:
 * hashes all titles, sends plan request, uploads/downloads as directed.
 * summary: optional output. progress: optional callback for status updates. */
void sync_auto_all(SyncState *state, SyncSummary *summary, SyncProgressFn progress);

#endif /* SYNC_H */
