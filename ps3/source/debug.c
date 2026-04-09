#include "debug.h"

#include "common.h"

#include <dirent.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>

void debug_dump_path_stat(const char *label, const char *path) {
    struct stat st;
    if (!path || !path[0]) {
        debug_log("perm: %s path missing", label ? label : "(null)");
        return;
    }
    if (stat(path, &st) != 0) {
        debug_log("perm: %s stat failed path=%s", label ? label : "(null)", path);
        return;
    }
    debug_log("perm: %s path=%s mode=%07o size=%u",
              label ? label : "(null)",
              path,
              (unsigned)(st.st_mode & 07777),
              (unsigned)st.st_size);
}

static void dump_child_stats(const char *parent_path) {
    DIR *dir;
    struct dirent *ent;

    if (!parent_path || !parent_path[0]) return;

    dir = opendir(parent_path);
    if (!dir) {
        debug_log("perm: opendir failed path=%s", parent_path);
        return;
    }

    while ((ent = readdir(dir)) != NULL) {
        char child_path[PATH_LEN];
        if (strcmp(ent->d_name, ".") == 0 || strcmp(ent->d_name, "..") == 0) {
            continue;
        }
        snprintf(child_path, sizeof(child_path), "%s/%s", parent_path, ent->d_name);
        debug_dump_path_stat(ent->d_name, child_path);
    }

    closedir(dir);
}

void debug_dump_savedata_permissions(const char *savedata_root, const char *target_path) {
    DIR *dir;
    struct dirent *ent;
    int sibling_count = 0;
    const char *target_name = target_path ? strrchr(target_path, '/') : NULL;

    debug_log("perm: ---- target save ----");
    debug_dump_path_stat("target_dir", target_path);
    dump_child_stats(target_path);

    if (!savedata_root || !savedata_root[0]) {
        debug_log("perm: savedata_root missing");
        return;
    }

    dir = opendir(savedata_root);
    if (!dir) {
        debug_log("perm: opendir failed root=%s", savedata_root);
        return;
    }

    if (target_name && target_name[0] == '/') {
        target_name++;
    } else {
        target_name = NULL;
    }

    debug_log("perm: ---- sibling saves ----");
    while ((ent = readdir(dir)) != NULL && sibling_count < 5) {
        char child_path[PATH_LEN];
        if (strcmp(ent->d_name, ".") == 0 || strcmp(ent->d_name, "..") == 0) {
            continue;
        }
        if (target_name && strcmp(ent->d_name, target_name) == 0) {
            continue;
        }
        snprintf(child_path, sizeof(child_path), "%s/%s", savedata_root, ent->d_name);
        debug_dump_path_stat(ent->d_name, child_path);
        sibling_count++;
    }

    closedir(dir);
}

static FILE *g_debug_log = NULL;

bool debug_log_open(void) {
    if (g_debug_log) {
        return true;
    }

    /* Try USB drives first so the log is easy to retrieve */
    for (int i = 0; i < 8; i++) {
        char path[64];
        snprintf(path, sizeof(path), "/dev_usb%03d/ps3sync_debug.log", i);
        g_debug_log = fopen(path, "wb");
        if (g_debug_log) return true;
    }

    /* Fall back to internal HDD */
    g_debug_log = fopen(DEBUG_LOG_FILE, "wb");
    return g_debug_log != NULL;
}

void debug_log_close(void) {
    if (!g_debug_log) {
        return;
    }
    fclose(g_debug_log);
    g_debug_log = NULL;
}

void debug_log(const char *fmt, ...) {
    va_list args;

    if (!g_debug_log && !debug_log_open()) {
        return;
    }

    va_start(args, fmt);
    vfprintf(g_debug_log, fmt, args);
    va_end(args);

    if (fmt[0] != '\0') {
        size_t len = strlen(fmt);
        if (fmt[len - 1] != '\n') {
            fputc('\n', g_debug_log);
        }
    }

    fflush(g_debug_log);
}
