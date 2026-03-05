/*
 * Vita Save Sync - Sync orchestration
 */

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <stdarg.h>

#include <psp2/io/fcntl.h>

#include "sync.h"
#include "saves.h"
#include "network.h"
#include "bundle.h"
#include "config.h"

#define RESP_BUF_SIZE (4 * 1024 * 1024)   /* 4MB for save downloads */

/* Sync-specific diagnostic log */
static SceUID g_sync_log_fd = -1;

static void sync_log_open(void) {
    if (g_sync_log_fd >= 0) return;  /* already open (e.g. decide called before execute) */
    sceIoMkdir("ux0:data/vitasync", 0777);
    g_sync_log_fd = sceIoOpen("ux0:data/vitasync/sync_diag.txt",
                               SCE_O_WRONLY | SCE_O_CREAT | SCE_O_APPEND, 0777);
    /* Write a separator so multiple runs are distinguishable */
    const char sep[] = "---\n";
    sceIoWrite(g_sync_log_fd, sep, sizeof(sep) - 1);
}

static void sync_log(const char *fmt, ...) {
    if (g_sync_log_fd < 0) return;
    char buf[512];
    va_list args;
    va_start(args, fmt);
    vsnprintf(buf, sizeof(buf), fmt, args);
    va_end(args);
    sceIoWrite(g_sync_log_fd, buf, strlen(buf));
}

static void sync_log_close(void) {
    if (g_sync_log_fd >= 0) {
        sceIoClose(g_sync_log_fd);
        g_sync_log_fd = -1;
    }
}

SyncAction sync_decide(const SyncState *state, int title_idx) {
    TitleInfo *title = (TitleInfo *)&state->titles[title_idx];

    sync_log_open();
    sync_log("sync_decide: game_id=%s\n", title->game_id);

    /* Compute local hash if needed */
    if (!title->hash_calculated) {
        int hr = saves_compute_hash(title);
        sync_log("  saves_compute_hash -> %d\n", hr);
        if (hr < 0) {
            sync_log("  -> SYNC_FAILED (hash)\n");
            sync_log_close();
            return SYNC_FAILED;
        }
    }

    /* Convert local hash to hex string */
    char local_hash[65];
    for (int i = 0; i < 32; i++)
        sprintf(&local_hash[i*2], "%02x", title->hash[i]);
    local_hash[64] = '\0';
    sync_log("  local_hash=%s\n", local_hash);

    /* Query server */
    char server_hash[65] = "";
    uint32_t server_size = 0;
    int r = network_get_save_info(state, title->game_id, server_hash, &server_size);
    if (r < 0 && r != 1)
        sync_log("  network_get_save_info -> HTTP %d\n", -r);
    else
        sync_log("  network_get_save_info -> %d  server_hash=%s\n", r, server_hash);

    if (r == 1) {
        sync_log("  -> SYNC_UPLOAD (no server save)\n");
        sync_log_close();
        return SYNC_UPLOAD;
    }
    if (r < 0) {
        sync_log("  -> SYNC_FAILED (network_get_save_info error)\n");
        sync_log_close();
        return SYNC_FAILED;
    }

    /* Get last synced hash */
    char last_hash[65] = "";
    bool has_last = config_get_last_hash(title->game_id, last_hash);
    sync_log("  has_last=%d last_hash=%s\n", (int)has_last, last_hash);

    if (strcmp(local_hash, server_hash) == 0) {
        sync_log("  -> SYNC_UP_TO_DATE\n");
        sync_log_close();
        return SYNC_UP_TO_DATE;
    }

    SyncAction action;
    if (has_last) {
        if (strcmp(last_hash, server_hash) == 0)      action = SYNC_UPLOAD;
        else if (strcmp(last_hash, local_hash) == 0)  action = SYNC_DOWNLOAD;
        else                                           action = SYNC_CONFLICT;
    } else {
        action = SYNC_DOWNLOAD;  /* no history: prefer download */
    }
    sync_log("  -> action=%d\n", (int)action);
    sync_log_close();
    return action;
}

/* Error codes returned by sync_execute (in addition to 0 = success):
 *  SYNC_ERR_HASH    (-2): saves_compute_hash failed (can't read save files)
 *  SYNC_ERR_BUNDLE  (-3): bundle_create or bundle_parse failed
 *  SYNC_ERR_NETWORK (-4): network upload or download failed (HTTP error or connection)
 *  SYNC_ERR_EXTRACT (-5): bundle_extract failed (can't write save files)
 */
#define SYNC_ERR_HASH     (-2)
#define SYNC_ERR_BUNDLE   (-3)
#define SYNC_ERR_NETWORK  (-4)
#define SYNC_ERR_EXTRACT  (-5)

int sync_execute(SyncState *state, int title_idx, SyncAction action) {
    TitleInfo *title = &state->titles[title_idx];

    sync_log_open();
    sync_log("sync_execute: game_id=%s action=%d\n", title->game_id, (int)action);
    sync_log("  save_dir=%s file_count=%d total_size=%u\n",
             title->save_dir, title->file_count, title->total_size);

    int result = -1;

    if (action == SYNC_FAILED) {
        /* sync_decide already failed — don't attempt the operation */
        sync_log("  action=SYNC_FAILED: aborting (see decide log above)\n");
        result = SYNC_ERR_NETWORK;  /* most likely cause; decide log will say which */
        goto done;
    }

    if (action == SYNC_UPLOAD) {
        if (!title->hash_calculated) {
            int hr = saves_compute_hash(title);
            sync_log("  saves_compute_hash -> %d\n", hr);
            if (hr < 0) { result = SYNC_ERR_HASH; goto done; }
        }

        uint8_t *bundle_data = NULL;
        uint32_t bundle_size = 0;
        int br = bundle_create(title, &bundle_data, &bundle_size);
        sync_log("  bundle_create -> %d  size=%u\n", br, bundle_size);
        if (br < 0) { result = SYNC_ERR_BUNDLE; goto done; }

        int nr = network_upload_save(state, title, bundle_data, bundle_size);
        free(bundle_data);
        sync_log("  network_upload_save -> %d (0=ok, >0=HTTP status, <0=conn error)\n", nr);

        if (nr == 0) {
            char hash_hex[65];
            for (int i = 0; i < 32; i++)
                sprintf(&hash_hex[i*2], "%02x", title->hash[i]);
            hash_hex[64] = '\0';
            config_set_last_hash(title->game_id, hash_hex);
            result = 0;
        } else {
            result = SYNC_ERR_NETWORK;
        }

    } else if (action == SYNC_DOWNLOAD) {
        static uint8_t resp_buf[RESP_BUF_SIZE];
        int nr = network_download_save(state, title->game_id, resp_buf, RESP_BUF_SIZE);
        sync_log("  network_download_save -> %d\n", nr);
        if (nr <= 0) { result = SYNC_ERR_NETWORK; goto done; }

        Bundle bundle;
        memset(&bundle, 0, sizeof(Bundle));
        int pr = bundle_parse(resp_buf, (uint32_t)nr, &bundle);
        sync_log("  bundle_parse -> %d  file_count=%d\n", pr, bundle.file_count);
        if (pr < 0) { result = SYNC_ERR_BUNDLE; goto done; }

        int er = bundle_extract(&bundle, title);
        sync_log("  bundle_extract -> %d\n", er);
        if (er == 0) {
            title->hash_calculated = false;
            saves_compute_hash(title);
            char hash_hex[65];
            for (int i = 0; i < 32; i++)
                sprintf(&hash_hex[i*2], "%02x", title->hash[i]);
            hash_hex[64] = '\0';
            config_set_last_hash(title->game_id, hash_hex);
            result = 0;
        } else {
            result = SYNC_ERR_EXTRACT;
        }

        bundle_free(&bundle);

    } else if (action == SYNC_UP_TO_DATE) {
        if (!title->hash_calculated)
            saves_compute_hash(title);
        if (title->hash_calculated) {
            char hash_hex[65];
            for (int i = 0; i < 32; i++)
                sprintf(&hash_hex[i*2], "%02x", title->hash[i]);
            hash_hex[64] = '\0';
            config_set_last_hash(title->game_id, hash_hex);
        }
        result = 0;
    }

done:
    sync_log("sync_execute result -> %d\n", result);
    sync_log_close();
    return result;
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
