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

#define ZLIB_CHUNK 32768U

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
        static const char H[] = "0123456789abcdef";
        for (int i = 0; i < 32; i++) {
            hash_hex[i * 2]     = H[(hash[i] >> 4) & 0xF];
            hash_hex[i * 2 + 1] = H[hash[i] & 0xF];
        }
        hash_hex[64] = '\0';
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
