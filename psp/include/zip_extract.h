/*
 * zip_extract.h — minimal ZIP_STORED archive extractor for PSP.
 *
 * Mirror of the PS3 client's zip_extract.h.  Used when the server
 * returns a ZIP archive (e.g. PS3 bundles or PS1 CHD → CUE/BIN); the
 * PSP client today only consumes raw .pbp / .cso outputs from the
 * server's PSP-specific extract paths, but the extractor is included
 * for parity and for future bundle support.
 */

#ifndef PSPSYNC_ZIP_EXTRACT_H
#define PSPSYNC_ZIP_EXTRACT_H

#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>

bool zip_extract_stored(const char *zip_path, const char *out_dir,
                        char *error_out, size_t error_out_size);

#endif /* PSPSYNC_ZIP_EXTRACT_H */
