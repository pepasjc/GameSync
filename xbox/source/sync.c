// Sync helpers used by the interactive UI.

#include "sync.h"

#include "bundle.h"
#include "network.h"
#include "saves.h"
#include "state.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include <hal/debug.h>

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

static XboxSaveTitle *find_local(XboxSaveList *list, const char *tid)
{
    for (int i = 0; i < list->title_count; i++) {
        if (strcmp(list->titles[i].title_id, tid) == 0) {
            return &list->titles[i];
        }
    }
    return NULL;
}

static int local_save_hex(XboxSaveTitle *t, char *out_hex)
{
    uint8_t raw[32];
    return bundle_compute_save_hash(t, raw, out_hex);
}

char sync_status_glyph(TitleStatus s)
{
    switch (s) {
    case TITLE_STATUS_UP_TO_DATE:    return '=';
    case TITLE_STATUS_NEEDS_UPLOAD:  return '^';
    case TITLE_STATUS_NEEDS_DOWNLOAD:return 'v';
    case TITLE_STATUS_CONFLICT:      return '!';
    case TITLE_STATUS_SERVER_ONLY:   return '+';
    default:                         return '?';
    }
}

// Search a (count, ids[][]) tuple for ``tid`` and return 1 on hit.
static int contains(int count, char (*ids)[XBOX_TITLE_ID_LEN + 1],
                    const char *tid)
{
    if (!ids) return 0;
    for (int i = 0; i < count; i++) {
        if (strcmp(ids[i], tid) == 0) return 1;
    }
    return 0;
}

TitleStatus sync_plan_status(const SyncPlan *p, const char *tid)
{
    if (!p || !tid) return TITLE_STATUS_UNKNOWN;
    if (contains(p->up_to_date_count,  p->up_to_date_ids,  tid)) return TITLE_STATUS_UP_TO_DATE;
    if (contains(p->upload_count,      p->upload_ids,      tid)) return TITLE_STATUS_NEEDS_UPLOAD;
    if (contains(p->download_count,    p->download_ids,    tid)) return TITLE_STATUS_NEEDS_DOWNLOAD;
    if (contains(p->conflict_count,    p->conflict_ids,    tid)) return TITLE_STATUS_CONFLICT;
    if (contains(p->server_only_count, p->server_only_ids, tid)) return TITLE_STATUS_SERVER_ONLY;
    return TITLE_STATUS_UNKNOWN;
}

// ---------------------------------------------------------------------------
// Plan
// ---------------------------------------------------------------------------

int sync_compute_plan(const XboxConfig *cfg,
                      const XboxSaveList *list,
                      SyncPlan *out_plan)
{
    if (state_init() != 0) return -1;
    return network_sync_plan(cfg, list, out_plan);
}

// ---------------------------------------------------------------------------
// Single-title actions
// ---------------------------------------------------------------------------

int sync_one_upload(const XboxConfig *cfg, XboxSaveTitle *t)
{
    if (!cfg || !t) return -1;

    uint8_t *bundle_data = NULL;
    uint32_t bundle_size = 0;
    uint32_t ts = (uint32_t)time(NULL);

    int rc = bundle_create(t, ts, &bundle_data, &bundle_size);
    if (rc != 0 || !bundle_data) {
        debugPrint("upload %s: bundle fail rc=%d\n", t->title_id, rc);
        return -1;
    }
    int code = network_upload_save(cfg, t->title_id, bundle_data, bundle_size);
    free(bundle_data);

    if (code < 200 || code >= 300) {
        debugPrint("upload %s: HTTP %d\n", t->title_id, code);
        return -1;
    }

    char hex[XBOX_HASH_BUF];
    if (local_save_hex(t, hex) == 0) {
        state_set_last_hash(t->title_id, hex);
    }
    return 0;
}

int sync_one_download(const XboxConfig *cfg,
                      XboxSaveList *list,
                      const char *tid)
{
    if (!cfg || !tid) return -1;

    uint8_t *bundle_data = NULL;
    uint32_t bundle_size = 0;
    int code = network_download_save(cfg, tid, &bundle_data, &bundle_size);
    if (code < 200 || code >= 300 || !bundle_data) {
        debugPrint("download %s: HTTP %d\n", tid, code);
        free(bundle_data);
        return -1;
    }

    ParsedBundle pb;
    int rc = bundle_parse(bundle_data, bundle_size, &pb);
    free(bundle_data);
    if (rc != 0) {
        debugPrint("download %s: parse fail\n", tid);
        return -1;
    }

    rc = bundle_apply_to_disk(&pb, tid);
    bundle_parsed_free(&pb);
    if (rc != 0) {
        debugPrint("download %s: apply fail\n", tid);
        return -1;
    }

    XboxSaveTitle *local = find_local(list, tid);
    if (local) {
        char hex[XBOX_HASH_BUF];
        if (local_save_hex(local, hex) == 0) {
            state_set_last_hash(tid, hex);
        }
    }
    return 0;
}

int sync_one_smart(const XboxConfig *cfg,
                   XboxSaveList *list,
                   const char *tid,
                   const SyncPlan *plan)
{
    TitleStatus s = sync_plan_status(plan, tid);
    XboxSaveTitle *local = find_local(list, tid);

    switch (s) {
    case TITLE_STATUS_UP_TO_DATE:
        return 0;
    case TITLE_STATUS_NEEDS_UPLOAD:
        return local ? sync_one_upload(cfg, local) : -1;
    case TITLE_STATUS_NEEDS_DOWNLOAD:
    case TITLE_STATUS_SERVER_ONLY:
        return sync_one_download(cfg, list, tid);
    case TITLE_STATUS_CONFLICT:
        debugPrint("conflict on %s - use X (force up) or Y (force down)\n", tid);
        return -1;
    default:
        // Unknown status (plan stale). Default to upload so a user-side
        // change still propagates; safer than silently skipping.
        return local ? sync_one_upload(cfg, local) : -1;
    }
}

// ---------------------------------------------------------------------------
// Sync all (skip conflicts)
// ---------------------------------------------------------------------------

int sync_run_all(const XboxConfig *cfg,
                 XboxSaveList *list,
                 const SyncPlan *plan,
                 SyncProgressFn cb,
                 void *user,
                 SyncSummary *out)
{
    SyncSummary s = {0};
    if (out) memset(out, 0, sizeof(*out));
    if (!cfg || !list || !plan) return -1;

    s.up_to_date = plan->up_to_date_count;
    s.conflicts  = plan->conflict_count;

    int total = plan->upload_count + plan->download_count
              + plan->server_only_count;
    int done  = 0;
    char msg[200];

    for (int i = 0; i < plan->upload_count; i++) {
        const char *tid = plan->upload_ids[i];
        XboxSaveTitle *t = find_local(list, tid);

        if (cb) {
            snprintf(msg, sizeof(msg), "Uploading %s (%d/%d)...",
                     tid, done + 1, total);
            cb(msg, done, total, user);
        }

        int rc = -1;
        if (t) rc = sync_one_upload(cfg, t);
        if (rc == 0) s.uploaded++;       else s.upload_failed++;
        done++;

        if (cb) {
            snprintf(msg, sizeof(msg), "Uploaded %s (%d/%d) %s",
                     tid, done, total, rc == 0 ? "OK" : "FAIL");
            cb(msg, done, total, user);
        }
    }
    for (int i = 0; i < plan->download_count; i++) {
        const char *tid = plan->download_ids[i];

        if (cb) {
            snprintf(msg, sizeof(msg), "Downloading %s (%d/%d)...",
                     tid, done + 1, total);
            cb(msg, done, total, user);
        }

        int rc = sync_one_download(cfg, list, tid);
        if (rc == 0) s.downloaded++;     else s.download_failed++;
        done++;

        if (cb) {
            snprintf(msg, sizeof(msg), "Downloaded %s (%d/%d) %s",
                     tid, done, total, rc == 0 ? "OK" : "FAIL");
            cb(msg, done, total, user);
        }
    }
    for (int i = 0; i < plan->server_only_count; i++) {
        const char *tid = plan->server_only_ids[i];

        if (cb) {
            snprintf(msg, sizeof(msg), "New %s (%d/%d)...",
                     tid, done + 1, total);
            cb(msg, done, total, user);
        }

        int rc = sync_one_download(cfg, list, tid);
        if (rc == 0) s.downloaded++;     else s.download_failed++;
        done++;

        if (cb) {
            snprintf(msg, sizeof(msg), "Pulled %s (%d/%d) %s",
                     tid, done, total, rc == 0 ? "OK" : "FAIL");
            cb(msg, done, total, user);
        }
    }

    if (out) *out = s;
    return 0;
}
