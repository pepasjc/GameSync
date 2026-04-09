/*
 * PS3 Save Sync - Save resign orchestration
 *
 * High-level API that ties together PFD resign + SFO patching
 * with console identity auto-detection.
 */

#ifndef PS3SYNC_RESIGN_H
#define PS3SYNC_RESIGN_H

#include "common.h"
#include <stdbool.h>

/*
 * Initialize the resign subsystem.
 * Detects the console's PSID via syscall 872 and sets up crypto keys.
 * Must be called once at startup (after sysutil is available).
 *
 * Returns true on success, false if PSID detection fails.
 */
bool resign_init(void);

/*
 * Resign a downloaded PS3 save so it works on this console/user.
 *
 * Steps:
 *   1. Patch PARAM.SFO with target user's account_id, user_id, PSID
 *   2. Recompute all HMAC-SHA1 hashes in PARAM.PFD
 *   3. Re-encrypt and write PARAM.PFD
 *
 * title: the save's TitleInfo (provides local_path and kind)
 * state: provides ps3_user, selected_user for identity
 *
 * Only resigns SAVE_KIND_PS3 saves. Returns 0 immediately for other kinds.
 * Returns 0 on success, -1 on error (non-fatal — save was still extracted).
 */
int resign_save(const TitleInfo *title, const SyncState *state);

/*
 * Update PARAM.PFD hashes and re-sign for this console/user without
 * touching PARAM.SFO or the entry keys.
 *
 * Used after reencrypt_files_from_pfd() for RPCS3 -> PS3 sync: the data
 * files have been re-encrypted with their existing keys so we only need to
 * recompute the file hashes and re-sign the PFD structure.
 *
 * Returns 0 on success, -1 on error.
 */
int resign_pfd_only(const TitleInfo *title, const SyncState *state);

#endif /* PS3SYNC_RESIGN_H */
