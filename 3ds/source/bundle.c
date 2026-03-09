#include "bundle.h"
#include "sha256.h"
#include <zlib.h>

// Write a u16 little-endian
static void write_u16_le(u8 *buf, u16 val) {
    buf[0] = (u8)(val);
    buf[1] = (u8)(val >> 8);
}

// Write a u32 little-endian
static void write_u32_le(u8 *buf, u32 val) {
    buf[0] = (u8)(val);
    buf[1] = (u8)(val >> 8);
    buf[2] = (u8)(val >> 16);
    buf[3] = (u8)(val >> 24);
}

// Write a u64 big-endian
static void write_u64_be(u8 *buf, u64 val) {
    buf[0] = (u8)(val >> 56);
    buf[1] = (u8)(val >> 48);
    buf[2] = (u8)(val >> 40);
    buf[3] = (u8)(val >> 32);
    buf[4] = (u8)(val >> 24);
    buf[5] = (u8)(val >> 16);
    buf[6] = (u8)(val >> 8);
    buf[7] = (u8)(val);
}

// Read a u16 little-endian
static u16 read_u16_le(const u8 *buf) {
    return (u16)buf[0] | ((u16)buf[1] << 8);
}

// Read a u32 little-endian
static u32 read_u32_le(const u8 *buf) {
    return (u32)buf[0] | ((u32)buf[1] << 8) |
           ((u32)buf[2] << 16) | ((u32)buf[3] << 24);
}

// Read a u64 big-endian
static u64 read_u64_be(const u8 *buf) {
    return ((u64)buf[0] << 56) | ((u64)buf[1] << 48) |
           ((u64)buf[2] << 40) | ((u64)buf[3] << 32) |
           ((u64)buf[4] << 24) | ((u64)buf[5] << 16) |
           ((u64)buf[6] << 8)  | ((u64)buf[7]);
}

// Parse file table and data from payload buffer
// Returns file count on success, -1 on error
static int parse_payload(const u8 *payload, u32 payload_size,
                         u32 file_count, ArchiveFile *files, int max_files) {
    if ((int)file_count > max_files) return -1;

    u32 offset = 0;

    // File table
    for (u32 i = 0; i < file_count; i++) {
        if (offset + 2 > payload_size) return -1;
        u16 path_len = read_u16_le(payload + offset); offset += 2;

        if (offset + path_len > payload_size) return -1;
        if (path_len >= MAX_PATH_LEN) return -1;
        memcpy(files[i].path, payload + offset, path_len);
        files[i].path[path_len] = '\0';
        offset += path_len;

        if (offset + 4 > payload_size) return -1;
        files[i].size = read_u32_le(payload + offset); offset += 4;

        // Skip hash (we verify on server side)
        if (offset + 32 > payload_size) return -1;
        offset += 32;
    }

    // File data - point into the payload buffer
    for (u32 i = 0; i < file_count; i++) {
        if (offset + files[i].size > payload_size) return -1;
        files[i].data = (u8 *)(payload + offset);
        offset += files[i].size;
    }

    return (int)file_count;
}

u8 *bundle_create(u64 title_id, u32 timestamp,
                  const ArchiveFile *files, int file_count,
                  u32 *out_size) {
    // Calculate payload size (file table + file data)
    u32 total_data = 0;
    u32 table_size = 0;
    for (int i = 0; i < file_count; i++) {
        u16 path_len = (u16)strlen(files[i].path);
        table_size += 2 + path_len + 4 + 32; // path_len + path + size + sha256
        total_data += files[i].size;
    }
    u32 payload_size = table_size + total_data;

    // Build uncompressed payload
    u8 *payload = (u8 *)malloc(payload_size);
    if (!payload) return NULL;

    u32 offset = 0;

    // File table
    for (int i = 0; i < file_count; i++) {
        u16 path_len = (u16)strlen(files[i].path);
        write_u16_le(payload + offset, path_len); offset += 2;
        memcpy(payload + offset, files[i].path, path_len); offset += path_len;
        write_u32_le(payload + offset, files[i].size); offset += 4;

        // SHA-256 of file data
        u8 hash[32];
        sha256(files[i].data, files[i].size, hash);
        memcpy(payload + offset, hash, 32); offset += 32;
    }

    // File data
    for (int i = 0; i < file_count; i++) {
        memcpy(payload + offset, files[i].data, files[i].size);
        offset += files[i].size;
    }

    // Compress payload
    uLongf compressed_size = compressBound(payload_size);
    u8 *compressed = (u8 *)malloc(compressed_size);
    if (!compressed) {
        free(payload);
        return NULL;
    }

    int zret = compress2(compressed, &compressed_size, payload, payload_size, 6);
    free(payload);

    if (zret != Z_OK) {
        free(compressed);
        return NULL;
    }

    // Build final bundle: header + compressed payload
    u32 header_size = 4 + 4 + 8 + 4 + 4 + 4; // magic + ver + tid + ts + count + uncompressed_size
    u32 bundle_size = header_size + (u32)compressed_size;

    u8 *buf = (u8 *)malloc(bundle_size);
    if (!buf) {
        free(compressed);
        return NULL;
    }

    offset = 0;

    // Header (v2 compressed)
    memcpy(buf + offset, BUNDLE_MAGIC, 4); offset += 4;
    write_u32_le(buf + offset, BUNDLE_VERSION_COMPRESSED); offset += 4;
    write_u64_be(buf + offset, title_id); offset += 8;
    write_u32_le(buf + offset, timestamp); offset += 4;
    write_u32_le(buf + offset, (u32)file_count); offset += 4;
    write_u32_le(buf + offset, payload_size); offset += 4;  // uncompressed size

    // Compressed payload
    memcpy(buf + offset, compressed, compressed_size);
    free(compressed);

    *out_size = bundle_size;
    return buf;
}

int bundle_parse(const u8 *data, u32 data_size,
                 u64 *out_title_id, u32 *out_timestamp,
                 ArchiveFile *files, int max_files,
                 u8 **out_decompressed) {
    *out_decompressed = NULL;

    if (data_size < 28) return -1;

    u32 offset = 0;

    // Verify magic
    if (memcmp(data + offset, BUNDLE_MAGIC, 4) != 0) return -1;
    offset += 4;

    u32 version = read_u32_le(data + offset); offset += 4;
    if (version != BUNDLE_VERSION && version != BUNDLE_VERSION_COMPRESSED)
        return -1;

    *out_title_id = read_u64_be(data + offset); offset += 8;
    *out_timestamp = read_u32_le(data + offset); offset += 4;

    u32 file_count = read_u32_le(data + offset); offset += 4;
    u32 size_field = read_u32_le(data + offset); offset += 4;

    const u8 *payload;
    u32 payload_size;

    if (version == BUNDLE_VERSION_COMPRESSED) {
        // v2: decompress payload
        u32 uncompressed_size = size_field;
        u8 *decompressed = (u8 *)malloc(uncompressed_size);
        if (!decompressed) return -1;

        uLongf dest_len = uncompressed_size;
        int zret = uncompress(decompressed, &dest_len,
                              data + offset, data_size - offset);
        if (zret != Z_OK || dest_len != uncompressed_size) {
            free(decompressed);
            return -1;
        }

        *out_decompressed = decompressed;
        payload = decompressed;
        payload_size = uncompressed_size;
    } else {
        // v1: payload is uncompressed, in-place
        payload = data + offset;
        payload_size = data_size - offset;
    }

    return parse_payload(payload, payload_size, file_count, files, max_files);
}

void bundle_compute_save_hash(const ArchiveFile *files, int file_count,
                              char *hex_out) {
    SHA256_CTX ctx;
    sha256_init(&ctx);

    for (int i = 0; i < file_count; i++) {
        sha256_update(&ctx, files[i].data, files[i].size);
    }

    u8 hash[32];
    sha256_final(&ctx, hash);

    for (int i = 0; i < 32; i++) {
        snprintf(hex_out + i * 2, 3, "%02x", hash[i]);
    }
    hex_out[64] = '\0';
}
