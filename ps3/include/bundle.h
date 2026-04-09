#ifndef PS3SYNC_BUNDLE_H
#define PS3SYNC_BUNDLE_H

#include "common.h"
#include "saves.h"

/* 3DSS v4 bundle format (same as Vita client):
 *
 * Header (52 bytes):
 *   [0..3]   magic "3DSS"
 *   [4..7]   version = 4 (LE u32)
 *   [8..39]  game_id (32-byte ASCII, NUL-padded)
 *   [40..43] timestamp (LE u32, Unix seconds)
 *   [44..47] file_count (LE u32)
 *   [48..51] uncompressed_size (LE u32)
 *   [52..]   zlib-compressed payload
 *
 * Payload (uncompressed):
 *   For each file: [path_len:u16][path:bytes][size:u32][sha256:32 bytes]
 *   Then for each file: [data:bytes]
 */

#define BUNDLE_MAGIC         "3DSS"
#define BUNDLE_VERSION_V4    4U
#define BUNDLE_HEADER_SIZE   52U

typedef struct {
    char      path[MAX_FILE_LEN];
    uint32_t  size;
    uint8_t   hash[32];
    const uint8_t *data;   /* points into data_buf — valid until bundle_free() */
} BundleFile;

typedef struct {
    char       game_id[GAME_ID_LEN];
    uint32_t   timestamp;
    int        file_count;
    BundleFile files[MAX_FILES];
    uint8_t   *data_buf;   /* malloc'd; must be freed via bundle_free() */
} Bundle;

/* Create a bundle from the given title's save files.
 * On success, *out_data is malloc'd (caller must free) and *out_size is set.
 * Returns 0 on success, -1 on failure. */
int  bundle_create(const TitleInfo *title, uint8_t **out_data, uint32_t *out_size);

/* Parse a bundle from raw bytes.  On success bundle->data_buf is malloc'd.
 * Returns 0 on success, -1 on failure. */
int  bundle_parse(const uint8_t *data, uint32_t size, Bundle *bundle);

/* Write all files from bundle into title's save directory.
 * Returns 0 on success, -1 on failure. */
int  bundle_extract(const Bundle *bundle, TitleInfo *title);

/* Free memory allocated by bundle_parse / bundle_create. */
void bundle_free(Bundle *bundle);

#endif /* PS3SYNC_BUNDLE_H */
