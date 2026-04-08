#include "saves.h"

#include "apollo.h"
#include "decrypt.h"
#include "export_zip.h"
#include "hash.h"
#include "state.h"
#include "ui.h"

#include "debug.h"

#include <ctype.h>
#include <dirent.h>
#include <stdio.h>
#include <stdlib.h>
#include <strings.h>
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

static void sort_name_entries(char names[][MAX_FILE_LEN], uint32_t *sizes, int count) {
    if (count <= 1) {
        return;
    }

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

static int collect_relative_files(
    const char *root_path,
    const char *current_path,
    const char *prefix,
    char names[][MAX_FILE_LEN],
    uint32_t *sizes,
    int max,
    int count
) {
    DIR *dir = opendir(current_path);
    if (!dir) {
        return count;
    }

    struct dirent *entry;
    while ((entry = readdir(dir)) != NULL && count < max) {
        char child_path[PATH_LEN];
        char rel_path[MAX_FILE_LEN];
        struct stat st;

        if (is_dot_name(entry->d_name)) {
            continue;
        }

        path_join(current_path, entry->d_name, child_path, sizeof(child_path));
        if (!path_stat(child_path, &st)) {
            continue;
        }

        if (prefix && prefix[0]) {
            snprintf(rel_path, sizeof(rel_path), "%s/%s", prefix, entry->d_name);
        } else {
            snprintf(rel_path, sizeof(rel_path), "%s", entry->d_name);
        }
        rel_path[sizeof(rel_path) - 1] = '\0';

        if (S_ISDIR(st.st_mode)) {
            count = collect_relative_files(
                root_path, child_path, rel_path, names, sizes, max, count
            );
        } else if (S_ISREG(st.st_mode)) {
            strncpy(names[count], rel_path, MAX_FILE_LEN - 1);
            names[count][MAX_FILE_LEN - 1] = '\0';
            if (st.st_size < 0) {
                sizes[count] = 0;
            } else if ((unsigned long long)st.st_size > 0xFFFFFFFFULL) {
                sizes[count] = 0xFFFFFFFFU;
            } else {
                sizes[count] = (uint32_t)st.st_size;
            }
            count++;
        }
    }

    closedir(dir);
    (void)root_path;
    return count;
}

static void mkdir_parents_for_file(const char *file_path) {
    char tmp[PATH_LEN];
    size_t len;

    strncpy(tmp, file_path, sizeof(tmp) - 1);
    tmp[sizeof(tmp) - 1] = '\0';
    len = strlen(tmp);

    for (size_t i = 1; i < len; i++) {
        if (tmp[i] != '/') {
            continue;
        }
        tmp[i] = '\0';
        mkdir(tmp, 0700);
        tmp[i] = '/';
    }
}

static int chmod_tree(const char *root_path) {
    DIR *dir;
    struct dirent *entry;

    if (!root_path || !root_path[0]) {
        return -1;
    }
    if (chmod(root_path, 0700) != 0) {
        return -1;
    }

    dir = opendir(root_path);
    if (!dir) {
        return -1;
    }

    while ((entry = readdir(dir)) != NULL) {
        char child_path[PATH_LEN];
        struct stat st;

        if (is_dot_name(entry->d_name)) {
            continue;
        }

        path_join(root_path, entry->d_name, child_path, sizeof(child_path));
        if (!path_stat(child_path, &st)) {
            continue;
        }

        if (S_ISDIR(st.st_mode)) {
            chmod_tree(child_path);
        } else if (S_ISREG(st.st_mode)) {
            chmod(child_path, 0644);
        }
    }

    closedir(dir);
    return 0;
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

static int find_title_index(const SyncState *state, const char *title_id) {
    int i;
    for (i = 0; i < state->num_titles; i++) {
        if (strcmp(state->titles[i].title_id, title_id) == 0) {
            return i;
        }
    }
    return -1;
}

static void canonicalize_slot_suffix(
    const char *title_id,
    char *out,
    size_t out_size
) {
    size_t pos = 0;
    const char *suffix = title_id;

    if (!title_id || !out || out_size == 0) {
        return;
    }

    if (strlen(title_id) > 9) {
        suffix = title_id + 9;
    }

    while (*suffix && pos + 1 < out_size) {
        unsigned char c = (unsigned char)*suffix++;
        if (!isalnum(c)) {
            continue;
        }
        out[pos++] = (char)toupper(c);
    }
    out[pos] = '\0';
}

static int find_matching_export_title(const SyncState *state, const char *export_title_id) {
    char export_code[16];
    char export_suffix[GAME_ID_LEN];
    int same_code_idx = -1;
    int same_code_count = 0;

    if (!apollo_extract_game_code(export_title_id, export_code, sizeof(export_code))) {
        return -1;
    }
    canonicalize_slot_suffix(export_title_id, export_suffix, sizeof(export_suffix));

    for (int i = 0; i < state->num_titles; i++) {
        char local_suffix[GAME_ID_LEN];

        if (state->titles[i].kind != SAVE_KIND_PS3) {
            continue;
        }
        if (strcmp(state->titles[i].title_id, export_title_id) == 0) {
            return i;
        }
        if (strcmp(state->titles[i].game_code, export_code) != 0) {
            continue;
        }

        same_code_count++;
        same_code_idx = i;

        canonicalize_slot_suffix(state->titles[i].title_id, local_suffix, sizeof(local_suffix));
        if (export_suffix[0] && local_suffix[0] && strcmp(local_suffix, export_suffix) == 0) {
            return i;
        }
    }

    return same_code_count == 1 ? same_code_idx : -1;
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

static bool collect_dir_hash_stats_recursive(
    const char *path,
    const char *rel_prefix,
    uint32_t *total_size,
    int *file_count
) {
    DIR *dir;
    struct dirent *entry;

    dir = opendir(path);
    if (!dir) {
        return false;
    }

    while ((entry = readdir(dir)) != NULL) {
        char child_path[PATH_LEN];
        char rel_path[MAX_FILE_LEN];
        struct stat st;

        if (is_dot_name(entry->d_name)) {
            continue;
        }

        path_join(path, entry->d_name, child_path, sizeof(child_path));
        if (rel_prefix && rel_prefix[0]) {
            snprintf(rel_path, sizeof(rel_path), "%s/%s", rel_prefix, entry->d_name);
        } else {
            snprintf(rel_path, sizeof(rel_path), "%s", entry->d_name);
        }
        rel_path[sizeof(rel_path) - 1] = '\0';

        if (!path_stat(child_path, &st)) {
            continue;
        }

        if (S_ISDIR(st.st_mode)) {
            if (!collect_dir_hash_stats_recursive(child_path, rel_path, total_size, file_count)) {
                closedir(dir);
                return false;
            }
        } else if (S_ISREG(st.st_mode)) {
            if (hash_should_skip_ps3_file(rel_path)) {
                continue;
            }
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

static bool collect_dir_hash_stats(const char *path, uint32_t *total_size, int *file_count) {
    if (total_size) {
        *total_size = 0;
    }
    if (file_count) {
        *file_count = 0;
    }
    return collect_dir_hash_stats_recursive(path, "", total_size, file_count);
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
    title->hash_total_size = total_size;
    title->hash_file_count = file_count;
    if (kind == SAVE_KIND_PS3) {
        collect_dir_hash_stats(dir_path, &title->hash_total_size, &title->hash_file_count);
    }
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
    strncpy(title->upload_path, file_path, sizeof(title->upload_path) - 1);
    title->kind = SAVE_KIND_PS1_VM1;
    title->total_size = total_size;
    title->file_count = 1;
    title->hash_total_size = total_size;
    title->hash_file_count = 1;
}

static void attach_export_zip(
    SyncState *state,
    const ExportZipInfo *zip_info,
    const char *zip_path
) {
    int idx;
    TitleInfo *title;

    if (!state || !zip_info || !zip_path) {
        return;
    }

    idx = find_matching_export_title(state, zip_info->title_id);
    if (idx >= 0) {
        title = &state->titles[idx];
        if (title->local_path[0] != '\0' && path_is_dir(title->local_path)) {
            debug_log("scan: keeping HDD save as hash source for %s; ignoring export zip %s",
                      title->title_id, zip_path);
            return;
        }
    } else {
        if (state->num_titles >= MAX_TITLES) {
            return;
        }
        title = &state->titles[state->num_titles++];
        memset(title, 0, sizeof(*title));
        strncpy(title->title_id, zip_info->title_id, sizeof(title->title_id) - 1);
        apollo_extract_game_code(title->title_id, title->game_code, sizeof(title->game_code));
        strncpy(title->name, title->title_id, sizeof(title->name) - 1);
        snprintf(title->local_path, sizeof(title->local_path), "%s/%s",
                 state->savedata_root[0] ? state->savedata_root
                                         : "/dev_hdd0/home/00000001/savedata",
                 title->title_id);
        title->kind = SAVE_KIND_PS3;
        title->status = TITLE_STATUS_LOCAL_ONLY;
    }

    strncpy(title->upload_path, zip_path, sizeof(title->upload_path) - 1);
    title->upload_is_zip = true;
    title->file_count = zip_info->file_count;
    title->total_size = zip_info->total_size;
    title->hash_file_count = zip_info->file_count;
    title->hash_total_size = zip_info->total_size;
    export_zip_comparable_stats(zip_path, &title->hash_file_count, &title->hash_total_size);
    title->hash_calculated = false;
}

static void attach_export_dir(
    SyncState *state,
    const char *title_id,
    const char *dir_path,
    uint32_t total_size,
    int file_count
) {
    int idx;
    TitleInfo *title;

    if (!state || !title_id || !dir_path) {
        return;
    }

    idx = find_matching_export_title(state, title_id);
    if (idx < 0) {
        idx = find_title_index(state, title_id);
    }

    if (idx >= 0) {
        title = &state->titles[idx];
        if (title->local_path[0] != '\0' && path_is_dir(title->local_path)) {
            debug_log("scan: keeping HDD save as hash source for %s; ignoring usb dir %s",
                      title->title_id, dir_path);
            return;
        }
    } else {
        if (state->num_titles >= MAX_TITLES) {
            return;
        }
        title = &state->titles[state->num_titles++];
        memset(title, 0, sizeof(*title));
        strncpy(title->title_id, title_id, sizeof(title->title_id) - 1);
        apollo_extract_game_code(title->title_id, title->game_code, sizeof(title->game_code));
        strncpy(title->name, title->title_id, sizeof(title->name) - 1);
        snprintf(title->local_path, sizeof(title->local_path), "%s/%s",
                 state->savedata_root[0] ? state->savedata_root
                                         : "/dev_hdd0/home/00000001/savedata",
                 title->title_id);
        title->kind = SAVE_KIND_PS3;
        title->status = TITLE_STATUS_LOCAL_ONLY;
    }

    strncpy(title->upload_path, dir_path, sizeof(title->upload_path) - 1);
    title->upload_is_zip = false;
    title->file_count = file_count;
    title->total_size = total_size;
    title->hash_total_size = total_size;
    title->hash_file_count = file_count;
    collect_dir_hash_stats(dir_path, &title->hash_total_size, &title->hash_file_count);
    title->hash_calculated = false;
}

static void scan_ps3_export_root(SyncState *state, const char *root_path) {
    DIR *dir;
    struct dirent *entry;

    if (!path_is_dir(root_path)) {
        return;
    }

    dir = opendir(root_path);
    if (!dir) {
        return;
    }

    debug_log("scan: scanning exports %s", root_path);

    while ((entry = readdir(dir)) != NULL) {
        char zip_path[PATH_LEN];
        const char *ext = strrchr(entry->d_name, '.');
        ExportZipInfo *info;

        if (is_dot_name(entry->d_name) || !ext) {
            continue;
        }
        if (strcasecmp(ext, ".zip") != 0) {
            continue;
        }

        path_join(root_path, entry->d_name, zip_path, sizeof(zip_path));
        if (!path_is_regular(zip_path, NULL)) {
            continue;
        }
        info = (ExportZipInfo *)malloc(sizeof(*info));
        if (!info) {
            continue;
        }
        if (!export_zip_parse(zip_path, info)) {
            debug_log("scan: skip invalid export zip: %s", zip_path);
            free(info);
            continue;
        }

        debug_log("scan: export %s -> %s (%d files)", entry->d_name, info->title_id, info->file_count);
        attach_export_zip(state, info, zip_path);
        free(info);
    }

    closedir(dir);
}

static void scan_ps3_usb_savedata_root(SyncState *state, const char *root_path) {
    DIR *dir;
    struct dirent *entry;

    if (!path_is_dir(root_path)) {
        return;
    }

    dir = opendir(root_path);
    if (!dir) {
        return;
    }

    debug_log("scan: scanning usb savedata %s", root_path);

    while ((entry = readdir(dir)) != NULL && state->num_titles < MAX_TITLES) {
        char child_path[PATH_LEN];
        uint32_t total_size = 0;
        int file_count = 0;

        if (is_dot_name(entry->d_name)) {
            continue;
        }
        if (!apollo_is_ps3_save_dir(entry->d_name)) {
            continue;
        }

        path_join(root_path, entry->d_name, child_path, sizeof(child_path));
        if (!path_is_dir(child_path)) {
            continue;
        }
        if (!collect_dir_stats(child_path, &total_size, &file_count)) {
            continue;
        }
        attach_export_dir(state, entry->d_name, child_path, total_size, file_count);
        debug_log("scan: usb savedata %s (%d files)", entry->d_name, file_count);
    }

    closedir(dir);
}

static void scan_ps3_savedata_root(SyncState *state, const char *root_path) {
    DIR *dir;
    struct dirent *entry;

    if (!path_is_dir(root_path)) {
        debug_log("scan: root not a dir: %s", root_path);
        return;
    }

    dir = opendir(root_path);
    if (!dir) {
        debug_log("scan: opendir failed: %s", root_path);
        return;
    }

    debug_log("scan: scanning %s", root_path);

    while ((entry = readdir(dir)) != NULL && state->num_titles < MAX_TITLES) {
        char save_path[PATH_LEN];
        uint32_t total_size = 0;
        int file_count = 0;

        if (is_dot_name(entry->d_name)) continue;

        if (!apollo_is_ps3_save_dir(entry->d_name)) {
            debug_log("scan: skip (bad name): %s", entry->d_name);
            continue;
        }

        path_join(root_path, entry->d_name, save_path, sizeof(save_path));
        if (!path_is_dir(save_path)) {
            debug_log("scan: skip (not dir): %s", entry->d_name);
            continue;
        }
        if (!collect_dir_stats(save_path, &total_size, &file_count)) {
            debug_log("scan: skip (stat failed): %s", entry->d_name);
            continue;
        }

        char game_code[16];
        uppercase_copy(game_code, sizeof(game_code), entry->d_name);
        game_code[9] = '\0';
        SaveKind kind = apollo_detect_save_kind(game_code);
        if (kind == SAVE_KIND_PSP || kind == SAVE_KIND_PS2) {
            debug_log("scan: skip (PSP/PS2): %s", entry->d_name);
            continue;
        }

        debug_log("scan: add kind=%d: %s", (int)kind, entry->d_name);
        add_ps3_title(state, entry->d_name, save_path, total_size, file_count);
    }

    debug_log("scan: done, total=%d", state->num_titles);
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
        if (state->selected_user > 0) {
            /* Use the explicitly selected user */
            snprintf(root_path, sizeof(root_path),
                     "/dev_hdd0/home/%08d/savedata", state->selected_user);
            strncpy(state->savedata_root, root_path, sizeof(state->savedata_root) - 1);
            scan_ps3_savedata_root(state, root_path);
        } else {
            /* Auto-detect: use the first user directory that exists */
            int found_any = 0;
            for (int uid = 1; uid <= 16; uid++) {
                snprintf(root_path, sizeof(root_path),
                         "/dev_hdd0/home/%08d/savedata", uid);
                if (path_is_dir(root_path)) {
                    state->selected_user = uid;
                    snprintf(state->ps3_user, sizeof(state->ps3_user),
                             "%08d", uid);
                    strncpy(state->savedata_root, root_path,
                            sizeof(state->savedata_root) - 1);
                    found_any = 1;
                    break;
                }
            }
            if (!found_any) {
                apollo_get_ps3_savedata_root(state, root_path, sizeof(root_path));
                strncpy(state->savedata_root, root_path, sizeof(state->savedata_root) - 1);
            }
            scan_ps3_savedata_root(state, root_path);
        }
    }

    if (state->scan_ps3) {
        for (usb_index = 0; usb_index < 8 && state->num_titles < MAX_TITLES; usb_index++) {
            apollo_get_ps3_usb_savedata_root(usb_index, root_path, sizeof(root_path));
            scan_ps3_usb_savedata_root(state, root_path);
        }
    }

    if (state->scan_ps3) {
        for (usb_index = 0; usb_index < 8 && state->num_titles < MAX_TITLES; usb_index++) {
            apollo_get_ps3_export_root(usb_index, root_path, sizeof(root_path));
            scan_ps3_export_root(state, root_path);
        }
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
    int cache_file_count;
    uint32_t cache_total_size;
    const char *ps3_hash_source;
    char decrypt_temp[PATH_LEN];
    bool did_decrypt = false;

    if (!title) {
        return false;
    }

    saves_get_hash_cache_key_stats(title, &cache_file_count, &cache_total_size);
    if (state_get_cached_hash(title->title_id, cache_file_count, cache_total_size, cached_hex)
            && hash_from_hex(cached_hex, title->hash)) {
        ui_status("Using cached hash: %s", title->game_code);
        title->hash_calculated = true;
        return true;
    }

    if (title->kind == SAVE_KIND_PS3) {
        ps3_hash_source = title->upload_path[0] ? title->upload_path : title->local_path;
        if (!ps3_hash_source[0]) {
            return false;
        }
        if (!title->upload_is_zip && !title->upload_path[0] && title->local_path[0]) {
            decrypt_temp[0] = '\0';
            if (decrypt_save(title, decrypt_temp, sizeof(decrypt_temp)) != 0 || !decrypt_temp[0]) {
                debug_log("hash: decrypt_save failed for %s", title->title_id);
                return false;
            }
            ps3_hash_source = decrypt_temp;
            did_decrypt = true;
        }
        if (title->upload_is_zip) {
            int file_count = 0;
            uint32_t total_size = 0;
            if (!export_zip_hash_files_sha256(ps3_hash_source, title->hash, &file_count, &total_size)) {
                return false;
            }
            title->hash_file_count = file_count;
            title->hash_total_size = total_size;
            title->file_count = file_count;
            title->total_size = total_size;
        } else {
            int file_count = 0;
            uint32_t total_size = 0;
            if (!hash_dir_files_sha256(ps3_hash_source, title->hash, &file_count, &total_size)) {
                if (did_decrypt) {
                    decrypt_cleanup(decrypt_temp);
                }
                return false;
            }
            title->hash_file_count = file_count;
            title->hash_total_size = total_size;
            title->file_count = file_count;
            title->total_size = total_size;
        }
        if (did_decrypt) {
            decrypt_cleanup(decrypt_temp);
        }
    } else if (title->kind == SAVE_KIND_PS1_VM1) {
        if (!hash_file_sha256(title->local_path, title->hash, &title->total_size)) {
            return false;
        }
        title->file_count = 1;
        title->hash_file_count = 1;
        title->hash_total_size = title->total_size;
    } else {
        return false;
    }

    ui_status("Writing hash cache: %s", title->game_code);
    hash_to_hex(title->hash, computed_hex);
    state_set_cached_hash(title->title_id, title->hash_file_count, title->hash_total_size, computed_hex);
    ui_status("Finished hash cache: %s", title->game_code);
    title->hash_calculated = true;
    return true;
}

/* ---- New functions for bundle/sync ---- */

int saves_compute_hash(TitleInfo *title) {
    ui_status("Starting save hash: %s", title->game_code);
    return saves_calculate_hash(title) ? 0 : -1;
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

    /* PS3: enumerate regular files recursively within the save directory */
    if (title->upload_is_zip) {
        return export_zip_list_files(title->upload_path, names, sizes, max);
    }
    int count = collect_relative_files(
        title->upload_path[0] ? title->upload_path : title->local_path,
        title->upload_path[0] ? title->upload_path : title->local_path,
        "",
        names,
        sizes,
        max,
        0
    );
    sort_name_entries(names, sizes, count);
    return count;
}

int saves_read_file(const TitleInfo *title, const char *name,
                    uint8_t *buf, uint32_t buf_size) {
    char path[PATH_LEN];
    uint32_t total = 0;
    if (title->kind == SAVE_KIND_PS1_VM1) {
        strncpy(path, title->local_path, sizeof(path) - 1);
        path[sizeof(path) - 1] = '\0';
    } else if (title->upload_is_zip) {
        uint32_t bytes_read = 0;
        return export_zip_read_file(title->upload_path, name, buf, buf_size, &bytes_read)
            ? (int)bytes_read
            : -1;
    } else {
        snprintf(path, sizeof(path), "%s/%s",
                 title->upload_path[0] ? title->upload_path : title->local_path,
                 name);
    }
    FILE *f = fopen(path, "rb");
    if (!f) return -1;

    while (total < buf_size) {
        size_t want = buf_size - total;
        size_t n;
        if (want > 32768U) want = 32768U;
        n = fread(buf + total, 1, want, f);
        total += (uint32_t)n;
        if (n < want) {
            if (ferror(f)) {
                fclose(f);
                return -1;
            }
            break;
        }
        pump_callbacks();
    }
    fclose(f);
    return (int)total;
}

int saves_write_file(const TitleInfo *title, const char *name,
                     const uint8_t *buf, uint32_t size) {
    char path[PATH_LEN];
    if (title->kind == SAVE_KIND_PS1_VM1) {
        /* Ensure parent directory exists */
        char dir_path[PATH_LEN];
        strncpy(dir_path, title->local_path, sizeof(dir_path) - 1);
        char *slash = strrchr(dir_path, '/');
        if (slash) { *slash = '\0'; mkdir(dir_path, 0700); }
        strncpy(path, title->local_path, sizeof(path) - 1);
        path[sizeof(path) - 1] = '\0';
    } else {
        mkdir(title->local_path, 0700);
        snprintf(path, sizeof(path), "%s/%s", title->local_path, name);
        mkdir_parents_for_file(path);
    }
    FILE *f = fopen(path, "wb");
    if (!f) return -1;

    for (uint32_t off = 0; off < size; ) {
        size_t chunk = size - off;
        size_t written;
        if (chunk > 32768U) chunk = 32768U;
        written = fwrite(buf + off, 1, chunk, f);
        if (written != chunk) {
            fclose(f);
            return -1;
        }
        off += (uint32_t)written;
        pump_callbacks();
    }

    fclose(f);
    return 0;
}

int saves_normalize_permissions(const char *root_path) {
    return chmod_tree(root_path);
}

bool saves_has_upload_source(const TitleInfo *title) {
    if (!title) {
        return false;
    }
    if (title->kind == SAVE_KIND_PS3) {
        return title->upload_path[0] != '\0';
    }
    return title->local_path[0] != '\0';
}

bool saves_get_hash_cache_key_stats(const TitleInfo *title, int *file_count_out, uint32_t *total_size_out) {
    if (!title) {
        return false;
    }

    if (file_count_out) {
        *file_count_out = title->hash_file_count > 0 ? title->hash_file_count : title->file_count;
    }
    if (total_size_out) {
        *total_size_out = title->hash_total_size > 0 ? title->hash_total_size : title->total_size;
    }
    return true;
}

bool saves_is_ps3_metadata_file(const char *name) {
    /* These files are owned by the console/user and must be preserved when
     * updating an existing save slot, so the resign step can patch + re-sign
     * the native structures rather than trying to create them from scratch. */
    static const char *const metadata[] = {
        "PARAM.SFO", "PARAM.PFD",
        "ICON0.PNG", "PIC1.PNG", "PIC0.PNG", "SND0.AT3",
        NULL
    };
    for (int i = 0; metadata[i]; i++) {
        if (strcasecmp(name, metadata[i]) == 0)
            return true;
    }
    return false;
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
