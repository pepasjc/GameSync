/*
 * PSP Save Sync - Save enumeration and I/O
 *
 * PSP saves are stored in ms0:/PSP/SAVEDATA/<GAMEID>/
 * Each directory contains multiple files: PARAM.SFO, DATA.BIN, ICON0.PNG, etc.
 */

#include <stdio.h>
#include <string.h>
#include <ctype.h>
#include <pspiofilemgr.h>
#include <pspdebug.h>

#include "saves.h"
#include "sha256.h"

bool saves_is_valid_game_id(const char *game_id) {
    /* PSP product code: exactly 9 chars, 4 uppercase letters + 5 digits */
    if (strlen(game_id) != 9) return false;
    for (int i = 0; i < 4; i++)
        if (!isupper((unsigned char)game_id[i])) return false;
    for (int i = 4; i < 9; i++)
        if (!isdigit((unsigned char)game_id[i])) return false;
    return true;
}

void saves_scan(SyncState *state) {
    state->num_titles = 0;

    pspDebugScreenPrintf("saves_scan: opening %s\n", SAVEDATA_PATH);
    SceUID dir = sceIoDopen(SAVEDATA_PATH);
    if (dir < 0) {
        pspDebugScreenPrintf("saves_scan: sceIoDopen failed: %d\n", dir);
        return;
    }
    pspDebugScreenPrintf("saves_scan: dir opened OK\n");

    /* SceIoDirent must be zeroed: d_private is a kernel-filled pointer and
     * must start as NULL so the kernel skips writing long filenames through it
     * when it is not needed. Uninitialized garbage here causes a crash on
     * real hardware (PRO-C). */
    SceIoDirent entry;
    memset(&entry, 0, sizeof(entry));

    int scanned = 0;
    while (sceIoDread(dir, &entry) > 0) {
        scanned++;
        if (!(entry.d_stat.st_attr & FIO_SO_IFDIR)) continue;
        if (entry.d_name[0] == '.') continue;

        char game_id[GAME_ID_LEN];
        strncpy(game_id, entry.d_name, GAME_ID_LEN - 1);
        game_id[GAME_ID_LEN - 1] = '\0';

        /* Convert to uppercase */
        for (int i = 0; game_id[i]; i++)
            game_id[i] = toupper((unsigned char)game_id[i]);

        if (!saves_is_valid_game_id(game_id)) continue;
        if (state->num_titles >= MAX_TITLES) break;

        pspDebugScreenPrintf("  found: %s\n", game_id);

        TitleInfo *t = &state->titles[state->num_titles];
        memset(t, 0, sizeof(TitleInfo));
        strncpy(t->game_id, game_id, GAME_ID_LEN - 1);
        strncpy(t->name, game_id, MAX_TITLE_LEN - 1);  /* default name = ID */
        snprintf(t->save_dir, SAVE_DIR_LEN, "%s/%s", SAVEDATA_PATH, game_id);

        /* Count files and total size */
        SceUID save_dir = sceIoDopen(t->save_dir);
        if (save_dir >= 0) {
            SceIoDirent fentry;
            memset(&fentry, 0, sizeof(fentry));
            while (sceIoDread(save_dir, &fentry) > 0) {
                if (fentry.d_stat.st_attr & FIO_SO_IFREG) {
                    t->file_count++;
                    t->total_size += fentry.d_stat.st_size;
                }
            }
            sceIoDclose(save_dir);
        }

        if (t->file_count > 0)
            state->num_titles++;
    }
    pspDebugScreenPrintf("saves_scan: done. scanned=%d found=%d\n",
                         scanned, state->num_titles);
    sceIoDclose(dir);
}

int saves_list_files(const TitleInfo *title,
                     char files[][MAX_FILE_LEN], uint32_t sizes[], int max_files) {
    int count = 0;
    SceUID dir = sceIoDopen(title->save_dir);
    if (dir < 0) return -1;

    SceIoDirent entry;
    memset(&entry, 0, sizeof(entry));
    while (sceIoDread(dir, &entry) > 0 && count < max_files) {
        if (!(entry.d_stat.st_attr & FIO_SO_IFREG)) continue;
        if (entry.d_name[0] == '.') continue;

        strncpy(files[count], entry.d_name, MAX_FILE_LEN - 1);
        files[count][MAX_FILE_LEN - 1] = '\0';
        sizes[count] = entry.d_stat.st_size;
        count++;
    }
    sceIoDclose(dir);
    return count;
}

int saves_compute_hash(TitleInfo *title) {
    char files[MAX_FILES][MAX_FILE_LEN];
    uint32_t sizes[MAX_FILES];
    int n = saves_list_files(title, files, sizes, MAX_FILES);
    if (n < 0) return -1;

    /* Sort file names (simple insertion sort for small arrays) */
    for (int i = 1; i < n; i++) {
        char tmp_name[MAX_FILE_LEN];
        uint32_t tmp_size = sizes[i];
        strncpy(tmp_name, files[i], MAX_FILE_LEN);
        int j = i - 1;
        while (j >= 0 && strcmp(files[j], tmp_name) > 0) {
            strncpy(files[j+1], files[j], MAX_FILE_LEN);
            sizes[j+1] = sizes[j];
            j--;
        }
        strncpy(files[j+1], tmp_name, MAX_FILE_LEN);
        sizes[j+1] = tmp_size;
    }

    SHA256_CTX ctx;
    sha256_init(&ctx);

    static uint8_t file_buf[MAX_FILE_SIZE];

    for (int i = 0; i < n; i++) {
        char path[SAVE_DIR_LEN + MAX_FILE_LEN + 2];
        snprintf(path, sizeof(path), "%s/%s", title->save_dir, files[i]);

        SceUID fd = sceIoOpen(path, PSP_O_RDONLY, 0777);
        if (fd < 0) continue;

        int bytes;
        while ((bytes = sceIoRead(fd, file_buf, sizeof(file_buf))) > 0)
            sha256_update(&ctx, file_buf, bytes);
        sceIoClose(fd);
    }

    sha256_final(&ctx, title->hash);
    title->hash_calculated = true;
    return 0;
}

int saves_read_file(const TitleInfo *title, const char *rel_path,
                    uint8_t *buf, uint32_t buf_size) {
    char path[SAVE_DIR_LEN + MAX_FILE_LEN + 2];
    snprintf(path, sizeof(path), "%s/%s", title->save_dir, rel_path);

    SceUID fd = sceIoOpen(path, PSP_O_RDONLY, 0777);
    if (fd < 0) return -1;

    int total = 0, bytes;
    while ((bytes = sceIoRead(fd, buf + total, buf_size - total)) > 0)
        total += bytes;
    sceIoClose(fd);
    return total;
}

int saves_write_file(const TitleInfo *title, const char *rel_path,
                     const uint8_t *data, uint32_t size) {
    /* Create save directory if needed */
    sceIoMkdir(title->save_dir, 0777);

    char path[SAVE_DIR_LEN + MAX_FILE_LEN + 2];
    snprintf(path, sizeof(path), "%s/%s", title->save_dir, rel_path);

    SceUID fd = sceIoOpen(path, PSP_O_WRONLY | PSP_O_CREAT | PSP_O_TRUNC, 0777);
    if (fd < 0) return -1;

    int written = sceIoWrite(fd, data, size);
    sceIoClose(fd);
    return (written == (int)size) ? 0 : -1;
}
