#ifndef PS3SYNC_HASH_H
#define PS3SYNC_HASH_H

#include <stdbool.h>
#include <stdint.h>

bool hash_file_sha256(const char *path, uint8_t hash_out[32], uint32_t *size_out);
bool hash_dir_files_sha256(
    const char *path,
    uint8_t hash_out[32],
    int *file_count_out,
    uint32_t *total_size_out
);
bool hash_should_skip_ps3_file(const char *rel_path);
bool hash_from_hex(const char *hex, uint8_t hash_out[32]);
void hash_to_hex(const uint8_t hash[32], char hash_hex_out[65]);

#endif /* PS3SYNC_HASH_H */
