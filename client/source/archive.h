#ifndef ARCHIVE_H
#define ARCHIVE_H

#include "common.h"

// A single file read from a save archive
typedef struct {
    char path[MAX_PATH_LEN];
    u32 size;
    u8 *data; // malloc'd, caller must free via archive_free_files()
} ArchiveFile;

// Get file count and total size from a save archive without reading content.
// Returns 0 on success, -1 if the archive cannot be opened.
int archive_stat(u64 title_id, FS_MediaType media_type,
                 int *file_count, u32 *total_size);

// Read all files from a title's save archive.
// Returns number of files read, fills files array (up to max_files).
// Caller must call archive_free_files() when done.
int archive_read(u64 title_id, FS_MediaType media_type,
                 ArchiveFile *files, int max_files);

// Write files to a title's save archive (overwriting existing).
// Returns true on success. Commits the save data.
bool archive_write(u64 title_id, FS_MediaType media_type,
                   const ArchiveFile *files, int file_count);

// Free all data buffers in a file array
void archive_free_files(ArchiveFile *files, int count);

#endif // ARCHIVE_H
