/*
 * PS3 Save Sync - Save data decryption / encryption
 *
 * Decrypts PS3 HDD save data files so they can be uploaded to the
 * server in a format usable by RPCS3 and other emulators.
 *
 * Encrypts plaintext save files (from RPCS3) so they can be written
 * back to the PS3 HDD in the format the console expects.
 *
 * PS3 HDD saves have their data files encrypted per-file using keys
 * stored in PARAM.PFD. The decryption process:
 *   1. Parse PARAM.PFD to get entry keys for each file
 *   2. Decrypt entry keys using syscon_manager_key + secure_file_id
 *   3. Decrypt file data using the decrypted entry key
 *   4. Write decrypted files to a temp directory
 *
 * The encryption process (inverse):
 *   1. Generate a random entry key per file
 *   2. Encrypt file data in-place using the entry key
 *   3. Encrypt the entry key using syscon_manager_key + secure_file_id
 *   4. Return encrypted entry keys for storage in PARAM.PFD
 *
 * Based on analysis of Apollo Save Tool's pfd.c decryption code.
 */

#ifndef PS3SYNC_DECRYPT_H
#define PS3SYNC_DECRYPT_H

#include "common.h"
#include <stdbool.h>
#include <stdint.h>

/* ---- Encrypt output structures ---- */

#define ENCRYPT_MAX_FILES 64

/* Encrypted entry key for one file (to be stored in PARAM.PFD) */
typedef struct {
    char    filename[65];          /* filename (NUL-terminated) */
    uint8_t encrypted_key[64];    /* AES-CBC encrypted entry key */
    uint64_t original_size;       /* actual (unpadded) file size before encryption */
} encrypt_key_entry_t;

/* Collection of encrypted entry keys from encrypt_save() */
typedef struct {
    encrypt_key_entry_t entries[ENCRYPT_MAX_FILES];
    int count;
} encrypt_keys_t;

/* ---- Decryption API ---- */

/*
 * Decrypt an HDD save directory to a temporary location.
 *
 * Reads PARAM.PFD from save_dir_path, decrypts all data files listed
 * in it using the game's secure_file_id from the gamekeys database,
 * and writes decrypted copies to a temp directory.
 *
 * Files NOT listed in PARAM.PFD are copied as-is (assumed unencrypted).
 * PARAM.PFD is excluded from the output (signature file not needed by
 * emulators). PARAM.SFO is copied as-is (emulators need it for game info).
 *
 * title: the save's TitleInfo (provides local_path, game_code)
 * out_path: receives the path to the temp directory containing
 *           decrypted files (caller must call decrypt_cleanup() when done)
 * out_path_size: size of the out_path buffer
 *
 * Returns 0 on success, negative on error:
 *   -1 = general error (no PFD, no gamekey, I/O failure)
 *   -2 = gamekeys database not loaded
 *   -3 = secure_file_id not found for this game
 */
int decrypt_save(const TitleInfo *title,
                 char *out_path, size_t out_path_size);

/*
 * Clean up a temporary decrypted save directory.
 * Removes all files and the directory itself.
 */
void decrypt_cleanup(const char *temp_path);

/*
 * Re-encrypt plaintext data files in title->local_path using the entry
 * keys already stored in the existing PARAM.PFD.
 *
 * Used for RPCS3 -> PS3 sync when the save slot already exists: the bundle
 * contains unencrypted data files; we re-encrypt each one using the same
 * per-file key that was originally stored in PARAM.PFD so that pfd_resign
 * only needs to update the file hashes and re-sign, leaving the entry keys
 * (and PARAM.SFO) completely untouched.
 *
 * Only processes files that have an entry in PARAM.PFD.  Files not listed
 * (icons, PARAM.SFO) are left as-is.
 *
 * Returns 0 on success, negative on error:
 *   -1 = general error (no PFD, I/O failure)
 *   -2 = gamekeys database not loaded
 *   -3 = secure_file_id not found for any file
 */
int reencrypt_files_from_pfd(const TitleInfo *title);

/* ---- Encryption API ---- */

/*
 * Encrypt plaintext save data files in-place for PS3 HDD storage.
 *
 * For each data file in title->local_path (excluding PARAM.SFO,
 * PARAM.PFD, icons, and media files):
 *   1. Generates a random 64-byte entry key
 *   2. Encrypts the file data in-place
 *   3. Encrypts the entry key for PARAM.PFD storage
 *
 * The encrypted entry keys are returned in out_keys, which must be
 * passed to pfd_create_encrypted() so the keys are stored in PARAM.PFD.
 *
 * title: the save's TitleInfo (provides local_path, game_code)
 * out_keys: receives the encrypted entry keys for each file
 *
 * Returns 0 on success, negative on error:
 *   -1 = general error
 *   -2 = gamekeys database not loaded
 *   -3 = secure_file_id not found for this game
 */
int encrypt_save(const TitleInfo *title, encrypt_keys_t *out_keys);

#endif /* PS3SYNC_DECRYPT_H */
