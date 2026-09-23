/*
 * downloads.c — flat-file download manager.
 *
 * One line per entry:
 *   <key>=<status>|<offset>|<total>|<filename>|<target>|<system>|<name>|
 *         <extract>|<url_path>|<install>
 * In-RAM mirror, atomic save via tmp+rename.
 */

#include "downloads.h"
#include "roms.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <unistd.h>

const char *downloads_status_to_str(DownloadStatus s) {
    switch (s) {
        case DL_STATUS_QUEUED:    return "queued";
        case DL_STATUS_ACTIVE:    return "active";
        case DL_STATUS_PAUSED:    return "paused";
        case DL_STATUS_COMPLETED: return "completed";
        case DL_STATUS_ERROR:     return "error";
    }
    return "queued";
}

DownloadStatus downloads_str_to_status(const char *s) {
    if (!s) return DL_STATUS_QUEUED;
    if (strcmp(s, "queued")    == 0) return DL_STATUS_QUEUED;
    if (strcmp(s, "active")    == 0) return DL_STATUS_PAUSED;  /* recovered as paused */
    if (strcmp(s, "paused")    == 0) return DL_STATUS_PAUSED;
    if (strcmp(s, "completed") == 0) return DL_STATUS_COMPLETED;
    if (strcmp(s, "error")     == 0) return DL_STATUS_ERROR;
    return DL_STATUS_QUEUED;
}

static uint64_t stat_part_size(const char *target_path) {
    char part[DOWNLOAD_PATH_LEN + 8];
    snprintf(part, sizeof(part), "%s.part", target_path);
    struct stat st;
    if (stat(part, &st) != 0) return 0;
    return (uint64_t)st.st_size;
}

static bool target_already_exists(const char *target_path) {
    struct stat st;
    return stat(target_path, &st) == 0;
}

static void next_field(char **cursor, char *out, size_t out_size) {
    if (out_size == 0) return;
    out[0] = '\0';
    if (!cursor || !*cursor) return;
    char *start = *cursor;
    char *sep = strchr(start, '|');
    size_t len;
    if (sep) { len = (size_t)(sep - start); *cursor = sep + 1; }
    else     { len = strlen(start); *cursor = start + len; }
    if (len >= out_size) len = out_size - 1;
    memcpy(out, start, len);
    out[len] = '\0';
}

bool downloads_load(DownloadList *list) {
    if (!list) return false;
    list->count = 0;

    FILE *fp = fopen(roms_downloads_file(), "rb");
    if (!fp) return true;        /* no file = empty list */

    char line[2048];
    while (fgets(line, sizeof(line), fp) != NULL && list->count < DOWNLOAD_MAX) {
        size_t n = strcspn(line, "\r\n");
        line[n] = '\0';
        if (line[0] == '\0') continue;

        char *eq = strchr(line, '=');
        if (!eq) continue;
        *eq = '\0';
        char *value = eq + 1;

        DownloadEntry *e = &list->items[list->count];
        memset(e, 0, sizeof(*e));
        strncpy(e->key, line, sizeof(e->key) - 1);

        /* rom_id is the key up to the optional '#<part>' suffix. */
        const char *hash = strchr(e->key, '#');
        size_t idlen = hash ? (size_t)(hash - e->key) : strlen(e->key);
        if (idlen >= sizeof(e->rom_id)) idlen = sizeof(e->rom_id) - 1;
        memcpy(e->rom_id, e->key, idlen);
        e->rom_id[idlen] = '\0';

        char status_buf[DOWNLOAD_STATUS_LEN], offset_buf[32], total_buf[32];
        char install_buf[8];
        char *cursor = value;
        next_field(&cursor, status_buf,        sizeof(status_buf));
        next_field(&cursor, offset_buf,        sizeof(offset_buf));
        next_field(&cursor, total_buf,         sizeof(total_buf));
        next_field(&cursor, e->filename,       sizeof(e->filename));
        next_field(&cursor, e->target_path,    sizeof(e->target_path));
        next_field(&cursor, e->system,         sizeof(e->system));
        next_field(&cursor, e->name,           sizeof(e->name));
        next_field(&cursor, e->extract_format, sizeof(e->extract_format));
        next_field(&cursor, e->url_path,       sizeof(e->url_path));
        next_field(&cursor, install_buf,       sizeof(install_buf));

        e->status  = downloads_str_to_status(status_buf);
        e->offset  = strtoull(offset_buf, NULL, 10);
        e->total   = strtoull(total_buf,  NULL, 10);
        e->install = (DownloadInstall)atoi(install_buf);

        if (target_already_exists(e->target_path)) {
            e->status = DL_STATUS_COMPLETED;
            e->offset = e->total;
        } else if (e->status == DL_STATUS_PAUSED || e->status == DL_STATUS_ERROR) {
            uint64_t part_size = stat_part_size(e->target_path);
            if (part_size < e->offset) e->offset = part_size;
        }
        list->count++;
    }
    fclose(fp);
    return true;
}

bool downloads_save(const DownloadList *list) {
    if (!list) return false;

    char tmp_path[SAVE_DIR_LEN];
    snprintf(tmp_path, sizeof(tmp_path), "%s.tmp", roms_downloads_file());

    FILE *fp = fopen(tmp_path, "wb");
    if (!fp) return false;

    for (int i = 0; i < list->count; i++) {
        const DownloadEntry *e = &list->items[i];
        DownloadStatus persisted =
            (e->status == DL_STATUS_ACTIVE) ? DL_STATUS_PAUSED : e->status;
        fprintf(fp, "%s=%s|%llu|%llu|%s|%s|%s|%s|%s|%s|%d\n",
                e->key,
                downloads_status_to_str(persisted),
                (unsigned long long)e->offset,
                (unsigned long long)e->total,
                e->filename, e->target_path, e->system, e->name,
                e->extract_format, e->url_path, (int)e->install);
    }
    fclose(fp);

    if (rename(tmp_path, roms_downloads_file()) != 0) {
        remove(roms_downloads_file());
        if (rename(tmp_path, roms_downloads_file()) != 0) return false;
    }
    return true;
}

DownloadEntry *downloads_find(DownloadList *list, const char *key) {
    if (!list || !key) return NULL;
    for (int i = 0; i < list->count; i++)
        if (strcmp(list->items[i].key, key) == 0) return &list->items[i];
    return NULL;
}

static DownloadEntry *upsert(DownloadList *list, const char *key) {
    DownloadEntry *e = downloads_find(list, key);
    if (e) return e;
    if (list->count >= DOWNLOAD_MAX) return NULL;
    e = &list->items[list->count++];
    memset(e, 0, sizeof(*e));
    strncpy(e->key, key, sizeof(e->key) - 1);
    e->status = DL_STATUS_QUEUED;
    return e;
}

DownloadEntry *downloads_upsert_gc(DownloadList *list, const RomEntry *rom) {
    if (!list || !rom) return NULL;
    DownloadEntry *e = upsert(list, rom->rom_id);
    if (!e) return NULL;

    strncpy(e->rom_id,   rom->rom_id,   sizeof(e->rom_id)   - 1);
    strncpy(e->filename, rom->filename, sizeof(e->filename) - 1);
    strncpy(e->name,     rom->name,     sizeof(e->name)     - 1);
    strncpy(e->system,   "GC",          sizeof(e->system)   - 1);
    strncpy(e->extract_format, roms_preferred_extract_format(rom),
            sizeof(e->extract_format) - 1);
    e->url_path[0] = '\0';   /* derived from rom_id + extract_format */
    e->install     = DL_INSTALL_GC_ISO;
    e->total       = rom->size;
    if (e->target_path[0] == '\0')
        roms_resolve_gc_staging_path(rom, e->target_path, sizeof(e->target_path));
    return e;
}

DownloadEntry *downloads_upsert_wbfs_part(DownloadList *list,
                                          const RomEntry *rom,
                                          const char *part_name,
                                          uint64_t part_size,
                                          const char *game_dir) {
    if (!list || !rom || !part_name || !game_dir) return NULL;

    char key[DOWNLOAD_KEY_LEN];
    snprintf(key, sizeof(key), "%s#%s", rom->rom_id, part_name);

    DownloadEntry *e = upsert(list, key);
    if (!e) return NULL;

    strncpy(e->rom_id,   rom->rom_id, sizeof(e->rom_id)   - 1);
    strncpy(e->filename, part_name,   sizeof(e->filename) - 1);
    snprintf(e->name, sizeof(e->name), "%s (%s)", rom->name, part_name);
    strncpy(e->system, "WII", sizeof(e->system) - 1);
    e->extract_format[0] = '\0';
    e->install = DL_INSTALL_NONE;
    e->total   = part_size;
    snprintf(e->url_path, sizeof(e->url_path),
             "/api/v1/roms/%s/wbfs/%s", rom->rom_id, part_name);
    snprintf(e->target_path, sizeof(e->target_path), "%s/%s", game_dir, part_name);
    return e;
}

DownloadEntry *downloads_upsert_wup_file(DownloadList *list,
                                         const RomEntry *rom,
                                         const char *rel_name,
                                         uint64_t file_size,
                                         const char *game_dir) {
    if (!list || !rom || !rel_name || !game_dir) return NULL;

    char key[DOWNLOAD_KEY_LEN];
    snprintf(key, sizeof(key), "%s#%s", rom->rom_id, rel_name);

    DownloadEntry *e = upsert(list, key);
    if (!e) return NULL;

    strncpy(e->rom_id,   rom->rom_id, sizeof(e->rom_id)   - 1);
    strncpy(e->filename, rel_name,    sizeof(e->filename) - 1);
    snprintf(e->name, sizeof(e->name), "%s (%s)", rom->name, rel_name);
    strncpy(e->system, "WIIU", sizeof(e->system) - 1);
    e->extract_format[0] = '\0';
    /* No post-download action: the folder becomes installable only once
     * every file has landed, which the install view checks for itself by
     * looking for title.tmd.  Auto-installing per file would be wrong. */
    e->install = DL_INSTALL_NONE;
    e->total   = file_size;
    snprintf(e->url_path, sizeof(e->url_path),
             "/api/v1/roms/%s/file/%s", rom->rom_id, rel_name);
    snprintf(e->target_path, sizeof(e->target_path), "%s/%s", game_dir, rel_name);
    return e;
}

bool downloads_remove(DownloadList *list, const char *key) {
    if (!list || !key) return false;
    for (int i = 0; i < list->count; i++) {
        if (strcmp(list->items[i].key, key) != 0) continue;
        char part[DOWNLOAD_PATH_LEN + 8];
        snprintf(part, sizeof(part), "%s.part", list->items[i].target_path);
        remove(part);
        for (int j = i + 1; j < list->count; j++) list->items[j - 1] = list->items[j];
        list->count--;
        return true;
    }
    return false;
}

DownloadEntry *downloads_next_runnable(DownloadList *list) {
    if (!list) return NULL;
    for (int i = 0; i < list->count; i++)
        if (list->items[i].status == DL_STATUS_QUEUED) return &list->items[i];
    for (int i = 0; i < list->count; i++)
        if (list->items[i].status == DL_STATUS_PAUSED ||
            list->items[i].status == DL_STATUS_ERROR)
            return &list->items[i];
    return NULL;
}
