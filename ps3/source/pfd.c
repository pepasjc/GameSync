/*
 * PS3 Save Sync - PARAM.PFD resign engine
 *
 * Implements the PS3 PARAM.PFD binary format parsing and HMAC-SHA1
 * hash recomputation needed to resign saves for a different console/user.
 *
 * Based on analysis of:
 *   - Apollo Save Tool (bucanero/apollo-ps3) — pfd.c, pfd_util.c
 *   - flatz's pfd_sfo_tools
 *
 * Uses PolarSSL (in PSL1GHT SDK) for AES-128-CBC and HMAC-SHA1.
 */

#include "pfd.h"
#include "common.h"
#include "debug.h"

#include <polarssl/aes.h>
#include <polarssl/sha1.h>

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <dirent.h>

/* ---- Endian helpers (PS3 is big-endian, PFD uses big-endian) ---- */

static uint64_t read_be64(const uint8_t *p) {
    return ((uint64_t)p[0] << 56) | ((uint64_t)p[1] << 48) |
           ((uint64_t)p[2] << 40) | ((uint64_t)p[3] << 32) |
           ((uint64_t)p[4] << 24) | ((uint64_t)p[5] << 16) |
           ((uint64_t)p[6] <<  8) | ((uint64_t)p[7]);
}

static void write_be64(uint8_t *p, uint64_t v) {
    p[0] = (uint8_t)(v >> 56); p[1] = (uint8_t)(v >> 48);
    p[2] = (uint8_t)(v >> 40); p[3] = (uint8_t)(v >> 32);
    p[4] = (uint8_t)(v >> 24); p[5] = (uint8_t)(v >> 16);
    p[6] = (uint8_t)(v >>  8); p[7] = (uint8_t)(v);
}

/* ---- PFD on-disk layout offsets ---- */

/*
 * PARAM.PFD binary layout (verified from Apollo pfd_internal.h):
 *
 * Offset  Size    Field
 * 0       8       magic (u64 BE = 0x0000000050464442 "PFDB")
 * 8       8       version (u64 BE = 3 or 4)
 * 16      16      header_key (used as IV for AES-CBC)
 * 32      20      signature.bottom_hash   (Apollo pfd_signature_t field order)
 * 52      20      signature.top_hash
 * 72      20      signature.hash_key
 * 92      4       signature.padding
 * 96      ...     hash_table: capacity(u64), num_reserved(u64), num_used(u64),
 *                 then capacity * u64 entry_indices
 * After hash_table: entry_table (each entry = 272 bytes)
 * After entry_table: entry_signature_table (capacity * 20 bytes)
 */

/* magic(8) + version(8) = 16 bytes, then header_key(16), then signature(64) */
#define OFF_MAGIC       0
#define OFF_VERSION     8
#define OFF_HEADER_KEY  16
#define OFF_SIGNATURE   32
#define SIG_SIZE        64   /* bottom_hash(20) + top_hash(20) + hash_key(20) + pad(4) */

/* Signature sub-offsets (relative to OFF_SIGNATURE) — Apollo pfd_signature_t field order */
#define SIG_BOTTOM_HASH 0
#define SIG_TOP_HASH    20
#define SIG_HASH_KEY    40

#define OFF_HASH_TABLE  96   /* right after magic(8) + version(8) + header_key(16) + signature(64) */

/* Hash table entry index for "empty slot" (all-ones sentinel, always >= num_reserved) */
#define PFD_ENTRY_INDEX_FREE  0xFFFFFFFFFFFFFFFFULL

/* ---- Key setup ---- */

/* Static keys — de-obfuscated values (Apollo stores XOR'd with D4D16B0C5DB08791) */
static const uint8_t k_syscon_manager_key[16] = {
    0xD4, 0x13, 0xB8, 0x96, 0x63, 0xE1, 0xFE, 0x9F,
    0x75, 0x14, 0x3D, 0x3B, 0xB4, 0x56, 0x52, 0x74,
};

static const uint8_t k_keygen_key[20] = {
    0x6B, 0x1A, 0xCE, 0xA2, 0x46, 0xB7, 0x45, 0xFD,
    0x8F, 0x93, 0x76, 0x3B, 0x92, 0x05, 0x94, 0xCD,
    0x53, 0x48, 0x3B, 0x82,
};

static const uint8_t k_savegame_param_sfo_key[20] = {
    0x0C, 0x08, 0x00, 0x0E, 0x09, 0x05, 0x04, 0x04,
    0x0D, 0x01, 0x0F, 0x00, 0x04, 0x06, 0x02, 0x02,
    0x09, 0x06, 0x0D, 0x03,
};

static const uint8_t k_fallback_disc_hash_key[16] = {
    0xD1, 0xC1, 0xE1, 0x0B, 0x9C, 0x54, 0x7E, 0x68,
    0x9B, 0x80, 0x5D, 0xCD, 0x97, 0x10, 0xCE, 0x8D,
};

static const uint8_t k_authentication_id[8] = {
    0x10, 0x10, 0x00, 0x00, 0x01, 0x00, 0x00, 0x03,
};

void pfd_setup_keys(pfd_keys_t *keys) {
    memset(keys, 0, sizeof(*keys));
    memcpy(keys->syscon_manager_key,     k_syscon_manager_key,     16);
    memcpy(keys->keygen_key,             k_keygen_key,             20);
    memcpy(keys->savegame_param_sfo_key, k_savegame_param_sfo_key, 20);
    memcpy(keys->fallback_disc_hash_key, k_fallback_disc_hash_key, 16);
    memcpy(keys->authentication_id,      k_authentication_id,       8);

    /* Default disc_hash_key to fallback — override per-game if known */
    memcpy(keys->disc_hash_key, keys->fallback_disc_hash_key, 16);

    /* secure_file_id starts unset — caller must set if known */
    keys->has_secure_file_id = false;
}

/* ---- File I/O helpers ---- */

static int read_file(const char *path, uint8_t *buf, size_t max_size, size_t *actual_size) {
    FILE *fp = fopen(path, "rb");
    if (!fp) return -1;

    fseek(fp, 0, SEEK_END);
    long fsize = ftell(fp);
    fseek(fp, 0, SEEK_SET);

    if (fsize < 0 || (size_t)fsize > max_size) {
        fclose(fp);
        return -1;
    }

    size_t rd = fread(buf, 1, (size_t)fsize, fp);
    fclose(fp);

    if (rd != (size_t)fsize) return -1;
    if (actual_size) *actual_size = rd;
    return 0;
}

static int write_file(const char *path, const uint8_t *buf, size_t size) {
    FILE *fp = fopen(path, "wb");
    if (!fp) return -1;

    size_t wr = fwrite(buf, 1, size, fp);
    fclose(fp);

    return (wr == size) ? 0 : -1;
}

static int get_file_size(const char *path, uint64_t *out) {
    struct stat st;
    if (stat(path, &st) != 0) return -1;
    *out = (uint64_t)st.st_size;
    return 0;
}

/* ---- HMAC-SHA1 helpers ---- */

static void hmac_sha1(const uint8_t *key, size_t key_len,
                      const uint8_t *data, size_t data_len,
                      uint8_t out[20]) {
    sha1_hmac(key, key_len, data, data_len, out);
}

/* Compute HMAC-SHA1 of a file's contents, streaming in 4KB chunks.
 * Pumps callbacks between chunks for PS3 Lv2 kernel safety. */
static int hmac_sha1_file(const char *path,
                          const uint8_t *key, size_t key_len,
                          uint8_t out[20]) {
    FILE *fp = fopen(path, "rb");
    if (!fp) return -1;

    sha1_context ctx;
    sha1_hmac_starts(&ctx, key, key_len);

    uint8_t chunk[4096];
    size_t rd;
    while ((rd = fread(chunk, 1, sizeof(chunk), fp)) > 0) {
        sha1_hmac_update(&ctx, chunk, rd);
        pump_callbacks();
    }

    fclose(fp);
    sha1_hmac_finish(&ctx, out);
    return 0;
}

/* ---- PFD parsing & resign ---- */

/*
 * Internal context for a loaded PARAM.PFD.
 * We keep the entire file in a buffer and operate on it via offsets.
 */
typedef struct {
    uint8_t data[PFD_MAX_FILE_SIZE];
    size_t  data_size;
    int     version;   /* 3 or 4 */

    /* Pointers into data[] */
    uint8_t *header_key;     /* 16 bytes at offset 16 */
    uint8_t *sig_hash_key;   /* 20 bytes */
    uint8_t *sig_top_hash;   /* 20 bytes */
    uint8_t *sig_bottom_hash;/* 20 bytes */

    /* Hash table */
    uint64_t ht_capacity;
    uint64_t ht_num_reserved;
    uint64_t ht_num_used;
    size_t   ht_indices_off;  /* offset of the index array */

    /* Entry table */
    size_t   et_off;          /* offset of entry table */
    int      et_count;        /* actual number of entries (ht_num_used) */

    /* Entry signature table */
    size_t   est_off;         /* offset of entry signature table */

    /* Derived key */
    uint8_t real_hash_key[PFD_HASH_KEY_SIZE];

    /* Save directory path */
    char dir_path[512];
} pfd_file_t;

/* Get pointer to entry at index i (each entry is 272 bytes) */
static uint8_t *entry_ptr(pfd_file_t *pfd, int i) {
    return pfd->data + pfd->et_off + (size_t)i * PFD_ENTRY_SIZE;
}

/* Entry layout (272 bytes):
 *   0..7     additional_index (u64 BE)
 *   8..72    file_name (65 bytes, NUL-terminated)
 *   73..79   padding (7 bytes)
 *   80..143  key (64 bytes)
 *   144..223 file_hashes[4][20]  (4 * 20 = 80 bytes)
 *   224..263 padding (40 bytes)
 *   264..271 file_size (u64 BE)
 */
#define ENT_OFF_ADD_INDEX   0
#define ENT_OFF_FILENAME    8
#define ENT_OFF_KEY         80
#define ENT_OFF_HASHES      144
#define ENT_OFF_FILE_SIZE   264

static const char *entry_filename(pfd_file_t *pfd, int i) {
    return (const char *)(entry_ptr(pfd, i) + ENT_OFF_FILENAME);
}

#if 0 /* currently unused — kept for future per-game key support */
static uint64_t entry_file_size(pfd_file_t *pfd, int i) {
    return read_be64(entry_ptr(pfd, i) + ENT_OFF_FILE_SIZE);
}
#endif

static void entry_set_file_size(pfd_file_t *pfd, int i, uint64_t size) {
    write_be64(entry_ptr(pfd, i) + ENT_OFF_FILE_SIZE, size);
}

static uint8_t *entry_hash(pfd_file_t *pfd, int i, int hash_idx) {
    return entry_ptr(pfd, i) + ENT_OFF_HASHES + hash_idx * PFD_HASH_SIZE;
}

/* Get the hash table entry index at slot j */
static uint64_t ht_index(pfd_file_t *pfd, int j) {
    return read_be64(pfd->data + pfd->ht_indices_off + (size_t)j * 8);
}

/* Get pointer to entry signature at slot j */
static uint8_t *entry_sig(pfd_file_t *pfd, int j) {
    return pfd->data + pfd->est_off + (size_t)j * PFD_HASH_SIZE;
}

/* ---- Decrypt/encrypt PFD signature block ---- */

static int decrypt_signature(pfd_file_t *pfd, const pfd_keys_t *keys) {
    aes_context aes;
    uint8_t iv[16];

    /* IV = header_key */
    memcpy(iv, pfd->header_key, 16);

    aes_setkey_dec(&aes, keys->syscon_manager_key, 128);

    /* Decrypt 64 bytes of signature (hash_key + top_hash + bottom_hash + pad) */
    if (aes_crypt_cbc(&aes, AES_DECRYPT, SIG_SIZE,
                       iv, pfd->data + OFF_SIGNATURE,
                       pfd->data + OFF_SIGNATURE) != 0) {
        return -1;
    }

    /* Set up pointers to the now-decrypted fields (Apollo order: bottom, top, hash_key) */
    pfd->sig_bottom_hash = pfd->data + OFF_SIGNATURE + SIG_BOTTOM_HASH;
    pfd->sig_top_hash    = pfd->data + OFF_SIGNATURE + SIG_TOP_HASH;
    pfd->sig_hash_key    = pfd->data + OFF_SIGNATURE + SIG_HASH_KEY;

    return 0;
}

static int encrypt_signature(pfd_file_t *pfd, const pfd_keys_t *keys) {
    aes_context aes;
    uint8_t iv[16];

    memcpy(iv, pfd->header_key, 16);

    aes_setkey_enc(&aes, keys->syscon_manager_key, 128);

    if (aes_crypt_cbc(&aes, AES_ENCRYPT, SIG_SIZE,
                       iv, pfd->data + OFF_SIGNATURE,
                       pfd->data + OFF_SIGNATURE) != 0) {
        return -1;
    }
    return 0;
}

/* ---- Derive real hash key ---- */

static void derive_hash_key(pfd_file_t *pfd, const pfd_keys_t *keys) {
    if (pfd->version >= PFD_VERSION_V4) {
        /* V4: real_hash_key = HMAC-SHA1(keygen_key, sig_hash_key) */
        hmac_sha1(keys->keygen_key, PFD_HASH_KEY_SIZE,
                  pfd->sig_hash_key, PFD_HASH_KEY_SIZE,
                  pfd->real_hash_key);
    } else {
        /* V3: use hash_key directly */
        memcpy(pfd->real_hash_key, pfd->sig_hash_key, PFD_HASH_KEY_SIZE);
    }
}

/* ---- Import (read + parse) PARAM.PFD ---- */

static int pfd_import(pfd_file_t *pfd, const char *save_dir, const pfd_keys_t *keys) {
    char pfd_path[600];
    snprintf(pfd_path, sizeof(pfd_path), "%s/PARAM.PFD", save_dir);
    strncpy(pfd->dir_path, save_dir, sizeof(pfd->dir_path) - 1);

    debug_log("pfd_import: reading %s", pfd_path);

    if (read_file(pfd_path, pfd->data, PFD_MAX_FILE_SIZE, &pfd->data_size) != 0) {
        debug_log("pfd_import: failed to read PARAM.PFD");
        return -1;
    }

    /* Validate magic (u64 at offset 0) */
    uint64_t magic = read_be64(pfd->data + OFF_MAGIC);
    if (magic != PFD_MAGIC) {
        debug_log("pfd_import: bad magic 0x%016llx", (unsigned long long)magic);
        return -1;
    }

    /* Version (u64 at offset 8) */
    pfd->version = (int)read_be64(pfd->data + OFF_VERSION);
    if (pfd->version != PFD_VERSION_V3 && pfd->version != PFD_VERSION_V4) {
        debug_log("pfd_import: unsupported version %d", pfd->version);
        return -1;
    }
    debug_log("pfd_import: version=%d size=%u", pfd->version, (unsigned)pfd->data_size);

    /* Header key pointer */
    pfd->header_key = pfd->data + OFF_HEADER_KEY;

    /* Decrypt signature */
    if (decrypt_signature(pfd, keys) != 0) {
        debug_log("pfd_import: signature decryption failed");
        return -1;
    }

    /* Derive real hash key */
    derive_hash_key(pfd, keys);

    /* Parse hash table */
    size_t off = OFF_HASH_TABLE;
    pfd->ht_capacity     = read_be64(pfd->data + off); off += 8;
    pfd->ht_num_reserved = read_be64(pfd->data + off); off += 8;
    pfd->ht_num_used     = read_be64(pfd->data + off); off += 8;
    pfd->ht_indices_off  = off;

    debug_log("pfd_import: ht cap=%u reserved=%u used=%u",
              (unsigned)pfd->ht_capacity,
              (unsigned)pfd->ht_num_reserved,
              (unsigned)pfd->ht_num_used);

    /* Entry table follows hash table indices */
    pfd->et_off   = off + pfd->ht_capacity * 8;
    pfd->et_count = (int)pfd->ht_num_used;

    /* Entry signature table follows the full entry table (num_reserved slots, not just num_used).
     * Real PS3 saves can have num_reserved > num_used (pre-allocated free slots). */
    pfd->est_off = pfd->et_off + (size_t)pfd->ht_num_reserved * PFD_ENTRY_SIZE;

    /* Sanity check */
    size_t expected_end = pfd->est_off + pfd->ht_capacity * PFD_HASH_SIZE;
    if (expected_end > pfd->data_size) {
        debug_log("pfd_import: file too small (expected %u, got %u)",
                  (unsigned)expected_end, (unsigned)pfd->data_size);
        return -1;
    }

    return 0;
}

/* ---- Find entry by filename ---- */

#if 0 /* currently unused — kept for future per-game key support */
static int find_entry(pfd_file_t *pfd, const char *filename) {
    for (int i = 0; i < pfd->et_count; i++) {
        if (strcmp(entry_filename(pfd, i), filename) == 0)
            return i;
    }
    return -1;
}
#endif

/* ---- Build 20-byte hash key from secure_file_id ---- */

/*
 * Apollo Save Tool's key schedule for file hash HMAC keys:
 * Interleave secure_file_id bytes with fixed constants to produce
 * a 20-byte key used as the HMAC-SHA1 key for non-SFO file hashes.
 *
 * This is the SAME interleaving used by decrypt.c's build_iv_hash_key()
 * for the AES-CBC IV (first 16 bytes), extended to 20 bytes for HMAC.
 *
 * From Apollo pfd.c — pfd_generate_hash_key_for_secure_file_id():
 *   pos 1 -> 0x0B, pos 2 -> 0x0F, pos 5 -> 0x0E, pos 8 -> 0x0A
 *   All other positions filled sequentially from secure_file_id[0..].
 */
static void build_file_hash_key(const uint8_t secure_file_id[16],
                                 uint8_t hash_key_out[20]) {
    int j = 0;  /* index into secure_file_id */
    for (int i = 0; i < 20; i++) {
        switch (i) {
            case 1:  hash_key_out[i] = 0x0B; break;
            case 2:  hash_key_out[i] = 0x0F; break;
            case 5:  hash_key_out[i] = 0x0E; break;
            case 8:  hash_key_out[i] = 0x0A; break;
            default: hash_key_out[i] = secure_file_id[j++ % 16]; break;
        }
    }
}

/* Build the 16-byte IV used to AES-wrap PFD entry keys.
 * This mirrors decrypt.c's build_iv_hash_key() and Apollo's key schedule. */
static void build_entry_key_iv(const uint8_t secure_file_id[16],
                                uint8_t iv_out[16]) {
    int j = 0;
    for (int i = 0; i < 16; i++) {
        switch (i) {
            case 1:  iv_out[i] = 0x0B; break;
            case 2:  iv_out[i] = 0x0F; break;
            case 5:  iv_out[i] = 0x0E; break;
            case 8:  iv_out[i] = 0x0A; break;
            default: iv_out[i] = secure_file_id[j++]; break;
        }
    }
}

/* Deterministically synthesize a 64-byte entry key for files that are
 * represented in PARAM.PFD but not encrypted on disk (notably PARAM.SFO).
 * Working native saves keep a non-zero wrapped key blob for PARAM.SFO; using
 * an all-zero blob is the last major structural difference in our create path.
 */
static void synthesize_plain_entry_key(const char *filename, uint64_t file_size,
                                        uint8_t entry_key_out[64]) {
    uint32_t seed = 0x3D55AACC;
    for (const char *p = filename; *p; p++)
        seed = seed * 31 + (uint8_t)*p;
    seed ^= (uint32_t)file_size;
    seed ^= 0x53464F31U;  /* "SFO1" salt to keep it distinct from data-file keys */

    for (int k = 0; k < 64; k++) {
        seed = seed * 1103515245U + 12345U;
        entry_key_out[k] = (uint8_t)(seed >> 16);
    }
}

static int encrypt_pfd_entry_key(const uint8_t entry_key_plain[64],
                                  const uint8_t secure_file_id[16],
                                  const pfd_keys_t *keys,
                                  uint8_t entry_key_enc_out[64]) {
    uint8_t iv[16];
    aes_context aes;

    build_entry_key_iv(secure_file_id, iv);
    aes_setkey_enc(&aes, keys->syscon_manager_key, 128);

    if (aes_crypt_cbc(&aes, AES_ENCRYPT, 64, iv,
                       (uint8_t *)entry_key_plain, entry_key_enc_out) != 0) {
        return -1;
    }

    return 0;
}

/* ---- Update file hashes for one entry ---- */

/*
 * For each file entry in the PFD, compute HMAC-SHA1 hashes.
 *
 * PARAM.SFO gets 4 different hashes (using different keys):
 *   [0] FILE         — key = savegame_param_sfo_key
 *   [1] FILE_CID     — key = console_id
 *   [2] FILE_DHK_CID2 — key = disc_hash_key
 *   [3] FILE_AID_UID — key = authentication_id + user_id
 *
 * Other files get only:
 *   [0] FILE         — key = file_hash_key derived from secure_file_id
 *                       (falls back to real_hash_key if no secure_file_id)
 *   [1..3]           — zeroed
 */
static int update_entry_hashes(pfd_file_t *pfd, int entry_idx, const pfd_keys_t *keys) {
    const char *fname = entry_filename(pfd, entry_idx);
    char fpath[600];
    snprintf(fpath, sizeof(fpath), "%s/%s", pfd->dir_path, fname);

    /* Check file exists and get actual size */
    uint64_t fsize;
    if (get_file_size(fpath, &fsize) != 0) {
        debug_log("pfd update_entry_hashes: file not found: %s", fpath);
        return -1;
    }

    /* NOTE: We do NOT update file_size here. The PFD entry's file_size
     * must be the original unpadded size, which is already set correctly:
     * - pfd_resign(): preserved from the existing PFD
     * - pfd_create(): set by pfd_collect_files() (no encryption, disk=actual)
     * - pfd_create_encrypted(): set by caller using original sizes
     * Overwriting it with the on-disk size would be wrong for encrypted files
     * (which are padded to 16 bytes). */

    bool is_sfo = (strcmp(fname, "PARAM.SFO") == 0);

    if (is_sfo) {
        /* Hash 0: FILE — use savegame_param_sfo_key */
        if (hmac_sha1_file(fpath, keys->savegame_param_sfo_key, 20,
                           entry_hash(pfd, entry_idx, PFD_ENTRY_HASH_FILE)) != 0)
            return -1;
        pump_callbacks();

        /* Hash 1: FILE_CID — use console_id (PSID) */
        if (hmac_sha1_file(fpath, keys->console_id, PFD_KEY_SIZE,
                           entry_hash(pfd, entry_idx, PFD_ENTRY_HASH_FILE_CID)) != 0)
            return -1;
        pump_callbacks();

        /* Hash 2: FILE_DHK_CID2 — use disc_hash_key */
        if (hmac_sha1_file(fpath, keys->disc_hash_key, PFD_KEY_SIZE,
                           entry_hash(pfd, entry_idx, PFD_ENTRY_HASH_FILE_DHK_CID2)) != 0)
            return -1;
        pump_callbacks();

        /* Hash 3: FILE_AID_UID — derive 20-byte key from user_id (Apollo algorithm):
         * cycle user_id bytes, substituting fixed values 11 at pos 3 and 14 at pos 7 */
        {
            uint8_t aid_uid_key[PFD_HASH_KEY_SIZE];
            memset(aid_uid_key, 0, PFD_HASH_KEY_SIZE);
            int j = 0;
            for (int ii = 0; ii < (int)PFD_HASH_KEY_SIZE; ii++) {
                switch (ii) {
                    case 3: aid_uid_key[ii] = 11; break;
                    case 7: aid_uid_key[ii] = 14; break;
                    default: aid_uid_key[ii] = keys->user_id[j++ % 8]; break;
                }
            }
            if (hmac_sha1_file(fpath, aid_uid_key, PFD_HASH_KEY_SIZE,
                               entry_hash(pfd, entry_idx, PFD_ENTRY_HASH_FILE_AID_UID)) != 0)
                return -1;
        }
        pump_callbacks();

    } else {
        /* Non-SFO file: hash 0 only.
         * If a game-specific secure_file_id is known, derive the 20-byte
         * HMAC key from it (Apollo's pfd_generate_hash_key_for_secure_file_id).
         * Otherwise fall back to real_hash_key (PFD signature-derived key). */
        const uint8_t *hash_key;
        size_t hash_key_len;
        uint8_t file_hash_key[PFD_HASH_KEY_SIZE];

        if (keys->has_secure_file_id) {
            build_file_hash_key(keys->secure_file_id, file_hash_key);
            hash_key = file_hash_key;
            hash_key_len = PFD_HASH_KEY_SIZE;
            debug_log("pfd update_entry_hashes: using secure_file_id-derived key for %s", fname);
        } else {
            hash_key = pfd->real_hash_key;
            hash_key_len = PFD_HASH_KEY_SIZE;
            debug_log("pfd update_entry_hashes: using real_hash_key (no secure_file_id) for %s", fname);
        }

        if (hmac_sha1_file(fpath, hash_key, hash_key_len,
                           entry_hash(pfd, entry_idx, PFD_ENTRY_HASH_FILE)) != 0)
            return -1;
        pump_callbacks();

        /* Hashes 1-3: zero for non-SFO files */
        memset(entry_hash(pfd, entry_idx, PFD_ENTRY_HASH_FILE_CID), 0, PFD_HASH_SIZE);
        memset(entry_hash(pfd, entry_idx, PFD_ENTRY_HASH_FILE_DHK_CID2), 0, PFD_HASH_SIZE);
        memset(entry_hash(pfd, entry_idx, PFD_ENTRY_HASH_FILE_AID_UID), 0, PFD_HASH_SIZE);
    }

    return 0;
}

/* ---- Compute entry signature hashes ---- */

/*
 * For each hash table bucket, compute HMAC-SHA1 of the chain of entries
 * that hash to that bucket. The chain is formed by following the
 * additional_index field of each entry.
 *
 * Per entry: file_name(65 bytes) + key_data(192 bytes), skipping the
 * 7 padding bytes between them (Apollo pfd_calculate_entry_hash).
 */
static int update_entry_signatures(pfd_file_t *pfd) {
    for (uint64_t j = 0; j < pfd->ht_capacity; j++) {
        uint64_t idx = ht_index(pfd, (int)j);
        uint8_t *sig = entry_sig(pfd, (int)j);

        if (idx >= pfd->ht_num_reserved) {
            /* Empty bucket: HMAC-SHA1 of empty input (Apollo pfd_calculate_default_hash) */
            hmac_sha1(pfd->real_hash_key, PFD_HASH_KEY_SIZE, NULL, 0, sig);
            continue;
        }

        /* Build chain of entry data for all entries in this bucket.
         * Apollo hashes: file_name(65 bytes) + key_data(192 bytes) per entry,
         * skipping the 7 padding bytes between them. */
        sha1_context ctx;
        sha1_hmac_starts(&ctx, pfd->real_hash_key, PFD_HASH_KEY_SIZE);

        uint64_t cur = idx;
        int safety = 0;
        while (cur < pfd->ht_num_reserved && safety < 256) {
            uint8_t *ep = entry_ptr(pfd, (int)cur);
            /* file_name: 65 bytes at entry offset 8 */
            sha1_hmac_update(&ctx, ep + ENT_OFF_FILENAME, PFD_MAX_FILE_NAME);
            /* key+hashes+padding+size: 192 bytes at entry offset 80 (= ENT_OFF_KEY),
             * skipping the 7 padding bytes at offsets 73..79 */
            sha1_hmac_update(&ctx, ep + ENT_OFF_KEY, PFD_ENTRY_SIZE - ENT_OFF_KEY);

            /* Follow chain: next = additional_index of this entry */
            cur = read_be64(ep + ENT_OFF_ADD_INDEX);
            safety++;
        }

        sha1_hmac_finish(&ctx, sig);
        pump_callbacks();
    }

    return 0;
}

/* ---- Compute bottom and top hashes ---- */

/*
 * bottom_hash = HMAC-SHA1(real_hash_key, all_entry_signatures_concatenated)
 * top_hash    = HMAC-SHA1(real_hash_key, hash_table_data)
 *
 * hash_table_data = capacity(8) + num_reserved(8) + num_used(8) + indices(cap*8)
 */
static int update_top_bottom_hashes(pfd_file_t *pfd) {
    /* Bottom hash: HMAC of all entry signatures */
    size_t total_sig_size = pfd->ht_capacity * PFD_HASH_SIZE;
    hmac_sha1(pfd->real_hash_key, PFD_HASH_KEY_SIZE,
              pfd->data + pfd->est_off, total_sig_size,
              pfd->sig_bottom_hash);
    pump_callbacks();

    /* Top hash: HMAC of entire hash table region
     * (capacity + num_reserved + num_used + all indices) */
    size_t ht_data_size = 24 + pfd->ht_capacity * 8;  /* 3 * u64 + cap * u64 */
    hmac_sha1(pfd->real_hash_key, PFD_HASH_KEY_SIZE,
              pfd->data + OFF_HASH_TABLE, ht_data_size,
              pfd->sig_top_hash);
    pump_callbacks();

    return 0;
}

/* ---- Export (re-encrypt + write) PARAM.PFD ---- */

static int pfd_export(pfd_file_t *pfd, const pfd_keys_t *keys) {
    /* Re-encrypt signature block */
    if (encrypt_signature(pfd, keys) != 0) {
        debug_log("pfd_export: encryption failed");
        return -1;
    }

    /* Write to file */
    char pfd_path[600];
    snprintf(pfd_path, sizeof(pfd_path), "%s/PARAM.PFD", pfd->dir_path);

    debug_log("pfd_export: writing %s (%u bytes)", pfd_path, (unsigned)pfd->data_size);
    if (write_file(pfd_path, pfd->data, pfd->data_size) != 0) {
        debug_log("pfd_export: write failed");
        return -1;
    }

    return 0;
}

/* ---- pfd_create: build a fresh PARAM.PFD from scratch ---- */

/*
 * Used when a save was synced from an emulator (e.g. RPCS3) that never
 * generates PARAM.PFD. Without it a real PS3 refuses to load the save.
 *
 * We enumerate the protected files in save_dir_path, build the standard
 * fixed-size save-data tables used by real PS3 saves, compute all HMAC-SHA1
 * file hashes for the target console/user, and write a valid PARAM.PFD.
 *
 * If entry_keys is non-NULL, the encrypted entry keys are written into
 * the PFD entry table before computing HMAC signatures.
 */

#define PFD_MAX_ENTRIES            114  /* reserved entry slots in real save PFDs */
#define PFD_HASH_TABLE_CAPACITY     57  /* fixed X/Y table size used by PS3 saves */
#define PFD_RESERVED_ENTRY_SLOTS   114  /* fixed protected-file table size */

/* Hash used by the PS3 for filename → bucket assignment (Apollo: (h<<5)-h+c = h*31+c) */
static uint64_t pfd_hash_filename(const char *name) {
    uint64_t h = 0;
    for (; *name; name++)
        h = (h << 5) - h + (uint8_t)*name;
    return h;
}

static bool pfd_should_track_file(const char *name) {
    if (strcmp(name, "PARAM.PFD") == 0) return false; /* skip self */

    /* Keep PARAM.SFO protected, but skip metadata assets that are present
     * on disk and shown on XMB without being tracked by the working PFDs we
     * compared (ICON0/PIC1/etc.). */
    if (strcmp(name, "PARAM.SFO") == 0) return true;
    if (strcmp(name, "ICON0.PNG") == 0) return false;
    if (strcmp(name, "ICON1.PAM") == 0) return false;
    if (strcmp(name, "PIC0.PNG") == 0) return false;
    if (strcmp(name, "PIC1.PNG") == 0) return false;
    if (strcmp(name, "PIC1.PAM") == 0) return false;
    if (strcmp(name, "SND0.AT3") == 0) return false;

    return true;
}

/* Enumerate protected files in dir_path.
 * PARAM.SFO is always placed first to match the accepted PS3 PFDs we observed.
 * The remaining protected files are sorted by name for deterministic output. */
static int pfd_collect_files(const char *dir_path,
                              char names[PFD_MAX_ENTRIES][PFD_MAX_FILE_NAME],
                              uint64_t sizes[PFD_MAX_ENTRIES]) {
    DIR *d = opendir(dir_path);
    if (!d) return -1;

    int count = 0;
    struct dirent *ent;
    while ((ent = readdir(d)) != NULL && count < PFD_MAX_ENTRIES) {
        if (ent->d_name[0] == '.') continue;                    /* . and .. */
        if (!pfd_should_track_file(ent->d_name)) continue;

        char fpath[600];
        snprintf(fpath, sizeof(fpath), "%s/%s", dir_path, ent->d_name);

        struct stat st;
        if (stat(fpath, &st) != 0 || !S_ISREG(st.st_mode)) continue;

        strncpy(names[count], ent->d_name, PFD_MAX_FILE_NAME - 1);
        names[count][PFD_MAX_FILE_NAME - 1] = '\0';
        sizes[count] = (uint64_t)st.st_size;
        count++;
    }
    closedir(d);

    /* PARAM.SFO first, then insertion-sort the rest by name. */
    int start = 0;
    for (int i = 0; i < count; i++) {
        if (strcmp(names[i], "PARAM.SFO") == 0) {
            if (i != 0) {
                char tmp_name[PFD_MAX_FILE_NAME];
                uint64_t tmp_size = sizes[i];
                strncpy(tmp_name, names[i], PFD_MAX_FILE_NAME);
                for (int j = i; j > 0; j--) {
                    strncpy(names[j], names[j - 1], PFD_MAX_FILE_NAME);
                    sizes[j] = sizes[j - 1];
                }
                strncpy(names[0], tmp_name, PFD_MAX_FILE_NAME);
                sizes[0] = tmp_size;
            }
            start = 1;
            break;
        }
    }

    for (int i = 1; i < count; i++) {
        if (i < start) continue;
        char tmp_name[PFD_MAX_FILE_NAME];
        uint64_t tmp_size = sizes[i];
        strncpy(tmp_name, names[i], PFD_MAX_FILE_NAME);
        int j = i - 1;
        while (j >= start && strcmp(names[j], tmp_name) > 0) {
            strncpy(names[j + 1], names[j], PFD_MAX_FILE_NAME);
            sizes[j + 1] = sizes[j];
            j--;
        }
        strncpy(names[j + 1], tmp_name, PFD_MAX_FILE_NAME);
        sizes[j + 1] = tmp_size;
    }

    return count;
}

int pfd_create(const char *save_dir_path, const pfd_keys_t *keys) {
    return pfd_create_encrypted(save_dir_path, keys, NULL, 0);
}

int pfd_create_encrypted(const char *save_dir_path, const pfd_keys_t *keys,
                          const pfd_entry_key_t *entry_keys, int num_keys) {
    debug_log("pfd_create: building PARAM.PFD for %s (entry_keys=%s)",
              save_dir_path, entry_keys ? "yes" : "no");

    /* --- Collect files --- */
    static char  names[PFD_MAX_ENTRIES][PFD_MAX_FILE_NAME];
    static uint64_t file_sizes[PFD_MAX_ENTRIES];

    int num_files = pfd_collect_files(save_dir_path, names, file_sizes);
    if (num_files <= 0) {
        debug_log("pfd_create: no files found in %s", save_dir_path);
        return -1;
    }
    if (num_files > PFD_RESERVED_ENTRY_SLOTS) {
        debug_log("pfd_create: too many protected files (%d)", num_files);
        return -1;
    }
    debug_log("pfd_create: %d protected files to hash", num_files);

    /* --- Determine layout ---
     *
     * Real PS3 save-data PFDs use the fixed 57/114 table geometry documented
     * by Apollo/psdevwiki. The sentinel value for "no entry" in hash table
     * slots and additional_index chain terminators is num_reserved
     * (one-past-end), NOT 0xFFFFFFFFFFFFFFFF.
     *
     * The file is padded to PFD_MAX_FILE_SIZE (32KB) to match real PS3
     * firmware expectations. */
    uint64_t capacity     = PFD_HASH_TABLE_CAPACITY;
    uint64_t num_reserved = PFD_RESERVED_ENTRY_SLOTS;

    size_t content_size = (size_t)OFF_HASH_TABLE
        + 24                                          /* ht header: cap + reserved + used */
        + (size_t)capacity     * 8                    /* ht index slots */
        + (size_t)num_reserved * PFD_ENTRY_SIZE       /* entry table (all reserved slots) */
        + (size_t)capacity     * PFD_HASH_SIZE;       /* entry signature table */

    /* Pad to 32KB to match real PS3 PFD files */
    size_t total_size = PFD_MAX_FILE_SIZE;  /* 32768 */
    if (content_size > total_size) {
        debug_log("pfd_create: layout too large (%u bytes)", (unsigned)content_size);
        return -1;
    }

    /* --- Allocate and zero pfd context (on heap: ~33KB) --- */
    pfd_file_t *pfd = (pfd_file_t *)malloc(sizeof(pfd_file_t));
    if (!pfd) { debug_log("pfd_create: malloc failed"); return -1; }
    memset(pfd, 0, sizeof(*pfd));

    strncpy(pfd->dir_path, save_dir_path, sizeof(pfd->dir_path) - 1);
    pfd->data_size = total_size;
    pfd->version   = PFD_VERSION_V3;  /* v3 is most common; v4 adds extra key derivation */

    /* --- Write fixed header --- */
    write_be64(pfd->data + OFF_MAGIC,   PFD_MAGIC);
    write_be64(pfd->data + OFF_VERSION, (uint64_t)PFD_VERSION_V3);

    /* header_key (AES-CBC IV for signature encryption).
     * Real PS3 PFDs have a non-zero header key.  Generate a deterministic
     * one from the save directory path so the output is reproducible.
     * We use HMAC-SHA1(savegame_param_sfo_key, path) and take 16 bytes. */
    pfd->header_key = pfd->data + OFF_HEADER_KEY;
    {
        uint8_t hk_tmp[20];
        hmac_sha1(keys->savegame_param_sfo_key, PFD_HASH_KEY_SIZE,
                  (const uint8_t *)save_dir_path, strlen(save_dir_path),
                  hk_tmp);
        memcpy(pfd->header_key, hk_tmp, 16);
    }

    /* --- Set up signature block pointers (Apollo order: bottom, top, hash_key) --- */
    pfd->sig_bottom_hash = pfd->data + OFF_SIGNATURE + SIG_BOTTOM_HASH;
    pfd->sig_top_hash    = pfd->data + OFF_SIGNATURE + SIG_TOP_HASH;
    pfd->sig_hash_key    = pfd->data + OFF_SIGNATURE + SIG_HASH_KEY;

    /* Choose sig_hash_key: HMAC-SHA1(keygen_key, save_dir_path) — deterministic, unique per save */
    hmac_sha1(keys->keygen_key, PFD_HASH_KEY_SIZE,
              (const uint8_t *)save_dir_path, strlen(save_dir_path),
              pfd->sig_hash_key);

    /* Derive real_hash_key (v3: uses sig_hash_key directly) */
    derive_hash_key(pfd, keys);

    /* --- Write hash table header --- */
    size_t off = OFF_HASH_TABLE;
    pfd->ht_capacity     = capacity;
    pfd->ht_num_reserved = num_reserved;
    pfd->ht_num_used     = (uint64_t)num_files;
    write_be64(pfd->data + off, capacity);      off += 8;
    write_be64(pfd->data + off, num_reserved);  off += 8;
    write_be64(pfd->data + off, (uint64_t)num_files); off += 8;  /* num_used */
    pfd->ht_indices_off = off;

    /* Initialize all bucket slots to the sentinel value (= num_reserved).
     * Real PS3 PFDs use num_reserved as "no entry" sentinel, NOT 0xFFFFFFFFFFFFFFFF. */
    for (uint64_t j = 0; j < capacity; j++)
        write_be64(pfd->data + off + j * 8, num_reserved);

    /* --- Entry table and signature table offsets --- */
    pfd->et_off   = off + capacity * 8;
    pfd->et_count = num_files;
    pfd->est_off  = pfd->et_off + (size_t)num_reserved * PFD_ENTRY_SIZE;

    /* Unused entry slots (num_files..num_reserved-1) are already zeroed
     * from the memset above, which matches the enc reference. */

    /* --- Fill entry table and insert into hash table --- */
    for (int i = 0; i < num_files; i++) {
        uint8_t *ep = entry_ptr(pfd, i);
        memset(ep, 0, PFD_ENTRY_SIZE);

        /* Filename (NUL-terminated, up to 64 chars) */
        strncpy((char *)(ep + ENT_OFF_FILENAME), names[i], PFD_MAX_FILE_NAME - 1);

        /* File size */
        entry_set_file_size(pfd, i, file_sizes[i]);

        /* additional_index defaults to sentinel (= num_reserved, end-of-chain) */
        write_be64(ep + ENT_OFF_ADD_INDEX, num_reserved);

        /* Insert into hash table: prepend to bucket chain */
        uint64_t bucket = pfd_hash_filename(names[i]) % capacity;
        uint64_t existing_head = ht_index(pfd, (int)bucket);
        write_be64(ep + ENT_OFF_ADD_INDEX, existing_head);   /* chain to old head */
        write_be64(pfd->data + pfd->ht_indices_off + bucket * 8, (uint64_t)i);

        /* Write encrypted entry key if provided */
        bool wrote_entry_key = false;
        if (entry_keys && num_keys > 0) {
            for (int k = 0; k < num_keys; k++) {
                if (strcmp(entry_keys[k].filename, names[i]) == 0) {
                    memcpy(ep + ENT_OFF_KEY, entry_keys[k].encrypted_key, 64);
                    /* Use the original (unpadded) file size — the encrypted
                     * file on disk is padded to 16 bytes but the PFD must
                     * store the actual data size for the PS3 to decrypt. */
                    if (entry_keys[k].original_size > 0) {
                        entry_set_file_size(pfd, i, entry_keys[k].original_size);
                    }
                    debug_log("pfd_create: set entry key for %s (orig_size=%llu)",
                              names[i], (unsigned long long)entry_keys[k].original_size);
                    wrote_entry_key = true;
                    break;
                }
            }
        }

        /* PARAM.SFO is not encrypted on disk, but working native saves still
         * carry a wrapped non-zero 64-byte key blob in its PFD entry. */
        if (!wrote_entry_key &&
            strcmp(names[i], "PARAM.SFO") == 0 &&
            keys->has_secure_file_id) {
            uint8_t sfo_plain_key[64];
            uint8_t sfo_enc_key[64];

            synthesize_plain_entry_key(names[i], file_sizes[i], sfo_plain_key);
            if (encrypt_pfd_entry_key(sfo_plain_key, keys->secure_file_id,
                                      keys, sfo_enc_key) == 0) {
                memcpy(ep + ENT_OFF_KEY, sfo_enc_key, 64);
                debug_log("pfd_create: synthesized PARAM.SFO entry key");
            } else {
                debug_log("pfd_create: failed to synthesize PARAM.SFO entry key");
            }
        }
    }

    pump_callbacks();

    /* --- Compute file hashes for all entries --- */
    debug_log("pfd_create: computing file hashes");
    for (int i = 0; i < num_files; i++) {
        const char *fname = entry_filename(pfd, i);
        debug_log("pfd_create: hashing [%d] %s", i, fname);
        if (update_entry_hashes(pfd, i, keys) != 0) {
            debug_log("pfd_create: failed to hash %s", fname);
            free(pfd);
            return -1;
        }
        pump_callbacks();
    }

    /* --- Compute entry signatures --- */
    debug_log("pfd_create: computing entry signatures");
    if (update_entry_signatures(pfd) != 0) {
        debug_log("pfd_create: entry signatures failed");
        free(pfd);
        return -1;
    }
    pump_callbacks();

    /* --- Compute top/bottom hashes --- */
    debug_log("pfd_create: computing top/bottom hashes");
    if (update_top_bottom_hashes(pfd) != 0) {
        debug_log("pfd_create: top/bottom hash failed");
        free(pfd);
        return -1;
    }
    pump_callbacks();

    /* --- Encrypt signature block and write PARAM.PFD --- */
    int result = pfd_export(pfd, keys);
    debug_log("pfd_create: %s", result == 0 ? "success" : "export failed");

    free(pfd);
    return result;
}

/* ---- Public API ---- */

int pfd_resign(const char *save_dir_path, const pfd_keys_t *keys) {
    /* Allocate PFD context on heap — it's ~33KB, too large for 64KB stack */
    pfd_file_t *pfd = (pfd_file_t *)malloc(sizeof(pfd_file_t));
    if (!pfd) {
        debug_log("pfd_resign: malloc failed");
        return -1;
    }
    memset(pfd, 0, sizeof(*pfd));

    int result = -1;

    debug_log("pfd_resign: starting resign for %s", save_dir_path);
    pump_callbacks();

    /* 1. Import: read + parse + decrypt */
    if (pfd_import(pfd, save_dir_path, keys) != 0) {
        debug_log("pfd_resign: import failed");
        goto done;
    }
    pump_callbacks();

    /* 2. Update file hashes for ALL entries */
    debug_log("pfd_resign: updating %d file entries", pfd->et_count);
    for (int i = 0; i < pfd->et_count; i++) {
        const char *fname = entry_filename(pfd, i);
        debug_log("pfd_resign: hashing [%d] %s", i, fname);

        if (update_entry_hashes(pfd, i, keys) != 0) {
            debug_log("pfd_resign: failed to update hashes for %s", fname);
            goto done;
        }
        pump_callbacks();
    }

    /* 3. Recompute entry signatures */
    debug_log("pfd_resign: updating entry signatures");
    if (update_entry_signatures(pfd) != 0) {
        debug_log("pfd_resign: entry signature update failed");
        goto done;
    }
    pump_callbacks();

    /* 4. Recompute bottom + top hashes */
    debug_log("pfd_resign: updating top/bottom hashes");
    if (update_top_bottom_hashes(pfd) != 0) {
        debug_log("pfd_resign: top/bottom hash update failed");
        goto done;
    }
    pump_callbacks();

    /* 5. Export: re-encrypt + write */
    if (pfd_export(pfd, keys) != 0) {
        debug_log("pfd_resign: export failed");
        goto done;
    }

    debug_log("pfd_resign: success");
    result = 0;

done:
    free(pfd);
    return result;
}
