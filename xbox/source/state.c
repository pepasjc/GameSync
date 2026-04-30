// Per-title sync state - one file per title under E:\UDATA\TDSV0000\state\.

#include "state.h"

#include <stdio.h>
#include <string.h>

#include <hal/debug.h>
#include <windows.h>

static const char *STATE_DIR = "E:\\UDATA\\TDSV0000\\state";
static const char *CACHE_DIR = "E:\\UDATA\\TDSV0000\\hashcache";
static int g_state_ready = 0;

int state_init(void)
{
    if (g_state_ready) return 0;
    if (!CreateDirectoryA(STATE_DIR, NULL)) {
        DWORD err = GetLastError();
        if (err != ERROR_ALREADY_EXISTS) {
            debugPrint("state: mkdir %s err=%lu\n", STATE_DIR, (unsigned long)err);
            return -1;
        }
    }
    if (!CreateDirectoryA(CACHE_DIR, NULL)) {
        DWORD err = GetLastError();
        if (err != ERROR_ALREADY_EXISTS) {
            debugPrint("state: mkdir %s err=%lu\n", CACHE_DIR, (unsigned long)err);
            return -1;
        }
    }
    g_state_ready = 1;
    return 0;
}

static void state_path(const char *title_id, char *buf, int buf_len)
{
    snprintf(buf, buf_len, "%s\\%s.txt", STATE_DIR, title_id);
}

static void cache_path(const char *title_id, char *buf, int buf_len)
{
    snprintf(buf, buf_len, "%s\\%s.txt", CACHE_DIR, title_id);
}

static uint32_t fnv1a_bytes(uint32_t h, const void *data, int len)
{
    const unsigned char *p = (const unsigned char *)data;
    for (int i = 0; i < len; i++) {
        h ^= p[i];
        h *= 16777619u;
    }
    return h;
}

static uint32_t fnv1a_u32(uint32_t h, uint32_t v)
{
    return fnv1a_bytes(h, &v, sizeof(v));
}

static uint32_t title_fingerprint(const XboxSaveTitle *title)
{
    uint32_t h = 2166136261u;
    if (!title) return 0;
    h = fnv1a_bytes(h, title->title_id, (int)strlen(title->title_id));
    h = fnv1a_u32(h, (uint32_t)title->file_count);
    h = fnv1a_u32(h, title->total_size);
    h = fnv1a_u32(h, title->latest_mtime);
    for (int i = 0; i < title->file_count; i++) {
        const XboxSaveFile *f = &title->files[i];
        h = fnv1a_bytes(h, f->relative_path, (int)strlen(f->relative_path));
        h = fnv1a_u32(h, f->file_size);
        h = fnv1a_u32(h, f->mtime);
    }
    return h;
}

int state_get_last_hash(const char *title_id, char *out)
{
    if (!title_id || !out) return 0;
    out[0] = '\0';

    char path[260];
    state_path(title_id, path, sizeof(path));

    HANDLE h = CreateFileA(path, GENERIC_READ, FILE_SHARE_READ, NULL,
                           OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) return 0;

    char buf[XBOX_HASH_BUF + 4];
    DWORD got = 0;
    BOOL ok = ReadFile(h, buf, sizeof(buf) - 1, &got, NULL);
    CloseHandle(h);
    if (!ok || got == 0) return 0;
    buf[got] = '\0';

    // Strip trailing whitespace.
    while (got > 0) {
        char c = buf[got - 1];
        if (c == ' ' || c == '\t' || c == '\r' || c == '\n') {
            buf[--got] = '\0';
        } else {
            break;
        }
    }
    if (got != XBOX_HASH_HEX_LEN) return 0;

    memcpy(out, buf, XBOX_HASH_HEX_LEN);
    out[XBOX_HASH_HEX_LEN] = '\0';
    return 1;
}

int state_set_last_hash(const char *title_id, const char *hex64)
{
    if (!title_id || !hex64) return -1;
    if (strlen(hex64) != XBOX_HASH_HEX_LEN) return -1;

    char path[260];
    state_path(title_id, path, sizeof(path));

    HANDLE h = CreateFileA(path, GENERIC_WRITE, 0, NULL,
                           CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) {
        debugPrint("state: write %s err=%lu\n",
                   path, (unsigned long)GetLastError());
        return -1;
    }
    DWORD written = 0;
    BOOL ok = WriteFile(h, hex64, XBOX_HASH_HEX_LEN, &written, NULL);
    CloseHandle(h);
    return (ok && written == XBOX_HASH_HEX_LEN) ? 0 : -1;
}

int state_get_cached_save_hash(const XboxSaveTitle *title, char *out)
{
    if (!title || !out) return 0;
    out[0] = '\0';
    if (state_init() != 0) return 0;

    char path[260];
    cache_path(title->title_id, path, sizeof(path));

    HANDLE h = CreateFileA(path, GENERIC_READ, FILE_SHARE_READ, NULL,
                           OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) return 0;

    char buf[256];
    DWORD got = 0;
    BOOL ok = ReadFile(h, buf, sizeof(buf) - 1, &got, NULL);
    CloseHandle(h);
    if (!ok || got == 0) return 0;
    buf[got] = '\0';

    unsigned version = 0;
    unsigned file_count = 0;
    unsigned total_size = 0;
    unsigned latest_mtime = 0;
    unsigned fingerprint = 0;
    char hash[XBOX_HASH_BUF] = "";
    if (sscanf(buf, "%u %u %u %u %u %64s",
               &version, &file_count, &total_size, &latest_mtime,
               &fingerprint, hash) != 6) {
        return 0;
    }
    if (version != 1 ||
        file_count != (unsigned)title->file_count ||
        total_size != (unsigned)title->total_size ||
        latest_mtime != (unsigned)title->latest_mtime ||
        fingerprint != (unsigned)title_fingerprint(title) ||
        strlen(hash) != XBOX_HASH_HEX_LEN) {
        return 0;
    }

    memcpy(out, hash, XBOX_HASH_BUF);
    return 1;
}

int state_set_cached_save_hash(const XboxSaveTitle *title, const char *hex64)
{
    if (!title || !hex64) return -1;
    if (strlen(hex64) != XBOX_HASH_HEX_LEN) return -1;
    if (state_init() != 0) return -1;

    char path[260];
    cache_path(title->title_id, path, sizeof(path));

    HANDLE h = CreateFileA(path, GENERIC_WRITE, 0, NULL,
                           CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) {
        debugPrint("cache: write %s err=%lu\n",
                   path, (unsigned long)GetLastError());
        return -1;
    }

    char buf[256];
    int n = snprintf(buf, sizeof(buf), "1 %u %u %u %u %s\r\n",
                     (unsigned)title->file_count,
                     (unsigned)title->total_size,
                     (unsigned)title->latest_mtime,
                     (unsigned)title_fingerprint(title),
                     hex64);
    DWORD written = 0;
    BOOL ok = WriteFile(h, buf, (DWORD)n, &written, NULL);
    CloseHandle(h);
    return (ok && written == (DWORD)n) ? 0 : -1;
}

int state_clear_cached_save_hash(const char *title_id)
{
    if (!title_id) return -1;
    char path[260];
    cache_path(title_id, path, sizeof(path));
    if (DeleteFileA(path)) return 0;
    DWORD err = GetLastError();
    return (err == ERROR_FILE_NOT_FOUND || err == ERROR_PATH_NOT_FOUND) ? 0 : -1;
}

int state_clear_hash_cache(void)
{
    if (state_init() != 0) return -1;

    char search[260];
    snprintf(search, sizeof(search), "%s\\*.txt", CACHE_DIR);

    WIN32_FIND_DATAA fd;
    HANDLE h = FindFirstFileA(search, &fd);
    if (h == INVALID_HANDLE_VALUE) return 0;

    do {
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) continue;
        char path[260];
        snprintf(path, sizeof(path), "%s\\%s", CACHE_DIR, fd.cFileName);
        DeleteFileA(path);
    } while (FindNextFileA(h, &fd));
    FindClose(h);
    return 0;
}
