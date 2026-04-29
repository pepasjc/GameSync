/*
 * zip_extract.c — minimal ZIP_STORED reader.
 *
 * Walks Local File Headers (signature 0x04034b50) sequentially, copying
 * each member's raw bytes into ``out_dir/<member-name>``.  Stops at the
 * Central Directory marker (0x02014b50) or End of Central Directory
 * (0x06054b50).  No compression, no encryption, no ZIP64.
 *
 * Local File Header layout (little-endian, fixed offsets):
 *
 *   0x00  uint32  signature       (0x04034b50)
 *   0x04  uint16  version_needed
 *   0x06  uint16  flags
 *   0x08  uint16  compression     (must be 0 for stored)
 *   0x0A  uint16  mod_time
 *   0x0C  uint16  mod_date
 *   0x0E  uint32  crc32
 *   0x12  uint32  compressed_size
 *   0x16  uint32  uncompressed_size
 *   0x1A  uint16  name_length
 *   0x1C  uint16  extra_length
 *   0x1E  char[]  name
 *               char[]  extra
 *               char[]  data        (compressed_size bytes; same as
 *                                    uncompressed_size for stored)
 *
 * 0xFFFFFFFF in size fields signals ZIP64 — we surface that as an
 * explicit error so the caller can fall back to a different path.
 */

#include "zip_extract.h"
#include "common.h"
#include "debug.h"

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <lv2/sysfs.h>

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

/* mkdir -p one component at a time.  ``path`` is mutated temporarily
 * (NUL inserted at each separator, then restored).  No recursion to
 * keep stack shallow on PS3. */
static void mkdir_path(char *path) {
    if (!path || !*path) return;
    char *p = path;
    if (*p == '/') p++;            /* skip leading slash */
    while (*p) {
        if (*p == '/') {
            *p = '\0';
            mkdir(path, 0755);
            sysFsMkdir(path, 0777);  /* ignore error; mkdir() may have won */
            *p = '/';
        }
        p++;
    }
    /* Final component (if it doesn't end with a slash). */
    mkdir(path, 0755);
    sysFsMkdir(path, 0777);
}

/* Validate that ``name`` doesn't escape its destination via .. or
 * absolute paths.  Mirrors the server's _serve_bundle_zip guard. */
static bool name_is_safe(const char *name) {
    if (!name || !*name) return false;
    if (name[0] == '/' || name[0] == '\\') return false;
    /* Reject any ".." path component. */
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
        debug_log("zip: open %s failed errno=%d", zip_path, errno);
        return false;
    }

    /* Make sure the destination tree exists.  ``out_dir`` itself may
     * be nested several levels deep (PSXISO/<game>/) so use the
     * mkdir-path helper. */
    {
        char tmp[PATH_LEN];
        snprintf(tmp, sizeof(tmp), "%s", out_dir);
        mkdir_path(tmp);
    }

    static uint8_t header[LFH_FIXED_LEN];
    static uint8_t buf[EXTRACT_BUF_SIZE];
    uint32_t members = 0;

    while (1) {
        size_t got = fread(header, 1, LFH_FIXED_LEN, zf);
        if (got == 0) break;
        if (got < LFH_FIXED_LEN) {
            set_err(error_out, error_out_size, "Truncated header");
            fclose(zf);
            return false;
        }

        uint32_t sig = le32(header);
        if (sig == CDH_SIGNATURE || sig == EOCD_SIGNATURE) {
            /* Reached the central directory — every Local File Header
             * has been consumed.  We don't need anything from CDH. */
            break;
        }
        if (sig != LFH_SIGNATURE) {
            set_err(error_out, error_out_size, "Bad LFH signature");
            debug_log("zip: bad sig 0x%08x at member %u", sig, members);
            fclose(zf);
            return false;
        }

        uint16_t compression = le16(header + 0x08);
        uint32_t comp_size   = le32(header + 0x12);
        uint32_t uncomp_size = le32(header + 0x16);
        uint16_t name_len    = le16(header + 0x1A);
        uint16_t extra_len   = le16(header + 0x1C);

        if (compression != 0) {
            set_err(error_out, error_out_size, "Compressed members not supported");
            fclose(zf);
            return false;
        }
        if (comp_size == 0xFFFFFFFFu || uncomp_size == 0xFFFFFFFFu) {
            set_err(error_out, error_out_size, "ZIP64 not supported");
            fclose(zf);
            return false;
        }
        if (comp_size != uncomp_size) {
            set_err(error_out, error_out_size, "Stored size mismatch");
            fclose(zf);
            return false;
        }
        if (name_len >= PATH_LEN - 1) {
            set_err(error_out, error_out_size, "Member name too long");
            fclose(zf);
            return false;
        }

        char name[PATH_LEN];
        if (fread(name, 1, name_len, zf) != name_len) {
            set_err(error_out, error_out_size, "Read name failed");
            fclose(zf);
            return false;
        }
        name[name_len] = '\0';

        /* Skip the extra field — we don't consume any of its records. */
        if (extra_len > 0) {
            if (fseek(zf, extra_len, SEEK_CUR) != 0) {
                set_err(error_out, error_out_size, "Skip extra failed");
                fclose(zf);
                return false;
            }
        }

        if (!name_is_safe(name)) {
            debug_log("zip: refusing unsafe member %s", name);
            set_err(error_out, error_out_size, "Path traversal");
            fclose(zf);
            return false;
        }

        /* Directory entries inside ZIPs end with '/' and have size 0.
         * mkdir the path, then skip to next header. */
        if (name_len > 0 && (name[name_len - 1] == '/' ||
                             name[name_len - 1] == '\\')) {
            char dir_path[PATH_LEN];
            snprintf(dir_path, sizeof(dir_path), "%s/%s", out_dir, name);
            /* Strip trailing slash before mkdir. */
            size_t dl = strlen(dir_path);
            while (dl > 0 && (dir_path[dl - 1] == '/' || dir_path[dl - 1] == '\\')) {
                dir_path[--dl] = '\0';
            }
            mkdir_path(dir_path);
            continue;
        }

        /* Build the destination path and ensure parent dirs exist. */
        char out_path[PATH_LEN];
        if (snprintf(out_path, sizeof(out_path), "%s/%s",
                     out_dir, name) >= (int)sizeof(out_path))
        {
            set_err(error_out, error_out_size, "Output path overflow");
            fclose(zf);
            return false;
        }
        {
            char parent[PATH_LEN];
            snprintf(parent, sizeof(parent), "%s", out_path);
            char *slash = strrchr(parent, '/');
            if (slash) {
                *slash = '\0';
                mkdir_path(parent);
            }
        }

        FILE *out = fopen(out_path, "wb");
        if (!out) {
            set_err(error_out, error_out_size, "Cannot create output file");
            debug_log("zip: open %s failed errno=%d", out_path, errno);
            fclose(zf);
            return false;
        }

        uint32_t remaining = uncomp_size;
        while (remaining > 0) {
            uint32_t want = remaining < (uint32_t)sizeof(buf)
                          ? remaining : (uint32_t)sizeof(buf);
            size_t r = fread(buf, 1, want, zf);
            if (r == 0) {
                fclose(out);
                set_err(error_out, error_out_size, "Read body failed");
                fclose(zf);
                return false;
            }
            if (fwrite(buf, 1, r, out) != r) {
                fclose(out);
                set_err(error_out, error_out_size, "Write body failed");
                fclose(zf);
                return false;
            }
            remaining -= (uint32_t)r;
            /* Pump sysutil between chunks — large CHD extracts can
             * produce hundreds of MB of cue/bin data so the OS would
             * otherwise consider us frozen. */
            pump_callbacks();
        }

        fclose(out);
        members++;
    }

    fclose(zf);
    debug_log("zip: extracted %u members from %s into %s",
              members, zip_path, out_dir);
    return true;
}
