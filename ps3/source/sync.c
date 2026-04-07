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
#include "decrypt.h"
#include "gamekeys.h"
#include "resign.h"
#include "pfd.h"
#include "state.h"
#include "hash.h"
#include "debug.h"
#include "ui.h"

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <sys/stat.h>

#define SYNC_ERR_HASH     (-2)
#define SYNC_ERR_BUNDLE   (-3)
#define SYNC_ERR_NETWORK  (-4)
#define SYNC_ERR_EXTRACT  (-5)
#define SYNC_ERR_EXPORT   (-6)
#define SYNC_ERR_NEEDS_LOCAL_SLOT (-7)

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
    debug_log("sync_decide: %s", title->title_id);

    if (title->server_only) {
        debug_log("  server_only -> DOWNLOAD");
        return SYNC_DOWNLOAD;
    }

    /* Compute local hash if needed — show progress, can be slow for large saves */
    if (!title->hash_calculated) {
        ui_status("Hashing %s... (may take a moment)", title->game_code);
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
    int r = network_get_save_info(state, title->title_id,
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
    bool has_last = state_get_last_hash(title->title_id, last_hash);
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

    /* Update cached status on the title */
    switch (action) {
        case SYNC_UPLOAD:     title->status = TITLE_STATUS_UPLOAD;    break;
        case SYNC_DOWNLOAD:   title->status = TITLE_STATUS_DOWNLOAD;  break;
        case SYNC_CONFLICT:   title->status = TITLE_STATUS_CONFLICT;  break;
        case SYNC_UP_TO_DATE: title->status = TITLE_STATUS_SYNCED;    break;
        default: break;
    }
    return action;
}

/* ---- sync_execute ---- */

int sync_execute(SyncState *state, int title_idx, SyncAction action) {
    TitleInfo *title = &state->titles[title_idx];
    debug_log("sync_execute: %s action=%d", title->title_id, (int)action);

    int result = -1;

    if (action == SYNC_FAILED) {
        return SYNC_ERR_NETWORK;
    }

    if (action == SYNC_DOWNLOAD &&
        title->kind == SAVE_KIND_PS3 &&
        title->server_only) {
        debug_log("sync_execute: blocking server-only PS3 download for %s until a local save exists",
                  title->title_id);
        return SYNC_ERR_NEEDS_LOCAL_SLOT;
    }

    if (action == SYNC_UPLOAD) {
        if (title->server_only) return SYNC_ERR_HASH;

        /* For PS3 HDD saves without an export zip, try on-console decryption.
         * This decrypts the save files to a temp directory and sets upload_path
         * so the existing bundle/upload flow works unchanged. */
        char decrypt_temp[PATH_LEN] = "";
        bool did_decrypt = false;

        debug_log("sync_execute UPLOAD: kind=%d local_path='%s'",
                  (int)title->kind, title->local_path);

        if (title->kind == SAVE_KIND_PS3 &&
            title->upload_path[0] == '\0' &&
            title->local_path[0] != '\0' &&
            gamekeys_is_loaded()) {

            debug_log("sync_execute: entering decrypt path for %s (game_code=%s)",
                      title->title_id, title->game_code);
            ui_status("Decrypting HDD save: %s", title->game_code);
            int dr = decrypt_save(title, decrypt_temp, sizeof(decrypt_temp));
            debug_log("sync_execute: decrypt_save returned %d, temp='%s'", dr, decrypt_temp);
            if (dr == 0 && decrypt_temp[0] != '\0') {
                /* Point upload_path at the decrypted temp directory */
                strncpy(title->upload_path, decrypt_temp, PATH_LEN - 1);
                title->upload_path[PATH_LEN - 1] = '\0';
                title->upload_is_zip = false;
                did_decrypt = true;
                debug_log("sync_execute: decrypted %s to %s",
                          title->title_id, decrypt_temp);
            } else {
                debug_log("sync_execute: decrypt failed (%d) for %s, temp='%s'",
                          dr, title->title_id, decrypt_temp);
                /* Fall through — saves_has_upload_source will fail below */
            }
            pump_callbacks();
        } else {
            debug_log("sync_execute: skipping decrypt for %s (kind=%d)",
                      title->title_id, (int)title->kind);
        }

        if (!saves_has_upload_source(title)) {
            if (did_decrypt) {
                decrypt_cleanup(decrypt_temp);
                title->upload_path[0] = '\0';
            }
            if (title->kind == SAVE_KIND_PS3 && !gamekeys_is_loaded()) {
                ui_status("No games.conf — cannot decrypt HDD save %s", title->title_id);
                debug_log("sync_execute: no gamekeys and no export zip for %s", title->title_id);
            } else {
                ui_status("Cannot upload %s — decrypt failed or no export zip", title->title_id);
                debug_log("sync_execute: no upload source for title_id=%s game_code=%s", title->title_id, title->game_code);
            }
            return SYNC_ERR_EXPORT;
        }

        if (!title->hash_calculated) {
            ui_status("Hashing local save: %s", title->game_code);
            if (saves_compute_hash(title) < 0) {
                if (did_decrypt) {
                    decrypt_cleanup(decrypt_temp);
                    title->upload_path[0] = '\0';
                }
                return SYNC_ERR_HASH;
            }
            pump_callbacks();
            ui_status("Finished hashing local save: %s", title->game_code);
        }

        uint8_t *bundle_data = NULL;
        uint32_t bundle_size = 0;
        /* bundle_create reads files, computes SHA-256, compresses with zlib -
         * pump_callbacks() is called inside bundle_create() between stages. */
        ui_status("Building bundle: %s", title->game_code);
        if (bundle_create(title, &bundle_data, &bundle_size) < 0) {
            if (did_decrypt) {
                decrypt_cleanup(decrypt_temp);
                title->upload_path[0] = '\0';
            }
            return SYNC_ERR_BUNDLE;
        }
        ui_status("Finished bundle: %s", title->game_code);

        pump_callbacks();

        ui_status("Uploading bundle: %s", title->game_code);
        int nr = network_upload_save(state, title->title_id,
                                     bundle_data, bundle_size);
        free(bundle_data);
        debug_log("  upload -> %d", nr);

        pump_callbacks();

        if (nr == 0) {
            ui_status("Finalizing upload: %s", title->game_code);
            char hx[65]; hash_to_hex(title->hash, hx);
            state_set_last_hash(title->title_id, hx);
            title->status = TITLE_STATUS_SYNCED;
            ui_status("Upload complete: %s", title->game_code);
            result = 0;
        } else {
            result = SYNC_ERR_NETWORK;
        }

        /* Clean up decrypted temp files */
        if (did_decrypt) {
            decrypt_cleanup(decrypt_temp);
            title->upload_path[0] = '\0';
        }

    } else if (action == SYNC_DOWNLOAD) {
        uint8_t *resp = (uint8_t *)malloc(8 * 1024 * 1024);
        if (!resp) return SYNC_ERR_NETWORK;

        ui_status("Starting download: %s", title->game_code);
        int nr = network_download_save(state, title->title_id,
                                       resp, 8 * 1024 * 1024);
        debug_log("  download -> %d bytes", nr);
        if (nr <= 0) { free(resp); return SYNC_ERR_NETWORK; }
        ui_status("Finished download: %s", title->game_code);

        pump_callbacks();

        /* Heap-allocate Bundle — it is 38KB on the stack (files[128]),
         * which overflows the PSL1GHT default 64KB main thread stack when
         * combined with the already-deep download call chain. */
        Bundle *bundle = (Bundle *)malloc(sizeof(Bundle));
        if (!bundle) { free(resp); return SYNC_ERR_NETWORK; }
        memset(bundle, 0, sizeof(Bundle));

        /* bundle_parse decompresses zlib + verifies SHA-256 per file -
         * pump_callbacks() is called inside between stages. */
        ui_status("Parsing bundle: %s", title->game_code);
        if (bundle_parse(resp, (uint32_t)nr, bundle) < 0) {
            free(bundle);
            free(resp);
            return SYNC_ERR_BUNDLE;
        }
        free(resp);
        ui_status("Finished parsing bundle: %s", title->game_code);

        pump_callbacks();

        /* For PS3 saves: if the save directory already exists locally, only
         * write the actual save data files and skip metadata (PARAM.SFO,
         * PARAM.PFD, icons).  This is the key to making RPCS3->PS3 work:
         * the RPCS3 bundle has unencrypted data without a valid PARAM.PFD.
         * If we keep the native metadata and just update the game data,
         * resign_save can re-sign the existing PARAM.PFD instead of trying
         * to create one from scratch (which fails without game encrypt keys). */
        bool local_save_exists = false;
        if (title->kind == SAVE_KIND_PS3 && title->local_path[0] != '\0') {
            struct stat dir_st;
            local_save_exists = (stat(title->local_path, &dir_st) == 0
                                 && S_ISDIR(dir_st.st_mode));
        }
        debug_log("sync_execute: local_save_exists=%d for %s",
                  (int)local_save_exists, title->title_id);

        int extract_result = 0;
        ui_status("Extracting save files: %s", title->game_code);
        if (local_save_exists) {
            /* Selective extract: skip metadata to preserve native PS3 files */
            for (int fi = 0; fi < bundle->file_count && extract_result == 0; fi++) {
                if (saves_is_ps3_metadata_file(bundle->files[fi].path)) {
                    debug_log("sync_execute: preserving metadata %s",
                              bundle->files[fi].path);
                    continue;
                }
                ui_status("Writing %s", bundle->files[fi].path);
                if (saves_write_file(title, bundle->files[fi].path,
                                     bundle->files[fi].data,
                                     bundle->files[fi].size) < 0) {
                    debug_log("sync_execute: write_file %s failed",
                              bundle->files[fi].path);
                    extract_result = -1;
                }
                pump_callbacks();
            }
        } else {
            extract_result = bundle_extract(bundle, title);
        }

        if (extract_result == 0) {
            ui_status("Finished extracting files: %s", title->game_code);
            pump_callbacks();

            if (title->kind == SAVE_KIND_PS3) {
                if (local_save_exists) {
                    /* RPCS3 -> PS3: re-encrypt data files using the existing
                     * keys from PARAM.PFD (same key, new content), then just
                     * update the file hashes and re-sign PFD.  PARAM.SFO and
                     * the entry keys are left completely untouched — this is
                     * exactly what PS3BruteforceSaveData does manually. */
                    int rer = reencrypt_files_from_pfd(title);
                    debug_log("sync_execute: reencrypt_files_from_pfd -> %d", rer);
                    pump_callbacks();
                    if (rer == 0) {
                        int rr = resign_pfd_only(title, state);
                        if (rr != 0) {
                            debug_log("sync_execute: resign_pfd_only failed for %s (non-fatal)",
                                      title->title_id);
                        }
                    } else {
                        debug_log("sync_execute: reencrypt failed for %s (non-fatal)",
                                  title->title_id);
                    }
                } else {
                    /* Fresh download (no existing local save): patch ownership,
                     * encrypt save data, and create PARAM.PFD from scratch. */
                    int rr = resign_save(title, state);
                    if (rr != 0) {
                        debug_log("sync_execute: resign failed for %s (non-fatal)",
                                  title->title_id);
                    }
                }
                pump_callbacks();
                if (title->kind == SAVE_KIND_PS3) {
                    int pr = saves_normalize_permissions(title->local_path);
                    debug_log("sync_execute: normalize_permissions -> %d for %s",
                              pr, title->title_id);
                }
                debug_dump_savedata_permissions(state->savedata_root, title->local_path);
            }

            title->server_only = false;
            title->on_server   = true;
            refresh_local_stats(title);
            pump_callbacks();
            title->hash_calculated = false;
            ui_status("Hashing extracted save: %s", title->game_code);
            saves_compute_hash(title);
            pump_callbacks();
            if (title->hash_calculated) {
                ui_status("Saving sync state: %s", title->game_code);
                char hx[65]; hash_to_hex(title->hash, hx);
                state_set_last_hash(title->title_id, hx);
                ui_status("Finished sync state: %s", title->game_code);
            }
            title->status = TITLE_STATUS_SYNCED;
            ui_status("Download complete: %s", title->game_code);
            result = 0;
        } else {
            result = SYNC_ERR_EXTRACT;
        }
        bundle_free(bundle);
        free(bundle);

    } else if (action == SYNC_UP_TO_DATE) {
        if (!title->hash_calculated) {
            ui_status("Hashing local save: %s", title->game_code);
            saves_compute_hash(title);
        }
        pump_callbacks();
        if (title->hash_calculated) {
            char hx[65]; hash_to_hex(title->hash, hx);
            state_set_last_hash(title->title_id, hx);
        }
        ui_status("Already up to date: %s", title->game_code);
        result = 0;
    }

    debug_log("sync_execute result -> %d", result);
    return result;
}

void sync_refresh_statuses(SyncState *state, SyncProgressFn progress) {
    NetworkSyncPlan plan;
    char msg[128];

    if (!state) {
        return;
    }

    for (int i = 0; i < state->num_titles; i++) {
        TitleInfo *t = &state->titles[i];
        if (t->server_only) {
            t->status = TITLE_STATUS_SERVER_ONLY;
        } else if (!t->on_server) {
            t->status = TITLE_STATUS_LOCAL_ONLY;
        } else {
            t->status = TITLE_STATUS_UNKNOWN;
        }
    }

    for (int i = 0; i < state->num_titles; i++) {
        TitleInfo *t = &state->titles[i];
        if (t->server_only || t->hash_calculated) {
            continue;
        }

        char cached[65];
        if (state_get_cached_hash(t->title_id, t->file_count, t->total_size, cached)) {
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
            char hx[65];
            hash_to_hex(t->hash, hx);
            state_set_cached_hash(t->title_id, t->file_count, t->total_size, hx);
        }
        pump_callbacks();
    }

    if (progress) {
        progress("Checking sync status...");
    }
    if (network_get_sync_plan(state, &plan) != 0) {
        debug_log("sync_refresh_statuses: get_sync_plan failed");
        return;
    }

    for (int i = 0; i < state->num_titles; i++) {
        TitleInfo *t = &state->titles[i];
        if (!t->server_only && t->on_server && t->hash_calculated) {
            t->status = TITLE_STATUS_SYNCED;
        }
    }

    for (int i = 0; i < plan.upload_count; i++) {
        for (int j = 0; j < state->num_titles; j++) {
            if (strcmp(state->titles[j].title_id, plan.upload[i]) == 0) {
                state->titles[j].status = TITLE_STATUS_UPLOAD;
                break;
            }
        }
    }
    for (int i = 0; i < plan.download_count; i++) {
        for (int j = 0; j < state->num_titles; j++) {
            if (strcmp(state->titles[j].title_id, plan.download[i]) == 0) {
                state->titles[j].status = TITLE_STATUS_DOWNLOAD;
                break;
            }
        }
    }
    for (int i = 0; i < plan.conflict_count; i++) {
        for (int j = 0; j < state->num_titles; j++) {
            if (strcmp(state->titles[j].title_id, plan.conflict[i]) == 0) {
                state->titles[j].status = TITLE_STATUS_CONFLICT;
                break;
            }
        }
    }
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
        if (state_get_cached_hash(t->title_id,
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
            state_set_cached_hash(t->title_id, t->file_count, t->total_size, hx);
        }
        pump_callbacks();
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
            if (strcmp(state->titles[j].title_id, plan.upload[i]) != 0) continue;
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
            if (strcmp(state->titles[j].title_id, plan.download[i]) != 0) continue;
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
        if (state->titles[i].kind == SAVE_KIND_PS3) {
            debug_log("sync_auto_all: skipping server-only PS3 save %s until a local slot exists",
                      state->titles[i].title_id);
            if (summary) summary->skipped++;
            srv_only_count++;
            continue;
        }
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
