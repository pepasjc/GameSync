/*
 * bundle.c — streaming 3DSS v5 encoder / applier.  See bundle.h.
 *
 * Ported from xbox/source/bundle.c with the Win32 file API swapped for stdio
 * and the fixed E:\UDATA root replaced by SaveTitle.root / dest_root.
 */

#include "bundle.h"
#include "sha256.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include <zlib.h>

#define ZLIB_CHUNK 32768U

/* Reject a server-supplied member name that would escape the target dir. */
static int bundle_path_unsafe(const char *name) {
    if (!name || !name[0]) return 1;
    if (strstr(name, "..")) return 1;
    if (strchr(name, ':')) return 1;
    if (name[0] == '/' || name[0] == '\\') return 1;
    return 0;
}

/* ---- LE helpers ---- */

static void write_le16(uint8_t *p, uint16_t v) {
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
}

static void write_le32(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >>  8);
    p[2] = (uint8_t)(v >> 16);
    p[3] = (uint8_t)(v >> 24);
}

static uint16_t read_le16(const uint8_t *p) {
    return (uint16_t)p[0] | ((uint16_t)p[1] << 8);
}

static uint32_t read_le32(const uint8_t *p) {
    return (uint32_t)p[0]
         | ((uint32_t)p[1] <<  8)
         | ((uint32_t)p[2] << 16)
         | ((uint32_t)p[3] << 24);
}

static void hash_to_hex(const uint8_t hash[32], char *hash_hex) {
    static const char H[] = "0123456789abcdef";
    if (!hash_hex) return;
    for (int i = 0; i < 32; i++) {
        hash_hex[i * 2]     = H[(hash[i] >> 4) & 0xF];
        hash_hex[i * 2 + 1] = H[hash[i] & 0xF];
    }
    hash_hex[64] = '\0';
}

static void full_path(const SaveTitle *t, const SaveFile *f,
                      char *out, size_t out_size) {
    snprintf(out, out_size, "%s/%s", t->root, f->relative_path);
}

/* ---- zlib deflate (streaming) ---- */

typedef struct {
    z_stream      zs;
    BundleWriteFn write;
    void         *write_ctx;
    uint8_t       out[ZLIB_CHUNK];
} BundleDeflater;

static int deflater_write_pending(BundleDeflater *d) {
    uint32_t produced = ZLIB_CHUNK - d->zs.avail_out;
    if (produced == 0) return 0;
    return d->write(d->write_ctx, d->out, produced);
}

static int deflater_feed(BundleDeflater *d, const uint8_t *data, uint32_t size) {
    d->zs.next_in  = (Bytef *)data;
    d->zs.avail_in = size;

    while (d->zs.avail_in > 0) {
        d->zs.next_out  = d->out;
        d->zs.avail_out = ZLIB_CHUNK;
        if (deflate(&d->zs, Z_NO_FLUSH) != Z_OK) return -1;
        if (deflater_write_pending(d) != 0) return -1;
    }
    return 0;
}

static int deflater_finish(BundleDeflater *d) {
    for (;;) {
        d->zs.next_out  = d->out;
        d->zs.avail_out = ZLIB_CHUNK;
        int zr = deflate(&d->zs, Z_FINISH);
        if (zr != Z_OK && zr != Z_STREAM_END) return -1;
        if (deflater_write_pending(d) != 0) return -1;
        if (zr == Z_STREAM_END) return 0;
    }
}

/* ---- save hash ---- */

int bundle_compute_save_hash(const SaveTitle *title,
                             uint8_t hash[32],
                             char *hash_hex) {
    if (!title || !hash) return -1;

    SHA256_CTX ctx;
    sha256_init(&ctx);

    char path[SAVE_DIR_LEN + SAVE_PATH_MAX];
    uint8_t *chunk = (uint8_t *)malloc(ZLIB_CHUNK);
    if (!chunk) return -1;

    int rc = 0;
    for (int i = 0; i < title->file_count; i++) {
        const SaveFile *f = &title->files[i];
        if (f->file_size == 0) continue;
        full_path(title, f, path, sizeof(path));

        FILE *fp = fopen(path, "rb");
        if (!fp) { rc = -1; break; }
        size_t got;
        while ((got = fread(chunk, 1, ZLIB_CHUNK, fp)) > 0)
            sha256_update(&ctx, chunk, got);
        int ferr = ferror(fp);
        fclose(fp);
        if (ferr) { rc = -1; break; }
    }
    free(chunk);
    if (rc != 0) return rc;

    sha256_final(&ctx, hash);
    hash_to_hex(hash, hash_hex);
    return 0;
}

/* Per-file hashes (for the file table) plus the whole-save hash, in one pass. */
static int prehash_title_files(const SaveTitle *title,
                               uint8_t *file_hashes,
                               uint8_t save_hash[32]) {
    SHA256_CTX save_ctx;
    sha256_init(&save_ctx);

    char path[SAVE_DIR_LEN + SAVE_PATH_MAX];
    uint8_t *chunk = (uint8_t *)malloc(ZLIB_CHUNK);
    if (!chunk) return -1;

    int rc = 0;
    for (int i = 0; i < title->file_count; i++) {
        const SaveFile *f = &title->files[i];
        SHA256_CTX file_ctx;
        sha256_init(&file_ctx);

        if (f->file_size > 0) {
            full_path(title, f, path, sizeof(path));
            FILE *fp = fopen(path, "rb");
            if (!fp) { rc = -1; break; }
            size_t got;
            while ((got = fread(chunk, 1, ZLIB_CHUNK, fp)) > 0) {
                sha256_update(&file_ctx, chunk, got);
                sha256_update(&save_ctx, chunk, got);
            }
            int ferr = ferror(fp);
            fclose(fp);
            if (ferr) { rc = -1; break; }
        }
        sha256_final(&file_ctx, file_hashes + i * 32);
    }
    free(chunk);
    if (rc != 0) return rc;

    sha256_final(&save_ctx, save_hash);
    return 0;
}

int bundle_stream_create(const SaveTitle *title,
                         uint32_t timestamp,
                         BundleWriteFn write,
                         void *write_ctx,
                         char *save_hash_hex) {
    if (!title || !write || title->file_count <= 0) return -1;

    int n = title->file_count;
    uint8_t *file_hashes = (uint8_t *)malloc((size_t)n * 32);
    if (!file_hashes) return -1;

    uint8_t save_hash[32];
    if (prehash_title_files(title, file_hashes, save_hash) != 0) {
        free(file_hashes);
        return -1;
    }
    hash_to_hex(save_hash, save_hash_hex);

    uint32_t payload_size = 0;
    for (int i = 0; i < n; i++) {
        const SaveFile *f = &title->files[i];
        payload_size += 2 + (uint32_t)strlen(f->relative_path) + 4 + 32 + f->file_size;
    }

    uint8_t header[BUNDLE_HEADER_SIZE_V5];
    memcpy(header, BUNDLE_MAGIC, 4);
    write_le32(header + 4, BUNDLE_VERSION_V5);
    memset(header + 8, 0, 64);
    size_t tid_len = strlen(title->title_id);
    if (tid_len > 63) tid_len = 63;
    memcpy(header + 8, title->title_id, tid_len);
    write_le32(header + 72, timestamp);
    write_le32(header + 76, (uint32_t)n);
    write_le32(header + 80, payload_size);

    if (write(write_ctx, header, sizeof(header)) != 0) {
        free(file_hashes);
        return -1;
    }

    BundleDeflater *d = (BundleDeflater *)calloc(1, sizeof(*d));
    if (!d) { free(file_hashes); return -1; }
    d->write = write;
    d->write_ctx = write_ctx;
    if (deflateInit(&d->zs, 6) != Z_OK) {
        free(d);
        free(file_hashes);
        return -1;
    }

    int rc = -1;
    uint8_t *chunk = NULL;
    uint8_t *entry = (uint8_t *)malloc(2 + SAVE_PATH_MAX + 4 + 32);
    if (!entry) goto done;

    for (int i = 0; i < n; i++) {
        const SaveFile *f = &title->files[i];
        uint16_t path_len = (uint16_t)strlen(f->relative_path);
        uint32_t off = 0;

        if (path_len >= SAVE_PATH_MAX) goto done;
        write_le16(entry + off, path_len);
        off += 2;
        memcpy(entry + off, f->relative_path, path_len);
        off += path_len;
        write_le32(entry + off, f->file_size);
        off += 4;
        memcpy(entry + off, file_hashes + i * 32, 32);
        off += 32;

        if (deflater_feed(d, entry, off) != 0) goto done;
    }

    chunk = (uint8_t *)malloc(ZLIB_CHUNK);
    if (!chunk) goto done;

    for (int i = 0; i < n; i++) {
        const SaveFile *f = &title->files[i];
        if (f->file_size == 0) continue;

        char path[SAVE_DIR_LEN + SAVE_PATH_MAX];
        full_path(title, f, path, sizeof(path));
        FILE *fp = fopen(path, "rb");
        if (!fp) goto done;

        uint32_t remaining = f->file_size;
        int fail = 0;
        while (remaining > 0) {
            size_t want = remaining < ZLIB_CHUNK ? remaining : ZLIB_CHUNK;
            size_t got  = fread(chunk, 1, want, fp);
            if (got == 0) { fail = 1; break; }
            if (deflater_feed(d, chunk, (uint32_t)got) != 0) { fail = 1; break; }
            remaining -= (uint32_t)got;
        }
        fclose(fp);
        if (fail) goto done;
    }

    if (deflater_finish(d) != 0) goto done;
    rc = 0;

done:
    deflateEnd(&d->zs);
    free(d);
    free(entry);
    free(chunk);
    free(file_hashes);
    return rc;
}

/* ---- apply (download path) ---- */

static int read_exact(FILE *fp, void *buf, size_t size) {
    return fread(buf, 1, size, fp) == size ? 0 : -1;
}

static int bundle_header_read_file(FILE *fp,
                                   uint32_t *out_version,
                                   uint32_t *out_file_count,
                                   uint32_t *out_payload_size) {
    uint8_t first[8];
    if (read_exact(fp, first, sizeof(first)) != 0) return -1;
    if (memcmp(first, BUNDLE_MAGIC, 4) != 0) return -1;
    uint32_t version = read_le32(first + 4);

    size_t skip;
    if      (version == BUNDLE_VERSION_V5) skip = 64;
    else if (version == 4)                 skip = 32;
    else if (version == 3)                 skip = 16;
    else if (version == 2)                 skip = 8;
    else                                   return -1;   /* v1 is uncompressed */

    uint8_t tmp[64];
    if (read_exact(fp, tmp, skip) != 0) return -1;

    uint8_t tail[12];
    if (read_exact(fp, tail, sizeof(tail)) != 0) return -1;
    if (out_version)      *out_version      = version;
    if (out_file_count)   *out_file_count   = read_le32(tail + 4);
    if (out_payload_size) *out_payload_size = read_le32(tail + 8);
    return 0;
}

static int inflate_payload_to_file(FILE *in, FILE *out, uint32_t expected_size) {
    z_stream zs;
    memset(&zs, 0, sizeof(zs));
    if (inflateInit(&zs) != Z_OK) return -1;

    uint8_t *inbuf  = (uint8_t *)malloc(ZLIB_CHUNK);
    uint8_t *outbuf = (uint8_t *)malloc(ZLIB_CHUNK);
    if (!inbuf || !outbuf) {
        free(inbuf); free(outbuf);
        inflateEnd(&zs);
        return -1;
    }

    int rc = -1, done = 0;
    uint32_t total_out = 0;
    while (!done) {
        size_t got = fread(inbuf, 1, ZLIB_CHUNK, in);
        if (got == 0) break;
        zs.next_in  = inbuf;
        zs.avail_in = (uInt)got;

        while (zs.avail_in > 0) {
            zs.next_out  = outbuf;
            zs.avail_out = ZLIB_CHUNK;
            int zr = inflate(&zs, Z_NO_FLUSH);
            if (zr != Z_OK && zr != Z_STREAM_END) goto finish;
            uInt have = ZLIB_CHUNK - zs.avail_out;
            if (have > 0) {
                if (fwrite(outbuf, 1, have, out) != have) goto finish;
                total_out += have;
            }
            if (zr == Z_STREAM_END) { done = 1; break; }
            if (have == 0 && zs.avail_in == 0) break;
        }
    }
    rc = (done && total_out == expected_size) ? 0 : -1;

finish:
    inflateEnd(&zs);
    free(inbuf);
    free(outbuf);
    return rc;
}

/* Create every parent directory of a file path. */
static int make_parents(const char *path) {
    char tmp[SAVE_DIR_LEN + SAVE_PATH_MAX];
    strncpy(tmp, path, sizeof(tmp) - 1);
    tmp[sizeof(tmp) - 1] = '\0';
    char *slash = strrchr(tmp, '/');
    if (!slash) return 0;
    *slash = '\0';
    return savetree_mkdir_p(tmp);
}

typedef struct {
    char     relative_path[SAVE_PATH_MAX];
    uint32_t size;
    uint8_t  sha256[32];
} StreamBundleFile;

int bundle_apply_file_to_disk(const char *bundle_path, const char *dest_root) {
    if (!bundle_path || !dest_root || !dest_root[0]) return -1;

    FILE *in = fopen(bundle_path, "rb");
    if (!in) return -1;

    uint32_t version = 0, file_count = 0, payload_size = 0;
    if (bundle_header_read_file(in, &version, &file_count, &payload_size) != 0 ||
        file_count == 0 || file_count > SAVE_MAX_FILES_PER_TITLE) {
        fclose(in);
        return -1;
    }

    char payload_path[SAVE_DIR_LEN + 64];
    snprintf(payload_path, sizeof(payload_path), "%s.payload", bundle_path);
    FILE *payload = fopen(payload_path, "w+b");
    if (!payload) { fclose(in); return -1; }

    int rc = inflate_payload_to_file(in, payload, payload_size);
    fclose(in);
    if (rc != 0) {
        fclose(payload);
        remove(payload_path);
        return -1;
    }
    rewind(payload);

    StreamBundleFile *files =
        (StreamBundleFile *)calloc(file_count, sizeof(StreamBundleFile));
    if (!files) { fclose(payload); remove(payload_path); return -1; }

    uint8_t *chunk = NULL;
    rc = -1;
    for (uint32_t i = 0; i < file_count; i++) {
        uint8_t lenbuf[2];
        if (read_exact(payload, lenbuf, sizeof(lenbuf)) != 0) goto done;
        uint16_t plen = read_le16(lenbuf);
        if (plen == 0 || plen >= SAVE_PATH_MAX) goto done;
        if (read_exact(payload, files[i].relative_path, plen) != 0) goto done;
        files[i].relative_path[plen] = '\0';
        if (bundle_path_unsafe(files[i].relative_path)) goto done;

        uint8_t sizebuf[4];
        if (read_exact(payload, sizebuf, sizeof(sizebuf)) != 0) goto done;
        files[i].size = read_le32(sizebuf);
        if (read_exact(payload, files[i].sha256, 32) != 0) goto done;
    }

    if (savetree_mkdir_p(dest_root) != 0) goto done;

    chunk = (uint8_t *)malloc(ZLIB_CHUNK);
    if (!chunk) goto done;

    for (uint32_t i = 0; i < file_count; i++) {
        StreamBundleFile *f = &files[i];
        char fullpath[SAVE_DIR_LEN + SAVE_PATH_MAX];
        snprintf(fullpath, sizeof(fullpath), "%s/%s", dest_root, f->relative_path);

        if (make_parents(fullpath) != 0) goto done;

        FILE *out = fopen(fullpath, "wb");
        if (!out) goto done;

        SHA256_CTX hash_ctx;
        sha256_init(&hash_ctx);
        uint32_t remaining = f->size;
        int fail = 0;
        while (remaining > 0) {
            size_t want = remaining < ZLIB_CHUNK ? remaining : ZLIB_CHUNK;
            if (read_exact(payload, chunk, want) != 0) { fail = 1; break; }
            if (fwrite(chunk, 1, want, out) != want)   { fail = 1; break; }
            sha256_update(&hash_ctx, chunk, want);
            remaining -= (uint32_t)want;
        }
        if (fclose(out) != 0) fail = 1;
        if (fail) goto done;

        uint8_t got_hash[32];
        sha256_final(&hash_ctx, got_hash);
        if (memcmp(got_hash, f->sha256, 32) != 0) goto done;
    }
    rc = 0;

done:
    free(chunk);
    free(files);
    fclose(payload);
    remove(payload_path);
    return rc;
}
