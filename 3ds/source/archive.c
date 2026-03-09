#include "archive.h"

#define MAX_ARCHIVE_FILES 64

static Result open_save_archive(FS_Archive *archive, u64 title_id, FS_MediaType media_type) {
    u32 path_data[3] = {media_type, (u32)(title_id & 0xFFFFFFFF), (u32)(title_id >> 32)};
    return FSUSER_OpenArchive(archive, ARCHIVE_USER_SAVEDATA,
        (FS_Path){PATH_BINARY, sizeof(path_data), path_data});
}

// Recursively read files from a directory in the archive
static int read_dir(FS_Archive archive, const char *dir_path,
                    ArchiveFile *files, int offset, int max_files) {
    Handle dir_handle;
    int added = 0;

    Result res = FSUSER_OpenDirectory(&dir_handle, archive,
        fsMakePath(PATH_ASCII, dir_path));
    if (R_FAILED(res)) return 0;

    FS_DirectoryEntry *entries = (FS_DirectoryEntry *)malloc(32 * sizeof(FS_DirectoryEntry));
    if (!entries) {
        FSDIR_Close(dir_handle);
        return 0;
    }
    u32 entries_read = 0;

    while (true) {
        res = FSDIR_Read(dir_handle, &entries_read, 32, entries);
        if (R_FAILED(res) || entries_read == 0) break;

        for (u32 i = 0; i < entries_read && (offset + added) < max_files; i++) {
            // Convert UTF-16 name to ASCII
            char name[256];
            int j;
            for (j = 0; j < 255 && entries[i].name[j]; j++)
                name[j] = (char)entries[i].name[j];
            name[j] = '\0';

            // Build full path
            char full_path[MAX_PATH_LEN];
            if (strcmp(dir_path, "/") == 0)
                snprintf(full_path, sizeof(full_path), "/%s", name);
            else
                snprintf(full_path, sizeof(full_path), "%s/%s", dir_path, name);

            if (entries[i].attributes & FS_ATTRIBUTE_DIRECTORY) {
                // Recurse into subdirectory
                added += read_dir(archive, full_path, files, offset + added, max_files);
            } else {
                // Read file
                Handle file_handle;
                res = FSUSER_OpenFile(&file_handle, archive,
                    fsMakePath(PATH_ASCII, full_path), FS_OPEN_READ, 0);
                if (R_FAILED(res)) continue;

                u64 file_size;
                FSFILE_GetSize(file_handle, &file_size);

                u8 *buf = (u8 *)malloc((size_t)file_size);
                if (!buf) {
                    FSFILE_Close(file_handle);
                    continue;
                }

                u32 bytes_read;
                res = FSFILE_Read(file_handle, &bytes_read, 0, buf, (u32)file_size);
                FSFILE_Close(file_handle);

                if (R_FAILED(res)) {
                    free(buf);
                    continue;
                }

                ArchiveFile *af = &files[offset + added];
                // Store path without leading slash for bundle format
                strncpy(af->path, full_path + 1, MAX_PATH_LEN - 1);
                af->path[MAX_PATH_LEN - 1] = '\0';
                af->size = bytes_read;
                af->data = buf;
                added++;
            }
        }
    }

    free(entries);
    FSDIR_Close(dir_handle);
    return added;
}

// Recursively count files and sum sizes without reading content
static void stat_dir(FS_Archive archive, const char *dir_path,
                     int *file_count, u32 *total_size) {
    Handle dir_handle;
    Result res = FSUSER_OpenDirectory(&dir_handle, archive,
        fsMakePath(PATH_ASCII, dir_path));
    if (R_FAILED(res)) return;

    FS_DirectoryEntry *entries = (FS_DirectoryEntry *)malloc(32 * sizeof(FS_DirectoryEntry));
    if (!entries) {
        FSDIR_Close(dir_handle);
        return;
    }
    u32 entries_read = 0;

    while (true) {
        res = FSDIR_Read(dir_handle, &entries_read, 32, entries);
        if (R_FAILED(res) || entries_read == 0) break;

        for (u32 i = 0; i < entries_read; i++) {
            char name[256];
            int j;
            for (j = 0; j < 255 && entries[i].name[j]; j++)
                name[j] = (char)entries[i].name[j];
            name[j] = '\0';

            char full_path[MAX_PATH_LEN];
            if (strcmp(dir_path, "/") == 0)
                snprintf(full_path, sizeof(full_path), "/%s", name);
            else
                snprintf(full_path, sizeof(full_path), "%s/%s", dir_path, name);

            if (entries[i].attributes & FS_ATTRIBUTE_DIRECTORY) {
                stat_dir(archive, full_path, file_count, total_size);
            } else {
                (*file_count)++;
                *total_size += (u32)entries[i].fileSize;
            }
        }
    }

    free(entries);
    FSDIR_Close(dir_handle);
}

int archive_stat(u64 title_id, FS_MediaType media_type,
                 int *file_count, u32 *total_size) {
    FS_Archive archive;
    Result res = open_save_archive(&archive, title_id, media_type);
    if (R_FAILED(res)) return -1;

    *file_count = 0;
    *total_size = 0;
    stat_dir(archive, "/", file_count, total_size);

    FSUSER_CloseArchive(archive);
    return 0;
}

int archive_read(u64 title_id, FS_MediaType media_type,
                 ArchiveFile *files, int max_files) {
    FS_Archive archive;
    Result res = open_save_archive(&archive, title_id, media_type);
    if (R_FAILED(res)) return -1;

    int count = read_dir(archive, "/", files, 0, max_files);

    FSUSER_CloseArchive(archive);
    return count;
}

// Delete all files/dirs in a directory recursively
static void clear_dir(FS_Archive archive, const char *dir_path) {
    Handle dir_handle;
    Result res = FSUSER_OpenDirectory(&dir_handle, archive,
        fsMakePath(PATH_ASCII, dir_path));
    if (R_FAILED(res)) return;

    FS_DirectoryEntry *entries = (FS_DirectoryEntry *)malloc(32 * sizeof(FS_DirectoryEntry));
    if (!entries) {
        FSDIR_Close(dir_handle);
        return;
    }
    u32 entries_read = 0;

    while (true) {
        res = FSDIR_Read(dir_handle, &entries_read, 32, entries);
        if (R_FAILED(res) || entries_read == 0) break;

        for (u32 i = 0; i < entries_read; i++) {
            char name[256];
            int j;
            for (j = 0; j < 255 && entries[i].name[j]; j++)
                name[j] = (char)entries[i].name[j];
            name[j] = '\0';

            char full_path[MAX_PATH_LEN];
            if (strcmp(dir_path, "/") == 0)
                snprintf(full_path, sizeof(full_path), "/%s", name);
            else
                snprintf(full_path, sizeof(full_path), "%s/%s", dir_path, name);

            if (entries[i].attributes & FS_ATTRIBUTE_DIRECTORY) {
                clear_dir(archive, full_path);
                FSUSER_DeleteDirectory(archive, fsMakePath(PATH_ASCII, full_path));
            } else {
                FSUSER_DeleteFile(archive, fsMakePath(PATH_ASCII, full_path));
            }
        }
    }

    free(entries);
    FSDIR_Close(dir_handle);
}

// Ensure parent directories exist for a path like "/subdir/file.bin"
static void ensure_parent_dirs(FS_Archive archive, const char *path) {
    char buf[MAX_PATH_LEN];
    strncpy(buf, path, MAX_PATH_LEN - 1);
    buf[MAX_PATH_LEN - 1] = '\0';

    for (char *p = buf + 1; *p; p++) {
        if (*p == '/') {
            *p = '\0';
            FSUSER_CreateDirectory(archive, fsMakePath(PATH_ASCII, buf), 0);
            *p = '/';
        }
    }
}

bool archive_write(u64 title_id, FS_MediaType media_type,
                   const ArchiveFile *files, int file_count) {
    FS_Archive archive;
    Result res = open_save_archive(&archive, title_id, media_type);
    if (R_FAILED(res)) return false;

    // Clear existing save data
    clear_dir(archive, "/");

    // Write each file
    for (int i = 0; i < file_count; i++) {
        char full_path[MAX_PATH_LEN];
        snprintf(full_path, sizeof(full_path), "/%s", files[i].path);

        ensure_parent_dirs(archive, full_path);

        // Create and write file
        FSUSER_CreateFile(archive, fsMakePath(PATH_ASCII, full_path), 0, files[i].size);

        Handle file_handle;
        res = FSUSER_OpenFile(&file_handle, archive,
            fsMakePath(PATH_ASCII, full_path), FS_OPEN_WRITE, 0);
        if (R_FAILED(res)) {
            FSUSER_CloseArchive(archive);
            return false;
        }

        u32 bytes_written;
        res = FSFILE_Write(file_handle, &bytes_written, 0,
            files[i].data, files[i].size, FS_WRITE_FLUSH);
        FSFILE_Close(file_handle);

        if (R_FAILED(res) || bytes_written != files[i].size) {
            FSUSER_CloseArchive(archive);
            return false;
        }
    }

    // CRITICAL: commit save data or changes are lost
    FSUSER_ControlArchive(archive, ARCHIVE_ACTION_COMMIT_SAVE_DATA, NULL, 0, NULL, 0);

    FSUSER_CloseArchive(archive);
    return true;
}

void archive_free_files(ArchiveFile *files, int count) {
    for (int i = 0; i < count; i++) {
        free(files[i].data);
        files[i].data = NULL;
    }
}
