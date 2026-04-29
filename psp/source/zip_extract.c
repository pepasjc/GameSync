/*
 * zip_extract.c — minimal ZIP_STORED reader for PSP.
 *
 * Walks Local File Headers and copies raw bytes into per-member
 * files.  No compression, no encryption, no ZIP64.  PSP libc supports
 * fopen/fwrite/rename on ms0:/ paths so the implementation is the
 * same as the PS3 module.
 */

#include "zip_extract.h"
#include "common.h"
#include "roms.h"

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include <pspiofilemgr.h>

#define LFH_SIGNATURE  0x04034b50u
#define CDH_SIGNATURE  0x02014b50u
#define EOCD_SIGNATURE 0x06054b50u
#define LFH_FIXED_LEN  30
#define EXTRACT_BUF_SIZE 65536

static uint16_t le16(const uint8_t *p) {
    return (uint16_t)(p[0] | (p[1] << 8));
}

static uint32_t le32(const uint8_t *p) {
    return (uint32_t)p[0]
         | ((uint32_t)p[1] << 8)
         | ((uint32_t)p[2] << 16)
         | ((uint32_t)p[3] << 24);
}

static void set_err(char *out, size_t out_size, const char *msg) {
    if (out && out_size) snprintf(out, out_size, "%s", msg);
}

static bool name_is_safe(const char *name) {
    if (!name || !*name) return false;
    if (name[0] == '/' || name[0] == '\\') return false;
    const char *seg = name;
    while (*seg) {
        const char *next = seg;
        while (*next && *next != '/' && *next != '\\') next++;
        size_t len = (size_t)(next - seg);
        if (len == 2 && seg[0] == '.' && seg[1] == '.') return false;
        if (!*next) break;
        seg = next + 1;
    }
    return true;
}

bool zip_extract_stored(const char *zip_path, const char *out_dir,
                        char *error_out, size_t error_out_size) {
    if (!zip_path || !out_dir) {
        set_err(error_out, error_out_size, "Bad arguments");
        return false;
    }

    FILE *zf = fopen(zip_path, "rb");
    if (!zf) {
        set_err(error_out, error_out_size, "Cannot open ZIP");
        return false;
    }

    roms_mkdir_p(out_dir);

    static uint8_t header[LFH_FIXED_LEN];
    static uint8_t buf[EXTRACT_BUF_SIZE];

    while (1) {
        size_t got = fread(header, 1, LFH_FIXED_LEN, zf);
        if (got == 0) break;
        if (got < LFH_FIXED_LEN) {
            set_err(error_out, error_out_size, "Truncated header");
            fclose(zf); return false;
        }

        uint32_t sig = le32(header);
        if (sig == CDH_SIGNATURE || sig == EOCD_SIGNATURE) break;
        if (sig != LFH_SIGNATURE) {
            set_err(error_out, error_out_size, "Bad LFH signature");
            fclose(zf); return false;
        }

        uint16_t compression = le16(header + 0x08);
        uint32_t comp_size   = le32(header + 0x12);
        uint32_t uncomp_size = le32(header + 0x16);
        uint16_t name_len    = le16(header + 0x1A);
        uint16_t extra_len   = le16(header + 0x1C);

        if (compression != 0) {
            set_err(error_out, error_out_size, "Compressed members not supported");
            fclose(zf); return false;
        }
        if (comp_size == 0xFFFFFFFFu || uncomp_size == 0xFFFFFFFFu) {
            set_err(error_out, error_out_size, "ZIP64 not supported");
            fclose(zf); return false;
        }
        if (comp_size != uncomp_size) {
            set_err(error_out, error_out_size, "Stored size mismatch");
            fclose(zf); return false;
        }
        if (name_len >= 480) {
            set_err(error_out, error_out_size, "Member name too long");
            fclose(zf); return false;
        }

        char name[512];
        if (fread(name, 1, name_len, zf) != name_len) {
            set_err(error_out, error_out_size, "Read name failed");
            fclose(zf); return false;
        }
        name[name_len] = '\0';

        if (extra_len > 0) {
            if (fseek(zf, extra_len, SEEK_CUR) != 0) {
                set_err(error_out, error_out_size, "Skip extra failed");
                fclose(zf); return false;
            }
        }

        if (!name_is_safe(name)) {
            set_err(error_out, error_out_size, "Path traversal");
            fclose(zf); return false;
        }

        if (name_len > 0 && (name[name_len - 1] == '/' ||
                             name[name_len - 1] == '\\')) {
            char dir_path[512];
            snprintf(dir_path, sizeof(dir_path), "%s/%s", out_dir, name);
            size_t dl = strlen(dir_path);
            while (dl > 0 && (dir_path[dl - 1] == '/' || dir_path[dl - 1] == '\\')) {
                dir_path[--dl] = '\0';
            }
            roms_mkdir_p(dir_path);
            continue;
        }

        char out_path[512];
        if (snprintf(out_path, sizeof(out_path), "%s/%s",
                     out_dir, name) >= (int)sizeof(out_path))
        {
            set_err(error_out, error_out_size, "Output path overflow");
            fclose(zf); return false;
        }
        {
            char parent[512];
            snprintf(parent, sizeof(parent), "%s", out_path);
            char *slash = strrchr(parent, '/');
            if (slash) {
                *slash = '\0';
                roms_mkdir_p(parent);
            }
        }

        FILE *out = fopen(out_path, "wb");
        if (!out) {
            set_err(error_out, error_out_size, "Cannot create output file");
            fclose(zf); return false;
        }

        uint32_t remaining = uncomp_size;
        while (remaining > 0) {
            uint32_t want = remaining < (uint32_t)sizeof(buf)
                          ? remaining : (uint32_t)sizeof(buf);
            size_t r = fread(buf, 1, want, zf);
            if (r == 0) {
                fclose(out);
                set_err(error_out, error_out_size, "Read body failed");
                fclose(zf); return false;
            }
            if (fwrite(buf, 1, r, out) != r) {
                fclose(out);
                set_err(error_out, error_out_size, "Write body failed");
                fclose(zf); return false;
            }
            remaining -= (uint32_t)r;
        }
        fclose(out);
    }

    fclose(zf);
    return true;
}
