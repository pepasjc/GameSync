/*
 * sync.c — three-way-hash smart sync for vWii / Wii U save trees.
 *
 * Ported from xbox/source/sync.c.  The one behavioural addition is the
 * mandatory SD backup taken before a download overwrites NAND.
 */

#include "sync.h"

#include "bundle.h"
#include "natives.h"
#include "state.h"

#include <errno.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include <whb/log.h>

static char g_msg[256] = "";

const char *sync_last_message(void) { return g_msg; }

static void set_msg(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(g_msg, sizeof(g_msg), fmt, ap);
    va_end(ap);
}

char sync_status_glyph(TitleStatus s) {
    switch (s) {
        case TITLE_STATUS_UP_TO_DATE:     return '=';
        case TITLE_STATUS_NEEDS_UPLOAD:   return '^';
        case TITLE_STATUS_NEEDS_DOWNLOAD: return 'v';
        case TITLE_STATUS_CONFLICT:       return '!';
        case TITLE_STATUS_SERVER_ONLY:    return '+';
        default:                          return '?';
    }
}

const char *sync_status_text(TitleStatus s) {
    switch (s) {
        case TITLE_STATUS_UP_TO_DATE:     return "synced";
        case TITLE_STATUS_NEEDS_UPLOAD:   return "upload";
        case TITLE_STATUS_NEEDS_DOWNLOAD: return "download";
        case TITLE_STATUS_CONFLICT:       return "CONFLICT";
        case TITLE_STATUS_SERVER_ONLY:    return "server";
        default:                          return "?";
    }
}

static int contains(int count, char (*ids)[TITLE_ID_LEN], const char *tid) {
    if (!ids) return 0;
    for (int i = 0; i < count; i++)
        if (strcmp(ids[i], tid) == 0) return 1;
    return 0;
}

TitleStatus sync_plan_status(const SyncPlan *p, const char *tid) {
    if (!p || !tid) return TITLE_STATUS_UNKNOWN;
    if (contains(p->up_to_date_count,  p->up_to_date_ids,  tid)) return TITLE_STATUS_UP_TO_DATE;
    if (contains(p->upload_count,      p->upload_ids,      tid)) return TITLE_STATUS_NEEDS_UPLOAD;
    if (contains(p->download_count,    p->download_ids,    tid)) return TITLE_STATUS_NEEDS_DOWNLOAD;
    if (contains(p->conflict_count,    p->conflict_ids,    tid)) return TITLE_STATUS_CONFLICT;
    if (contains(p->server_only_count, p->server_only_ids, tid)) return TITLE_STATUS_SERVER_ONLY;
    return TITLE_STATUS_UNKNOWN;
}

int sync_compute_plan(const SyncState *state,
                      const SaveTitleList *list,
                      const char *const *platforms, int platform_count,
                      SyncPlan *out_plan) {
    if (state_init(state) != 0) {
        set_msg("Cannot create state dir on SD");
        return -1;
    }
    int rc = network_sync_plan(state, list, platforms, platform_count, out_plan);
    if (rc != 0) {
        set_msg("%s", network_last_error());
        return rc;
    }

    /* Hand the server the names this console read from each title's meta.xml.
     * Nothing else can name a Wii U save — its 16-hex title id resolves to no
     * DAT entry — so without this the desktop / Steam Deck / Android clients
     * list them as raw hex.  Upload carries the same hint, but a save that is
     * already up to date never uploads, which is why it rides the plan too.
     * Cosmetic, so a failure here must not fail the sync. */
    (void)network_push_name_hints(state, list);
    return rc;
}

/* ---- single-title actions ---- */

static int sync_one_upload_impl(const SyncState *state, SaveTitle *t, int force) {
    if (!state || !t) return -1;
    if (t->file_count <= 0) { set_msg("%s: no files to upload", t->title_id); return -1; }

    uint32_t ts = (uint32_t)time(NULL);
    char hex[STATE_HASH_BUF] = "";

    int code = network_upload_save_stream(state, t, ts, force, hex);
    if (code < 200 || code >= 300) {
        set_msg("Upload %s failed: HTTP %d", t->title_id, code);
        return -1;
    }
    if (hex[0]) {
        state_set_last_hash(state, t->title_id, hex);
        state_set_cached_save_hash(state, t, hex);
    }
    set_msg("Uploaded %s (%u file(s), %u KB)", t->title_id,
            (unsigned)t->file_count, (unsigned)(t->total_size / 1024));
    return 0;
}

int sync_one_upload(const SyncState *state, SaveTitle *t) {
    return sync_one_upload_impl(state, t, 0);
}

int sync_one_upload_force(const SyncState *state, SaveTitle *t) {
    return sync_one_upload_impl(state, t, 1);
}

int sync_one_download(const SyncState *state, SaveTitleList *list,
                      const char *tid) {
    if (!state || !tid) return -1;

    char dest_root[SAVE_DIR_LEN];
    if (!natives_root_for(tid, dest_root, sizeof(dest_root))) {
        set_msg("%s is not a vWii / Wii U title id", tid);
        return -1;
    }

    /* Mandatory backup: a bad NAND write is unrecoverable without one. */
    char bmsg[192];
    if (natives_backup(state, tid, bmsg, sizeof(bmsg)) != 0) {
        set_msg("%s", bmsg);
        return -1;
    }

    char tmpdir[SAVE_DIR_LEN];
    sdpath(state, APP_DATA_SUBDIR "/tmp", tmpdir, sizeof(tmpdir));
    savetree_mkdir_p(tmpdir);

    char bundle_path[SAVE_DIR_LEN];
    snprintf(bundle_path, sizeof(bundle_path), "%s/download.3dss", tmpdir);

    uint64_t bundle_size = 0;
    int code = network_download_save_to_file(state, tid, bundle_path, &bundle_size);
    if (code < 200 || code >= 300 || bundle_size == 0) {
        remove(bundle_path);
        set_msg("Download %s failed: HTTP %d (%s)", tid, code,
                network_last_error());
        WHBLogPrintf("sync: download %s -> HTTP %d size=%llu (%s)", tid, code,
                     (unsigned long long)bundle_size, network_last_error());
        return -1;
    }

    WHBLogPrintf("sync: applying %s (%llu bytes) -> %s", tid,
                 (unsigned long long)bundle_size, dest_root);
    errno = 0;
    int rc = bundle_apply_file_to_disk(bundle_path, dest_root);
    int apply_errno = errno;
    remove(bundle_path);
    if (rc != 0) {
        set_msg("Apply %s to %s failed (errno %d) - save left as-is",
                tid, dest_root, apply_errno);
        WHBLogPrintf("sync: apply %s -> rc=%d errno=%d dest=%s",
                     tid, rc, apply_errno, dest_root);
        return -1;
    }
    state_clear_cached_save_hash(state, tid);

    /* Re-hash from disk so the next plan sees the post-restore state.
     * Deliberately done on a throw-away SaveTitle rather than the caller's
     * entry: ``list`` may be a shallow merge of the vWii + Wii U lists whose
     * ``files`` arrays are still owned by the originals, and re-scanning in
     * place would free memory those still point at. */
    (void)list;
    SaveTitle t;
    memset(&t, 0, sizeof(t));
    strncpy(t.title_id, tid, sizeof(t.title_id) - 1);
    strncpy(t.root, dest_root, sizeof(t.root) - 1);
    if (savetree_scan(&t, NULL) == 0) {
        char hex[STATE_HASH_BUF];
        uint8_t raw[32];
        if (bundle_compute_save_hash(&t, raw, hex) == 0)
            state_set_last_hash(state, tid, hex);
    }
    savetree_free_title(&t);

    set_msg("Restored %s (%s)", tid, bmsg);
    return 0;
}

int sync_one_smart(const SyncState *state, SaveTitleList *list,
                   const char *tid, const SyncPlan *plan) {
    TitleStatus s = sync_plan_status(plan, tid);
    SaveTitle *local = savetree_find(list, tid);

    switch (s) {
        case TITLE_STATUS_UP_TO_DATE:
            set_msg("%s already up to date", tid);
            return 0;
        case TITLE_STATUS_NEEDS_UPLOAD:
            return local ? sync_one_upload(state, local) : -1;
        case TITLE_STATUS_NEEDS_DOWNLOAD:
        case TITLE_STATUS_SERVER_ONLY:
            return sync_one_download(state, list, tid);
        case TITLE_STATUS_CONFLICT:
            set_msg("%s conflicts - use force up / force down", tid);
            return -1;
        default:
            /* Plan stale: default to upload so a local change still lands. */
            return local ? sync_one_upload(state, local) : -1;
    }
}

/* ---- run the whole plan (conflicts skipped) ---- */

int sync_run_all(const SyncState *state,
                 SaveTitleList *list,
                 const SyncPlan *plan,
                 SyncProgressFn cb,
                 void *user,
                 SyncSummary *out) {
    SyncSummary s = {0};
    if (out) memset(out, 0, sizeof(*out));
    if (!state || !list || !plan) return -1;

    s.up_to_date = plan->up_to_date_count;
    s.conflicts  = plan->conflict_count;

    int total = plan->upload_count + plan->download_count + plan->server_only_count;
    int done  = 0;
    char msg[220];

    for (int i = 0; i < plan->upload_count; i++) {
        const char *tid = plan->upload_ids[i];
        SaveTitle *t = savetree_find(list, tid);

        if (cb) {
            snprintf(msg, sizeof(msg), "Uploading %s (%d/%d)...", tid, done + 1, total);
            cb(msg, done, total, user);
        }
        int rc = t ? sync_one_upload(state, t) : -1;
        if (rc == 0) s.uploaded++; else s.upload_failed++;
        done++;
        if (cb) {
            snprintf(msg, sizeof(msg), "Uploaded %s (%d/%d) %s",
                     tid, done, total, rc == 0 ? "OK" : "FAIL");
            cb(msg, done, total, user);
        }
    }

    for (int pass = 0; pass < 2; pass++) {
        int count = pass == 0 ? plan->download_count : plan->server_only_count;
        char (*ids)[TITLE_ID_LEN] = pass == 0 ? plan->download_ids
                                              : plan->server_only_ids;
        for (int i = 0; i < count; i++) {
            const char *tid = ids[i];
            if (cb) {
                snprintf(msg, sizeof(msg), "%s %s (%d/%d)...",
                         pass == 0 ? "Downloading" : "New", tid, done + 1, total);
                cb(msg, done, total, user);
            }
            int rc = sync_one_download(state, list, tid);
            if (rc == 0) s.downloaded++; else s.download_failed++;
            done++;
            if (cb) {
                snprintf(msg, sizeof(msg), "%s %s (%d/%d) %s",
                         pass == 0 ? "Downloaded" : "Pulled",
                         tid, done, total, rc == 0 ? "OK" : "FAIL");
                cb(msg, done, total, user);
            }
        }
    }

    if (out) *out = s;
    return 0;
}
