/*
 * downloads.c — PSP download manager persistence.
 *
 * Identical concept to the PS3 client: a flat key=value file at
 * DOWNLOADS_FILE, in-RAM mirror, atomic save via tmp+rename.  PSP's
 * libc supports rename() and stat() on ms0:/ paths so the same
 * pattern works without modification.
 */

#include "downloads.h"

#include <errno.h>
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
    if (strcmp(s, "active")    == 0) return DL_STATUS_PAUSED;
    if (strcmp(s, "paused")    == 0) return DL_STATUS_PAUSED;
    if (strcmp(s, "completed") == 0) return DL_STATUS_COMPLETED;
    if (strcmp(s, "error")     == 0) return DL_STATUS_ERROR;
    return DL_STATUS_QUEUED;
}

/* --- Reconcile --- */

static uint64_t stat_part_size(const char *target_path) {
    char part[512];
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
    if (sep) {
        len = (size_t)(sep - start);
        *cursor = sep + 1;
    } else {
        len = strlen(start);
        *cursor = start + len;
    }
    if (len >= out_size) len = out_size - 1;
    memcpy(out, start, len);
    out[len] = '\0';
}

bool downloads_load(DownloadList *list) {
    if (!list) return false;
    list->count = 0;

    FILE *fp = fopen(DOWNLOADS_FILE, "rb");
    if (!fp) return true;

    char line[1024];
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
        strncpy(e->rom_id, line, sizeof(e->rom_id) - 1);

        char status_buf[DOWNLOAD_STATUS_LEN];
        char offset_buf[32];
        char total_buf[32];
        char bundle_flag_buf[8];
        char bundle_index_buf[16];
        char bundle_count_buf[16];
        char *cursor = value;
        next_field(&cursor, status_buf,        sizeof(status_buf));
        next_field(&cursor, offset_buf,        sizeof(offset_buf));
        next_field(&cursor, total_buf,         sizeof(total_buf));
        next_field(&cursor, e->filename,       sizeof(e->filename));
        next_field(&cursor, e->target_path,    sizeof(e->target_path));
        next_field(&cursor, e->system,         sizeof(e->system));
        next_field(&cursor, e->name,           sizeof(e->name));
        next_field(&cursor, bundle_flag_buf,   sizeof(bundle_flag_buf));
        next_field(&cursor, bundle_index_buf,  sizeof(bundle_index_buf));
        next_field(&cursor, bundle_count_buf,  sizeof(bundle_count_buf));
        next_field(&cursor, e->extract_format, sizeof(e->extract_format));

        e->status = downloads_str_to_status(status_buf);
        e->offset = strtoull(offset_buf, NULL, 10);
        e->total  = strtoull(total_buf,  NULL, 10);
        e->is_bundle    = (bundle_flag_buf[0] == '1');
        e->bundle_index = atoi(bundle_index_buf);
        e->bundle_count = atoi(bundle_count_buf);

        if (target_already_exists(e->target_path)) {
            e->status = DL_STATUS_COMPLETED;
            e->offset = e->total;
        } else if (e->status == DL_STATUS_PAUSED ||
                   e->status == DL_STATUS_ACTIVE ||
                   e->status == DL_STATUS_ERROR)
        {
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
    char tmp_path[256];
    snprintf(tmp_path, sizeof(tmp_path), "%s.tmp", DOWNLOADS_FILE);

    FILE *fp = fopen(tmp_path, "wb");
    if (!fp) return false;

    for (int i = 0; i < list->count; i++) {
        const DownloadEntry *e = &list->items[i];
        DownloadStatus persisted =
            (e->status == DL_STATUS_ACTIVE) ? DL_STATUS_PAUSED : e->status;

        fprintf(fp, "%s=%s|%llu|%llu|%s|%s|%s|%s|%d|%d|%d|%s\n",
                e->rom_id,
                downloads_status_to_str(persisted),
                (unsigned long long)e->offset,
                (unsigned long long)e->total,
                e->filename,
                e->target_path,
                e->system,
                e->name,
                e->is_bundle ? 1 : 0,
                e->bundle_index,
                e->bundle_count,
                e->extract_format);
    }
    fclose(fp);

    if (rename(tmp_path, DOWNLOADS_FILE) != 0) {
        unlink(DOWNLOADS_FILE);
        if (rename(tmp_path, DOWNLOADS_FILE) != 0) return false;
    }
    return true;
}

DownloadEntry *downloads_find(DownloadList *list, const char *rom_id) {
    if (!list || !rom_id) return NULL;
    for (int i = 0; i < list->count; i++) {
        if (strcmp(list->items[i].rom_id, rom_id) == 0)
            return &list->items[i];
    }
    return NULL;
}

DownloadEntry *downloads_upsert_from_catalog(DownloadList *list,
                                             const RomEntry *rom) {
    if (!list || !rom) return NULL;
    DownloadEntry *e = downloads_find(list, rom->rom_id);
    bool created = false;
    if (!e) {
        if (list->count >= DOWNLOAD_MAX) return NULL;
        e = &list->items[list->count++];
        memset(e, 0, sizeof(*e));
        strncpy(e->rom_id,   rom->rom_id,   sizeof(e->rom_id) - 1);
        strncpy(e->filename, rom->filename, sizeof(e->filename) - 1);
        strncpy(e->name,     rom->name,     sizeof(e->name) - 1);
        strncpy(e->system,   rom->system,   sizeof(e->system) - 1);
        e->status = DL_STATUS_QUEUED;
        created = true;
    }
    e->total       = rom->size;
    e->is_bundle   = rom->is_bundle;
    if (rom->is_bundle && rom->file_count > 0) {
        e->bundle_count = rom->file_count;
    }
    /* Pick the right extract format for this PSP catalog entry —
     * roms_preferred_extract_format already knows about cso vs eboot. */
    const char *fmt = roms_preferred_extract_format(rom);
    strncpy(e->extract_format, fmt, sizeof(e->extract_format) - 1);
    e->extract_format[sizeof(e->extract_format) - 1] = '\0';

    if (!rom->is_bundle && (created || e->target_path[0] == '\0')) {
        roms_resolve_target_path(rom, e->target_path, sizeof(e->target_path));
    }
    return e;
}

bool downloads_remove(DownloadList *list, const char *rom_id) {
    if (!list || !rom_id) return false;
    for (int i = 0; i < list->count; i++) {
        if (strcmp(list->items[i].rom_id, rom_id) != 0) continue;
        char part[512];
        snprintf(part, sizeof(part), "%s.part", list->items[i].target_path);
        unlink(part);
        for (int j = i + 1; j < list->count; j++) {
            list->items[j - 1] = list->items[j];
        }
        list->count--;
        return true;
    }
    return false;
}

DownloadEntry *downloads_next_runnable(DownloadList *list) {
    if (!list) return NULL;
    for (int i = 0; i < list->count; i++) {
        if (list->items[i].status == DL_STATUS_QUEUED) return &list->items[i];
    }
    for (int i = 0; i < list->count; i++) {
        if (list->items[i].status == DL_STATUS_PAUSED ||
            list->items[i].status == DL_STATUS_ERROR)
            return &list->items[i];
    }
    return NULL;
}
