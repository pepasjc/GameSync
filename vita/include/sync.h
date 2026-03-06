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

SyncAction sync_decide(const SyncState *state, int title_idx);
int sync_execute(SyncState *state, int title_idx, SyncAction action);
void sync_scan_all(SyncState *state, SyncSummary *summary);

typedef void (*SyncProgressFn)(const char *msg);

void sync_auto_all(SyncState *state, SyncSummary *summary, SyncProgressFn progress);

#endif
