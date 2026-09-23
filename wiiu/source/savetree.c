/*
 * savetree.c — recursive save-directory enumeration shared by the vWii and
 * Wii U save modules.
 *
 * Everything here is plain POSIX (opendir/stat/fopen): libmocha exposes SLC
 * and MLC through newlib devoptabs, so the NAND trees are ordinary paths.
 */

#include "savetree.h"

#include <dirent.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define COPY_CHUNK 65536

static bool excluded(const char *name, const char *const *exclude) {
    if (!exclude) return false;
    for (int i = 0; exclude[i]; i++)
        if (strcmp(name, exclude[i]) == 0) return true;
    return false;
}

static int push_file(SaveTitle *t, const char *rel, const struct stat *st) {
    if (t->file_count >= SAVE_MAX_FILES_PER_TITLE) return -1;
    if (t->file_count >= t->file_cap) {
        int cap = t->file_cap ? t->file_cap * 2 : 32;
        SaveFile *n = (SaveFile *)realloc(t->files, (size_t)cap * sizeof(SaveFile));
        if (!n) return -1;
        t->files = n;
        t->file_cap = cap;
    }
    SaveFile *f = &t->files[t->file_count];
    memset(f, 0, sizeof(*f));
    strncpy(f->relative_path, rel, sizeof(f->relative_path) - 1);
    f->file_size = (uint32_t)st->st_size;
    f->mtime     = (uint32_t)st->st_mtime;
    t->file_count++;
    t->total_size += f->file_size;
    if (f->mtime > t->latest_mtime) t->latest_mtime = f->mtime;
    return 0;
}

/* Depth-first walk.  ``rel`` is the path relative to title->root ("" at the
 * top level); ``exclude`` only applies to top-level directory names. */
static int walk(SaveTitle *t, const char *rel, const char *const *exclude) {
    char dirpath[SAVE_DIR_LEN + SAVE_PATH_MAX];
    if (rel[0])
        snprintf(dirpath, sizeof(dirpath), "%s/%s", t->root, rel);
    else
        snprintf(dirpath, sizeof(dirpath), "%s", t->root);

    DIR *d = opendir(dirpath);
    if (!d) return -1;

    struct dirent *de;
    int rc = 0;
    while ((de = readdir(d)) != NULL) {
        if (strcmp(de->d_name, ".") == 0 || strcmp(de->d_name, "..") == 0) continue;
        if (!rel[0] && excluded(de->d_name, exclude)) continue;

        char child_rel[SAVE_PATH_MAX];
        if (rel[0])
            snprintf(child_rel, sizeof(child_rel), "%s/%s", rel, de->d_name);
        else
            snprintf(child_rel, sizeof(child_rel), "%s", de->d_name);

        char full[SAVE_DIR_LEN + SAVE_PATH_MAX];
        snprintf(full, sizeof(full), "%s/%s", t->root, child_rel);

        struct stat st;
        if (stat(full, &st) != 0) continue;
        if (S_ISDIR(st.st_mode)) {
            if (walk(t, child_rel, exclude) != 0) { rc = -1; break; }
        } else if (S_ISREG(st.st_mode)) {
            if (push_file(t, child_rel, &st) != 0) { rc = -1; break; }
        }
    }
    closedir(d);
    return rc;
}

/* Sort by relative path so the bundle's file order (and therefore the save
 * hash) is stable across consoles and readdir orderings. */
static int cmp_file(const void *a, const void *b) {
    return strcmp(((const SaveFile *)a)->relative_path,
                  ((const SaveFile *)b)->relative_path);
}

int savetree_scan(SaveTitle *title, const char *const *exclude) {
    if (!title) return -1;
    free(title->files);
    title->files = NULL;
    title->file_cap = 0;
    title->file_count = 0;
    title->total_size = 0;
    title->latest_mtime = 0;

    if (walk(title, "", exclude) != 0) return -1;
    if (title->file_count > 1)
        qsort(title->files, (size_t)title->file_count, sizeof(SaveFile), cmp_file);
    return 0;
}

void savetree_free_title(SaveTitle *title) {
    if (!title) return;
    free(title->files);
    title->files = NULL;
    title->file_cap = 0;
    title->file_count = 0;
}

void savetree_free_list(SaveTitleList *list) {
    if (!list) return;
    for (int i = 0; i < list->title_count; i++) savetree_free_title(&list->titles[i]);
    list->title_count = 0;
}

SaveTitle *savetree_find(SaveTitleList *list, const char *title_id) {
    if (!list || !title_id) return NULL;
    for (int i = 0; i < list->title_count; i++)
        if (strcmp(list->titles[i].title_id, title_id) == 0) return &list->titles[i];
    return NULL;
}

int savetree_mkdir_p(const char *path) {
    if (!path || !*path) return -1;
    char buf[SAVE_DIR_LEN + SAVE_PATH_MAX];
    strncpy(buf, path, sizeof(buf) - 1);
    buf[sizeof(buf) - 1] = '\0';

    /* Skip the devoptab prefix ("fs:/vol/external01", "storage_mlc01:") so we
     * never try to mkdir the mount point itself. */
    char *p = buf;
    char *colon = strchr(buf, ':');
    if (colon) { p = colon + 1; if (*p == '/') p++; }
    else if (*p == '/') p++;

    for (; *p; p++) {
        if (*p == '/') {
            *p = '\0';
            if (buf[0]) mkdir(buf, 0777);
            *p = '/';
        }
    }
    mkdir(buf, 0777);

    struct stat st;
    return (stat(buf, &st) == 0 && S_ISDIR(st.st_mode)) ? 0 : -1;
}

static int copy_file(const char *src, const char *dst) {
    FILE *in = fopen(src, "rb");
    if (!in) return -1;
    FILE *out = fopen(dst, "wb");
    if (!out) { fclose(in); return -1; }

    char *buf = (char *)malloc(COPY_CHUNK);
    if (!buf) { fclose(in); fclose(out); return -1; }

    int rc = 0;
    for (;;) {
        size_t n = fread(buf, 1, COPY_CHUNK, in);
        if (n == 0) break;
        if (fwrite(buf, 1, n, out) != n) { rc = -1; break; }
    }
    free(buf);
    fclose(in);
    if (fclose(out) != 0) rc = -1;
    return rc;
}

int savetree_copy_dir(const char *src, const char *dst) {
    if (savetree_mkdir_p(dst) != 0) return -1;

    DIR *d = opendir(src);
    if (!d) return -1;

    struct dirent *de;
    int rc = 0;
    while ((de = readdir(d)) != NULL) {
        if (strcmp(de->d_name, ".") == 0 || strcmp(de->d_name, "..") == 0) continue;

        char s[SAVE_DIR_LEN + SAVE_PATH_MAX];
        char t[SAVE_DIR_LEN + SAVE_PATH_MAX];
        snprintf(s, sizeof(s), "%s/%s", src, de->d_name);
        snprintf(t, sizeof(t), "%s/%s", dst, de->d_name);

        struct stat st;
        if (stat(s, &st) != 0) continue;
        if (S_ISDIR(st.st_mode)) {
            if (savetree_copy_dir(s, t) != 0) { rc = -1; break; }
        } else if (S_ISREG(st.st_mode)) {
            if (copy_file(s, t) != 0) { rc = -1; break; }
        }
    }
    closedir(d);
    return rc;
}
