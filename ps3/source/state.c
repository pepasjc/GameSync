#include "state.h"

#include "ui.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define KV_BUFFER_SIZE 32768

static void append_span(char *dest, size_t dest_size, const char *src, size_t src_len) {
    size_t used = strlen(dest);
    size_t available;

    if (used >= dest_size) {
        return;
    }

    available = dest_size - used - 1;
    if (src_len > available) {
        src_len = available;
    }
    memcpy(dest + used, src, src_len);
    dest[used + src_len] = '\0';
}

static bool kv_get(const char *path, const char *key, char *value_out, size_t value_out_size) {
    FILE *fp = fopen(path, "rb");
    if (!fp) {
        return false;
    }

    char line[512];
    const size_t key_len = strlen(key);
    while (fgets(line, sizeof(line), fp) != NULL) {
        if (strncmp(line, key, key_len) != 0 || line[key_len] != '=') {
            continue;
        }
        char *value = line + key_len + 1;
        size_t len = strcspn(value, "\r\n");
        if (len >= value_out_size) {
            len = value_out_size - 1;
        }
        memcpy(value_out, value, len);
        value_out[len] = '\0';
        fclose(fp);
        return true;
    }

    fclose(fp);
    return false;
}

static bool kv_set(const char *path, const char *key, const char *value) {
    char *existing = (char *)malloc(KV_BUFFER_SIZE);
    char *rewritten = (char *)malloc(KV_BUFFER_SIZE);
    if (!existing || !rewritten) {
        free(existing);
        free(rewritten);
        return false;
    }
    existing[0] = '\0';

    FILE *read_fp = fopen(path, "rb");
    if (read_fp) {
        size_t bytes = fread(existing, 1, KV_BUFFER_SIZE - 1, read_fp);
        existing[bytes] = '\0';
        fclose(read_fp);
    }

    rewritten[0] = '\0';
    const size_t key_len = strlen(key);
    const char *cursor = existing;

    while (*cursor != '\0') {
        const char *line_end = cursor + strcspn(cursor, "\r\n");
        size_t line_len = (size_t)(line_end - cursor);
        bool is_match = line_len > key_len && strncmp(cursor, key, key_len) == 0
            && cursor[key_len] == '=';

        if (line_len > 0 && !is_match) {
            append_span(rewritten, KV_BUFFER_SIZE, cursor, line_len);
            append_span(rewritten, KV_BUFFER_SIZE, "\n", 1);
        }

        cursor = line_end;
        while (*cursor == '\r' || *cursor == '\n') {
            cursor++;
        }
    }

    append_span(rewritten, KV_BUFFER_SIZE, key, strlen(key));
    append_span(rewritten, KV_BUFFER_SIZE, "=", 1);
    append_span(rewritten, KV_BUFFER_SIZE, value, strlen(value));
    append_span(rewritten, KV_BUFFER_SIZE, "\n", 1);

    FILE *write_fp = fopen(path, "wb");
    if (!write_fp) {
        free(existing);
        free(rewritten);
        return false;
    }

    size_t total = strlen(rewritten);
    for (size_t off = 0; off < total; ) {
        size_t chunk = total - off;
        size_t written;
        if (chunk > 4096U) chunk = 4096U;
        written = fwrite(rewritten + off, 1, chunk, write_fp);
        if (written != chunk) {
            fclose(write_fp);
            free(existing);
            free(rewritten);
            return false;
        }
        off += written;
        pump_callbacks();
    }
    fclose(write_fp);
    free(existing);
    free(rewritten);
    return true;
}

bool state_get_last_hash(const char *title_id, char *hash_out) {
    return kv_get(STATE_FILE, title_id, hash_out, 65);
}

bool state_set_last_hash(const char *title_id, const char *hash_hex) {
    ui_status("Writing sync state file");
    return kv_set(STATE_FILE, title_id, hash_hex);
}

bool state_get_cached_hash(
    const char *title_id,
    int file_count,
    uint32_t total_size,
    char *hash_out
) {
    char key[96];
    snprintf(key, sizeof(key), "%s:%d:%u", title_id, file_count, total_size);
    return kv_get(HASH_CACHE_FILE, key, hash_out, 65);
}

bool state_set_cached_hash(
    const char *title_id,
    int file_count,
    uint32_t total_size,
    const char *hash_hex
) {
    char key[96];
    snprintf(key, sizeof(key), "%s:%d:%u", title_id, file_count, total_size);
    ui_status("Writing hash cache file");
    return kv_set(HASH_CACHE_FILE, key, hash_hex);
}
