#include "saves.h"

#include "apollo.h"
#include "hash.h"
#include "state.h"

#include <ctype.h>
#include <dirent.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>

static bool is_dot_name(const char *name) {
    return strcmp(name, ".") == 0 || strcmp(name, "..") == 0;
}

static void path_join(const char *base, const char *name, char *out, size_t out_size) {
    const size_t base_len = strlen(base);
    snprintf(
        out,
        out_size,
        "%s%s%s",
        base,
        (base_len > 0 && base[base_len - 1] == '/') ? "" : "/",
        name
    );
}

static bool path_stat(const char *path, struct stat *st) {
    return stat(path, st) == 0;
}

static bool path_is_dir(const char *path) {
    struct stat st;
    return path_stat(path, &st) && S_ISDIR(st.st_mode);
}

static bool path_is_regular(const char *path, uint32_t *size_out) {
    struct stat st;
    if (!path_stat(path, &st) || !S_ISREG(st.st_mode)) {
        return false;
    }
    if (size_out) {
        if (st.st_size < 0) {
            *size_out = 0;
        } else if ((unsigned long long)st.st_size > 0xFFFFFFFFULL) {
            *size_out = 0xFFFFFFFFU;
        } else {
            *size_out = (uint32_t)st.st_size;
        }
    }
    return true;
}

static void uppercase_copy(char *out, size_t out_size, const char *value) {
    size_t i;
    if (out_size == 0) {
        return;
    }
    for (i = 0; value[i] != '\0' && i + 1 < out_size; i++) {
        out[i] = (char)toupper((unsigned char)value[i]);
    }
    out[i] = '\0';
}

static bool title_exists(const SyncState *state, const char *title_id) {
    int i;
    for (i = 0; i < state->num_titles; i++) {
        if (strcmp(state->titles[i].title_id, title_id) == 0) {
            return true;
        }
    }
    return false;
}

static bool collect_dir_stats(const char *path, uint32_t *total_size, int *file_count) {
    DIR *dir;
    struct dirent *entry;

    dir = opendir(path);
    if (!dir) {
        return false;
    }

    while ((entry = readdir(dir)) != NULL) {
        char child_path[PATH_LEN];
        struct stat st;

        if (is_dot_name(entry->d_name)) {
            continue;
        }

        path_join(path, entry->d_name, child_path, sizeof(child_path));
        if (!path_stat(child_path, &st)) {
            continue;
        }

        if (S_ISDIR(st.st_mode)) {
            if (!collect_dir_stats(child_path, total_size, file_count)) {
                closedir(dir);
                return false;
            }
        } else if (S_ISREG(st.st_mode)) {
            (*file_count)++;
            if (st.st_size > 0) {
                unsigned long long new_size = (unsigned long long)(*total_size)
                    + (unsigned long long)st.st_size;
                *total_size = new_size > 0xFFFFFFFFULL ? 0xFFFFFFFFU : (uint32_t)new_size;
            }
        }
    }

    closedir(dir);
    return true;
}

static void add_ps3_title(
    SyncState *state,
    const char *dir_name,
    const char *dir_path,
    uint32_t total_size,
    int file_count
) {
    TitleInfo *title;
    char game_code[16];
    SaveKind kind;

    if (state->num_titles >= MAX_TITLES) {
        return;
    }

    /* Extract game code and detect platform; skip PSP and PS2 saves */
    uppercase_copy(game_code, sizeof(game_code), dir_name);
    game_code[9] = '\0'; /* clamp to 9 chars for detection */
    kind = apollo_detect_save_kind(game_code);
    if (kind == SAVE_KIND_PSP || kind == SAVE_KIND_PS2) {
        return;
    }

    title = &state->titles[state->num_titles++];
    memset(title, 0, sizeof(*title));
    uppercase_copy(title->title_id, sizeof(title->title_id), dir_name);
    apollo_extract_game_code(title->title_id, title->game_code, sizeof(title->game_code));
    strncpy(title->name, dir_name, sizeof(title->name) - 1);
    strncpy(title->local_path, dir_path, sizeof(title->local_path) - 1);
    title->kind = kind;
    title->total_size = total_size;
    title->file_count = file_count;
}

static void add_ps1_title(
    SyncState *state,
    const char *title_id,
    const char *file_name,
    const char *file_path,
    uint32_t total_size
) {
    TitleInfo *title;

    if (state->num_titles >= MAX_TITLES || title_exists(state, title_id)) {
        return;
    }

    title = &state->titles[state->num_titles++];
    memset(title, 0, sizeof(*title));
    strncpy(title->title_id, title_id, sizeof(title->title_id) - 1);
    strncpy(title->game_code, title_id, sizeof(title->game_code) - 1);
    strncpy(title->name, file_name, sizeof(title->name) - 1);
    strncpy(title->local_path, file_path, sizeof(title->local_path) - 1);
    title->kind = SAVE_KIND_PS1_VM1;
    title->total_size = total_size;
    title->file_count = 1;
}

static void scan_ps3_savedata_root(SyncState *state, const char *root_path) {
    DIR *dir;
    struct dirent *entry;

    if (!path_is_dir(root_path)) {
        return;
    }

    dir = opendir(root_path);
    if (!dir) {
        return;
    }

    while ((entry = readdir(dir)) != NULL && state->num_titles < MAX_TITLES) {
        char save_path[PATH_LEN];
        uint32_t total_size = 0;
        int file_count = 0;

        if (is_dot_name(entry->d_name) || !apollo_is_ps3_save_dir(entry->d_name)) {
            continue;
        }

        path_join(root_path, entry->d_name, save_path, sizeof(save_path));
        if (!path_is_dir(save_path)) {
            continue;
        }
        if (!collect_dir_stats(save_path, &total_size, &file_count)) {
            continue;
        }

        add_ps3_title(state, entry->d_name, save_path, total_size, file_count);
    }

    closedir(dir);
}

static void scan_ps1_vmc_root(SyncState *state, const char *root_path) {
    DIR *dir;
    struct dirent *entry;

    if (!path_is_dir(root_path)) {
        return;
    }

    dir = opendir(root_path);
    if (!dir) {
        return;
    }

    while ((entry = readdir(dir)) != NULL && state->num_titles < MAX_TITLES) {
        char title_id[GAME_ID_LEN];
        char vm1_path[PATH_LEN];
        uint32_t total_size = 0;

        if (is_dot_name(entry->d_name) || !apollo_is_ps1_vm1_file(entry->d_name)) {
            continue;
        }
        if (!apollo_extract_ps1_title_id(entry->d_name, title_id, sizeof(title_id))) {
            continue;
        }

        path_join(root_path, entry->d_name, vm1_path, sizeof(vm1_path));
        if (!path_is_regular(vm1_path, &total_size)) {
            continue;
        }

        add_ps1_title(state, title_id, entry->d_name, vm1_path, total_size);
    }

    closedir(dir);
}

void saves_scan(SyncState *state) {
    char root_path[PATH_LEN];
    int usb_index;

    state->num_titles = 0;

    if (state->scan_ps3) {
        apollo_get_ps3_savedata_root(state, root_path, sizeof(root_path));
        scan_ps3_savedata_root(state, root_path);
    }

    if (!state->scan_ps1) {
        return;
    }

    apollo_get_ps1_vmc_root(root_path, sizeof(root_path));
    scan_ps1_vmc_root(state, root_path);

    for (usb_index = 0; usb_index < 8 && state->num_titles < MAX_TITLES; usb_index++) {
        apollo_get_ps1_usb_vmc_root(usb_index, root_path, sizeof(root_path));
        scan_ps1_vmc_root(state, root_path);
    }
}

bool saves_calculate_hash(TitleInfo *title) {
    char cached_hex[65];
    char computed_hex[65];

    if (!title) {
        return false;
    }

    if (state_get_cached_hash(title->title_id, title->file_count, title->total_size, cached_hex)
            && hash_from_hex(cached_hex, title->hash)) {
        title->hash_calculated = true;
        return true;
    }

    if (title->kind == SAVE_KIND_PS3) {
        int file_count = 0;
        uint32_t total_size = 0;
        if (!hash_dir_files_sha256(title->local_path, title->hash, &file_count, &total_size)) {
            return false;
        }
        title->file_count = file_count;
        title->total_size = total_size;
    } else if (title->kind == SAVE_KIND_PS1_VM1) {
        if (!hash_file_sha256(title->local_path, title->hash, &title->total_size)) {
            return false;
        }
        title->file_count = 1;
    } else {
        return false;
    }

    hash_to_hex(title->hash, computed_hex);
    state_set_cached_hash(title->title_id, title->file_count, title->total_size, computed_hex);
    title->hash_calculated = true;
    return true;
}

/* ---- New functions for bundle/sync ---- */

int saves_compute_hash(TitleInfo *title) {
    return saves_calculate_hash(title) ? 0 : -1;
}

/* Compare function for qsort */
static int cmp_names(const void *a, const void *b) {
    return strcmp((const char *)a, (const char *)b);
}

int saves_list_files(const TitleInfo *title,
                     char names[][MAX_FILE_LEN], uint32_t *sizes, int max) {
    if (!title || max <= 0) return 0;

    if (title->kind == SAVE_KIND_PS1_VM1) {
        /* Single file — use filename portion of local_path */
        const char *slash = strrchr(title->local_path, '/');
        const char *fname = slash ? slash + 1 : title->local_path;
        strncpy(names[0], fname, MAX_FILE_LEN - 1);
        names[0][MAX_FILE_LEN - 1] = '\0';
        struct stat st;
        sizes[0] = (stat(title->local_path, &st) == 0) ? (uint32_t)st.st_size : 0;
        return 1;
    }

    /* PS3: enumerate regular files directly in the save directory */
    DIR *dir = opendir(title->local_path);
    if (!dir) return 0;

    int count = 0;
    struct dirent *entry;
    while ((entry = readdir(dir)) != NULL && count < max) {
        char child[PATH_LEN];
        struct stat st;

        if (is_dot_name(entry->d_name)) continue;
        path_join(title->local_path, entry->d_name, child, sizeof(child));
        if (stat(child, &st) != 0 || !S_ISREG(st.st_mode)) continue;

        strncpy(names[count], entry->d_name, MAX_FILE_LEN - 1);
        names[count][MAX_FILE_LEN - 1] = '\0';
        sizes[count] = (uint32_t)st.st_size;
        count++;
    }
    closedir(dir);

    /* Sort by name for stable hash ordering */
    if (count > 1) {
        /* Simple insertion sort (avoids wide qsort struct) */
        for (int i = 1; i < count; i++) {
            char tmp_name[MAX_FILE_LEN];
            uint32_t tmp_size = sizes[i];
            strncpy(tmp_name, names[i], MAX_FILE_LEN);
            int j = i - 1;
            while (j >= 0 && strcmp(names[j], tmp_name) > 0) {
                strncpy(names[j + 1], names[j], MAX_FILE_LEN);
                sizes[j + 1] = sizes[j];
                j--;
            }
            strncpy(names[j + 1], tmp_name, MAX_FILE_LEN);
            sizes[j + 1] = tmp_size;
        }
    }
    (void)cmp_names;  /* suppress unused warning */
    return count;
}

int saves_read_file(const TitleInfo *title, const char *name,
                    uint8_t *buf, uint32_t buf_size) {
    char path[PATH_LEN];
    if (title->kind == SAVE_KIND_PS1_VM1) {
        strncpy(path, title->local_path, sizeof(path) - 1);
        path[sizeof(path) - 1] = '\0';
    } else {
        snprintf(path, sizeof(path), "%s/%s", title->local_path, name);
    }
    FILE *f = fopen(path, "rb");
    if (!f) return -1;
    int n = (int)fread(buf, 1, buf_size, f);
    fclose(f);
    return n;
}

int saves_write_file(const TitleInfo *title, const char *name,
                     const uint8_t *buf, uint32_t size) {
    char path[PATH_LEN];
    if (title->kind == SAVE_KIND_PS1_VM1) {
        /* Ensure parent directory exists */
        char dir_path[PATH_LEN];
        strncpy(dir_path, title->local_path, sizeof(dir_path) - 1);
        char *slash = strrchr(dir_path, '/');
        if (slash) { *slash = '\0'; mkdir(dir_path, 0755); }
        strncpy(path, title->local_path, sizeof(path) - 1);
        path[sizeof(path) - 1] = '\0';
    } else {
        mkdir(title->local_path, 0755);
        snprintf(path, sizeof(path), "%s/%s", title->local_path, name);
    }
    FILE *f = fopen(path, "wb");
    if (!f) return -1;
    fwrite(buf, 1, size, f);
    fclose(f);
    return 0;
}

bool saves_is_relevant_game_code(const char *id) {
    SaveKind kind;
    if (!id) return false;
    size_t len = strlen(id);
    if (len < 9) return false;
    for (int i = 0; i < 4; i++)
        if (!isupper((unsigned char)id[i])) return false;
    for (int i = 4; i < 9; i++)
        if (!isdigit((unsigned char)id[i])) return false;
    /* Only keep PS3 and PS1 saves */
    kind = apollo_detect_save_kind(id);
    return kind == SAVE_KIND_PS3 || kind == SAVE_KIND_PS1 || kind == SAVE_KIND_PS1_VM1;
}
