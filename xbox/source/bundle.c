// v5 bundle encoder for the Xbox client. See bundle.h for wire format.

#include "bundle.h"
#include "sha256.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <hal/debug.h>
#include <windows.h>
#include <zlib.h>

#define ZLIB_CHUNK 8192U

// nxdk's zlib is built with Z_SOLO so the convenience helpers (compress(),
// uncompress(), compressBound()) are excluded. Replicate the worst-case
// bound formula straight from zlib's compress.c so we can size the output
// buffer for deflate.
static uLong xbox_compress_bound(uLong source_len)
{
    return source_len
         + (source_len >> 12)
         + (source_len >> 14)
         + (source_len >> 25)
         + 13;
}

// ---- LE writers ------------------------------------------------------------

static void write_le16(uint8_t *p, uint16_t v)
{
    p[0] = (uint8_t)(v);
    p[1] = (uint8_t)(v >> 8);
}

static void write_le32(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)(v);
    p[1] = (uint8_t)(v >>  8);
    p[2] = (uint8_t)(v >> 16);
    p[3] = (uint8_t)(v >> 24);
}

static uint16_t read_le16(const uint8_t *p)
{
    return (uint16_t)p[0] | ((uint16_t)p[1] << 8);
}

static uint32_t read_le32(const uint8_t *p)
{
    return (uint32_t)p[0]
         | ((uint32_t)p[1] <<  8)
         | ((uint32_t)p[2] << 16)
         | ((uint32_t)p[3] << 24);
}

// ---- File I/O --------------------------------------------------------------

// Read a full file into a malloc'd buffer. Caller frees. Returns NULL on
// error or if expected_size disagrees with the actual file size on disk
// (caller's stat info became stale in some catastrophic way).
static uint8_t *read_full_file(const char *path, uint32_t expected_size)
{
    HANDLE h = CreateFileA(path, GENERIC_READ, FILE_SHARE_READ, NULL,
                           OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) {
        return NULL;
    }
    uint8_t *buf = (uint8_t *)malloc(expected_size > 0 ? expected_size : 1);
    if (!buf) {
        CloseHandle(h);
        return NULL;
    }
    DWORD off = 0;
    while (off < expected_size) {
        DWORD want = expected_size - off;
        if (want > ZLIB_CHUNK) want = ZLIB_CHUNK;
        DWORD got = 0;
        if (!ReadFile(h, buf + off, want, &got, NULL) || got == 0) {
            free(buf);
            CloseHandle(h);
            return NULL;
        }
        off += got;
    }
    CloseHandle(h);
    return buf;
}

// ---- zlib deflate (chunked) -----------------------------------------------

// nxdk builds zlib with Z_SOLO, which omits the default zcalloc/zcfree
// allocator. zlib requires us to plug a (de)allocator pair into the
// z_stream or deflateInit fails. Wrap plain malloc/free.
static voidpf zlib_alloc(voidpf opaque, uInt items, uInt size)
{
    (void)opaque;
    return malloc((size_t)items * (size_t)size);
}

static void zlib_free(voidpf opaque, voidpf ptr)
{
    (void)opaque;
    free(ptr);
}

static void hash_to_hex(const uint8_t hash[32], char *hash_hex)
{
    static const char H[] = "0123456789abcdef";
    if (!hash_hex) return;
    for (int i = 0; i < 32; i++) {
        hash_hex[i * 2]     = H[(hash[i] >> 4) & 0xF];
        hash_hex[i * 2 + 1] = H[hash[i] & 0xF];
    }
    hash_hex[64] = '\0';
}

static int compress_payload(const uint8_t *payload, uint32_t payload_size,
                            uint8_t *compressed, uLongf *compressed_size)
{
    z_stream zs;
    memset(&zs, 0, sizeof(zs));
    zs.zalloc = zlib_alloc;
    zs.zfree  = zlib_free;
    zs.opaque = NULL;

    int zr = deflateInit(&zs, 6);
    if (zr != Z_OK) {
        debugPrint("bundle: deflateInit=%d\n", zr);
        return -1;
    }

    zs.next_in   = (Bytef *)payload;
    zs.avail_in  = 0;
    zs.next_out  = compressed;
    zs.avail_out = 0;

    for (;;) {
        if (zs.avail_in == 0 && zs.total_in < payload_size) {
            uInt chunk = (uInt)(payload_size - (uint32_t)zs.total_in);
            if (chunk > ZLIB_CHUNK) chunk = ZLIB_CHUNK;
            zs.next_in  = (Bytef *)(payload + zs.total_in);
            zs.avail_in = chunk;
        }
        if (zs.avail_out == 0) {
            uLongf remaining = *compressed_size - zs.total_out;
            uInt chunk = (uInt)remaining;
            if (chunk > ZLIB_CHUNK) chunk = ZLIB_CHUNK;
            if (chunk == 0) {
                deflateEnd(&zs);
                return -1;
            }
            zs.next_out  = compressed + zs.total_out;
            zs.avail_out = chunk;
        }

        int flush = (zs.total_in >= payload_size) ? Z_FINISH : Z_NO_FLUSH;
        zr = deflate(&zs, flush);

        if (zr == Z_STREAM_END) {
            *compressed_size = zs.total_out;
            deflateEnd(&zs);
            return 0;
        }
        if (zr != Z_OK) {
            debugPrint("bundle: deflate loop zr=%d in=%lu out=%lu\n",
                       zr,
                       (unsigned long)zs.total_in,
                       (unsigned long)zs.total_out);
            deflateEnd(&zs);
            return -1;
        }
    }
}

typedef struct {
    z_stream      zs;
    BundleWriteFn write;
    void         *write_ctx;
    uint8_t       out[ZLIB_CHUNK];
} BundleDeflater;

static int deflater_write_pending(BundleDeflater *d)
{
    uint32_t produced = ZLIB_CHUNK - d->zs.avail_out;
    if (produced == 0) return 0;
    return d->write(d->write_ctx, d->out, produced);
}

static int deflater_feed(BundleDeflater *d,
                         const uint8_t *data,
                         uint32_t size)
{
    d->zs.next_in = (Bytef *)data;
    d->zs.avail_in = size;

    while (d->zs.avail_in > 0) {
        d->zs.next_out = d->out;
        d->zs.avail_out = ZLIB_CHUNK;
        int zr = deflate(&d->zs, Z_NO_FLUSH);
        if (zr != Z_OK) {
            debugPrint("bundle: stream deflate zr=%d\n", zr);
            return -1;
        }
        if (deflater_write_pending(d) != 0) return -1;
    }
    return 0;
}

static int deflater_finish(BundleDeflater *d)
{
    for (;;) {
        d->zs.next_out = d->out;
        d->zs.avail_out = ZLIB_CHUNK;
        int zr = deflate(&d->zs, Z_FINISH);
        if (zr != Z_OK && zr != Z_STREAM_END) {
            debugPrint("bundle: stream finish zr=%d\n", zr);
            return -1;
        }
        if (deflater_write_pending(d) != 0) return -1;
        if (zr == Z_STREAM_END) return 0;
    }
}

// ---- Public API ------------------------------------------------------------

void bundle_hash_buffer(const uint8_t *data, uint32_t size,
                        uint32_t chunk, uint8_t hash[32])
{
    SHA256_CTX ctx;
    sha256_init(&ctx);

    if (chunk == 0) {
        sha256_update(&ctx, data, size);
    } else {
        uint32_t off = 0;
        while (off < size) {
            uint32_t take = size - off;
            if (take > chunk) take = chunk;
            sha256_update(&ctx, data + off, take);
            off += take;
        }
    }
    sha256_final(&ctx, hash);
}

int bundle_create(const XboxSaveTitle *title,
                  uint32_t timestamp,
                  uint8_t **out_data,
                  uint32_t *out_size)
{
    if (!title || !out_data || !out_size || title->file_count <= 0) {
        debugPrint("bundle: bad args\n");
        return -1;
    }

    int n = title->file_count;

    // Reserve per-file buffers; clean up via a single failure path.
    uint8_t **file_data = (uint8_t **)calloc((size_t)n, sizeof(uint8_t *));
    if (!file_data) {
        debugPrint("bundle: calloc(file_data) fail\n");
        return -1;
    }

    int rc = -1;
    uint8_t *payload    = NULL;
    uint8_t *compressed = NULL;
    uint8_t *bundle     = NULL;

    char fullpath[XBOX_PATH_MAX * 2];

    // 1. Read every file from disk.
    for (int i = 0; i < n; i++) {
        const XboxSaveFile *f = &title->files[i];
        snprintf(fullpath, sizeof(fullpath),
                 "E:\\UDATA\\%s\\%s",
                 title->title_id, f->relative_path);

        if (f->file_size == 0) {
            // Empty file - allocate a 1-byte sentinel so we never NULL-deref.
            file_data[i] = (uint8_t *)malloc(1);
            if (!file_data[i]) {
                debugPrint("bundle: alloc fail empty %s\n", f->relative_path);
                goto done;
            }
            continue;
        }
        file_data[i] = read_full_file(fullpath, f->file_size);
        if (!file_data[i]) {
            debugPrint("bundle: read fail %s (size=%u)\n",
                       fullpath, (unsigned)f->file_size);
            debugPrint("        GetLastError=%lu\n",
                       (unsigned long)GetLastError());
            goto done;
        }
    }

    // 2. Estimate payload size: per-file (2 path_len + path + 4 size + 32 hash + bytes).
    uint32_t payload_cap = 4096; // margin
    for (int i = 0; i < n; i++) {
        const XboxSaveFile *f = &title->files[i];
        payload_cap += 2 + (uint32_t)strlen(f->relative_path) + 4 + 32 + f->file_size;
    }

    payload = (uint8_t *)malloc(payload_cap);
    if (!payload) {
        debugPrint("bundle: malloc(payload=%u) fail\n", (unsigned)payload_cap);
        goto done;
    }

    // 3. Build payload: file table.
    uint32_t off = 0;
    for (int i = 0; i < n; i++) {
        const XboxSaveFile *f = &title->files[i];
        uint16_t path_len = (uint16_t)strlen(f->relative_path);

        write_le16(payload + off, path_len);
        off += 2;
        memcpy(payload + off, f->relative_path, path_len);
        off += path_len;
        write_le32(payload + off, f->file_size);
        off += 4;

        uint8_t fhash[32];
        bundle_hash_buffer(file_data[i], f->file_size, ZLIB_CHUNK, fhash);
        memcpy(payload + off, fhash, 32);
        off += 32;
    }
    // 4. Build payload: file data (concatenated in the same order).
    for (int i = 0; i < n; i++) {
        const XboxSaveFile *f = &title->files[i];
        if (f->file_size > 0) {
            memcpy(payload + off, file_data[i], f->file_size);
            off += f->file_size;
        }
    }
    uint32_t payload_size = off;

    // 5. Compress payload with zlib (level 6 - matches server side).
    uLongf csize = xbox_compress_bound((uLong)payload_size);
    compressed = (uint8_t *)malloc(csize);
    if (!compressed) {
        debugPrint("bundle: malloc(compressed=%lu) fail\n",
                   (unsigned long)csize);
        goto done;
    }
    if (compress_payload(payload, payload_size, compressed, &csize) != 0) {
        debugPrint("bundle: deflate fail (payload=%u)\n",
                   (unsigned)payload_size);
        goto done;
    }

    // 6. Assemble bundle: header + compressed payload.
    uint32_t bundle_size = BUNDLE_HEADER_SIZE_V5 + (uint32_t)csize;
    bundle = (uint8_t *)malloc(bundle_size);
    if (!bundle) {
        debugPrint("bundle: malloc(bundle=%u) fail\n", (unsigned)bundle_size);
        goto done;
    }

    memcpy(bundle, BUNDLE_MAGIC, 4);
    write_le32(bundle + 4, BUNDLE_VERSION_V5);

    // 64-byte ASCII title_id, null-padded.
    memset(bundle + 8, 0, 64);
    size_t tid_len = strlen(title->title_id);
    if (tid_len > 63) tid_len = 63;
    memcpy(bundle + 8, title->title_id, tid_len);

    write_le32(bundle + 72, timestamp);
    write_le32(bundle + 76, (uint32_t)n);
    write_le32(bundle + 80, payload_size);
    memcpy(bundle + BUNDLE_HEADER_SIZE_V5, compressed, csize);

    *out_data = bundle;
    *out_size = bundle_size;
    bundle = NULL;   // ownership transferred
    rc = 0;

done:
    free(payload);
    free(compressed);
    free(bundle);
    if (file_data) {
        for (int i = 0; i < n; i++) free(file_data[i]);
        free(file_data);
    }
    return rc;
}

static int prehash_title_files(const XboxSaveTitle *title,
                               uint8_t *file_hashes,
                               uint8_t save_hash[32])
{
    int rc = -1;
    SHA256_CTX save_ctx;
    sha256_init(&save_ctx);

    char fullpath[XBOX_PATH_MAX * 2];
    uint8_t *chunk = (uint8_t *)malloc(ZLIB_CHUNK);
    if (!chunk) {
        debugPrint("stream hash: malloc chunk fail\n");
        return -1;
    }

    for (int i = 0; i < title->file_count; i++) {
        const XboxSaveFile *f = &title->files[i];
        SHA256_CTX file_ctx;
        sha256_init(&file_ctx);

        if (f->file_size > 0) {
            snprintf(fullpath, sizeof(fullpath),
                     "E:\\UDATA\\%s\\%s",
                     title->title_id, f->relative_path);

            HANDLE h = CreateFileA(fullpath, GENERIC_READ, FILE_SHARE_READ, NULL,
                                   OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
            if (h == INVALID_HANDLE_VALUE) {
                debugPrint("stream hash: open fail %s\n", fullpath);
                goto done;
            }

            DWORD got = 0;
            BOOL ok;
            do {
                ok = ReadFile(h, chunk, ZLIB_CHUNK, &got, NULL);
                if (!ok) break;
                if (got > 0) {
                    sha256_update(&file_ctx, chunk, got);
                    sha256_update(&save_ctx, chunk, got);
                }
            } while (got > 0);
            CloseHandle(h);
            if (!ok) goto done;
        }

        sha256_final(&file_ctx, file_hashes + i * 32);
    }

    sha256_final(&save_ctx, save_hash);
    rc = 0;

done:
    free(chunk);
    return rc;
}

int bundle_stream_create(const XboxSaveTitle *title,
                         uint32_t timestamp,
                         BundleWriteFn write,
                         void *write_ctx,
                         char *save_hash_hex)
{
    if (!title || !write || title->file_count <= 0) {
        debugPrint("bundle stream: bad args\n");
        return -1;
    }

    int n = title->file_count;
    uint8_t *file_hashes = (uint8_t *)malloc((size_t)n * 32);
    if (!file_hashes) {
        debugPrint("bundle stream: malloc(file_hashes) fail\n");
        return -1;
    }

    uint8_t save_hash[32];
    if (prehash_title_files(title, file_hashes, save_hash) != 0) {
        free(file_hashes);
        return -1;
    }
    hash_to_hex(save_hash, save_hash_hex);

    uint32_t payload_size = 0;
    for (int i = 0; i < n; i++) {
        const XboxSaveFile *f = &title->files[i];
        uint32_t path_len = (uint32_t)strlen(f->relative_path);
        payload_size += 2 + path_len + 4 + 32 + f->file_size;
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
    if (!d) {
        debugPrint("bundle stream: malloc deflater fail\n");
        free(file_hashes);
        return -1;
    }
    d->zs.zalloc = zlib_alloc;
    d->zs.zfree  = zlib_free;
    d->zs.opaque = NULL;
    d->write = write;
    d->write_ctx = write_ctx;

    int deflater_ready = 0;
    if (deflateInit(&d->zs, 6) != Z_OK) {
        debugPrint("bundle stream: deflateInit fail\n");
        free(d);
        free(file_hashes);
        return -1;
    }
    deflater_ready = 1;

    int rc = -1;
    uint8_t *chunk = NULL;
    uint8_t entry[2 + XBOX_PATH_MAX + 4 + 32];
    for (int i = 0; i < n; i++) {
        const XboxSaveFile *f = &title->files[i];
        uint16_t path_len = (uint16_t)strlen(f->relative_path);
        uint32_t off = 0;

        if (path_len >= XBOX_PATH_MAX) goto done_stream;
        write_le16(entry + off, path_len);
        off += 2;
        memcpy(entry + off, f->relative_path, path_len);
        off += path_len;
        write_le32(entry + off, f->file_size);
        off += 4;
        memcpy(entry + off, file_hashes + i * 32, 32);
        off += 32;

        if (deflater_feed(d, entry, off) != 0) goto done_stream;
    }

    char fullpath[XBOX_PATH_MAX * 2];
    chunk = (uint8_t *)malloc(ZLIB_CHUNK);
    if (!chunk) {
        debugPrint("bundle stream: malloc chunk fail\n");
        goto done_stream;
    }

    for (int i = 0; i < n; i++) {
        const XboxSaveFile *f = &title->files[i];
        if (f->file_size == 0) continue;

        snprintf(fullpath, sizeof(fullpath),
                 "E:\\UDATA\\%s\\%s",
                 title->title_id, f->relative_path);
        HANDLE h = CreateFileA(fullpath, GENERIC_READ, FILE_SHARE_READ, NULL,
                               OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
        if (h == INVALID_HANDLE_VALUE) {
            debugPrint("bundle stream: open fail %s\n", fullpath);
            goto done_stream;
        }

        DWORD got = 0;
        BOOL ok;
        do {
            ok = ReadFile(h, chunk, ZLIB_CHUNK, &got, NULL);
            if (!ok) break;
            if (got > 0 && deflater_feed(d, chunk, got) != 0) {
                ok = FALSE;
                break;
            }
        } while (got > 0);
        CloseHandle(h);
        if (!ok) goto done_stream;
    }

    if (deflater_finish(d) != 0) goto done_stream;
    rc = 0;

done_stream:
    if (deflater_ready) deflateEnd(&d->zs);
    free(d);
    free(chunk);
    free(file_hashes);
    return rc;
}

// ---------------------------------------------------------------------------
// Save-hash (concatenated file content; matches server semantics)
// ---------------------------------------------------------------------------

int bundle_compute_save_hash(const XboxSaveTitle *title,
                             uint8_t hash[32],
                             char *hash_hex)
{
    if (!title || !hash) return -1;

    SHA256_CTX ctx;
    sha256_init(&ctx);

    char fullpath[XBOX_PATH_MAX * 2];
    uint8_t chunk[ZLIB_CHUNK];

    for (int i = 0; i < title->file_count; i++) {
        const XboxSaveFile *f = &title->files[i];
        snprintf(fullpath, sizeof(fullpath),
                 "E:\\UDATA\\%s\\%s",
                 title->title_id, f->relative_path);

        if (f->file_size == 0) continue;

        HANDLE h = CreateFileA(fullpath, GENERIC_READ, FILE_SHARE_READ, NULL,
                               OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
        if (h == INVALID_HANDLE_VALUE) {
            debugPrint("hash: open fail %s\n", fullpath);
            return -1;
        }
        DWORD got = 0;
        BOOL ok;
        do {
            ok = ReadFile(h, chunk, sizeof(chunk), &got, NULL);
            if (!ok) break;
            if (got > 0) sha256_update(&ctx, chunk, got);
        } while (got > 0);
        CloseHandle(h);
        if (!ok) return -1;
    }

    sha256_final(&ctx, hash);

    if (hash_hex) {
        hash_to_hex(hash, hash_hex);
    }
    return 0;
}

// ---------------------------------------------------------------------------
// zlib inflate (chunked) - mirrors compress_payload above
// ---------------------------------------------------------------------------

static int decompress_payload(const uint8_t *src, uint32_t src_size,
                              uint8_t *dst, uint32_t dst_size)
{
    z_stream zs;
    memset(&zs, 0, sizeof(zs));
    zs.zalloc = zlib_alloc;
    zs.zfree  = zlib_free;
    zs.opaque = NULL;

    if (inflateInit(&zs) != Z_OK) return -1;

    zs.next_in   = (Bytef *)src;
    zs.avail_in  = 0;
    zs.next_out  = dst;
    zs.avail_out = 0;

    for (;;) {
        if (zs.avail_in == 0 && zs.total_in < src_size) {
            uInt c = (uInt)(src_size - (uint32_t)zs.total_in);
            if (c > ZLIB_CHUNK) c = ZLIB_CHUNK;
            zs.next_in  = (Bytef *)(src + zs.total_in);
            zs.avail_in = c;
        }
        if (zs.avail_out == 0) {
            uInt c = (uInt)(dst_size - (uint32_t)zs.total_out);
            if (c == 0) {
                inflateEnd(&zs);
                return -1;
            }
            if (c > ZLIB_CHUNK) c = ZLIB_CHUNK;
            zs.next_out  = dst + zs.total_out;
            zs.avail_out = c;
        }

        int zr = inflate(&zs, Z_NO_FLUSH);
        if (zr == Z_STREAM_END) {
            int ok = (zs.total_out == dst_size);
            inflateEnd(&zs);
            return ok ? 0 : -1;
        }
        if (zr != Z_OK) {
            debugPrint("bundle: inflate zr=%d\n", zr);
            inflateEnd(&zs);
            return -1;
        }
    }
}

// ---------------------------------------------------------------------------
// Bundle parser
// ---------------------------------------------------------------------------

int bundle_parse(const uint8_t *data, uint32_t size, ParsedBundle *out)
{
    if (!data || !out) return -1;
    memset(out, 0, sizeof(*out));

    if (size < 8) return -1;
    if (memcmp(data, BUNDLE_MAGIC, 4) != 0) {
        debugPrint("bundle: bad magic\n");
        return -1;
    }
    uint32_t version = read_le32(data + 4);

    int      tid_field_len;
    uint32_t off;
    if (version == BUNDLE_VERSION_V5) {
        tid_field_len = 64;
        off = 8 + 64;
    } else if (version == 4) {
        tid_field_len = 32;
        off = 8 + 32;
    } else {
        debugPrint("bundle: ver=%u unsupported\n", (unsigned)version);
        return -1;
    }

    if (size < off + 12) return -1;

    // ASCII title_id; trim NULs.
    int copy = tid_field_len < (int)sizeof(out->title_id) - 1
                   ? tid_field_len
                   : (int)sizeof(out->title_id) - 1;
    memcpy(out->title_id, data + 8, copy);
    out->title_id[copy] = '\0';
    for (int i = (int)strlen(out->title_id) - 1; i >= 0; i--) {
        if (out->title_id[i] == '\0' || out->title_id[i] == ' ') {
            out->title_id[i] = '\0';
        } else {
            break;
        }
    }
    int actual_len = (int)strlen(out->title_id);
    (void)actual_len;

    out->timestamp = read_le32(data + off); off += 4;
    int     file_count        = (int)read_le32(data + off); off += 4;
    uint32_t uncompressed_size = read_le32(data + off);     off += 4;

    if (file_count <= 0 || file_count > 4096) {
        debugPrint("bundle: file_count=%d\n", file_count);
        return -1;
    }
    if (size < off) return -1;

    // Inflate payload.
    uint8_t *payload = (uint8_t *)malloc(uncompressed_size);
    if (!payload) return -1;

    if (decompress_payload(data + off, size - off, payload, uncompressed_size) != 0) {
        free(payload);
        return -1;
    }

    // Walk file table.
    out->files = (ParsedBundleFile *)calloc((size_t)file_count,
                                            sizeof(ParsedBundleFile));
    if (!out->files) { free(payload); return -1; }
    out->file_count = file_count;

    uint32_t p = 0;
    for (int i = 0; i < file_count; i++) {
        if (p + 2 > uncompressed_size) goto bad;
        uint16_t plen = read_le16(payload + p); p += 2;
        if (p + plen > uncompressed_size) goto bad;
        if (plen >= sizeof(out->files[i].relative_path)) goto bad;
        memcpy(out->files[i].relative_path, payload + p, plen);
        out->files[i].relative_path[plen] = '\0';
        p += plen;

        if (p + 4 > uncompressed_size) goto bad;
        out->files[i].size = read_le32(payload + p); p += 4;

        if (p + 32 > uncompressed_size) goto bad;
        memcpy(out->files[i].sha256, payload + p, 32); p += 32;
    }
    // File data follows.
    for (int i = 0; i < file_count; i++) {
        uint32_t fs = out->files[i].size;
        if (p + fs > uncompressed_size) goto bad;
        if (fs > 0) {
            out->files[i].data = (uint8_t *)malloc(fs);
            if (!out->files[i].data) goto bad;
            memcpy(out->files[i].data, payload + p, fs);
        }
        p += fs;
    }

    free(payload);
    return 0;

bad:
    debugPrint("bundle: parse short at p=%u/%u\n",
               (unsigned)p, (unsigned)uncompressed_size);
    free(payload);
    bundle_parsed_free(out);
    return -1;
}

void bundle_parsed_free(ParsedBundle *pb)
{
    if (!pb) return;
    if (pb->files) {
        for (int i = 0; i < pb->file_count; i++) {
            free(pb->files[i].data);
        }
        free(pb->files);
    }
    memset(pb, 0, sizeof(*pb));
}

// ---------------------------------------------------------------------------
// Apply parsed bundle to disk
// ---------------------------------------------------------------------------

// Single-level mkdir tolerating "already exists".
static int mkdir_one(const char *path)
{
    if (CreateDirectoryA(path, NULL)) return 0;
    DWORD err = GetLastError();
    if (err == ERROR_ALREADY_EXISTS) return 0;
    debugPrint("apply: mkdir %s err=%lu\n", path, (unsigned long)err);
    return -1;
}

// Make every parent directory along ``path`` (which is a file path, not a
// directory). Skips the drive prefix and the final filename component.
static int make_parents(const char *path)
{
    char tmp[XBOX_PATH_MAX * 2];
    int  len = (int)strlen(path);
    if (len <= 0 || len >= (int)sizeof(tmp)) return -1;
    memcpy(tmp, path, len + 1);

    // Skip drive prefix if any.
    int start = 0;
    if (len >= 3 && tmp[1] == ':' && (tmp[2] == '\\' || tmp[2] == '/')) {
        start = 3;
    }
    // Strip the filename - find last separator.
    int last_sep = -1;
    for (int i = len - 1; i >= start; i--) {
        if (tmp[i] == '\\' || tmp[i] == '/') { last_sep = i; break; }
    }
    if (last_sep < 0) return 0;

    for (int i = start; i <= last_sep; i++) {
        if (tmp[i] == '\\' || tmp[i] == '/') {
            char saved = tmp[i];
            tmp[i] = '\0';
            if (i > start) {
                if (mkdir_one(tmp) != 0) return -1;
            }
            tmp[i] = saved;
        }
    }
    return 0;
}

static int read_exact(HANDLE h, void *buf, DWORD size)
{
    uint8_t *p = (uint8_t *)buf;
    DWORD off = 0;
    while (off < size) {
        DWORD got = 0;
        if (!ReadFile(h, p + off, size - off, &got, NULL) || got == 0) {
            return -1;
        }
        off += got;
    }
    return 0;
}

static int write_exact(HANDLE h, const void *buf, DWORD size)
{
    const uint8_t *p = (const uint8_t *)buf;
    DWORD off = 0;
    while (off < size) {
        DWORD wrote = 0;
        if (!WriteFile(h, p + off, size - off, &wrote, NULL) || wrote == 0) {
            return -1;
        }
        off += wrote;
    }
    return 0;
}

static int bundle_header_read_file(HANDLE h,
                                   uint32_t *out_version,
                                   uint32_t *out_file_count,
                                   uint32_t *out_payload_size)
{
    uint8_t first[8];
    if (read_exact(h, first, sizeof(first)) != 0) return -1;
    if (memcmp(first, BUNDLE_MAGIC, 4) != 0) return -1;
    uint32_t version = read_le32(first + 4);

    DWORD skip = 0;
    if (version == BUNDLE_VERSION_V5) {
        skip = 64;
    } else if (version == 4) {
        skip = 32;
    } else if (version == 3) {
        skip = 16;
    } else if (version == 2 || version == 1) {
        skip = 8;
    } else {
        return -1;
    }

    uint8_t tmp[64];
    if (skip > sizeof(tmp)) return -1;
    if (read_exact(h, tmp, skip) != 0) return -1;

    uint8_t tail[12];
    if (read_exact(h, tail, sizeof(tail)) != 0) return -1;
    if (out_version) *out_version = version;
    if (out_file_count) *out_file_count = read_le32(tail + 4);
    if (out_payload_size) *out_payload_size = read_le32(tail + 8);
    return 0;
}

static int inflate_bundle_payload_to_file(HANDLE in,
                                          HANDLE out,
                                          uint32_t expected_size)
{
    z_stream zs;
    memset(&zs, 0, sizeof(zs));
    zs.zalloc = zlib_alloc;
    zs.zfree  = zlib_free;
    zs.opaque = NULL;
    if (inflateInit(&zs) != Z_OK) return -1;

    uint8_t *inbuf = (uint8_t *)malloc(ZLIB_CHUNK);
    uint8_t *outbuf = (uint8_t *)malloc(ZLIB_CHUNK);
    if (!inbuf || !outbuf) {
        free(inbuf);
        free(outbuf);
        inflateEnd(&zs);
        return -1;
    }

    int rc = -1;
    int done = 0;
    uint32_t total_out = 0;
    while (!done) {
        DWORD got = 0;
        if (!ReadFile(in, inbuf, ZLIB_CHUNK, &got, NULL)) goto finish;
        if (got == 0) break;
        zs.next_in = inbuf;
        zs.avail_in = got;

        while (zs.avail_in > 0) {
            zs.next_out = outbuf;
            zs.avail_out = ZLIB_CHUNK;
            int zr = inflate(&zs, Z_NO_FLUSH);
            if (zr != Z_OK && zr != Z_STREAM_END) goto finish;
            DWORD have = ZLIB_CHUNK - zs.avail_out;
            if (have > 0) {
                if (write_exact(out, outbuf, have) != 0) goto finish;
                total_out += have;
            }
            if (zr == Z_STREAM_END) {
                done = 1;
                break;
            }
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

typedef struct {
    char relative_path[XBOX_PATH_MAX];
    uint32_t size;
    uint8_t sha256[32];
} StreamBundleFile;

int bundle_apply_file_to_disk(const char *bundle_path, const char *udata_title_id)
{
    if (!bundle_path || !udata_title_id || !udata_title_id[0]) return -1;

    HANDLE in = CreateFileA(bundle_path, GENERIC_READ, FILE_SHARE_READ, NULL,
                            OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (in == INVALID_HANDLE_VALUE) {
        debugPrint("apply-file: open bundle err=%lu\n",
                   (unsigned long)GetLastError());
        return -1;
    }

    uint32_t version = 0;
    uint32_t file_count = 0;
    uint32_t payload_size = 0;
    if (bundle_header_read_file(in, &version, &file_count, &payload_size) != 0 ||
        file_count == 0 ||
        file_count > XBOX_MAX_FILES_PER_TITLE) {
        debugPrint("apply-file: bad bundle header\n");
        CloseHandle(in);
        return -1;
    }
    if (version == 1) {
        debugPrint("apply-file: uncompressed v1 bundle not supported here\n");
        CloseHandle(in);
        return -1;
    }

    mkdir_one("E:\\UDATA\\TDSV0000");
    const char *payload_path = "E:\\UDATA\\TDSV0000\\download_payload.tmp";
    DeleteFileA(payload_path);
    HANDLE payload = CreateFileA(payload_path,
                                 GENERIC_READ | GENERIC_WRITE,
                                 0, NULL, CREATE_ALWAYS,
                                 FILE_ATTRIBUTE_NORMAL, NULL);
    if (payload == INVALID_HANDLE_VALUE) {
        debugPrint("apply-file: payload open err=%lu\n",
                   (unsigned long)GetLastError());
        CloseHandle(in);
        return -1;
    }

    int rc = inflate_bundle_payload_to_file(in, payload, payload_size);
    CloseHandle(in);
    if (rc != 0) {
        debugPrint("apply-file: inflate failed\n");
        CloseHandle(payload);
        DeleteFileA(payload_path);
        return -1;
    }
    SetFilePointer(payload, 0, NULL, FILE_BEGIN);

    StreamBundleFile *files = (StreamBundleFile *)calloc(
        file_count, sizeof(StreamBundleFile));
    if (!files) {
        CloseHandle(payload);
        DeleteFileA(payload_path);
        return -1;
    }

    rc = -1;
    for (uint32_t i = 0; i < file_count; i++) {
        uint8_t lenbuf[2];
        if (read_exact(payload, lenbuf, sizeof(lenbuf)) != 0) goto done;
        uint16_t plen = read_le16(lenbuf);
        if (plen == 0 || plen >= XBOX_PATH_MAX) goto done;
        if (read_exact(payload, files[i].relative_path, plen) != 0) goto done;
        files[i].relative_path[plen] = '\0';

        uint8_t sizebuf[4];
        if (read_exact(payload, sizebuf, sizeof(sizebuf)) != 0) goto done;
        files[i].size = read_le32(sizebuf);
        if (read_exact(payload, files[i].sha256, 32) != 0) goto done;
    }

    char title_root[XBOX_PATH_MAX];
    snprintf(title_root, sizeof(title_root),
             "E:\\UDATA\\%s", udata_title_id);
    mkdir_one(title_root);

    uint8_t *chunk = (uint8_t *)malloc(ZLIB_CHUNK);
    if (!chunk) goto done;

    for (uint32_t i = 0; i < file_count; i++) {
        StreamBundleFile *f = &files[i];
        char fullpath[XBOX_PATH_MAX * 2];
        snprintf(fullpath, sizeof(fullpath),
                 "%s\\%s", title_root, f->relative_path);

        if (make_parents(fullpath) != 0) {
            debugPrint("apply-file: make_parents %s failed\n", fullpath);
            free(chunk);
            goto done;
        }

        DWORD attrs = GetFileAttributesA(fullpath);
        if (attrs != INVALID_FILE_ATTRIBUTES &&
            (attrs & (FILE_ATTRIBUTE_READONLY |
                      FILE_ATTRIBUTE_HIDDEN |
                      FILE_ATTRIBUTE_SYSTEM))) {
            SetFileAttributesA(fullpath, FILE_ATTRIBUTE_NORMAL);
        }

        HANDLE out = CreateFileA(fullpath,
                                 GENERIC_READ | GENERIC_WRITE,
                                 FILE_SHARE_READ | FILE_SHARE_WRITE,
                                 NULL,
                                 OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
        if (out == INVALID_HANDLE_VALUE) {
            debugPrint("apply-file: open %s err=%lu\n",
                       fullpath, (unsigned long)GetLastError());
            free(chunk);
            goto done;
        }
        SetFilePointer(out, 0, NULL, FILE_BEGIN);
        if (!SetEndOfFile(out)) {
            debugPrint("apply-file: trunc %s err=%lu\n",
                       fullpath, (unsigned long)GetLastError());
            CloseHandle(out);
            free(chunk);
            goto done;
        }

        SHA256_CTX hash_ctx;
        sha256_init(&hash_ctx);
        uint32_t remaining = f->size;
        while (remaining > 0) {
            DWORD want = remaining > ZLIB_CHUNK ? ZLIB_CHUNK : remaining;
            if (read_exact(payload, chunk, want) != 0) {
                CloseHandle(out);
                free(chunk);
                goto done;
            }
            if (write_exact(out, chunk, want) != 0) {
                debugPrint("apply-file: write %s err=%lu\n",
                           fullpath, (unsigned long)GetLastError());
                CloseHandle(out);
                free(chunk);
                goto done;
            }
            sha256_update(&hash_ctx, chunk, want);
            remaining -= want;
        }
        CloseHandle(out);

        uint8_t got_hash[32];
        sha256_final(&hash_ctx, got_hash);
        if (memcmp(got_hash, f->sha256, 32) != 0) {
            debugPrint("apply-file: sha mismatch %s\n", f->relative_path);
            free(chunk);
            goto done;
        }
    }

    free(chunk);
    rc = 0;

done:
    free(files);
    CloseHandle(payload);
    DeleteFileA(payload_path);
    return rc;
}

int bundle_apply_to_disk(const ParsedBundle *pb, const char *udata_title_id)
{
    if (!pb || !udata_title_id || !udata_title_id[0]) return -1;

    char title_root[XBOX_PATH_MAX];
    snprintf(title_root, sizeof(title_root),
             "E:\\UDATA\\%s", udata_title_id);
    mkdir_one(title_root);  // OK if exists

    for (int i = 0; i < pb->file_count; i++) {
        const ParsedBundleFile *f = &pb->files[i];

        // Verify SHA before touching disk.
        if (f->size > 0) {
            uint8_t got[32];
            sha256(f->data, f->size, got);
            if (memcmp(got, f->sha256, 32) != 0) {
                debugPrint("apply: sha mismatch %s\n", f->relative_path);
                debugPrint("\n*** download stopped - reading error in 10s ***\n");
                Sleep(10000);
                return -1;
            }
        }

        char fullpath[XBOX_PATH_MAX * 2];
        snprintf(fullpath, sizeof(fullpath),
                 "%s\\%s", title_root, f->relative_path);

        if (make_parents(fullpath) != 0) {
            debugPrint("apply: make_parents %s failed\n", fullpath);
            debugPrint("\n*** download stopped - reading error in 10s ***\n");
            Sleep(10000);
            return -1;
        }

        // FATX's CreateFile semantics differ from NTFS: CREATE_ALWAYS hits
        // ERROR_ACCESS_DENIED on existing files, and CREATE_NEW after a
        // DeleteFile sometimes lands in ERROR_NOT_ENOUGH_MEMORY. The
        // robust path is OPEN_ALWAYS (create-or-open) followed by an
        // explicit truncate to zero, then write the new content.
        // Xbox saves often have FILE_ATTRIBUTE_READONLY (or hidden/system)
        // set by the kernel; clear those before opening for write so
        // OPEN_ALWAYS doesn't bounce with ERROR_ACCESS_DENIED.
        DWORD attrs = GetFileAttributesA(fullpath);
        if (attrs != INVALID_FILE_ATTRIBUTES &&
            (attrs & (FILE_ATTRIBUTE_READONLY |
                      FILE_ATTRIBUTE_HIDDEN |
                      FILE_ATTRIBUTE_SYSTEM))) {
            SetFileAttributesA(fullpath, FILE_ATTRIBUTE_NORMAL);
        }

        HANDLE h = CreateFileA(fullpath,
                               GENERIC_READ | GENERIC_WRITE,
                               FILE_SHARE_READ | FILE_SHARE_WRITE,
                               NULL,
                               OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
        if (h == INVALID_HANDLE_VALUE) {
            DWORD oerr = GetLastError();
            debugPrint("apply: open %s err=%lu\n",
                       fullpath, (unsigned long)oerr);
            debugPrint("\n*** download stopped - reading error in 10s ***\n");
            Sleep(10000);
            return -1;
        }
        // Move to start, then truncate.
        SetFilePointer(h, 0, NULL, FILE_BEGIN);
        if (!SetEndOfFile(h)) {
            DWORD terr = GetLastError();
            debugPrint("apply: trunc %s err=%lu\n",
                       fullpath, (unsigned long)terr);
            debugPrint("\n*** download stopped - reading error in 10s ***\n");
            Sleep(10000);
            CloseHandle(h);
            return -1;
        }
        DWORD off = 0;
        while (off < f->size) {
            DWORD want = f->size - off;
            if (want > ZLIB_CHUNK) want = ZLIB_CHUNK;
            DWORD wrote = 0;
            BOOL ok = WriteFile(h, f->data + off, want, &wrote, NULL);
            if (!ok || wrote == 0) {
                DWORD werr = GetLastError();
                CloseHandle(h);
                debugPrint("apply: write %s err=%lu off=%u\n",
                           fullpath, (unsigned long)werr, (unsigned)off);
                debugPrint("\n*** download stopped - reading error in 10s ***\n");
                Sleep(10000);
                return -1;
            }
            off += wrote;
        }
        // FATX caches aggressively, but nxdk's CloseHandle path issues an
        // implicit flush via NtClose, so we don't need an explicit
        // FlushFileBuffers (which isn't part of nxdk's winapi shim).
        CloseHandle(h);
    }
    return 0;
}
