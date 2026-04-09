/*
 * PS3 Save Sync - PARAM.SFO patching
 *
 * Reads a PS3 PARAM.SFO file, patches ownership fields (ACCOUNT_ID,
 * PARAMS embedded structure with user_id/PSID/account_id), fixes
 * format types for PARAMS/PARAMS2, sets owner flags, and optionally
 * removes copy protection. Writes the result back in-place.
 *
 * SFO file format:
 *   Header (20 bytes):
 *     [0..3]   magic = 0x00505346 ("\0PSF")
 *     [4..7]   version = 0x00000101 (1.1)
 *     [8..11]  key_table_offset (u32 LE)
 *     [12..15] data_table_offset (u32 LE)
 *     [16..19] num_entries (u32 LE)
 *
 *   Index table (num_entries * 16 bytes each):
 *     [0..1]   key_offset (u16 LE, relative to key_table_offset)
 *     [2..3]   param_format (u16 LE: 0x0004=utf-8, 0x0204=utf-8-special, 0x0404=u32)
 *     [4..7]   param_length (u32 LE, actual data size)
 *     [8..11]  param_max_length (u32 LE, max data size)
 *     [12..15] data_offset (u32 LE, relative to data_table_offset)
 *
 *   Key table: NUL-terminated ASCII strings
 *   Data table: parameter values (strings, u32s, or binary blobs)
 *
 * All multi-byte integers in SFO are little-endian.
 */

#include "sfo.h"
#include "common.h"
#include "debug.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ---- SFO constants ---- */

#define SFO_MAGIC           0x46535000U  /* "\0PSF" — bytes 00 50 53 46 read as LE u32 */
#define SFO_VERSION         0x00000101U  /* v1.1 */
#define SFO_HEADER_SIZE     20
#define SFO_INDEX_ENTRY_SIZE 16
#define SFO_MAX_FILE_SIZE   65536  /* generous upper bound */
#define SFO_SAVEDATA_DIRECTORY_MAX 64

/* Parameter formats */
#define SFO_FMT_UTF8        0x0004
#define SFO_FMT_UTF8_S      0x0204  /* utf-8 special (used by RPCS3 for binary blobs) */
#define SFO_FMT_U32         0x0404

/* ATTRIBUTE flag for copy protection */
#define SFO_ATTR_COPY_PROTECTED  0x0000000CU

/* PARAMS owner flags (from psdevwiki / real PS3 save analysis):
 *   byte 0:  always 0x01
 *   offset 20 (unk4, u32 LE): always 0x01 */
#define SFO_PARAMS_BYTE0_DEFAULT     0x01
#define SFO_PARAMS_UNK4_OFFSET      20
#define SFO_PARAMS_UNK4_DEFAULT     1
#define SFO_PARAMS_FLAGS_DEFAULT     0x03
#define SFO_PARAMS_BYTE2_DEFAULT     0x01
#define SFO_PARAMS_COUNTER_SLOT_DEFAULT 0x03
#define SFO_PARAMS_COUNTER3_OFFSET   16
#define SFO_PARAMS_COUNTER3_DEFAULT  3

/* ---- LE read/write helpers ---- */

static uint16_t rd16(const uint8_t *p) {
    return (uint16_t)(p[0] | (p[1] << 8));
}

static uint32_t rd32(const uint8_t *p) {
    return (uint32_t)(p[0] | (p[1] << 8) | (p[2] << 16) | (p[3] << 24));
}

static void wr16(uint8_t *p, uint16_t v) {
    p[0] = (uint8_t)(v);
    p[1] = (uint8_t)(v >> 8);
}

static void wr32(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)(v);
    p[1] = (uint8_t)(v >> 8);
    p[2] = (uint8_t)(v >> 16);
    p[3] = (uint8_t)(v >> 24);
}

static uint32_t align_up_4(uint32_t v) {
    return (v + 3U) & ~3U;
}

static bool is_rpcs3_sfo_entry(const char *key_name) {
    if (!key_name) return false;
    if (key_name[0] == '*') return true;
    if (strcmp(key_name, "RPCS3_BLIST") == 0) return true;
    return false;
}

static uint32_t normalized_param_max(const char *key_name, uint32_t param_max) {
    if (key_name && strcmp(key_name, "SAVEDATA_DIRECTORY") == 0 &&
        param_max < SFO_SAVEDATA_DIRECTORY_MAX) {
        return SFO_SAVEDATA_DIRECTORY_MAX;
    }
    return param_max;
}

/* ---- SFO patching ---- */

int sfo_patch(const char *sfo_path, const sfo_patch_t *patch) {
    uint8_t *buf = NULL;
    int result = -1;

    debug_log("sfo_patch: opening %s", sfo_path);

    /* Read entire SFO file */
    FILE *fp = fopen(sfo_path, "rb");
    if (!fp) {
        debug_log("sfo_patch: failed to open %s", sfo_path);
        return -1;
    }

    fseek(fp, 0, SEEK_END);
    long fsize = ftell(fp);
    fseek(fp, 0, SEEK_SET);

    if (fsize < SFO_HEADER_SIZE || fsize > SFO_MAX_FILE_SIZE) {
        debug_log("sfo_patch: bad file size %ld", fsize);
        fclose(fp);
        return -1;
    }

    buf = (uint8_t *)malloc((size_t)fsize);
    if (!buf) {
        fclose(fp);
        return -1;
    }

    if (fread(buf, 1, (size_t)fsize, fp) != (size_t)fsize) {
        debug_log("sfo_patch: read error");
        goto done;
    }
    fclose(fp);
    fp = NULL;

    /* Validate header */
    uint32_t magic = rd32(buf);
    if (magic != SFO_MAGIC) {
        debug_log("sfo_patch: bad magic 0x%08x", magic);
        goto done;
    }

    uint32_t key_table_off  = rd32(buf + 8);
    uint32_t data_table_off = rd32(buf + 12);
    uint32_t num_entries    = rd32(buf + 16);

    debug_log("sfo_patch: entries=%u key_off=%u data_off=%u",
              num_entries, key_table_off, data_table_off);

    /* ---- First pass: read real ACCOUNT_ID for use in PARAMS ----
     * The top-level ACCOUNT_ID field has the real PSN account_id
     * (e.g. "6fb5b578ce81a7f9").  We copy this into the PARAMS
     * embedded structure at +48, because the caller may not have it. */
    char real_account_id[SFO_ACCOUNT_ID_SIZE + 1];
    memset(real_account_id, 0, sizeof(real_account_id));

    for (uint32_t i = 0; i < num_entries; i++) {
        uint8_t *idx = buf + SFO_HEADER_SIZE + i * SFO_INDEX_ENTRY_SIZE;
        uint16_t key_off   = rd16(idx);
        uint16_t param_fmt = rd16(idx + 2);
        uint32_t param_len = rd32(idx + 4);
        uint32_t data_off  = rd32(idx + 12);

        const char *key_name = (const char *)(buf + key_table_off + key_off);
        uint8_t *data_ptr    = buf + data_table_off + data_off;

        if (strcmp(key_name, "ACCOUNT_ID") == 0 &&
            (param_fmt == SFO_FMT_UTF8 || param_fmt == SFO_FMT_UTF8_S) &&
            param_len >= SFO_ACCOUNT_ID_SIZE) {
            memcpy(real_account_id, data_ptr, SFO_ACCOUNT_ID_SIZE);
            real_account_id[SFO_ACCOUNT_ID_SIZE] = '\0';
            debug_log("sfo_patch: read existing ACCOUNT_ID = %s", real_account_id);
        }
    }

    /* Decide which account_id to use for PARAMS +48:
     * If caller provided a real (non-all-zeros) account_id, use it.
     * Otherwise use the one we read from the SFO's ACCOUNT_ID field. */
    const char *params_account_id = real_account_id;
    if (patch->account_id[0]) {
        int all_zero = 1;
        for (int j = 0; j < SFO_ACCOUNT_ID_SIZE; j++) {
            if (patch->account_id[j] != '0') { all_zero = 0; break; }
        }
        if (!all_zero) {
            params_account_id = patch->account_id;
        }
    }
    debug_log("sfo_patch: using account_id for PARAMS = %.16s", params_account_id);

    /* ---- Second pass: patch all fields ---- */
    for (uint32_t i = 0; i < num_entries; i++) {
        uint8_t *idx = buf + SFO_HEADER_SIZE + i * SFO_INDEX_ENTRY_SIZE;
        uint16_t key_off     = rd16(idx);
        uint16_t param_fmt   = rd16(idx + 2);
        uint32_t param_len   = rd32(idx + 4);
        uint32_t param_max   = rd32(idx + 8);
        uint32_t data_off    = rd32(idx + 12);

        const char *key_name = (const char *)(buf + key_table_off + key_off);
        uint8_t *data_ptr    = buf + data_table_off + data_off;

        pump_callbacks();

        /* Patch ACCOUNT_ID (16-char hex string).
         * On real PS3: fmt=0x0004, param_max=16 (no NUL terminator).
         * Only overwrite if caller has a real (non-all-zeros) account_id. */
        if (strcmp(key_name, "ACCOUNT_ID") == 0 &&
            (param_fmt == SFO_FMT_UTF8 || param_fmt == SFO_FMT_UTF8_S)) {
            if (patch->account_id[0] && param_max >= SFO_ACCOUNT_ID_SIZE) {
                int all_zero = 1;
                for (int j = 0; j < SFO_ACCOUNT_ID_SIZE; j++) {
                    if (patch->account_id[j] != '0') { all_zero = 0; break; }
                }
                if (!all_zero) {
                    memset(data_ptr, 0, param_max);
                    memcpy(data_ptr, patch->account_id, SFO_ACCOUNT_ID_SIZE);
                    wr32(idx + 4, SFO_ACCOUNT_ID_SIZE);
                    debug_log("sfo_patch: patched ACCOUNT_ID = %s", patch->account_id);
                } else {
                    debug_log("sfo_patch: keeping existing ACCOUNT_ID (caller all-zeros)");
                }
            }
        }

        /* Patch PARAMS (binary blob containing user_id, PSID, account_id).
         *
         * PARAMS layout (from Apollo sfo_param_params_t / psdevwiki):
         *   +0   byte: always 0x01 (owner flag)
         *   +1   byte: savedata feature flags
         *   +2   byte: unknown
         *   +3   byte: cumulated counter slot number
         *   +4   byte: SFO updates counter slot number
         *   +5..7  reserved (zeros)
         *   +8..11  counter slot 1 (u32, always 0)
         *   +12..15 counter slot 2 (u32)
         *   +16..19 counter slot 3 (u32)
         *   +20..23 counter slot 4 (u32, always 1)
         *   +24  user_id_1 (u32 LE)
         *   +28  psid[16]
         *   +44  user_id_2 (u32 LE, duplicate)
         *   +48  account_id[16] (hex string)
         *
         * RPCS3 uses fmt=0x0204 but real PS3 uses fmt=0x0004.
         * We must fix the format type AND populate the owner fields. */
        if (strcmp(key_name, "PARAMS") == 0 &&
            (param_fmt == SFO_FMT_UTF8_S || param_fmt == SFO_FMT_UTF8)) {
            if (param_max >= SFO_PARAMS_MIN_SIZE) {
                bool params_was_truncated = (param_len < SFO_PARAMS_MIN_SIZE);

                /* Fix format type: RPCS3 uses 0x0204, real PS3 uses 0x0004 */
                if (param_fmt != SFO_FMT_UTF8) {
                    wr16(idx + 2, SFO_FMT_UTF8);
                    debug_log("sfo_patch: fixed PARAMS format 0x%04x -> 0x0004",
                              param_fmt);
                }

                /* If the existing data is too small (e.g. RPCS3 empty PARAMS
                 * with param_len=1), zero-fill and expand to full size. */
                if (param_len < SFO_PARAMS_MIN_SIZE) {
                    memset(data_ptr, 0, param_max);
                    wr32(idx + 4, param_max);
                    debug_log("sfo_patch: expanded PARAMS from %u to %u bytes",
                              param_len, param_max);
                }

                /* Set required owner flags:
                 *   byte 0 = 0x01 (always required by PS3 firmware)
                 *   offset 20 (unk4) = 1 (always required)
                 * Only set if currently zero to avoid overwriting valid
                 * values from a real PS3 save being re-signed. */
                if (data_ptr[0] == 0x00) {
                    data_ptr[0] = SFO_PARAMS_BYTE0_DEFAULT;
                    debug_log("sfo_patch: set PARAMS byte[0] = 0x01");
                }
                if (rd32(data_ptr + SFO_PARAMS_UNK4_OFFSET) == 0) {
                    wr32(data_ptr + SFO_PARAMS_UNK4_OFFSET, SFO_PARAMS_UNK4_DEFAULT);
                    debug_log("sfo_patch: set PARAMS unk4 = 1");
                }

                /* RPCS3 often stores an effectively-empty PARAMS blob
                 * (len=1). When we expand that for a real PS3 save, seed the
                 * metadata counters with the native-style defaults observed
                 * in working saves instead of leaving them all zero. */
                if (params_was_truncated ||
                    (data_ptr[1] == 0x00 && data_ptr[2] == 0x00 &&
                     data_ptr[3] == 0x00 && data_ptr[4] == 0x00 &&
                     rd32(data_ptr + SFO_PARAMS_COUNTER3_OFFSET) == 0)) {
                    data_ptr[1] = SFO_PARAMS_FLAGS_DEFAULT;
                    data_ptr[2] = SFO_PARAMS_BYTE2_DEFAULT;
                    data_ptr[3] = SFO_PARAMS_COUNTER_SLOT_DEFAULT;
                    data_ptr[4] = SFO_PARAMS_COUNTER_SLOT_DEFAULT;
                    wr32(data_ptr + SFO_PARAMS_COUNTER3_OFFSET,
                         SFO_PARAMS_COUNTER3_DEFAULT);
                    debug_log("sfo_patch: initialized PARAMS counters/flags");
                }

                /* Patch user_id at offsets 24 and 44 (two copies, LE u32) */
                wr32(data_ptr + SFO_PARAMS_USER_ID_1_OFFSET, patch->user_id);
                wr32(data_ptr + SFO_PARAMS_USER_ID_2_OFFSET, patch->user_id);

                /* Patch PSID at offset 28 (16 bytes) */
                memcpy(data_ptr + SFO_PARAMS_PSID_OFFSET, patch->psid, 16);

                /* Patch account_id at offset 48 (16 bytes, hex string).
                 * Uses real account_id from ACCOUNT_ID field if caller's
                 * is all zeros. */
                if (params_account_id[0]) {
                    memset(data_ptr + SFO_PARAMS_ACCOUNT_ID_OFFSET, 0, 16);
                    memcpy(data_ptr + SFO_PARAMS_ACCOUNT_ID_OFFSET,
                           params_account_id, SFO_ACCOUNT_ID_SIZE);
                }

                debug_log("sfo_patch: patched PARAMS (user_id=%u, acct=%.16s)",
                          patch->user_id, params_account_id);
            }
        }

        /* Fix PARAMS2 format type: RPCS3 uses 0x0204, real PS3 uses 0x0004. */
        if (strcmp(key_name, "PARAMS2") == 0 &&
            (param_fmt == SFO_FMT_UTF8_S || param_fmt == SFO_FMT_UTF8)) {
            if (param_fmt != SFO_FMT_UTF8) {
                wr16(idx + 2, SFO_FMT_UTF8);
                debug_log("sfo_patch: fixed PARAMS2 format 0x%04x -> 0x0004",
                          param_fmt);
            }
            /* Fix param_len: RPCS3 writes len=1, real PS3 uses len=param_max */
            if (param_len < param_max) {
                /* Zero-fill the gap */
                memset(data_ptr + param_len, 0, param_max - param_len);
                wr32(idx + 4, param_max);
                debug_log("sfo_patch: fixed PARAMS2 len %u -> %u",
                          param_len, param_max);
            }
        }

        /* Remove copy protection from ATTRIBUTE field */
        if (strcmp(key_name, "ATTRIBUTE") == 0 && param_fmt == SFO_FMT_U32) {
            if (patch->flags & SFO_PATCH_FLAG_REMOVE_COPY_PROTECTION) {
                uint32_t attr = rd32(data_ptr);
                attr &= ~SFO_ATTR_COPY_PROTECTED;
                wr32(data_ptr, attr);
                debug_log("sfo_patch: removed copy protection (attr=0x%08x)", attr);
            }
        }

        (void)param_len;
    }

    /* Rebuild the SFO without RPCS3-only metadata entries so the result
     * matches a native PS3 save layout more closely. */
    {
        uint32_t kept_entries = 0;
        uint32_t key_bytes = 0;
        uint32_t data_bytes = 0;
        bool removed_any = false;

        for (uint32_t i = 0; i < num_entries; i++) {
            uint8_t *idx = buf + SFO_HEADER_SIZE + i * SFO_INDEX_ENTRY_SIZE;
            uint16_t key_off   = rd16(idx);
            uint32_t param_max = rd32(idx + 8);
            const char *key_name = (const char *)(buf + key_table_off + key_off);
            uint32_t effective_max = normalized_param_max(key_name, param_max);

            if (is_rpcs3_sfo_entry(key_name)) {
                removed_any = true;
                debug_log("sfo_patch: removing RPCS3 entry %s", key_name);
                continue;
            }

            kept_entries++;
            key_bytes += (uint32_t)strlen(key_name) + 1U;
            data_bytes += align_up_4(effective_max);
        }

        if (removed_any) {
            uint32_t new_key_off = SFO_HEADER_SIZE + kept_entries * SFO_INDEX_ENTRY_SIZE;
            uint32_t new_data_off = align_up_4(new_key_off + key_bytes);
            uint32_t new_size = new_data_off + data_bytes;
            uint8_t *new_buf = (uint8_t *)calloc(1, new_size);
            uint32_t new_i = 0;
            uint32_t next_key = 0;
            uint32_t next_data = 0;

            if (!new_buf) {
                debug_log("sfo_patch: rebuild alloc failed");
                goto done;
            }

            wr32(new_buf + 0, SFO_MAGIC);
            wr32(new_buf + 4, SFO_VERSION);
            wr32(new_buf + 8, new_key_off);
            wr32(new_buf + 12, new_data_off);
            wr32(new_buf + 16, kept_entries);

            for (uint32_t i = 0; i < num_entries; i++) {
                uint8_t *idx = buf + SFO_HEADER_SIZE + i * SFO_INDEX_ENTRY_SIZE;
                uint16_t key_off   = rd16(idx);
                uint16_t param_fmt = rd16(idx + 2);
                uint32_t param_len = rd32(idx + 4);
                uint32_t param_max = rd32(idx + 8);
                uint32_t data_off  = rd32(idx + 12);
                const char *key_name = (const char *)(buf + key_table_off + key_off);
                uint8_t *data_ptr = buf + data_table_off + data_off;

                if (is_rpcs3_sfo_entry(key_name)) continue;

                uint8_t *new_idx = new_buf + SFO_HEADER_SIZE + new_i * SFO_INDEX_ENTRY_SIZE;
                size_t key_len = strlen(key_name) + 1U;
                uint32_t effective_max = normalized_param_max(key_name, param_max);
                uint32_t data_span = align_up_4(effective_max);
                uint32_t copy_len = param_max;
                if (copy_len > effective_max) copy_len = effective_max;

                wr16(new_idx + 0, (uint16_t)next_key);
                wr16(new_idx + 2, param_fmt);
                wr32(new_idx + 4, param_len);
                wr32(new_idx + 8, effective_max);
                wr32(new_idx + 12, next_data);

                memcpy(new_buf + new_key_off + next_key, key_name, key_len);
                memcpy(new_buf + new_data_off + next_data, data_ptr, copy_len);

                next_key += (uint32_t)key_len;
                next_data += data_span;
                new_i++;
            }

            free(buf);
            buf = new_buf;
            fsize = (long)new_size;
            key_table_off = new_key_off;
            data_table_off = new_data_off;
            num_entries = kept_entries;
            debug_log("sfo_patch: rebuilt SFO without RPCS3 entries (%u entries)",
                      kept_entries);
        }
    }

    /* Write patched SFO back */
    fp = fopen(sfo_path, "wb");
    if (!fp) {
        debug_log("sfo_patch: failed to open for writing");
        goto done;
    }

    if (fwrite(buf, 1, (size_t)fsize, fp) != (size_t)fsize) {
        debug_log("sfo_patch: write error");
        fclose(fp);
        goto done;
    }
    fclose(fp);
    fp = NULL;

    debug_log("sfo_patch: success");
    result = 0;

done:
    if (fp) fclose(fp);
    free(buf);
    return result;
}
