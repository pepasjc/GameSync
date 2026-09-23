/*
 * state.c — per-title sync state + local-hash cache on the SD card.
 *
 * Ported from xbox/source/state.c; Win32 file calls become stdio and the
 * fixed E:\UDATA root becomes the configured SD app directory.
 */

#include "state.h"

#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define STATE_SUBDIR APP_DATA_SUBDIR "/state"
#define CACHE_SUBDIR APP_DATA_SUBDIR "/hashcache"

/* Title ids are safe filename components by construction (GC_XXXX, WII_XXXX,
 * 16-hex) but sanitise anyway — a server-supplied id reaches here too. */
static void safe_id(const char *title_id, char *out, size_t out_size) {
    size_t j = 0;
    for (size_t i = 0; title_id[i] && j + 1 < out_size; i++) {
        char c = title_id[i];
        bool ok = (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
                  (c >= '0' && c <= '9') || c == '_' || c == '-' || c == '.';
        out[j++] = ok ? c : '_';
    }
    out[j] = '\0';
}

static void state_path(const SyncState *st, const char *title_id,
                       char *buf, size_t buf_len) {
    char id[TITLE_ID_LEN];
    safe_id(title_id, id, sizeof(id));
    char rel[TITLE_ID_LEN + 64];
    snprintf(rel, sizeof(rel), STATE_SUBDIR "/%s.txt", id);
    sdpath(st, rel, buf, buf_len);
}

static void cache_path(const SyncState *st, const char *title_id,
                       char *buf, size_t buf_len) {
    char id[TITLE_ID_LEN];
    safe_id(title_id, id, sizeof(id));
    char rel[TITLE_ID_LEN + 64];
    snprintf(rel, sizeof(rel), CACHE_SUBDIR "/%s.txt", id);
    sdpath(st, rel, buf, buf_len);
}

int state_init(const SyncState *st) {
    char path[SAVE_DIR_LEN];
    sdpath(st, APP_DATA_SUBDIR, path, sizeof(path));
    mkdir(path, 0777);
    sdpath(st, STATE_SUBDIR, path, sizeof(path));
    mkdir(path, 0777);
    sdpath(st, CACHE_SUBDIR, path, sizeof(path));
    mkdir(path, 0777);

    struct stat sb;
    return (stat(path, &sb) == 0) ? 0 : -1;
}

int state_get_last_hash(const SyncState *st, const char *title_id, char *out) {
    if (!title_id || !out) return 0;
    out[0] = '\0';

    char path[SAVE_DIR_LEN];
    state_path(st, title_id, path, sizeof(path));

    FILE *fp = fopen(path, "rb");
    if (!fp) return 0;
    char buf[STATE_HASH_BUF + 8];
    size_t got = fread(buf, 1, sizeof(buf) - 1, fp);
    fclose(fp);
    if (got == 0) return 0;
    buf[got] = '\0';

    while (got > 0) {
        char c = buf[got - 1];
        if (c == ' ' || c == '\t' || c == '\r' || c == '\n') buf[--got] = '\0';
        else break;
    }
    if (got != STATE_HASH_HEX_LEN) return 0;

    memcpy(out, buf, STATE_HASH_HEX_LEN);
    out[STATE_HASH_HEX_LEN] = '\0';
    return 1;
}

int state_set_last_hash(const SyncState *st, const char *title_id, const char *hex64) {
    if (!title_id || !hex64 || strlen(hex64) != STATE_HASH_HEX_LEN) return -1;
    state_init(st);

    char path[SAVE_DIR_LEN];
    state_path(st, title_id, path, sizeof(path));

    FILE *fp = fopen(path, "wb");
    if (!fp) return -1;
    size_t n = fwrite(hex64, 1, STATE_HASH_HEX_LEN, fp);
    fclose(fp);
    return n == STATE_HASH_HEX_LEN ? 0 : -1;
}

/* Cheap change detector: any file added/removed/resized/touched, or the tree
 * reordered, changes the fingerprint and invalidates the cached hash. */
static uint32_t fnv1a_bytes(uint32_t h, const void *data, int len) {
    const unsigned char *p = (const unsigned char *)data;
    for (int i = 0; i < len; i++) {
        h ^= p[i];
        h *= 16777619u;
    }
    return h;
}

static uint32_t fnv1a_u32(uint32_t h, uint32_t v) {
    return fnv1a_bytes(h, &v, sizeof(v));
}

static uint32_t title_fingerprint(const SaveTitle *title) {
    uint32_t h = 2166136261u;
    if (!title) return 0;
    h = fnv1a_bytes(h, title->title_id, (int)strlen(title->title_id));
    h = fnv1a_u32(h, (uint32_t)title->file_count);
    h = fnv1a_u32(h, title->total_size);
    h = fnv1a_u32(h, title->latest_mtime);
    for (int i = 0; i < title->file_count; i++) {
        const SaveFile *f = &title->files[i];
        h = fnv1a_bytes(h, f->relative_path, (int)strlen(f->relative_path));
        h = fnv1a_u32(h, f->file_size);
        h = fnv1a_u32(h, f->mtime);
    }
    return h;
}

int state_get_cached_save_hash(const SyncState *st, const SaveTitle *title, char *out) {
    if (!title || !out) return 0;
    out[0] = '\0';

    char path[SAVE_DIR_LEN];
    cache_path(st, title->title_id, path, sizeof(path));

    FILE *fp = fopen(path, "rb");
    if (!fp) return 0;
    char buf[256];
    size_t got = fread(buf, 1, sizeof(buf) - 1, fp);
    fclose(fp);
    if (got == 0) return 0;
    buf[got] = '\0';

    unsigned version = 0, file_count = 0, total_size = 0;
    unsigned latest_mtime = 0, fingerprint = 0;
    char hash[STATE_HASH_BUF] = "";
    if (sscanf(buf, "%u %u %u %u %u %64s",
               &version, &file_count, &total_size, &latest_mtime,
               &fingerprint, hash) != 6)
        return 0;

    if (version != 1 ||
        file_count   != (unsigned)title->file_count ||
        total_size   != (unsigned)title->total_size ||
        latest_mtime != (unsigned)title->latest_mtime ||
        fingerprint  != title_fingerprint(title) ||
        strlen(hash) != STATE_HASH_HEX_LEN)
        return 0;

    memcpy(out, hash, STATE_HASH_BUF);
    return 1;
}

int state_set_cached_save_hash(const SyncState *st, const SaveTitle *title,
                               const char *hex64) {
    if (!title || !hex64 || strlen(hex64) != STATE_HASH_HEX_LEN) return -1;
    state_init(st);

    char path[SAVE_DIR_LEN];
    cache_path(st, title->title_id, path, sizeof(path));

    FILE *fp = fopen(path, "wb");
    if (!fp) return -1;
    int n = fprintf(fp, "1 %u %u %u %u %s\n",
                    (unsigned)title->file_count,
                    (unsigned)title->total_size,
                    (unsigned)title->latest_mtime,
                    (unsigned)title_fingerprint(title),
                    hex64);
    fclose(fp);
    return n > 0 ? 0 : -1;
}

int state_clear_cached_save_hash(const SyncState *st, const char *title_id) {
    if (!title_id) return -1;
    char path[SAVE_DIR_LEN];
    cache_path(st, title_id, path, sizeof(path));
    remove(path);
    return 0;
}
