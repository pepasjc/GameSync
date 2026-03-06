/*
 * PSP Save Sync - Bundle v3 format
 *
 * Creates and parses 3DSS v3 bundles for PSP saves.
 * Uses zlib for compression.
 */

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <zlib.h>
#include <pspkernel.h>
#include <psprtc.h>

#include "bundle.h"
#include "saves.h"
#include "sha256.h"

#define MAX_PAYLOAD (8 * 1024 * 1024)  /* 8MB */

static uint32_t read_le32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static void write_le32(uint8_t *p, uint32_t v) {
    p[0] = v & 0xFF; p[1] = (v >> 8) & 0xFF;
    p[2] = (v >> 16) & 0xFF; p[3] = (v >> 24) & 0xFF;
}

static uint16_t read_le16(const uint8_t *p) {
    return (uint16_t)p[0] | ((uint16_t)p[1] << 8);
}

static void write_le16(uint8_t *p, uint16_t v) {
    p[0] = v & 0xFF; p[1] = (v >> 8) & 0xFF;
}

int bundle_create(const TitleInfo *title, uint8_t **out_data, uint32_t *out_size) {
    /* Collect files */
    char file_names[MAX_FILES][MAX_FILE_LEN];
    uint32_t file_sizes[MAX_FILES];
    int n = saves_list_files(title, file_names, file_sizes, MAX_FILES);
    if (n <= 0) return -1;

    /* Sort file names (insertion sort) */
    for (int i = 1; i < n; i++) {
        char tmp_name[MAX_FILE_LEN];
        uint32_t tmp_size = file_sizes[i];
        strncpy(tmp_name, file_names[i], MAX_FILE_LEN);
        int j = i - 1;
        while (j >= 0 && strcmp(file_names[j], tmp_name) > 0) {
            strncpy(file_names[j+1], file_names[j], MAX_FILE_LEN);
            file_sizes[j+1] = file_sizes[j];
            j--;
        }
        strncpy(file_names[j+1], tmp_name, MAX_FILE_LEN);
        file_sizes[j+1] = tmp_size;
    }

    /* Read all file data */
    static uint8_t file_data_buf[MAX_PAYLOAD];
    static uint8_t *file_ptrs[MAX_FILES];
    uint32_t file_data_offset = 0;

    for (int i = 0; i < n; i++) {
        file_ptrs[i] = file_data_buf + file_data_offset;
        int r = saves_read_file(title, file_names[i], file_ptrs[i],
                                MAX_PAYLOAD - file_data_offset);
        if (r < 0) return -1;
        file_sizes[i] = r;
        file_data_offset += r;
    }

    /* Build payload: file table + file data */
    static uint8_t payload_buf[MAX_PAYLOAD + 65536];
    uint32_t payload_offset = 0;

    /* File table */
    for (int i = 0; i < n; i++) {
        uint16_t path_len = strlen(file_names[i]);
        write_le16(payload_buf + payload_offset, path_len); payload_offset += 2;
        memcpy(payload_buf + payload_offset, file_names[i], path_len); payload_offset += path_len;
        write_le32(payload_buf + payload_offset, file_sizes[i]); payload_offset += 4;

        uint8_t hash[32];
        sha256(file_ptrs[i], file_sizes[i], hash);
        memcpy(payload_buf + payload_offset, hash, 32); payload_offset += 32;
    }

    /* File data */
    for (int i = 0; i < n; i++) {
        memcpy(payload_buf + payload_offset, file_ptrs[i], file_sizes[i]);
        payload_offset += file_sizes[i];
    }

    /* Compress payload */
    uLongf compressed_size = compressBound(payload_offset);
    uint8_t *compressed = malloc(compressed_size);
    if (!compressed) return -1;

    if (compress2(compressed, &compressed_size, payload_buf, payload_offset, 6) != Z_OK) {
        free(compressed);
        return -1;
    }

    /* Build bundle: header + compressed payload */
    uint32_t bundle_size = BUNDLE_HEADER_SIZE + compressed_size;
    uint8_t *bundle = malloc(bundle_size);
    if (!bundle) { free(compressed); return -1; }

    /* Magic */
    memcpy(bundle + 0, BUNDLE_MAGIC, 4);
    /* Version = 4 */
    write_le32(bundle + 4, BUNDLE_VERSION_V4);
    /* Title ID: 32 bytes, null-padded ASCII */
    memset(bundle + 8, 0, 32);
    strncpy((char *)(bundle + 8), title->game_id, 32);
    /* Timestamp */
    u64 tick;
    sceRtcGetCurrentTick(&tick);
    write_le32(bundle + 40, (uint32_t)(tick / 1000));
    /* File count */
    write_le32(bundle + 44, n);
    /* Uncompressed size */
    write_le32(bundle + 48, payload_offset);
    /* Compressed payload */
    memcpy(bundle + BUNDLE_HEADER_SIZE, compressed, compressed_size);

    free(compressed);

    *out_data = bundle;
    *out_size = bundle_size;
    return 0;
}

int bundle_parse(const uint8_t *data, uint32_t size, Bundle *bundle) {
    if (size < 36) return -1;  /* minimum: v3 header size */
    if (memcmp(data, BUNDLE_MAGIC, 4) != 0) return -1;

    uint32_t version = read_le32(data + 4);
    uint32_t ts_off, fc_off, usz_off, hdr_size;

    memset(bundle->game_id, 0, GAME_ID_LEN);
    if (version == BUNDLE_VERSION_V4) {
        if (size < BUNDLE_HEADER_SIZE) return -1;
        strncpy(bundle->game_id, (const char *)(data + 8), GAME_ID_LEN - 1);
        ts_off = 40; fc_off = 44; usz_off = 48; hdr_size = BUNDLE_HEADER_SIZE;
    } else if (version == BUNDLE_VERSION_V3) {
        strncpy(bundle->game_id, (const char *)(data + 8), GAME_ID_LEN - 1);
        ts_off = 24; fc_off = 28; usz_off = 32; hdr_size = 36;
    } else {
        return -1;
    }

    bundle->timestamp = read_le32(data + ts_off);
    bundle->file_count = read_le32(data + fc_off);
    uint32_t uncompressed_size = read_le32(data + usz_off);

    if (bundle->file_count > MAX_FILES) return -1;
    if (uncompressed_size > MAX_PAYLOAD) return -1;

    /* Decompress payload */
    uint8_t *payload = malloc(uncompressed_size);
    if (!payload) return -1;

    uLongf actual_size = uncompressed_size;
    if (uncompress(payload, &actual_size, data + hdr_size,
                   size - hdr_size) != Z_OK) {
        free(payload);
        return -1;
    }
    if (actual_size != uncompressed_size) { free(payload); return -1; }

    bundle->data_buf = payload;

    /* Parse file table */
    uint32_t offset = 0;
    for (int i = 0; i < bundle->file_count; i++) {
        if (offset + 2 > actual_size) { free(payload); return -1; }
        uint16_t path_len = read_le16(payload + offset); offset += 2;

        if (offset + path_len > actual_size) { free(payload); return -1; }
        if (path_len >= MAX_FILE_LEN) { free(payload); return -1; }
        memcpy(bundle->files[i].path, payload + offset, path_len);
        bundle->files[i].path[path_len] = '\0';
        offset += path_len;

        if (offset + 4 > actual_size) { free(payload); return -1; }
        bundle->files[i].size = read_le32(payload + offset); offset += 4;

        if (offset + 32 > actual_size) { free(payload); return -1; }
        memcpy(bundle->files[i].hash, payload + offset, 32); offset += 32;
    }

    /* Set data pointers */
    for (int i = 0; i < bundle->file_count; i++) {
        if (offset + bundle->files[i].size > actual_size) { free(payload); return -1; }
        bundle->files[i].data = payload + offset;

        /* Verify hash */
        uint8_t computed[32];
        sha256(bundle->files[i].data, bundle->files[i].size, computed);
        if (memcmp(computed, bundle->files[i].hash, 32) != 0) {
            free(payload);
            return -1;
        }

        offset += bundle->files[i].size;
    }

    return 0;
}

int bundle_extract(const Bundle *bundle, TitleInfo *title) {
    for (int i = 0; i < bundle->file_count; i++) {
        int r = saves_write_file(title, bundle->files[i].path,
                                 bundle->files[i].data, bundle->files[i].size);
        if (r < 0) return r;
    }
    return 0;
}

void bundle_free(Bundle *bundle) {
    if (bundle->data_buf) {
        free(bundle->data_buf);
        bundle->data_buf = NULL;
    }
}
