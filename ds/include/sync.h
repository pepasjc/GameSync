#ifndef SYNC_H
#define SYNC_H

#include "common.h"

// Sync decision for a single title
typedef struct {
    SyncAction action;           // Determined sync action
    char server_hash[65];        // Server save hash (hex string)
    uint32_t server_timestamp;   // Server's client_timestamp (unix epoch)
    size_t server_size;          // Server save size in bytes
    bool has_last_synced;        // Whether we have a state file
    char last_synced_hash[65];   // Last successfully synced hash
    uint32_t local_mtime;        // Local file modification time
} SyncDecision;

// Summary of batch sync results
typedef struct {
    int uploaded;
    int downloaded;
    int up_to_date;
    int conflicts;
    int failed;
} SyncSummary;

// Load last synced hash from state file
// Returns true if found and valid, false otherwise
bool sync_load_last_hash(const char *title_id_hex, char *hash_out);

// Save last synced hash to state file
// Returns true on success
bool sync_save_last_hash(const char *title_id_hex, const char *hash);

// Determine sync action for a single title (no side effects)
// Returns 0 on success, -1 on network error
int sync_decide(SyncState *state, int title_idx, SyncDecision *decision);

// Execute a sync action (upload or download) and save state on success
// Returns 0 on success, -1 on error
int sync_execute(SyncState *state, int title_idx, SyncAction action);

// Batch sync all titles: decide + execute for each
// Returns 0 on success, -1 on fatal error
int sync_all(SyncState *state, SyncSummary *summary);

// Scan all titles: decide sync status for each, store in title->scan_result
// Does NOT upload/download — only checks status
int sync_scan_all(SyncState *state, SyncSummary *summary);

// History version info
#define MAX_HISTORY_VERSIONS 20

typedef struct {
    char timestamp[32];  // ISO 8601 timestamp
    uint32_t size;
    int file_count;
} HistoryVersion;

// Get list of history versions for a title.
// Returns number of versions found, or -1 on error.
int sync_get_history(SyncState *state, const char *title_id_hex,
                     HistoryVersion *versions, int max_versions);

// Download a specific history version.
// Returns 0 on success, -1 on error.
int sync_download_history(SyncState *state, int title_idx, const char *timestamp);

#endif
