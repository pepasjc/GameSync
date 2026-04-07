/*
 * PS3 Save Sync - Bundle v4 format
 *
 * Creates and parses 3DSS v4 bundles (identical to Vita client format).
 * Requires zlib: link with -lz, include path in $(PSL1GHT)/portlibs/ppu/include.
 */

#include "bundle.h"
#include "saves.h"
#include "sha256.h"
#include "debug.h"
#include "ui.h"

#include <zlib.h>
#include <time.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

#define MAX_PAYLOAD  (8 * 1024 * 1024)   /* 8 MB uncompressed max */
#define ZLIB_CHUNK   32768U

/* ---- LE read/write helpers ---- */

static uint32_t read_le32(const uint8_t *p) {
    return (uint32_t)p[0]
         | ((uint32_t)p[1] <<  8)
         | ((uint32_t)p[2] << 16)
         | ((uint32_t)p[3] << 24);
}

static void write_le32(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)(v);
    p[1] = (uint8_t)(v >>  8);
    p[2] = (uint8_t)(v >> 16);
    p[3] = (uint8_t)(v >> 24);
}

static uint16_t read_le16(const uint8_t *p) {
    return (uint16_t)p[0] | ((uint16_t)p[1] << 8);
}

static void write_le16(uint8_t *p, uint16_t v) {
    p[0] = (uint8_t)(v);
    p[1] = (uint8_t)(v >> 8);
}

static int compress_payload(const uint8_t *payload, uint32_t payload_size,
                            uint8_t *compressed, uLongf *compressed_size) {
    z_stream zs;
    int zr;

    memset(&zs, 0, sizeof(zs));
    if (deflateInit(&zs, 6) != Z_OK) {
        return -1;
    }

    zs.next_in = (Bytef *)payload;
    zs.avail_in = 0;
    zs.next_out = compressed;
    zs.avail_out = 0;

    while (1) {
        int flush;

        if (zs.avail_in == 0 && zs.total_in < payload_size) {
            uInt chunk = (uInt)(payload_size - (uint32_t)zs.total_in);
            if (chunk > ZLIB_CHUNK) chunk = ZLIB_CHUNK;
            zs.next_in = (Bytef *)(payload + zs.total_in);
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
            zs.next_out = compressed + zs.total_out;
            zs.avail_out = chunk;
        }

        flush = (zs.total_in >= payload_size) ? Z_FINISH : Z_NO_FLUSH;
        zr = deflate(&zs, flush);
        pump_callbacks();

        if (zr == Z_STREAM_END) {
            *compressed_size = zs.total_out;
            deflateEnd(&zs);
            return 0;
        }
        if (zr != Z_OK) {
            deflateEnd(&zs);
            return -1;
        }
    }
}

static int uncompress_payload(const uint8_t *data, uint32_t size,
                              uint8_t *payload, uint32_t payload_size,
                              uLongf *actual_size) {
    z_stream zs;
    int zr;

    memset(&zs, 0, sizeof(zs));
    if (inflateInit(&zs) != Z_OK) {
        return -1;
    }

    zs.next_in = (Bytef *)data;
    zs.avail_in = 0;
    zs.next_out = payload;
    zs.avail_out = 0;

    while (1) {
        if (zs.avail_in == 0 && zs.total_in < size) {
            uInt chunk = (uInt)(size - (uint32_t)zs.total_in);
            if (chunk > ZLIB_CHUNK) chunk = ZLIB_CHUNK;
            zs.next_in = (Bytef *)(data + zs.total_in);
            zs.avail_in = chunk;
        }
        if (zs.avail_out == 0 && zs.total_out < payload_size) {
            uInt chunk = (uInt)(payload_size - (uint32_t)zs.total_out);
            if (chunk > ZLIB_CHUNK) chunk = ZLIB_CHUNK;
            zs.next_out = payload + zs.total_out;
            zs.avail_out = chunk;
        }

        zr = inflate(&zs, Z_NO_FLUSH);
        pump_callbacks();

        if (zr == Z_STREAM_END) {
            *actual_size = zs.total_out;
            inflateEnd(&zs);
            return 0;
        }
        if (zr != Z_OK) {
            inflateEnd(&zs);
            return -1;
        }
        if (zs.total_out >= payload_size && zr != Z_STREAM_END) {
            inflateEnd(&zs);
            return -1;
        }
    }
}

static void sha256_buffer_pumped(const uint8_t *data, uint32_t size, uint8_t hash[32]) {
    SHA256_CTX ctx;
    uint32_t off = 0;

    sha256_init(&ctx);
    while (off < size) {
        uint32_t chunk = size - off;
        if (chunk > ZLIB_CHUNK) chunk = ZLIB_CHUNK;
        sha256_update(&ctx, data + off, chunk);
        off += chunk;
        pump_callbacks();
    }
    sha256_final(&ctx, hash);
}

/* ---- bundle_create ---- */

int bundle_create(const TitleInfo *title, uint8_t **out_data, uint32_t *out_size) {
    char     file_names[MAX_FILES][MAX_FILE_LEN];
    uint32_t file_sizes[MAX_FILES];

    int n = saves_list_files(title, file_names, file_sizes, MAX_FILES);
    if (n <= 0) {
        debug_log("bundle: no files found for %s", title->game_code);
        return -1;
    }

    /* ---- Read file data ---- */
    ui_status("Reading save files: %s", title->game_code);
    uint8_t **file_data = (uint8_t **)malloc((size_t)n * sizeof(uint8_t *));
    if (!file_data) return -1;

    for (int i = 0; i < n; i++) {
        file_data[i] = NULL;
        if (file_sizes[i] == 0) {
            file_data[i] = (uint8_t *)malloc(1);  /* empty file sentinel */
            continue;
        }
        file_data[i] = (uint8_t *)malloc(file_sizes[i]);
        if (!file_data[i]) goto fail_data;
        int r = saves_read_file(title, file_names[i], file_data[i], file_sizes[i]);
        if (r < 0) {
            debug_log("bundle: read_file %s failed", file_names[i]);
            goto fail_data;
        }
        file_sizes[i] = (uint32_t)r;
        pump_callbacks();
    }

    /* ---- Build uncompressed payload ---- */
    /* Size estimate: file table + data */
    uint32_t payload_cap = 0;
    for (int i = 0; i < n; i++)
        payload_cap += 2 + (uint32_t)strlen(file_names[i]) + 4 + 32 + file_sizes[i];
    payload_cap += 4096;  /* margin */

    uint8_t *payload = (uint8_t *)malloc(payload_cap);
    if (!payload) goto fail_data;

    uint32_t off = 0;

    /* File table */
    ui_status("Hashing bundle files: %s", title->game_code);
    for (int i = 0; i < n; i++) {
        uint16_t path_len = (uint16_t)strlen(file_names[i]);
        write_le16(payload + off, path_len); off += 2;
        memcpy(payload + off, file_names[i], path_len); off += path_len;
        write_le32(payload + off, file_sizes[i]); off += 4;
        uint8_t fhash[32];
        sha256_buffer_pumped(file_data[i], file_sizes[i], fhash);
        memcpy(payload + off, fhash, 32); off += 32;
    }
    /* File data */
    for (int i = 0; i < n; i++) {
        memcpy(payload + off, file_data[i], file_sizes[i]);
        off += file_sizes[i];
    }
    uint32_t payload_size = off;

    /* ---- Compress ---- */
    ui_status("Compressing bundle: %s", title->game_code);
    uLongf csize = compressBound((uLong)payload_size);
    uint8_t *compressed = (uint8_t *)malloc(csize);
    if (!compressed) { free(payload); goto fail_data; }

    if (compress_payload(payload, payload_size, compressed, &csize) != 0) {
        debug_log("bundle: compress failed");
        free(compressed); free(payload); goto fail_data;
    }
    free(payload);
    pump_callbacks();

    /* ---- Assemble bundle ---- */
    uint32_t bundle_size = BUNDLE_HEADER_SIZE + (uint32_t)csize;
    uint8_t *bundle = (uint8_t *)malloc(bundle_size);
    if (!bundle) { free(compressed); goto fail_data; }

    memcpy(bundle, BUNDLE_MAGIC, 4);
    write_le32(bundle + 4, BUNDLE_VERSION_V4);
    memset(bundle + 8, 0, 32);
    strncpy((char *)(bundle + 8), title->title_id, 31);
    write_le32(bundle + 40, (uint32_t)time(NULL));
    write_le32(bundle + 44, (uint32_t)n);
    write_le32(bundle + 48, payload_size);
    memcpy(bundle + BUNDLE_HEADER_SIZE, compressed, csize);
    free(compressed);

    for (int i = 0; i < n; i++) free(file_data[i]);
    free(file_data);

    *out_data = bundle;
    *out_size = bundle_size;
    debug_log("bundle: created %u bytes (%d files) for %s",
              bundle_size, n, title->game_code);
    return 0;

fail_data:
    for (int i = 0; i < n; i++) if (file_data[i]) free(file_data[i]);
    free(file_data);
    return -1;
}

/* ---- bundle_parse ---- */

int bundle_parse(const uint8_t *data, uint32_t size, Bundle *bundle) {
    if (!data || size < BUNDLE_HEADER_SIZE) return -1;
    if (memcmp(data, BUNDLE_MAGIC, 4) != 0) return -1;

    uint32_t version = read_le32(data + 4);
    if (version != BUNDLE_VERSION_V4) {
        debug_log("bundle: unsupported version %u", version);
        return -1;
    }

    memset(bundle->game_id, 0, GAME_ID_LEN);
    strncpy(bundle->game_id, (const char *)(data + 8), GAME_ID_LEN - 1);
    bundle->timestamp  = read_le32(data + 40);
    bundle->file_count = (int)read_le32(data + 44);
    uint32_t uncompressed_size = read_le32(data + 48);

    if (bundle->file_count < 0 || bundle->file_count > MAX_FILES) return -1;
    if (uncompressed_size == 0 || uncompressed_size > (uint32_t)MAX_PAYLOAD) return -1;

    uint8_t *payload = (uint8_t *)malloc(uncompressed_size);
    if (!payload) return -1;

    ui_status("Decompressing bundle: %s", bundle->game_id);
    uLongf actual = uncompressed_size;
    if (uncompress_payload(data + BUNDLE_HEADER_SIZE,
                   size - BUNDLE_HEADER_SIZE,
                   payload,
                   uncompressed_size,
                   &actual) != 0) {
        debug_log("bundle: uncompress failed");
        free(payload); return -1;
    }
    if (actual != uncompressed_size) { free(payload); return -1; }
    pump_callbacks();

    bundle->data_buf = payload;

    uint32_t off = 0;
    ui_status("Reading bundle table: %s", bundle->game_id);
    for (int i = 0; i < bundle->file_count; i++) {
        if (off + 2 > actual) { free(payload); return -1; }
        uint16_t path_len = read_le16(payload + off); off += 2;

        if (path_len >= MAX_FILE_LEN || off + path_len > actual) {
            free(payload); return -1;
        }
        memcpy(bundle->files[i].path, payload + off, path_len);
        bundle->files[i].path[path_len] = '\0';
        off += path_len;

        if (off + 4 > actual) { free(payload); return -1; }
        bundle->files[i].size = read_le32(payload + off); off += 4;

        if (off + 32 > actual) { free(payload); return -1; }
        memcpy(bundle->files[i].hash, payload + off, 32); off += 32;
    }

    ui_status("Verifying bundle files: %s", bundle->game_id);
    for (int i = 0; i < bundle->file_count; i++) {
        if (off + bundle->files[i].size > actual) { free(payload); return -1; }
        bundle->files[i].data = payload + off;

        uint8_t computed[32];
        sha256_buffer_pumped(bundle->files[i].data, bundle->files[i].size, computed);
        if (memcmp(computed, bundle->files[i].hash, 32) != 0) {
            debug_log("bundle: hash mismatch for file %s", bundle->files[i].path);
            free(payload); return -1;
        }
        off += bundle->files[i].size;
    }

    debug_log("bundle: parsed %d files for %s", bundle->file_count, bundle->game_id);
    return 0;
}

/* ---- bundle_extract ---- */

int bundle_extract(const Bundle *bundle, TitleInfo *title) {
    ui_status("Writing save files: %s", title->game_code);
    for (int i = 0; i < bundle->file_count; i++) {
        ui_status("Writing file %d/%d: %s", i + 1, bundle->file_count,
                  bundle->files[i].path);
        int r = saves_write_file(title,
                                 bundle->files[i].path,
                                 bundle->files[i].data,
                                 bundle->files[i].size);
        if (r < 0) {
            debug_log("bundle: write_file %s failed", bundle->files[i].path);
            return -1;
        }
        pump_callbacks();
    }
    return 0;
}

/* ---- bundle_free ---- */

void bundle_free(Bundle *bundle) {
    if (bundle && bundle->data_buf) {
        free(bundle->data_buf);
        bundle->data_buf = NULL;
    }
}
