/*
 * downloads.c — Vita download queue persistence.
 *
 * Same concept as the PSP client: one ``rom_id=field|field|...`` line
 * per entry at DOWNLOADS_FILE, in-RAM mirror, atomic save via
 * tmp + sceIoRename.  On load, entries are reconciled against disk:
 * a target that already exists is completed, and a paused entry's
 * offset is clamped to the actual ``.part`` size.
 */

#include "downloads.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <psp2/io/fcntl.h>
#include <psp2/io/stat.h>

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
    /* An entry persisted as active means we were killed mid-transfer;
     * treat it as paused so the .part is resumed, not restarted. */
    if (strcmp(s, "active")    == 0) return DL_STATUS_PAUSED;
    if (strcmp(s, "paused")    == 0) return DL_STATUS_PAUSED;
    if (strcmp(s, "completed") == 0) return DL_STATUS_COMPLETED;
    if (strcmp(s, "error")     == 0) return DL_STATUS_ERROR;
    return DL_STATUS_QUEUED;
}

/* --- Reconcile --- */

static uint64_t stat_part_size(const char *target_path) {
    char part[DOWNLOAD_PATH_LEN + 8];
    snprintf(part, sizeof(part), "%s.part", target_path);
    SceIoStat st;
    if (sceIoGetstat(part, &st) < 0) return 0;
    return (uint64_t)st.st_size;
}

static bool target_already_exists(const char *target_path) {
    SceIoStat st;
    return sceIoGetstat(target_path, &st) >= 0;
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

/* Whole-file read into a static buffer; the queue is at most
 * DOWNLOAD_MAX lines of ~700 B. */
static char g_file_buf[DOWNLOAD_MAX * 768];

bool downloads_load(DownloadList *list) {
    if (!list) return false;
    list->count = 0;

    SceUID fd = sceIoOpen(DOWNLOADS_FILE, SCE_O_RDONLY, 0777);
    if (fd < 0) return true;
    int n = sceIoRead(fd, g_file_buf, sizeof(g_file_buf) - 1);
    sceIoClose(fd);
    if (n <= 0) return true;
    g_file_buf[n] = '\0';

    char *line = g_file_buf;
    while (line && *line && list->count < DOWNLOAD_MAX) {
        char *nl = strchr(line, '\n');
        if (nl) *nl = '\0';
        size_t len = strcspn(line, "\r");
        line[len] = '\0';

        if (line[0]) {
            char *eq = strchr(line, '=');
            if (eq) {
                *eq = '\0';
                char *value = eq + 1;

                DownloadEntry *e = &list->items[list->count];
                memset(e, 0, sizeof(*e));
                strncpy(e->rom_id, line, sizeof(e->rom_id) - 1);

                char status_buf[DOWNLOAD_STATUS_LEN];
                char offset_buf[32];
                char total_buf[32];
                char *cursor = value;
                next_field(&cursor, status_buf,        sizeof(status_buf));
                next_field(&cursor, offset_buf,        sizeof(offset_buf));
                next_field(&cursor, total_buf,         sizeof(total_buf));
                next_field(&cursor, e->filename,       sizeof(e->filename));
                next_field(&cursor, e->target_path,    sizeof(e->target_path));
                next_field(&cursor, e->system,         sizeof(e->system));
                next_field(&cursor, e->name,           sizeof(e->name));
                next_field(&cursor, e->extract_format, sizeof(e->extract_format));

                e->status = downloads_str_to_status(status_buf);
                e->offset = strtoull(offset_buf, NULL, 10);
                e->total  = strtoull(total_buf,  NULL, 10);

                if (target_already_exists(e->target_path)) {
                    e->status = DL_STATUS_COMPLETED;
                    e->offset = e->total;
                } else if (e->status == DL_STATUS_PAUSED ||
                           e->status == DL_STATUS_ERROR)
                {
                    uint64_t part_size = stat_part_size(e->target_path);
                    if (part_size < e->offset) e->offset = part_size;
                }
                if (e->target_path[0]) list->count++;
            }
        }
        line = nl ? nl + 1 : NULL;
    }
    return true;
}

bool downloads_save(const DownloadList *list) {
    if (!list) return false;
    char tmp_path[256];
    snprintf(tmp_path, sizeof(tmp_path), "%s.tmp", DOWNLOADS_FILE);

    SceUID fd = sceIoOpen(tmp_path, SCE_O_WRONLY | SCE_O_CREAT | SCE_O_TRUNC, 0777);
    if (fd < 0) return false;

    char line[1024];
    for (int i = 0; i < list->count; i++) {
        const DownloadEntry *e = &list->items[i];
        DownloadStatus persisted =
            (e->status == DL_STATUS_ACTIVE) ? DL_STATUS_PAUSED : e->status;

        int n = snprintf(line, sizeof(line), "%s=%s|%llu|%llu|%s|%s|%s|%s|%s\n",
                         e->rom_id,
                         downloads_status_to_str(persisted),
                         (unsigned long long)e->offset,
                         (unsigned long long)e->total,
                         e->filename,
                         e->target_path,
                         e->system,
                         e->name,
                         e->extract_format);
        if (n > 0) sceIoWrite(fd, line, (size_t)n < sizeof(line) ? (size_t)n : sizeof(line) - 1);
    }
    sceIoClose(fd);

    sceIoRemove(DOWNLOADS_FILE);
    return sceIoRename(tmp_path, DOWNLOADS_FILE) >= 0;
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
    e->total = rom->size;
    const char *fmt = roms_preferred_extract_format(rom);
    strncpy(e->extract_format, fmt, sizeof(e->extract_format) - 1);
    e->extract_format[sizeof(e->extract_format) - 1] = '\0';

    if (created || e->target_path[0] == '\0')
        roms_resolve_target_path(rom, e->target_path, sizeof(e->target_path));
    return e;
}

bool downloads_remove(DownloadList *list, const char *rom_id) {
    if (!list || !rom_id) return false;
    for (int i = 0; i < list->count; i++) {
        if (strcmp(list->items[i].rom_id, rom_id) != 0) continue;
        char part[DOWNLOAD_PATH_LEN + 8];
        snprintf(part, sizeof(part), "%s.part", list->items[i].target_path);
        sceIoRemove(part);
        for (int j = i + 1; j < list->count; j++)
            list->items[j - 1] = list->items[j];
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
