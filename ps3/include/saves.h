#ifndef PS3SYNC_SAVES_H
#define PS3SYNC_SAVES_H

#include "common.h"

#define MAX_FILES    128
#define MAX_FILE_LEN 256

void saves_scan(SyncState *state);
bool saves_calculate_hash(TitleInfo *title);

/* New functions for bundle/sync support */
int  saves_compute_hash(TitleInfo *title);  /* 0 = ok, -1 = fail */
int  saves_list_files(const TitleInfo *title,
                      char names[][MAX_FILE_LEN], uint32_t *sizes, int max);
int  saves_read_file(const TitleInfo *title, const char *name,
                     uint8_t *buf, uint32_t buf_size);
int  saves_write_file(const TitleInfo *title, const char *name,
                      const uint8_t *buf, uint32_t size);
int  saves_normalize_permissions(const char *root_path);
bool saves_has_upload_source(const TitleInfo *title);
bool saves_get_hash_cache_key(
    const TitleInfo *title,
    int *file_count_out,
    uint32_t *total_size_out,
    uint32_t *stamp_out
);
bool saves_is_shared_ps1_entry(const TitleInfo *title);

/* Returns true for PS3/PS1 game codes (4 UPPER + 5 digits, not Vita PC*) */
bool saves_is_relevant_game_code(const char *id);

/* Returns true if name is a PS3 save metadata file that should be preserved
 * when syncing into an existing save slot (PARAM.SFO, PARAM.PFD, icons, etc.) */
bool saves_is_ps3_metadata_file(const char *name);

#endif /* PS3SYNC_SAVES_H */
