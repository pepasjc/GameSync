#ifndef PS3SYNC_EXPORT_ZIP_H
#define PS3SYNC_EXPORT_ZIP_H

#include "common.h"
#include "saves.h"

#include <stdbool.h>
#include <stdint.h>

typedef struct {
    char path[MAX_FILE_LEN];
    uint32_t size;
    uint32_t compressed_size;
    uint16_t method;
    uint16_t flags;
    uint32_t local_header_offset;
} ExportZipEntry;

typedef struct {
    char zip_path[PATH_LEN];
    char title_id[GAME_ID_LEN];
    int file_count;
    uint32_t total_size;
    ExportZipEntry files[MAX_FILES];
} ExportZipInfo;

bool export_zip_parse(const char *zip_path, ExportZipInfo *info);
int  export_zip_list_files(const char *zip_path,
                           char names[][MAX_FILE_LEN],
                           uint32_t *sizes,
                           int max_files);
bool export_zip_read_file(const char *zip_path,
                          const char *name,
                          uint8_t *buf,
                          uint32_t buf_size,
                          uint32_t *bytes_read_out);
bool export_zip_hash_files_sha256(const char *zip_path,
                                  uint8_t hash_out[32],
                                  int *file_count_out,
                                  uint32_t *total_size_out);

#endif /* PS3SYNC_EXPORT_ZIP_H */
