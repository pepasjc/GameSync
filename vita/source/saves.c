/*
 * Vita Save Sync - Save enumeration and I/O
 *
 * Native Vita saves:  ux0:user/00/savedata/<TITLEID>/  (e.g. PCSE00082)
 * PSP emu saves:      ux0:pspemu/PSP/SAVEDATA/<GAMEID>/ (e.g. ULUS10272)
 */

#include <stdio.h>
#include <string.h>
#include <ctype.h>
#include <psp2/io/fcntl.h>
#include <psp2/io/dirent.h>
#include <psp2/io/stat.h>

#include "saves.h"
#include "sha256.h"

bool saves_is_vita_game_id(const char *game_id) {
    /* Vita product code: PCS + uppercase letter + 5 digits = 9 chars */
    if (strlen(game_id) != 9) return false;
    if (game_id[0] != 'P' || game_id[1] != 'C' || game_id[2] != 'S') return false;
    if (!isupper((unsigned char)game_id[3])) return false;
    for (int i = 4; i < 9; i++)
        if (!isdigit((unsigned char)game_id[i])) return false;
    return true;
}

bool saves_is_psp_game_id(const char *game_id) {
    /* PSP product code: 4 uppercase letters + 5 digits = 9 chars */
    if (strlen(game_id) != 9) return false;
    for (int i = 0; i < 4; i++)
        if (!isupper((unsigned char)game_id[i])) return false;
    for (int i = 4; i < 9; i++)
        if (!isdigit((unsigned char)game_id[i])) return false;
    return true;
}

static void scan_dir(SyncState *state, const char *base_path, Platform platform) {
    SceUID dir = sceIoDopen(base_path);
    if (dir < 0) return;

    SceIoDirent entry;
    while (sceIoDread(dir, &entry) > 0) {
        if (!SCE_S_ISDIR(entry.d_stat.st_mode)) continue;
        if (entry.d_name[0] == '.') continue;

        char game_id[GAME_ID_LEN];
        strncpy(game_id, entry.d_name, GAME_ID_LEN - 1);
        game_id[GAME_ID_LEN - 1] = '\0';

        for (int i = 0; game_id[i]; i++)
            game_id[i] = toupper((unsigned char)game_id[i]);

        bool valid = (platform == PLATFORM_VITA)
                     ? saves_is_vita_game_id(game_id)
                     : saves_is_psp_game_id(game_id);
        if (!valid) continue;
        if (state->num_titles >= MAX_TITLES) break;

        TitleInfo *t = &state->titles[state->num_titles];
        memset(t, 0, sizeof(TitleInfo));
        strncpy(t->game_id, game_id, GAME_ID_LEN - 1);
        strncpy(t->name, game_id, MAX_TITLE_LEN - 1);
        snprintf(t->save_dir, SAVE_DIR_LEN, "%s/%s", base_path, game_id);
        t->platform = platform;

        /* Count files and total size */
        SceUID save_dir = sceIoDopen(t->save_dir);
        if (save_dir >= 0) {
            SceIoDirent fe;
            while (sceIoDread(save_dir, &fe) > 0) {
                if (SCE_S_ISREG(fe.d_stat.st_mode)) {
                    t->file_count++;
                    t->total_size += fe.d_stat.st_size;
                }
            }
            sceIoDclose(save_dir);
        }

        if (t->file_count > 0)
            state->num_titles++;
    }
    sceIoDclose(dir);
}

void saves_scan(SyncState *state) {
    state->num_titles = 0;
    if (state->scan_vita_saves)
        scan_dir(state, VITA_SAVEDATA_PATH, PLATFORM_VITA);
    if (state->scan_psp_emu_saves)
        scan_dir(state, PSP_SAVEDATA_PATH, PLATFORM_PSP_EMU);
}

int saves_list_files(const TitleInfo *title,
                     char files[][MAX_FILE_LEN], uint32_t sizes[], int max_files) {
    int count = 0;
    SceUID dir = sceIoDopen(title->save_dir);
    if (dir < 0) return -1;

    SceIoDirent entry;
    while (sceIoDread(dir, &entry) > 0 && count < max_files) {
        if (!SCE_S_ISREG(entry.d_stat.st_mode)) continue;
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

    /* Sort file names (insertion sort) */
    for (int i = 1; i < n; i++) {
        char tmp[MAX_FILE_LEN];
        uint32_t tmp_size = sizes[i];
        strncpy(tmp, files[i], MAX_FILE_LEN);
        int j = i - 1;
        while (j >= 0 && strcmp(files[j], tmp) > 0) {
            strncpy(files[j+1], files[j], MAX_FILE_LEN);
            sizes[j+1] = sizes[j];
            j--;
        }
        strncpy(files[j+1], tmp, MAX_FILE_LEN);
        sizes[j+1] = tmp_size;
    }

    SHA256_CTX ctx;
    sha256_init(&ctx);

    static uint8_t file_buf[MAX_FILE_SIZE];

    for (int i = 0; i < n; i++) {
        char path[SAVE_DIR_LEN + MAX_FILE_LEN + 2];
        snprintf(path, sizeof(path), "%s/%s", title->save_dir, files[i]);

        SceUID fd = sceIoOpen(path, SCE_O_RDONLY, 0777);
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

    SceUID fd = sceIoOpen(path, SCE_O_RDONLY, 0777);
    if (fd < 0) return -1;

    int total = 0, bytes;
    while ((bytes = sceIoRead(fd, buf + total, buf_size - total)) > 0)
        total += bytes;
    sceIoClose(fd);
    return total;
}

int saves_write_file(const TitleInfo *title, const char *rel_path,
                     const uint8_t *data, uint32_t size) {
    sceIoMkdir(title->save_dir, 0777);
    char path[SAVE_DIR_LEN + MAX_FILE_LEN + 2];
    snprintf(path, sizeof(path), "%s/%s", title->save_dir, rel_path);

    SceUID fd = sceIoOpen(path, SCE_O_WRONLY | SCE_O_CREAT | SCE_O_TRUNC, 0777);
    if (fd < 0) return -1;

    int written = sceIoWrite(fd, data, size);
    sceIoClose(fd);
    return (written == (int)size) ? 0 : -1;
}
