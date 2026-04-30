/*
 * downloads.c — pause/resume download manager (persistence layer).
 *
 * Format identical concept to state.c: a flat key=value text file at
 * DOWNLOADS_FILE.  We keep an in-RAM mirror for fast iteration.  Save()
 * rewrites the whole file each time — that's fine, list is bounded to
 * DOWNLOAD_MAX entries (128) and the writes are infrequent (once per
 * status transition).
 *
 * Atomicity: writes go to <file>.tmp first, then rename(2) — a power loss
 * mid-write leaves the previous version intact.  The same pattern is used
 * by the desktop client for download_state.json.
 */

#include "downloads.h"
#include "debug.h"

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
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
    if (strcmp(s, "active")    == 0) return DL_STATUS_PAUSED; /* downgrade transient → paused */
    if (strcmp(s, "paused")    == 0) return DL_STATUS_PAUSED;
    if (strcmp(s, "completed") == 0) return DL_STATUS_COMPLETED;
    if (strcmp(s, "error")     == 0) return DL_STATUS_ERROR;
    return DL_STATUS_QUEUED;
}

/* --- Reconcile offset with on-disk .part file --- */

static uint64_t stat_part_size(const char *target_path) {
    char part[PATH_LEN + 8];
    snprintf(part, sizeof(part), "%s.part", target_path);
    struct stat st;
    if (stat(part, &st) != 0) return 0;
    return (uint64_t)st.st_size;
}

static bool target_already_exists(const char *target_path) {
    struct stat st;
    return stat(target_path, &st) == 0;
}

/* --- Load --- */

/* Pull the next pipe-separated token from *cursor, advancing *cursor past
 * the separator.  Empty fields are returned as "". */
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
    if (!fp) {
        /* No file yet — clean state. */
        return true;
    }

    char line[1024];
    while (fgets(line, sizeof(line), fp) != NULL && list->count < DOWNLOAD_MAX) {
        size_t n = strcspn(line, "\r\n");
        line[n] = '\0';
        if (line[0] == '\0') continue;

        char *eq = strchr(line, '=');
        if (!eq) {
            debug_log("downloads: skipping malformed line: %s", line);
            continue;
        }
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
        /* Bundle fields are appended at the end so older downloads.dat
         * files (without them) parse cleanly with empty strings. */
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

        /* Reconcile against disk so we never resume from a stale offset:
         *   - Final file already exists? Mark COMPLETED, clamp offset.
         *   - .part smaller than recorded offset? Trust the disk. */
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
    debug_log("downloads: loaded %d entries", list->count);
    return true;
}

/* --- Save --- */

bool downloads_save(const DownloadList *list) {
    if (!list) return false;

    char tmp_path[PATH_LEN];
    snprintf(tmp_path, sizeof(tmp_path), "%s.tmp", DOWNLOADS_FILE);

    FILE *fp = fopen(tmp_path, "wb");
    if (!fp) {
        debug_log("downloads: open %s failed errno=%d", tmp_path, errno);
        return false;
    }

    for (int i = 0; i < list->count; i++) {
        const DownloadEntry *e = &list->items[i];
        /* Persist the live status, except ACTIVE which is transient — it
         * always becomes PAUSED across sessions so a crash mid-flight is
         * recovered as a resumable pause. */
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
        int first_errno = errno;
        if (first_errno != EEXIST) {
            debug_log("downloads: rename %s -> %s failed errno=%d",
                      tmp_path, DOWNLOADS_FILE, first_errno);
            unlink(tmp_path);
            return false;
        }
        if (unlink(DOWNLOADS_FILE) != 0 && errno != ENOENT) {
            debug_log("downloads: unlink %s failed errno=%d",
                      DOWNLOADS_FILE, errno);
            return false;
        }
        if (rename(tmp_path, DOWNLOADS_FILE) != 0) {
            debug_log("downloads: rename %s -> %s retry failed errno=%d "
                      "(first errno=%d)",
                      tmp_path, DOWNLOADS_FILE, errno, first_errno);
            return false;
        }
    }
    return true;
}

/* --- Lookups + mutations --- */

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
    /* Always refresh size + bundle metadata from the catalog (server may
     * have re-imported with a different layout). */
    e->total       = rom->size;
    e->is_bundle   = rom->is_bundle;
    if (rom->is_bundle && rom->file_count > 0) {
        e->bundle_count = rom->file_count;
    }
    /* Carry the server's extract hint over so the worker knows whether
     * to add ``?extract=<fmt>`` to the request URL.  Empty = raw. */
    strncpy(e->extract_format, rom->extract_format,
            sizeof(e->extract_format) - 1);
    e->extract_format[sizeof(e->extract_format) - 1] = '\0';

    /* For bundles, target_path is a per-file location which only makes
     * sense once we have the manifest; leave empty until run_download
     * resolves each file individually.  For single-file ROMs, resolve
     * once now. */
    if (!rom->is_bundle && (created || e->target_path[0] == '\0')) {
        roms_resolve_target_path(rom, e->target_path, sizeof(e->target_path));
    }
    return e;
}

bool downloads_remove(DownloadList *list, const char *rom_id) {
    if (!list || !rom_id) return false;
    for (int i = 0; i < list->count; i++) {
        if (strcmp(list->items[i].rom_id, rom_id) != 0) continue;

        /* Drop the .part if present.  Final file is left alone — the user
         * deleting an entry doesn't mean wiping a completed download. */
        char part[PATH_LEN + 8];
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
    /* Prefer QUEUED first so a freshly-added rom doesn't sit behind an
     * older paused one indefinitely. */
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
