/*
 * PS3 Save Sync - Sync orchestration
 *
 * Implements smart three-way hash sync:
 *   local_hash == server_hash        -> up to date
 *   server has no save               -> upload
 *   last_synced_hash == server_hash  -> upload (only client changed)
 *   last_synced_hash == local_hash   -> download (only server changed)
 *   all three differ                 -> conflict
 *   no last_synced_hash + differ     -> download (safe default)
 */

#include "sync.h"
#include "saves.h"
#include "network.h"
#include "bundle.h"
#include "state.h"
#include "hash.h"
#include "debug.h"

#include <stdio.h>
#include <string.h>
#include <stdlib.h>

#define SYNC_ERR_HASH     (-2)
#define SYNC_ERR_BUNDLE   (-3)
#define SYNC_ERR_NETWORK  (-4)
#define SYNC_ERR_EXTRACT  (-5)

/* Refresh file_count / total_size after a successful download */
static void refresh_local_stats(TitleInfo *title) {
    char names[MAX_FILES][MAX_FILE_LEN];
    uint32_t sizes[MAX_FILES];
    int n = saves_list_files(title, names, sizes, MAX_FILES);
    if (n < 0) return;
    title->file_count = n;
    title->total_size = 0;
    for (int i = 0; i < n; i++)
        title->total_size += sizes[i];
}

/* ---- sync_decide ---- */

SyncAction sync_decide(const SyncState *state, int title_idx) {
    TitleInfo *title = (TitleInfo *)&state->titles[title_idx];
    debug_log("sync_decide: %s", title->game_code);

    if (title->server_only) {
        debug_log("  server_only -> DOWNLOAD");
        return SYNC_DOWNLOAD;
    }

    /* Compute local hash if needed */
    if (!title->hash_calculated) {
        int hr = saves_compute_hash(title);
        debug_log("  compute_hash -> %d", hr);
        if (hr < 0) return SYNC_FAILED;
    }

    char local_hash[65];
    hash_to_hex(title->hash, local_hash);
    debug_log("  local_hash=%s", local_hash);

    /* Query server */
    char server_hash[65] = "";
    uint32_t server_size = 0;
    int r = network_get_save_info(state, title->game_code,
                                  server_hash, &server_size, NULL);
    debug_log("  get_save_info -> %d  server_hash=%s", r, server_hash);

    if (r == 1) {
        debug_log("  -> UPLOAD (no server save)");
        return SYNC_UPLOAD;
    }
    if (r < 0) {
        debug_log("  -> FAILED (network error)");
        return SYNC_FAILED;
    }

    if (strcmp(local_hash, server_hash) == 0) {
        debug_log("  -> UP_TO_DATE");
        return SYNC_UP_TO_DATE;
    }

    char last_hash[65] = "";
    bool has_last = state_get_last_hash(title->game_code, last_hash);
    debug_log("  has_last=%d last_hash=%s", (int)has_last, last_hash);

    SyncAction action;
    if (has_last) {
        if (strcmp(last_hash, server_hash) == 0)     action = SYNC_UPLOAD;
        else if (strcmp(last_hash, local_hash) == 0) action = SYNC_DOWNLOAD;
        else                                          action = SYNC_CONFLICT;
    } else {
        action = SYNC_DOWNLOAD;  /* no history: prefer download */
    }
    debug_log("  -> action=%d", (int)action);
    return action;
}

/* ---- sync_execute ---- */

int sync_execute(SyncState *state, int title_idx, SyncAction action) {
    TitleInfo *title = &state->titles[title_idx];
    debug_log("sync_execute: %s action=%d", title->game_code, (int)action);

    int result = -1;

    if (action == SYNC_FAILED) {
        return SYNC_ERR_NETWORK;
    }

    if (action == SYNC_UPLOAD) {
        if (title->server_only) return SYNC_ERR_HASH;

        if (!title->hash_calculated) {
            if (saves_compute_hash(title) < 0) return SYNC_ERR_HASH;
        }

        uint8_t *bundle_data = NULL;
        uint32_t bundle_size = 0;
        if (bundle_create(title, &bundle_data, &bundle_size) < 0)
            return SYNC_ERR_BUNDLE;

        int nr = network_upload_save(state, title->game_code,
                                     bundle_data, bundle_size);
        free(bundle_data);
        debug_log("  upload -> %d", nr);

        if (nr == 0) {
            char hx[65]; hash_to_hex(title->hash, hx);
            state_set_last_hash(title->game_code, hx);
            result = 0;
        } else {
            result = SYNC_ERR_NETWORK;
        }

    } else if (action == SYNC_DOWNLOAD) {
        uint8_t *resp = (uint8_t *)malloc(8 * 1024 * 1024);
        if (!resp) return SYNC_ERR_NETWORK;

        int nr = network_download_save(state, title->game_code,
                                       resp, 8 * 1024 * 1024);
        debug_log("  download -> %d bytes", nr);
        if (nr <= 0) { free(resp); return SYNC_ERR_NETWORK; }

        Bundle bundle;
        memset(&bundle, 0, sizeof(Bundle));
        if (bundle_parse(resp, (uint32_t)nr, &bundle) < 0) {
            free(resp);
            return SYNC_ERR_BUNDLE;
        }
        free(resp);

        if (bundle_extract(&bundle, title) == 0) {
            title->server_only = false;
            refresh_local_stats(title);
            title->hash_calculated = false;
            saves_compute_hash(title);
            if (title->hash_calculated) {
                char hx[65]; hash_to_hex(title->hash, hx);
                state_set_last_hash(title->game_code, hx);
            }
            result = 0;
        } else {
            result = SYNC_ERR_EXTRACT;
        }
        bundle_free(&bundle);

    } else if (action == SYNC_UP_TO_DATE) {
        if (!title->hash_calculated) saves_compute_hash(title);
        if (title->hash_calculated) {
            char hx[65]; hash_to_hex(title->hash, hx);
            state_set_last_hash(title->game_code, hx);
        }
        result = 0;
    }

    debug_log("sync_execute result -> %d", result);
    return result;
}

/* ---- sync_auto_all ---- */

void sync_auto_all(SyncState *state, SyncSummary *summary,
                   SyncProgressFn progress) {
    if (summary) memset(summary, 0, sizeof(SyncSummary));

    char msg[128];

    /* Hash all local titles first (use cache where possible) */
    for (int i = 0; i < state->num_titles; i++) {
        TitleInfo *t = &state->titles[i];
        if (t->server_only || t->hash_calculated) continue;

        char cached[65];
        if (state_get_cached_hash(t->game_code,
                                  t->file_count, t->total_size, cached)) {
            if (hash_from_hex(cached, t->hash)) {
                t->hash_calculated = true;
                continue;
            }
        }

        if (progress) {
            snprintf(msg, sizeof(msg), "Hashing %d/%d: %s",
                     i + 1, state->num_titles, t->game_code);
            progress(msg);
        }
        if (saves_compute_hash(t) == 0) {
            char hx[65]; hash_to_hex(t->hash, hx);
            state_set_cached_hash(t->game_code, t->file_count, t->total_size, hx);
        }
    }

    if (progress) progress("Requesting sync plan from server...");

    static NetworkSyncPlan plan;
    if (network_get_sync_plan(state, &plan) != 0) {
        debug_log("sync_auto_all: get_sync_plan failed");
        if (summary) summary->failed = state->num_titles;
        return;
    }

    /* Upload */
    for (int i = 0; i < plan.upload_count; i++) {
        for (int j = 0; j < state->num_titles; j++) {
            if (strcmp(state->titles[j].game_code, plan.upload[i]) != 0) continue;
            if (progress) {
                snprintf(msg, sizeof(msg), "Uploading %d/%d: %s",
                         i + 1, plan.upload_count, plan.upload[i]);
                progress(msg);
            }
            int r = sync_execute(state, j, SYNC_UPLOAD);
            if (summary) { if (r == 0) summary->uploaded++; else summary->failed++; }
            break;
        }
    }

    /* Download */
    for (int i = 0; i < plan.download_count; i++) {
        for (int j = 0; j < state->num_titles; j++) {
            if (strcmp(state->titles[j].game_code, plan.download[i]) != 0) continue;
            if (progress) {
                snprintf(msg, sizeof(msg), "Downloading %d/%d: %s",
                         i + 1, plan.download_count, plan.download[i]);
                progress(msg);
            }
            int r = sync_execute(state, j, SYNC_DOWNLOAD);
            if (summary) { if (r == 0) summary->downloaded++; else summary->failed++; }
            break;
        }
    }

    /* Server-only (not in plan since they have no local hash) */
    int srv_only_count = 0;
    for (int i = 0; i < state->num_titles; i++) {
        if (!state->titles[i].server_only) continue;
        srv_only_count++;
        if (progress) {
            snprintf(msg, sizeof(msg), "Downloading server save: %s",
                     state->titles[i].game_code);
            progress(msg);
        }
        int r = sync_execute(state, i, SYNC_DOWNLOAD);
        if (summary) { if (r == 0) summary->downloaded++; else summary->failed++; }
    }

    if (summary) {
        summary->conflicts  = plan.conflict_count;
        summary->up_to_date = state->num_titles
                              - plan.upload_count
                              - plan.download_count
                              - srv_only_count
                              - plan.conflict_count;
        if (summary->up_to_date < 0) summary->up_to_date = 0;
    }
}
