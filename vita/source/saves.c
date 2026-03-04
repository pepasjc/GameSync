/*
 * Vita Save Sync - Save enumeration and I/O
 *
 * Native Vita saves:  ux0:user/00/savedata/<TITLEID>/  (e.g. PCSE00082)
 * PSP emu saves:      ux0:pspemu/PSP/SAVEDATA/<GAMEID>/ (e.g. ULUS10272)
 *
 * NOTE: sceIoDread() does NOT reliably fill d_stat.st_mode on the Vita.
 * Always use sceIoGetstat() to determine file vs directory.
 */

#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#include <ctype.h>
#include <psp2/io/fcntl.h>
#include <psp2/io/dirent.h>
#include <psp2/io/stat.h>

#include "saves.h"
#include "sha256.h"

/* --------------------------------------------------------------------------
 * Diagnostic log
 * Writes ux0:data/vitasync/diag.txt so it can be read from VitaShell.
 * -------------------------------------------------------------------------- */

static SceUID g_diag_fd = -1;

static void diag_open(void) {
    sceIoMkdir("ux0:data/vitasync", 0777);
    g_diag_fd = sceIoOpen("ux0:data/vitasync/diag.txt",
                           SCE_O_WRONLY | SCE_O_CREAT | SCE_O_TRUNC, 0777);
}

static void diag_write(const char *fmt, ...) {
    if (g_diag_fd < 0) return;
    char buf[256];
    va_list args;
    va_start(args, fmt);
    vsnprintf(buf, sizeof(buf), fmt, args);
    va_end(args);
    sceIoWrite(g_diag_fd, buf, strlen(buf));
}

static void diag_close(void) {
    if (g_diag_fd >= 0) {
        sceIoClose(g_diag_fd);
        g_diag_fd = -1;
    }
}

/* Returns true if path is a directory (via sceIoGetstat). */
static bool path_is_dir(const char *path) {
    SceIoStat st;
    if (sceIoGetstat(path, &st) < 0) return false;
    return SCE_S_ISDIR(st.st_mode);
}

/* Returns true if path is a regular file (via sceIoGetstat). */
static bool path_is_file(const char *path) {
    SceIoStat st;
    if (sceIoGetstat(path, &st) < 0) return false;
    return SCE_S_ISREG(st.st_mode);
}

/* Returns file size via sceIoGetstat, or 0 on error. */
static uint32_t path_file_size(const char *path) {
    SceIoStat st;
    if (sceIoGetstat(path, &st) < 0) return 0;
    return (uint32_t)st.st_size;
}

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
    diag_write("[scan_dir] sceIoDopen(\"%s\") = %d\n", base_path, (int)dir);
    if (dir < 0) return;

    SceIoDirent entry;
    int raw_count = 0;
    memset(&entry, 0, sizeof(entry));
    while (sceIoDread(dir, &entry) > 0) {
        raw_count++;
        diag_write("  entry[%d]: \"%s\"\n", raw_count, entry.d_name);

        if (entry.d_name[0] == '.') {
            diag_write("    -> skip (dot entry)\n");
            memset(&entry, 0, sizeof(entry)); continue;
        }

        /* Build full path and verify it's a directory via stat */
        char title_path[SAVE_DIR_LEN];
        snprintf(title_path, sizeof(title_path), "%s/%s", base_path, entry.d_name);

        SceIoStat st;
        int stat_ret = sceIoGetstat(title_path, &st);
        diag_write("    sceIoGetstat(\"%s\") = %d  st_mode=0x%x\n",
                   title_path, stat_ret, (unsigned)st.st_mode);

        if (!SCE_S_ISDIR(st.st_mode)) {
            diag_write("    -> skip (not a dir)\n");
            memset(&entry, 0, sizeof(entry)); continue;
        }

        char game_id[GAME_ID_LEN];
        strncpy(game_id, entry.d_name, GAME_ID_LEN - 1);
        game_id[GAME_ID_LEN - 1] = '\0';
        for (int i = 0; game_id[i]; i++)
            game_id[i] = toupper((unsigned char)game_id[i]);

        bool valid = (platform == PLATFORM_VITA)
                     ? saves_is_vita_game_id(game_id)
                     : saves_is_psp_game_id(game_id);
        if (!valid) {
            diag_write("    -> skip (invalid game ID \"%s\")\n", game_id);
            memset(&entry, 0, sizeof(entry)); continue;
        }
        if (state->num_titles >= MAX_TITLES) break;

        diag_write("    -> ACCEPTED game_id=\"%s\"\n", game_id);

        TitleInfo *t = &state->titles[state->num_titles];
        memset(t, 0, sizeof(TitleInfo));
        strncpy(t->game_id, game_id, GAME_ID_LEN - 1);
        strncpy(t->name, game_id, MAX_TITLE_LEN - 1);
        snprintf(t->save_dir, SAVE_DIR_LEN, "%s/%s", base_path, game_id);
        t->platform = platform;

        /* Count files and total size.
         * Use sceIoGetstat for each entry — d_stat from sceIoDread is unreliable. */
        SceUID save_dir = sceIoDopen(t->save_dir);
        diag_write("    sceIoDopen save_dir(\"%s\") = %d\n", t->save_dir, (int)save_dir);
        if (save_dir >= 0) {
            SceIoDirent fe;
            memset(&fe, 0, sizeof(fe));
            while (sceIoDread(save_dir, &fe) > 0) {
                if (fe.d_name[0] == '.') { memset(&fe, 0, sizeof(fe)); continue; }

                char fe_path[SAVE_DIR_LEN];
                snprintf(fe_path, sizeof(fe_path), "%s/%s", t->save_dir, fe.d_name);

                if (path_is_file(fe_path)) {
                    t->file_count++;
                    t->total_size += path_file_size(fe_path);
                } else if (path_is_dir(fe_path)) {
                    /* Recurse one level into subdirectory */
                    SceUID sub = sceIoDopen(fe_path);
                    if (sub >= 0) {
                        SceIoDirent se;
                        memset(&se, 0, sizeof(se));
                        while (sceIoDread(sub, &se) > 0) {
                            if (se.d_name[0] == '.') { memset(&se, 0, sizeof(se)); continue; }
                            char se_path[SAVE_DIR_LEN];
                            snprintf(se_path, sizeof(se_path), "%s/%s", fe_path, se.d_name);
                            if (path_is_file(se_path)) {
                                t->file_count++;
                                t->total_size += path_file_size(se_path);
                            }
                            memset(&se, 0, sizeof(se));
                        }
                        sceIoDclose(sub);
                    }
                }
                memset(&fe, 0, sizeof(fe));
            }
            sceIoDclose(save_dir);
        }
        diag_write("    file_count=%d total_size=%u\n", t->file_count, t->total_size);

        /* Always add the title — even if file count is 0 (stat may fail on encrypted saves) */
        state->num_titles++;
        memset(&entry, 0, sizeof(entry));
    }
    diag_write("[scan_dir] done: raw_count=%d\n", raw_count);
    sceIoDclose(dir);
}

void saves_scan(SyncState *state) {
    state->num_titles = 0;
    diag_open();
    diag_write("saves_scan start  scan_vita=%d scan_psp=%d\n",
               state->scan_vita_saves, state->scan_psp_emu_saves);
    if (state->scan_vita_saves)
        scan_dir(state, VITA_SAVEDATA_PATH, PLATFORM_VITA);
    if (state->scan_psp_emu_saves)
        scan_dir(state, PSP_SAVEDATA_PATH, PLATFORM_PSP_EMU);
    diag_write("saves_scan done  num_titles=%d\n", state->num_titles);
    diag_close();
}

int saves_list_files(const TitleInfo *title,
                     char files[][MAX_FILE_LEN], uint32_t sizes[], int max_files) {
    int count = 0;
    SceUID dir = sceIoDopen(title->save_dir);
    if (dir < 0) return -1;

    SceIoDirent entry;
    memset(&entry, 0, sizeof(entry));
    while (sceIoDread(dir, &entry) > 0 && count < max_files) {
        if (entry.d_name[0] == '.') { memset(&entry, 0, sizeof(entry)); continue; }

        char entry_path[SAVE_DIR_LEN];
        snprintf(entry_path, sizeof(entry_path), "%s/%s", title->save_dir, entry.d_name);

        if (path_is_file(entry_path)) {
            strncpy(files[count], entry.d_name, MAX_FILE_LEN - 1);
            files[count][MAX_FILE_LEN - 1] = '\0';
            sizes[count] = path_file_size(entry_path);
            count++;
        } else if (path_is_dir(entry_path)) {
            /* Recurse one level — store as "subdir/filename" */
            SceUID sub = sceIoDopen(entry_path);
            if (sub >= 0) {
                SceIoDirent se;
                memset(&se, 0, sizeof(se));
                while (sceIoDread(sub, &se) > 0 && count < max_files) {
                    if (se.d_name[0] == '.') { memset(&se, 0, sizeof(se)); continue; }
                    char se_path[SAVE_DIR_LEN];
                    snprintf(se_path, sizeof(se_path), "%s/%s", entry_path, se.d_name);
                    if (path_is_file(se_path)) {
                        snprintf(files[count], MAX_FILE_LEN, "%s/%s",
                                 entry.d_name, se.d_name);
                        sizes[count] = path_file_size(se_path);
                        count++;
                    }
                    memset(&se, 0, sizeof(se));
                }
                sceIoDclose(sub);
            }
        }
        memset(&entry, 0, sizeof(entry));
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

    /* If rel_path contains a slash, create the intermediate subdirectory */
    char path[SAVE_DIR_LEN + MAX_FILE_LEN + 2];
    snprintf(path, sizeof(path), "%s/%s", title->save_dir, rel_path);
    char *slash = strrchr(path, '/');
    if (slash && slash != path) {
        char parent[SAVE_DIR_LEN + MAX_FILE_LEN + 2];
        size_t parent_len = (size_t)(slash - path);
        if (parent_len < sizeof(parent)) {
            memcpy(parent, path, parent_len);
            parent[parent_len] = '\0';
            sceIoMkdir(parent, 0777);
        }
    }

    SceUID fd = sceIoOpen(path, SCE_O_WRONLY | SCE_O_CREAT | SCE_O_TRUNC, 0777);
    if (fd < 0) return -1;

    int written = sceIoWrite(fd, data, size);
    sceIoClose(fd);
    return (written == (int)size) ? 0 : -1;
}
