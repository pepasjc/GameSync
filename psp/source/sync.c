/*
 * PSP Save Sync - Sync orchestration
 */

#include <stdio.h>
#include <string.h>
#include <stdlib.h>

#include "sync.h"
#include "saves.h"
#include "network.h"
#include "bundle.h"
#include "config.h"

#define RESP_BUF_SIZE (4 * 1024 * 1024)  /* 4MB for save downloads */

SyncAction sync_decide(const SyncState *state, int title_idx) {
    const TitleInfo *title = &state->titles[title_idx];

    /* Compute local hash if needed */
    TitleInfo *t = (TitleInfo *)title;
    if (!t->hash_calculated) {
        if (saves_compute_hash(t) < 0)
            return SYNC_FAILED;
    }

    /* Get local hash as hex */
    char local_hash[65];
    for (int i = 0; i < 32; i++)
        sprintf(&local_hash[i*2], "%02x", t->hash[i]);
    local_hash[64] = '\0';

    /* Get server hash */
    char server_hash[65] = "";
    uint32_t server_size = 0;
    int r = network_get_save_info(state, title->game_id, server_hash, &server_size, NULL);

    if (r == 1) {
        /* Server has no save: upload */
        return SYNC_UPLOAD;
    }
    if (r < 0) return SYNC_FAILED;

    /* Get last synced hash */
    char last_hash[65] = "";
    bool has_last = config_get_last_hash(title->game_id, last_hash);

    if (strcmp(local_hash, server_hash) == 0)
        return SYNC_UP_TO_DATE;

    if (has_last) {
        if (strcmp(last_hash, server_hash) == 0)
            return SYNC_UPLOAD;   /* server unchanged, client changed */
        if (strcmp(last_hash, local_hash) == 0)
            return SYNC_DOWNLOAD; /* client unchanged, server changed */
        return SYNC_CONFLICT;     /* both changed */
    }

    /* No history: treat as download to be safe */
    return SYNC_DOWNLOAD;
}

int sync_execute(SyncState *state, int title_idx, SyncAction action) {
    TitleInfo *title = &state->titles[title_idx];

    if (action == SYNC_UPLOAD) {
        /* Ensure hash is computed */
        if (!title->hash_calculated)
            if (saves_compute_hash(title) < 0) return -1;

        /* Create bundle */
        uint8_t *bundle_data = NULL;
        uint32_t bundle_size = 0;
        if (bundle_create(title, &bundle_data, &bundle_size) < 0)
            return -1;

        int r = network_upload_save(state, title, bundle_data, bundle_size);
        free(bundle_data);

        if (r == 0) {
            /* Save last_synced_hash */
            char hash_hex[65];
            for (int i = 0; i < 32; i++)
                sprintf(&hash_hex[i*2], "%02x", title->hash[i]);
            hash_hex[64] = '\0';
            config_set_last_hash(title->game_id, hash_hex);
        }
        return r;

    } else if (action == SYNC_DOWNLOAD) {
        static uint8_t resp_buf[RESP_BUF_SIZE];
        int r = network_download_save(state, title->game_id, resp_buf, RESP_BUF_SIZE);
        if (r <= 0) return -1;

        Bundle bundle;
        memset(&bundle, 0, sizeof(Bundle));
        if (bundle_parse(resp_buf, r, &bundle) < 0) return -1;

        int extract_r = bundle_extract(&bundle, title);
        if (extract_r == 0) {
            /* Recompute hash after download */
            title->hash_calculated = false;
            saves_compute_hash(title);

            char hash_hex[65];
            for (int i = 0; i < 32; i++)
                sprintf(&hash_hex[i*2], "%02x", title->hash[i]);
            hash_hex[64] = '\0';
            config_set_last_hash(title->game_id, hash_hex);
        }

        bundle_free(&bundle);
        return extract_r;

    } else if (action == SYNC_UP_TO_DATE) {
        /* Nothing to do; ensure state is recorded */
        if (!title->hash_calculated)
            saves_compute_hash(title);
        if (title->hash_calculated) {
            char hash_hex[65];
            for (int i = 0; i < 32; i++)
                sprintf(&hash_hex[i*2], "%02x", title->hash[i]);
            hash_hex[64] = '\0';
            config_set_last_hash(title->game_id, hash_hex);
        }
        return 0;
    }

    return -1;
}

void sync_auto_all(SyncState *state, SyncSummary *summary, SyncProgressFn progress) {
    if (summary) memset(summary, 0, sizeof(SyncSummary));

    /* Step 1: compute hashes for all titles */
    char msg[64];
    for (int i = 0; i < state->num_titles; i++) {
        TitleInfo *t = &state->titles[i];
        if (progress) {
            snprintf(msg, sizeof(msg), "Hashing %d/%d: %s",
                     i + 1, state->num_titles, t->game_id);
            progress(msg);
        }
        if (!t->hash_calculated)
            saves_compute_hash(t);
    }

    /* Step 2: get sync plan from server */
    if (progress) progress("Requesting sync plan...");
    static NetworkSyncPlan plan;
    if (network_get_sync_plan(state, &plan) != 0) {
        if (summary) summary->failed = state->num_titles;
        return;
    }

    /* Step 3: process uploads */
    for (int i = 0; i < plan.upload_count; i++) {
        for (int j = 0; j < state->num_titles; j++) {
            if (strcmp(state->titles[j].game_id, plan.upload[i]) == 0) {
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
    }

    /* Step 4: process downloads */
    for (int i = 0; i < plan.download_count; i++) {
        for (int j = 0; j < state->num_titles; j++) {
            if (strcmp(state->titles[j].game_id, plan.download[i]) == 0) {
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
    }

    if (summary) {
        summary->conflicts  = plan.conflict_count;
        summary->up_to_date = state->num_titles
                              - plan.upload_count
                              - plan.download_count
                              - plan.conflict_count;
        if (summary->up_to_date < 0) summary->up_to_date = 0;
    }
}

void sync_scan_all(SyncState *state, SyncSummary *summary) {
    if (summary) memset(summary, 0, sizeof(SyncSummary));

    for (int i = 0; i < state->num_titles; i++) {
        SyncAction action = sync_decide(state, i);
        if (!summary) continue;

        switch (action) {
            case SYNC_UP_TO_DATE: summary->up_to_date++; break;
            case SYNC_UPLOAD:     summary->uploaded++;   break;
            case SYNC_DOWNLOAD:   summary->downloaded++; break;
            case SYNC_CONFLICT:   summary->conflicts++;  break;
            default:              summary->failed++;     break;
        }
    }
}
