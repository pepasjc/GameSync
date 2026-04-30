// Save enumeration on the Xbox HDD - see saves.h for design notes.

#include "saves.h"

#include <ctype.h>
#include <stdio.h>
#include <string.h>

#include <nxdk/mount.h>
#include <windows.h>

static int hex_value(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    return -1;
}

static int hex_byte(const char *s)
{
    int hi = hex_value(s[0]);
    int lo = hex_value(s[1]);
    if (hi < 0 || lo < 0) return -1;
    return (hi << 4) | lo;
}

int saves_is_game_title_id(const char *name)
{
    if (!name) return 0;
    int len = (int)strlen(name);
    if (len != XBOX_TITLE_ID_LEN) return 0;
    for (int i = 0; i < XBOX_TITLE_ID_LEN; i++) {
        if (hex_value(name[i]) < 0) return 0;
    }

    int publisher_a = hex_byte(name);
    int publisher_b = hex_byte(name + 2);
    return publisher_a >= 'A' && publisher_a <= 'Z' &&
           publisher_b >= 'A' && publisher_b <= 'Z';
}

int saves_init(void)
{
    // E: is the standard data partition on retail Xbox FATX layouts. nxdk's
    // automount may already have wired this up at startup; if not, mount
    // explicitly. Either way we tolerate "already mounted" as success.
    if (nxIsDriveMounted('E')) {
        return 0;
    }
    BOOL ok = nxMountDrive('E', "\\Device\\Harddisk0\\Partition1\\");
    return ok ? 0 : -1;
}

static uint32_t filetime_to_unix(const FILETIME *ft)
{
    const unsigned long long unix_epoch_in_filetime = 116444736000000000ULL;
    unsigned long long ticks =
        ((unsigned long long)ft->dwHighDateTime << 32) |
        (unsigned long long)ft->dwLowDateTime;

    if (ticks <= unix_epoch_in_filetime) return 0;
    return (uint32_t)((ticks - unix_epoch_in_filetime) / 10000000ULL);
}

static void scan_files_recursive(const char *root,
                                 const char *rel_dir,
                                 XboxSaveTitle *title)
{
    char search[XBOX_PATH_MAX];
    if (rel_dir[0]) {
        snprintf(search, sizeof(search), "%s\\%s\\*", root, rel_dir);
    } else {
        snprintf(search, sizeof(search), "%s\\*", root);
    }

    WIN32_FIND_DATAA fd;
    HANDLE h = FindFirstFileA(search, &fd);
    if (h == INVALID_HANDLE_VALUE) {
        return;
    }

    do {
        if (strcmp(fd.cFileName, ".") == 0 ||
            strcmp(fd.cFileName, "..") == 0) {
            continue;
        }

        char child_rel[XBOX_PATH_MAX];
        if (rel_dir[0]) {
            snprintf(child_rel, sizeof(child_rel), "%s\\%s",
                     rel_dir, fd.cFileName);
        } else {
            snprintf(child_rel, sizeof(child_rel), "%s", fd.cFileName);
        }

        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
            scan_files_recursive(root, child_rel, title);
        } else {
            if (title->file_count >= XBOX_MAX_FILES_PER_TITLE) {
                continue;
            }
            XboxSaveFile *f = &title->files[title->file_count++];
            // Saves are tiny relative to 32-bit. nFileSizeHigh != 0 would be
            // a rogue file (>=4 GiB) - clamp to UINT32_MAX so size stays sane.
            uint32_t sz = (fd.nFileSizeHigh != 0)
                              ? 0xFFFFFFFFu
                              : fd.nFileSizeLow;
            f->file_size = sz;
            strncpy(f->relative_path, child_rel,
                    sizeof(f->relative_path) - 1);
            f->relative_path[sizeof(f->relative_path) - 1] = '\0';
            title->total_size += sz;

            uint32_t mtime = filetime_to_unix(&fd.ftLastWriteTime);
            f->mtime = mtime;
            if (mtime > title->latest_mtime) title->latest_mtime = mtime;
        }
    } while (FindNextFileA(h, &fd));

    FindClose(h);
}

int saves_scan(XboxSaveList *out)
{
    if (!out) return 0;
    memset(out, 0, sizeof(*out));

    WIN32_FIND_DATAA fd;
    HANDLE h = FindFirstFileA("E:\\UDATA\\*", &fd);
    if (h == INVALID_HANDLE_VALUE) {
        return 0;
    }

    do {
        if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)) continue;
        if (!saves_is_game_title_id(fd.cFileName)) continue;
        if (out->title_count >= XBOX_MAX_TITLES) break;

        XboxSaveTitle *t = &out->titles[out->title_count++];
        for (int i = 0; i < XBOX_TITLE_ID_LEN; i++) {
            t->title_id[i] = (char)toupper((unsigned char)fd.cFileName[i]);
        }
        t->title_id[XBOX_TITLE_ID_LEN] = '\0';

        char title_root[XBOX_PATH_MAX];
        snprintf(title_root, sizeof(title_root),
                 "E:\\UDATA\\%s", t->title_id);
        scan_files_recursive(title_root, "", t);

        out->total_files += t->file_count;
        out->total_bytes += t->total_size;
    } while (FindNextFileA(h, &fd));

    FindClose(h);
    return out->title_count;
}
