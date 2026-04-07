/*
 * PS3 Save Sync - PARAM.PFD resign engine
 *
 * Minimal implementation of the PS3 PARAM.PFD resign algorithm,
 * based on analysis of Apollo Save Tool (bucanero/apollo-ps3)
 * and flatz's original pfd_sfo_tools.
 *
 * Supports PFD version 3 and 4 (most PS3 games use v4).
 * Uses PolarSSL (included in PSL1GHT SDK) for AES-128 and HMAC-SHA1.
 */

#ifndef PS3SYNC_PFD_H
#define PS3SYNC_PFD_H

#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>

/* ---- Constants ---- */

#define PFD_MAGIC           0x50464442ULL  /* "PFDB" as big-endian u64 at offset 0 */
#define PFD_VERSION_V3      3
#define PFD_VERSION_V4      4

#define PFD_KEY_SIZE        16
#define PFD_HASH_KEY_SIZE   20
#define PFD_HASH_SIZE       20  /* HMAC-SHA1 output */

#define PFD_MAX_FILE_NAME   65
#define PFD_ENTRY_SIZE      272
#define PFD_MAX_FILE_SIZE   32768  /* PARAM.PFD max = 32KB */

#define PFD_ENTRY_HASH_FILE          0
#define PFD_ENTRY_HASH_FILE_CID      1
#define PFD_ENTRY_HASH_FILE_DHK_CID2 2
#define PFD_ENTRY_HASH_FILE_AID_UID  3
#define PFD_ENTRY_NUM_HASHES         4

/* ---- Crypto key configuration ---- */

typedef struct {
    uint8_t syscon_manager_key[PFD_KEY_SIZE];     /* AES key for PFD signature encryption */
    uint8_t keygen_key[PFD_HASH_KEY_SIZE];        /* HMAC key to derive real_hash_key (v4) */
    uint8_t savegame_param_sfo_key[PFD_HASH_KEY_SIZE]; /* HMAC key for PARAM.SFO file hash */
    uint8_t fallback_disc_hash_key[PFD_KEY_SIZE]; /* default disc hash key */
    uint8_t authentication_id[8];                 /* for AID_UID hash */

    uint8_t console_id[PFD_KEY_SIZE];             /* runtime: IDPS/PSID from console */
    uint8_t user_id[8];                           /* runtime: formatted user ID string */
    uint8_t disc_hash_key[PFD_KEY_SIZE];          /* per-game (or fallback if unknown) */

    uint8_t secure_file_id[PFD_KEY_SIZE];         /* per-game secure_file_id (from games.conf) */
    bool    has_secure_file_id;                   /* true if secure_file_id is valid */
} pfd_keys_t;

/* ---- PFD context (opaque) ---- */

typedef struct pfd_ctx pfd_ctx_t;

/* ---- API ---- */

/*
 * Initialize the PFD crypto keys from the well-known static values.
 * Must be called once before any pfd_resign() calls.
 * Fills in syscon_manager_key, keygen_key, savegame_param_sfo_key,
 * fallback_disc_hash_key, authentication_id.
 */
void pfd_setup_keys(pfd_keys_t *keys);

/*
 * Resign a PS3 save directory.
 *
 * This is the main entry point. It:
 *   1. Reads PARAM.PFD from save_dir_path
 *   2. Decrypts the signature block
 *   3. Recomputes ALL HMAC-SHA1 hashes for every file entry
 *   4. Recomputes entry signatures, bottom hash, top hash
 *   5. Re-encrypts and writes PARAM.PFD
 *
 * keys must have been initialized via pfd_setup_keys(), then filled in:
 *   - keys->console_id  = target console's PSID (16 bytes)
 *   - keys->user_id     = formatted user ID (8-char decimal string as bytes)
 *   - keys->disc_hash_key = per-game key or copy of fallback_disc_hash_key
 *
 * Returns 0 on success, -1 on error.
 */
int pfd_resign(const char *save_dir_path, const pfd_keys_t *keys);

/*
 * Create a brand-new PARAM.PFD for a save directory that doesn't have one
 * (e.g. saves synced from RPCS3 or other emulators).
 *
 * Enumerates the protected files in save_dir_path, builds the fixed-size
 * save-data tables used by native PS3 saves, computes all HMAC-SHA1 hashes
 * for the target console/user identity in keys, and writes PARAM.PFD to
 * save_dir_path.
 *
 * Entry keys are left zeroed (plaintext saves, no file encryption).
 *
 * Returns 0 on success, -1 on error.
 */
int pfd_create(const char *save_dir_path, const pfd_keys_t *keys);

/*
 * Create a brand-new PARAM.PFD with encrypted entry keys.
 *
 * Same as pfd_create(), but stores the provided encrypted entry keys
 * in the PFD entry table before computing HMAC signatures. This is
 * used when save data files have been encrypted via encrypt_save() —
 * the entry keys must be in the PFD for the PS3 to decrypt them.
 *
 * entry_keys:  array of {filename, encrypted_key} pairs (from encrypt_save())
 * num_keys:    number of entries in entry_keys array
 *
 * If a file has no matching entry key (e.g. ICON0.PNG), its key is zeroed.
 *
 * Returns 0 on success, -1 on error.
 */
typedef struct {
    char    filename[65];
    uint8_t encrypted_key[64];
    uint64_t original_size;       /* actual (unpadded) file size before encryption */
} pfd_entry_key_t;

int pfd_create_encrypted(const char *save_dir_path, const pfd_keys_t *keys,
                          const pfd_entry_key_t *entry_keys, int num_keys);

#endif /* PS3SYNC_PFD_H */
