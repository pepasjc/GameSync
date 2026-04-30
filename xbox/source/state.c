// Per-title sync state - one file per title under E:\UDATA\TDSV0000\state\.

#include "state.h"

#include <stdio.h>
#include <string.h>

#include <hal/debug.h>
#include <windows.h>

static const char *STATE_DIR = "E:\\UDATA\\TDSV0000\\state";

int state_init(void)
{
    if (CreateDirectoryA(STATE_DIR, NULL)) return 0;
    DWORD err = GetLastError();
    if (err == ERROR_ALREADY_EXISTS) return 0;
    debugPrint("state: mkdir %s err=%lu\n", STATE_DIR, (unsigned long)err);
    return -1;
}

static void state_path(const char *title_id, char *buf, int buf_len)
{
    snprintf(buf, buf_len, "%s\\%s.txt", STATE_DIR, title_id);
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
