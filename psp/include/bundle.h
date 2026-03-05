#ifndef BUNDLE_H
#define BUNDLE_H

#include "common.h"

/* Bundle v4 format (PSP/Vita):
 *   [4B]  Magic "3DSS"
 *   [4B]  Version = 4 (uint32 LE)
 *   [32B] Title ID (ASCII, null-padded; e.g. "ULUS10272DATA00\0...")
 *   [4B]  Timestamp (uint32 LE)
 *   [4B]  File count (uint32 LE)
 *   [4B]  Uncompressed payload size (uint32 LE)
 *   [NB]  zlib-compressed payload:
 *           File table: for each file:
 *             [2B] path length, [NB] path, [4B] size, [32B] SHA-256
 *           File data: for each file in same order
 *
 * v3 (legacy, 16-byte title ID field) is only used for parsing downloaded saves
 * from an older server. New uploads always use v4.
 */

#define BUNDLE_MAGIC "3DSS"
#define BUNDLE_VERSION_V3  3
#define BUNDLE_VERSION_V4  4
#define BUNDLE_HEADER_SIZE 52  /* 4+4+32+4+4+4 */

typedef struct {
    char   path[MAX_FILE_LEN];
    uint32_t size;
    uint8_t  hash[32];
    uint8_t *data;              /* points into decoded buffer; do not free separately */
} BundleFileInfo;

typedef struct {
    char     game_id[GAME_ID_LEN];
    uint32_t timestamp;
    int      file_count;
    BundleFileInfo files[MAX_FILES];
    uint8_t *data_buf;          /* heap-allocated decoded payload data */
} Bundle;

/* Create a v3 bundle from all files in title->save_dir.
 * out: heap-allocated output buffer. Caller must free().
 * out_size: size of the bundle.
 * Returns 0 on success. */
int bundle_create(const TitleInfo *title, uint8_t **out, uint32_t *out_size);

/* Parse a v3 bundle from raw data.
 * bundle: output struct; call bundle_free() when done.
 * Returns 0 on success. */
int bundle_parse(const uint8_t *data, uint32_t size, Bundle *bundle);

/* Extract all files from a parsed bundle into title->save_dir.
 * Returns 0 on success. */
int bundle_extract(const Bundle *bundle, TitleInfo *title);

/* Free heap memory inside a Bundle struct. */
void bundle_free(Bundle *bundle);

#endif /* BUNDLE_H */
