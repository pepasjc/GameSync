/*
 * PS3 Save Sync - PARAM.SFO patching
 *
 * Reads and patches PS3 PARAM.SFO files to update ownership fields
 * (account_id, user_id, PSID) so downloaded saves are recognized
 * by the target PS3 console/user.
 *
 * Also handles:
 *   - Removing RPCS3-specific entries (*GAME, *ICON0.PNG, *PIC1.PNG, RPCS3_BLIST)
 *   - Fixing PARAMS/PARAMS2 format types (0x0204 -> 0x0004)
 *   - Setting PARAMS owner flags (byte 0, unk4)
 *   - Copying real account_id from ACCOUNT_ID into PARAMS embedded blob
 */

#ifndef PS3SYNC_SFO_H
#define PS3SYNC_SFO_H

#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>

/* SFO account ID size (16 hex chars) */
#define SFO_ACCOUNT_ID_SIZE 16

/* SFO PARAMS field embedded structure offsets (from Apollo sfo_param_params_t
 * and psdevwiki.com/ps3/PARAM.SFO):
 *
 *   0       byte: always 0x01 (owner flag)
 *   1       byte: savedata feature flags / version
 *   2       byte: unknown
 *   3       byte: cumulated counter slot number
 *   4       byte: SFO updates counter slot number
 *   5..7    reserved (zeros)
 *   8..11   counter slot 1 (u32, always 0)
 *  12..15   counter slot 2 (u32, unk2)
 *  16..19   counter slot 3 (u32, unk3)
 *  20..23   counter slot 4 (u32, always 1 = unk4)
 *  24..27   user_id_1 (u32 LE)
 *  28..43   psid[16]
 *  44..47   user_id_2 (u32 LE) — duplicate of user_id_1
 *  48..63   account_id[16]     — hex string
 */
#define SFO_PARAMS_MIN_SIZE          64  /* must be >= 64 to cover all fields */
#define SFO_PARAMS_USER_ID_1_OFFSET  24  /* u32 LE at offset 24 */
#define SFO_PARAMS_PSID_OFFSET       28  /* 16 bytes at offset 28 */
#define SFO_PARAMS_USER_ID_2_OFFSET  44  /* u32 LE at offset 44 (duplicate) */
#define SFO_PARAMS_ACCOUNT_ID_OFFSET 48  /* 16 bytes at offset 48 */

/* Patch flags */
#define SFO_PATCH_FLAG_REMOVE_COPY_PROTECTION  (1U << 0)

typedef struct {
    uint32_t flags;
    uint32_t user_id;
    char     account_id[SFO_ACCOUNT_ID_SIZE + 1]; /* 16-char hex + NUL */
    uint8_t  psid[16];
} sfo_patch_t;

/*
 * Patch a PARAM.SFO file in-place.
 *
 * Updates:
 *   - Removes RPCS3-specific entries (*GAME, *ICON0.PNG, etc.)
 *   - Fixes PARAMS/PARAMS2 format types to 0x0004 (binary)
 *   - Sets PARAMS owner flags (byte 0 = 0x01, unk4 = 1)
 *   - ACCOUNT_ID parameter (string, 16 hex chars)
 *   - PARAMS embedded structure (user_id, PSID, account_id)
 *   - Optionally removes copy protection flag (ATTRIBUTE field)
 *
 * Returns 0 on success, -1 on error.
 */
int sfo_patch(const char *sfo_path, const sfo_patch_t *patch);

#endif /* PS3SYNC_SFO_H */
