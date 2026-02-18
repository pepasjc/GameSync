#ifndef BUNDLE_H
#define BUNDLE_H

#include "common.h"

#define BUNDLE_MAGIC        "3DSS"
#define BUNDLE_VERSION_V3   3
#define BUNDLE_HEADER_SIZE  36   /* 4+4+16+4+4+4 */

typedef struct {
    char     path[MAX_FILE_LEN];
    uint32_t size;
    uint8_t  hash[32];
    uint8_t *data;
} BundleFileInfo;

typedef struct {
    char     game_id[GAME_ID_LEN];
    uint32_t timestamp;
    int      file_count;
    BundleFileInfo files[MAX_FILES];
    uint8_t *data_buf;
} Bundle;

int bundle_create(const TitleInfo *title, uint8_t **out, uint32_t *out_size);
int bundle_parse(const uint8_t *data, uint32_t size, Bundle *bundle);
int bundle_extract(const Bundle *bundle, TitleInfo *title);
void bundle_free(Bundle *bundle);

#endif
