// Per-title sync state. Stores ``last_synced_hash`` for each title so the
// server's three-way comparison can tell which side changed since the last
// successful sync.
//
// On-disk layout (mirrors the 3DS client's per-title state files):
//   E:\UDATA\TDSV0000\state\<TITLE_ID>.txt     -- 64 hex chars, no newline

#ifndef XBOX_STATE_H
#define XBOX_STATE_H

#define XBOX_HASH_HEX_LEN 64       // 32 bytes -> 64 hex chars
#define XBOX_HASH_BUF     65       // hex chars + NUL

#include "saves.h"

// Ensure the state directory exists. Returns 0 on success.
int state_init(void);

// Read the last-synced hash for a title. ``out`` must hold at least
// XBOX_HASH_BUF bytes. Returns 1 if a hash was loaded, 0 otherwise.
int state_get_last_hash(const char *title_id, char *out);

// Persist a hash for a title. Hash must be 64 hex chars (no newline).
// Returns 0 on success.
int state_set_last_hash(const char *title_id, const char *hex64);

// Hash-cache helpers used to avoid re-reading unchanged saves during plan
// creation. These cache only local save hashes; last-synced state is separate.
int state_get_cached_save_hash(const XboxSaveTitle *title, char *out);
int state_set_cached_save_hash(const XboxSaveTitle *title, const char *hex64);
int state_clear_cached_save_hash(const char *title_id);
int state_clear_hash_cache(void);

#endif // XBOX_STATE_H
