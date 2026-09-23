#ifndef WIIUSYNC_STATE_H
#define WIIUSYNC_STATE_H

/*
 * Per-title sync state.  ``last_synced_hash`` lets the server's three-way
 * comparison tell which side changed since the last successful sync.
 *
 * On-disk (mirrors the 3DS / Xbox clients, but on SD so a NAND wipe or a
 * mocha-less boot can't lose it):
 *   fs:/vol/external01/3dssync/state/<title_id>.txt      64 hex chars
 *   fs:/vol/external01/3dssync/hashcache/<title_id>.txt  cached local hash
 */

#include "common.h"
#include "savetree.h"

#define STATE_HASH_HEX_LEN 64
#define STATE_HASH_BUF     65

/* Ensure the state + cache directories exist. Returns 0 on success. */
int state_init(const SyncState *st);

/* Read/write the last-synced hash for a title. */
int state_get_last_hash(const SyncState *st, const char *title_id, char *out);
int state_set_last_hash(const SyncState *st, const char *title_id, const char *hex64);

/* Hash cache — avoids re-reading unchanged saves when building a plan. */
int state_get_cached_save_hash(const SyncState *st, const SaveTitle *title, char *out);
int state_set_cached_save_hash(const SyncState *st, const SaveTitle *title, const char *hex64);
int state_clear_cached_save_hash(const SyncState *st, const char *title_id);

#endif /* WIIUSYNC_STATE_H */
