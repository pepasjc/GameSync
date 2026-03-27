#include "sync.h"
#include "archive.h"
#include "bundle.h"
#include "config.h"
#include "nds.h"
#include "network.h"
#include "sha256.h"

#include <inttypes.h>
#include <sys/stat.h>

#define MAX_SAVE_FILES 64
#define STATE_DIR "sdmc:/3ds/3dssync/state"
#define MAX_UPLOAD_SIZE 0x70000  // 448KB compressed - bundles are zlib compressed

const char *sync_result_str(SyncResult result) {
    switch (result) {
        case SYNC_OK:           return "OK";
        case SYNC_ERR_NETWORK:  return "Network error";
        case SYNC_ERR_SERVER:   return "Server error";
        case SYNC_ERR_ARCHIVE:  return "Save read/write error";
        case SYNC_ERR_BUNDLE:   return "Bundle format error";
        case SYNC_ERR_TOO_LARGE: return "Save too large";
        default:                return "Unknown error";
    }
}

// Load the last synced hash for a title from the state file.
// Returns true and fills hash_out (65 bytes) on success.
static bool load_last_synced_hash(const char *title_id_hex, char *hash_out) {
    char path[256];
    snprintf(path, sizeof(path), STATE_DIR "/%s.txt", title_id_hex);

    FILE *f = fopen(path, "r");
    if (!f) return false;

    char buf[65] = {0};
    size_t rd = fread(buf, 1, 64, f);
    fclose(f);

    if (rd != 64) return false;

    // Validate hex characters
    for (int i = 0; i < 64; i++) {
        char c = buf[i];
        if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F')))
            return false;
    }

    memcpy(hash_out, buf, 64);
    hash_out[64] = '\0';
    return true;
}

// Save the current hash as the last synced hash for a title.
static bool save_last_synced_hash(const char *title_id_hex, const char *hash) {
    // Ensure directories exist
    mkdir("sdmc:/3ds", 0777);
    mkdir("sdmc:/3ds/3dssync", 0777);
    mkdir(STATE_DIR, 0777);

    char path[256];
    snprintf(path, sizeof(path), STATE_DIR "/%s.txt", title_id_hex);

    FILE *f = fopen(path, "w");
    if (!f) return false;

    size_t written = fwrite(hash, 1, 64, f);
    fclose(f);

    return written == 64;
}

// Minimal JSON string search - find value for a key in a JSON string.
// Returns pointer to the start of the value (after the colon and quote).
// Only handles simple string/number values, not nested objects.
static const char *json_find_key(const char *json, const char *key) {
    char search[128];
    snprintf(search, sizeof(search), "\"%s\"", key);
    const char *pos = strstr(json, search);
    if (!pos) return NULL;
    pos += strlen(search);
    // Skip : and whitespace
    while (*pos == ':' || *pos == ' ' || *pos == '\t') pos++;
    return pos;
}

// Parse a JSON array of strings, returns count. Fills out_items.
static int json_parse_string_array(const char *json, const char *key,
                                   char out_items[][17], int max_items) {
    const char *arr = json_find_key(json, key);
    if (!arr || *arr != '[') return 0;
    arr++; // skip '['

    int count = 0;
    while (*arr && *arr != ']' && count < max_items) {
        // Find next quoted string
        const char *q1 = strchr(arr, '"');
        if (!q1) break;
        q1++;
        const char *q2 = strchr(q1, '"');
        if (!q2) break;

        int len = (int)(q2 - q1);
        if (len > 0 && len <= 16) {
            memcpy(out_items[count], q1, len);
            out_items[count][len] = '\0';
            count++;
        }
        arr = q2 + 1;
    }
    return count;
}

// Build JSON metadata for one title
static int build_title_json(char *buf, int buf_size, const TitleInfo *title,
                            const char *hash, u32 total_size,
                            const char *last_synced_hash) {
    // Get a timestamp (seconds since 2000-01-01 from 3DS, convert to rough unix)
    u64 ms = osGetTime(); // ms since Jan 1 2000
    u32 timestamp = (u32)(ms / 1000) + 946684800; // add seconds from 1970 to 2000

    if (last_synced_hash && last_synced_hash[0] != '\0') {
        return snprintf(buf, buf_size,
            "{\"title_id\":\"%s\",\"save_hash\":\"%s\","
            "\"timestamp\":%lu,\"size\":%lu,\"last_synced_hash\":\"%s\"}",
            title->title_id_hex, hash,
            (unsigned long)timestamp, (unsigned long)total_size,
            last_synced_hash);
    } else {
        return snprintf(buf, buf_size,
            "{\"title_id\":\"%s\",\"save_hash\":\"%s\","
            "\"timestamp\":%lu,\"size\":%lu}",
            title->title_id_hex, hash,
            (unsigned long)timestamp, (unsigned long)total_size);
    }
}

static SyncResult upload_title_with_hash(const AppConfig *config, const TitleInfo *title,
                                         SyncProgressCb progress, const char *save_hash) {
    char msg[128];
    snprintf(msg, sizeof(msg), "Reading save: %s", title->title_id_hex);
    if (progress) progress(msg);

    // Heap-allocate to avoid stack overflow
    ArchiveFile *files = (ArchiveFile *)malloc(MAX_SAVE_FILES * sizeof(ArchiveFile));
    if (!files) return SYNC_ERR_ARCHIVE;

    int file_count;
    if (title->is_nds && title->media_type == MEDIATYPE_GAME_CARD)
        file_count = nds_cart_read_save(files, MAX_SAVE_FILES);
    else if (title->is_nds)
        file_count = nds_read_save(title->sav_path, files, MAX_SAVE_FILES);
    else
        file_count = archive_read(title->title_id, title->media_type,
                                  files, MAX_SAVE_FILES);
    if (file_count < 0) { free(files); return SYNC_ERR_ARCHIVE; }
    if (file_count == 0) { free(files); return SYNC_OK; }

    // Compute hash if not provided
    char computed_hash[65] = {0};
    const char *hash_to_save = save_hash;
    if (!hash_to_save || hash_to_save[0] == '\0') {
        bundle_compute_save_hash(files, file_count, computed_hash);
        hash_to_save = computed_hash;
    }

    snprintf(msg, sizeof(msg), "Uploading: %s (%d files)", title->title_id_hex, file_count);
    if (progress) progress(msg);

    // Create bundle
    u64 ms = osGetTime();
    u32 timestamp = (u32)(ms / 1000) + 946684800;
    u32 bundle_size;
    u8 *bundle = bundle_create(title->title_id, timestamp,
                               files, file_count, &bundle_size);
    archive_free_files(files, file_count);
    free(files);

    if (!bundle) return SYNC_ERR_BUNDLE;

    // Check size before attempting upload
    if (bundle_size > MAX_UPLOAD_SIZE) {
        free(bundle);
        return SYNC_ERR_TOO_LARGE;
    }

    // POST to server — include product code so the server can store the game name
    char path[128];
    if (title->product_code[0]) {
        snprintf(path, sizeof(path), "/saves/%s?game_code=%s",
                 title->title_id_hex, title->product_code);
    } else {
        snprintf(path, sizeof(path), "/saves/%s", title->title_id_hex);
    }

    u32 resp_size, status;
    u8 *resp = network_post(config, path, bundle, bundle_size, &resp_size, &status);
    free(bundle);

    if (!resp) return SYNC_ERR_NETWORK;
    free(resp);

    if (status == 200) {
        // Upload succeeded - save this hash as last synced state
        save_last_synced_hash(title->title_id_hex, hash_to_save);
        return SYNC_OK;
    }
    return SYNC_ERR_SERVER;
}

static SyncResult download_title(const AppConfig *config, const TitleInfo *title,
                                 SyncProgressCb progress) {
    char msg[128];
    snprintf(msg, sizeof(msg), "Downloading: %s", title->title_id_hex);
    if (progress) progress(msg);

    char path[64];
    snprintf(path, sizeof(path), "/saves/%s", title->title_id_hex);

    u32 resp_size, status;
    u8 *resp = network_get(config, path, &resp_size, &status);
    if (!resp) return SYNC_ERR_NETWORK;
    if (status != 200) { free(resp); return SYNC_ERR_SERVER; }

    // Heap-allocate to avoid stack overflow
    ArchiveFile *files = (ArchiveFile *)malloc(MAX_SAVE_FILES * sizeof(ArchiveFile));
    if (!files) { free(resp); return SYNC_ERR_BUNDLE; }

    u64 tid;
    u32 ts;
    u8 *decompressed = NULL;
    int file_count = bundle_parse(resp, resp_size, &tid, &ts, files, MAX_SAVE_FILES, &decompressed);
    if (file_count < 0) {
        free(files);
        if (decompressed) free(decompressed);
        free(resp);
        return SYNC_ERR_BUNDLE;
    }

    // Compute hash of downloaded save (before write, while data is valid)
    char new_hash[65];
    bundle_compute_save_hash(files, file_count, new_hash);

    snprintf(msg, sizeof(msg), "Writing save: %s (%d files)", title->title_id_hex, file_count);
    if (progress) progress(msg);

    // Write save data
    bool ok;
    if (title->is_nds && title->media_type == MEDIATYPE_GAME_CARD)
        ok = nds_cart_write_save(files, file_count);
    else if (title->is_nds)
        ok = nds_write_save(title->sav_path, files, file_count);
    else
        ok = archive_write(title->title_id, title->media_type, files, file_count);
    free(files);
    // Free decompressed buffer if we had a compressed bundle
    // (file data pointers point into decompressed, not resp)
    if (decompressed) free(decompressed);
    free(resp);

    if (ok) {
        // Download and write succeeded - save this hash as last synced state
        save_last_synced_hash(title->title_id_hex, new_hash);
        return SYNC_OK;
    }
    return SYNC_ERR_ARCHIVE;
}

SyncResult sync_title(const AppConfig *config, const TitleInfo *title,
                      SyncProgressCb progress) {
    // For single-title sync: always upload (the server will reject if older)
    // Pass NULL for hash - upload_title_with_hash will compute it
    return upload_title_with_hash(config, title, progress, NULL);
}

SyncResult sync_download_title(const AppConfig *config, const TitleInfo *title,
                               SyncProgressCb progress) {
    // Force download from server, ignoring local state
    return download_title(config, title, progress);
}

bool sync_all(const AppConfig *config, const TitleInfo *titles, int title_count,
              SyncProgressCb progress, SyncSummary *summary) {
    // Initialize summary
    SyncSummary local_summary = {0};

    if (progress) progress("Preparing sync metadata...");

    // Cache for computed hashes (needed for upload later)
    char (*hash_cache)[65] = (char (*)[65])malloc(title_count * 65);
    if (!hash_cache) return false;

    // Heap-allocate files array to avoid stack overflow
    ArchiveFile *files = (ArchiveFile *)malloc(MAX_SAVE_FILES * sizeof(ArchiveFile));
    if (!files) { free(hash_cache); return false; }

    // Build JSON for sync request
    // Estimate: ~230 bytes per title (with last_synced_hash) + overhead
    int json_cap = title_count * 230 + 64;
    char *json = (char *)malloc(json_cap);
    if (!json) { free(files); free(hash_cache); return false; }

    int pos = snprintf(json, json_cap, "{\"console_id\":\"%s\",\"titles\":[", config->console_id);
    bool first_title = true;

    for (int i = 0; i < title_count; i++) {
        // Skip cartridge games in automatic sync (use manual A/B buttons instead)
        if (titles[i].media_type == MEDIATYPE_GAME_CARD) {
            hash_cache[i][0] = '\0';  // Mark as skipped
            continue;
        }

        char msg[128];

        // Get fingerprint cheaply without reading content.
        // mtime is only available for NDS .sav files on the real filesystem;
        // archive saves have no mtime, so mtime=0 disables caching for them.
        int stat_fc = 0;
        u32 stat_sz = 0;
        u32 stat_mtime = 0;
        bool has_stat = false;
        if (titles[i].is_nds && titles[i].media_type != MEDIATYPE_GAME_CARD) {
            struct stat st;
            if (stat(titles[i].sav_path, &st) == 0) {
                stat_fc = 1;
                stat_sz = (u32)st.st_size;
                stat_mtime = (u32)st.st_mtime;
                has_stat = true;
            }
        } else if (!titles[i].is_nds) {
            // archive_stat gives no mtime — stat_mtime stays 0 (cache disabled)
            has_stat = (archive_stat(titles[i].title_id, titles[i].media_type,
                                     &stat_fc, &stat_sz) == 0);
        }

        char current_hash[65] = {0};
        u32 total_size = 0;

        // Try hash cache first (only for NDS saves where mtime is reliable)
        char cached_hash[65];
        if (has_stat && stat_sz > 0 &&
                config_get_cached_hash(titles[i].title_id_hex, stat_fc, stat_sz, stat_mtime, cached_hash)) {
            snprintf(msg, sizeof(msg), "Cached %d/%d: %s",
                i + 1, title_count, titles[i].title_id_hex);
            if (progress) progress(msg);
            strcpy(current_hash, cached_hash);
            total_size = stat_sz;
        } else {
            snprintf(msg, sizeof(msg), "Hashing save %d/%d: %s",
                i + 1, title_count, titles[i].title_id_hex);
            if (progress) progress(msg);

            // Read save to compute current hash
            int fc;
            if (titles[i].is_nds && titles[i].media_type == MEDIATYPE_GAME_CARD)
                fc = nds_cart_read_save(files, MAX_SAVE_FILES);
            else if (titles[i].is_nds)
                fc = nds_read_save(titles[i].sav_path, files, MAX_SAVE_FILES);
            else
                fc = archive_read(titles[i].title_id, titles[i].media_type,
                                  files, MAX_SAVE_FILES);
            if (fc < 0) fc = 0;

            if (fc > 0) {
                bundle_compute_save_hash(files, fc, current_hash);
                for (int j = 0; j < fc; j++) total_size += files[j].size;
                archive_free_files(files, fc);
                // Store in cache for next run (skipped when mtime == 0)
                if (has_stat && stat_sz > 0)
                    config_set_cached_hash(titles[i].title_id_hex, stat_fc, stat_sz, stat_mtime, current_hash);
            } else {
                strcpy(current_hash, "0000000000000000000000000000000000000000000000000000000000000000");
            }
        }

        // Cache this hash for potential upload later
        strcpy(hash_cache[i], current_hash);

        // Load last synced hash (if exists)
        char last_synced[65] = {0};
        bool has_last_synced = load_last_synced_hash(titles[i].title_id_hex, last_synced);

        if (!first_title) pos += snprintf(json + pos, json_cap - pos, ",");
        first_title = false;
        pos += build_title_json(json + pos, json_cap - pos, &titles[i],
                               current_hash, total_size,
                               has_last_synced ? last_synced : NULL);
    }

    pos += snprintf(json + pos, json_cap - pos, "]}");

    // Done with files array for hashing phase
    free(files);

    // Send sync request
    if (progress) progress("Sending sync request...");

    u32 resp_size, status;
    u8 *resp = network_post_json(config, "/sync", json, &resp_size, &status);
    free(json);

    if (!resp) { free(hash_cache); return false; }
    if (status != 200) { free(resp); free(hash_cache); return false; }

    // Null-terminate response for string parsing
    u8 *resp_str = (u8 *)realloc(resp, resp_size + 1);
    if (!resp_str) { free(resp); free(hash_cache); return false; }
    resp_str[resp_size] = '\0';
    char *plan = (char *)resp_str;

    // Parse sync plan - heap allocate to avoid stack overflow (256 * 17 * 5 = 21KB)
    char (*upload_ids)[17] = (char (*)[17])malloc(MAX_TITLES * 17);
    char (*download_ids)[17] = (char (*)[17])malloc(MAX_TITLES * 17);
    char (*server_only_ids)[17] = (char (*)[17])malloc(MAX_TITLES * 17);
    char (*conflict_ids)[17] = (char (*)[17])malloc(MAX_TITLES * 17);
    char (*up_to_date_ids)[17] = (char (*)[17])malloc(MAX_TITLES * 17);

    if (!upload_ids || !download_ids || !server_only_ids || !conflict_ids || !up_to_date_ids) {
        free(upload_ids);
        free(download_ids);
        free(server_only_ids);
        free(conflict_ids);
        free(up_to_date_ids);
        free(resp_str);
        free(hash_cache);
        return false;
    }

    int upload_count = json_parse_string_array(plan, "upload", upload_ids, MAX_TITLES);
    int download_count = json_parse_string_array(plan, "download", download_ids, MAX_TITLES);
    int server_only_count = json_parse_string_array(plan, "server_only", server_only_ids, MAX_TITLES);
    int conflict_count = json_parse_string_array(plan, "conflict", conflict_ids, MAX_TITLES);
    int up_to_date_count = json_parse_string_array(plan, "up_to_date", up_to_date_ids, MAX_TITLES);

    free(resp_str);

    // Auto-resolve conflicts for titles without local saves -> download
    // (no local save means nothing to lose, safe to download from server)
    for (int i = 0; i < conflict_count; ) {
        bool resolved = false;
        for (int j = 0; j < title_count; j++) {
            if (strcmp(titles[j].title_id_hex, conflict_ids[i]) == 0 &&
                !titles[j].has_save_data) {
                // Move to download list
                if (download_count < MAX_TITLES) {
                    strcpy(download_ids[download_count], conflict_ids[i]);
                    download_count++;
                }
                // Remove from conflict list (shift remaining)
                for (int k = i; k < conflict_count - 1; k++)
                    strcpy(conflict_ids[k], conflict_ids[k + 1]);
                conflict_count--;
                resolved = true;
                break;
            }
        }
        if (!resolved) i++;
    }

    // Record counts in summary
    local_summary.up_to_date = up_to_date_count;
    local_summary.conflicts = conflict_count;
    local_summary.skipped = server_only_count; // Will reduce as we download

    // Copy conflict title IDs for UI display (up to MAX_CONFLICT_DISPLAY)
    int copy_count = conflict_count < MAX_CONFLICT_DISPLAY ? conflict_count : MAX_CONFLICT_DISPLAY;
    for (int i = 0; i < copy_count; i++) {
        strncpy(local_summary.conflict_titles[i], conflict_ids[i], 16);
        local_summary.conflict_titles[i][16] = '\0';
    }
    // Clear remaining slots
    for (int i = copy_count; i < MAX_CONFLICT_DISPLAY; i++) {
        local_summary.conflict_titles[i][0] = '\0';
    }

    char msg[128];

    // Process uploads
    for (int i = 0; i < upload_count; i++) {
        // Find the title and its cached hash
        for (int j = 0; j < title_count; j++) {
            if (strcmp(titles[j].title_id_hex, upload_ids[i]) == 0) {
                snprintf(msg, sizeof(msg), "Uploading %d/%d: %s",
                    i + 1, upload_count, upload_ids[i]);
                if (progress) progress(msg);

                if (upload_title_with_hash(config, &titles[j], NULL, hash_cache[j]) == SYNC_OK)
                    local_summary.uploaded++;
                else
                    local_summary.failed++;
                break;
            }
        }
    }

    // Process downloads (both "download" and "server_only")
    int total_dl = download_count + server_only_count;
    int dl_done = 0;

    for (int i = 0; i < download_count; i++) {
        for (int j = 0; j < title_count; j++) {
            if (strcmp(titles[j].title_id_hex, download_ids[i]) == 0) {
                snprintf(msg, sizeof(msg), "Downloading %d/%d: %s",
                    ++dl_done, total_dl, download_ids[i]);
                if (progress) progress(msg);

                if (download_title(config, &titles[j], NULL) == SYNC_OK)
                    local_summary.downloaded++;
                else
                    local_summary.failed++;
                break;
            }
        }
    }

    // server_only titles: download if title exists locally
    for (int i = 0; i < server_only_count; i++) {
        for (int j = 0; j < title_count; j++) {
            if (strcmp(titles[j].title_id_hex, server_only_ids[i]) == 0) {
                snprintf(msg, sizeof(msg), "Downloading %d/%d: %s",
                    ++dl_done, total_dl, server_only_ids[i]);
                if (progress) progress(msg);

                if (download_title(config, &titles[j], NULL) == SYNC_OK) {
                    local_summary.downloaded++;
                    local_summary.skipped--; // Was counted as skipped, now downloaded
                } else {
                    local_summary.failed++;
                    local_summary.skipped--;
                }
                break;
            }
        }
        // If not found locally, remains in skipped count
    }

    free(upload_ids);
    free(download_ids);
    free(server_only_ids);
    free(conflict_ids);
    free(up_to_date_ids);
    free(hash_cache);

    // Send product codes for all titles so the server can resolve game names
    // for saves that were already up-to-date and never went through an upload.
    // ~40 bytes per entry: "\"0004000000161E00\":\"CTR-P-A22J\","
    int hints_cap = title_count * 45 + 16;
    char *hints_json = (char *)malloc(hints_cap);
    if (hints_json) {
        int pos = snprintf(hints_json, hints_cap, "{\"codes\":{");
        bool first_hint = true;
        for (int i = 0; i < title_count; i++) {
            if (!titles[i].product_code[0]) continue;
            if (!first_hint)
                pos += snprintf(hints_json + pos, hints_cap - pos, ",");
            pos += snprintf(hints_json + pos, hints_cap - pos,
                            "\"%s\":\"%s\"",
                            titles[i].title_id_hex, titles[i].product_code);
            first_hint = false;
        }
        snprintf(hints_json + pos, hints_cap - pos, "}}");

        if (!first_hint) {  /* at least one entry */
            u32 resp_size, status;
            u8 *resp = network_post_json(config, "/titles/update_names",
                                         hints_json, &resp_size, &status);
            if (resp) free(resp);
        }
        free(hints_json);
    }

    if (summary) *summary = local_summary;
    return true;
}

// Parse a JSON string value (no escapes, simple case)
static bool json_parse_string(const char *json, const char *key, char *out, int out_size) {
    const char *pos = json_find_key(json, key);
    if (!pos || *pos != '"') return false;
    pos++; // skip opening quote

    const char *end = strchr(pos, '"');
    if (!end) return false;

    int len = (int)(end - pos);
    if (len >= out_size) len = out_size - 1;
    memcpy(out, pos, len);
    out[len] = '\0';
    return true;
}

// Parse a JSON integer value
static bool json_parse_int(const char *json, const char *key, int *out) {
    const char *pos = json_find_key(json, key);
    if (!pos) return false;
    *out = atoi(pos);
    return true;
}

bool sync_get_save_details(const AppConfig *config, const TitleInfo *title,
                           SaveDetails *details) {
    memset(details, 0, sizeof(SaveDetails));

    // --- Get local info ---
    ArchiveFile *files = (ArchiveFile *)malloc(MAX_SAVE_FILES * sizeof(ArchiveFile));
    if (!files) return false;

    int file_count;
    if (title->is_nds && title->media_type == MEDIATYPE_GAME_CARD)
        file_count = nds_cart_read_save(files, MAX_SAVE_FILES);
    else if (title->is_nds)
        file_count = nds_read_save(title->sav_path, files, MAX_SAVE_FILES);
    else
        file_count = archive_read(title->title_id, title->media_type,
                                  files, MAX_SAVE_FILES);
    if (file_count > 0) {
        details->local_exists = true;
        details->local_file_count = file_count;

        // Compute hash and total size
        bundle_compute_save_hash(files, file_count, details->local_hash);
        for (int i = 0; i < file_count; i++) {
            details->local_size += files[i].size;
        }
        archive_free_files(files, file_count);
    } else {
        details->local_exists = (file_count == 0);  // 0 files = empty save, -1 = error/no save
        details->local_file_count = 0;
        details->local_size = 0;
        strcpy(details->local_hash, "N/A");
    }
    free(files);

    // --- Load last synced hash ---
    details->has_last_synced = load_last_synced_hash(title->title_id_hex, details->last_synced_hash);

    // --- Fetch server info ---
    char path[64];
    snprintf(path, sizeof(path), "/saves/%s/meta", title->title_id_hex);

    u32 resp_size, status;
    u8 *resp = network_get(config, path, &resp_size, &status);

    if (resp && status == 200) {
        // Null-terminate for string parsing
        u8 *resp_str = (u8 *)realloc(resp, resp_size + 1);
        if (resp_str) {
            resp_str[resp_size] = '\0';
            char *json = (char *)resp_str;

            details->server_exists = true;

            json_parse_string(json, "save_hash", details->server_hash, sizeof(details->server_hash));
            json_parse_string(json, "last_sync", details->server_last_sync, sizeof(details->server_last_sync));
            json_parse_string(json, "console_id", details->server_console_id, sizeof(details->server_console_id));

            int size = 0, fc = 0;
            if (json_parse_int(json, "save_size", &size)) details->server_size = (u32)size;
            if (json_parse_int(json, "file_count", &fc)) details->server_file_count = fc;

            free(resp_str);
        } else {
            free(resp);
        }
    } else if (resp) {
        free(resp);
        details->server_exists = false;
    } else {
        details->server_exists = false;
    }

    // --- Determine sync status ---
    if (details->local_exists && details->server_exists) {
        details->is_synced = (strcmp(details->local_hash, details->server_hash) == 0);
    } else {
        details->is_synced = false;
    }

    return true;
}

SyncAction sync_decide(const SaveDetails *details) {
    // No local and no server -> up to date (nothing to sync)
    if (!details->local_exists && !details->server_exists) {
        return SYNC_ACTION_UP_TO_DATE;
    }

    // Only local exists -> upload
    if (details->local_exists && !details->server_exists) {
        return SYNC_ACTION_UPLOAD;
    }

    // Only server exists -> download
    if (!details->local_exists && details->server_exists) {
        return SYNC_ACTION_DOWNLOAD;
    }

    // Both exist - compare hashes
    if (details->is_synced) {
        return SYNC_ACTION_UP_TO_DATE;
    }

    // Hashes differ - three-way comparison
    if (details->has_last_synced) {
        // last_synced == server -> only client changed -> upload
        if (strcmp(details->last_synced_hash, details->server_hash) == 0) {
            return SYNC_ACTION_UPLOAD;
        }
        // last_synced == local -> only server changed -> download
        if (strcmp(details->last_synced_hash, details->local_hash) == 0) {
            return SYNC_ACTION_DOWNLOAD;
        }
        // All three differ -> conflict
        return SYNC_ACTION_CONFLICT;
    }

    // No sync history - server will decide based on console_id
    // For now, treat as conflict (user needs to decide)
    return SYNC_ACTION_CONFLICT;
}

int sync_get_history(const AppConfig *config, const char *title_id_hex,
                     HistoryVersion *versions, int max_versions) {
    char path[64];
    snprintf(path, sizeof(path), "/saves/%s/history", title_id_hex);

    u32 resp_size, status;
    u8 *resp = network_get(config, path, &resp_size, &status);

    if (!resp || status != 200) {
        if (resp) free(resp);
        return -1;
    }

    // Null-terminate for string parsing
    u8 *resp_str = (u8 *)realloc(resp, resp_size + 1);
    if (!resp_str) {
        free(resp);
        return -1;
    }
    resp_str[resp_size] = '\0';
    char *json = (char *)resp_str;

    // Find versions array
    const char *arr = json_find_key(json, "versions");
    if (!arr || *arr != '[') {
        free(resp_str);
        return 0;
    }
    arr++; // skip '['

    int count = 0;
    while (*arr && *arr != ']' && count < max_versions) {
        // Find next object
        const char *obj = strchr(arr, '{');
        if (!obj) break;
        obj++;
        const char *obj_end = strchr(obj, '}');
        if (!obj_end) break;

        // Extract timestamp and size from this object
        char timestamp[32] = "";
        int size = 0, file_count = 0;

        // Quick parse: find "timestamp":"value"
        const char *ts_start = strstr(obj, "\"timestamp\":\"");
        if (ts_start && ts_start < obj_end) {
            ts_start += 12; // skip "timestamp":"
            const char *ts_end = strchr(ts_start, '"');
            if (ts_end && ts_end < obj_end) {
                int len = (int)(ts_end - ts_start);
                if (len < (int)sizeof(timestamp)) {
                    memcpy(timestamp, ts_start, len);
                    timestamp[len] = '\0';
                }
            }
        }

        // Find "size":value
        const char *sz_start = strstr(obj, "\"size\":");
        if (sz_start && sz_start < obj_end) {
            sz_start += 7;
            size = atoi(sz_start);
        }

        // Find "file_count":value
        const char *fc_start = strstr(obj, "\"file_count\":");
        if (fc_start && fc_start < obj_end) {
            fc_start += 12;
            file_count = atoi(fc_start);
        }

        if (timestamp[0]) {
            strncpy(versions[count].timestamp, timestamp, sizeof(versions[count].timestamp) - 1);
            versions[count].timestamp[sizeof(versions[count].timestamp) - 1] = '\0';
            versions[count].size = (u32)size;
            versions[count].file_count = file_count;
            count++;
        }

        arr = obj_end + 1;
    }

    free(resp_str);
    return count;
}

SyncResult sync_download_history(const AppConfig *config, const TitleInfo *title,
                                 const char *timestamp, SyncProgressCb progress) {
    char msg[128];
    snprintf(msg, sizeof(msg), "Downloading history version...");
    if (progress) progress(msg);

    char path[64];
    snprintf(path, sizeof(path), "/saves/%s/history/%s", title->title_id_hex, timestamp);

    u32 resp_size, status;
    u8 *resp = network_get(config, path, &resp_size, &status);
    if (!resp) return SYNC_ERR_NETWORK;
    if (status != 200) { free(resp); return SYNC_ERR_SERVER; }

    // Parse bundle
    ArchiveFile *files = (ArchiveFile *)malloc(MAX_SAVE_FILES * sizeof(ArchiveFile));
    if (!files) { free(resp); return SYNC_ERR_BUNDLE; }

    u64 tid;
    u32 ts;
    u8 *decompressed = NULL;
    int file_count = bundle_parse(resp, resp_size, &tid, &ts, files, MAX_SAVE_FILES, &decompressed);
    if (file_count < 0) {
        free(files);
        if (decompressed) free(decompressed);
        free(resp);
        return SYNC_ERR_BUNDLE;
    }

    snprintf(msg, sizeof(msg), "Writing save: %s (%d files)", title->title_id_hex, file_count);
    if (progress) progress(msg);

    // Write save data
    bool ok;
    if (title->is_nds && title->media_type == MEDIATYPE_GAME_CARD)
        ok = nds_cart_write_save(files, file_count);
    else if (title->is_nds)
        ok = nds_write_save(title->sav_path, files, file_count);
    else
        ok = archive_write(title->title_id, title->media_type, files, file_count);

    free(files);
    if (decompressed) free(decompressed);
    free(resp);

    if (!ok) return SYNC_ERR_ARCHIVE;

    // Update last synced hash with the downloaded version
    char hash[65];
    bundle_compute_save_hash(files, file_count, hash);
    save_last_synced_hash(title->title_id_hex, hash);

    return SYNC_OK;
}
