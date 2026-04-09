/*
 * PS3 Save Sync - Save data decryption
 *
 * Decrypts PS3 HDD save data files using keys from PARAM.PFD and
 * the per-game secure_file_id from the gamekeys database.
 *
 * Algorithm (from Apollo Save Tool pfd.c):
 *
 * 1. Read PARAM.PFD, parse entry table to get per-file entry_key (64 bytes)
 * 2. Build iv_hash_key from secure_file_id by interleaving with constants:
 *      pos 1->11, pos 2->15, pos 5->14, pos 8->10, rest sequential
 * 3. AES-128-CBC decrypt the 64-byte entry_key using syscon_manager_key
 *    as AES key and iv_hash_key as IV
 * 4. For each 16-byte block i of file data:
 *      counter_key = block_index(u64 BE) + 8 zero bytes
 *      encrypted_counter = AES-128-ECB-encrypt(entry_key[0:16], counter_key)
 *      decrypted_block = AES-128-ECB-decrypt(entry_key[0:16], data_block)
 *      plaintext = decrypted_block XOR encrypted_counter
 * 5. Actual file size stored in PFD entry (data padded to 16 bytes)
 *
 * Uses PolarSSL (in PSL1GHT SDK) for AES-128.
 */

#include "decrypt.h"
#include "gamekeys.h"
#include "pfd.h"
#include "debug.h"
#include "ui.h"

#include <polarssl/aes.h>

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <dirent.h>

/* ---- Constants ---- */

#define DECRYPT_TEMP_ROOT "/dev_hdd0/tmp/3dssync"
#define PFD_FILE_ALIGNMENT 16

/* PFD entry layout (must match pfd.c) */
#define DENT_OFF_FILENAME    8
#define DENT_OFF_KEY         80
#define DENT_OFF_FILE_SIZE   264
#define DENT_SIZE            272
#define DENT_MAX_NAME        65

/* PFD header offsets — magic and version are u64 (8 bytes each), not u32!
 * Verified from Apollo pfd_internal.h: pfd_header_t { u64 magic; u64 version; }
 * PFD_HEADER_SIZE = 16, PFD_HEADER_KEY_OFFSET = 16, PFD_SIGNATURE_OFFSET = 32,
 * PFD_HASH_TABLE_OFFSET = 96 */
#define DPFD_MAGIC           0x50464442ULL
#define DPFD_OFF_MAGIC       0     /* u64 BE */
#define DPFD_OFF_VERSION     8     /* u64 BE */
#define DPFD_OFF_HEADER_KEY  16    /* 16 bytes */
#define DPFD_OFF_SIGNATURE   32    /* 64 bytes */
#define DPFD_SIG_SIZE        64
#define DPFD_OFF_HASH_TABLE  96    /* 16+16+64 = 96 */

#define DPFD_MAX_FILE_SIZE   32768

/* syscon_manager_key — de-obfuscated (Apollo stores XOR'd with D4D16B0C5DB08791) */
static const uint8_t k_syscon_mgr_key[16] = {
    0xD4, 0x13, 0xB8, 0x96, 0x63, 0xE1, 0xFE, 0x9F,
    0x75, 0x14, 0x3D, 0x3B, 0xB4, 0x56, 0x52, 0x74,
};

/* ---- Endian helpers ---- */

static uint32_t d_read_be32(const uint8_t *p) {
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) |
           ((uint32_t)p[2] <<  8) | ((uint32_t)p[3]);
}

static uint64_t d_read_be64(const uint8_t *p) {
    return ((uint64_t)p[0] << 56) | ((uint64_t)p[1] << 48) |
           ((uint64_t)p[2] << 40) | ((uint64_t)p[3] << 32) |
           ((uint64_t)p[4] << 24) | ((uint64_t)p[5] << 16) |
           ((uint64_t)p[6] <<  8) | ((uint64_t)p[7]);
}

static void d_write_be64(uint8_t *p, uint64_t v) {
    p[0] = (uint8_t)(v >> 56); p[1] = (uint8_t)(v >> 48);
    p[2] = (uint8_t)(v >> 40); p[3] = (uint8_t)(v >> 32);
    p[4] = (uint8_t)(v >> 24); p[5] = (uint8_t)(v >> 16);
    p[6] = (uint8_t)(v >>  8); p[7] = (uint8_t)(v);
}

/* ---- Build iv_hash_key from secure_file_id ---- */

/*
 * Apollo Save Tool's key schedule for the IV:
 * Interleave secure_file_id bytes with fixed constants.
 *
 * From Apollo pfd.c / pfd_util.c (pfd_get_entry_key / _get_aes_details_pfd):
 *   iv[0..15] built from secure_file_id with constants inserted at
 *   specific positions: pos 1=0x0B, pos 2=0x0F, pos 5=0x0E, pos 8=0x0A
 *   Other positions filled sequentially from secure_file_id.
 */
static void build_iv_hash_key(const uint8_t secure_file_id[16],
                               uint8_t iv_out[16]) {
    int j = 0;  /* index into secure_file_id */
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

/* ---- Decrypt entry key ---- */

/*
 * Decrypt the 64-byte entry key from a PFD entry.
 * Uses AES-128-CBC with syscon_manager_key and iv_hash_key derived
 * from the game's secure_file_id.
 *
 * entry_key_encrypted: 64 bytes from PFD entry at offset 80
 * secure_file_id: 16 bytes from gamekeys database
 * entry_key_out: receives 64 bytes of decrypted key
 */
static int decrypt_entry_key(const uint8_t entry_key_encrypted[64],
                              const uint8_t secure_file_id[16],
                              uint8_t entry_key_out[64]) {
    uint8_t iv[16];
    aes_context aes;

    build_iv_hash_key(secure_file_id, iv);

    aes_setkey_dec(&aes, k_syscon_mgr_key, 128);
    if (aes_crypt_cbc(&aes, AES_DECRYPT, 64, iv,
                       entry_key_encrypted, entry_key_out) != 0) {
        debug_log("decrypt_entry_key: AES-CBC decrypt failed");
        return -1;
    }

    return 0;
}

/* ---- Decrypt file data ---- */

/*
 * Decrypt file data using the custom counter mode:
 *   For each 16-byte block i:
 *     counter_key = i (u64 BE) + 8 zero bytes
 *     enc_counter = AES-ECB-encrypt(entry_key[0:16], counter_key)
 *     dec_block   = AES-ECB-decrypt(entry_key[0:16], data_block)
 *     plaintext   = dec_block XOR enc_counter
 *
 * data is modified in-place.
 * data_len should be aligned to 16 bytes (padded size from file).
 * actual_size is the real file size from the PFD entry.
 */
static int decrypt_file_data(uint8_t *data, size_t data_len,
                              const uint8_t entry_key[64],
                              uint64_t actual_size) {
    aes_context aes_enc, aes_dec;
    size_t num_blocks = data_len / 16;

    /* Use first 16 bytes of entry_key as the AES key */
    aes_setkey_enc(&aes_enc, entry_key, 128);
    aes_setkey_dec(&aes_dec, entry_key, 128);

    for (size_t i = 0; i < num_blocks; i++) {
        uint8_t counter_key[16];
        uint8_t enc_counter[16];
        uint8_t dec_block[16];
        uint8_t *block = data + i * 16;

        /* Build counter: block index as big-endian u64 + 8 zero bytes */
        d_write_be64(counter_key, (uint64_t)i);
        memset(counter_key + 8, 0, 8);

        /* Encrypt counter with entry key */
        aes_crypt_ecb(&aes_enc, AES_ENCRYPT, counter_key, enc_counter);

        /* Decrypt data block with entry key */
        aes_crypt_ecb(&aes_dec, AES_DECRYPT, block, dec_block);

        /* XOR to get plaintext */
        for (int b = 0; b < 16; b++)
            block[b] = dec_block[b] ^ enc_counter[b];

        /* Pump every 256 blocks (~4KB) to keep the kernel happy */
        if ((i & 0xFF) == 0) pump_callbacks();
    }

    (void)actual_size;
    return 0;
}

/* ---- Encrypt entry key ---- */

/*
 * Encrypt a 64-byte entry key for storage in PARAM.PFD.
 * Inverse of decrypt_entry_key: AES-128-CBC encrypt with syscon_manager_key
 * and iv_hash_key derived from the game's secure_file_id.
 *
 * entry_key_plain:     64 bytes of plaintext key
 * secure_file_id:      16 bytes from gamekeys database
 * entry_key_enc_out:   receives 64 bytes of encrypted key
 */
static int encrypt_entry_key(const uint8_t entry_key_plain[64],
                              const uint8_t secure_file_id[16],
                              uint8_t entry_key_enc_out[64]) {
    uint8_t iv[16];
    aes_context aes;

    build_iv_hash_key(secure_file_id, iv);

    aes_setkey_enc(&aes, k_syscon_mgr_key, 128);
    if (aes_crypt_cbc(&aes, AES_ENCRYPT, 64, iv,
                       entry_key_plain, entry_key_enc_out) != 0) {
        debug_log("encrypt_entry_key: AES-CBC encrypt failed");
        return -1;
    }

    return 0;
}

/* ---- Encrypt file data ---- */

/*
 * Encrypt file data using the custom counter mode (inverse of decrypt):
 *   For each 16-byte block i:
 *     counter_key = i (u64 BE) + 8 zero bytes
 *     enc_counter = AES-ECB-encrypt(entry_key[0:16], counter_key)
 *     temp        = plaintext XOR enc_counter
 *     ciphertext  = AES-ECB-encrypt(entry_key[0:16], temp)
 *
 * data is modified in-place.
 * data_len must be aligned to 16 bytes.
 */
static int encrypt_file_data(uint8_t *data, size_t data_len,
                              const uint8_t entry_key[64]) {
    aes_context aes_enc;
    size_t num_blocks = data_len / 16;

    /* Use first 16 bytes of entry_key as the AES key */
    aes_setkey_enc(&aes_enc, entry_key, 128);

    for (size_t i = 0; i < num_blocks; i++) {
        uint8_t counter_key[16];
        uint8_t enc_counter[16];
        uint8_t temp[16];
        uint8_t *block = data + i * 16;

        /* Build counter: block index as big-endian u64 + 8 zero bytes */
        d_write_be64(counter_key, (uint64_t)i);
        memset(counter_key + 8, 0, 8);

        /* Encrypt counter with entry key */
        aes_crypt_ecb(&aes_enc, AES_ENCRYPT, counter_key, enc_counter);

        /* XOR plaintext with encrypted counter */
        for (int b = 0; b < 16; b++)
            temp[b] = block[b] ^ enc_counter[b];

        /* AES-ECB encrypt the result */
        aes_crypt_ecb(&aes_enc, AES_ENCRYPT, temp, block);

        /* Pump every 256 blocks (~4KB) to keep the kernel happy */
        if ((i & 0xFF) == 0) pump_callbacks();
    }

    return 0;
}

/* ---- File I/O helpers ---- */

/*
 * read_full_file — Read entire file into buffer with debug logging.
 */
static int read_full_file(const char *path, uint8_t *buf,
                           size_t max_size, size_t *actual) {
    FILE *fp = fopen(path, "rb");
    if (!fp) {
        debug_log("read_full_file: fopen FAILED for '%s'", path);
        return -1;
    }

    fseek(fp, 0, SEEK_END);
    long sz = ftell(fp);
    fseek(fp, 0, SEEK_SET);

    if (sz < 0 || (size_t)sz > max_size) {
        debug_log("read_full_file: size out of range (sz=%ld, max=%u)",
                  sz, (unsigned)max_size);
        fclose(fp);
        return -1;
    }

    size_t rd = fread(buf, 1, (size_t)sz, fp);
    fclose(fp);

    if (rd != (size_t)sz) {
        debug_log("read_full_file: short read %u/%ld for '%s'",
                  (unsigned)rd, sz, path);
        return -1;
    }

    debug_log("read_full_file: OK %u bytes from '%s'", (unsigned)rd, path);

    *actual = rd;
    return 0;
}

static int write_full_file(const char *path, const uint8_t *buf, size_t size) {
    FILE *fp = fopen(path, "wb");
    if (!fp) return -1;
    size_t wr = fwrite(buf, 1, size, fp);
    fclose(fp);
    return (wr == size) ? 0 : -1;
}

static int copy_file(const char *src, const char *dst) {
    FILE *in = fopen(src, "rb");
    if (!in) return -1;

    FILE *out = fopen(dst, "wb");
    if (!out) { fclose(in); return -1; }

    uint8_t buf[4096];
    size_t n;
    while ((n = fread(buf, 1, sizeof(buf), in)) > 0) {
        if (fwrite(buf, 1, n, out) != n) {
            fclose(in);
            fclose(out);
            return -1;
        }
        pump_callbacks();
    }

    fclose(in);
    fclose(out);
    return 0;
}

/* Create directory and all parents */
static void mkdirp(const char *path) {
    char tmp[PATH_LEN];
    strncpy(tmp, path, sizeof(tmp) - 1);
    tmp[sizeof(tmp) - 1] = '\0';
    size_t len = strlen(tmp);
    for (size_t i = 1; i < len; i++) {
        if (tmp[i] == '/') {
            tmp[i] = '\0';
            mkdir(tmp, 0755);
            tmp[i] = '/';
        }
    }
    mkdir(tmp, 0755);
}

/* ---- PFD entry table parsing for decryption ---- */

typedef struct {
    char filename[DENT_MAX_NAME];
    uint8_t entry_key[64];       /* encrypted 64-byte key from PFD */
    uint64_t file_size;          /* actual file size (not padded) */
} decrypt_entry_t;

#define DECRYPT_MAX_ENTRIES 64

typedef struct {
    decrypt_entry_t entries[DECRYPT_MAX_ENTRIES];
    int count;
} decrypt_pfd_t;

/*
 * Parse PARAM.PFD to extract the entry table (filenames, keys, sizes).
 * We don't need the full resign logic — just read the entries.
 */
static int parse_pfd_entries(const char *save_dir, decrypt_pfd_t *out) {
    char pfd_path[PATH_LEN];
    uint8_t *pfd_data;
    size_t pfd_size;

    snprintf(pfd_path, sizeof(pfd_path), "%s/PARAM.PFD", save_dir);

    pfd_data = (uint8_t *)malloc(DPFD_MAX_FILE_SIZE);
    if (!pfd_data) return -1;

    if (read_full_file(pfd_path, pfd_data, DPFD_MAX_FILE_SIZE, &pfd_size) != 0) {
        debug_log("decrypt: failed to read %s", pfd_path);
        free(pfd_data);
        return -1;
    }

    /* Validate magic — magic is u64 BE, not u32 */
    if (pfd_size < DPFD_OFF_HASH_TABLE + 24) {
        debug_log("decrypt: PFD too small (%u bytes)", (unsigned)pfd_size);
        free(pfd_data);
        return -1;
    }

    uint64_t magic = d_read_be64(pfd_data + DPFD_OFF_MAGIC);
    if (magic != DPFD_MAGIC) {
        debug_log("decrypt: bad PFD magic 0x%016llx (expected 0x%016llx)",
                  (unsigned long long)magic, (unsigned long long)DPFD_MAGIC);
        free(pfd_data);
        return -1;
    }

    uint64_t version = d_read_be64(pfd_data + DPFD_OFF_VERSION);
    debug_log("decrypt: PFD magic OK, version=%llu, size=%u",
              (unsigned long long)version, (unsigned)pfd_size);

    /* Parse hash table header */
    size_t off = DPFD_OFF_HASH_TABLE;
    uint64_t capacity     = d_read_be64(pfd_data + off); off += 8;
    uint64_t num_reserved = d_read_be64(pfd_data + off); off += 8;
    uint64_t num_used     = d_read_be64(pfd_data + off); off += 8;

    /* Entry table starts after hash table indices */
    size_t et_off = off + capacity * 8;

    if (num_used > DECRYPT_MAX_ENTRIES) num_used = DECRYPT_MAX_ENTRIES;

    out->count = 0;
    for (uint64_t i = 0; i < num_used; i++) {
        size_t entry_off = et_off + i * DENT_SIZE;
        if (entry_off + DENT_SIZE > pfd_size) break;

        uint8_t *ep = pfd_data + entry_off;
        decrypt_entry_t *de = &out->entries[out->count];

        /* Filename */
        memcpy(de->filename, ep + DENT_OFF_FILENAME, DENT_MAX_NAME - 1);
        de->filename[DENT_MAX_NAME - 1] = '\0';

        /* Skip empty entries */
        if (de->filename[0] == '\0') continue;

        /* Entry key (64 bytes, still encrypted) */
        memcpy(de->entry_key, ep + DENT_OFF_KEY, 64);

        /* File size */
        de->file_size = d_read_be64(ep + DENT_OFF_FILE_SIZE);

        out->count++;
    }

    debug_log("decrypt: parsed %d PFD entries from %s", out->count, pfd_path);

    (void)num_reserved;
    free(pfd_data);
    return 0;
}

/* Check if a filename is in the PFD entry list */
static decrypt_entry_t *find_pfd_entry(decrypt_pfd_t *pfd, const char *filename) {
    for (int i = 0; i < pfd->count; i++) {
        if (strcmp(pfd->entries[i].filename, filename) == 0)
            return &pfd->entries[i];
    }
    return NULL;
}

/* ---- Public API ---- */

int decrypt_save(const TitleInfo *title,
                 char *out_path, size_t out_path_size) {
    if (!title || !out_path || out_path_size == 0) return -1;

    debug_log("decrypt_save: START title_id=%s game_code=%s local_path=%s",
              title->title_id, title->game_code, title->local_path);

    if (!gamekeys_is_loaded()) {
        debug_log("decrypt_save: gamekeys not loaded");
        return -2;
    }

    /* Check if PARAM.PFD exists — if not, files aren't encrypted */
    char pfd_check[PATH_LEN];
    snprintf(pfd_check, sizeof(pfd_check), "%s/PARAM.PFD", title->local_path);
    {
        struct stat st;
        if (stat(pfd_check, &st) != 0) {
            debug_log("decrypt_save: no PARAM.PFD at %s (stat failed), assuming unencrypted",
                      pfd_check);
            /* No PFD means no encryption — point output to local_path directly.
             * But we still need to copy files excluding PARAM.PFD/SFO to temp. */
        } else {
            debug_log("decrypt_save: PARAM.PFD found at %s, size=%u",
                      pfd_check, (unsigned)st.st_size);
        }
    }

    /* Look up secure_file_id for this game.
     * Use "*" as filename to get the default key — we'll do per-file
     * lookups later for each individual file. */
    uint8_t default_sfid[16];
    bool has_default_key = gamekeys_get_secure_file_id(
        title->game_code, "*", default_sfid);

    if (has_default_key) {
        debug_log("decrypt_save: found secure_file_id for %s", title->game_code);
    } else {
        /* Try without wildcard — some entries use specific filenames only.
         * We'll proceed and try per-file lookup below.
         * If no keys exist at all for this game, we still copy files unencrypted. */
        debug_log("decrypt_save: no default secure_file_id for %s, will try per-file",
                  title->game_code);
    }

    /* Parse PARAM.PFD entries */
    decrypt_pfd_t *pfd = (decrypt_pfd_t *)malloc(sizeof(decrypt_pfd_t));
    if (!pfd) return -1;
    memset(pfd, 0, sizeof(*pfd));

    bool has_pfd = (parse_pfd_entries(title->local_path, pfd) == 0 && pfd->count > 0);
    debug_log("decrypt_save: has_pfd=%d, pfd_count=%d", (int)has_pfd, pfd->count);

    if (has_pfd) {
        debug_log("decrypt_save: PFD has %d entries", pfd->count);
    }

    /* Create temp directory */
    snprintf(out_path, out_path_size, "%s/%s", DECRYPT_TEMP_ROOT, title->title_id);
    mkdirp(out_path);
    debug_log("decrypt_save: temp dir = %s", out_path);

    /* Process each file in the save directory */
    DIR *dir = opendir(title->local_path);
    if (!dir) {
        debug_log("decrypt_save: cannot open %s", title->local_path);
        free(pfd);
        return -1;
    }

    int files_processed = 0;
    int files_decrypted = 0;
    int errors = 0;

    struct dirent *ent;
    while ((ent = readdir(dir)) != NULL) {
        if (ent->d_name[0] == '.') continue;

        /* Skip PFD metadata files — not needed by emulators */
        if (strcmp(ent->d_name, "PARAM.PFD") == 0) continue;
        if (strcmp(ent->d_name, "PARAM.SFO") == 0) {
            /* Copy PARAM.SFO as-is — emulators need it for game info */
            char src[PATH_LEN], dst[PATH_LEN];
            snprintf(src, sizeof(src), "%s/%s", title->local_path, ent->d_name);
            snprintf(dst, sizeof(dst), "%s/%s", out_path, ent->d_name);
            if (copy_file(src, dst) != 0) {
                debug_log("decrypt_save: failed to copy %s", ent->d_name);
                errors++;
            } else {
                files_processed++;
            }
            continue;
        }

        char src_path[PATH_LEN];
        char dst_path[PATH_LEN];
        snprintf(src_path, sizeof(src_path), "%s/%s", title->local_path, ent->d_name);
        snprintf(dst_path, sizeof(dst_path), "%s/%s", out_path, ent->d_name);

        /* Check if it's a regular file */
        struct stat st;
        if (stat(src_path, &st) != 0 || !S_ISREG(st.st_mode)) continue;

        /* Is this file listed in the PFD? */
        decrypt_entry_t *pfd_entry = has_pfd ? find_pfd_entry(pfd, ent->d_name) : NULL;

        if (pfd_entry) {
            /* File is in PFD — needs decryption */
            ui_status("Decrypting: %s", ent->d_name);
            debug_log("decrypt_save: file '%s' found in PFD, file_size=%llu, disk_size=%u",
                      ent->d_name, (unsigned long long)pfd_entry->file_size,
                      (unsigned)st.st_size);

            /* Look up secure_file_id for this specific file */
            uint8_t sfid[16];
            bool has_sfid = gamekeys_get_secure_file_id(
                title->game_code, ent->d_name, sfid);

            if (!has_sfid) {
                /* No key for this file — copy as-is with a warning */
                debug_log("decrypt_save: no secure_file_id for %s:%s, copying as-is",
                          title->game_code, ent->d_name);
                if (copy_file(src_path, dst_path) != 0) errors++;
                else files_processed++;
                continue;
            }

            /* Decrypt entry key */
            uint8_t decrypted_entry_key[64];
            if (decrypt_entry_key(pfd_entry->entry_key, sfid,
                                   decrypted_entry_key) != 0) {
                debug_log("decrypt_save: entry key decrypt failed for %s", ent->d_name);
                errors++;
                continue;
            }

            /* Read the encrypted file data */
            size_t padded_size = ((size_t)pfd_entry->file_size + 15) & ~(size_t)15;
            if (padded_size == 0) padded_size = 16; /* minimum one block */

            /* File on disk may be larger than padded_size due to OS alignment */
            size_t read_size = (size_t)st.st_size;
            if (read_size < padded_size) {
                debug_log("decrypt_save: file %s smaller than expected (%u < %u)",
                          ent->d_name, (unsigned)read_size, (unsigned)padded_size);
                /* Use actual file size, rounded up to 16 */
                padded_size = (read_size + 15) & ~(size_t)15;
            }

            uint8_t *file_data = (uint8_t *)malloc(padded_size);
            if (!file_data) { errors++; continue; }
            memset(file_data, 0, padded_size);

            FILE *fp = fopen(src_path, "rb");
            if (!fp) { free(file_data); errors++; continue; }
            size_t rd = fread(file_data, 1, read_size, fp);
            fclose(fp);
            if (rd < pfd_entry->file_size) {
                debug_log("decrypt_save: short read on %s (%u/%u)",
                          ent->d_name, (unsigned)rd, (unsigned)pfd_entry->file_size);
            }

            /* Decrypt the file data */
            if (decrypt_file_data(file_data, padded_size,
                                   decrypted_entry_key,
                                   pfd_entry->file_size) != 0) {
                debug_log("decrypt_save: data decrypt failed for %s", ent->d_name);
                free(file_data);
                errors++;
                continue;
            }

            /* Write decrypted data (actual size, not padded) */
            if (write_full_file(dst_path, file_data,
                                 (size_t)pfd_entry->file_size) != 0) {
                debug_log("decrypt_save: write failed for %s -> %s", ent->d_name, dst_path);
                free(file_data);
                errors++;
                continue;
            }

            debug_log("decrypt_save: wrote %llu bytes to %s",
                      (unsigned long long)pfd_entry->file_size, dst_path);

            free(file_data);
            files_decrypted++;
            files_processed++;

        } else {
            /* File not in PFD — copy as-is (unencrypted) */
            debug_log("decrypt_save: file '%s' NOT in PFD, copying as-is", ent->d_name);
            if (copy_file(src_path, dst_path) != 0) {
                debug_log("decrypt_save: copy failed for %s", ent->d_name);
                errors++;
            } else {
                files_processed++;
            }
        }

        pump_callbacks();
    }

    closedir(dir);
    free(pfd);

    debug_log("decrypt_save: processed=%d decrypted=%d errors=%d for %s",
              files_processed, files_decrypted, errors, title->title_id);

    if (files_processed == 0 && errors > 0) {
        debug_log("decrypt_save: no files processed, returning error");
        return -1;
    }

    return 0;
}

/* ---- Encrypt save (for downloads: plaintext -> encrypted on PS3 HDD) ---- */

int encrypt_save(const TitleInfo *title, encrypt_keys_t *out_keys) {
    if (!title || !out_keys) return -1;
    memset(out_keys, 0, sizeof(*out_keys));

    debug_log("encrypt_save: START title_id=%s game_code=%s local_path=%s",
              title->title_id, title->game_code, title->local_path);

    if (!gamekeys_is_loaded()) {
        debug_log("encrypt_save: gamekeys not loaded");
        return -2;
    }

    /* Look up default secure_file_id for this game */
    uint8_t default_sfid[16];
    bool has_default_key = gamekeys_get_secure_file_id(
        title->game_code, "*", default_sfid);

    if (!has_default_key) {
        debug_log("encrypt_save: no secure_file_id for %s, skipping encryption",
                  title->game_code);
        return -3;
    }

    debug_log("encrypt_save: found secure_file_id for %s", title->game_code);

    /* Iterate over files in the save directory */
    DIR *dir = opendir(title->local_path);
    if (!dir) {
        debug_log("encrypt_save: cannot open %s", title->local_path);
        return -1;
    }

    int files_encrypted = 0;
    int errors = 0;

    struct dirent *ent;
    while ((ent = readdir(dir)) != NULL) {
        if (ent->d_name[0] == '.') continue;

        /* Skip metadata files — they are not encrypted */
        if (strcmp(ent->d_name, "PARAM.PFD") == 0) continue;
        if (strcmp(ent->d_name, "PARAM.SFO") == 0) continue;
        /* ICON0.PNG and PIC1.PNG are also not encrypted on PS3 */
        if (strcmp(ent->d_name, "ICON0.PNG") == 0) continue;
        if (strcmp(ent->d_name, "PIC1.PNG") == 0) continue;
        if (strcmp(ent->d_name, "ICON1.PAM") == 0) continue;
        if (strcmp(ent->d_name, "PIC1.PAM") == 0) continue;
        if (strcmp(ent->d_name, "SND0.AT3") == 0) continue;

        char fpath[PATH_LEN];
        snprintf(fpath, sizeof(fpath), "%s/%s", title->local_path, ent->d_name);

        struct stat st;
        if (stat(fpath, &st) != 0 || !S_ISREG(st.st_mode)) continue;

        if (out_keys->count >= ENCRYPT_MAX_FILES) {
            debug_log("encrypt_save: too many files (%d), skipping %s",
                      out_keys->count, ent->d_name);
            continue;
        }

        ui_status("Encrypting: %s", ent->d_name);
        debug_log("encrypt_save: encrypting %s (%u bytes)", ent->d_name, (unsigned)st.st_size);

        /* Look up per-file secure_file_id, fall back to default */
        uint8_t sfid[16];
        if (!gamekeys_get_secure_file_id(title->game_code, ent->d_name, sfid)) {
            memcpy(sfid, default_sfid, 16);
        }

        /* Generate a random 64-byte entry key.
         * The first 16 bytes are used as the AES key for file data encryption.
         * We use a simple PRNG seeded from the filename and file size;
         * the actual randomness doesn't matter for security — the PS3 save
         * system's security comes from the keys being encrypted in PARAM.PFD. */
        uint8_t entry_key[64];
        {
            /* Seed from filename hash + file size + a counter */
            uint32_t seed = 0x3D55AACC;
            for (const char *p = ent->d_name; *p; p++)
                seed = seed * 31 + (uint8_t)*p;
            seed ^= (uint32_t)st.st_size;
            seed ^= (uint32_t)files_encrypted * 0x9E3779B9;
            for (int k = 0; k < 64; k++) {
                seed = seed * 1103515245 + 12345;
                entry_key[k] = (uint8_t)(seed >> 16);
            }
        }

        /* Read the plaintext file */
        size_t actual_size = (size_t)st.st_size;
        size_t padded_size = (actual_size + 15) & ~(size_t)15;
        if (padded_size == 0) padded_size = 16;

        uint8_t *file_data = (uint8_t *)malloc(padded_size);
        if (!file_data) { errors++; continue; }
        memset(file_data, 0, padded_size);

        FILE *fp = fopen(fpath, "rb");
        if (!fp) { free(file_data); errors++; continue; }
        size_t rd = fread(file_data, 1, actual_size, fp);
        fclose(fp);
        if (rd != actual_size) {
            debug_log("encrypt_save: short read %u/%u on %s",
                      (unsigned)rd, (unsigned)actual_size, ent->d_name);
            free(file_data);
            errors++;
            continue;
        }

        /* Encrypt file data in-place */
        if (encrypt_file_data(file_data, padded_size, entry_key) != 0) {
            debug_log("encrypt_save: encrypt_file_data failed for %s", ent->d_name);
            free(file_data);
            errors++;
            continue;
        }

        /* Write encrypted data back (write the padded size — PS3 expects it) */
        fp = fopen(fpath, "wb");
        if (!fp) { free(file_data); errors++; continue; }
        size_t wr = fwrite(file_data, 1, padded_size, fp);
        fclose(fp);
        free(file_data);

        if (wr != padded_size) {
            debug_log("encrypt_save: write failed for %s", ent->d_name);
            errors++;
            continue;
        }

        /* Encrypt the entry key for storage in PARAM.PFD */
        encrypt_key_entry_t *ek = &out_keys->entries[out_keys->count];
        strncpy(ek->filename, ent->d_name, sizeof(ek->filename) - 1);
        ek->filename[sizeof(ek->filename) - 1] = '\0';
        ek->original_size = (uint64_t)actual_size;

        if (encrypt_entry_key(entry_key, sfid, ek->encrypted_key) != 0) {
            debug_log("encrypt_save: encrypt_entry_key failed for %s", ent->d_name);
            errors++;
            continue;
        }

        out_keys->count++;
        files_encrypted++;
        debug_log("encrypt_save: encrypted %s successfully", ent->d_name);
        pump_callbacks();
    }

    closedir(dir);

    debug_log("encrypt_save: encrypted=%d errors=%d for %s",
              files_encrypted, errors, title->title_id);

    if (files_encrypted == 0 && errors > 0) return -1;
    return 0;
}

int reencrypt_files_from_pfd(const TitleInfo *title) {
    if (!title) return -1;

    debug_log("reencrypt_files_from_pfd: START title_id=%s local_path=%s",
              title->title_id, title->local_path);

    if (!gamekeys_is_loaded()) {
        debug_log("reencrypt_files_from_pfd: gamekeys not loaded");
        return -2;
    }

    /* Parse the existing PARAM.PFD to get the (still-encrypted) entry keys */
    decrypt_pfd_t *pfd = (decrypt_pfd_t *)malloc(sizeof(decrypt_pfd_t));
    if (!pfd) return -1;
    memset(pfd, 0, sizeof(*pfd));

    if (parse_pfd_entries(title->local_path, pfd) != 0 || pfd->count == 0) {
        debug_log("reencrypt_files_from_pfd: failed to parse PARAM.PFD or no entries");
        free(pfd);
        return -1;
    }
    debug_log("reencrypt_files_from_pfd: PFD has %d entries", pfd->count);

    int files_reencrypted = 0;
    int errors = 0;

    for (int i = 0; i < pfd->count; i++) {
        decrypt_entry_t *de = &pfd->entries[i];

        /* Skip metadata — they are not data files */
        if (strcmp(de->filename, "PARAM.SFO") == 0) continue;
        if (strcmp(de->filename, "PARAM.PFD") == 0) continue;
        if (strcmp(de->filename, "ICON0.PNG") == 0) continue;
        if (strcmp(de->filename, "PIC1.PNG")  == 0) continue;
        if (strcmp(de->filename, "PIC0.PNG")  == 0) continue;
        if (strcmp(de->filename, "SND0.AT3")  == 0) continue;

        char fpath[PATH_LEN];
        snprintf(fpath, sizeof(fpath), "%s/%s", title->local_path, de->filename);

        struct stat st;
        if (stat(fpath, &st) != 0 || !S_ISREG(st.st_mode)) {
            debug_log("reencrypt_files_from_pfd: file %s not on disk, skipping",
                      de->filename);
            continue;
        }

        /* Get secure_file_id: try per-file first, fall back to wildcard */
        uint8_t sfid[16];
        if (!gamekeys_get_secure_file_id(title->game_code, de->filename, sfid) &&
            !gamekeys_get_secure_file_id(title->game_code, "*", sfid)) {
            debug_log("reencrypt_files_from_pfd: no secure_file_id for %s/%s",
                      title->game_code, de->filename);
            errors++;
            continue;
        }

        /* Decrypt the entry key stored in PARAM.PFD */
        uint8_t entry_key[64];
        if (decrypt_entry_key(de->entry_key, sfid, entry_key) != 0) {
            debug_log("reencrypt_files_from_pfd: decrypt_entry_key failed for %s",
                      de->filename);
            errors++;
            continue;
        }

        /* Read the plaintext file (from bundle extraction) */
        size_t actual_size = (size_t)st.st_size;
        size_t padded_size = (actual_size + 15) & ~(size_t)15;
        if (padded_size == 0) padded_size = 16;

        uint8_t *file_data = (uint8_t *)malloc(padded_size);
        if (!file_data) { errors++; continue; }
        memset(file_data, 0, padded_size);

        FILE *fp = fopen(fpath, "rb");
        if (!fp) { free(file_data); errors++; continue; }
        size_t rd = fread(file_data, 1, actual_size, fp);
        fclose(fp);

        if (rd != actual_size) {
            debug_log("reencrypt_files_from_pfd: short read %u/%u on %s",
                      (unsigned)rd, (unsigned)actual_size, de->filename);
            free(file_data);
            errors++;
            continue;
        }

        /* Encrypt in-place using the existing key */
        ui_status("Re-encrypting: %s", de->filename);
        if (encrypt_file_data(file_data, padded_size, entry_key) != 0) {
            debug_log("reencrypt_files_from_pfd: encrypt failed for %s", de->filename);
            free(file_data);
            errors++;
            continue;
        }

        /* Write encrypted data back (padded to 16 bytes, as PS3 expects) */
        fp = fopen(fpath, "wb");
        if (!fp) { free(file_data); errors++; continue; }
        size_t wr = fwrite(file_data, 1, padded_size, fp);
        fclose(fp);
        free(file_data);

        if (wr != padded_size) {
            debug_log("reencrypt_files_from_pfd: write failed for %s", de->filename);
            errors++;
            continue;
        }

        debug_log("reencrypt_files_from_pfd: re-encrypted %s (%u bytes) with existing key",
                  de->filename, (unsigned)actual_size);
        files_reencrypted++;
        pump_callbacks();
    }

    free(pfd);

    debug_log("reencrypt_files_from_pfd: reencrypted=%d errors=%d for %s",
              files_reencrypted, errors, title->title_id);

    if (files_reencrypted == 0) return -1;
    return 0;
}

void decrypt_cleanup(const char *temp_path) {
    if (!temp_path || temp_path[0] == '\0') return;

    /* Remove all files in the temp directory */
    DIR *dir = opendir(temp_path);
    if (!dir) return;

    struct dirent *ent;
    while ((ent = readdir(dir)) != NULL) {
        if (ent->d_name[0] == '.') continue;
        char fpath[PATH_LEN];
        snprintf(fpath, sizeof(fpath), "%s/%s", temp_path, ent->d_name);

        struct stat st;
        if (stat(fpath, &st) == 0) {
            if (S_ISREG(st.st_mode)) {
                remove(fpath);
            } else if (S_ISDIR(st.st_mode)) {
                /* Recurse into subdirectories */
                decrypt_cleanup(fpath);
            }
        }
    }

    closedir(dir);
    rmdir(temp_path);

    debug_log("decrypt_cleanup: removed %s", temp_path);
}
